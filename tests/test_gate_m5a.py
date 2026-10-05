"""The M5a gate: what it counts, what it checks, and when it fails.

The gate (bench/gates/m5a.py) inspects each file and checks what the
inspection names with pikepdf, decrypts each encrypted file and has two
third-party libraries read it, and introduces faults into the results of
decrypt() and recover(). Files are built with the byte-level builder; the
counts expected are what was written. pypdf and PyMuPDF are not loaded here:
the gate takes the libraries as readers, and the tests give it readers of
their own.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pdfacts
from pdfacts import ObjectRef, Unknown, UnknownReason
from pdfacts.backends.protocols import DecryptedBytes, RecoveredBytes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import compare  # noqa: E402
import m5a  # noqa: E402

from synthetic.builder import build, stream  # noqa: E402
from synthetic.encryption import encrypt  # noqa: E402

BODY = b"0 0 m 100 100 l S"
WIDGET = b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (Name) /Rect [10 10 100 30] /P 3 0 R"


def form(widget_entries: bytes = b"", catalog: bytes = b"", objects=None) -> bytes:
    """One page of 200 x 200, object 3, its content object 9, with one text
    field's widget, object 4."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R] >>" + catalog + b" >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> "
            b"/Contents 9 0 R /Annots [4 0 R] >>",
            4: WIDGET + widget_entries + b" >>",
            9: stream(BODY),
            **(objects or {}),
        },
        1,
    )


PLAIN = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> "
        b"/Contents 9 0 R >>",
        9: stream(BODY),
    },
    1,
)
# A page of the files below, 200 x 200, with no resources; its /Contents follows.
PAGE_OPENS = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>"
# Seven pages: /Contents a stream, a direct array of two streams and a null,
# an indirect array of two streams, none, a number, a null, and a reference to
# an object the file lacks.
FORMS = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R 6 0 R 7 0 R 8 0 R 12 0 R] /Count 7 >>",
        3: PAGE_OPENS + b" /Contents 9 0 R >>",
        4: PAGE_OPENS + b" /Contents [9 0 R null 10 0 R] >>",
        5: PAGE_OPENS + b" /Contents 11 0 R >>",
        6: PAGE_OPENS + b" >>",
        7: PAGE_OPENS + b" /Contents 5 >>",
        8: PAGE_OPENS + b" /Contents null >>",
        12: PAGE_OPENS + b" /Contents 20 0 R >>",
        9: stream(BODY),
        10: stream(BODY),
        11: b"[9 0 R 10 0 R]",
    },
    1,
)
# Two actions, one written in the catalog and one in object 5, each named by
# one entry; XFA, object 6; an embedded file, its specification object 7 and
# its stream object 8.
ACTIVE = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction << /S /JavaScript /JS (1) >> "
        b"/AcroForm << /Fields [4 0 R] /XFA [(template) 6 0 R] >> "
        b"/Names << /EmbeddedFiles << /Names [(f) 7 0 R] >> >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> "
        b"/Contents 9 0 R /Annots [4 0 R] >>",
        4: WIDGET + b" /AA << /K 5 0 R >> >>",
        5: b"<< /S /JavaScript /JS (2) >>",
        6: stream(b"<template/>"),
        7: b"<< /Type /Filespec /F (f) /EF << /F 8 0 R >> >>",
        8: stream(b"attached", b"/Type /EmbeddedFile"),
        9: stream(BODY),
    },
    1,
)
ENCRYPTED = encrypt(form(), user="", owner="o")
# What a reader of the tests reads of every file: one widget, the same before
# and after decryption and writing.
READ = [(0, "Name", (10.0, 10.0, 100.0, 30.0))]


def reader(
    before=READ,
    after=READ,
    written=READ,
    fields=(None, None, None),
    encrypted=(True, False, False),
    filled=True,
) -> m5a.Reader:
    """A reader of the tests. Of the input, the file that writes /Encrypt, it
    reads the widgets `before`; of the decrypted file `after`; of what it
    writes, the decrypted file followed by a marker, `written`. `fields` are
    the names of the field tree it reads of each, and `encrypted` whether it
    reads each as encrypted, in that order."""

    def read(data: bytes) -> tuple[list[tuple], list[str] | None, bool]:
        if data.endswith(b"%written"):
            return written, fields[2], encrypted[2]
        if b"/Encrypt" in data:
            return before, fields[0], encrypted[0]
        return after, fields[1], encrypted[1]

    return m5a.Reader(read, lambda data: data + b"%written", lambda data: filled)


READERS = {"one": reader(), "two": reader()}


def entry_of(data: bytes, readers=READERS) -> dict:
    return m5a.read_file(data, readers)


def test_1_a_file_is_counted_as_written():
    entry = entry_of(PLAIN)
    assert (entry["page_count"], entry["finding_count"]) == (1, 0)
    assert entry["contents"] == {
        "stream": 1,
        "array": 0,
        "indirect-array": 0,
        "absent": 0,
        "other": 0,
    }
    assert entry["findings"] == {"actions": 0, "triggers": 0, "xfa streams": 0, "embedded files": 0}
    assert entry["decryption"] == "not encrypted"
    assert entry["readers"] == {}
    assert entry["faults"] == {
        "decrypt": {},
        "recover": {
            "nothing changed": "returned",
            "nothing changed, written anew": "returned",
            "a page removed": "RecoveryFailedError",
            "no /Size": "RecoveryFailedError",
            "cut short": "RecoveryFailedError",
        },
    }
    assert entry["fault_count"] == 3
    assert entry["failed"] == []
    assert len(entry["inspection"]) == len(entry["recovery"]) == 64
    assert m5a.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 0


def test_2_each_form_of_contents():
    entry = entry_of(FORMS)
    assert entry["contents"] == {
        "stream": 1,
        "array": 1,
        "indirect-array": 1,
        "absent": 3,
        "other": 1,
    }
    assert entry["failed"] == []


@pytest.mark.parametrize(
    ("page", "change"),
    [
        (2, lambda form, streams, others, array: ("array", streams, others, None)),
        (1, lambda form, streams, others, array: (form, streams[:1], others, array)),
        (1, lambda form, streams, others, array: (form, streams[::-1], others, array)),
        (2, lambda form, streams, others, array: (form, [(3, 0), (3, 0)], others, array)),
        (1, lambda form, streams, others, array: (form, streams, others - 1, array)),
        (2, lambda form, streams, others, array: (form, streams, others, (3, 0))),
    ],
    ids=[
        "the form",
        "a stream fewer",
        "the streams in another order",
        "other objects",
        "the other entries",
        "the array",
    ],
)
def test_3_contents_read_otherwise_fail(monkeypatch, page, change):
    """pikepdf's reading of one page changed: that page fails."""
    written = m5a.contents_written

    def changed(pdf):
        found = written(pdf)
        found[page] = change(*found[page])
        return found

    monkeypatch.setattr(m5a, "contents_written", changed)
    assert entry_of(FORMS)["failed"] == [f"/Contents of page {page}"]


def test_4_each_finding_named_by_its_object():
    entry = entry_of(ACTIVE)
    assert entry["findings"] == {"actions": 2, "triggers": 2, "xfa streams": 1, "embedded files": 1}
    assert entry["finding_count"] == 6
    assert entry["failed"] == []


def misnamed(monkeypatch, change) -> None:
    """The inspection's active content changed by `change` before the gate
    checks it."""
    inspect = pdfacts.inspect

    def changed(document):
        found = inspect(document)
        return dataclasses.replace(found, active_content=change(found.active_content))

    monkeypatch.setattr(pdfacts, "inspect", changed)


PAGE = ObjectRef(object_number=3, generation=0)
UNKNOWN = Unknown(reason=UnknownReason.INVALID, detail="made unknown by the test")


def action(index: int, **change):
    return lambda c: dataclasses.replace(
        c,
        actions=tuple(
            dataclasses.replace(a, **change) if i == index else a for i, a in enumerate(c.actions)
        ),
    )


def trigger(index: int, **change):
    return lambda c: dataclasses.replace(
        c,
        triggers=tuple(
            dataclasses.replace(t, **change) if i == index else t for i, t in enumerate(c.triggers)
        ),
    )


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        # An action is looked for where its entries place it, and an entry
        # names an action of its kind: changed, the action fails with the one
        # entry that names it. An action no entry names any more is looked
        # for by its kind.
        (action(0, ref=PAGE), ["action 0", "trigger 0"]),
        (action(1, kind=None), ["action 1", "trigger 1"]),
        (trigger(1, owner=PAGE), ["action 1", "trigger 1"]),
        (trigger(1, event="F"), ["action 1", "trigger 1"]),
        (trigger(0, action=1), ["trigger 0"]),
        (lambda c: dataclasses.replace(c, xfa=(PAGE,)), ["XFA stream 0"]),
        (
            lambda c: dataclasses.replace(
                c,
                embedded_files=(dataclasses.replace(c.embedded_files[0], specification=PAGE),),
            ),
            ["embedded file 0"],
        ),
        (lambda c: dataclasses.replace(c, embedded_streams=((PAGE,),)), ["embedded file 0"]),
        (lambda c: dataclasses.replace(c, embedded_streams=((),)), ["embedded file 0"]),
    ],
    ids=[
        "an action's object",
        "an action's kind",
        "a trigger's owner",
        "a trigger's event",
        "a trigger's action",
        "an XFA stream",
        "an embedded file",
        "its stream",
        "no stream",
    ],
)
def test_5_a_finding_named_by_another_object_fails(monkeypatch, change, failed):
    """Each finding changed to name what its object does not hold."""
    misnamed(monkeypatch, change)
    assert entry_of(ACTIVE)["failed"] == failed


def read_digest(widgets: list[tuple], fields: list[str] | None) -> str:
    """The digest of what a reader read, from the values the test gives it."""
    digest = m5a.Digest()
    for page, name, rect in widgets:
        digest.update(page, len(name), *(ord(character) for character in name), *rect)
    if fields is not None:
        digest.update("fields", len(fields))
        for name in fields:
            digest.update(len(name), *(ord(character) for character in name))
    return digest.hexdigest()


def test_6_an_encrypted_file():
    entry = entry_of(ENCRYPTED)
    assert re.fullmatch(r"[0-9a-f]{64}", entry["decryption"])
    assert entry["decryption"] != hashlib.sha256(ENCRYPTED).hexdigest()
    assert entry["recovery"] == "encrypted"
    assert entry["readers"] == {
        name: {
            "widgets": 1,
            "as pikepdf": True,
            "fields": None,
            "digest": read_digest(READ, None),
            "decrypted equal": True,
            "written equal": True,
            "original encrypted": True,
            "decrypted encrypted": False,
            "filled": True,
        }
        for name in READERS
    }
    assert entry["faults"] == {
        "decrypt": {
            "nothing changed": "returned",
            "nothing changed, written anew": "returned",
            "a widget moved": "DecryptionFailedError",
            "a stroke added": "DecryptionFailedError",
            "a page removed": "DecryptionFailedError",
            "a field left out": "DecryptionFailedError",
            "still encrypted": "DecryptionFailedError",
            "a warning": "DecryptionFailedError",
        },
        "recover": {},
    }
    assert entry["fault_count"] == 6
    assert entry["failed"] == []


@pytest.mark.parametrize(
    ("read", "failed"),
    [
        (
            {"before": [], "after": [], "written": []},
            "as many widgets on each page as pikepdf finds",
        ),
        (
            dict.fromkeys(("before", "after", "written"), [(1, "Name", (10.0, 10.0, 100.0, 30.0))]),
            "as many widgets on each page as pikepdf finds",
        ),
        ({"after": []}, "the decrypted file's field names and rectangles"),
        (
            {"written": [(0, "Other", (10.0, 10.0, 100.0, 30.0))]},
            "the written file's field names and rectangles",
        ),
        (
            {"fields": (["a", "a.b"], ["a"], ["a", "a.b"])},
            "the decrypted file's field names and rectangles",
        ),
        (
            {"fields": (["a", "a.b"], ["a", "a.b"], ["a.b", "a"])},
            "the written file's field names and rectangles",
        ),
        ({"encrypted": (True, True, False)}, "the decrypted file is not encrypted"),
        ({"encrypted": (True, False, True)}, "the decrypted file is not encrypted"),
        ({"encrypted": (False, False, False)}, "the original is encrypted"),
        ({"filled": None}, "a text field where pikepdf finds one"),
        ({"filled": False}, "the value set in a text field"),
    ],
    ids=[
        "nothing read",
        "on another page",
        "after decryption",
        "after writing",
        "a field lost from the tree",
        "the tree in another order after writing",
        "still encrypted",
        "the reader's own file encrypted",
        "not read as encrypted",
        "no text field found",
        "not set",
    ],
)
def test_7_a_reader_that_reads_otherwise_fails(read, failed):
    entry = entry_of(ENCRYPTED, {"one": reader(**read), "two": reader()})
    assert entry["failed"] == [f"one: {failed}"]


def test_8_a_file_without_a_text_field_is_not_failed_for_it():
    """No widget, so no text field as pikepdf reads the file: a reader that
    finds none passes."""
    nothing = reader(before=[], after=[], written=[], filled=None)
    entry = entry_of(encrypt(PLAIN, user="", owner="o"), {"one": nothing})
    assert entry["readers"]["one"]["filled"] is None
    assert entry["failed"] == []


def test_9_a_fault_with_nothing_to_change():
    """No widget to move and no field to leave out: those faults do not
    apply, and are not counted."""
    nothing = reader(before=[], after=[], written=[], filled=None)
    entry = entry_of(encrypt(PLAIN, user="", owner="o"), {"one": nothing})
    assert entry["faults"]["decrypt"]["a widget moved"] == "not applicable"
    assert entry["fault_count"] == 4
    assert entry["failed"] == []


@pytest.mark.parametrize(
    ("data", "function", "faults", "name", "fault"),
    [
        (
            ENCRYPTED,
            "decrypt",
            "DECRYPTION_FAULTS",
            "rewrite_decrypted",
            lambda right, _: DecryptedBytes(data=right, warnings=()),
        ),
        (
            PLAIN,
            "recover",
            "RECOVERY_FAULTS",
            "rewrite_recovered",
            lambda right: RecoveredBytes(data=right.data, page_count=right.page_count, warnings=()),
        ),
    ],
    ids=["decrypt", "recover"],
)
def test_10_a_fault_the_function_does_not_catch_fails(
    monkeypatch, data, function, faults, name, fault
):
    """A "fault" that is the right result: the function returns it."""
    monkeypatch.setattr(m5a, faults, {"no fault": fault})
    entry = entry_of(data)
    assert entry["faults"][function] == {"no fault": "returned"}
    assert entry["failed"] == [f"{name}: no fault"]


def test_11_the_backend_is_restored_after_each_fault():
    entry_of(ENCRYPTED)
    entry_of(PLAIN)
    assert m5a.qpdf_backend.rewrite_decrypted.__module__ == "pdfacts.backends.qpdf"
    assert m5a.qpdf_backend.rewrite_recovered.__module__ == "pdfacts.backends.qpdf"


class Colour(enum.Enum):
    RED = "red"


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("red", Colour.RED),
        ("ab", b"ab"),
        (1, 1.0),
        (True, 1),
        (None, "none"),
        ((1, 2), (1, (2,))),
    ],
    ids=[
        "a text and an enum",
        "a text and bytes",
        "an int and a float",
        "a bool and an int",
        "none and a text",
        "nesting",
    ],
)
def test_12_the_digest_tells_values_apart(one, other):
    first, second = m5a.Digest(), m5a.Digest()
    m5a.put(first, one)
    m5a.put(second, other)
    assert first.hexdigest() != second.hexdigest()


def test_13_the_digest_refuses_another_type():
    with pytest.raises(TypeError):
        m5a.put(m5a.Digest(), [1, 2])


def test_14_without_size():
    assert m5a.without_size(b"<< /Size 12 /Root 1 0 R >>") == b"<<          /Root 1 0 R >>"
    assert m5a.without_size(b"/Size 3 x /Size 12 y") == b"/Size 3 x          y"
    assert m5a.without_size(b"<< /Root 1 0 R >>") is None


def corpus(tmp_path, monkeypatch, files: dict[str, bytes], listed=None) -> Path:
    """`files` in a folder, and a manifest that lists them, ids 01, 02, ...;
    `listed` gives other bytes to hash for a file. The gate takes the readers
    of the tests."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "file", "sha256"])
        for number, (name, data) in enumerate(files.items(), 1):
            (folder / name).write_bytes(data)
            hashed = (listed or {}).get(name, data)
            writer.writerow([f"{number:02d}", name, hashlib.sha256(hashed).hexdigest()])
    monkeypatch.setattr(m5a, "MANIFEST", manifest)
    monkeypatch.setattr(m5a, "READERS", READERS)
    monkeypatch.setattr(m5a, "third_party_versions", lambda: {"pypdf": "1", "pymupdf": "2"})
    monkeypatch.setattr(m5a, "quiet", lambda: None)
    return folder


def run(folder: Path, out: Path, *more: str) -> int:
    return m5a.main(["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder), *more])


READ_BY = (
    ": 1 widgets, as many on each page as pikepdf finds in 1 files; field names and rectangles"
    " equal after decryption in 1 files,"
    " after writing in 1; encrypted before and not after in 1; a value set and read back in 1,"
    " no text field in 0\n"
)
SUMMARY = (
    "2 files, 2 pages; 1 files encrypted, 1 of them with an owner password that is not the"
    " empty one\n"
    "/Contents: stream 2, array 0, indirect-array 0, absent 0, other 0;"
    " as pikepdf reads them on 2 of 2 pages\n"
    "active content: actions 0, triggers 0, xfa streams 0, embedded files 0;"
    " in the object named: 0 of 0\n"
    f"one{READ_BY}"
    f"two{READ_BY}"
    "decrypt(), faults refused: a widget moved 1 of 1, a stroke added 1 of 1,"
    " a page removed 1 of 1, a field left out 1 of 1, still encrypted 1 of 1, a warning 1 of 1;"
    " the right result returned in 1 of 1, written anew by pikepdf in 1 of 1\n"
    "recover(), faults refused: a page removed 1 of 1, no /Size 1 of 1, cut short 1 of 1;"
    " the right result returned in 1 of 1, written anew by pikepdf in 1 of 1\n"
    "files that failed a condition: 0\n"
)


def test_15_the_receipt(tmp_path, monkeypatch, capsys):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": ENCRYPTED})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(
        "ok a.pdf 1 pages 0 findings 3 faults\nok b.pdf 1 pages 0 findings 6 faults\n"
    )
    assert SUMMARY in printed
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["gate"] == "m5a"
    assert receipt["partial"] is False
    assert receipt["errors"] == {}
    assert sorted(receipt["files"]) == ["a.pdf", "b.pdf"]
    engines = receipt["environment"]["engine_info"]
    assert (engines["pypdf"], engines["pymupdf"]) == ("1", "2")
    assert receipt["environment"]["commit"] == "0" * 40
    assert engines["font_policy"] == "embedded-only"


def test_16_one_file_that_fails_fails_the_run(tmp_path, monkeypatch, capsys):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": ENCRYPTED})
    monkeypatch.setattr(
        m5a, "READERS", {"one": reader(after=[], fields=(["a"], ["a"], ["a"]), filled=False)}
    )
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["files"]["b.pdf"]["failed"] == [
        "one: the decrypted file's field names and rectangles",
        "one: the value set in a text field",
    ]
    assert receipt["files"]["a.pdf"]["failed"] == []
    printed = capsys.readouterr().out
    assert "failed b.pdf 1 pages 0 findings 6 faults\n" in printed
    assert (
        "one: 1 widgets, as many on each page as pikepdf finds in 1 files, 1 fields in the field"
        " tree; field names and rectangles equal after"
        " decryption in 0 files,"
        " after writing in 1; encrypted before and not after in 1; a value set and read back"
        " in 0, no text field in 0\n"
    ) in printed
    assert "files that failed a condition: 1\n" in printed


def test_17_an_error_fails_the_run(tmp_path, monkeypatch):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": b"%PDF-1.7\nnothing"})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["errors"] == {"b.pdf": "DamagedFileError"}


@pytest.mark.parametrize(
    "refusal",
    ["another hash", "a file missing", "an id not listed", "fonts of the host", "no group bench"],
)
def test_18_a_run_refused(tmp_path, monkeypatch, refusal):
    listed = {"a.pdf": b"other"} if refusal == "another hash" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN}, listed)
    if refusal == "a file missing":
        (folder / "a.pdf").unlink()
    if refusal == "fonts of the host":
        monkeypatch.setattr(m5a, "engine_info", lambda: SimpleNamespace(font_policy="host"))
    if refusal == "no group bench":

        def not_installed():
            raise ImportError("made so by the test")

        monkeypatch.setattr(m5a, "third_party_versions", not_installed)
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()


def test_19_only_the_files_named(tmp_path, monkeypatch):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": ENCRYPTED})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(receipt["files"]) == ["b.pdf"]
    assert receipt["partial"] is True


def test_20_two_receipts_compared(tmp_path, monkeypatch, capsys):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": ENCRYPTED})
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert run(folder, first) == run(folder, second) == 0
    assert compare.main([str(first), str(second)]) == 0
    receipt = json.loads(second.read_text(encoding="utf-8"))
    receipt["files"]["b.pdf"]["readers"]["one"]["widgets"] = 2
    second.write_text(json.dumps(receipt), encoding="utf-8")
    capsys.readouterr()
    assert compare.main([str(first), str(second)]) == 1
    assert "| b.pdf | identical | differs | identical | identical |" in capsys.readouterr().out


def test_21_the_gate_is_loaded_and_run_without_pypdf_and_pymupdf(monkeypatch):
    """The tests run without the dependency group "bench": the gate imports
    the libraries only where it reads with them."""
    for name in ("pypdf", "pymupdf", "fitz"):
        monkeypatch.setitem(sys.modules, name, None)
    spec = importlib.util.spec_from_file_location("m5a_alone", ROOT / "bench" / "gates" / "m5a.py")
    alone = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, alone)
    spec.loader.exec_module(alone)
    assert alone.read_file(ENCRYPTED, READERS)["failed"] == []
    with pytest.raises(ImportError):
        alone.third_party_versions()


# Four actions, each of a kind the corpus lacks: /S a string, in object 4,
# named by the catalog's /OpenAction; no /S, in object 5, its /Next; a name
# that is no UTF-8, written in the page, object 3, under /AA /O; and in object
# 6, which nothing names, no /S and /Type /Action.
ODD_KINDS = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" /AA << /O << /S /Go#E9To >> >> >>",
        4: b"<< /S (text) /Next 5 0 R >>",
        5: b"<< /JS (x) >>",
        6: b"<< /Type /Action /JS (y) >>",
    },
    1,
)


def test_22_an_action_of_each_kind_in_its_object():
    entry = entry_of(ODD_KINDS)
    assert entry["findings"] == {"actions": 4, "triggers": 2, "xfa streams": 0, "embedded files": 0}
    assert entry["failed"] == []


def kind_of(index: int, source: int):
    """The kind of action `index` made that of action `source`."""
    return lambda c: dataclasses.replace(
        c,
        actions=tuple(
            dataclasses.replace(a, kind=c.actions[source].kind) if i == index else a
            for i, a in enumerate(c.actions)
        ),
    )


@pytest.mark.parametrize(
    ("index", "source", "failed"),
    [
        # Action 1 is found through the /Next of action 0's dictionary.
        (0, 1, ["action 0", "action 1", "trigger 0"]),
        (0, 2, ["action 0", "action 1", "trigger 0"]),
        (1, 0, ["action 1"]),
        (1, 2, ["action 1"]),
        (2, 0, ["action 2", "trigger 1"]),
        (2, 1, ["action 2", "trigger 1"]),
        (3, 0, ["action 3"]),
        (3, 2, ["action 3"]),
    ],
    ids=[
        "unknown as none",
        "unknown as a name",
        "none as unknown",
        "none as a name",
        "a name as unknown",
        "a name as none",
        "none named by nothing as unknown",
        "none named by nothing as a name",
    ],
)
def test_23_an_action_of_another_kind_fails(monkeypatch, index, source, failed):
    misnamed(monkeypatch, kind_of(index, source))
    assert entry_of(ODD_KINDS)["failed"] == failed


def test_24_an_action_that_nothing_names_is_looked_for_by_its_kind(monkeypatch):
    """Placed in the page, which has no /S and no /Type /Action."""
    misnamed(monkeypatch, action(3, ref=PAGE))
    assert entry_of(ODD_KINDS)["failed"] == ["action 3"]


# A file specification written in a FileAttachment annotation, object 4, its
# /EF naming object 6 under /UF and object 5 under /F.
ATTACHED = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" /Annots [4 0 R] >>",
        4: b"<< /Type /Annot /Subtype /FileAttachment /Rect [0 0 10 10]"
        b" /FS << /Type /Filespec /F (a) /EF << /UF 6 0 R /F 5 0 R >> >> >>",
        5: stream(b"one", b"/Type /EmbeddedFile"),
        6: stream(b"two", b"/Type /EmbeddedFile"),
    },
    1,
)


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (lambda c: c, []),
        (
            lambda c: dataclasses.replace(c, embedded_streams=(c.embedded_streams[0][::-1],)),
            ["embedded file 0"],
        ),
    ],
    ids=["as written", "its streams in another order"],
)
def test_25_a_file_specification_written_in_another_object(monkeypatch, change, failed):
    misnamed(monkeypatch, change)
    entry = entry_of(ATTACHED)
    assert entry["findings"]["embedded files"] == 1
    assert entry["failed"] == failed


# The catalog's JavaScript name tree, a node of its own, object 4: one action
# in object 5, one written in the node.
NAME_TREE = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R /Names << /JavaScript 4 0 R >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" >>",
        4: b"<< /Names [(a) 5 0 R (b) << /S /JavaScript /JS (2) >>] >>",
        5: b"<< /S /JavaScript /JS (1) >>",
    },
    1,
)


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (lambda c: c, []),
        # The node's two entries take its two values in order: the first takes
        # the action written in the node, and the second finds none left.
        (action(0, ref=ObjectRef(object_number=4, generation=0)), ["action 1", "trigger 1"]),
        # The second entry finds no second action in object 5; the action
        # written in the node, named by none, is looked for by its kind.
        (trigger(1, action=0), ["trigger 1"]),
    ],
    ids=["as written", "the action of object 5 placed in the node", "both entries naming one"],
)
def test_26_the_entries_of_the_name_tree(monkeypatch, change, failed):
    misnamed(monkeypatch, change)
    entry = entry_of(NAME_TREE)
    assert entry["findings"]["triggers"] == 2
    assert entry["failed"] == failed


def test_27_a_fault_refused_with_another_error_fails(monkeypatch):
    """The error wanted of decrypt() made another: each fault is then refused
    with an error that is not the one wanted."""
    monkeypatch.setitem(m5a.WANTED, "decrypt", "NotEncryptedError")
    entry = entry_of(ENCRYPTED)
    faults = [fault for fault in m5a.DECRYPTION_FAULTS if fault not in m5a.CONTROLS]
    assert entry["faults"]["decrypt"] == {
        **dict.fromkeys(m5a.CONTROLS, "returned"),
        **dict.fromkeys(faults, "DecryptionFailedError"),
    }
    assert entry["failed"] == [f"rewrite_decrypted: {fault}" for fault in faults]


@pytest.mark.parametrize("control", ["nothing changed", "nothing changed, written anew"])
@pytest.mark.parametrize(
    ("faults", "make", "data", "name"),
    [
        (
            "DECRYPTION_FAULTS",
            lambda right, _: m5a._decrypted(m5a.edited(right, m5a._remove_page)),
            ENCRYPTED,
            "rewrite_decrypted",
        ),
        (
            "RECOVERY_FAULTS",
            lambda right: m5a._recovered(right, m5a.cut_short(right.data)),
            PLAIN,
            "rewrite_recovered",
        ),
    ],
    ids=["decrypt", "recover"],
)
def test_31_a_control_refused_fails(monkeypatch, faults, make, data, name, control):
    """A control made to change the result: the function refuses it, and the
    file fails, as where a function refused every result."""
    monkeypatch.setitem(getattr(m5a, faults), control, make)
    entry = entry_of(data)
    assert f"{name}: {control}" in entry["failed"]


# An action whose /Next names two, objects 5 and 6, in that order.
SEQUENCE = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" >>",
        4: b"<< /S /JavaScript /JS (1) /Next [5 0 R 6 0 R] >>",
        5: b"<< /S /JavaScript /JS (2) >>",
        6: b"<< /S /JavaScript /JS (3) >>",
    },
    1,
)


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (lambda c: c, []),
        (
            lambda c: dataclasses.replace(c, sequences=(c.sequences[0][::-1],)),
            ["action 1", "action 2"],
        ),
        (
            lambda c: dataclasses.replace(c, sequences=(c.sequences[0][:1],)),
            ["action 1"],
        ),
    ],
    ids=["as written", "in the other order", "one left out"],
)
def test_28_the_actions_a_next_names_in_order(monkeypatch, change, failed):
    """An action left out of the /Next is named by nothing, and found by its
    kind in its object; the /Next names one action more than its list."""
    misnamed(monkeypatch, change)
    assert entry_of(SEQUENCE)["failed"] == failed


# A link annotation, object 4, whose /A is written in it.
LINK = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" /Annots [4 0 R] >>",
        4: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A << /S /URI /URI (x) >> >>",
    },
    1,
)


@pytest.mark.parametrize(
    ("change", "failed"),
    [(lambda c: c, []), (action(0, ref=PAGE), ["action 0", "trigger 0"])],
    ids=["as written", "the action placed in the page"],
)
def test_29_the_action_of_an_annotation(monkeypatch, change, failed):
    misnamed(monkeypatch, change)
    entry = entry_of(LINK)
    assert (entry["findings"]["actions"], entry["findings"]["triggers"]) == (1, 1)
    assert entry["failed"] == failed


def test_30_a_stream_that_ef_names_twice():
    """/F and /UF name object 5: the inspection lists it once."""
    data = ATTACHED.replace(b"/UF 6 0 R /F 5 0 R", b"/UF 5 0 R /F 5 0 R")
    entry = entry_of(data)
    assert entry["findings"]["embedded files"] == 1
    assert entry["failed"] == []


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (
            lambda c: dataclasses.replace(c, xfa=(ObjectRef(object_number=9, generation=0),)),
            ["XFA stream 0"],
        ),
        (lambda c: dataclasses.replace(c, xfa=(*c.xfa, c.xfa[0])), ["XFA stream 1"]),
        (lambda c: dataclasses.replace(c, xfa=()), ["the XFA streams"]),
        (lambda c: dataclasses.replace(c, xfa=None), ["the XFA streams"]),
    ],
    ids=["another stream", "one more", "none", "no /XFA"],
)
def test_32_the_xfa_streams_as_xfa_names_them(monkeypatch, change, failed):
    """ACTIVE's /XFA names object 6; object 9 is the page's content stream."""
    misnamed(monkeypatch, change)
    assert entry_of(ACTIVE)["failed"] == failed


@pytest.mark.parametrize(
    ("xfa", "streams", "failed"),
    [
        (b"6 0 R", 1, []),
        (b"[(a) 6 0 R (b) 7 0 R]", 2, []),
        (b"[(template) 6 0 R (data)]", 0, []),
    ],
    ids=["a stream", "two packets", "not pairs"],
)
def test_33_each_form_of_xfa(xfa, streams, failed):
    """/XFA a stream, an array of pairs, and an array that is not of pairs,
    which the report gives as unknown and pikepdf reads as invalid."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [] /XFA " + xfa + b" >> >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: PAGE_OPENS + b" >>",
            6: stream(b"<template/>"),
            7: stream(b"<data/>"),
        },
        1,
    )
    entry = entry_of(data)
    assert entry["findings"]["xfa streams"] == streams
    assert entry["failed"] == failed


# The catalog writes its JavaScript name tree, with one action without /S,
# and its EmbeddedFiles tree, with one file specification, which has no /S
# either.
TWO_TREES = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R /Names << /JavaScript << /Names [(a) << /JS (1) >>] >>"
        b" /EmbeddedFiles << /Names [(f) << /Type /Filespec /F (f) /EF << /F 5 0 R >> >>] >> >> >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: PAGE_OPENS + b" >>",
        5: stream(b"attached", b"/Type /EmbeddedFile"),
    },
    1,
)


def test_34_the_javascript_tree_alone():
    """The report given a second entry of the JavaScript tree: the value of
    /EmbeddedFiles, which has no /S, is not taken for its action."""
    entry = entry_of(TWO_TREES)
    assert entry["findings"] == {"actions": 1, "triggers": 1, "xfa streams": 0, "embedded files": 1}
    assert entry["failed"] == []


def test_35_a_second_entry_of_the_javascript_tree_fails(monkeypatch):
    misnamed(monkeypatch, lambda c: dataclasses.replace(c, triggers=(*c.triggers, c.triggers[0])))
    assert entry_of(TWO_TREES)["failed"] == ["trigger 1"]


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (
            lambda found: dataclasses.replace(found, pages=UNKNOWN, contents=UNKNOWN),
            ["the pages' /Contents are unknown"],
        ),
        (
            lambda found: dataclasses.replace(found, active_content=UNKNOWN),
            ["the active content is unknown"],
        ),
        (
            lambda found: dataclasses.replace(
                found,
                active_content=dataclasses.replace(
                    found.active_content, actions=UNKNOWN, sequences=UNKNOWN, triggers=UNKNOWN
                ),
            ),
            ["the actions or their entries are unknown"],
        ),
        (
            lambda found: dataclasses.replace(
                found, active_content=dataclasses.replace(found.active_content, xfa=UNKNOWN)
            ),
            ["the XFA streams"],
        ),
    ],
    ids=["the pages", "the active content", "the actions and their entries", "the XFA"],
)
def test_36_a_part_unknown_fails(monkeypatch, change, failed):
    """What the gate cannot check fails: an unknown part, where pikepdf reads
    the file; unknown XFA only where pikepdf finds /XFA valid."""
    inspect = pdfacts.inspect
    monkeypatch.setattr(pdfacts, "inspect", lambda document: change(inspect(document)))
    assert entry_of(ACTIVE)["failed"] == failed


def test_37_the_summary_counts_what_failed(tmp_path, monkeypatch, capsys):
    """One page's /Contents and two findings made wrong, and the XFA."""
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": FORMS, "b.pdf": ACTIVE})
    written = m5a.contents_written

    def changed(pdf):
        found = written(pdf)
        if len(found) == 7:
            found[0] = ("array", *found[0][1:])
        return found

    monkeypatch.setattr(m5a, "contents_written", changed)
    misnamed(
        monkeypatch,
        lambda c: dataclasses.replace(c, xfa=(PAGE,), embedded_streams=((PAGE,),)) if c.xfa else c,
    )
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    printed = capsys.readouterr().out
    assert "; as pikepdf reads them on 7 of 8 pages\n" in printed
    assert "; in the object named: 4 of 6\n" in printed
    assert "files that failed a condition: 2\n" in printed


# Two pages: on the first a widget, a link and a null in /Annots; on the
# second two widgets; a third whose /Annots is a dictionary.
WIDGET_PAGES = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        3: PAGE_OPENS + b" /Annots [6 0 R << /Subtype /Link /Rect [0 0 1 1] >> null] >>",
        4: PAGE_OPENS + b" /Annots [6 0 R 7 0 R] >>",
        5: PAGE_OPENS + b" /Annots << /Subtype /Widget >> >>",
        6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 1 1] >>",
        7: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 1 1] >>",
    },
    1,
)


def test_38_the_widgets_pikepdf_finds_on_each_page():
    with m5a.pikepdf.open(m5a.io.BytesIO(WIDGET_PAGES)) as pdf:
        assert m5a.widget_pages(pdf) == [0, 1, 1]


class Node(dict):
    """A dictionary as pypdf gives it, which resolves to itself."""

    def get_object(self):
        return self


def test_39_a_widget_s_parents_once_each():
    """A widget, its parent and its grandparent, whose /Parent is the widget:
    each is read once, the name from the top, /FT from the nearest."""
    top = Node({"/T": "top", "/FT": "/Tx"})
    middle = Node({"/T": "middle", "/Parent": top})
    widget = Node({"/Subtype": "/Widget", "/Parent": middle})
    top["/Parent"] = widget
    assert list(m5a._pypdf_chain(widget)) == [widget, middle, top]
    assert m5a._pypdf_name(widget) == "top.middle"
    assert m5a._pypdf_inherited(widget, "/FT") == "/Tx"
    middle["/FT"] = "/Ch"
    assert m5a._pypdf_inherited(widget, "/FT") == "/Ch"
    assert m5a._pypdf_inherited(widget, "/V") is None


@pytest.mark.parametrize(
    ("faults", "data", "make"),
    [
        (
            "DECRYPTION_FAULTS",
            ENCRYPTED,
            lambda make: make(m5a.qpdf_backend.rewrite_decrypted(ENCRYPTED, None).data, ENCRYPTED),
        ),
        (
            "RECOVERY_FAULTS",
            PLAIN,
            lambda make: make(m5a.qpdf_backend.rewrite_recovered(PLAIN, None)),
        ),
    ],
    ids=["decrypt", "recover"],
)
def test_40_the_second_control_is_written_anew(monkeypatch, faults, data, make):
    """The second control's result is the right one written anew by pikepdf,
    as the faults that edit it are, with nothing edited: the same page."""
    edits = []
    edited = m5a.edited

    def recorded(data, edit):
        edits.append(edit)
        return edited(data, edit)

    monkeypatch.setattr(m5a, "edited", recorded)
    written = make(getattr(m5a, faults)[m5a.REWRITTEN])
    assert edits == [m5a._unchanged]
    with m5a.pikepdf.open(m5a.io.BytesIO(written.data)) as pdf:
        assert len(pdf.pages) == 1
        assert pdf.pages[0].obj.Contents.read_bytes() == BODY


def test_41_the_summary_counts_the_widgets_and_the_second_control(tmp_path, monkeypatch, capsys):
    """A reader that reads no widget, and the second control made to lose a
    page, which decrypt() refuses."""
    folder = corpus(tmp_path, monkeypatch, {"b.pdf": ENCRYPTED})
    monkeypatch.setattr(m5a, "READERS", {"one": reader(before=[], after=[], written=[])})
    monkeypatch.setitem(
        m5a.DECRYPTION_FAULTS,
        m5a.REWRITTEN,
        lambda right, _: m5a._decrypted(m5a.edited(right, m5a._remove_page)),
    )
    assert run(folder, tmp_path / "receipt.json") == 1
    printed = capsys.readouterr().out
    assert "one: 0 widgets, as many on each page as pikepdf finds in 0 files;" in printed
    assert "; the right result returned in 1 of 1, written anew by pikepdf in 0 of 1\n" in printed


def test_42_a_file_decrypt_takes_for_not_encrypted_fails(monkeypatch):
    """decrypt() made to take the encrypted file for one that is not:
    pikepdf reads it as encrypted, and the file fails."""

    def not_encrypted(data):
        raise pdfacts.NotEncryptedError()

    monkeypatch.setattr(pdfacts, "decrypt", not_encrypted)
    entry = entry_of(ENCRYPTED)
    assert entry["decryption"] == "not encrypted"
    assert "decrypt() takes the file for encrypted as pikepdf reads it" in entry["failed"]


def fields_of(widget: bytes, objects=None) -> bytes:
    """One page with a button's widget, object 4, and `widget`, object 5;
    `objects` add the parents it names."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: PAGE_OPENS + b" /Annots [4 0 R 5 0 R] >>",
            4: b"<< /Type /Annot /Subtype /Widget /FT /Btn /Rect [0 0 1 1] >>",
            5: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 1 1]" + widget + b" >>",
            **(objects or {}),
        },
        1,
    )


@pytest.mark.parametrize(
    ("data", "text"),
    [
        (fields_of(b""), False),
        (fields_of(b" /FT /Tx"), True),
        (fields_of(b" /Parent 6 0 R", {6: b"<< /Parent 7 0 R >>", 7: b"<< /FT /Tx >>"}), True),
        (fields_of(b" /FT /Ch /Parent 6 0 R", {6: b"<< /FT /Tx >>"}), False),
        (
            fields_of(b" /Parent 6 0 R", {6: b"<< /Parent 7 0 R >>", 7: b"<< /Parent 6 0 R >>"}),
            False,
        ),
        (
            fields_of(
                b" /Parent 6 0 R", {6: b"<< /Parent 7 0 R >>", 7: b"<< /Parent 6 0 R /FT /Tx >>"}
            ),
            True,
        ),
    ],
    ids=[
        "a button",
        "a text field",
        "inherited",
        "its own first",
        "parents in a loop",
        "in the loop",
    ],
)
def test_43_a_text_field_as_pikepdf_finds_it(data, text):
    with m5a.pikepdf.open(m5a.io.BytesIO(data)) as pdf:
        assert m5a.has_text_field(pdf) is text


def test_44_an_encrypted_file_without_an_owner_password_fails(tmp_path, monkeypatch, capsys):
    """The file of test 6 encrypted with the empty password as owner password
    too: it is not owner-password-protected."""
    data = encrypt(form(), user="", owner="")
    entry = entry_of(data)
    assert entry["owner password"] is False
    assert entry["failed"] == ["an owner password that is not the empty one"]
    assert entry_of(ENCRYPTED)["owner password"] is True
    assert entry_of(PLAIN)["owner password"] is False
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    assert run(folder, tmp_path / "receipt.json") == 1
    printed = capsys.readouterr().out
    assert (
        "; 1 files encrypted, 0 of them with an owner password that is not the empty one\n"
        in printed
    )


def test_45_the_widget_moved_after_a_null():
    """A null, then a link, then a widget in /Annots: the fault moves the
    widget by one point."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: PAGE_OPENS + b" /Annots [null << /Subtype /Link /Rect [0 0 1 1] >> 4 0 R] >>",
            4: b"<< /Type /Annot /Subtype /Widget /Rect [10 20 30 40] >>",
        },
        1,
    )
    with m5a.pikepdf.open(m5a.io.BytesIO(m5a.edited(data, m5a._move_widget))) as pdf:
        assert [float(value) for value in pdf.get_object((4, 0)).Rect] == [11, 20, 31, 40]
