"""Glyphs: what one place of a page's text draws, made from the characters PDFium reads.

docs/conventions.md, "Glyphs, words and lines", states the rules in prose;
each is pinned by a test on constructed values. Every coordinate is in user
space.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pdfacts.backends.protocols import TextCharacter
from pdfacts.core.geometry import Point, Quad, _finite_float
from pdfacts.core.made import made, point, quad
from pdfacts.core.refs import GlyphRef, _is_int
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.text.angles import degrees_of

# The baseline of an upright glyph runs left to right as the page is shown,
# within this many degrees.
UPRIGHT_DEGREES = 1.0

_UNREADABLE = UnknownReason.UNREADABLE


def _known(value: object, kinds: tuple[type, ...], name: str) -> None:
    if isinstance(value, Unknown):
        return
    if not isinstance(value, kinds):
        allowed = " or ".join(kind.__name__ for kind in kinds)
        raise TypeError(f"{name} must be {allowed} or Unknown, not {type(value).__name__}")


def _text(value: object, name: str) -> None:
    _known(value, (str,), name)
    if isinstance(value, str) and not value:
        raise ValueError(f"{name} is not empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class Glyph:
    """What one place of a page's text draws: a character PDFium reads from the
    content, or several it gives at one origin, a ligature it expanded.

    `text` is the characters as PDFium maps the codes, U+0002 standing for a
    hyphen at the end of a line; unknown where PDFium finds no character for a
    code. `direction` is the angle of the baseline, in degrees counterclockwise
    from the x axis, from 0 up to 360. `size` is the font size across the
    baseline. `box` is the advance box, its corners in the order: the start of
    the baseline at the descent, its end at the descent, its end at the
    ascent, its start at the ascent; it is known only where the origin, the
    direction and the size are. `upright` is true where the baseline runs left
    to right as the page is shown, within one degree; it is known only where
    the direction is. `render_mode` is 0 to 7 as Tr sets it; 3 and 7 paint
    nothing.
    """

    ref: GlyphRef
    text: str | Unknown
    origin: Point | Unknown
    direction: float | Unknown
    size: float | Unknown
    box: Quad | Unknown
    upright: bool | Unknown
    render_mode: int | Unknown
    hyphen: bool | Unknown

    def __post_init__(self) -> None:
        if not isinstance(self.ref, GlyphRef):
            raise TypeError(f"ref must be a GlyphRef, not {type(self.ref).__name__}")
        _text(self.text, "text")
        _known(self.origin, (Point,), "origin")
        _known(self.box, (Quad,), "box")
        _known(self.upright, (bool,), "upright")
        _known(self.hyphen, (bool,), "hyphen")
        for name in ("direction", "size"):
            value = getattr(self, name)
            if not isinstance(value, Unknown):
                object.__setattr__(self, name, _finite_float(value, name))
        if not isinstance(self.direction, Unknown) and not 0 <= self.direction < 360:
            raise ValueError(f"direction is from 0 up to 360, not {self.direction}")
        if not isinstance(self.size, Unknown) and self.size <= 0:
            raise ValueError(f"size is above 0, not {self.size}")
        if not isinstance(self.render_mode, Unknown):
            if not _is_int(self.render_mode):
                raise TypeError(
                    f"render_mode must be an int or Unknown, not {type(self.render_mode).__name__}"
                )
            if not 0 <= self.render_mode <= 7:
                raise ValueError(f"render_mode is 0 to 7, not {self.render_mode}")
        if isinstance(self.direction, Unknown) and not isinstance(self.upright, Unknown):
            raise ValueError("upright follows from the direction, which is unknown")
        if not isinstance(self.box, Unknown) and any(
            isinstance(v, Unknown) for v in (self.origin, self.direction, self.size)
        ):
            raise ValueError("the box follows from the origin, the direction and the size")


def glyphs_of(
    page_index: int, rotation: int, characters: Sequence[TextCharacter]
) -> tuple[Glyph, ...]:
    """The page's glyphs from its characters in content order.

    Characters one after another at one origin are one glyph, unless one of
    them is white space: a glyph of white space stands alone. `rotation` is
    the page's, 0, 90, 180 or 270, for whether a glyph is upright.
    """
    clusters: list[list[TextCharacter]] = []
    for character in characters:
        if (
            clusters
            and character.origin is not None
            and character.origin == clusters[-1][0].origin
            and not _space(character)
            and not any(_space(c) for c in clusters[-1])
        ):
            clusters[-1].append(character)
        else:
            clusters.append([character])
    return tuple(
        _glyph(page_index, index, rotation, cluster) for index, cluster in enumerate(clusters)
    )


def _space(character: TextCharacter) -> bool:
    """White space as str.isspace() takes it, of a code PDFium maps."""
    return character.map_error is False and chr(character.code).isspace()


def _text_of(cluster: list[TextCharacter]) -> str | Unknown:
    if any(c.map_error is not False for c in cluster):
        return Unknown(_UNREADABLE, "PDFium finds no character for a code")
    units = "".join(chr(c.code) for c in cluster)
    try:
        # PDFium gives a character beyond U+FFFF as the two halves of a
        # surrogate pair, at one origin.
        return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeDecodeError:
        return Unknown(_UNREADABLE, "PDFium gives half of a surrogate pair")


def _glyph(page_index: int, index: int, rotation: int, cluster: list[TextCharacter]) -> Glyph:
    first = cluster[0]
    origin: Point | Unknown = (
        point(*first.origin)
        if first.origin is not None
        else Unknown(_UNREADABLE, "PDFium gives no origin")
    )
    direction, size, along = _frame(first)
    box: Quad | Unknown
    if isinstance(origin, Unknown) or isinstance(along, Unknown):
        box = origin if isinstance(origin, Unknown) else along
    elif any(c.loose_box is None for c in cluster):
        box = Unknown(_UNREADABLE, "PDFium gives no box")
    elif first.ascent is None or first.descent is None:
        box = Unknown(_UNREADABLE, "PDFium gives no ascent or descent of the font")
    else:
        box = _box(origin, along, size, cluster, first.ascent, first.descent)
    upright: bool | Unknown = direction
    if not isinstance(direction, Unknown):
        shown = (direction - rotation) % 360
        upright = min(shown, 360 - shown) <= UPRIGHT_DEGREES
    render_mode: int | Unknown = (
        first.render_mode
        if first.render_mode is not None
        else Unknown(_UNREADABLE, "PDFium gives no render mode")
    )
    hyphen: bool | Unknown = any(c.hyphen for c in cluster)
    if any(c.hyphen is None for c in cluster):
        hyphen = Unknown(_UNREADABLE, "PDFium does not say whether it is a line-end hyphen")
    return made(
        Glyph,
        ref=made(GlyphRef, page_index=page_index, glyph_index=index),
        text=_text_of(cluster),
        origin=origin,
        direction=direction,
        size=size,
        box=box,
        upright=upright,
        render_mode=render_mode,
        hyphen=hyphen,
    )


def _frame(
    character: TextCharacter,
) -> tuple[float | Unknown, float | Unknown, tuple[float, float] | Unknown]:
    """The direction in degrees, the size across the baseline, and the unit
    vector along the baseline, of the glyph matrix: PDFium's matrix scaled by
    the font size."""
    if character.matrix is None or character.font_size is None:
        why = Unknown(_UNREADABLE, "PDFium gives no matrix or no font size")
        return why, why, why
    a, b, c, d = character.matrix[:4]
    sign = 1.0 if character.font_size > 0 else -1.0
    length = math.hypot(a, b)
    area = abs(a * d - b * c)
    if length == 0 or area == 0:
        why = Unknown(_UNREADABLE, "the glyph matrix has no extent")
        return why, why, why
    unit = (sign * a / length, sign * b / length)
    return degrees_of(*unit), abs(character.font_size) * area / length, unit


def _box(
    origin: Point,
    along: tuple[float, float],
    size: float | Unknown,
    cluster: list[TextCharacter],
    ascent: float,
    descent: float,
) -> Quad:
    """The advance box: along the baseline, the extent of PDFium's loose
    boxes; across, the descent and the ascent of the font at the glyph's size."""
    assert not isinstance(size, Unknown)
    ux, uy = along
    corners = [
        (x, y)
        for c in cluster
        for x in (c.loose_box[0], c.loose_box[2])  # type: ignore[index]
        for y in (c.loose_box[1], c.loose_box[3])  # type: ignore[index]
    ]
    projected = [(x - origin.x) * ux + (y - origin.y) * uy for x, y in corners]
    start, end = min(projected), max(projected)
    low, high = descent * size, ascent * size
    vx, vy = -uy, ux

    def at(u: float, v: float) -> Point:
        return point(origin.x + u * ux + v * vx, origin.y + u * uy + v * vy)

    return quad(at(start, low), at(end, low), at(end, high), at(start, high))
