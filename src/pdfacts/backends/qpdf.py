"""QPDF backend: object access, rewrite and decryption.

The only module allowed to import pikepdf.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType

import pikepdf

from pdfacts.backends.protocols import (
    AnnotationEntry,
    Box,
    EngineFailure,
    FailureKind,
    FieldEntry,
    FieldNode,
    FieldTree,
    KidEntry,
    Kids,
    PageAnnotations,
    PageObject,
    RecoveredBytes,
    WidgetEntry,
    Written,
)

# How far from the end QPDF looks for startxref (measured: it finds one that
# starts 1054 bytes before the end, not one that starts 1055 bytes before).
_STARTXREF_SEARCH = 1054
# White space as QPDF reads it around startxref, its offset and the sections
# and objects it looks for: PDF's six characters and the vertical tab
# (measured, review, round 8).
_SPACE = rb"[\x00\t\n\x0b\x0c\r ]"
# The header: %PDF- and a version, as QPDF takes it. Offsets in the file count
# from it, and it may follow other bytes (measured: QPDF and PDFium read such a
# file as written). It is looked for as far as classified() accepts it, one
# byte further than QPDF (review, round 8): where QPDF misses it, QPDF cannot
# read the last revision, and the file is refused or rebuilt.
_HEADER = re.compile(rb"%PDF-[0-9]+\.[0-9]+")
# startxref and the white space and comments before its offset, matched once
# and never inside the run of another, so that the file is read once however
# its startxrefs are laid out; possessive, so that a long run costs no memory.
_STARTXREF = re.compile(rb"startxref(?:" + _SPACE + rb"|%[^\r\n]*)++")
# White space and comments between the numbers and the keyword of an object
# header, and a sign on a number, which QPDF reads there (measured, reviews,
# rounds 9 and 11). An object header is looked for from every number, so what
# one gap may hold is bounded, and a crafted run of numbers and comments is
# not read to its end from each: at most eight runs of white space and
# comments, a comment ending at its end of line within 64 bytes. A header
# with more, or a longer comment, is not seen; no writer is known to write
# one.
_HEADER_GAP = rb"(?:" + _SPACE + rb"++|%[^\r\n]{0,64}[\r\n]){1,8}+"
_NUMBER = rb"[+-]?[0-9]+"
# The offset after startxref, in the forms QPDF reads through (measured): white
# space or comments before it, as above, a plus sign on it, a delimiter or the
# end of the file after it. It passes over a startxref without an offset, one
# glued to its offset and one whose offset other characters follow. An offset
# of more digits, leading zeros aside, names nothing in any file.
_OFFSET = re.compile(rb"\+?([0-9]+)(?![^\x00\t\n\x0b\x0c\r ()<>\[\]{}/%])")
_OFFSET_DIGITS = 20
# What the offset after startxref can name: a cross-reference table, or an
# object, which may be a cross-reference stream; QPDF, recovery off, tells
# whether it reads it. White space before it is allowed, more than QPDF reads
# (measured: one blank, not ten), and only the first bytes are looked at, so
# that a crafted file whose offsets all name a long run of white space is not
# read over and over.
_SECTION = re.compile(
    _SPACE + rb"*(?:xref|" + _NUMBER + _HEADER_GAP + _NUMBER + _HEADER_GAP + rb"obj)"
)
_REACH = 64
# Where the data of a stream begins, after its dictionary, and where it ends;
# endstream may follow the data directly (6 corpus files write it after a
# letter). The text of a startxref between the two ends no revision.
_STREAM_BOUNDARY = re.compile(rb">>" + _SPACE + rb"*stream(?=[\r\n])|endstream")
# "N G obj", where an object begins, in the forms QPDF reads.
_OBJECT_HEADER = re.compile(
    rb"(?<![0-9+\-])" + _NUMBER + _HEADER_GAP + _NUMBER + _HEADER_GAP + rb"obj(?![A-Za-z])"
)
# What a revision is written of: objects, a cross-reference table whose first
# numbers follow xref, a trailer whose dictionary follows it, either cut short
# at the end. Words alone, such as obj, stream or trailer in a web page
# appended to a file, are none (reviews, rounds 10 and 11).
_REVISION_PART = re.compile(
    _OBJECT_HEADER.pattern
    + rb"|(?<![A-Za-z])xref(?="
    + _SPACE
    + rb"*+(?:[0-9]|\Z))|(?<![A-Za-z])trailer(?="
    + _SPACE
    + rb"*+(?:<<|\Z))"
)


@dataclass(frozen=True)
class QpdfVersions:
    binding: str
    engine: str


def versions() -> QpdfVersions:
    return QpdfVersions(
        binding=str(pikepdf.__version__),
        engine=str(pikepdf.__libqpdf_version__),
    )


@contextmanager
def _translated() -> Iterator[None]:
    """Every exception from the engine as EngineFailure.

    MemoryError passes unchanged, and so does everything that is not an
    Exception, such as KeyboardInterrupt. Inside, numbers come back as int and
    Decimal whatever conversion mode the caller chose for pikepdf; a caller's
    "explicit" mode would otherwise turn every number into a pikepdf object.
    """
    try:
        with pikepdf.implicit_conversion():
            yield
    except MemoryError:
        raise
    except EngineFailure:
        raise
    # Not a PdfError in pikepdf 10; checked first in case it becomes one.
    except pikepdf.PasswordError as error:
        raise EngineFailure(FailureKind.PASSWORD, str(error)) from error
    except pikepdf.PdfError as error:
        raise EngineFailure(FailureKind.FORMAT, str(error)) from error
    except Exception as error:
        raise EngineFailure(FailureKind.OTHER, str(error)) from error


def _opened(
    data: bytes, password: str | None, sources: list[_Input], **options: bool
) -> pikepdf.Pdf:
    """pikepdf.open with the password given only where the file needs one.

    pikepdf warns when a password is given for a file that is not encrypted,
    and quieting it with warnings.catch_warnings would change the warning
    state of the whole process. So the file is opened without a password
    first, and again with it when that fails or the file is encrypted, so
    that a wrong password is refused. The input the returned document reads
    from is added to `sources`, for the caller to close.
    """
    source = _Input(data)
    try:
        pdf = pikepdf.open(source, password="", **options)
    except pikepdf.PasswordError:
        source.close()
        if not password:
            raise
    except BaseException:
        source.close()
        raise
    else:
        if not password or not pdf.is_encrypted:
            sources.append(source)
            return pdf
        pdf.close()
        source.close()
    source = _Input(data)
    sources.append(source)
    return pikepdf.open(source, password=password, **options)


def _check_index(index: int, count: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError(f"a page index is an int, not {type(index).__name__}")
    if not 0 <= index < count:
        raise IndexError(f"page index {index} is outside the {count} pages")


class _Input(io.BytesIO):
    """The bytes of a document.

    QPDF names its input in every warning, and by default that name holds a
    memory address, so the warnings of one file would differ between runs.
    """

    def __repr__(self) -> str:
        return "<input>"


class QpdfDocument:
    """A document opened by QPDF with recovery switched off: an ObjectReader.

    It holds the native handles; no method returns a pikepdf object. Values are
    reported as the file writes them: nothing is normalized, and whether a
    value is unknown is for the core to decide.

    Recovery being off does not keep QPDF from changing the document in
    memory. When it walks the page tree it gives a page without a usable media
    box US letter, turns a page written directly into /Kids into a new object,
    copies a page that is listed twice, drops a kid that is not a dictionary
    and flattens what is left, and may walk from another root; it says so in a
    warning at most. So the document is opened twice. The page list comes
    from the view QPDF walked, the values from a view nothing walks. When the
    pages QPDF found are not, index by index, the objects the file's page tree
    lists, no page has values: its object at an index may not be the file's.
    """

    def __init__(
        self,
        walked: pikepdf.Pdf,
        unwalked: pikepdf.Pdf,
        sources: list[_Input],
        pages: tuple[tuple[int, int], ...],
        pages_as_written: bool,
        objects_as_written: bool,
    ) -> None:
        # Use open(); this takes documents that are already open.
        self._walked = walked
        self._unwalked = unwalked
        self._sources = sources
        # The page objects as QPDF found them, read once: pikepdf takes time
        # in proportion to the page count for each access to its page list.
        self._pages = pages
        self._pages_as_written = pages_as_written
        self._objects_as_written = objects_as_written
        # Plain data kept so that shared objects convert once (issue #36).
        self._kept = _Kept()
        self._warnings: list[str] = []
        # The same warnings as a set: a damaged file can give tens of thousands.
        self._known_warnings: set[str] = set()
        self._closed = False

    @classmethod
    def open(cls, data: bytes, password: str | None) -> QpdfDocument:
        """Open `data`. A damaged file is a FORMAT failure: recovery is off."""
        views: list[pikepdf.Pdf] = []
        sources: list[_Input] = []
        try:
            with _translated():
                for _ in range(2):
                    views.append(
                        _opened(
                            data,
                            password,
                            sources,
                            attempt_recovery=False,
                            inherit_page_attributes=False,
                        )
                    )
                walked, unwalked = views
                # Walked now, so that what QPDF changed is among the warnings
                # from the start.
                pages = tuple(page.obj.objgen for page in walked.pages)
                as_written = _page_objects_as_written(unwalked)
                objects_as_written = _objects_as_written(unwalked)
        except BaseException:
            for view in views:
                # The first exception is the one to report. MemoryError is
                # never swallowed.
                with suppress(EngineFailure), _translated():
                    view.close()
            for source in sources:
                source.close()
            raise
        return cls(walked, unwalked, sources, pages, pages == as_written, objects_as_written)

    def page_count(self) -> int:
        """The number of pages QPDF found."""
        self._check_open()
        return len(self._pages)

    def object_count(self) -> int | None:
        """The /Size of the trailer; None unless it is an integer greater than 0.

        Objects are not counted instead: that is a different number.
        """
        self._check_open()
        with _translated():
            size = self._unwalked.trailer.get("/Size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return None
        return int(size)

    def objects_as_written(self) -> bool:
        """Whether each compressed object is named once in its object stream, at its index."""
        self._check_open()
        return self._objects_as_written

    def annotation_count(self, index: int) -> int | None:
        """The number of entries of the page's /Annots; None when it is no array."""
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            annots = self._unwalked.get_object(objgen).get("/Annots")
        if annots is None:
            return 0
        if isinstance(annots, pikepdf.Array):
            return len(annots)
        return None

    def annotations(self, index: int) -> PageAnnotations:
        """The entries of the page's /Annots and its widgets, as the file writes them."""
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            annots = self._unwalked.get_object(objgen).get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                return PageAnnotations((), annots is not None, ())
            entries: list[AnnotationEntry] = []
            widgets: list[WidgetEntry] = []
            for position, item in enumerate(annots):
                dictionary = isinstance(item, pikepdf.Dictionary)
                item_objgen = item.objgen if dictionary and item.is_indirect else None
                subtype_value = item.get("/Subtype") if dictionary else None
                subtype = (
                    _name_bytes(subtype_value) if isinstance(subtype_value, pikepdf.Name) else None
                )
                entry = AnnotationEntry(
                    position,
                    item_objgen[0] if item_objgen else None,
                    item_objgen[1] if item_objgen else None,
                    dictionary,
                    subtype,
                )
                entries.append(entry)
                if subtype != b"Widget":
                    continue
                kept = self._kept.widgets.get(item_objgen) if item_objgen else None
                if kept is not None:
                    # Listed again, on this page or another: converted once per
                    # document, with its own annotation.
                    widgets.append(replace(kept, annotation=entry))
                    continue
                one = _widget_entry(item, entry, self._kept)
                if item_objgen is not None:
                    self._kept.widgets[item_objgen] = one
                widgets.append(one)
            return PageAnnotations(tuple(entries), False, tuple(widgets))

    def field_tree(self) -> FieldTree | None:
        """The document's field tree from /AcroForm /Fields; None without /AcroForm."""
        self._check_open()
        if self._kept.tree_walked:
            return self._kept.tree
        with _translated():
            tree = _field_tree(self._unwalked, self._kept)
        self._kept.tree = tree
        self._kept.tree_walked = True
        return tree

    def _annotated_page(self, index: int) -> tuple[int, int]:
        """The objgen of page `index`, after the checks page_object() makes."""
        count = self.page_count()
        _check_index(index, count)
        if not self._pages_as_written:
            raise EngineFailure(
                FailureKind.FORMAT,
                "the pages QPDF found are not the objects the file's page tree lists, so "
                "the object QPDF has at a page index may not be the file's",
            )
        return self._pages[index]

    def page_object(self, index: int) -> PageObject:
        """The page's object and what the file writes on it and its ancestors.

        A FORMAT failure when the pages QPDF found are not the objects the
        file's page tree lists: a wrong object number is worse than none
        (docs/conventions.md, "Object identity").
        """
        count = self.page_count()
        _check_index(index, count)
        if not self._pages_as_written:
            raise EngineFailure(
                FailureKind.FORMAT,
                "the pages QPDF found are not the objects the file's page tree lists, so "
                "the object QPDF has at a page index may not be the file's",
            )
        objgen = self._pages[index]
        with _translated():
            page = self._unwalked.get_object(objgen)
            user_unit, user_unit_invalid = _user_unit(page.get("/UserUnit"))
            return PageObject(
                object_number=int(objgen[0]),
                generation=int(objgen[1]),
                media_box=_box(_inherited(page, "/MediaBox")),
                crop_box=_box(_inherited(page, "/CropBox")),
                rotate=_integer(_inherited(page, "/Rotate")),
                user_unit=user_unit,
                user_unit_invalid=user_unit_invalid,
            )

    def warnings(self) -> tuple[str, ...]:
        """The warnings QPDF gave so far, for example about a media box it added.

        QPDF hands out each warning once and then forgets it, so they are
        collected here. A warning both views give is kept once. Some messages
        QPDF sends to pikepdf's logger instead, for example about a kid it
        drops; they are not among these.
        """
        self._check_open()
        with _translated():
            walked = [str(message) for message in self._walked.get_warnings()]
            unwalked = [str(message) for message in self._unwalked.get_warnings()]
        self._warnings.extend(walked)
        self._known_warnings.update(walked)
        for message in unwalked:
            if message not in self._known_warnings:
                self._warnings.append(message)
                self._known_warnings.add(message)
        return tuple(self._warnings)

    def close(self) -> None:
        """Close the document. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            with _translated():
                self._walked.close()
        finally:
            try:
                with _translated():
                    self._unwalked.close()
            finally:
                for source in self._sources:
                    source.close()

    def _check_open(self) -> None:
        # The backend's own flag: what the engine does with a closed handle
        # is not something to rely on.
        if self._closed:
            raise EngineFailure(FailureKind.OTHER, "closed")


def revision_part_follows(data: bytes) -> bool:
    """Whether part of a revision follows the end of the last whole revision.

    The end is where QPDF reads the file to (_revision_ends); part of a
    revision is what _REVISION_PART finds after it. False when the file has
    no whole revision.
    """
    return _part_follows(data, _revision_ends(data))


def _part_follows(data: bytes, ends: list[int]) -> bool:
    return bool(ends) and _REVISION_PART.search(data, ends[-1]) is not None


def rewrite_recovered(data: bytes, password: str | None) -> RecoveredBytes:
    """The document QPDF rebuilds from `data`, with recovery on, written anew.

    A FORMAT failure when QPDF cannot rebuild it. /Size is set in the trailer
    before saving, so that QPDF writes the right one: when it could not read
    the damaged file's trailer, it would write one without /Size, and the
    result would be damaged again. Encryption is kept; pikepdf would drop it.
    Object streams are kept as they are and none are generated (issue #13,
    decision 3; without /Size set, QPDF could not open what it generated even
    with recovery on); nothing is linearized. An unencrypted result gets an
    /ID made from its content, not from the time, so that the same input
    gives the same bytes.

    A FORMAT failure also when the rebuilt document is not encrypted and yet
    holds an encryption dictionary: QPDF did not find the trailer that names
    it, and read the encrypted strings and streams as they are.

    QPDF reads the file up to the end of its last whole revision
    (_revision_ends), and what follows is left out. Where part of a revision
    follows, an object, a cross-reference table or a trailer, the last
    revision is unfinished; where QPDF, recovery off, cannot read the last
    revision after another, its startxref leads nowhere. Either is a FORMAT
    failure, since QPDF would read the revision before without a word, or,
    rebuilding the file from its objects, mix the two; it places objects in
    object streams only through a cross-reference stream, which an
    unfinished update lacks. A file without a whole revision is rebuilt from
    its objects.

    The warnings are those QPDF gave while it rebuilt the file, walked its
    pages and wrote it: it reads streams only when it writes them.
    """
    ends = _revision_ends(data)
    last = ends[-1] if ends else None
    if _part_follows(data, ends):
        raise _unfinished()
    if last is not None and _reads_as_written(data[:last], password):
        data = data[:last]
    elif len(ends) > 1:
        raise _unfinished()
    else:
        # No startxref leads QPDF to a whole revision: it has to rebuild the
        # file from its objects, the last of each number counting. A comment
        # longer than its search for startxref keeps it from reading one
        # first, which fails outside its recovery where the offset is too
        # large for it (measured), and from dropping an object that only white
        # space follows to the end ("EOF after endobj"). Comments mean nothing,
        # and QPDF does not write them.
        data += b"%" + b" " * (2 * _STARTXREF_SEARCH)
    sources: list[_Input] = []
    try:
        with _translated():
            pdf = _opened(
                data, password, sources, attempt_recovery=True, inherit_page_attributes=False
            )
            try:
                page_count = len(pdf.pages)
                if _lost_encryption(pdf):
                    raise EngineFailure(
                        FailureKind.FORMAT,
                        "the file is encrypted, and QPDF did not find the trailer that names "
                        "its encryption dictionary",
                    )
                # Any value: QPDF writes the number of objects it writes.
                pdf.trailer["/Size"] = 1
                written = io.BytesIO()
                encrypted = pdf.is_encrypted
                # pikepdf's save() adds a warning about widgets the form does
                # not reach, a form cut off before its /AcroForm; _save is what
                # it calls to write. pikepdf would also write the XMP metadata
                # anew to set the PDF version in it, an empty packet in place
                # of metadata it cannot parse.
                pdf._save(
                    written,
                    encryption=encrypted,
                    deterministic_id=not encrypted,
                    fix_metadata_version=False,
                )
                # Everything QPDF reported since the file was opened.
                reported = tuple(str(message) for message in pdf.get_warnings())
            finally:
                pdf.close()
    finally:
        for source in sources:
            source.close()
    return RecoveredBytes(data=written.getvalue(), page_count=page_count, warnings=reported)


def _unfinished() -> EngineFailure:
    return EngineFailure(
        FailureKind.FORMAT,
        "its last revision is unfinished, or its startxref leads nowhere: QPDF would read "
        "the revision before without a word or, rebuilding the file from its objects, mix "
        "the two",
    )


def _revision_ends(data: bytes) -> list[int]:
    """Where the offsets after the startxrefs of whole revisions end, in order.

    A startxref ends a revision where QPDF reads through it, it lies outside
    the data of a stream (between the dictionary's stream and an endstream
    that follows), and its offset, counted from the header, names a
    cross-reference table or an object after the startxref of the revision
    before: a later revision's section lies nowhere else, and an offset cut
    short, or cut short and followed by other bytes, can name an earlier one.
    The first-page trailer of a linearized file ends no revision
    (_first_page_trailer).
    """
    heading = _HEADER.search(data, 0, 1024 + len(b"%PDF-9.9"))
    header = heading.start() if heading else 0
    first_page = _first_page_trailer(data, header)
    # The stream boundaries around each startxref, read once in file order.
    boundaries = _STREAM_BOUNDARY.finditer(data)
    before = None
    after = next(boundaries, None)
    ends: list[int] = []
    previous = None
    for found in _STARTXREF.finditer(data):
        while after is not None and after.start() < found.start():
            before, after = after, next(boundaries, None)
        if found.start() == first_page or (
            before is not None
            and before.group().startswith(b">>")
            and after is not None
            and after.group() == b"endstream"
        ):
            continue
        match = _OFFSET.match(data, found.end())
        if match is None:
            continue
        digits = match.group(1).lstrip(b"0") or b"0"
        if len(digits) > _OFFSET_DIGITS:
            continue
        section = header + int(digits)
        if section >= len(data) or not _SECTION.match(data, section, section + _REACH):
            continue
        if previous is not None and section <= previous:
            continue
        ends.append(match.end())
        previous = found.start()
    return ends


def _first_page_trailer(data: bytes, header: int) -> int | None:
    """Where the startxref of a linearized file's first-page trailer begins.

    None where the file is not linearized or has no such trailer. The
    linearization dictionary is the first object, within the first 1024
    bytes (ISO 32000-1, F.2), and the first-page cross-reference section
    follows it (F.3): its startxref is the first, with at most the object of
    a cross-reference stream between. The last startxref of the revision
    names the first-page section, which lies before this one; some files
    have no first-page trailer.
    """
    first = _OBJECT_HEADER.search(data, header, header + 1024)
    if first is None:
        return None
    closing = data.find(b"endobj", first.end(), header + 1024)
    if closing < 0 or b"/Linearized" not in data[first.end() : closing]:
        return None
    found = _STARTXREF.search(data, closing)
    if found is None:
        return None
    between = _OBJECT_HEADER.finditer(data, closing, found.start())
    if next(between, None) is not None and next(between, None) is not None:
        return None
    return found.start()


def _reads_as_written(data: bytes, password: str | None) -> bool:
    """Whether QPDF, recovery off, reads the cross-reference sections of `data`.

    QPDF reads them when it opens a file: from the last startxref through
    each /Prev.
    """
    sources: list[_Input] = []
    try:
        with _translated():
            _opened(
                data, password, sources, attempt_recovery=False, inherit_page_attributes=False
            ).close()
    except EngineFailure as failure:
        if failure.kind is not FailureKind.FORMAT:
            raise
        return False
    finally:
        for source in sources:
            source.close()
    return True


def _lost_encryption(pdf: pikepdf.Pdf) -> bool:
    """Whether a document QPDF reads as unencrypted holds an encryption dictionary.

    One that the catalog does not reach: only a trailer names an encryption
    dictionary, and one the document uses, such as the seed values of a
    signature field, is none.
    """
    if pdf.is_encrypted:
        return False
    found = {obj.objgen for obj in pdf.objects if _encryption_dictionary(obj)}
    return bool(found - _reachable(pdf.Root))


def _reachable(root: pikepdf.Object) -> set[tuple[int, int]]:
    """The indirect objects reached from `root` through dictionaries and arrays."""
    seen: set[tuple[int, int]] = set()
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, pikepdf.Dictionary | pikepdf.Array | pikepdf.Stream):
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        stack.extend(node if isinstance(node, pikepdf.Array) else node.values())
    return seen


def _encryption_dictionary(obj: object) -> bool:
    """A security handler's name in /Filter, and what only encryption holds.

    /O and /U of the standard handler, /Recipients of the public-key one,
    crypt filters (/CF, /StmF, /StrF) from version 4 on, or else /V for the
    algorithm. A signature dictionary also names a handler in /Filter and may
    have /V; it has /ByteRange and /Contents, which encryption never has.
    """
    if not isinstance(obj, pikepdf.Dictionary) or not isinstance(obj.get("/Filter"), pikepdf.Name):
        return False
    if "/ByteRange" in obj or "/Contents" in obj:
        return False
    return ("/O" in obj and "/U" in obj) or any(
        key in obj for key in ("/Recipients", "/CF", "/StmF", "/StrF", "/V")
    )


def _page_objects_as_written(pdf: pikepdf.Pdf) -> tuple[tuple[int, int] | None, ...] | None:
    """The page objects in the order the file's page tree lists them.

    Read without QPDF's walk. A node with a /Kids array is a node of the tree,
    any other dictionary a page. A kid that is not a dictionary is None; a
    page written directly into /Kids has the objgen (0, 0), which QPDF never
    lists, since it makes such a page an object of its own. None for the whole
    tree when an object is reached twice, by a loop or by a page listed twice:
    QPDF refuses a loop in the tree it walks, but it may walk another tree
    than the one the catalog names.
    """
    pages: list[tuple[int, int] | None] = []
    seen: set[tuple[int, int]] = set()
    stack: list[object] = [pdf.Root.get("/Pages")]
    while stack:
        node = stack.pop()
        if not isinstance(node, pikepdf.Dictionary):
            pages.append(None)
            continue
        if node.is_indirect:
            if node.objgen in seen:
                return None
            seen.add(node.objgen)
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend(reversed(list(kids)))
        else:
            pages.append(node.objgen)
    return tuple(pages)


def _objects_as_written(pdf: pikepdf.Pdf) -> bool:
    """Whether each compressed object is named once in its object stream, at its index.

    The cross-reference gives a compressed object as an object stream and an
    index. QPDF finds the object by the number in the stream's header and
    keeps the last of two with one number; PDFium takes what sits at the
    index. Both read the same only when the header names the object at that
    index and names no number twice.
    """
    headers: dict[int, list[int]] = {}
    for (number, _), entry in pdf.get_xref_table().items():
        if entry.type != 2:
            continue
        header = headers.get(entry.obj_stream_number)
        if header is None:
            header = _object_stream_header(pdf, entry.obj_stream_number)
            if header is None or 0 in header or len(set(header)) != len(header):
                return False
            headers[entry.obj_stream_number] = header
        index = entry.obj_stream_index
        if not 0 <= index < len(header) or header[index] != number:
            return False
    return True


def _object_stream_header(pdf: pikepdf.Pdf, number: int) -> list[int] | None:
    """The object numbers the header of object stream `number` names, in order.

    None when the object `(number, 0)` is not a stream, its /N or /First is
    not an integer from 0, reading its data raises, or the bytes before
    /First are not exactly 2 * N tokens of ASCII digits separated by PDF
    white space. The even tokens are the numbers.
    """
    try:
        stream = pdf.get_object(number, 0)
        if not isinstance(stream, pikepdf.Stream):
            return None
        count = stream.get("/N")
        first = stream.get("/First")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or isinstance(first, bool)
            or not isinstance(first, int)
            or first < 0
        ):
            return None
        head = stream.read_bytes()[:first]
    except (pikepdf.PdfError, ValueError):
        return None
    tokens = re.split(rb"[\x00\t\n\x0c\r ]+", head.strip(b"\x00\t\n\x0c\r "))
    if len(tokens) != 2 * count:
        return None
    numbers = []
    for token in tokens:
        if not token.isdigit():
            return None
        numbers.append(int(token))
    return numbers[::2]


# --- widgets and their fields, as written ---------------------------------------

# The field keys a FieldEntry holds, as PDF names.
_FIELD_KEYS = ("/T", "/TU", "/FT", "/Ff", "/V", "/DV", "/Opt")
# The keys a WidgetEntry reads from the widget itself, as PDF names.
_WIDGET_KEYS = ("/Rect", "/F", "/AS")
# The keys it takes from /MK and /BS, as pairs of the PDF name and the bytes key.
_MK_KEYS = (("/BC", b"MK/BC"), ("/BG", b"MK/BG"))
_BS_KEYS = (("/W", b"BS/W"), ("/S", b"BS/S"))


class _Kept:
    """Plain data kept per document so that a shared object converts once.

    A value, a parent or a /Kids array named by thousands of dictionaries is
    converted once per document, not once per dictionary that names it. The
    stores are keyed by object number and generation, the states and the
    entries of /MK and /BS one store per role, so that one object can play two
    (issue #36, decision 7). Nothing is dropped before close().
    """

    def __init__(self) -> None:
        self.written: dict[tuple[int, int, int], Written] = {}
        self.fields: dict[tuple[int, int], FieldEntry] = {}
        self.kids: dict[tuple[int, int], Kids] = {}
        self.direct_kids: dict[tuple[int, int], Kids] = {}
        self.n_states: dict[tuple[int, int], tuple[bytes, ...]] = {}
        self.ap_n_states: dict[tuple[int, int], tuple[bytes, ...]] = {}
        self.mk: dict[tuple[int, int], dict[bytes, Written]] = {}
        self.bs: dict[tuple[int, int], dict[bytes, Written]] = {}
        self.widgets: dict[tuple[int, int], WidgetEntry] = {}
        self.tree: FieldTree | None = None
        self.tree_walked = False


def _written(value: object, depth: int, kept: _Kept) -> Written:
    """A value as the file writes it; an indirect one is converted once per depth."""
    if isinstance(value, pikepdf.Object) and value.is_indirect:
        key = (*value.objgen, depth)
        if key in kept.written:
            return kept.written[key]
        written = _converted(value, depth, kept)
        kept.written[key] = written
        return written
    return _converted(value, depth, kept)


def _converted(value: object, depth: int, kept: _Kept) -> Written:
    if value is None:
        return Written("null", None)
    if isinstance(value, bool):
        return Written("boolean", value)
    if isinstance(value, int):
        return Written("integer", value)
    if isinstance(value, Decimal):
        number = float(value)
        return Written("real", number) if math.isfinite(number) else Written("other", None)
    if isinstance(value, pikepdf.String):
        return Written("string", bytes(value))
    if isinstance(value, pikepdf.Name):
        return Written("name", _name_bytes(value))
    if isinstance(value, pikepdf.Array):
        if depth >= 2:
            return Written("other", None)
        return Written("array", tuple(_written(item, depth + 1, kept) for item in value))
    if isinstance(value, pikepdf.Stream):
        return Written("stream", None)
    if isinstance(value, pikepdf.Dictionary):
        return Written("dictionary", None)
    return Written("other", None)


def _name_bytes(name: pikepdf.Name) -> bytes:
    """The bytes of a name: unparse() without its slash, #xx resolved."""
    syntax = name.unparse(resolved=True)
    out = bytearray()
    at = 1
    while at < len(syntax):
        if syntax[at : at + 1] == b"#":
            out.append(int(syntax[at + 1 : at + 3], 16))
            at += 3
        else:
            out.append(syntax[at])
            at += 1
    return bytes(out)


def _key_bytes(key: str) -> bytes:
    """The bytes of a key of /AP /N, without its slash."""
    return key.encode("utf-8", "surrogateescape")[1:]


def _field_entry(dictionary: pikepdf.Dictionary, kept: _Kept) -> FieldEntry:
    """A field dictionary's own entries; an indirect one is converted once."""
    if dictionary.is_indirect and dictionary.objgen in kept.fields:
        return kept.fields[dictionary.objgen]
    entries: dict[bytes, Written] = {}
    for key in _FIELD_KEYS:
        value = dictionary.get(key)
        if value is not None:
            entries[key.encode("ascii")[1:]] = _written(value, 0, kept)
    objgen = dictionary.objgen if dictionary.is_indirect else None
    entry = FieldEntry(
        object_number=objgen[0] if objgen else None,
        generation=objgen[1] if objgen else None,
        entries=MappingProxyType(entries),
        kids=isinstance(dictionary.get("/Kids"), pikepdf.Array),
        subtype_widget=dictionary.get("/Subtype") == pikepdf.Name("/Widget"),
    )
    if dictionary.is_indirect:
        kept.fields[dictionary.objgen] = entry
    return entry


def _kids(array: pikepdf.Array) -> Kids:
    """The entries of one /Kids array, with the positions of its widgets."""
    entries: list[KidEntry] = []
    positions: dict[tuple[int, int], list[int]] = {}
    widgets = 0
    all_widgets = True
    for item in array:
        dictionary = isinstance(item, pikepdf.Dictionary)
        objgen = item.objgen if dictionary and item.is_indirect else None
        widget = dictionary and item.get("/Subtype") == pikepdf.Name("/Widget")
        entries.append(
            KidEntry(objgen[0] if objgen else None, objgen[1] if objgen else None, widget)
        )
        if widget:
            if objgen is not None:
                positions.setdefault(objgen, []).append(widgets)
            widgets += 1
        else:
            all_widgets = False
    return Kids(
        tuple(entries),
        MappingProxyType({key: tuple(value) for key, value in positions.items()}),
        all_widgets,
    )


def _parent_kids(dictionary: pikepdf.Dictionary, kept: _Kept) -> Kids | None:
    """The /Kids of the widget's own /Parent; the array is read once per document."""
    parent = dictionary.get("/Parent")
    if not (isinstance(parent, pikepdf.Dictionary) and parent.is_indirect):
        return None
    kids_value = parent.get("/Kids")
    if not isinstance(kids_value, pikepdf.Array):
        return None
    if kids_value.is_indirect:
        store, key = kept.kids, kids_value.objgen
    else:
        # A direct array is kept by the indirect parent that holds it, so that
        # parents sharing one array share one Kids.
        store, key = kept.direct_kids, parent.objgen
    if key not in store:
        store[key] = _kids(kids_value)
    return store[key]


def _taken(source: pikepdf.Dictionary, names, kept: _Kept) -> dict[bytes, Written]:
    taken = {}
    for pdf_key, bytes_key in names:
        value = source.get(pdf_key)
        if value is not None:
            taken[bytes_key] = _written(value, 0, kept)
    return taken


def _sub_entries(
    source: pikepdf.Dictionary, names, store: dict, kept: _Kept
) -> dict[bytes, Written]:
    """Entries taken from /MK or /BS; an indirect one is converted once per role."""
    if source.is_indirect:
        if source.objgen in store:
            return dict(store[source.objgen])
        taken = _taken(source, names, kept)
        store[source.objgen] = taken
        return dict(taken)
    return _taken(source, names, kept)


def _states(n: pikepdf.Dictionary, ap: pikepdf.Dictionary, kept: _Kept) -> tuple[bytes, ...]:
    """The keys of an /N dictionary in QPDF's order; converted once per role."""
    if n.is_indirect:
        store, key = kept.n_states, n.objgen
    elif ap.is_indirect:
        store, key = kept.ap_n_states, ap.objgen
    else:
        return tuple(_key_bytes(key) for key in n)
    if key not in store:
        store[key] = tuple(_key_bytes(key) for key in n)
    return store[key]


def _widget_entry(
    dictionary: pikepdf.Dictionary, annotation: AnnotationEntry, kept: _Kept
) -> WidgetEntry:
    """One widget of a page, its own entries and the chain of dictionaries above it."""
    entries: dict[bytes, Written] = {}
    for key in _WIDGET_KEYS:
        value = dictionary.get(key)
        if value is not None:
            entries[key.encode("ascii")[1:]] = _written(value, 0, kept)
    mk = dictionary.get("/MK")
    mk_invalid = mk is not None and not isinstance(mk, pikepdf.Dictionary)
    if not mk_invalid and mk is not None:
        entries.update(_sub_entries(mk, _MK_KEYS, kept.mk, kept))
    bs = dictionary.get("/BS")
    bs_invalid = bs is not None and not isinstance(bs, pikepdf.Dictionary)
    if not bs_invalid and bs is not None:
        entries.update(_sub_entries(bs, _BS_KEYS, kept.bs, kept))
    ap = dictionary.get("/AP")
    states: tuple[bytes, ...] | None = None
    states_invalid = False
    if ap is not None:
        if not isinstance(ap, pikepdf.Dictionary):
            states_invalid = True
        else:
            n = ap.get("/N")
            if isinstance(n, pikepdf.Dictionary):
                states = _states(n, ap, kept)
            elif n is not None and not isinstance(n, pikepdf.Stream):
                states_invalid = True
    chain: list[FieldEntry] = [_field_entry(dictionary, kept)]
    seen = {dictionary.objgen} if dictionary.is_indirect else set()
    current = dictionary
    chain_loops = chain_cut = parent_invalid = False
    while True:
        parent = current.get("/Parent")
        if parent is None:
            break
        if not isinstance(parent, pikepdf.Dictionary):
            parent_invalid = True
            break
        if len(chain) >= 33:
            chain_cut = True
            break
        if parent.is_indirect and parent.objgen in seen:
            chain_loops = True
            break
        chain.append(_field_entry(parent, kept))
        if parent.is_indirect:
            seen.add(parent.objgen)
        current = parent
    return WidgetEntry(
        annotation=annotation,
        entries=MappingProxyType(entries),
        states=states,
        states_invalid=states_invalid,
        mk_invalid=mk_invalid,
        bs_invalid=bs_invalid,
        chain=tuple(chain),
        chain_loops=chain_loops,
        chain_cut=chain_cut,
        parent_invalid=parent_invalid,
        parent_kids=_parent_kids(dictionary, kept),
    )


class _TreeWalk:
    """The state of one walk of the field tree, and its identity rules."""

    def __init__(self, kept: _Kept) -> None:
        self.kept = kept
        self.entries: list[FieldEntry] = []
        self.dictionaries: list[object] = []
        self.kids: list[tuple[int | None, ...] | None] = []
        self.parents: list[int | None] = []
        self.matches: list[bool] = []
        self.reached: list[bool] = []
        # A node's identity: an objgen for an indirect one, otherwise what
        # holds it and its position there.
        self.index_of: dict[object, int] = {}
        self.arrays_done: dict[tuple[int, int], tuple[int | None, ...]] = {}
        self.arrays_active: set[tuple[int, int]] = set()
        self.array_nodes: dict[tuple[int, int], list[int]] = {}
        self.array_marked: set[tuple[int, int]] = set()
        # Nodes whose /Kids is an array still being walked: their kids are that
        # array's, filled in when its walk ends.
        self.array_waiting: dict[tuple[int, int], list[int]] = {}
        self.cut = False


def _tree_entry(
    entry: object, key: object, parent: int | None, level: int, walk: _TreeWalk
) -> int | None:
    """One entry of /Fields or of a /Kids array: its node's index, or None.

    Called once per entry, so that a test can count the steps of a walk.
    """
    if not isinstance(entry, pikepdf.Dictionary):
        return None
    if entry.is_indirect:
        key = entry.objgen
    if key in walk.index_of:
        index = walk.index_of[key]
        walk.reached[index] = True
        return index
    index = len(walk.entries)
    walk.index_of[key] = index
    walk.entries.append(_field_entry(entry, walk.kept))
    walk.dictionaries.append(entry)
    walk.kids.append(None)
    walk.parents.append(parent)
    walk.matches.append(_parent_matches(entry, parent, walk))
    walk.reached.append(False)
    kids_value = entry.get("/Kids")
    if isinstance(kids_value, pikepdf.Array):
        if level >= 32:
            walk.cut = True
        else:
            walk.kids[index] = _walk_kids(kids_value, key, index, level, walk)
    else:
        walk.kids[index] = ()
    return index


def _parent_matches(entry: pikepdf.Dictionary, parent: int | None, walk: _TreeWalk) -> bool:
    """Whether the node's /Parent is the dictionary of the node it was reached through."""
    named = entry.get("/Parent")
    if parent is None:
        return named is None
    if not (isinstance(named, pikepdf.Dictionary) and named.is_indirect):
        return False
    above = walk.dictionaries[parent]
    return above.is_indirect and above.objgen == named.objgen


def _walk_kids(
    array: pikepdf.Array, node_key: object, node_index: int, level: int, walk: _TreeWalk
) -> tuple[int | None, ...]:
    """The node indices of a /Kids array's entries; an indirect one is walked once."""
    if not array.is_indirect:
        return _kids_entries(array, (node_key, "direct kids"), node_index, level, walk)
    key = array.objgen
    if key in walk.arrays_done:
        if key not in walk.array_marked:
            walk.array_marked.add(key)
            for held in walk.array_nodes[key]:
                walk.reached[held] = True
        return walk.arrays_done[key]
    if key in walk.arrays_active:
        # A loop through the array: it is not walked again. The node gets its
        # kids when the walk of the array ends, and they count as met again.
        walk.array_waiting.setdefault(key, []).append(node_index)
        return ()
    walk.arrays_active.add(key)
    try:
        kids = _kids_entries(array, key, node_index, level, walk)
    finally:
        walk.arrays_active.discard(key)
    walk.arrays_done[key] = kids
    walk.array_nodes[key] = [index for index in kids if index is not None]
    waiting = walk.array_waiting.pop(key, [])
    for node in waiting:
        walk.kids[node] = kids
    if waiting:
        walk.array_marked.add(key)
        for held in walk.array_nodes[key]:
            walk.reached[held] = True
    return kids


def _kids_entries(
    array: pikepdf.Array, holder_key: object, parent_index: int, level: int, walk: _TreeWalk
) -> tuple[int | None, ...]:
    kids = []
    for position, item in enumerate(array):
        kids.append(_tree_entry(item, (holder_key, position), parent_index, level + 1, walk))
    return tuple(kids)


def _field_tree(pdf: pikepdf.Pdf, kept: _Kept) -> FieldTree | None:
    """The field tree from /AcroForm /Fields, each node once, depth first."""
    acroform = pdf.Root.get("/AcroForm")
    if acroform is None:
        return None
    if not isinstance(acroform, pikepdf.Dictionary):
        return FieldTree((), (), True, False)
    fields = acroform.get("/Fields")
    if not isinstance(fields, pikepdf.Array):
        return FieldTree((), (), True, False)
    walk = _TreeWalk(kept)
    holder = fields.objgen if fields.is_indirect else "fields"
    roots = []
    invalid = False
    for position, item in enumerate(fields):
        if not isinstance(item, pikepdf.Dictionary):
            invalid = True
        roots.append(_tree_entry(item, (holder, position), None, 0, walk))
    nodes = tuple(
        FieldNode(entry, kids, parent, matches, reached)
        for entry, kids, parent, matches, reached in zip(
            walk.entries, walk.kids, walk.parents, walk.matches, walk.reached, strict=True
        )
    )
    return FieldTree(nodes, tuple(roots), invalid, walk.cut)


def _inherited(page: pikepdf.Dictionary, key: str) -> object:
    """The value of an inheritable key on the page or its nearest ancestor.

    The chain of /Parent is followed as far as it goes, and never twice
    through the same object. The first value found counts, also when it is
    not usable, as in PDFium. pikepdf does not show a key whose value is
    null, so an explicit null counts as absent, as ISO 32000 says; PDFium
    stops at it instead, so #11's cross-check sees such a page as a
    disagreement.
    """
    node: object = page
    seen: set[tuple[int, int]] = set()
    while isinstance(node, pikepdf.Dictionary):
        value = node.get(key)
        if value is not None:
            return value
        if node.is_indirect:
            if node.objgen in seen:
                return None
            seen.add(node.objgen)
        node = node.get("/Parent")
    return None


def _number(value: object) -> float | None:
    """A PDF number as a finite float; None for anything else, a flag included.

    QPDF reads integers into 64 bits, so an int always fits a float; a real
    can still be too large and become infinite.
    """
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _box(value: object) -> Box | None:
    """Four numbers as written, corners not put in order; None otherwise."""
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        return None
    x0, y0, x1, y1 = (_number(item) for item in value)
    if x0 is None or y0 is None or x1 is None or y1 is None:
        return None
    return (x0, y0, x1, y1)


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _user_unit(value: object) -> tuple[float | None, bool]:
    """The value and whether it is invalid: absent is not invalid."""
    if value is None:
        return None, False
    number = _number(value)
    if number is None or number <= 0:
        return None, True
    return number, False
