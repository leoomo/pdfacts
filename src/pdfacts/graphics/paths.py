"""Path: the paths a page draws, and the shape of each subpath.

The shape rules are those of docs/conventions.md, "Paths and images". They work
on segments in user space, as PDFium gives them: PDFium turns `re`,
`m l l l h` and `m l l l l h` into the same five points, the last segment
closed, and cannot tell them apart; so neither can these rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pdfacts.core.geometry import Point, Quad, Rect, _finite_float
from pdfacts.core.refs import GraphicRef, _is_int
from pdfacts.core.values import Unknown

__all__ = [
    "Dash",
    "DisplayColour",
    "FillRule",
    "LineCap",
    "LineJoin",
    "Path",
    "Segment",
    "SegmentKind",
    "ShapeKind",
    "Subpath",
]


class SegmentKind(StrEnum):
    MOVE = "move"
    LINE = "line"
    CURVE = "curve"


@dataclass(frozen=True, slots=True)
class Segment:
    """One segment of a subpath, its points in user space.

    A move and a line have one point, the one they go to; a curve has three,
    its two control points and its end. `closes` is set on the segment that
    closes its subpath, the last one.
    """

    kind: SegmentKind
    points: tuple[Point, ...]
    closes: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SegmentKind):
            raise TypeError(f"kind must be a SegmentKind, not {type(self.kind).__name__}")
        if type(self.points) is not tuple or not all(isinstance(p, Point) for p in self.points):
            raise TypeError("points must be a tuple of Point")
        wanted = 3 if self.kind is SegmentKind.CURVE else 1
        if len(self.points) != wanted:
            raise ValueError(f"a {self.kind} segment has {wanted} point(s), not {len(self.points)}")
        if type(self.closes) is not bool:
            raise TypeError(f"closes must be a bool, not {type(self.closes).__name__}")

    @property
    def end(self) -> Point:
        return self.points[-1]


class ShapeKind(StrEnum):
    """What a subpath is under the rules of docs/conventions.md, "Paths and images"."""

    LINE = "line"
    RECTANGLE = "rectangle"
    QUAD = "quad"
    POLYLINE = "polyline"
    CURVES = "curves"


@dataclass(frozen=True, slots=True)
class Subpath:
    """A move, the segments that follow it, and the shape they make.

    Only the last segment may close the subpath. The shape follows from the
    segments; `rect` is set for a rectangle and `quad` for a quad, both are
    None otherwise.
    """

    segments: tuple[Segment, ...]
    shape: ShapeKind = field(init=False)
    rect: Rect | None = field(init=False)
    quad: Quad | None = field(init=False)

    def __post_init__(self) -> None:
        if type(self.segments) is not tuple or not self.segments:
            raise TypeError("segments must be a tuple of at least one Segment")
        if not all(isinstance(s, Segment) for s in self.segments):
            raise TypeError("segments must be a tuple of Segment")
        if self.segments[0].kind is not SegmentKind.MOVE:
            raise ValueError("a subpath starts with a move")
        if any(s.kind is SegmentKind.MOVE for s in self.segments[1:]):
            raise ValueError("a subpath holds one move, its first segment")
        if any(s.closes for s in self.segments[:-1]):
            raise ValueError("only the last segment of a subpath closes it")
        shape, rect, quad = _shape(self.segments)
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "rect", rect)
        object.__setattr__(self, "quad", quad)

    @property
    def closed(self) -> bool:
        return self.segments[-1].closes


def _shape(segments: tuple[Segment, ...]) -> tuple[ShapeKind, Rect | None, Quad | None]:
    """The first rule that applies:

    - with a curve among its segments: curves;
    - a move and one line, not closed: a line;
    - a move and four lines whose last ends where the move is, closed or not:
      a rectangle when its edges alternate between keeping y and keeping x,
      else a quad; its corners are the move and the ends of the first three
      lines;
    - anything else: a polyline.
    """
    kinds = [s.kind for s in segments]
    if SegmentKind.CURVE in kinds:
        return ShapeKind.CURVES, None, None
    if kinds == [SegmentKind.MOVE, SegmentKind.LINE] and not segments[-1].closes:
        return ShapeKind.LINE, None, None
    if len(segments) == 5 and _same(segments[4].end, segments[0].end):
        corners = [s.end for s in segments[:4]]
        edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        keeps_y = [_near(p.y, q.y) for p, q in edges]
        keeps_x = [_near(p.x, q.x) for p, q in edges]
        first_y = keeps_y[0] and keeps_x[1] and keeps_y[2] and keeps_x[3]
        first_x = keeps_x[0] and keeps_y[1] and keeps_x[2] and keeps_y[3]
        if first_y or first_x:
            xs = [p.x for p in corners]
            ys = [p.y for p in corners]
            return ShapeKind.RECTANGLE, Rect(min(xs), min(ys), max(xs), max(ys)), None
        return ShapeKind.QUAD, None, Quad(*corners)
    return ShapeKind.POLYLINE, None, None


def _same(p: Point, q: Point) -> bool:
    return _near(p.x, q.x) and _near(p.y, q.y)


def _near(a: float, b: float) -> bool:
    """Within 0.001 plus a millionth of the larger magnitude of the two.

    PDFium keeps coordinates as 32-bit floats: near 14400 units they lie about
    0.001 apart, anywhere at most 2^-23 of the magnitude.
    """
    return abs(a - b) <= 0.001 + 1e-6 * max(abs(a), abs(b))


class FillRule(StrEnum):
    NONZERO = "nonzero"
    EVEN_ODD = "even-odd"


class LineCap(StrEnum):
    BUTT = "butt"
    ROUND = "round"
    SQUARE = "square"


class LineJoin(StrEnum):
    MITER = "miter"
    ROUND = "round"
    BEVEL = "bevel"


@dataclass(frozen=True, slots=True)
class DisplayColour:
    """A colour as PDFium converts it for display: 8-bit red, green, blue and alpha.

    Not the colour space and components the file writes; PDFium clamps what
    lies outside the range.
    """

    red: int
    green: int
    blue: int
    alpha: int

    def __post_init__(self) -> None:
        for name in ("red", "green", "blue", "alpha"):
            value = getattr(self, name)
            if not _is_int(value):
                raise TypeError(f"{name} must be an int, not {type(value).__name__}")
            if not 0 <= value <= 255:
                raise ValueError(f"{name} is from 0 to 255, not {value}")


@dataclass(frozen=True, slots=True)
class Dash:
    """A dash pattern in user space: the lengths of dashes and gaps, and the phase.

    Lengths of 0 or more, at least one of them above 0 (ISO 32000-1, 8.4.3.6);
    a solid line has no Dash.
    """

    lengths: tuple[float, ...]
    phase: float

    def __post_init__(self) -> None:
        if type(self.lengths) is not tuple:
            raise TypeError(f"lengths must be a tuple, not {type(self.lengths).__name__}")
        lengths = tuple(_finite_float(v, "a dash length") for v in self.lengths)
        if any(v < 0 for v in lengths):
            raise ValueError(f"a dash length is from 0, not {min(lengths)}")
        if not any(lengths):
            raise ValueError("a dash has a length above 0")
        object.__setattr__(self, "lengths", lengths)
        object.__setattr__(self, "phase", _finite_float(self.phase, "phase"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Path:
    """A path the page draws, in user space.

    `fill` is None when the path is not filled, and so is `fill_colour`, then
    only. The stroke's colour, width, cap and join are None when it is not
    stroked, then only; `dash` is None then and for a solid line.
    `annotation_flags` is the /F of the annotation whose appearance draws the
    path, None for the page's content.
    """

    ref: GraphicRef
    subpaths: tuple[Subpath, ...] | Unknown
    fill: FillRule | None | Unknown
    stroke: bool | Unknown
    fill_colour: DisplayColour | None | Unknown
    stroke_colour: DisplayColour | None | Unknown
    line_width: float | None | Unknown
    dash: Dash | None | Unknown
    line_cap: LineCap | None | Unknown
    line_join: LineJoin | None | Unknown
    annotation_flags: int | None | Unknown

    def __post_init__(self) -> None:
        if not isinstance(self.ref, GraphicRef):
            raise TypeError(f"ref must be a GraphicRef, not {type(self.ref).__name__}")
        if not isinstance(self.subpaths, Unknown):
            if type(self.subpaths) is not tuple or not all(
                isinstance(s, Subpath) for s in self.subpaths
            ):
                raise TypeError("subpaths must be a tuple of Subpath or Unknown")
        _check(self.fill, (FillRule,), "fill", none=True)
        _check(self.stroke, (bool,), "stroke", none=False)
        _check(self.fill_colour, (DisplayColour,), "fill_colour", none=True)
        _check(self.stroke_colour, (DisplayColour,), "stroke_colour", none=True)
        if self.line_width is not None and not isinstance(self.line_width, Unknown):
            object.__setattr__(self, "line_width", _finite_float(self.line_width, "line_width"))
            if self.line_width < 0:
                raise ValueError(f"line_width is from 0, not {self.line_width}")
        _check(self.dash, (Dash,), "dash", none=True)
        _check(self.line_cap, (LineCap,), "line_cap", none=True)
        _check(self.line_join, (LineJoin,), "line_join", none=True)
        _check_flags(self.annotation_flags, self.ref)
        if (self.fill is None) != (self.fill_colour is None):
            raise ValueError("fill_colour is None when the path is not filled, then only")
        stroked = [self.stroke_colour, self.line_width, self.line_cap, self.line_join]
        if self.stroke is False and any(v is not None for v in (*stroked, self.dash)):
            raise ValueError("a path that is not stroked has no values of a stroke")
        if self.stroke is not False and any(v is None for v in stroked):
            raise ValueError("a stroked path has its stroke colour, width, cap and join")


def _check(value: object, kinds: tuple[type, ...], name: str, *, none: bool) -> None:
    if isinstance(value, Unknown) or (none and value is None) or isinstance(value, kinds):
        return
    allowed = [k.__name__ for k in kinds] + (["None"] if none else []) + ["Unknown"]
    raise TypeError(f"{name} must be {', '.join(allowed)}, not {type(value).__name__}")


def _check_flags(value: object, ref: GraphicRef) -> None:
    """The /F of the annotation `ref` names, None for the page's content."""
    if value is None or isinstance(value, Unknown):
        pass
    elif not _is_int(value):
        raise TypeError(
            f"annotation_flags must be an int, None or Unknown, not {type(value).__name__}"
        )
    elif not 0 <= value <= 2**32 - 1:
        raise ValueError(f"annotation_flags is from 0 to 2^32 - 1, not {value}")
    if (value is None) != (ref.annotation is None):
        raise ValueError("annotation_flags is None for the page's content, then only")
