"""The M5a gate: inspection, decryption and recovery, over the corpus.

ROADMAP.md, M5a, sets four conditions; the gate checks each on every file:

1. Owner-password-protected files are readable and writable by a third-party
   library after decryption, field names and rectangles equal to those of the
   original. pdfacts.decrypt() writes each encrypted file anew; pypdf and
   PyMuPDF each read the field names and rectangles of every page's widgets
   in the original and in the result, pypdf also the names of /AcroForm's
   field tree, write the result anew and read it again, and set the value of
   the first text field, write the file and read the value back, in each
   file where pikepdf finds a text field. Each
   library's readings are compared with its own; each has to read on each
   page as many widgets as pikepdf finds in its /Annots, and to read the
   original as encrypted and the result as not. The files decrypt() takes for
   encrypted have to be those pikepdf reads as encrypted, each with an owner
   password that is not the empty one.
2. A page /Contents that is an indirect array is handled: pdfacts.inspect()
   gives every page's content streams, and pikepdf, read here apart from
   pdfacts, finds the same form of /Contents, the same streams, the same
   number of other entries and the same array.
3. The verification inside each function detects a deliberately introduced
   fault: the QPDF backend's writing is replaced by one that returns a result
   made wrong on purpose, and decrypt() and recover() have to refuse it; given
   the right result, unchanged and written anew by pikepdf as the faults that
   edit it are, they have to return it.
4. The active content report names the object in which each finding was
   made: pikepdf finds, in the object each finding names, the entry and the
   action of its kind it names, an action through the entries and the /Next
   that name it, the XFA streams where /XFA names them, and the file
   specification with the streams its /EF names.

The receipt holds per file counts, digests and the outcomes of the faults,
for comparing platforms; no value and no text of a document. pypdf and PyMuPDF
come from the dependency group "bench" and are imported only where they are
read, so that the tests can load this file without them. Run from the
repository root:

    uv run --group bench python bench/gates/m5a.py --commit SHA --out PATH \
        [--only ID ...] [--corpus DIR]

The runtime installs the font policy first, as for the other gates:
decrypt() renders each page in its check. Exit status 0 when every selected
file was read without an error and passed every condition; 1 otherwise; 2
when the run was refused and no receipt was written, as without the group.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import enum
import hashlib
import io
import json
import logging
import platform
import re
import sys
import time
import warnings
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pikepdf

import pdfacts
from pdfacts import Unknown
from pdfacts.backends import qpdf as qpdf_backend
from pdfacts.backends.protocols import DecryptedBytes, RecoveredBytes
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"
sys.path.insert(0, str(ROOT / "bench"))

from digest import Digest  # noqa: E402

# The value the gate sets in the first text field of a decrypted file.
FILLED = "pdfacts M5a gate"
# What a fault introduced into the results reports as QPDF's warning.
WARNING = "a warning introduced by the M5a gate"
# The stroke a fault adds to the end of the first page's content.
STROKE = b"q 0 0 1 RG 4 w 0 0 m 100 100 l S Q"
# How the inspection names the forms of /Contents.
FORMS = tuple(form.value for form in pdfacts.ContentsForm)
# The findings of active content, as the receipt counts them.
FINDINGS = ("actions", "triggers", "xfa streams", "embedded files")
# The error each function refuses a fault with.
WANTED = {"decrypt": "DecryptionFailedError", "recover": "RecoveryFailedError"}


def commit_argument(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("a commit is 40 lowercase hexadecimal digits")
    return value


# --- 2 and 4: the inspection ------------------------------------------------------------


def put(digest: Digest, value: object) -> None:
    """Every value of a fact, in field order: a dataclass by its name and its
    fields, an enum member by its value, a text and bytes by their length and
    code points, a sequence by its length and items."""
    if value is None:
        digest.update("none")
    elif isinstance(value, bool):
        digest.update("true" if value else "false")
    elif isinstance(value, enum.Enum):
        digest.update("enum", str(value.value))
    elif isinstance(value, str):
        digest.update("text", len(value), *(ord(character) for character in value))
    elif isinstance(value, bytes):
        digest.update("bytes", len(value), *value)
    elif isinstance(value, (int, float)):
        digest.update(value)
    elif isinstance(value, tuple):
        digest.update("tuple", len(value))
        for item in value:
            put(digest, item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        digest.update("fact", type(value).__name__)
        for field in dataclasses.fields(value):
            put(digest, getattr(value, field.name))
    else:
        raise TypeError(f"no digest for a value of {type(value).__name__}")


def inspection_digest(found: pdfacts.Inspection) -> str:
    digest = Digest()
    put(digest, found)
    return digest.hexdigest()


def contents_written(pdf: pikepdf.Pdf) -> list[tuple]:
    """Each page's /Contents as the file writes it, read with pikepdf: its
    form, the objects of the streams it names, the number of its other
    entries, and the object of the array where it is one of its own."""
    found = []
    for page in pdf.pages:
        # pikepdf gives a null, and a reference to an object the file lacks, as None.
        contents = page.obj.get("/Contents")
        if contents is None:
            found.append(("absent", [], 0, None))
        elif isinstance(contents, pikepdf.Stream):
            found.append(("stream", [_objgen_of(contents)], 0, None))
        elif isinstance(contents, pikepdf.Array):
            streams = [_objgen_of(item) for item in contents if isinstance(item, pikepdf.Stream)]
            others = len(contents) - len(streams)
            if contents.is_indirect:
                found.append(("indirect-array", streams, others, _objgen_of(contents)))
            else:
                found.append(("array", streams, others, None))
        else:
            found.append(("other", [], 1, None))
    return found


def contents_part(found: pdfacts.Inspection, pdf: pikepdf.Pdf) -> tuple[dict, list[str]]:
    """The forms of /Contents counted, and the pages where pikepdf finds
    another form, other streams, another number of other entries or another
    array than the inspection."""
    if isinstance(found.pages, Unknown):
        return {}, ["the pages' /Contents are unknown"]
    forms: Counter[str] = Counter()
    failed = []
    written = contents_written(pdf)
    for page, read in zip(found.pages, written, strict=True):
        forms[page.form.value] += 1
        named = found.contents[page.contents]
        given = (
            page.form.value,
            [_objgen(stream.ref) for stream in named.streams],
            named.others,
            None if page.array is None else _objgen(page.array),
        )
        if given != read:
            failed.append(f"/Contents of page {page.page_index}")
    return {form: forms[form] for form in FORMS}, failed


def _objgen(ref: pdfacts.ObjectRef) -> tuple[int, int]:
    return (ref.object_number, ref.generation)


def _indirect(value: object) -> bool:
    """Whether `value` is a reference to another object; pikepdf gives a number
    or a boolean as a Python value, which never is."""
    return isinstance(value, pikepdf.Object) and value.is_indirect


def _direct(value: object) -> Iterator[object]:
    """`value` and every dictionary and array it holds directly, not through
    another object; a stream counts as its dictionary."""
    stack = [value]
    while stack:
        item = stack.pop()
        yield item
        if isinstance(item, pikepdf.Array):
            stack.extend(entry for entry in item if not _indirect(entry))
        elif isinstance(item, pikepdf.Dictionary | pikepdf.Stream):
            stack.extend(entry for entry in item.values() if not _indirect(entry))


def _of_kind(value: object, kind: object) -> bool:
    """Whether `value` is a dictionary, or a stream read as its dictionary,
    whose /S is that of an action of `kind`: that name; none where `kind` is
    None; a value that is no name where it is unknown."""
    if not isinstance(value, pikepdf.Dictionary | pikepdf.Stream):
        return False
    written = value.get("/S")
    if kind is None:
        return written is None
    if isinstance(kind, Unknown):
        return written is not None and not isinstance(written, pikepdf.Name)
    # bytes() gives a name as the file writes it, #xx resolved, with its slash.
    return isinstance(written, pikepdf.Name) and bytes(written)[1:] == kind.raw


def _objgen_of(value: pikepdf.Object) -> tuple[int, int]:
    return (int(value.objgen[0]), int(value.objgen[1]))


def _placed(value: object, holder: tuple[int, int]) -> tuple[object, tuple[int, int]]:
    """`value`, found in what the object `holder` holds, with the object it is
    written in: its own where it is a reference, `holder` otherwise."""
    return value, _objgen_of(value) if _indirect(value) else holder


def _entries(owner: object, objgen: tuple[int, int], event: str) -> Iterator[tuple]:
    """The values of the entries of key `event` that `owner`, the object
    `objgen`, holds directly, each with the object it is written in:
    /OpenAction and /A in a dictionary, the other events in its /AA."""
    for item in _direct(owner):
        if not isinstance(item, pikepdf.Dictionary):
            continue
        if event in ("OpenAction", "A"):
            holder, key = item, f"/{event}"
        else:
            holder, key = item.get("/AA"), f"/{event}"
        _, written = _placed(holder, objgen)
        if isinstance(holder, pikepdf.Dictionary) and key in holder:
            yield _placed(holder[key], written)


def javascript_entries(pdf: pikepdf.Pdf) -> dict[tuple[int, int], list[tuple]]:
    """The values of the catalog's JavaScript name tree, by the object each
    node that writes them in its /Names is written in, each value with the
    object it is written in: from /Names /JavaScript down each /Kids, a node
    of its own read once."""
    found: dict[tuple[int, int], list[tuple]] = {}
    names, names_at = _placed(pdf.Root.get("/Names"), _objgen_of(pdf.Root))
    if not isinstance(names, pikepdf.Dictionary):
        return found
    tree = names.get("/JavaScript")
    stack = [_placed(tree, names_at)] if tree is not None else []
    read: set[tuple[int, int]] = set()
    while stack:
        node, node_at = stack.pop()
        if not isinstance(node, pikepdf.Dictionary | pikepdf.Stream):
            continue
        if _indirect(node):
            if node_at in read:
                continue
            read.add(node_at)
        entries, entries_at = _placed(node.get("/Names"), node_at)
        if isinstance(entries, pikepdf.Array):
            values = [_placed(value, entries_at) for value in list(entries)[1::2]]
            found.setdefault(node_at, []).extend(values)
        kids, kids_at = _placed(node.get("/Kids"), node_at)
        if isinstance(kids, pikepdf.Array):
            stack.extend(_placed(kid, kids_at) for kid in kids)
    return found


def _following(pdf: pikepdf.Pdf, action: object) -> Iterator[list[tuple]]:
    """For each dictionary of the action's kind in its object that has a
    /Next, the dictionaries and streams it names, in order, each with the
    object it is written in."""
    objgen = _objgen(action.ref)
    for item in _direct(pdf.get_object(objgen)):
        if not _of_kind(item, action.kind) or "/Next" not in item:
            continue
        value, written = _placed(item["/Next"], objgen)
        if isinstance(value, pikepdf.Array):
            named = [_placed(entry, written) for entry in value]
        else:
            named = [(value, written)]
        yield [
            (entry, at)
            for entry, at in named
            if isinstance(entry, pikepdf.Dictionary | pikepdf.Stream)
        ]


def _is(value: tuple, action: object) -> bool:
    """Whether `value`, with the object it is written in, is an action of the
    kind of `action`, written in its object."""
    entry, written = value
    return written == _objgen(action.ref) and _of_kind(entry, action.kind)


def _matched(values: list[tuple], wanted: list[object]) -> list[bool]:
    """For each of the actions `wanted`, in order, whether one of `values`
    that none before it took is that action (_is). A value is an action of
    one kind in one object, so taking the first that fits leaves no action
    without one that another order would give it."""
    left = list(values)
    found = []
    for action in wanted:
        at = next((index for index, value in enumerate(left) if _is(value, action)), None)
        if at is not None:
            del left[at]
        found.append(at is not None)
    return found


def actions_failed(pdf: pikepdf.Pdf, content: object) -> list[str]:
    """The actions, and the entries that name them, that pikepdf does not find
    where the report places them. The entries of one key in one owner, or of
    the JavaScript name tree in the nodes one owner writes, have to name, each
    a value of its own, a dictionary of its action's kind written in the
    action's object; a /Next, the actions it is listed with, in order.
    An action that entries or a /Next name has to be found by one of them;
    one that nothing names, as a dictionary of its kind in its object, whose
    /S names it or, without one, whose /Type is /Action, as the inspection
    finds it."""
    actions = content.actions
    groups: dict[tuple, list[int]] = {}
    for index, trigger in enumerate(content.triggers):
        groups.setdefault((_objgen(trigger.owner), trigger.event), []).append(index)
    named: set[int] = set()
    found: set[int] = set()
    triggers = []
    javascript = javascript_entries(pdf)
    for (objgen, event), indices in groups.items():
        if event is None:
            values = javascript.get(objgen, [])
        else:
            values = list(_entries(pdf.get_object(objgen), objgen, event))
        wanted = [actions[content.triggers[index].action] for index in indices]
        for index, matched in zip(indices, _matched(values, wanted), strict=True):
            named.add(content.triggers[index].action)
            if matched:
                found.add(content.triggers[index].action)
            else:
                triggers.append(index)
    for action in actions:
        if action.next is None:
            continue
        sequence = content.sequences[action.next]
        named.update(sequence)
        wanted = [actions[index] for index in sequence]
        for values in _following(pdf, action):
            if len(values) == len(wanted) and all(map(_is, values, wanted)):
                found.update(sequence)
                break
    failed = []
    for index, action in enumerate(actions):
        if index in named:
            here = index in found
        else:
            here = any(
                _of_kind(item, action.kind)
                and (action.kind is not None or item.get("/Type") == pikepdf.Name.Action)
                for item in _direct(pdf.get_object(_objgen(action.ref)))
            )
        if not here:
            failed.append(f"action {index}")
    return failed + [f"trigger {index}" for index in sorted(triggers)]


def xfa_written(pdf: pikepdf.Pdf) -> list[tuple[int, int]] | str | None:
    """The streams of /AcroForm's /XFA as pikepdf reads them, in order: a
    stream, or those of an array of pairs of a packet's name, a string, and
    its stream; None without /XFA; "invalid" where it is neither."""
    form = pdf.Root.get("/AcroForm")
    xfa = form.get("/XFA") if isinstance(form, pikepdf.Dictionary) else None
    if xfa is None:
        return None
    if isinstance(xfa, pikepdf.Stream):
        return [_objgen_of(xfa)]
    if isinstance(xfa, pikepdf.Array) and len(xfa) % 2 == 0:
        items = list(xfa)
        names, streams = items[0::2], items[1::2]
        if all(isinstance(name, pikepdf.String) for name in names) and all(
            isinstance(stream, pikepdf.Stream) for stream in streams
        ):
            return [_objgen_of(stream) for stream in streams]
    return "invalid"


def xfa_failed(content: object, pdf: pikepdf.Pdf) -> list[str]:
    """The XFA streams of the report that pikepdf does not find where /XFA
    names them, and "the XFA streams" where /XFA names others besides, or
    where the report gives none, or unknown, and pikepdf finds otherwise."""
    written = xfa_written(pdf)
    if not isinstance(content.xfa, tuple):
        expected = "invalid" if isinstance(content.xfa, Unknown) else None
        return [] if written == expected else ["the XFA streams"]
    streams = written if isinstance(written, list) else []
    failed = [
        f"XFA stream {index}"
        for index, ref in enumerate(content.xfa)
        if index >= len(streams) or streams[index] != _objgen(ref)
    ]
    if len(streams) > len(content.xfa):
        failed.append("the XFA streams")
    return failed


def _embedded_streams(specification: object) -> list[tuple[int, int]]:
    """The streams a file specification's /EF names, in the order of their
    keys' bytes, each once. pikepdf's values() of a dictionary come in that
    order, whatever order the file writes the keys in; its keys() is a set,
    whose order changes from one process to the next."""
    named: list[tuple[int, int]] = []
    for value in specification["/EF"].values():
        if isinstance(value, pikepdf.Stream) and _objgen_of(value) not in named:
            named.append(_objgen_of(value))
    return named


def findings_part(found: pdfacts.Inspection, pdf: pikepdf.Pdf) -> tuple[dict, list[str]]:
    """The findings of the active content counted, and those pikepdf does
    not find in the object the report names."""
    content = found.active_content
    if isinstance(content, Unknown):
        return {}, ["the active content is unknown"]
    failed: list[str] = []
    counts = dict.fromkeys(FINDINGS, 0)
    if any(
        isinstance(part, Unknown) for part in (content.actions, content.sequences, content.triggers)
    ):
        failed.append("the actions or their entries are unknown")
    else:
        counts["actions"] = len(content.actions)
        counts["triggers"] = len(content.triggers)
        failed += actions_failed(pdf, content)
    counts["xfa streams"] = len(content.xfa) if isinstance(content.xfa, tuple) else 0
    failed += xfa_failed(content, pdf)
    for index, embedded in enumerate(content.embedded_files):
        counts["embedded files"] += 1
        streams = [_objgen(ref) for ref in content.embedded_streams[embedded.streams]]
        written = pdf.get_object(_objgen(embedded.specification))
        if not any(
            isinstance(item, pikepdf.Dictionary)
            and isinstance(item.get("/EF"), pikepdf.Dictionary)
            and _embedded_streams(item) == streams
            for item in _direct(written)
        ):
            failed.append(f"embedded file {index}")
    return counts, failed


# --- 1: decryption, read and written by pypdf and PyMuPDF -------------------------------


def _pypdf_chain(annotation: object) -> Iterator[object]:
    """A widget and the dictionaries of its /Parent chain, up to one met
    before."""
    met: set[int] = set()
    node = annotation
    while isinstance(node, dict) and id(node) not in met:
        met.add(id(node))
        yield node
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None


def _pypdf_name(annotation: object) -> str:
    """A widget's fully qualified field name: its /T and those of its
    /Parent chain, joined by points."""
    parts = [str(node["/T"]) for node in _pypdf_chain(annotation) if "/T" in node]
    return ".".join(reversed(parts))


def _pypdf_inherited(annotation: object, key: str) -> object:
    """The value of `key` in a widget or in the nearest of its parents that
    has one; None where none has."""
    return next((node[key] for node in _pypdf_chain(annotation) if key in node), None)


def pypdf_read(data: bytes) -> tuple[list[tuple], list[str] | None, bool]:
    """What pypdf reads of the fields: each page's widgets, page by page in
    /Annots order, with the field name and the rectangle; the fully qualified
    names of the fields of /AcroForm's tree, in its order; and whether the
    file is encrypted, which pypdf opens with the empty password."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    encrypted = reader.is_encrypted
    if encrypted:
        reader.decrypt("")
    widgets = []
    for index, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if isinstance(widget, dict) and widget.get("/Subtype") == "/Widget":
                rect = tuple(float(value) for value in widget["/Rect"])
                widgets.append((index, _pypdf_name(widget), rect))
    return widgets, list(reader.get_fields() or {}), encrypted


def pypdf_written(data: bytes) -> bytes:
    import pypdf

    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(data)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def pypdf_filled(data: bytes) -> bool | None:
    """Whether the value pypdf sets in the first text field of the first page
    that has one is read back from the file it writes; None without one."""
    import pypdf

    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(data)))
    for page in writer.pages:
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if not isinstance(widget, dict) or widget.get("/Subtype") != "/Widget":
                continue
            if _pypdf_inherited(widget, "/FT") != "/Tx":
                continue
            # pypdf takes the field's fully qualified name, or its /T alone.
            name = _pypdf_name(widget)
            writer.update_page_form_field_values(page, {name: FILLED}, auto_regenerate=False)
            out = io.BytesIO()
            writer.write(out)
            fields = pypdf.PdfReader(io.BytesIO(out.getvalue())).get_fields() or {}
            return fields.get(name, {}).get("/V") == FILLED
    return None


def pymupdf_read(data: bytes) -> tuple[list[tuple], list[str] | None, bool]:
    """As pypdf_read(), with PyMuPDF, which reads fields through the widgets
    alone, so that it gives no names of the field tree. PyMuPDF opens an
    encrypted file with the empty password itself, and then counts it as not
    encrypted; the metadata name its encryption."""
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        widgets = [
            (index, widget.field_name, tuple(float(value) for value in widget.rect))
            for index, page in enumerate(document)
            for widget in page.widgets()
        ]
        return widgets, None, document.metadata.get("encryption") is not None
    finally:
        document.close()


def pymupdf_written(data: bytes) -> bytes:
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return document.tobytes()
    finally:
        document.close()


def pymupdf_filled(data: bytes) -> bool | None:
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page in document:
            for widget in page.widgets():
                if widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
                    continue
                name = widget.field_name
                widget.field_value = FILLED
                widget.update()
                written = pymupdf.open(stream=document.tobytes(), filetype="pdf")
                try:
                    return any(
                        other.field_name == name and other.field_value == FILLED
                        for again in written
                        for other in again.widgets()
                    )
                finally:
                    written.close()
        return None
    finally:
        document.close()


@dataclasses.dataclass(frozen=True)
class Reader:
    """A third-party library as the gate uses it."""

    read: Callable[[bytes], tuple[list[tuple], list[str] | None, bool]]
    written: Callable[[bytes], bytes]
    filled: Callable[[bytes], bool | None]


READERS = {
    "pypdf": Reader(pypdf_read, pypdf_written, pypdf_filled),
    "pymupdf": Reader(pymupdf_read, pymupdf_written, pymupdf_filled),
}


def fields_digest(widgets: list[tuple], fields: list[str] | None) -> str:
    """The widgets' pages, names and rectangles, and the names of the field
    tree."""
    digest = Digest()
    for page, name, rect in widgets:
        digest.update(page, len(name), *(ord(character) for character in name), *rect)
    if fields is not None:
        digest.update("fields", len(fields))
        for name in fields:
            digest.update(len(name), *(ord(character) for character in name))
    return digest.hexdigest()


def _widgets(pdf: pikepdf.Pdf) -> Iterator[tuple[int, pikepdf.Dictionary]]:
    """Each widget in the pages' /Annots, page by page, with the index of its
    page, as pikepdf reads them."""
    for index, page in enumerate(pdf.pages):
        annotations = page.obj.get("/Annots")
        if not isinstance(annotations, pikepdf.Array):
            continue
        for annotation in annotations:
            if (
                isinstance(annotation, pikepdf.Dictionary)
                and annotation.get("/Subtype") == "/Widget"
            ):
                yield index, annotation


def widget_pages(pdf: pikepdf.Pdf) -> list[int]:
    """The index of the page of each widget, as _widgets() gives them."""
    return [index for index, _ in _widgets(pdf)]


def _inherited(annotation: pikepdf.Dictionary, key: str) -> object:
    """The value of `key` in a widget or in the nearest of its parents that
    has one, as pikepdf reads them, up to a parent met before; None where
    none has."""
    met: set[tuple[int, int]] = set()
    node: object = annotation
    while isinstance(node, pikepdf.Dictionary):
        if key in node:
            return node[key]
        if node.is_indirect:
            if node.objgen in met:
                return None
            met.add(node.objgen)
        node = node.get("/Parent")
    return None


def has_text_field(pdf: pikepdf.Pdf) -> bool:
    """Whether a widget of _widgets() belongs to a text field, its /FT
    /Tx or inherited so."""
    return any(_inherited(annotation, "/FT") == "/Tx" for _, annotation in _widgets(pdf))


def reading(
    reader: Reader, original: bytes, decrypted: bytes, placed: list[int], text: bool
) -> tuple[dict, list[str]]:
    """What one library reads of the original and of the decrypted file, and
    the conditions it fails; `placed` is widget_pages() of the original, and
    `text` its has_text_field()."""
    widgets, fields, encrypted_before = reader.read(original)
    *after, encrypted = reader.read(decrypted)
    *again, encrypted_again = reader.read(reader.written(decrypted))
    filled = reader.filled(decrypted)
    entry = {
        "widgets": len(widgets),
        "as pikepdf": [widget[0] for widget in widgets] == placed,
        "fields": None if fields is None else len(fields),
        "digest": fields_digest(widgets, fields),
        "decrypted equal": after == [widgets, fields],
        "written equal": again == [widgets, fields],
        "original encrypted": encrypted_before,
        "decrypted encrypted": encrypted or encrypted_again,
        "filled": filled,
    }
    failed = [
        condition
        for condition, holds in (
            ("the original is encrypted", encrypted_before),
            ("as many widgets on each page as pikepdf finds", entry["as pikepdf"]),
            ("the decrypted file's field names and rectangles", entry["decrypted equal"]),
            ("the written file's field names and rectangles", entry["written equal"]),
            ("the decrypted file is not encrypted", not entry["decrypted encrypted"]),
            ("a text field where pikepdf finds one", (filled is not None) == text),
            ("the value set in a text field", filled is not False),
        )
        if not holds
    ]
    return entry, failed


def edited(data: bytes, edit: Callable[[pikepdf.Pdf], bool]) -> bytes | None:
    """`data` written anew by pikepdf after `edit`; None where `edit` finds
    nothing to change."""
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        if not edit(pdf):
            return None
        out = io.BytesIO()
        with warnings.catch_warnings():
            # pikepdf warns of the widgets a field left out leaves unreached.
            warnings.simplefilter("ignore", pikepdf.PageCopyWarning)
            pdf.save(out, deterministic_id=True)
        return out.getvalue()


def _move_widget(pdf: pikepdf.Pdf) -> bool:
    for _, annotation in _widgets(pdf):
        if "/Rect" in annotation:
            rect = [float(value) for value in annotation.Rect]
            annotation.Rect = pikepdf.Array([rect[0] + 1, rect[1], rect[2] + 1, rect[3]])
            return True
    return False


def _add_stroke(pdf: pikepdf.Pdf) -> bool:
    pdf.pages[0].contents_add(pikepdf.Stream(pdf, STROKE), prepend=False)
    return True


def _remove_page(pdf: pikepdf.Pdf) -> bool:
    del pdf.pages[-1]
    return True


def _leave_field_out(pdf: pikepdf.Pdf) -> bool:
    """The last entry of /AcroForm's /Fields taken out, its widgets left on
    the pages."""
    form = pdf.Root.get("/AcroForm")
    fields = form.get("/Fields") if isinstance(form, pikepdf.Dictionary) else None
    if not isinstance(fields, pikepdf.Array) or len(fields) == 0:
        return False
    del fields[-1]
    return True


def _unchanged(pdf: pikepdf.Pdf) -> bool:
    return True


def without_size(data: bytes) -> bytes | None:
    """`data` with the /Size of its last trailer, or of its cross-reference
    stream, taken out at the same length; None where there is none."""
    found = list(re.finditer(rb"/Size\s+\d+", data))
    if not found:
        return None
    last = found[-1]
    return data[: last.start()] + b" " * (last.end() - last.start()) + data[last.end() :]


def cut_short(data: bytes) -> bytes:
    return data[: len(data) // 2]


# The faults that change nothing, which each function has to return, so that
# what it refuses it refuses for what a fault changed: the right result, and
# the right result written anew by pikepdf, as the faults that edit it are.
CONTROL = "nothing changed"
REWRITTEN = "nothing changed, written anew"
CONTROLS = (CONTROL, REWRITTEN)
# Each fault of decrypt(): the result it makes from the right one and the input.
DECRYPTION_FAULTS: dict[str, Callable[[bytes, bytes], DecryptedBytes | None]] = {
    CONTROL: lambda right, _: DecryptedBytes(data=right, warnings=()),
    REWRITTEN: lambda right, _: _decrypted(edited(right, _unchanged)),
    "a widget moved": lambda right, _: _decrypted(edited(right, _move_widget)),
    "a stroke added": lambda right, _: _decrypted(edited(right, _add_stroke)),
    "a page removed": lambda right, _: _decrypted(edited(right, _remove_page)),
    "a field left out": lambda right, _: _decrypted(edited(right, _leave_field_out)),
    "still encrypted": lambda _, given: DecryptedBytes(data=given, warnings=()),
    "a warning": lambda right, _: DecryptedBytes(data=right, warnings=(WARNING,)),
}
# Each fault of recover(): the result it makes from the right one.
RECOVERY_FAULTS: dict[str, Callable[[RecoveredBytes], RecoveredBytes | None]] = {
    CONTROL: lambda right: right,
    REWRITTEN: lambda right: _recovered(right, edited(right.data, _unchanged)),
    "a page removed": lambda right: _recovered(right, edited(right.data, _remove_page)),
    "no /Size": lambda right: _recovered(right, without_size(right.data)),
    "cut short": lambda right: _recovered(right, cut_short(right.data)),
}


def _decrypted(data: bytes | None) -> DecryptedBytes | None:
    return None if data is None else DecryptedBytes(data=data, warnings=())


def _recovered(right: RecoveredBytes, data: bytes | None) -> RecoveredBytes | None:
    if data is None:
        return None
    return RecoveredBytes(data=data, page_count=right.page_count, warnings=right.warnings)


def faulted(name: str, made: object, call: Callable[[], object]) -> str:
    """The outcome of `call` while the QPDF backend's function `name` returns
    `made`: the name of the error it raised, or "returned"."""
    original = getattr(qpdf_backend, name)
    setattr(qpdf_backend, name, lambda *_: made)
    try:
        call()
    except pdfacts.PdfactsError as error:
        return type(error).__name__
    finally:
        setattr(qpdf_backend, name, original)
    return "returned"


def fault_outcomes(
    faults: dict, right: object, call: Callable[[], object], name: str, wanted: str, *given: bytes
) -> tuple[dict[str, str], list[str]]:
    """The outcome of each fault, "not applicable" where it finds nothing to
    change, and the faults whose outcome is not `wanted`, or for a control
    not "returned"."""
    outcomes = {}
    for fault, make in faults.items():
        made = make(right, *given)
        outcomes[fault] = "not applicable" if made is None else faulted(name, made, call)
    failed = [
        f"{name}: {fault}"
        for fault, outcome in outcomes.items()
        if outcome not in ("returned" if fault in CONTROLS else wanted, "not applicable")
    ]
    return outcomes, failed


# --- a file -----------------------------------------------------------------------------


def read_file(data: bytes, readers: dict[str, Reader]) -> dict:
    """The receipt's entry of one file."""
    failed: list[str] = []
    with pdfacts.open(data) as document:
        page_count = document.page_count
        found = pdfacts.inspect(document)
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        forms, missed = contents_part(found, pdf)
        failed += missed
        findings, missed = findings_part(found, pdf)
        failed += missed
        placed = widget_pages(pdf)
        text = has_text_field(pdf)
        encrypted = pdf.is_encrypted
        # pikepdf opens with the empty password; it matches the user password.
        owner = encrypted and not pdf.owner_password_matched

    decryption, readings, faults = "not encrypted", {}, {"decrypt": {}, "recover": {}}
    try:
        decrypted = pdfacts.decrypt(data).data
    except pdfacts.NotEncryptedError:
        decrypted = None
    if encrypted != (decrypted is not None):
        failed.append("decrypt() takes the file for encrypted as pikepdf reads it")
    if encrypted and not owner:
        failed.append("an owner password that is not the empty one")
    if decrypted is not None:
        decryption = hashlib.sha256(decrypted).hexdigest()
        for name, reader in readers.items():
            readings[name], missed = reading(reader, data, decrypted, placed, text)
            failed += [f"{name}: {condition}" for condition in missed]
        right = qpdf_backend.rewrite_decrypted(data, None)
        faults["decrypt"], missed = fault_outcomes(
            DECRYPTION_FAULTS,
            right.data,
            lambda: pdfacts.decrypt(data),
            "rewrite_decrypted",
            WANTED["decrypt"],
            data,
        )
        failed += missed

    recovered = pdfacts.recover(data)
    # An encrypted result differs from run to run (recover() keeps encryption).
    recovery = "encrypted" if decrypted is not None else hashlib.sha256(recovered.data).hexdigest()
    if decrypted is None:
        right = qpdf_backend.rewrite_recovered(data, None)
        faults["recover"], missed = fault_outcomes(
            RECOVERY_FAULTS,
            right,
            lambda: pdfacts.recover(data),
            "rewrite_recovered",
            WANTED["recover"],
        )
        failed += missed
    introduced = [
        outcome
        for outcomes in faults.values()
        for fault, outcome in outcomes.items()
        if fault not in CONTROLS and outcome != "not applicable"
    ]
    return {
        "page_count": page_count,
        "finding_count": sum(findings.values()),
        "fault_count": len(introduced),
        "inspection": inspection_digest(found),
        "decryption": decryption,
        "owner password": owner,
        "recovery": recovery,
        "contents": forms,
        "findings": findings,
        "readers": readings,
        "faults": faults,
        "failed": failed,
    }


def exit_status(receipt: dict) -> int:
    """0 when no file failed a condition and none raised an error."""
    if receipt["errors"] or any(entry["failed"] for entry in receipt["files"].values()):
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M5a gate: inspection, decryption, recovery.")
    parser.add_argument("--commit", required=True, type=commit_argument, metavar="SHA")
    parser.add_argument("--out", required=True, type=Path, help="the receipt to write")
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="manifest ids")
    parser.add_argument("--corpus", type=Path, default=ROOT / "bench" / "corpus" / "files")
    arguments = parser.parse_args(argv)

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if arguments.only:
        known = {row["id"] for row in rows}
        unknown = [given for given in dict.fromkeys(arguments.only) if given not in known]
        if unknown:
            print(", ".join(unknown) + ": not in the manifest", file=sys.stderr)
            return 2
        wanted = set(arguments.only)
        rows = [row for row in rows if row["id"] in wanted]

    checked: dict[str, bytes] = {}
    for row in rows:
        path = arguments.corpus / row["file"]
        try:
            data = path.read_bytes()
        except OSError as error:
            print(f"{row['file']}: cannot be read: {error}", file=sys.stderr)
            return 2
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            print(f"{row['file']}: the SHA-256 is not {row['sha256']}", file=sys.stderr)
            return 2
        checked[row["file"]] = data

    runtime.start()
    if engine_info().font_policy != "embedded-only":
        print("the font policy of the runtime is not embedded-only", file=sys.stderr)
        return 2

    try:
        versions = third_party_versions()
    except ImportError:
        print("pypdf and PyMuPDF are not installed: run with uv run --group bench", file=sys.stderr)
        return 2
    quiet()
    files: dict[str, dict] = {}
    errors: dict[str, str] = {}
    started = time.perf_counter()
    for row in rows:
        name = row["file"]
        try:
            entry = read_file(checked[name], READERS)
        except Exception as error:
            print(f"error {name} {type(error).__name__}")
            print(f"{name}: {error}", file=sys.stderr)
            errors[name] = type(error).__name__
        else:
            files[name] = entry
            print(
                f"{'failed' if entry['failed'] else 'ok'} {name} {entry['page_count']} pages"
                f" {entry['finding_count']} findings {entry['fault_count']} faults"
            )
            for condition in entry["failed"]:
                print(f"  failed: {condition}")
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "gate": "m5a",
        "environment": {
            "commit": arguments.commit,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_info": engine_info().as_dict() | versions,
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "errors": errors,
        "files": files,
        "partial": bool(arguments.only),
        "seconds": seconds,
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print_summary(len(rows), files)
    print(f"{len(errors)} errors, {seconds} s; receipt {arguments.out}")
    return exit_status(receipt)


def quiet() -> None:
    """pypdf logs what it repairs and PyMuPDF prints MuPDF's errors; the gate's
    output is its own."""
    import pymupdf

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


def third_party_versions() -> dict[str, str]:
    """The versions of the libraries the gate compares with, beside the engines'."""
    import pymupdf
    import pypdf

    return {"pypdf": pypdf.__version__, "pymupdf": pymupdf.VersionBind}


def print_summary(count: int, files: dict[str, dict]) -> None:
    """What each condition found, over the files read."""
    entries = list(files.values())
    encrypted = [entry for entry in entries if entry["decryption"] != "not encrypted"]
    pages = sum(entry["page_count"] for entry in entries)
    owned = sum(entry["owner password"] for entry in encrypted)
    print(
        f"{count} files, {pages} pages; {len(encrypted)} files encrypted,"
        f" {owned} of them with an owner password that is not the empty one"
    )

    def failing(prefixes: tuple[str, ...]) -> int:
        return sum(
            condition.startswith(prefixes) for entry in entries for condition in entry["failed"]
        )

    forms: Counter[str] = Counter()
    findings: Counter[str] = Counter()
    for entry in entries:
        forms.update(entry["contents"])
        findings.update(entry["findings"])
    read = sum(forms.values())
    print(
        "/Contents: "
        + ", ".join(f"{form} {forms[form]}" for form in FORMS)
        + f"; as pikepdf reads them on {read - failing(('/Contents',))} of {read} pages"
    )
    found = sum(findings.values())
    named = failing(("action ", "trigger ", "XFA stream ", "embedded file "))
    print(
        "active content: "
        + ", ".join(f"{key} {findings[key]}" for key in FINDINGS)
        + f"; in the object named: {found - named} of {found}"
    )
    for name in READERS:
        readings = [entry["readers"][name] for entry in encrypted]
        widgets = sum(reading["widgets"] for reading in readings)
        placed = sum(reading["as pikepdf"] for reading in readings)
        fields = [reading["fields"] for reading in readings if reading["fields"] is not None]
        tree = f", {sum(fields)} fields in the field tree" if fields else ""
        decrypted = sum(reading["decrypted equal"] for reading in readings)
        written = sum(reading["written equal"] for reading in readings)
        plain = sum(
            reading["original encrypted"] and not reading["decrypted encrypted"]
            for reading in readings
        )
        filled = sum(reading["filled"] is True for reading in readings)
        without = sum(reading["filled"] is None for reading in readings)
        print(
            f"{name}: {widgets} widgets, as many on each page as pikepdf finds in {placed} files"
            f"{tree}; field names and rectangles equal after decryption"
            f" in {decrypted} files, after writing in {written}; encrypted before and not after"
            f" in {plain};"
            f" a value set and read back in {filled}, no text field in {without}"
        )
    for function, faults in (("decrypt", DECRYPTION_FAULTS), ("recover", RECOVERY_FAULTS)):
        wanted = WANTED[function]
        counted = {}
        for fault in [*faults, *CONTROLS]:
            outcomes = [entry["faults"][function].get(fault) for entry in entries]
            introduced = [
                outcome for outcome in outcomes if outcome not in (None, "not applicable")
            ]
            right = "returned" if fault in CONTROLS else wanted
            counted[fault] = f"{introduced.count(right)} of {len(introduced)}"
        print(
            f"{function}(), faults refused: "
            + ", ".join(f"{fault} {counted[fault]}" for fault in faults if fault not in CONTROLS)
            + f"; the right result returned in {counted[CONTROL]},"
            f" written anew by pikepdf in {counted[REWRITTEN]}"
        )
    failed = sum(1 for entry in entries if entry["failed"])
    print(f"files that failed a condition: {failed}")


if __name__ == "__main__":
    raise SystemExit(main())
