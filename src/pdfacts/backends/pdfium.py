"""PDFium backend: text, geometry, rendering, annotations and what a page draws.

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
    AppearanceDrawing,
    Box,
    Drawing,
    DrawnImage,
    DrawnPath,
    EngineFailure,
    FailureKind,
    PageText,
    RawBitmap,
    TextCharacter,
    Transform,
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
    """A document opened by PDFium.

    A GeometryReader, a Renderer, an AnnotationViewer, a DrawingReader and a
    TextReader. It holds the native handle; no method returns a pypdfium2
    object. A page is opened for one call and closed before the call returns.
    No form environment is ever made: the document that renders must draw as
    M1 recorded.
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

    def security_handler_revision(self) -> int | None:
        """/R of the security handler; None where PDFium reads the file as not
        encrypted. PDFium merges the trailers of the file's sections, so it
        finds /Encrypt in an older one too (measured)."""
        self._check_open()
        with _translated():
            revision = pdfium_c.FPDF_GetSecurityHandlerRevision(self._document.raw)
        return None if revision == -1 else int(revision)

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
        makes sure. The page's own rotation is applied, and the annotations'
        appearances are drawn as PDFium draws them by default; no form
        environment is set up, so no widget is drawn ("Rendering"). Width and
        height are what pypdfium2 makes of the page size, the scale and the
        crop. The pixels are copied out of PDFium's bitmap before it is freed.

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

    def page_drawing(self, index: int, cap: int | None) -> Drawing:
        """The paths and images of the page's content, forms expanded, in drawing order.

        Reading stops once `cap` objects are read and another follows; a cap
        below 0 is a ValueError.
        """
        _check_cap(cap)
        return self._read_page(index, lambda page: _page_drawing(page, cap))

    def appearance_drawings(
        self, index: int, cap: int | None
    ) -> tuple[AppearanceDrawing | None, ...]:
        """For each entry of the page's /Annots, what PDFium draws as its normal appearance.

        None where PDFium gives no annotation for the entry. `cap` counts the
        objects of all appearances together; reading stops as page_drawing
        stops, and the drawing that stopped is the last one given. PDFium
        parses an appearance when its objects are first asked for. After a
        rendering of the page, PDFium draws appearances it generated for
        annotations that have none (measured, issue G3).
        """
        _check_cap(cap)
        return self._read_page(index, lambda page: _appearance_drawings(page, cap))

    def page_text(self, index: int, cap: int | None) -> PageText:
        """The characters of the page's content, forms expanded, in content order.

        Once `cap` characters are given and another follows, the rest are left
        out; a cap below 0 is a ValueError.
        """
        _check_cap(cap)
        return self._read_page(index, lambda page: _page_text(page, cap))

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


_IDENTITY: Transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# PDFium reads forms 40 levels deep in a page's content and in an appearance;
# a form drawn deeper is an object without children (measured, issue G3).
_FORM_LEVELS = 40


def _check_cap(cap: int | None) -> None:
    if cap is not None and cap < 0:
        raise ValueError(f"a cap is 0 or more, not {cap}")


class _Budget:
    """How many more objects, or characters, may be read."""

    def __init__(self, cap: int | None) -> None:
        self.left = cap
        self.stopped = False

    def take(self) -> bool:
        """False, and stopped, when the cap is used up."""
        if self.left is None:
            return True
        if self.left == 0:
            self.stopped = True
            return False
        self.left -= 1
        return True


def _count(value: int, what: str) -> int:
    """A count PDFium gives; an engine failure where it gives -1 instead."""
    if value < 0:
        raise EngineFailure(FailureKind.OTHER, f"PDFium gives no count of {what}")
    return value


class _Found:
    """The objects of one drawing so far, and whether a form lay too deep."""

    def __init__(self) -> None:
        self.objects: list[DrawnPath | DrawnImage] = []
        self.too_deep = False

    def drawing(self, budget: _Budget) -> Drawing:
        return Drawing(objects=tuple(self.objects), stopped=budget.stopped, too_deep=self.too_deep)


def _page_drawing(page: pypdfium2.PdfPage, cap: int | None) -> Drawing:
    budget = _Budget(cap)
    found = _Found()
    raw = page.raw
    for i in range(_count(pdfium_c.FPDFPage_CountObjects(raw), "the page's objects")):
        if not _walk(pdfium_c.FPDFPage_GetObject(raw, i), _IDENTITY, 0, raw, found, budget):
            break
    return found.drawing(budget)


def _page_text(page: pypdfium2.PdfPage, cap: int | None) -> PageText:
    order, too_deep = _text_objects(page.raw)
    found: list[tuple[int, int]] = []
    textpage = page.get_textpage()
    try:
        raw = textpage.raw
        for i in range(_count(pdfium_c.FPDFText_CountChars(raw), "the page's characters")):
            generated = pdfium_c.FPDFText_IsGenerated(raw, i)
            if generated == -1:
                raise EngineFailure(
                    FailureKind.OTHER, "PDFium does not say whether a character is its own"
                )
            if generated == 1:
                continue
            obj = pdfium_c.FPDFText_GetTextObject(raw, i)
            position = order.get(ctypes.cast(obj, ctypes.c_void_p).value) if obj else None
            if position is None:
                raise EngineFailure(
                    FailureKind.OTHER, "PDFium gives a character of no text object on the page"
                )
            found.append((position, i))
        found.sort()
        budget = _Budget(cap)
        characters: list[TextCharacter] = []
        fonts: dict[int, tuple[float | None, float | None]] = {}
        for _, i in found:
            if not budget.take():
                break
            characters.append(_character(raw, i, fonts))
    finally:
        textpage.close()
    return PageText(characters=tuple(characters), stopped=budget.stopped, too_deep=too_deep)


def _answer(value: int) -> bool | None:
    """1 as True, 0 as False, and -1, PDFium's failure, as None."""
    return {1: True, 0: False}.get(value)


def _character(
    textpage: pdfium_c.FPDF_TEXTPAGE,
    index: int,
    fonts: dict[int, tuple[float | None, float | None]],
) -> TextCharacter:
    x, y = ctypes.c_double(), ctypes.c_double()
    origin = None
    if pdfium_c.FPDFText_GetCharOrigin(textpage, index, ctypes.byref(x), ctypes.byref(y)):
        origin = (x.value, y.value)
    m = pdfium_c.FS_MATRIX()
    matrix = None
    if pdfium_c.FPDFText_GetMatrix(textpage, index, ctypes.byref(m)):
        matrix = (m.a, m.b, m.c, m.d, m.e, m.f)
    rect = pdfium_c.FS_RECTF()
    loose_box = None
    if pdfium_c.FPDFText_GetLooseCharBox(textpage, index, ctypes.byref(rect)):
        loose_box = (rect.left, rect.bottom, rect.right, rect.top)
    obj = pdfium_c.FPDFText_GetTextObject(textpage, index)
    font_size = None
    render_mode = None
    ascent: float | None = None
    descent: float | None = None
    if obj:
        font_size = pdfium_c.FPDFText_GetFontSize(textpage, index)
        mode = pdfium_c.FPDFTextObj_GetTextRenderMode(obj)
        render_mode = mode if mode >= 0 else None
        ascent, descent = _font_metrics(obj, fonts)
    return TextCharacter(
        code=pdfium_c.FPDFText_GetUnicode(textpage, index),
        map_error=_answer(pdfium_c.FPDFText_HasUnicodeMapError(textpage, index)),
        hyphen=_answer(pdfium_c.FPDFText_IsHyphen(textpage, index)),
        origin=origin,
        matrix=matrix,
        font_size=font_size,
        loose_box=loose_box,
        render_mode=render_mode,
        ascent=ascent,
        descent=descent,
    )


def _font_metrics(
    obj: pdfium_c.FPDF_PAGEOBJECT, fonts: dict[int, tuple[float | None, float | None]]
) -> tuple[float | None, float | None]:
    """The ascent and descent of the text object's font, per unit of font size.

    Asked at size 1000, where PDFium's metrics in thousandths come back whole,
    and divided by 1000.
    """
    font = pdfium_c.FPDFTextObj_GetFont(obj)
    if not font:
        return None, None
    key = ctypes.addressof(font.contents)
    if key not in fonts:
        ascent, descent = ctypes.c_float(), ctypes.c_float()
        fonts[key] = (
            ascent.value / 1000
            if pdfium_c.FPDFFont_GetAscent(font, 1000.0, ctypes.byref(ascent))
            else None,
            descent.value / 1000
            if pdfium_c.FPDFFont_GetDescent(font, 1000.0, ctypes.byref(descent))
            else None,
        )
    return fonts[key]


def _text_objects(page: pdfium_c.FPDF_PAGE) -> tuple[dict[int, int], bool]:
    """Each text object of the page's content by its address, numbered in
    content order, and whether a form lies deeper than PDFium reads forms."""
    order: dict[int, int] = {}
    too_deep = False
    count = _count(pdfium_c.FPDFPage_CountObjects(page), "the page's objects")
    pending = [(pdfium_c.FPDFPage_GetObject(page, i), 0) for i in reversed(range(count))]
    while pending:
        obj, level = pending.pop()
        if not obj:
            raise EngineFailure(FailureKind.OTHER, "PDFium gives no object at an index it counts")
        kind = pdfium_c.FPDFPageObj_GetType(obj)
        if kind == pdfium_c.FPDF_PAGEOBJ_TEXT:
            order[ctypes.cast(obj, ctypes.c_void_p).value] = len(order)
        elif kind == pdfium_c.FPDF_PAGEOBJ_FORM:
            if level >= _FORM_LEVELS:
                too_deep = True
                continue
            count = _count(pdfium_c.FPDFFormObj_CountObjects(obj), "a form's objects")
            pending.extend(
                (pdfium_c.FPDFFormObj_GetObject(obj, i), level + 1) for i in reversed(range(count))
            )
    return order, too_deep


def _appearance_drawings(
    page: pypdfium2.PdfPage, cap: int | None
) -> tuple[AppearanceDrawing | None, ...]:
    budget = _Budget(cap)
    drawings: list[AppearanceDrawing | None] = []
    raw = page.raw
    for index in range(pdfium_c.FPDFPage_GetAnnotCount(raw)):
        annot = pdfium_c.FPDFPage_GetAnnot(raw, index)
        if not annot:
            drawings.append(None)
            continue
        try:
            found = _Found()
            count = _count(pdfium_c.FPDFAnnot_GetObjectCount(annot), "an appearance's objects")
            for i in range(count):
                obj = pdfium_c.FPDFAnnot_GetObject(annot, i)
                if not _walk(obj, _IDENTITY, 0, raw, found, budget):
                    break
            drawings.append(
                AppearanceDrawing(
                    index=index,
                    appearance_text=_appearance_text(annot),
                    flags=pdfium_c.FPDFAnnot_GetFlags(annot),
                    drawing=found.drawing(budget),
                )
            )
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)
        if budget.stopped:
            break
    return tuple(drawings)


def _walk(
    obj: pdfium_c.FPDF_PAGEOBJECT,
    outer: Transform | None,
    level: int,
    page: pdfium_c.FPDF_PAGE,
    found: _Found,
    budget: _Budget,
) -> bool:
    """Add the paths and images of `obj`, a form's in order; False once the cap is used up.

    `level` counts the forms that hold `obj`.
    """
    if not obj:
        raise EngineFailure(FailureKind.OTHER, "PDFium gives no object at an index it counts")
    kind = pdfium_c.FPDFPageObj_GetType(obj)
    if kind not in (
        pdfium_c.FPDF_PAGEOBJ_FORM,
        pdfium_c.FPDF_PAGEOBJ_PATH,
        pdfium_c.FPDF_PAGEOBJ_IMAGE,
    ):
        return True
    own = _matrix(obj)
    matrix = None if own is None or outer is None else _then(own, outer)
    if kind == pdfium_c.FPDF_PAGEOBJ_FORM:
        if level >= _FORM_LEVELS:
            found.too_deep = True
            return True
        for i in range(_count(pdfium_c.FPDFFormObj_CountObjects(obj), "a form's objects")):
            child = pdfium_c.FPDFFormObj_GetObject(obj, i)
            if not _walk(child, matrix, level + 1, page, found, budget):
                return False
        return True
    if not budget.take():
        return False
    found.objects.append(
        _path(obj, matrix) if kind == pdfium_c.FPDF_PAGEOBJ_PATH else _image(obj, matrix, page)
    )
    return True


def _matrix(obj: pdfium_c.FPDF_PAGEOBJECT) -> Transform | None:
    m = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)):
        return None
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _then(first: Transform, second: Transform) -> Transform:
    """The transform that applies `first`, then `second`."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a2 * a1 + c2 * b1,
        b2 * a1 + d2 * b1,
        a2 * c1 + c2 * d1,
        b2 * c1 + d2 * d1,
        a2 * e1 + c2 * f1 + e2,
        b2 * e1 + d2 * f1 + f2,
    )


def _path(obj: pdfium_c.FPDF_PAGEOBJECT, matrix: Transform | None) -> DrawnPath:
    fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
    drawn = pdfium_c.FPDFPath_GetDrawMode(obj, ctypes.byref(fill_mode), ctypes.byref(stroke))
    width, phase = ctypes.c_float(), ctypes.c_float()
    has_width = pdfium_c.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width))
    has_phase = pdfium_c.FPDFPageObj_GetDashPhase(obj, ctypes.byref(phase))
    return DrawnPath(
        matrix=matrix,
        segments=_segments(obj),
        fill_mode=fill_mode.value if drawn else None,
        stroke=bool(stroke.value) if drawn else None,
        fill_colour=_colour(pdfium_c.FPDFPageObj_GetFillColor, obj),
        stroke_colour=_colour(pdfium_c.FPDFPageObj_GetStrokeColor, obj),
        width=width.value if has_width else None,
        dash=_dash(obj),
        dash_phase=phase.value if has_phase else None,
        cap=_style(pdfium_c.FPDFPageObj_GetLineCap(obj)),
        join=_style(pdfium_c.FPDFPageObj_GetLineJoin(obj)),
    )


def _style(value: int) -> int | None:
    """A line cap or join; None for PDFium's -1, which it gives when it has none."""
    return value if value >= 0 else None


def _segments(obj: pdfium_c.FPDF_PAGEOBJECT) -> tuple[tuple[int, float, float, bool], ...] | None:
    count = pdfium_c.FPDFPath_CountSegments(obj)
    if count < 0:
        return None
    segments = []
    x, y = ctypes.c_float(), ctypes.c_float()
    for i in range(count):
        segment = pdfium_c.FPDFPath_GetPathSegment(obj, i)
        if not segment or not pdfium_c.FPDFPathSegment_GetPoint(
            segment, ctypes.byref(x), ctypes.byref(y)
        ):
            return None
        segments.append(
            (
                pdfium_c.FPDFPathSegment_GetType(segment),
                x.value,
                y.value,
                bool(pdfium_c.FPDFPathSegment_GetClose(segment)),
            )
        )
    return tuple(segments)


def _dash(obj: pdfium_c.FPDF_PAGEOBJECT) -> tuple[float, ...] | None:
    count = pdfium_c.FPDFPageObj_GetDashCount(obj)
    if count < 0:
        return None
    lengths = (ctypes.c_float * count)()
    if count and not pdfium_c.FPDFPageObj_GetDashArray(obj, lengths, count):
        return None
    return tuple(lengths)


def _colour(
    get: Callable[..., int], obj: pdfium_c.FPDF_PAGEOBJECT
) -> tuple[int, int, int, int] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not get(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)):
        return None
    return (r.value, g.value, b.value, a.value)


def _image(
    obj: pdfium_c.FPDF_PAGEOBJECT, matrix: Transform | None, page: pdfium_c.FPDF_PAGE
) -> DrawnImage:
    meta = pdfium_c.FPDF_IMAGEOBJ_METADATA()
    if not pdfium_c.FPDFImageObj_GetImageMetadata(obj, page, ctypes.byref(meta)):
        return DrawnImage(
            matrix=matrix, width=None, height=None, colour_space=None, bits_per_pixel=None
        )
    return DrawnImage(
        matrix=matrix,
        width=meta.width,
        height=meta.height,
        colour_space=meta.colorspace,
        bits_per_pixel=meta.bits_per_pixel,
    )


def _appearance_text(annot: pdfium_c.FPDF_ANNOTATION) -> str:
    """FPDFAnnot_GetAP, normal mode, as text; "" where PDFium gives none."""
    mode = pdfium_c.FPDF_ANNOT_APPEARANCEMODE_NORMAL
    needed = pdfium_c.FPDFAnnot_GetAP(annot, mode, None, 0)
    if needed <= 2:
        return ""
    buffer = ctypes.create_string_buffer(needed)
    pdfium_c.FPDFAnnot_GetAP(
        annot, mode, ctypes.cast(buffer, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), needed
    )
    return buffer.raw[:-2].decode("utf-16-le", "surrogatepass")


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
