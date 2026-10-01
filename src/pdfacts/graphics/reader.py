"""The graphics reader: the paths and images a page draws (docs/conventions.md, "Paths and images").

Every value comes from PDFium, which interprets the content. QPDF decides
which annotation appearance PDFium would draw, gives what PDFium does not
(its /Rect, /BBox and /Matrix, and whether a pattern colour is set), and the
two are checked against each other before an appearance's objects are used.
"""

from __future__ import annotations

import math

from pdfacts.backends.protocols import (
    AnnotationAppearance,
    AppearanceDrawing,
    DrawnImage,
    DrawnPath,
    SelectedAppearance,
    Transform,
    Written,
)
from pdfacts.core.document import Document
from pdfacts.core.errors import LimitExceededError
from pdfacts.core.geometry import Matrix, Point, Quad, Rect
from pdfacts.core.refs import AnnotationRef, GraphicRef
from pdfacts.core.strings import decode_name
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.graphics.images import ColourSpace, Image
from pdfacts.graphics.paths import (
    Dash,
    DisplayColour,
    FillRule,
    LineCap,
    LineJoin,
    Path,
    Segment,
    SegmentKind,
    Subpath,
)

__all__ = ["images", "paths"]

_INVALID = UnknownReason.INVALID
_UNREADABLE = UnknownReason.UNREADABLE
_DISAGREE = UnknownReason.ENGINES_DISAGREE

# PDFium's FPDF_* codes, named here so the reader imports no engine.
_SEGMENT_MOVE = 2
_SEGMENT_LINE = 0
_SEGMENT_CURVE = 1
_FILL = {0: None, 1: FillRule.EVEN_ODD, 2: FillRule.NONZERO}
_CAP = {0: LineCap.BUTT, 1: LineCap.ROUND, 2: LineCap.SQUARE}
_JOIN = {0: LineJoin.MITER, 1: LineJoin.ROUND, 2: LineJoin.BEVEL}
_COLOUR_SPACES = {
    1: ColourSpace.DEVICE_GRAY,
    2: ColourSpace.DEVICE_RGB,
    3: ColourSpace.DEVICE_CMYK,
    4: ColourSpace.CAL_GRAY,
    5: ColourSpace.CAL_RGB,
    6: ColourSpace.LAB,
    7: ColourSpace.ICC_BASED,
    8: ColourSpace.SEPARATION,
    9: ColourSpace.DEVICE_N,
    10: ColourSpace.INDEXED,
}
# FPDF_COLORSPACE_PATTERN: never an image's (ISO 32000-1, 8.9.5.1, Table 89).
_PATTERN_SPACE = 11
# PDFDocEncoding as FPDFAnnot_GetAP maps each byte of an appearance (measured,
# issue G3): undefined bytes become U+0000.
_PDFIUM_TEXT = [chr(code) for code in range(256)]
_PDFIUM_TEXT[0x18:0x20] = "˘ˇˆ˙˝˛˚˜"
_PDFIUM_TEXT[0x80:0x9F] = "•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž"
_PDFIUM_TEXT[0xA0] = "€"
for _code in (0x7F, 0x9F, 0xAD):
    _PDFIUM_TEXT[_code] = "\x00"


def paths(document: Document, page_index: int) -> tuple[Path, ...] | Unknown:
    """The paths the page draws, in drawing order; or why they are unknown."""
    read = _read(document, page_index)
    return read if isinstance(read, Unknown) else read[0]


def images(document: Document, page_index: int) -> tuple[Image, ...] | Unknown:
    """The images the page places, in drawing order; or why they are unknown."""
    read = _read(document, page_index)
    return read if isinstance(read, Unknown) else read[1]


def _read(
    document: Document, page_index: int
) -> tuple[tuple[Path, ...], tuple[Image, ...]] | Unknown:
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    page = document.pages[page_index]  # the index checks of Document._page
    with document._access() as access:
        if access.qpdf_unknown is not None:
            return access.qpdf_unknown
        if isinstance(page.ref.object_number, Unknown):
            return page.ref.object_number
        count = access.annotation_count(page_index)
        if count is None:
            return Unknown(_INVALID, "/Annots is not an array")
        theirs = access.pdfium_annotation_count(page_index)
        cap = access.max_items_per_page
        if cap is not None and max(count, theirs) > cap:
            raise LimitExceededError(
                limit="max_items_per_page", allowed=cap, found=max(count, theirs)
            )
        if count != theirs:
            return Unknown(
                _DISAGREE, f"QPDF finds {count} annotations on the page, PDFium {theirs}"
            )
        content = access.page_drawing(page_index, cap)
        if content.stopped:
            raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
        left = None if cap is None else cap - len(content.objects)
        drawings = access.appearance_drawings(page_index, left)
        if any(d is not None and d.drawing.stopped for d in drawings):
            raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
        appearances = access.appearances(page_index)
        pattern = access.page_uses_pattern(page_index)
    if content.too_deep:
        return Unknown(_UNREADABLE, _TOO_DEEP)
    out = _Out(page_index)
    colour = _colour_known(pattern)
    for obj in content.objects:
        out.add(obj, obj.matrix, None, None, colour)
    for entry in appearances:
        drawing = (
            drawings[entry.annotation.index] if entry.annotation.index < len(drawings) else None
        )
        page_unknown = _annotation(entry, drawing, out)
        if page_unknown is not None:
            return page_unknown
    return tuple(out.paths), tuple(out.images)


class _Out:
    """The facts made so far, and the next drawing index."""

    def __init__(self, page_index: int) -> None:
        self.page_index = page_index
        self.paths: list[Path] = []
        self.images: list[Image] = []

    def ref(self, annotation: AnnotationRef | None) -> GraphicRef:
        index = len(self.paths) + len(self.images)
        return GraphicRef(page_index=self.page_index, annotation=annotation, drawing_index=index)

    def add(
        self,
        obj: DrawnPath | DrawnImage,
        matrix: Transform | None,
        annotation: AnnotationRef | None,
        flags: int | None | Unknown,
        colour: Unknown | None,
    ) -> None:
        ref = self.ref(annotation)
        if isinstance(obj, DrawnPath):
            self.paths.append(_path(obj, matrix, ref, flags, colour))
        else:
            self.images.append(_image(obj, matrix, ref, flags))

    def unknown(self, annotation: AnnotationRef, why: Unknown) -> None:
        """One path and one image standing for an annotation whose graphics are unknown.

        Every value is unknown with the reason, the flags included: where the
        engines disagree, they may disagree on the flags.
        """
        self.paths.append(
            Path(
                ref=self.ref(annotation),
                subpaths=why,
                fill=why,
                stroke=why,
                fill_colour=why,
                stroke_colour=why,
                line_width=why,
                dash=why,
                line_cap=why,
                line_join=why,
                annotation_flags=why,
            )
        )
        self.images.append(
            Image(
                ref=self.ref(annotation),
                corners=why,
                pixel_width=why,
                pixel_height=why,
                colour_space=why,
                bits_per_pixel=why,
                annotation_flags=why,
            )
        )


def _annotation(
    entry: AnnotationAppearance, drawing: AppearanceDrawing | None, out: _Out
) -> Unknown | None:
    """The graphics of one annotation's appearance, checked and placed on the page.

    None, or why none of the page's graphics are known: PDFium draws an
    annotation that has no /Subtype to name it by in a reference.
    """
    selected = entry.appearance
    drawn = drawing is not None and (
        bool(drawing.drawing.objects) or drawing.appearance_text != "" or drawing.drawing.too_deep
    )
    if not entry.annotation.subtype:
        if drawn:
            return Unknown(
                _INVALID,
                f"PDFium draws annotation {entry.annotation.index} of the page,"
                " which has no /Subtype to name it by",
            )
        return None
    annotation = AnnotationRef(
        page_index=out.page_index,
        annotation_index=entry.annotation.index,
        kind=decode_name(entry.annotation.subtype).text,
        object_number=entry.annotation.object_number,
        generation=entry.annotation.generation,
    )
    flags = _flags(entry.flags)
    if entry.undecided is not None:
        out.unknown(annotation, Unknown(_UNREADABLE, entry.undecided))
        return None
    if selected is None:
        # QPDF selects no appearance: accepted only where PDFium draws none.
        if drawn:
            out.unknown(
                annotation, Unknown(_DISAGREE, "PDFium draws an appearance where QPDF selects none")
            )
        return None
    why = _check(entry, selected, drawing)
    if why is None and drawing is not None and drawing.drawing.too_deep:
        why = Unknown(_UNREADABLE, _TOO_DEEP)
    placement = why if why is not None else _placement(entry, selected)
    if isinstance(placement, Unknown) or drawing is None:
        out.unknown(annotation, placement if isinstance(placement, Unknown) else _GONE)
        return None
    colour = _colour_known(selected.uses_pattern)
    for obj in drawing.drawing.objects:
        matrix = None if obj.matrix is None else _then(obj.matrix, placement)
        out.add(obj, matrix, annotation, flags, colour)
    return None


def _check(
    entry: AnnotationAppearance, selected: SelectedAppearance, drawing: AppearanceDrawing | None
) -> Unknown | None:
    """Whether PDFium draws the appearance QPDF selects; the reason where it does not."""
    if drawing is None:
        return Unknown(_DISAGREE, "PDFium gives no annotation at this index")
    if selected.data is None:
        return Unknown(_UNREADABLE, "QPDF cannot decode the appearance stream")
    if drawing.appearance_text != "".join(_PDFIUM_TEXT[byte] for byte in selected.data):
        return Unknown(_DISAGREE, "PDFium draws another appearance for this annotation")
    written = entry.flags
    if written is None or written.kind == "integer":
        ours = 0 if written is None else written.data % 2**32
        if ours != drawing.flags % 2**32:
            return Unknown(_DISAGREE, "PDFium reads other annotation flags")
    return None


def _flags(written: Written | None) -> int | Unknown:
    if written is None:
        return 0
    if written.kind == "integer" and 0 <= written.data <= 2**32 - 1:
        return written.data
    return Unknown(_INVALID, "the /F of the annotation is not a 32-bit integer")


def _placement(entry: AnnotationAppearance, selected: SelectedAppearance) -> Transform | Unknown:
    """The appearance's space onto the page (ISO 32000-1, 12.5.5).

    Its /Matrix, then its /BBox, transformed, scaled and moved onto /Rect.
    """
    rect = _rect(entry.rect)
    if rect is None:
        return Unknown(_INVALID, "the /Rect of the annotation is not four numbers")
    bbox = _rect(selected.bbox)
    if bbox is None:
        return Unknown(_INVALID, "the /BBox of the appearance is not four numbers")
    if selected.matrix is None:
        matrix = Matrix(1, 0, 0, 1, 0, 0)
    else:
        numbers = _numbers(selected.matrix, 6)
        if numbers is None:
            return Unknown(_INVALID, "the /Matrix of the appearance is not six numbers")
        matrix = Matrix(*numbers)
    try:
        box = matrix.apply_rect(bbox)
        if box.width == 0 or box.height == 0:
            return Unknown(_INVALID, "the box of the appearance has no area")
        sx, sy = rect.width / box.width, rect.height / box.height
        onto = matrix.then(Matrix(sx, 0, 0, sy, rect.x0 - box.x0 * sx, rect.y0 - box.y0 * sy))
    except ValueError:
        # Matrix and Rect refuse what is not finite.
        return Unknown(_INVALID, "the placement of the appearance is not finite in user space")
    return (onto.a, onto.b, onto.c, onto.d, onto.e, onto.f)


def _rect(written: Written | None) -> Rect | None:
    numbers = _numbers(written, 4)
    if numbers is None:
        return None
    try:
        return Rect(*numbers)
    except ValueError:
        return None


def _numbers(written: Written | None, count: int) -> list[float] | None:
    if written is None or written.kind != "array" or len(written.data) != count:
        return None
    if not all(item.kind in ("integer", "real") for item in written.data):
        return None
    return [float(item.data) for item in written.data]


def _colour_known(uses_pattern: bool | None) -> Unknown | None:
    """Why the colours are unknown where a pattern colour is set; None where they are known."""
    if uses_pattern is None:
        return Unknown(
            _UNREADABLE,
            "QPDF cannot read the content, or warns that it misread an object of the file,"
            " to see whether a pattern colour is set",
        )
    if uses_pattern:
        return Unknown(
            _UNREADABLE,
            "a pattern colour or colour space is set here, and PDFium reports a colour of its own",
        )
    return None


def _then(first: Transform, second: Transform) -> Transform:
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


def _point(matrix: Transform, x: float, y: float) -> Point:
    a, b, c, d, e, f = matrix
    return Point(a * x + c * y + e, b * x + d * y + f)


def _subpaths(obj: DrawnPath, matrix: Transform | None) -> tuple[Subpath, ...] | Unknown:
    if matrix is None:
        return _NO_MATRIX
    points = obj.segments
    if points is None:
        return Unknown(_UNREADABLE, "PDFium gives no segment or point at an index it counts")
    groups: list[list[Segment]] = []
    i = 0
    try:
        while i < len(points):
            kind, x, y, closes = points[i]
            if kind == _SEGMENT_MOVE:
                groups.append([Segment(SegmentKind.MOVE, (_point(matrix, x, y),), closes)])
                i += 1
                continue
            if not groups:
                return Unknown(_UNREADABLE, "PDFium gives a path that does not begin with a move")
            if groups[-1][-1].closes:
                # h ended the subpath; the next begins where it began (ISO
                # 32000-1, 8.5.2.1), and PDFium gives no move for it.
                groups.append([Segment(SegmentKind.MOVE, (groups[-1][0].end,))])
            if kind == _SEGMENT_LINE:
                groups[-1].append(Segment(SegmentKind.LINE, (_point(matrix, x, y),), closes))
                i += 1
            elif (
                kind == _SEGMENT_CURVE
                and i + 2 < len(points)
                and all(points[j][0] == _SEGMENT_CURVE for j in (i + 1, i + 2))
            ):
                three = tuple(_point(matrix, px, py) for _, px, py, _ in points[i : i + 3])
                groups[-1].append(Segment(SegmentKind.CURVE, three, points[i + 2][3]))
                i += 3
            else:
                return Unknown(_UNREADABLE, "PDFium gives a segment pdfacts cannot read")
    except ValueError:
        return Unknown(_INVALID, "a point of the path is not finite in user space")
    return tuple(Subpath(tuple(group)) for group in groups)


def _scale(matrix: Transform) -> float | None:
    """How the transform scales a length, where it scales every direction alike.

    Alike within a thousandth: PDFium keeps 32-bit floats, and an appearance's
    box and /Rect rarely have exactly one ratio (measured over the corpus,
    issue G5: of 7727 appearance streams, 2503 are scaled evenly, 4949 within
    a thousandth, 275 by more). The scale is then the geometric mean of the
    two, within half a thousandth of either.
    """
    a, b, c, d, _, _ = matrix
    x, y = math.hypot(a, b), math.hypot(c, d)
    larger = max(x, y)
    if abs(x - y) > 1e-3 * larger or abs(a * c + b * d) > 1e-3 * larger * larger:
        return None
    return math.sqrt(x * y)


def _path(
    obj: DrawnPath,
    matrix: Transform | None,
    ref: GraphicRef,
    flags: int | None | Unknown,
    colour: Unknown | None,
) -> Path:
    fill: FillRule | None | Unknown
    stroke: bool | Unknown
    if obj.fill_mode is None or obj.stroke is None:
        fill = stroke = Unknown(_UNREADABLE, "PDFium gives no draw mode for the path")
    else:
        fill = _FILL.get(obj.fill_mode, Unknown(_UNREADABLE, "PDFium gives an unknown fill mode"))
        stroke = obj.stroke
    if stroke is False:
        width, dash, cap, join = None, None, None, None
        stroke_colour: DisplayColour | None | Unknown = None
    elif isinstance(stroke, Unknown):
        width = dash = cap = join = stroke_colour = stroke
    else:
        scale: float | None | Unknown = _NO_MATRIX if matrix is None else _scale(matrix)
        width = _width(obj.width, scale)
        dash = _dash(obj, scale)
        cap = _CAP.get(obj.cap, Unknown(_UNREADABLE, "PDFium gives no line cap"))
        join = _JOIN.get(obj.join, Unknown(_UNREADABLE, "PDFium gives no line join"))
        stroke_colour = _display(obj.stroke_colour, colour)
    if fill is None:
        fill_colour: DisplayColour | None | Unknown = None
    elif isinstance(fill, Unknown):
        fill_colour = fill
    else:
        fill_colour = _display(obj.fill_colour, colour)
    return Path(
        ref=ref,
        subpaths=_subpaths(obj, matrix),
        fill=fill,
        stroke=stroke,
        fill_colour=fill_colour,
        stroke_colour=stroke_colour,
        line_width=width,
        dash=dash,
        line_cap=cap,
        line_join=join,
        annotation_flags=flags,
    )


_TOO_DEEP = "a form is drawn deeper than PDFium reads forms, 40 levels"
_GONE = Unknown(_DISAGREE, "PDFium gives no annotation at this index")
_NO_MATRIX = Unknown(_UNREADABLE, "PDFium gives no matrix for the object or a form that holds it")
_UNEVEN = Unknown(_UNREADABLE, "the transform scales the line width unevenly")
_TOO_LARGE = Unknown(_INVALID, "a length of the line is not finite in user space")


def _width(value: float | None, scale: float | None | Unknown) -> float | Unknown:
    if value is None:
        return Unknown(_UNREADABLE, "PDFium gives no line width")
    if value < 0:
        return Unknown(_INVALID, "the file writes a negative line width")
    return _length(value, scale)


def _dash(obj: DrawnPath, scale: float | None | Unknown) -> Dash | None | Unknown:
    if obj.dash is None:
        return Unknown(_UNREADABLE, "PDFium gives no dash array")
    if not obj.dash:
        return None
    if obj.dash_phase is None:
        return Unknown(_UNREADABLE, "PDFium gives no dash phase")
    if any(v < 0 for v in obj.dash):
        return Unknown(_INVALID, "the file writes a negative dash length")
    if not any(obj.dash):
        return Unknown(_INVALID, "the file writes a dash whose lengths are all 0")
    lengths = [_length(v, scale) for v in obj.dash]
    phase = _length(obj.dash_phase, scale)
    for value in (*lengths, phase):
        if isinstance(value, Unknown):
            return value
    if not any(lengths):
        return Unknown(_UNREADABLE, "the transform makes every length of the dash 0")
    return Dash(tuple(lengths), phase)  # type: ignore[arg-type]


def _length(value: float, scale: float | None | Unknown) -> float | Unknown:
    """A length of the line in user space."""
    if isinstance(scale, Unknown):
        return scale
    if scale is None:
        return _UNEVEN
    length = value * scale
    return length if math.isfinite(length) else _TOO_LARGE


def _display(
    value: tuple[int, int, int, int] | None, colour: Unknown | None
) -> DisplayColour | Unknown:
    if colour is not None:
        return colour
    if value is None:
        return Unknown(_UNREADABLE, "PDFium gives no colour")
    return DisplayColour(*value)


def _image(
    obj: DrawnImage, matrix: Transform | None, ref: GraphicRef, flags: int | None | Unknown
) -> Image:
    corners: Quad | Unknown
    if matrix is None:
        corners = _NO_MATRIX
    else:
        try:
            corners = Quad(
                _point(matrix, 0, 0),
                _point(matrix, 1, 0),
                _point(matrix, 1, 1),
                _point(matrix, 0, 1),
            )
        except ValueError:
            corners = Unknown(_INVALID, "a corner of the image is not finite in user space")
    if obj.width is None or obj.height is None or obj.bits_per_pixel is None:
        none = Unknown(_UNREADABLE, "PDFium gives no metadata for the image")
        return Image(
            ref=ref,
            corners=corners,
            pixel_width=none,
            pixel_height=none,
            colour_space=none,
            bits_per_pixel=none,
            annotation_flags=flags,
        )
    decoded: ColourSpace | None | Unknown
    bits: int | Unknown
    if obj.bits_per_pixel == 0:
        # Measured (issue G4): what PDFium cannot decode it reports with no
        # colour space and 0 bits per pixel.
        decoded = bits = Unknown(_UNREADABLE, "PDFium cannot decode the image")
    else:
        decoded, bits = _colour_space(obj.colour_space), obj.bits_per_pixel
    return Image(
        ref=ref,
        corners=corners,
        pixel_width=_pixels(obj.width, "width"),
        pixel_height=_pixels(obj.height, "height"),
        colour_space=decoded,
        bits_per_pixel=bits,
        annotation_flags=flags,
    )


def _pixels(value: int, name: str) -> int | Unknown:
    if 1 <= value <= 2**31 - 1:
        return value
    return Unknown(_INVALID, f"the image's {name} is not a whole number of pixels from 1")


def _colour_space(code: int | None) -> ColourSpace | None | Unknown:
    if code == 0:
        return None
    if code in _COLOUR_SPACES:
        return _COLOUR_SPACES[code]
    if code == _PATTERN_SPACE:
        return Unknown(_INVALID, "the image's colour space is a pattern")
    return Unknown(_UNREADABLE, f"PDFium gives a colour space pdfacts does not know: {code}")
