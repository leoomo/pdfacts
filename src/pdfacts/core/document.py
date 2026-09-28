"""open(), Document and Page: the first thing a caller uses.

open() classifies the input before an engine sees it, starts the runtime and
opens the file with PDFium, then with QPDF. It repairs nothing: a file the
engine cannot load as it is written is damaged (docs/conventions.md, "Damaged
files"). Every call into an engine happens while the engine lock is held.

Page geometry always comes from PDFium. Object numbers and UserUnit come from
QPDF, and only where the two engines agree on the page (docs/conventions.md,
"Object identity"); elsewhere they are unknown, with the reason.
"""

from __future__ import annotations

import math
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from types import TracebackType

from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import Box, EngineFailure, FailureKind, PageObject
from pdfacts.backends.qpdf import QpdfDocument
from pdfacts.core import runtime
from pdfacts.core.errors import (
    HEADER_SEARCH_BYTES,
    ClosedError,
    DamagedFileError,
    EmptyInputError,
    EngineError,
    LimitExceededError,
    LimitUncheckedError,
    NotPdfError,
    PasswordRequiredError,
)
from pdfacts.core.geometry import PageGeometry, Rect
from pdfacts.core.limits import Limits
from pdfacts.core.refs import PageRef
from pdfacts.core.values import Unknown, UnknownReason

HEADER = b"%PDF-"
END = b"%%EOF"
# White space as ISO 32000 defines it, 7.2.2: NUL, tab, LF, FF, CR, space.
WHITE_SPACE = b"\x00\t\n\x0c\r "
# Two boxes are equal when every coordinate differs by at most this much:
# PDFium holds coordinates as 32-bit numbers, QPDF as decimal text.
BOX_TOLERANCE = 0.001


class Page:
    """One page of a Document: its geometry and its reference.

    Both were read when the page was first asked for. They are plain data and
    stay usable after the document is closed; the Page is not: afterwards
    every member raises ClosedError.
    """

    def __init__(self, document: Document, geometry: PageGeometry, ref: PageRef) -> None:
        # Document makes pages; a caller asks it for them.
        self._document = document
        self._geometry = geometry
        self._ref = ref

    @property
    def index(self) -> int:
        self._document._check_open()
        return self._geometry.index

    @property
    def geometry(self) -> PageGeometry:
        self._document._check_open()
        return self._geometry

    @property
    def ref(self) -> PageRef:
        self._document._check_open()
        return self._ref


class Pages:
    """The pages of a Document: len(), an index from 0, iteration.

    No slices and no negative index. A page is read when it is first asked
    for, and the deadline is checked before.
    """

    def __init__(self, document: Document) -> None:
        self._document = document

    def __len__(self) -> int:
        return self._document.page_count

    def __getitem__(self, index: int) -> Page:
        return self._document._page(index)

    def __iter__(self) -> Iterator[Page]:
        for index in range(len(self)):
            yield self._document._page(index)


class Document:
    """An open PDF document; open() makes one.

    Closing it releases the engines' handles. Afterwards every member except
    close() and closed raises ClosedError.
    """

    def __init__(self, opened: _Opened) -> None:
        # Use open(); this takes documents the engines have already opened.
        self._pdfium = opened.pdfium
        self._qpdf = opened.qpdf
        self._qpdf_unknown = opened.qpdf_unknown
        self._page_count = opened.page_count
        self._object_count = opened.object_count
        self._notes = list(opened.notes)
        self._deadline = opened.deadline
        self._read: dict[int, Page] = {}
        self._pages = Pages(self)
        self._closed = False
        # A document dropped without close() is closed under the engine lock,
        # or handed to the next holder of the lock (runtime.close_soon).
        # Otherwise pypdfium2 would close it whenever the garbage collector
        # runs, in whichever thread, while another thread may be in PDFium.
        # At interpreter exit pypdfium2's own handler may come first.
        self._finalizer = weakref.finalize(
            self, runtime.close_soon, _quiet_close(opened.pdfium, opened.qpdf)
        )

    @property
    def page_count(self) -> int:
        """The number of pages, as PDFium counts them."""
        self._check_open()
        return self._page_count

    @property
    def pages(self) -> Pages:
        self._check_open()
        return self._pages

    @property
    def object_count(self) -> int | Unknown:
        """The /Size of the trailer, as QPDF reads it."""
        self._check_open()
        return self._object_count

    @property
    def warnings(self) -> tuple[str, ...]:
        """Why values from QPDF are unknown, where they are, and what QPDF reported."""
        self._check_open()
        reported: tuple[str, ...] = ()
        if self._qpdf is not None:
            with runtime.engine():
                self._check_open()
                reported = _qpdf_call(self._qpdf.warnings)
        return tuple(self._notes) + reported

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Release the document. A further call does nothing but finish this one."""
        if not self._finalizer.alive:
            return
        with runtime.engine():
            # Closed for every use from here on, whatever happens below. The
            # finalizer stays until the engines have closed the document, so
            # that a close interrupted before that is still finished.
            self._closed = True
            if not self._finalizer.alive:
                return
            failures: list[EngineError] = []
            with _collect(failures):
                _pdfium_call(self._pdfium.close)
            if self._qpdf is not None:
                with _collect(failures):
                    _qpdf_call(self._qpdf.close)
            self._finalizer.detach()
            if failures:
                raise failures[0]

    def __enter__(self) -> Document:
        self._check_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _check_open(self) -> None:
        if self.closed:
            raise ClosedError()

    def _page(self, index: int) -> Page:
        self._check_open()
        if isinstance(index, slice):
            raise TypeError("pages do not take slices")
        if not isinstance(index, int) or isinstance(index, bool):
            raise TypeError(f"a page index is an int, not {type(index).__name__}")
        if not 0 <= index < self._page_count:
            raise IndexError(f"page index {index} is outside the {self._page_count} pages")
        page = self._read.get(index)
        if page is None:
            self._deadline()
            with runtime.engine():
                self._check_open()
                page = self._read_page(index)
            self._read[index] = page
        return page

    def _read_page(self, index: int) -> Page:
        box = _pdfium_call(lambda: self._pdfium.visible_box(index))
        rotation = _pdfium_call(lambda: self._pdfium.rotation(index))
        visible = _visible_box(index, box)
        unknown = self._qpdf_unknown
        page_object = None
        if unknown is None:
            assert self._qpdf is not None
            try:
                page_object = self._qpdf.page_object(index)
            except EngineFailure as failure:
                unknown = Unknown(UnknownReason.UNREADABLE, failure.detail)
                self._note(f"QPDF cannot give the values of pages: {failure.detail}")
        if page_object is not None:
            written = _written_visible_box(index, page_object)
            disagreement = _disagreement(box, rotation, written, page_object.rotate)
            if disagreement is not None:
                unknown = Unknown(UnknownReason.ENGINES_DISAGREE, disagreement)
                self._note(f"page {index}: {disagreement}")
        if unknown is not None:
            ref = PageRef(page_index=index, object_number=unknown, generation=unknown)
            user_unit: float | Unknown = unknown
        else:
            assert page_object is not None
            ref = PageRef(
                page_index=index,
                object_number=page_object.object_number,
                generation=page_object.generation,
            )
            user_unit = _user_unit(page_object)
        geometry = PageGeometry(
            index=index, visible_box=visible, rotation=rotation, user_unit=user_unit
        )
        return Page(self, geometry, ref)

    def _note(self, note: str) -> None:
        if note not in self._notes:
            self._notes.append(note)


class _Opened:
    """What open() hands to Document."""

    def __init__(
        self,
        *,
        pdfium: PdfiumDocument,
        page_count: int,
        qpdf: QpdfDocument | None,
        qpdf_unknown: Unknown | None,
        object_count: int | Unknown,
        notes: list[str],
        deadline: Callable[[], None],
    ) -> None:
        self.pdfium = pdfium
        self.page_count = page_count
        # None when QPDF could not open the file.
        self.qpdf = qpdf
        # Why every value from QPDF is unknown, when it is.
        self.qpdf_unknown = qpdf_unknown
        self.object_count = object_count
        self.notes = notes
        self.deadline = deadline


@contextmanager
def _collect(failures: list[EngineError]) -> Iterator[None]:
    """An EngineError is kept, so that the next close still runs."""
    try:
        yield
    except EngineError as failure:
        failures.append(failure)


def _quiet_close(pdfium: PdfiumDocument, qpdf: QpdfDocument | None) -> Callable[[], None]:
    """The close of a document nobody closed; nobody is left to hear of a failure."""

    def close() -> None:
        with suppress(EngineFailure):
            pdfium.close()
        if qpdf is not None:
            with suppress(EngineFailure):
                qpdf.close()

    return close


def open(
    data: bytes,
    *,
    password: str | None = None,
    limits: Limits | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> Document:
    """Open a PDF file from its bytes.

    The caller keeps the bytes: the facts describe exactly them, and their
    hash identifies them. A path would hide which bytes were read.

    Raises EmptyInputError, NotPdfError or DamagedFileError for input that is
    not a readable PDF, PasswordRequiredError when a password is needed or
    wrong, LimitExceededError when a limit is exceeded, LimitUncheckedError
    when max_objects is set and the object count is unknown, and EngineError
    when an engine fails otherwise. MemoryError passes unchanged.

    `_clock` is for tests: the deadline counts from the start of open().
    """
    started = _clock()
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, not {type(data).__name__}")
    if password is not None:
        _check_password(password)
    if limits is None:
        limits = Limits()
    elif not isinstance(limits, Limits):
        raise TypeError(f"limits must be a Limits or None, not {type(limits).__name__}")

    # The input is classified before an engine sees it: PDFium gives the same
    # error for an empty file, a file without a header and a truncated one.
    if not data:
        raise EmptyInputError()
    if limits.max_bytes is not None and len(data) > limits.max_bytes:
        raise LimitExceededError(limit="max_bytes", allowed=limits.max_bytes, found=len(data))
    if HEADER not in data[:HEADER_SEARCH_BYTES]:
        raise NotPdfError()
    # A file cut short can end in an earlier revision's cross-reference table,
    # which PDFium then reads without a word; so can one with bytes appended.
    end = data.rfind(END)
    if end < 0 or data[end + len(END) :].strip(WHITE_SPACE):
        raise DamagedFileError(
            detail="it does not end with %%EOF and white space; it may be cut short "
            "or have bytes appended"
        )

    runtime.start()
    with runtime.engine():
        try:
            pdfium = PdfiumDocument.open(data, password)
        except EngineFailure as failure:
            raise _open_error(failure, password) from failure
        qpdf: QpdfDocument | None = None
        try:
            page_count = _pdfium_call(pdfium.page_count)
            # The limit first: a file over the limit is refused before the
            # check that reads every page dictionary, and a file both over the
            # limit and with a page missing gets LimitExceededError.
            if limits.max_pages is not None and page_count > limits.max_pages:
                raise LimitExceededError(
                    limit="max_pages", allowed=limits.max_pages, found=page_count
                )
            try:
                pdfium.check_pages()
            except EngineFailure as failure:
                raise _open_error(failure, password) from failure
            qpdf, qpdf_unknown = _open_qpdf(data, password, page_count)
            object_count = _object_count(qpdf, qpdf_unknown)
            if limits.max_objects is not None:
                if isinstance(object_count, Unknown):
                    raise LimitUncheckedError(
                        limit="max_objects", allowed=limits.max_objects, reason=object_count
                    )
                if object_count > limits.max_objects:
                    raise LimitExceededError(
                        limit="max_objects", allowed=limits.max_objects, found=object_count
                    )
            return Document(
                _Opened(
                    pdfium=pdfium,
                    page_count=page_count,
                    qpdf=qpdf,
                    qpdf_unknown=qpdf_unknown,
                    object_count=object_count,
                    notes=[qpdf_unknown.detail] if qpdf_unknown is not None else [],
                    deadline=_deadline(limits.deadline_seconds, started, _clock),
                )
            )
        except BaseException:
            # The first exception is the one to report. MemoryError is never
            # swallowed.
            with suppress(EngineFailure):
                pdfium.close()
            if qpdf is not None:
                with suppress(EngineFailure):
                    qpdf.close()
            raise


def _open_qpdf(
    data: bytes, password: str | None, page_count: int
) -> tuple[QpdfDocument | None, Unknown | None]:
    """QPDF's view of the file, and why its values are unknown if they are.

    A failure of QPDF does not end open(): PDFium has read the file, and
    only the values that come from QPDF become unknown (issue #11, decision 6).
    """
    try:
        qpdf = QpdfDocument.open(data, password)
    except EngineFailure as failure:
        return None, Unknown(
            UnknownReason.UNREADABLE, f"QPDF could not open the file: {failure.detail}"
        )
    count = _qpdf_call(qpdf.page_count)
    if count != page_count:
        return qpdf, Unknown(
            UnknownReason.ENGINES_DISAGREE, f"PDFium counts {page_count} pages, QPDF {count}"
        )
    return qpdf, None


def _object_count(qpdf: QpdfDocument | None, unknown: Unknown | None) -> int | Unknown:
    if unknown is not None:
        return unknown
    assert qpdf is not None
    count = _qpdf_call(qpdf.object_count)
    if count is None:
        return Unknown(
            UnknownReason.INVALID, "the trailer has no /Size that is a whole number above 0"
        )
    return count


def _deadline(
    seconds: float | None, started: float, clock: Callable[[], float]
) -> Callable[[], None]:
    """What raises LimitExceededError once `seconds` have passed since `started`."""

    def check() -> None:
        if seconds is None:
            return
        elapsed = clock() - started
        if elapsed > seconds:
            raise LimitExceededError(limit="deadline_seconds", allowed=seconds, found=elapsed)

    return check


def _visible_box(index: int, box: Box) -> Rect:
    """PDFium's visible box as a Rect; DamagedFileError when it is none.

    Not finite: the file writes a number beyond what PDFium holds. No area:
    the crop box lies outside the media box, and PDFium answers (0, 0, 0, 0),
    or it touches it only. Neither is a box to report (docs/conventions.md,
    "Damaged files").
    """
    if not all(math.isfinite(value) for value in box):
        raise DamagedFileError(detail=f"page {index} has a visible box that is not finite: {box}")
    rect = Rect(*box)
    if rect.width == 0 or rect.height == 0:
        raise DamagedFileError(detail=f"page {index} has no visible area: {box}")
    return rect


def _written_visible_box(index: int, page: PageObject) -> Box | None:
    """The media box intersected with the crop box, as the file writes them.

    None without a usable media box, which leaves PDFium's US letter nothing
    to be compared with. DamagedFileError when the file's visible area is
    empty or flat: PDFium puts in US letter for a flat media box and ignores
    a flat crop box, both of which are values the file does not write.
    """
    if page.media_box is None:
        return None
    visible: Box | None = _ordered(page.media_box)
    if page.crop_box is not None:
        visible = _intersection(visible, _ordered(page.crop_box))
    if visible is None or visible[0] == visible[2] or visible[1] == visible[3]:
        written = f"media box {page.media_box}, crop box {page.crop_box}"
        raise DamagedFileError(detail=f"page {index} has no visible area: {written}")
    return visible


def _disagreement(box: Box, rotation: int, written: Box | None, rotate: int | None) -> str | None:
    """How QPDF's page differs from PDFium's, or None.

    QPDF's values are brought into PDFium's form first: corners in order, the
    media box intersected with the crop box, the rotation into 0..270
    (issue #11, decision 7). Without a usable media box only the rotation is
    compared.
    """
    theirs = _normal_rotation(rotate)
    if theirs != rotation:
        return f"PDFium turns it by {rotation}, QPDF by {theirs}"
    if written is None:
        return None
    if any(abs(a - b) > BOX_TOLERANCE for a, b in zip(box, written, strict=True)):
        return f"PDFium's visible box is {box}, QPDF's {written}"
    return None


def _normal_rotation(rotate: int | None) -> int:
    """value % 360; a result that is not a multiple of 90 counts as 0."""
    if rotate is None:
        return 0
    turned = rotate % 360
    return turned if turned % 90 == 0 else 0


def _ordered(box: Box) -> Box:
    x0, y0, x1, y1 = box
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _intersection(first: Box, second: Box) -> Box | None:
    """The overlap of two boxes with ordered corners; None when they do not overlap."""
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    if x0 > x1 or y0 > y1:
        return None
    return (x0, y0, x1, y1)


def _user_unit(page: PageObject) -> float | Unknown:
    """1.0 when absent, the default ISO 32000 defines; unknown when not usable."""
    if page.user_unit_invalid:
        return Unknown(UnknownReason.INVALID, "UserUnit is not a number greater than 0")
    if page.user_unit is None:
        return 1.0
    return page.user_unit


def _check_password(password: object) -> None:
    if not isinstance(password, str):
        raise TypeError(f"password must be a str or None, not {type(password).__name__}")
    # PDFium takes the password as text ending at NUL: everything after a NUL
    # would be ignored, and "user\x00junk" would open a file whose password
    # is "user".
    if "\x00" in password:
        raise ValueError("a password cannot contain NUL")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("the password cannot be written as UTF-8") from None


def _open_error(failure: EngineFailure, password: str | None) -> Exception:
    if failure.kind is FailureKind.PASSWORD:
        return PasswordRequiredError(password_was_given=password is not None)
    if failure.kind is FailureKind.FORMAT:
        return DamagedFileError(detail=failure.detail)
    return EngineError(engine="pdfium", detail=failure.detail)


def _pdfium_call[T](call: Callable[[], T]) -> T:
    """call(), with a failure of PDFium as EngineError."""
    try:
        return call()
    except EngineFailure as failure:
        raise EngineError(engine="pdfium", detail=failure.detail) from failure


def _qpdf_call[T](call: Callable[[], T]) -> T:
    """call(), with a failure of QPDF as EngineError."""
    try:
        return call()
    except EngineFailure as failure:
        raise EngineError(engine="qpdf", detail=failure.detail) from failure
