"""PDFium backend: text, geometry and rendering.

The only module allowed to import pypdfium2.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import TypeVar

import pypdfium2
import pypdfium2.raw as pdfium_c

from pdfacts.backends.protocols import (
    AnnotationView,
    Box,
    EngineFailure,
    FailureKind,
    RawBitmap,
)

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


# The most pixels a side of a rendered page may have: PDFium takes it as a C int.
_MOST_PIXELS = 2**31 - 1
# A bitmap whose memory PDFium allocates and pypdfium2 frees, rows packed.
_PACKED_BITMAP = partial(pypdfium2.PdfBitmap.new_foreign, force_packed=True)

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
    """A document opened by PDFium: a GeometryReader, a Renderer, an AnnotationViewer.

    It holds the native handle; no method returns a pypdfium2 object. A page is
    opened for one call and closed before the call returns. No form environment
    is ever made: the document that renders must draw as M1 recorded.
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
        finds no page (docs/conventions.md, "Damaged files"). Whether every
        page it counts is there is check_pages(), which takes longer.

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
                # The first failure is the one to report. MemoryError is
                # never swallowed.
                with suppress(EngineFailure), _translated():
                    document.close()
                raise
        return cls(document, data)

    def page_count(self) -> int:
        self._check_open()
        with _translated():
            return len(self._document)

    def check_pages(self) -> None:
        """A FORMAT failure unless PDFium finds a dictionary for every page it counts.

        The count comes from /Count and can promise pages that are not there.
        Each size is read from the page dictionary without loading the page,
        but the time still grows with the page count: the core checks its
        page limit first.
        """
        self._check_open()
        with _translated():
            count = len(self._document)
            size = pdfium_c.FS_SIZEF()
            for index in range(count):
                if not pdfium_c.FPDF_GetPageSizeByIndexF(self._document.raw, index, size):
                    raise EngineFailure(
                        FailureKind.FORMAT,
                        f"PDFium finds no page dictionary for page {index} of the {count} "
                        "it counts",
                    )

    def visible_box(self, index: int) -> Box:
        """The crop box intersected with the media box, corners in order.

        Inheritance is resolved and the box is not rotated. A page without a
        usable media box gets US letter, as PDFium assumes.
        """
        return self._read_page(index, _bounding_box)

    def rotation(self, index: int) -> int:
        """0, 90, 180 or 270; PDFium brings other values into that form."""
        return self._read_page(index, lambda page: int(page.get_rotation()))

    def render(
        self, index: int, *, scale: float, crop: tuple[float, float, float, float], rgb: bool
    ) -> RawBitmap:
        """The page at `scale` pixels per unit, without what `crop` cuts off.

        `scale` is finite and above 0, and `crop` is not negative; the core
        makes sure. The page's own rotation is applied, and forms and
        annotations are drawn as PDFium draws them by default. Width and height
        are what pypdfium2 makes of the page size, the scale and the crop. The
        pixels are copied out of PDFium's bitmap before it is freed.

        ValueError when the page at this scale is more pixels than PDFium
        addresses, or when less than one pixel is left after the crop.
        """
        rendered = self._read_page(index, lambda page: _rendered(page, scale, crop, rgb))
        if isinstance(rendered, str):
            raise ValueError(rendered)
        return rendered

    def annotation_count(self, index: int) -> int:
        """The number of annotations PDFium counts on the page, every entry."""
        return self._read_page(index, lambda page: int(pdfium_c.FPDFPage_GetAnnotCount(page.raw)))

    def annotation_views(self, index: int) -> tuple[AnnotationView | None, ...]:
        """What PDFium reads in each annotation dictionary of the page's /Annots.

        One view per entry PDFium counts, `None` where `FPDFPage_GetAnnot`
        gives no annotation: for a NULL handle the reading functions answer as
        for an annotation without entries, so nothing but the None tells that
        there is no dictionary.
        """
        return self._read_page(index, _annotation_views)

    def close(self) -> None:
        """Close the document. A further call does nothing but finish this one.

        Every other method refuses from the start of the first call, whatever
        happens during it: an interrupt can come after PDFium freed the handle
        and before pypdfium2 forgot it. A further call finishes a close that
        was interrupted before pypdfium2 began; pypdfium2 frees the handle at
        most once. Interrupted after pypdfium2 began and before it reached
        PDFium, the handle leaks, which is the lesser harm than freeing it
        twice.
        """
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


def _annotation_views(page: pypdfium2.PdfPage) -> tuple[AnnotationView | None, ...]:
    views: list[AnnotationView | None] = []
    for index in range(pdfium_c.FPDFPage_GetAnnotCount(page.raw)):
        views.append(_annotation_view(page, index))
    return tuple(views)


def _annotation_view(page: pypdfium2.PdfPage, index: int) -> AnnotationView | None:
    annot = pdfium_c.FPDFPage_GetAnnot(page.raw, index)
    if not annot:
        return None
    try:
        rect: Box | None = None
        bounds = pdfium_c.FS_RECTF()
        if pdfium_c.FPDFAnnot_GetRect(annot, ctypes.byref(bounds)):
            rect = (bounds.left, bounds.bottom, bounds.right, bounds.top)
        values = {
            key: (pdfium_c.FPDFAnnot_GetValueType(annot, key), _annot_text(annot, key))
            for key in (b"V", b"DV", b"TU", b"FT")
        }
        return AnnotationView(
            index=index,
            widget=pdfium_c.FPDFAnnot_GetSubtype(annot) == pdfium_c.FPDF_ANNOT_WIDGET,
            rect=rect,
            flags=pdfium_c.FPDFAnnot_GetFlags(annot),
            t_type=pdfium_c.FPDFAnnot_GetValueType(annot, b"T"),
            t=_annot_text(annot, b"T"),
            as_type=pdfium_c.FPDFAnnot_GetValueType(annot, b"AS"),
            as_text=_annot_text(annot, b"AS"),
            parent_type=pdfium_c.FPDFAnnot_GetValueType(annot, b"Parent"),
            values=MappingProxyType(values),
        )
    finally:
        pdfium_c.FPDFPage_CloseAnnot(annot)


def _annot_text(annot: pdfium_c.FPDF_ANNOTATION, key: bytes) -> str:
    """FPDFAnnot_GetStringValue as text: UTF-16 with lone surrogates kept."""
    needed = pdfium_c.FPDFAnnot_GetStringValue(annot, key, None, 0)
    if needed <= 2:
        return ""
    buffer = ctypes.create_string_buffer(needed)
    pdfium_c.FPDFAnnot_GetStringValue(
        annot, key, ctypes.cast(buffer, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), needed
    )
    return buffer.raw[:-2].decode("utf-16-le", "surrogatepass")


def _check_as_written(raw: pdfium_c.FPDF_DOCUMENT) -> None:
    """A FORMAT failure when PDFium repaired the document or finds no page."""
    if pdfium_c.FPDF_GetPageCount(raw) < 1:
        raise EngineFailure(FailureKind.FORMAT, "PDFium finds no page in the document")
    if not pdfium_c.FPDF_DocumentHasValidCrossReferenceTable(raw):
        raise EngineFailure(
            FailureKind.FORMAT,
            "PDFium could read the document only by rebuilding its cross-reference table",
        )


def _rendered(
    page: pypdfium2.PdfPage, scale: float, crop: tuple[float, float, float, float], rgb: bool
) -> RawBitmap | str:
    """The page drawn and copied, or why nothing was drawn.

    The page's size in pixels goes to PDFium as a C int. pypdfium2 raises
    ValueError when less than one pixel is left after the crop, before it
    makes a bitmap; for the options used here it has no other.

    The bitmap is one PDFium allocates, which pypdfium2 frees on close.
    pypdfium2's default bitmap, over a buffer of its own, is never freed in
    PDFium (5.13.0 passes needs_free=False). Its rows are packed: PDFium would
    pad them to four bytes that it never draws. RGB is PDFium's BGR with the
    byte order reversed.
    """
    width, height = page.get_width() * scale, page.get_height() * scale
    if not (width <= _MOST_PIXELS and height <= _MOST_PIXELS):
        return f"at scale {scale} the page is {width} x {height} pixels, more than PDFium addresses"
    # Every bitmap made is closed here, under the engine lock, also when an
    # interrupt arrives inside pypdfium2 before it returns the bitmap. Not
    # covered: an interrupt while pypdfium2 makes the bitmap, which leaks the
    # handle or leaves it to pypdfium2's finalizer, outside the lock.
    made: list[pypdfium2.PdfBitmap] = []

    def make(*args: object, **kwargs: object) -> pypdfium2.PdfBitmap:
        made.append(_PACKED_BITMAP(*args, **kwargs))
        return made[-1]

    try:
        try:
            bitmap = page.render(
                scale=scale, crop=crop, grayscale=not rgb, rev_byteorder=rgb, bitmap_maker=make
            )
        except ValueError:
            return f"at scale {scale} less than one pixel is left after the crop {crop}"
        return RawBitmap(
            width=bitmap.width,
            height=bitmap.height,
            row_length=bitmap.stride,
            channels=bitmap.n_channels,
            data=bytes(bitmap.buffer),
        )
    finally:
        for made_bitmap in made:
            made_bitmap.close()


def _bounding_box(page: pypdfium2.PdfPage) -> Box:
    rect = pdfium_c.FS_RECTF()
    if not pdfium_c.FPDF_GetPageBoundingBox(page.raw, rect):
        raise EngineFailure(FailureKind.OTHER, "FPDF_GetPageBoundingBox failed")
    return (float(rect.left), float(rect.bottom), float(rect.right), float(rect.top))
