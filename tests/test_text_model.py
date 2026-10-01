"""The facts of a page's text: what Glyph, Word, Line and GlyphRef accept.

Each type refuses what its docstring rules out; the values here are written
out as the test needs them (AGENTS.md, rule 2).
"""

from __future__ import annotations

import dataclasses

import pytest

import pdfacts
from pdfacts import Glyph, GlyphRef, Line, Point, Quad, Rect, Unknown, UnknownReason, Word

BOX = Quad(Point(100, 98), Point(110, 98), Point(110, 108), Point(100, 108))
WHY = Unknown(UnknownReason.UNREADABLE, "PDFium gives none")


def glyph(**changes) -> Glyph:
    values = dict(
        ref=GlyphRef(page_index=0, glyph_index=0),
        text="A",
        origin=Point(100, 100),
        direction=0.0,
        size=10.0,
        box=BOX,
        upright=True,
        render_mode=0,
        hyphen=False,
    )
    values.update(changes)
    return Glyph(**values)


def test_the_types_are_exported():
    for name in ("Glyph", "GlyphRef", "Line", "Word"):
        assert name in pdfacts.__all__
        assert getattr(pdfacts, name) is not None


def test_a_glyph_ref_counts_from_0():
    assert GlyphRef(page_index=0, glyph_index=0).glyph_index == 0
    with pytest.raises(ValueError):
        GlyphRef(page_index=0, glyph_index=-1)
    with pytest.raises(ValueError):
        GlyphRef(page_index=-1, glyph_index=0)
    with pytest.raises(TypeError):
        GlyphRef(page_index=0, glyph_index=True)


def test_a_glyph_with_every_value_known_and_with_every_value_unknown():
    assert glyph().text == "A"
    unknown = glyph(
        text=WHY,
        origin=WHY,
        direction=WHY,
        size=WHY,
        box=WHY,
        upright=WHY,
        render_mode=WHY,
        hyphen=WHY,
    )
    assert unknown.size is WHY
    with pytest.raises(dataclasses.FrozenInstanceError):
        unknown.size = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"ref": (0, 0)}, TypeError),
        ({"text": ""}, ValueError),
        ({"text": 65}, TypeError),
        ({"origin": (100, 100)}, TypeError),
        ({"direction": 360.0}, ValueError),
        ({"direction": -0.5}, ValueError),
        ({"size": float("inf")}, ValueError),
        ({"size": 0.0}, ValueError),
        ({"size": -10.0}, ValueError),
        ({"box": Rect(100, 98, 110, 108)}, TypeError),
        ({"upright": 1}, TypeError),
        ({"render_mode": 8}, ValueError),
        ({"render_mode": True}, TypeError),
        ({"hyphen": 0}, TypeError),
        ({"direction": WHY, "box": WHY}, ValueError),
    ],
    ids=[
        "ref no GlyphRef",
        "text empty",
        "text no str",
        "origin no Point",
        "direction 360",
        "direction below 0",
        "size not finite",
        "size 0",
        "size below 0",
        "box no Quad",
        "upright no bool",
        "render mode 8",
        "render mode a bool",
        "hyphen no bool",
        "upright known without a direction",
    ],
)
def test_a_glyph_refuses(changes, error):
    with pytest.raises(error):
        glyph(**changes)


def test_a_known_box_needs_its_origin_direction_and_size():
    for name in ("origin", "direction", "size"):
        with pytest.raises(ValueError):
            glyph(**{name: WHY, "upright": WHY})
    assert glyph(origin=WHY, box=WHY).box is WHY


def test_a_direction_given_as_an_int_is_a_float():
    assert isinstance(glyph(direction=90).direction, float)


@pytest.mark.parametrize("kind", [Word, Line])
def test_words_and_lines_name_their_parts_once_in_order(kind):
    name = "glyphs" if kind is Word else "words"
    made = kind(page_index=0, text="A", box=Rect(0, 0, 1, 1), **{name: (0, 2)})
    assert getattr(made, name) == (0, 2)
    for bad, error in (
        ((), TypeError),
        ([0], TypeError),
        ((2, 0), ValueError),
        ((0, 0), ValueError),
        ((-1,), ValueError),
        ((0.0,), TypeError),
        ((True,), TypeError),
    ):
        with pytest.raises(error):
            kind(page_index=0, text="A", box=Rect(0, 0, 1, 1), **{name: bad})


@pytest.mark.parametrize("kind", [Word, Line])
def test_words_and_lines_refuse(kind):
    name = "glyphs" if kind is Word else "words"
    values = dict(page_index=0, text="A", box=Rect(0, 0, 1, 1), **{name: (0,)})
    assert kind(**{**values, "text": WHY}).text is WHY
    for changes, error in (
        ({"text": ""}, ValueError),
        ({"box": BOX}, TypeError),
        ({"page_index": -1}, ValueError),
        ({"page_index": True}, TypeError),
        ({"page_index": 0.0}, TypeError),
    ):
        with pytest.raises(error):
            kind(**{**values, **changes})
