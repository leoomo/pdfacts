"""PDFium backend: text, geometry and rendering.

The only module allowed to import pypdfium2.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

import pypdfium2
import pypdfium2.raw as pdfium_c

from pdfacts.backends.protocols import Box, EngineFailure, FailureKind

T = TypeVar("T")

# Every font info handed to PDFium by install_font_policy(). None may ever be
# freed, so the list is never cleared: PDFium keeps a pointer to the last one,
# and when a font info is replaced it reads the old one once more (the
# destructor of CFX_ExternalFontInfo looks at Release). There is more than one
# entry only when an exception interrupted an installation that was repeated.
_FONT_INFOS: list[pdfium_c.FPDF_SYSFONTINFO] = []
# True once FPDF_SetSystemFontInfo has returned.
_font_policy_installed = False


@dataclass(frozen=True)
class PdfiumVersions:
    binding: str
    engine: str
    origin: str
    flags: tuple[str, ...]


def versions() -> PdfiumVersions:
    info = pypdfium2.PDFIUM_INFO
    return PdfiumVersions(
        binding=str(pypdfium2.PYPDFIUM_INFO),
        engine=str(info),
        origin=str(info.origin),
        flags=tuple(str(flag) for flag in info.flags),
    )


def install_font_policy(policy: str) -> None:
    """Install the font policy for the whole process.

    "embedded-only": fonts installed on the host are ignored. PDFium gets a
    font info of version 1 whose callbacks are all null, so it uses only the
    fonts embedded in a file and its own built-in substitutes. Once a call has
    returned, every further call does nothing.

    PDFium's header marks GetFontData, DeleteFont and one of MapFont and
    GetFont as required. The pinned build checks every callback for null
    before calling it (fpdfsdk/fpdf_sysfontinfo.cpp, CFX_ExternalFontInfo, on
    the branch chromium/7999); that is what makes a font info without
    callbacks safe. An engine upgrade has to check it again. Any version other
    than 1 or 2 is ignored without an error.
    """
    global _font_policy_installed
    if policy != "embedded-only":
        raise ValueError(f"unknown font policy {policy!r}")
    if _font_policy_installed:
        return
    info = pdfium_c.FPDF_SYSFONTINFO()
    info.version = 1
    # Kept before PDFium gets the pointer and never dropped. An exception, for
    # example from a signal handler, can arrive just before the call or just
    # after it, and after it PDFium holds the pointer.
    _FONT_INFOS.append(info)
    pdfium_c.FPDF_SetSystemFontInfo(ctypes.byref(info))
    _font_policy_installed = True


# The error codes of FPDF_GetLastError that say something about the input.
_FAILURE_KINDS = {
    pdfium_c.FPDF_ERR_FORMAT: FailureKind.FORMAT,
    pdfium_c.FPDF_ERR_PASSWORD: FailureKind.PASSWORD,
}
# What public/fpdfview.h says each code means.
_ERROR_NAMES = {
    pdfium_c.FPDF_ERR_SUCCESS: "no error",
    pdfium_c.FPDF_ERR_UNKNOWN: "unknown error",
    pdfium_c.FPDF_ERR_FILE: "file not found or could not be opened",
    pdfium_c.FPDF_ERR_FORMAT: "file not in PDF format or corrupted",
    pdfium_c.FPDF_ERR_PASSWORD: "password required or incorrect password",
    pdfium_c.FPDF_ERR_SECURITY: "unsupported security scheme",
    pdfium_c.FPDF_ERR_PAGE: "page not found or content error",
}


@contextmanager
def _translated() -> Iterator[None]:
    """Every exception from the engine as EngineFailure.

    MemoryError passes unchanged, and so does everything that is not an
    Exception, such as KeyboardInterrupt.
    """
    try:
        yield
    except MemoryError:
        raise
    except EngineFailure:
        raise
    except pypdfium2.PdfiumError as error:
        kind = _FAILURE_KINDS.get(getattr(error, "err_code", None), FailureKind.OTHER)
        raise EngineFailure(kind, str(error)) from error
    except Exception as error:
        raise EngineFailure(FailureKind.OTHER, str(error)) from error


def _check_index(index: int, count: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError(f"a page index is an int, not {type(index).__name__}")
    if not 0 <= index < count:
        raise IndexError(f"page index {index} is outside the {count} pages")


class PdfiumDocument:
    """A document opened by PDFium: a GeometryReader.

    It holds the native handle; no method returns a pypdfium2 object. A page is
    opened for one call and closed before the call returns.
    """

    def __init__(self, document: pypdfium2.PdfDocument, data: bytes) -> None:
        # Use open(); this takes a document that is already open.
        self._document = document
        # PDFium reads from these bytes for as long as the document is open.
        self._data = data
        self._closed = False

    @classmethod
    def open(cls, data: bytes, password: str | None) -> PdfiumDocument:
        """Open `data` as the file writes it.

        A FORMAT failure also when PDFium could read the file only by
        rebuilding its cross-reference table, which is a repair, and when it
        finds no page, or no page dictionary for one of the pages it counts
        (docs/conventions.md, "Damaged files").

        The document is loaded through the raw call, not through pypdfium2's
        PdfDocument, which leaves the handle of a document without pages open.
        The handle is wrapped at once, so that pypdfium2 alone closes it: if
        wrapping fails, the handle leaks rather than being closed twice.
        """
        secret = None if password is None else (password + "\x00").encode("utf-8")
        with _translated():
            raw = pdfium_c.FPDF_LoadMemDocument64(data, len(data), secret)
            if not raw:
                code = pdfium_c.FPDF_GetLastError()
                raise EngineFailure(
                    _FAILURE_KINDS.get(code, FailureKind.OTHER),
                    f"PDFium could not load the document: {_ERROR_NAMES.get(code, 'error')} "
                    f"(code {code})",
                )
            document = pypdfium2.PdfDocument(raw)
            try:
                _check_as_written(raw)
            except BaseException:
                document.close()
                raise
        return cls(document, data)

    def page_count(self) -> int:
        self._check_open()
        with _translated():
            return len(self._document)

    def visible_box(self, index: int) -> Box:
        """The crop box intersected with the media box, corners in order.

        Inheritance is resolved and the box is not rotated. A page without a
        usable media box gets US letter, as PDFium assumes.
        """
        return self._read_page(index, _bounding_box)

    def rotation(self, index: int) -> int:
        """0, 90, 180 or 270; PDFium brings other values into that form."""
        return self._read_page(index, lambda page: int(page.get_rotation()))

    def close(self) -> None:
        """Close the document. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        with _translated():
            self._document.close()

    def _check_open(self) -> None:
        # The backend's own flag: what the engine does with a closed handle
        # is not something to rely on.
        if self._closed:
            raise EngineFailure(FailureKind.OTHER, "closed")

    def _read_page(self, index: int, read: Callable[[pypdfium2.PdfPage], T]) -> T:
        count = self.page_count()
        _check_index(index, count)
        with _translated():
            page = self._document[index]
            try:
                return read(page)
            finally:
                page.close()


def _check_as_written(raw: pdfium_c.FPDF_DOCUMENT) -> None:
    """A FORMAT failure unless PDFium reads the document without repairing it."""
    count = pdfium_c.FPDF_GetPageCount(raw)
    if count < 1:
        raise EngineFailure(FailureKind.FORMAT, "PDFium finds no page in the document")
    if not pdfium_c.FPDF_DocumentHasValidCrossReferenceTable(raw):
        raise EngineFailure(
            FailureKind.FORMAT,
            "PDFium could read the document only by rebuilding its cross-reference table",
        )
    # The page count comes from /Count and can promise pages that are not
    # there. The size is read from the page dictionary without loading the
    # page.
    size = pdfium_c.FS_SIZEF()
    for index in range(count):
        if not pdfium_c.FPDF_GetPageSizeByIndexF(raw, index, size):
            raise EngineFailure(
                FailureKind.FORMAT,
                f"PDFium finds no page dictionary for page {index} of the {count} it counts",
            )


def _bounding_box(page: pypdfium2.PdfPage) -> Box:
    rect = pdfium_c.FS_RECTF()
    if not pdfium_c.FPDF_GetPageBoundingBox(page.raw, rect):
        raise EngineFailure(FailureKind.OTHER, "FPDF_GetPageBoundingBox failed")
    return (float(rect.left), float(rect.bottom), float(rect.right), float(rect.top))
