"""Words and lines: glyphs joined by the pen's movement along the baseline.

docs/conventions.md, "Glyphs, words and lines", states the rule in prose;
each threshold is pinned by a test on constructed values. The thresholds
follow the approach of MuPDF's structured text, recalled, not copied
(docs/provenance.md).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pdfacts.core.geometry import Point, Rect
from pdfacts.core.refs import _is_int
from pdfacts.core.values import Unknown
from pdfacts.text.glyphs import Glyph, _text

# In units of the glyph's size: a gap forward from SPACE on ends a word; a step
# of LINE or more forward or back, or a baseline off by LINE or more, begins a
# new line.
SPACE = 0.15
LINE = 0.8


def _page(value: object) -> None:
    if not _is_int(value):
        raise TypeError(f"page_index must be an int, not {type(value).__name__}")
    if value < 0:  # type: ignore[operator]
        raise ValueError(f"page_index starts at 0, not {value}")


def _indices(value: object, name: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise TypeError(f"{name} must be a tuple of at least one index")
    for index in value:
        if not _is_int(index):
            raise TypeError(f"{name} holds ints, not {type(index).__name__}")
        if index < 0:
            raise ValueError(f"{name} holds indices from 0, not {index}")
    if list(value) != sorted(set(value)):
        raise ValueError(f"{name} holds each index once, in order")


@dataclass(frozen=True, slots=True, kw_only=True)
class Word:
    """Glyphs of one line with no space between them.

    `glyphs` are the glyphs' indices on the page, in content order, none of
    them white space. `text` is their text with U+0002 written as "-";
    unknown where the text of one of them is. `box` holds their boxes.
    """

    page_index: int
    glyphs: tuple[int, ...]
    text: str | Unknown
    box: Rect

    def __post_init__(self) -> None:
        _page(self.page_index)
        _indices(self.glyphs, "glyphs")
        _text(self.text, "text")
        if not isinstance(self.box, Rect):
            raise TypeError(f"box must be a Rect, not {type(self.box).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """Words one after another along one baseline.

    `words` are the words' indices on the page, in content order. `text` is
    their text, one space between two; unknown where the text of one of them
    is. `box` holds their boxes.
    """

    page_index: int
    words: tuple[int, ...]
    text: str | Unknown
    box: Rect

    def __post_init__(self) -> None:
        _page(self.page_index)
        _indices(self.words, "words")
        _text(self.text, "text")
        if not isinstance(self.box, Rect):
            raise TypeError(f"box must be a Rect, not {type(self.box).__name__}")


def words_and_lines(
    page_index: int, glyphs: Sequence[Glyph]
) -> tuple[tuple[Word, ...], tuple[Line, ...]]:
    """The page's words and lines from its glyphs in content order.

    Only upright glyphs whose box is known take part; the others are passed
    over. For each glyph that takes part after the first, the step from where
    the glyph before that took part ended along its baseline to this glyph's
    origin is measured in units of this glyph's size, along its baseline and
    across it. Across by LINE or more either way, or along by LINE or more
    either way, begins a new line; along by SPACE or more forward ends a word;
    a smaller step, or a step back, keeps the glyph in the word. A glyph of
    white space ends a word and is in none, and moves the pen like any glyph.
    A line without a word is none.
    """
    lines: list[list[list[int]]] = []
    pen: Point | None = None
    for glyph in glyphs:
        if glyph.upright is not True or isinstance(glyph.box, Unknown):
            continue
        assert not isinstance(glyph.origin, Unknown)
        assert not isinstance(glyph.size, Unknown) and not isinstance(glyph.direction, Unknown)
        ux = math.cos(math.radians(glyph.direction))
        uy = math.sin(math.radians(glyph.direction))
        if pen is None:
            lines.append([[]])
        else:
            dx, dy = glyph.origin.x - pen.x, glyph.origin.y - pen.y
            step = (dx * ux + dy * uy) / glyph.size
            off = (-dx * uy + dy * ux) / glyph.size
            if abs(off) >= LINE or abs(step) >= LINE:
                lines.append([[]])
            elif step >= SPACE:
                lines[-1].append([])
        if isinstance(glyph.text, str) and glyph.text.isspace():
            lines[-1].append([])
        else:
            lines[-1][-1].append(glyph.ref.glyph_index)
        end = max(
            (p.x - glyph.origin.x) * ux + (p.y - glyph.origin.y) * uy
            for p in (glyph.box.p1, glyph.box.p2, glyph.box.p3, glyph.box.p4)
        )
        pen = Point(glyph.origin.x + end * ux, glyph.origin.y + end * uy)
    by_index = {glyph.ref.glyph_index: glyph for glyph in glyphs}
    words: list[Word] = []
    made: list[Line] = []
    for groups in lines:
        indices = []
        for group in groups:
            if group:
                indices.append(len(words))
                words.append(_word(page_index, [by_index[i] for i in group]))
        if indices:
            made.append(_line(page_index, indices, [words[i] for i in indices]))
    return tuple(words), tuple(made)


def _bounds(points: list[Point]) -> Rect:
    return Rect(
        min(p.x for p in points),
        min(p.y for p in points),
        max(p.x for p in points),
        max(p.y for p in points),
    )


def _word(page_index: int, glyphs: list[Glyph]) -> Word:
    unknown = next((g.text for g in glyphs if isinstance(g.text, Unknown)), None)
    text = "".join(g.text.replace("\x02", "-") for g in glyphs if isinstance(g.text, str))
    corners = [p for g in glyphs for p in (g.box.p1, g.box.p2, g.box.p3, g.box.p4)]  # type: ignore[union-attr]
    return Word(
        page_index=page_index,
        glyphs=tuple(g.ref.glyph_index for g in glyphs),
        text=unknown if unknown is not None else text,
        box=_bounds(corners),
    )


def _line(page_index: int, indices: list[int], words: list[Word]) -> Line:
    unknown = next((w.text for w in words if isinstance(w.text, Unknown)), None)
    text = unknown if unknown is not None else " ".join(w.text for w in words)  # type: ignore[misc]
    corners = [
        Point(x, y) for w in words for x in (w.box.x0, w.box.x1) for y in (w.box.y0, w.box.y1)
    ]
    return Line(page_index=page_index, words=tuple(indices), text=text, box=_bounds(corners))
