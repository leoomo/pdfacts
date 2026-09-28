"""open() and Document: the first thing a caller uses.

open() classifies the input before an engine sees it, starts the runtime and
opens the file with PDFium. It never repairs anything: a file the engine
cannot load is damaged (docs/conventions.md, "Damaged files"). Every call into
an engine happens while the engine lock is held.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from types import TracebackType

from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.core import runtime
from pdfacts.core.errors import (
    HEADER_SEARCH_BYTES,
    ClosedError,
    DamagedFileError,
    EmptyInputError,
    EngineError,
    LimitExceededError,
    NotPdfError,
    PasswordRequiredError,
)
from pdfacts.core.limits import Limits

HEADER = b"%PDF-"


class Document:
    """An open PDF document; open() makes one.

    Closing it releases the engine's handles. Afterwards every member except
    close() and closed raises ClosedError.
    """

    def __init__(self, pdfium: PdfiumDocument, page_count: int) -> None:
        # Use open(); this takes a backend document that is already open.
        self._pdfium = pdfium
        self._page_count = page_count
        self._closed = False

    @property
    def page_count(self) -> int:
        """The number of pages, as PDFium counts them."""
        self._check_open()
        return self._page_count

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Release the document. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        with runtime.engine_lock:
            _pdfium_call(self._pdfium.close)

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
        if self._closed:
            raise ClosedError()


def open(data: bytes, *, password: str | None = None, limits: Limits | None = None) -> Document:  # noqa: A001
    """Open a PDF file from its bytes.

    The caller keeps the bytes: the facts describe exactly them, and their
    hash identifies them. A path would hide which bytes were read.

    Raises EmptyInputError, NotPdfError or DamagedFileError for input that is
    not a readable PDF, PasswordRequiredError when a password is needed or
    wrong, LimitExceededError when a limit is exceeded, and EngineError when
    the engine fails otherwise. MemoryError passes unchanged.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, not {type(data).__name__}")
    if password is not None and not isinstance(password, str):
        raise TypeError(f"password must be a str or None, not {type(password).__name__}")
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

    runtime.start()
    with runtime.engine_lock:
        try:
            pdfium = PdfiumDocument.open(data, password)
        except EngineFailure as failure:
            raise _open_error(failure, password) from failure
        try:
            page_count = _pdfium_call(pdfium.page_count)
            if limits.max_pages is not None and page_count > limits.max_pages:
                raise LimitExceededError(
                    limit="max_pages", allowed=limits.max_pages, found=page_count
                )
        except BaseException:
            # The first exception is the one to report.
            with suppress(Exception):
                pdfium.close()
            raise
    return Document(pdfium, page_count)


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
