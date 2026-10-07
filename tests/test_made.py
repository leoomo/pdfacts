"""Facts pdfacts computes itself, made without their constructors' checks
(pdfacts.core.made; ROADMAP.md, M4b).

Every fact the text reader makes must be one its public constructor makes
from the same values: equal, of the same repr, and passing every check. The
inputs are constructed; the reference is the constructor itself.
"""

from __future__ import annotations

import dataclasses
import math
import random
import sys
from pathlib import Path

import pytest

import pdfacts
from pdfacts.backends.protocols import TextCharacter
from pdfacts.core.geometry import Point, Quad, Rect
from pdfacts.core.made import made, point, quad, rect
from pdfacts.core.refs import GlyphRef
from pdfacts.text.glyphs import Glyph, glyphs_of
from pdfacts.text.layout import Line, Word, words_and_lines

sys.path.insert(0, str(Path(__file__).parent))
from synthetic.builder import build, stream  # noqa: E402


def rechecked(value):
    """`value` made again through the public constructors, all the way down."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: rechecked(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return type(value)(**fields)
    if isinstance(value, tuple):
        return tuple(rechecked(item) for item in value)
    return value


def assert_as_constructed(facts) -> None:
    for fact in facts:
        again = rechecked(fact)
        assert again == fact
        assert repr(again) == repr(fact)


# --- the constructors of pdfacts.core.made -------------------------------------------


def test_point_is_the_constructors():
    assert point(1.5, -2.0) == Point(1.5, -2.0)
    assert repr(point(-0.0, 3.0)) == repr(Point(-0.0, 3.0)) == "Point(x=0.0, y=3.0)"


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize("where", ["x", "y"])
def test_point_refuses_what_is_not_finite_as_the_constructor(bad, where):
    values = {"x": 1.0, "y": 1.0, where: bad}
    with pytest.raises(ValueError) as checked:
        Point(**values)
    with pytest.raises(ValueError) as unchecked:
        point(**values)
    assert str(unchecked.value) == str(checked.value)


def test_rect_puts_the_corners_in_order_as_the_constructor():
    assert rect(5.0, 7.0, 1.0, -0.0) == Rect(5.0, 7.0, 1.0, -0.0)
    assert repr(rect(5.0, 7.0, 1.0, -0.0)) == repr(Rect(5.0, 7.0, 1.0, -0.0))


@pytest.mark.parametrize("where", ["x0", "y0", "x1", "y1"])
def test_rect_refuses_what_is_not_finite_as_the_constructor(where):
    values = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0, where: math.inf}
    with pytest.raises(ValueError) as checked:
        Rect(**values)
    with pytest.raises(ValueError) as unchecked:
        rect(**values)
    assert str(unchecked.value) == str(checked.value)


def test_quad_and_made_hold_every_field():
    corners = [Point(float(i), float(-i)) for i in range(4)]
    assert quad(*corners) == Quad(*corners)
    ref = made(GlyphRef, page_index=2, glyph_index=5)
    assert ref == GlyphRef(page_index=2, glyph_index=5)
    assert repr(ref) == repr(GlyphRef(page_index=2, glyph_index=5))


def test_a_fact_made_without_checks_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        point(1.0, 2.0).x = 3.0  # type: ignore[misc]


# --- the text reader's facts ---------------------------------------------------------


def character(rng: random.Random, x: float, y: float, matrix, size: float) -> TextCharacter:
    code = rng.choice([0x20, 0x41, 0x61, 0xE4, 0x2D, 0xFB01])
    return TextCharacter(
        code=code,
        map_error=rng.random() < 0.05,
        origin=(x, y) if rng.random() > 0.03 else None,
        matrix=matrix,
        font_size=size,
        loose_box=(x - 0.2, y - 2.0, x + 5.0, y + 7.0) if rng.random() > 0.03 else None,
        ascent=0.9 if rng.random() > 0.03 else None,
        descent=-0.2,
        render_mode=rng.choice([0, 0, 0, 3, 7, None]),
        hyphen=rng.choice([False, False, True, None]),
    )


@pytest.mark.parametrize("seed", range(40))
def test_glyphs_words_and_lines_are_as_constructed(seed):
    """Lines of characters at angles, with negative zero, unknown values and
    characters drawn at one origin, as the reader's rules meet them."""
    rng = random.Random(seed)
    angle = rng.choice([0.0, 90.0, 180.0, 270.0, 0.5, 33.0])
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    size = rng.choice([1.0, 7.5, 12.0, -10.0])
    matrix = (c, s, -s, c, 0.0, 0.0)
    characters = []
    for line in range(rng.randint(1, 4)):
        x, y = -0.0, 700.0 - 14.0 * line
        for _ in range(rng.randint(1, 30)):
            characters.append(character(rng, x, y, matrix, size))
            if rng.random() > 0.1:
                x += rng.choice([0.0, 5.0, 5.2, 9.0, 40.0])
    rotation = rng.choice([0, 90, 180, 270])
    glyphs = glyphs_of(seed % 3, rotation, characters)
    words, lines = words_and_lines(seed % 3, glyphs)
    assert_as_constructed(glyphs)
    assert_as_constructed(words)
    assert_as_constructed(lines)
    assert all(type(g) is Glyph for g in glyphs)
    assert all(type(w) is Word for w in words) and all(type(x) is Line for x in lines)


def page(content: bytes, rotate: int = 0) -> bytes:
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Rotate %d"
            b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>" % rotate,
            4: stream(content),
            5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        },
        1,
    )


@pytest.mark.parametrize(
    "content",
    [
        b"BT /F1 12 Tf 10 250 Td (Two words) Tj 0 -14 Td (and a line) Tj ET",
        b"BT /F1 9 Tf 0 1 -1 0 150 20 Tm (turned) Tj ET",
        b"BT /F1 10 Tf 2 Tc 30 Tz 0 0 Td (spaced out) Tj ET",
        b"BT /F1 11 Tf 20 100 Td [(W) 120 (ave) -300 (gap)] TJ ET",
        b"BT /F1 0.5 Tf -0 -0 Td (tiny at zero) Tj ET",
    ],
)
@pytest.mark.parametrize("rotate", [0, 90])
def test_what_the_reader_gives_is_as_constructed(content, rotate):
    with pdfacts.open(page(content, rotate)) as document:
        found = pdfacts.glyphs(document, 0)
        assert found
        assert_as_constructed(found)
        assert_as_constructed(pdfacts.words(document, 0))
        assert_as_constructed(pdfacts.lines(document, 0))
