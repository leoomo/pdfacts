"""decrypt(): an encrypted file written anew without encryption.

open() reads an encrypted file as it is and decrypts what it reads
(docs/conventions.md, "Decryption"). decrypt() has QPDF write the file
without encryption and checks the result against the input through both
engines; the caller opens those bytes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pdfacts.backends import qpdf
from pdfacts.backends.protocols import (
    AnnotationView,
    DecryptedOtherwise,
    EncryptionEntries,
    EngineFailure,
    FailureKind,
    FieldEntry,
    Kids,
    PageText,
    WidgetEntry,
    Written,
)
from pdfacts.core import runtime
from pdfacts.core.bitmap import Colour
from pdfacts.core.document import Document, FeatureAccess, _deadline, classified, open
from pdfacts.core.errors import (
    DamagedFileError,
    DecryptionFailedError,
    EngineError,
    InputError,
    LimitExceededError,
    NotEncryptedError,
    PasswordRequiredError,
)
from pdfacts.core.geometry import PageGeometry
from pdfacts.core.limits import Limits
from pdfacts.core.values import Unknown

# The most pixels the check renders a page at, as Page.render counts them: at
# scale 1 where the page is at most this many, at a lower scale otherwise.
CHECK_PIXELS = 1 << 22
# The /CFM of a crypt filter that both engines decrypt alike, whatever /R says
# (measured).
METHODS = frozenset({b"V2", b"AESV2", b"AESV3"})
# Why QPDF and PDFium decrypt an object otherwise, by DecryptedOtherwise.kind.
OTHERWISE = {
    "crypt filter": "a stream whose /DecodeParms name a crypt filter, which QPDF takes and "
    "PDFium does not",
    "cross-reference": "a stream /Type /XRef that the document refers to, which QPDF leaves "
    "as written and PDFium decrypts",
    "signature": "a dictionary PDFium takes for a signature and QPDF does not, whose "
    "/Contents PDFium leaves as written and QPDF decrypts",
    "metadata": "the catalog's /Metadata under /EncryptMetadata false, not a stream /Type "
    "/Metadata with /Subtype /XML at /V 4 or more, which PDFium leaves as written and QPDF "
    "decrypts",
    "trailer": "a string in it, which QPDF leaves as written and PDFium decrypts",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Decryption:
    """An encrypted file written anew without encryption, and checked."""

    # The decrypted file.
    data: bytes = field(repr=False)
    # Pages in the decrypted file, as many as in the input.
    page_count: int


def decrypt(
    data: bytes,
    *,
    password: str | None = None,
    limits: Limits | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> Decryption:
    """The encrypted file `data`, written anew by QPDF without encryption and
    checked against the input.

    The input is opened as open() opens it, with its errors and its limits,
    a page of it damaged too: a password is needed where open() needs one,
    and either of the file's passwords decrypts it. Then, in this order:
    DecryptionFailedError when every value from QPDF is unknown for the
    input, whether or not it is encrypted; NotEncryptedError when both
    engines read the file as not encrypted, unless QPDF finds an encryption
    dictionary the catalog does not reach or warns that it mis-read an
    object, which is DecryptionFailedError; DecryptionFailedError when a
    crypt filter that /StmF, /StrF or /EFF names is neither absent, nor
    /Identity where /CF defines no filter of that name, nor defined in /CF
    with a /CFM of METHODS; and DecryptionFailedError when an object reached
    from the catalog or /Info is one the engines decrypt otherwise
    (OTHERWISE).

    DecryptionFailedError also when QPDF cannot write the file or reports
    anything while it reads or writes it, and when the result fails a check.
    The result has to open as open() opens a file, read as not encrypted by
    both engines, with /Size in its trailer; give for each page what the
    input gives: its geometry, what PDFium reads in each annotation
    dictionary of its /Annots, the characters PDFium reads in its content,
    and the pixels of PDFium's rendering in RGB (CHECK_PIXELS); and give the
    input's fields as QPDF reads them, the field tree and each page's
    widgets (_Fields). Neither the input nor the result may give a warning.

    The limits apply as for open(), to the input and to the result, with two
    exceptions: max_bytes applies to the input only, and deadline_seconds
    counts from the start of decrypt(), checked as open() checks it, once
    the file is written, after each page of the check and after the fields
    of each page. max_render_pixels applies to the renderings of the check;
    max_items_per_page holds the entries of each page's /Annots as each
    engine counts them, and its characters as glyphs() holds them.
    EngineError when an engine fails otherwise.

    The object numbers of the result may differ from those of the input. Its
    /ID keeps the first part of the input's and gets a second made from its
    content. A signature of the input no longer matches the bytes.

    `_clock` is for tests.
    """
    started = _clock()
    limits = classified(data, password, limits)
    deadline = _deadline(limits.deadline_seconds, started, _clock)
    with open(data, password=password, limits=limits) as source:
        _check_encrypted(source)
        # The runtime started in open().
        with runtime.engine():
            try:
                written = qpdf.rewrite_decrypted(data, password)
            except EngineFailure as failure:
                raise _decryption_error(failure) from failure
        deadline()
        if written.warnings:
            raise DecryptionFailedError(
                detail=f"QPDF reported while it decrypted the file: {'; '.join(written.warnings)}"
            )
        _verify(source, written.data, dataclasses.replace(limits, max_bytes=None), deadline)
        return Decryption(data=written.data, page_count=source.page_count)


def _check_encrypted(source: Document) -> None:
    """open() has compared the encryption the engines read ("Object identity")."""
    with source._access() as access:
        if access.qpdf_unknown is not None:
            raise DecryptionFailedError(
                detail=f"every value from QPDF is unknown: {access.qpdf_unknown.detail}"
            )
        if access.security_handler_revision() is not None:
            refused = _refused_filter(access.encryption_entries())
            if refused is not None:
                raise DecryptionFailedError(
                    detail="its crypt filters are not all ones both engines decrypt alike: "
                    f"{refused}"
                )
            otherwise = access.decrypted_otherwise()
            if otherwise:
                raise DecryptionFailedError(detail=_otherwise(otherwise[0]))
            return
        if access.lost_encryption():
            raise DecryptionFailedError(
                detail="neither engine reads the file as encrypted, and it holds an "
                "encryption dictionary that no trailer names: its strings and streams may be "
                "encrypted with nothing to decrypt them"
            )
        if access.misread():
            raise DecryptionFailedError(
                detail="neither engine reads the file as encrypted, and QPDF warns that it "
                "mis-read an object, which may be the encryption dictionary"
            )
        raise NotEncryptedError()


def _otherwise(found: DecryptedOtherwise) -> str:
    if found.objgen is None:
        where = "an object the trailer holds"
    else:
        number, generation = found.objgen
        where = f"object {number} {generation}"
    return f"the engines decrypt {where} otherwise: {OTHERWISE[found.kind]}"


def _refused_filter(entries: EncryptionEntries) -> str | None:
    """What makes a crypt filter one the engines may decrypt otherwise; None
    where each that /StmF, /StrF and /EFF name is one they decrypt alike."""
    if isinstance(entries.crypt_filters, str):
        return entries.crypt_filters
    methods = {crypt.name: crypt.method for crypt in entries.crypt_filters}
    named = (
        ("/StmF", entries.stream_filter),
        ("/StrF", entries.string_filter),
        ("/EFF", entries.file_filter),
    )
    for key, name in named:
        if name is None:
            continue
        if isinstance(name, str):
            return name
        if name == b"Identity":
            if name in methods:
                return f"{key} names /Identity, and /CF defines a crypt filter of that name"
            continue
        if name not in methods:
            return f"{key} names a crypt filter /CF does not define"
        method = methods[name]
        if isinstance(method, str):
            return method
        if method not in METHODS:
            if method is None:
                return f"the crypt filter {key} names has no /CFM"
            return f"the crypt filter {key} names has a /CFM other than /V2, /AESV2 and /AESV3"
    return None


def _verify(source: Document, data: bytes, limits: Limits, deadline: Callable[[], None]) -> None:
    try:
        result = open(data, limits=limits)
    except (InputError, PasswordRequiredError) as error:
        # An EngineError passes: it says nothing about the result.
        raise DecryptionFailedError(detail=f"the decrypted file does not open: {error}") from error
    with result:
        with result._access() as access:
            revision = access.security_handler_revision()
        if revision is not None:
            raise DecryptionFailedError(
                detail=f"PDFium reads the decrypted file as encrypted, revision {revision}"
            )
        if result.page_count != source.page_count:
            raise DecryptionFailedError(
                detail=f"the decrypted file has {result.page_count} pages, "
                f"the file {source.page_count}"
            )
        for index in range(source.page_count):
            _compare_page(source, result, index)
            deadline()
        _compare_fields(source, result, deadline)
        # Last: reading a page or the fields can add one.
        for document, which in ((source, "the file"), (result, "the decrypted file")):
            if document.warnings:
                raise DecryptionFailedError(
                    detail=f"{which} gives warnings: {'; '.join(document.warnings)}"
                )
        object_count = result.object_count
        if isinstance(object_count, Unknown):
            raise DecryptionFailedError(
                detail=f"the decrypted file has no usable /Size: {object_count.detail}"
            )


def _compare_page(source: Document, result: Document, index: int) -> None:
    before = source.pages[index]
    try:
        after = result.pages[index]
    except DamagedFileError as error:
        raise DecryptionFailedError(
            detail=f"page {index} of the decrypted file is damaged: {error}"
        ) from error
    if after.geometry != before.geometry:
        raise DecryptionFailedError(
            detail=f"page {index} of the decrypted file has the geometry {after.geometry}, "
            f"the file {before.geometry}"
        )
    with source._access() as access:
        views = _annotation_views(access, index)
        text = _page_text(access, index)
    with result._access() as access:
        if _annotation_views(access, index) != views:
            raise DecryptionFailedError(
                detail=f"PDFium reads the annotations of page {index} otherwise in the "
                "decrypted file"
            )
        if _page_text(access, index) != text:
            raise DecryptionFailedError(
                detail=f"PDFium reads the text of page {index} otherwise in the decrypted file"
            )
    # At most CHECK_PIXELS pixels, each side far below what PDFium addresses.
    scale = _scale(before.geometry)
    if after.render(scale, colour=Colour.RGB) != before.render(scale, colour=Colour.RGB):
        raise DecryptionFailedError(
            detail=f"PDFium renders page {index} otherwise in the decrypted file"
        )


def _compare_fields(source: Document, result: Document, deadline: Callable[[], None]) -> None:
    """The result has to give the input's fields (_Fields): first the field
    tree, then page by page what widgets() reads; the deadline is checked
    after each page.

    QPDF's values are known for both: decrypt() refuses an input for which
    they are unknown, and a result for which they are fails the comparison of
    its first page, whose geometry holds the user unit QPDF reads.
    """
    ours, theirs = _Fields(source), _Fields(result)
    if theirs.tree() != ours.tree():
        raise DecryptionFailedError(
            detail="QPDF reads the field tree of /AcroForm otherwise in the decrypted file"
        )
    for index in range(source.page_count):
        if theirs.page(index) != ours.page(index):
            raise DecryptionFailedError(
                detail=f"QPDF reads the widgets of page {index} otherwise in the decrypted file"
            )
        deadline()


class _Labels:
    """Each object a label of its own, in the order it is first met: the
    object numbers QPDF writes anew are left out, and what refers to what is
    kept. A dictionary written directly has none."""

    def __init__(self) -> None:
        self._labels: dict[tuple[int, int], int] = {}

    def __call__(self, object_number: int | None, generation: int | None) -> int | None:
        if object_number is None or generation is None:
            return None
        return self._labels.setdefault((object_number, generation), len(self._labels))


class _Fields:
    """The fields of one document as QPDF reads them for fields() and
    widgets(), the tree first, then page by page, each object by its label
    (_Labels) and each value by its digest (Written.digest).

    What the backend gives once for many dictionaries is taken once: a value
    by its digest, made once for that object; a field dictionary that is an
    object of its own once for its object; a /Kids array, which the backend
    keeps for the document, once for that array. Compared by content, each
    would cost each time it is named. What the backend converts anew for each
    page that names it, a dictionary written directly in /Annots, is taken
    anew too, and not kept.
    """

    def __init__(self, document: Document) -> None:
        self._document = document
        self._labels = _Labels()
        self._nodes: dict[tuple[int, int], tuple] = {}
        # By id(): the /Kids arrays and the tree's lists of nodes, which the
        # backend keeps for the document, each kept here with its digest.
        self._kept: dict[int, tuple[object, bytes]] = {}

    def _kept_digest(self, value: object, make: Callable[[], object]) -> bytes:
        known = self._kept.get(id(value))
        if known is None:
            known = (value, _digest(repr(make()).encode()))
            self._kept[id(value)] = known
        return known[1]

    def node(self, entry: FieldEntry) -> tuple:
        """A field dictionary as QPDF reads it: its label, its entries, whether
        it has /Kids and whether it is a widget; once for an object of its
        own."""
        if entry.object_number is None or entry.generation is None:
            return self._node(entry)
        key = (entry.object_number, entry.generation)
        if key not in self._nodes:
            self._nodes[key] = self._node(entry)
        return self._nodes[key]

    def _node(self, entry: FieldEntry) -> tuple:
        return (
            self._labels(entry.object_number, entry.generation),
            _entries(entry.entries),
            entry.kids,
            entry.subtype_widget,
        )

    def tree(self) -> tuple | None:
        """The field tree from /AcroForm /Fields as FieldTree gives it, each
        node by node(), with the nodes its /Kids name, its parent, and whether
        its /Parent names that and it is reached twice; the nodes /Fields
        names, and whether the tree is valid and was cut. None without
        /AcroForm."""
        with self._document._access() as access:
            found = access.field_tree()
        if found is None:
            return None
        nodes = tuple(
            (
                self.node(node.entry),
                None
                if node.kids is None
                else self._kept_digest(node.kids, lambda kids=node.kids: kids),
                node.parent,
                node.parent_matches,
                node.reached_twice,
            )
            for node in found.nodes
        )
        return found.roots, found.invalid, found.cut, nodes

    def page(self, index: int) -> object:
        """Whether the page's /Annots is no array, and each widget QPDF reads
        on the page (widget()); or, as widgets() gives it, the unknown object
        number of a page QPDF does not read as PDFium does ("Object
        identity"). The entries of /Annots are held to max_items_per_page as
        QPDF counts them, before the widgets are read."""
        number = self._document.pages[index].ref.object_number
        if isinstance(number, Unknown):
            return number
        with self._document._access() as access:
            count = access.annotation_count(index)
            allowed = access.max_items_per_page
            if allowed is not None and count is not None and count > allowed:
                raise LimitExceededError(limit="max_items_per_page", allowed=allowed, found=count)
            annotations = access.annotations(index)
        return annotations.not_an_array, tuple(self.widget(one) for one in annotations.widgets)

    def widget(self, widget: WidgetEntry) -> tuple:
        """A widget as widgets() reads it: its index in /Annots and its object,
        its own entries, the keys of its /AP /N and whether /AP, /MK or /BS is
        written otherwise than allowed; the field dictionaries of its /Parent
        chain (node()), whether the chain loops, is cut or meets a /Parent
        that is no dictionary, and the /Kids of its own /Parent (_kids)."""
        annotation = widget.annotation
        kids = widget.parent_kids
        return (
            annotation.index,
            self._labels(annotation.object_number, annotation.generation),
            _entries(widget.entries),
            None if widget.states is None else widget.states.digest(),
            widget.states_invalid,
            widget.mk_invalid,
            widget.bs_invalid,
            tuple(self.node(entry) for entry in widget.chain),
            widget.chain_loops,
            widget.chain_cut,
            widget.parent_invalid,
            None if kids is None else self._kept_digest(kids, lambda: _kids(kids, self._labels)),
        )


def _entries(entries: Mapping[bytes, Written]) -> tuple:
    """A dictionary's entries as the backend gives them, each value by its
    digest."""
    return tuple((key, value.digest()) for key, value in sorted(entries.items()))


def _kids(kids: Kids, labels: _Labels) -> tuple:
    """A /Kids array as QPDF reads it for widgets(): each entry by its label
    (_Labels) and whether it is a widget, the positions of each widget among
    the widget entries, and whether every entry is a widget."""
    return (
        tuple(
            (labels(entry.object_number, entry.generation), entry.widget) for entry in kids.entries
        ),
        tuple(
            sorted((labels(*key), positions) for key, positions in kids.widget_positions.items())
        ),
        kids.all_widgets,
    )


def _digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _page_text(access: FeatureAccess, index: int) -> PageText:
    """PDFium's characters of the page, held to max_items_per_page as glyphs() holds them."""
    cap = access.max_items_per_page
    text = access.page_text(index, cap)
    if text.stopped:
        if cap is None:  # raised, not asserted: python -O removes an assert
            raise AssertionError("the backend stops only at a cap")
        raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
    return text


def _annotation_views(access: FeatureAccess, index: int) -> tuple[AnnotationView | None, ...]:
    """What PDFium reads in the page's /Annots, its entries held to max_items_per_page."""
    count = access.pdfium_annotation_count(index)
    allowed = access.max_items_per_page
    if allowed is not None and count > allowed:
        raise LimitExceededError(limit="max_items_per_page", allowed=allowed, found=count)
    return access.annotation_views(index)


def _scale(geometry: PageGeometry) -> float:
    """1, or the scale s at which (width s + 1)(height s + 1) is CHECK_PIXELS:
    each side rounds up by less than a pixel, so the page is at most that
    many pixels, however long and thin it is. The root is written so that
    nothing is subtracted: for a page of 10^-14 units the usual form loses
    every digit to cancellation and gives 0 (review of #99)."""
    width, height = geometry.width, geometry.height
    if math.ceil(width) * math.ceil(height) <= CHECK_PIXELS:
        return 1.0
    area, sides = width * height, width + height
    root = math.sqrt(sides * sides + 4 * area * (CHECK_PIXELS - 1))
    return 2 * (CHECK_PIXELS - 1) / (sides + root)


def _decryption_error(failure: EngineFailure) -> Exception:
    # open() has had QPDF open the file with the same password.
    if failure.kind is FailureKind.FORMAT:
        return DecryptionFailedError(detail=f"QPDF cannot write the file: {failure.detail}")
    return EngineError(engine="qpdf", detail=failure.detail)
