"""QPDF backend: object access, rewrite and decryption.

The only module allowed to import pikepdf.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from decimal import Decimal

import pikepdf

from pdfacts.backends.protocols import Box, EngineFailure, FailureKind, PageObject, RecoveredBytes

# How far from the end QPDF looks for startxref (measured: it finds one that
# starts 1054 bytes before the end, not one that starts 1055 bytes before).
_STARTXREF_SEARCH = 1054
# startxref and an offset, as QPDF reads through them (measured: it passes
# over a startxref without an offset, one glued to its offset and one whose
# offset other characters follow). Stricter than QPDF, which also takes a
# comment or a sign before the offset.
_STARTXREF = re.compile(rb"startxref[\x00\t\n\x0c\r ]+([0-9]+)(?![^\x00\t\n\x0c\r ()<>\[\]{}/%])")
# What the offset after startxref names: a cross-reference table, or an object,
# which may be a cross-reference stream. QPDF skips white space before it
# (measured).
_SECTION = re.compile(
    rb"[\x00\t\n\x0c\r ]*(?:xref|[0-9]+[\x00\t\n\x0c\r ]+[0-9]+[\x00\t\n\x0c\r ]+obj)"
)
# "N G obj", where an object begins.
_OBJECT_HEADER = re.compile(
    rb"(?<![0-9])[0-9]+[\x00\t\n\x0c\r ]+[0-9]+[\x00\t\n\x0c\r ]+obj(?![A-Za-z])"
)
# The end of the first page, in the linearization dictionary of a linearized
# file, which lies within its first 1024 bytes (ISO 32000-1, F.2).
_FIRST_PAGE_END = re.compile(rb"/E[\x00\t\n\x0c\r ]+([0-9]+)")


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
    ) -> None:
        # Use open(); this takes documents that are already open.
        self._walked = walked
        self._unwalked = unwalked
        self._sources = sources
        # The page objects as QPDF found them, read once: pikepdf takes time
        # in proportion to the page count for each access to its page list.
        self._pages = pages
        self._pages_as_written = pages_as_written
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
        except BaseException:
            for view in views:
                # The first exception is the one to report. MemoryError is
                # never swallowed.
                with suppress(EngineFailure), _translated():
                    view.close()
            for source in sources:
                source.close()
            raise
        return cls(walked, unwalked, sources, pages, pages == as_written)

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

    QPDF reads the file up to the end of its last whole revision, and what
    follows is left out. Where objects follow, the file ends inside an
    incremental update: a FORMAT failure, since QPDF would read the revision
    before without a word, or, rebuilding the file from its objects, mix the
    update with it; it places objects in object streams only through a
    cross-reference stream, which a cut update lacks. A file without a whole
    revision is rebuilt from its objects.

    The warnings are those QPDF gave while it rebuilt the file, walked its
    pages and wrote it: it reads streams only when it writes them.
    """
    end = _last_revision(data)
    if end is None:
        # A comment longer than QPDF's search for startxref makes it rebuild
        # the file from its objects, the last of each number counting, and
        # keeps it from dropping an object that only white space follows to
        # the end ("EOF after endobj"). Comments mean nothing, and QPDF does
        # not write them.
        data += b"%" + b" " * (2 * _STARTXREF_SEARCH)
    elif _OBJECT_HEADER.search(data, end):
        raise EngineFailure(
            FailureKind.FORMAT,
            "it ends inside an incremental update, which QPDF would drop without a word "
            "or, rebuilding the file from its objects, mix with the revision before",
        )
    else:
        data = data[:end]
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


def _last_revision(data: bytes) -> int | None:
    """Where the offset after the startxref of the last whole revision ends.

    None where the file has no whole revision. A startxref ends a revision
    where QPDF reads through it, its offset names a cross-reference section,
    and that section lies after the startxref of the revision before: a
    later revision's section lies nowhere else, and an offset cut short, or
    cut short and followed by other bytes, can name an earlier one. In a
    linearized file, a startxref before the end of the first page ends the
    first-page section, not a revision; its offset is often 0, which names
    no section, but need not be.
    """
    head = data[:1024]
    first_page_end = 0
    if b"/Linearized" in head:
        match = _FIRST_PAGE_END.search(head, 0, max(head.find(b"endobj"), 0))
        first_page_end = int(match.group(1)) if match else 0
    previous = end = None
    for found in re.finditer(rb"startxref", data):
        if found.start() < first_page_end:
            continue
        match = _STARTXREF.match(data, found.start())
        if match is None:
            continue
        offset = int(match.group(1))
        if _SECTION.match(data, offset) and (previous is None or offset > previous):
            previous, end = found.start(), match.end()
    return end


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
