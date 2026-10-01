"""The rule set of a page's text: characters into glyphs, glyphs into words and lines.

The rules are those of docs/conventions.md, "Glyphs, words and lines". Each
character is built here as #70's backend gives it: by default a glyph of a
font 1000 thousandths wide, ascent 800, descent -200, at size 10, upright. The
values expected follow from these numbers (AGENTS.md, rule 2); the
thresholds are written out as numbers.
"""

from __future__ import annotations

import math

import pytest

from pdfacts.backends.protocols import TextCharacter
from pdfacts.core.geometry import Point, Quad, Rect
from pdfacts.core.refs import GlyphRef
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.text.glyphs import UPRIGHT_DEGREES, glyphs_of
from pdfacts.text.layout import LINE, SPACE, words_and_lines


def char(
    text: str,
    x: float,
    y: float = 100.0,
    *,
    size: float = 10.0,
    matrix: tuple[float, ...] | None = (1.0, 0.0, 0.0, 1.0),
    loose: tuple[float, float, float, float] | None | type(...) = ...,
    ascent: float | None = 0.8,
    descent: float | None = -0.2,
    **others,
) -> TextCharacter:
    """A character at (x, y); by default its loose box the advance box of a
    glyph 1000 wide at size 10; `loose=None` for none."""
    if loose is ...:
        loose = (x, y - 2.0, x + 10.0, y + 8.0)
    values = dict(
        code=ord(text),
        map_error=False,
        hyphen=False,
        origin=(x, y),
        matrix=None if matrix is None else (*matrix, x, y),
        font_size=size,
        loose_box=loose,
        render_mode=0,
        ascent=ascent,
        descent=descent,
    )
    values.update(others)
    return TextCharacter(**values)


def turned(text: str, x: float, y: float, degrees: float) -> TextCharacter:
    a, b = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return char(text, x, y, matrix=(a, b, -b, a), loose=(x - 10, y - 10, x + 10, y + 10))


def read(*characters: TextCharacter, rotation: int = 0):
    glyphs = glyphs_of(0, rotation, characters)
    words, lines = words_and_lines(0, glyphs)
    return glyphs, words, lines


def texts(items) -> list:
    return [item.text for item in items]


def test_the_thresholds():
    assert (UPRIGHT_DEGREES, SPACE, LINE) == (1.0, 0.15, 0.8)


# --- characters into glyphs --------------------------------------------------------------


def test_a_character_is_a_glyph():
    (glyph,) = glyphs_of(3, 0, [char("A", 100)])
    assert glyph.ref == GlyphRef(page_index=3, glyph_index=0)
    assert (glyph.text, glyph.origin, glyph.direction, glyph.size) == (
        "A",
        Point(100, 100),
        0.0,
        10.0,
    )
    assert glyph.box == Quad(Point(100, 98), Point(110, 98), Point(110, 108), Point(100, 108))
    assert (glyph.upright, glyph.render_mode, glyph.hyphen) == (True, 0, False)


def test_characters_at_one_origin_are_one_glyph():
    """A ligature PDFium expanded: f and i at one origin, their boxes as one."""
    glyphs = glyphs_of(
        0,
        0,
        [
            char("f", 100, loose=(100, 98, 106, 108)),
            char("i", 100, loose=(99, 98, 110, 108)),
            char("B", 110),
        ],
    )
    assert texts(glyphs) == ["fi", "B"]
    assert glyphs[0].box == Quad(Point(99, 98), Point(110, 98), Point(110, 108), Point(99, 108))
    assert [g.ref.glyph_index for g in glyphs] == [0, 1]


def test_a_glyph_of_several_characters_takes_the_first_ones_values():
    """Render mode and font from the first character; a flag or a gap of any."""
    (glyph,) = glyphs_of(
        0,
        0,
        [
            char("f", 100, render_mode=3, ascent=0.8),
            char("i", 100, render_mode=0, ascent=0.5, hyphen=True),
        ],
    )
    assert (glyph.render_mode, glyph.hyphen) == (3, True)
    assert glyph.box == Quad(Point(100, 98), Point(110, 98), Point(110, 108), Point(100, 108))
    (unmapped,) = glyphs_of(0, 0, [char("f", 100), char("i", 100, map_error=True)])
    assert isinstance(unmapped.text, Unknown)
    (boxless,) = glyphs_of(0, 0, [char("f", 100), char("i", 100, loose=None)])
    assert isinstance(boxless.box, Unknown)


def test_a_space_at_the_next_origin_is_a_glyph_of_its_own():
    glyphs = glyphs_of(
        0, 0, [char(" ", 100), char("B", 100), char(" ", 100), char(" ", 110), char(" ", 110)]
    )
    assert texts(glyphs) == [" ", "B", " ", " ", " "]


@pytest.mark.parametrize(
    "space", ["\u00a0", "\t", "\u2007"], ids=["no-break space", "tab", "figure space"]
)
def test_white_space_at_a_letters_origin_stands_alone(space):
    glyphs = glyphs_of(0, 0, [char("B", 100), char(space, 100)])
    assert texts(glyphs) == ["B", space]


@pytest.mark.parametrize("error", [True, None], ids=["no character", "no answer"])
def test_a_code_pdfium_cannot_map_is_no_white_space(error):
    """A tab whose code PDFium maps to nothing joins the letter at its origin."""
    (glyph,) = glyphs_of(0, 0, [char("B", 100), char("\t", 100, map_error=error)])
    assert isinstance(glyph.text, Unknown)


def test_a_code_pdfium_cannot_map_leaves_the_text_unknown():
    glyphs = glyphs_of(0, 0, [char("A", 100, map_error=True), char("B", 110, map_error=None)])
    for glyph in glyphs:
        assert isinstance(glyph.text, Unknown) and glyph.text.reason is UnknownReason.UNREADABLE


def test_halves_of_a_surrogate_pair_are_one_character():
    (glyph,) = glyphs_of(0, 0, [char("\ud835", 100), char("\udc65", 100)])
    assert glyph.text == "\U0001d465"
    (alone,) = glyphs_of(0, 0, [char("\ud835", 100)])
    assert isinstance(alone.text, Unknown) and alone.text.reason is UnknownReason.UNREADABLE


def test_the_direction_from_the_matrix():
    (glyph,) = glyphs_of(0, 0, [turned("A", 100, 100, 90)])
    assert glyph.direction == pytest.approx(90.0)
    assert glyph.upright is False


def test_a_matrix_without_area_leaves_the_glyph_without_size_or_box():
    """PDFium gives (1, 0, 0, 0) for text under `1 0 0 0 0 0 cm`."""
    (glyph,) = glyphs_of(0, 0, [char("A", 100, matrix=(1.0, 0.0, 0.0, 0.0))])
    for value in (glyph.direction, glyph.size, glyph.box, glyph.upright):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.UNREADABLE


def test_an_angle_a_hair_below_0_is_0():
    (glyph,) = glyphs_of(0, 0, [char("A", 100, matrix=(1.0, -1e-17, 0.0, 1.0))])
    assert (glyph.direction, glyph.upright) == (0.0, True)


def test_a_negative_font_size_turns_the_glyph_half_round():
    (glyph,) = glyphs_of(0, 0, [char("A", 100, size=-10.0, loose=(90, 92, 100, 102))])
    assert (glyph.direction, glyph.size, glyph.upright) == (180.0, 10.0, False)


@pytest.mark.parametrize(
    ("matrix", "size"),
    [((2.0, 0.0, 0.0, 3.0), 30.0), ((0.5, 0.0, 0.0, 1.0), 10.0), ((1.0, 0.0, 0.5, 1.0), 10.0)],
    ids=["scaled", "horizontal scaling", "sheared"],
)
def test_the_size_is_the_font_size_across_the_baseline(matrix, size):
    (glyph,) = glyphs_of(0, 0, [char("A", 100, matrix=matrix)])
    assert glyph.size == size


def test_the_box_at_the_glyphs_size():
    """Twice as wide, three times as high: size 30, ascent 24 above, descent 6 below."""
    (glyph,) = glyphs_of(
        0, 0, [char("A", 100, matrix=(2.0, 0.0, 0.0, 3.0), loose=(100, 94, 120, 124))]
    )
    assert glyph.box == Quad(Point(100, 94), Point(120, 94), Point(120, 124), Point(100, 124))


def test_the_box_along_the_baseline_is_the_loose_box_across_the_ascent_and_descent():
    """The loose box begins before the origin, at the ink, and reaches above the ascent."""
    (glyph,) = glyphs_of(0, 0, [char("j", 100, loose=(99, 95, 106, 112))])
    assert glyph.box == Quad(Point(99, 98), Point(106, 98), Point(106, 108), Point(99, 108))


def test_the_box_begins_at_the_ink_where_the_loose_box_does():
    """PDFium gives some hyphens their ink box alone, beginning after the origin."""
    (glyph,) = glyphs_of(0, 0, [char("-", 100, loose=(102, 102, 108, 104))])
    assert glyph.box == Quad(Point(102, 98), Point(108, 98), Point(108, 108), Point(102, 108))


def test_the_box_of_a_turned_glyph_turns_with_it():
    """Baseline up the page: the ascent lies to its left."""
    (glyph,) = glyphs_of(
        0, 0, [char("A", 100, matrix=(0.0, 1.0, -1.0, 0.0), loose=(92, 100, 102, 110))]
    )
    assert glyph.box == Quad(Point(102, 100), Point(102, 110), Point(92, 110), Point(92, 100))


@pytest.mark.parametrize(
    ("rotation", "degrees", "upright"),
    [
        (0, 0.9, True),
        (0, -0.9, True),
        (0, 1.1, False),
        (0, -1.1, False),
        (270, 270.0, True),
        (90, 90.0, True),
        (90, 0.0, False),
        (180, 180.0, True),
    ],
)
def test_upright_as_the_page_is_shown(rotation, degrees, upright):
    (glyph,) = glyphs_of(0, rotation, [turned("A", 100, 100, degrees)])
    assert glyph.upright is upright


def test_what_pdfium_does_not_give_is_unknown():
    glyphs = glyphs_of(
        0,
        0,
        [
            char("A", 100, matrix=None),
            char("B", 110, ascent=None),
            char("C", 120, loose=None),
            char("D", 130, render_mode=None, hyphen=None),
        ],
    )
    first = glyphs[0]
    assert all(
        isinstance(v, Unknown) for v in (first.direction, first.size, first.box, first.upright)
    )
    assert isinstance(glyphs[1].box, Unknown) and isinstance(glyphs[2].box, Unknown)
    assert isinstance(glyphs[3].render_mode, Unknown) and isinstance(glyphs[3].hyphen, Unknown)
    assert glyphs[1].upright is True


def test_the_hyphen_and_the_render_mode_are_carried():
    (glyph,) = glyphs_of(0, 0, [char("\x02", 100, hyphen=True, render_mode=3)])
    assert (glyph.text, glyph.hyphen, glyph.render_mode) == ("\x02", True, 3)


# --- glyphs into words and lines --------------------------------------------------------


def test_glyphs_one_after_another_are_a_word():
    _, words, lines = read(char("A", 100), char("B", 110))
    assert (texts(words), texts(lines)) == (["AB"], ["AB"])
    assert words[0].glyphs == (0, 1)
    assert words[0].box == Rect(100, 98, 120, 108)
    assert (lines[0].words, lines[0].box) == ((0,), Rect(100, 98, 120, 108))


@pytest.mark.parametrize(
    ("gap", "words", "lines"),
    [
        (1.49, ["AB"], ["AB"]),
        (1.5, ["A", "B"], ["A B"]),
        (7.99, ["A", "B"], ["A B"]),
        (8.0, ["A", "B"], ["A", "B"]),
        (-1.5, ["AB"], ["AB"]),
        (-7.99, ["AB"], ["AB"]),
        (-8.0, ["A", "B"], ["A", "B"]),
    ],
    ids=[
        "below 0.15",
        "at 0.15",
        "below 0.8",
        "at 0.8",
        "back 0.15",
        "back below 0.8",
        "back 0.8",
    ],
)
def test_the_step_along_the_baseline(gap, words, lines):
    """A ends at 110; B begins `gap` after."""
    _, found, made = read(char("A", 100), char("B", 110 + gap))
    assert (texts(found), texts(made)) == (words, lines)


def test_a_step_back_into_an_overhang_stays_in_the_word():
    """The f's ink reaches 4 past its advance; the i begins at the advance."""
    _, words, _ = read(char("f", 100, loose=(100, 98, 114, 108)), char("i", 110))
    assert texts(words) == ["fi"]


@pytest.mark.parametrize(("off", "lines"), [(7.99, 1), (8.0, 2), (-8.0, 2)])
def test_the_baseline_off(off, lines):
    _, _, made = read(char("A", 100), char("B", 110, 100 + off))
    assert len(made) == lines


def test_the_step_in_units_of_the_later_glyphs_size():
    """A ends at 110. B, twice the size, 10 further: 0.5 of its size, a space.
    D, half the size of C, 1.6 after it: 0.16 of D's size, a space; 0.08 of C's."""
    big = char("B", 120, size=20.0, loose=(120, 96, 140, 116))
    _, words, lines = read(char("A", 100), big)
    assert (texts(words), texts(lines)) == (["A", "B"], ["A B"])
    small = char("D", 141.6, size=10.0)
    _, words, _ = read(char("C", 120, size=20.0, loose=(120, 96, 140, 116)), small)
    assert texts(words) == ["C", "D"]


def test_a_space_ends_a_word_and_moves_the_pen():
    _, words, lines = read(char("A", 100), char(" ", 110), char("B", 120))
    assert (texts(words), texts(lines)) == (["A", "B"], ["A B"])
    assert [w.glyphs for w in words] == [(0,), (2,)]
    assert lines[0].box == Rect(100, 98, 130, 108)


@pytest.mark.parametrize("space", ["\u00a0", "\t"], ids=["no-break space", "tab"])
def test_any_white_space_ends_a_word(space):
    _, words, _ = read(char("A", 100), char(space, 110), char("B", 120))
    assert texts(words) == ["A", "B"]


def test_leading_and_trailing_spaces_extend_no_box():
    _, words, lines = read(char(" ", 90), char("A", 100), char(" ", 110))
    assert (texts(words), lines[0].box) == (["A"], Rect(100, 98, 110, 108))


def test_a_line_of_spaces_alone_is_none():
    _, words, lines = read(char(" ", 100), char(" ", 110))
    assert (words, lines) == ((), ())


def test_glyphs_that_are_not_upright_are_passed_over():
    """The turned X lies far off; A and B join as if it were not there."""
    _, words, _ = read(char("A", 100), turned("X", 300, 300, 90), char("B", 110))
    assert texts(words) == ["AB"] and words[0].glyphs == (0, 2)


def test_white_space_passed_over_ends_no_word():
    """The space is turned: it takes no part, and A and B stay one word."""
    _, words, _ = read(char("A", 100), turned(" ", 110, 100, 90), char("B", 110))
    assert texts(words) == ["AB"]


def test_glyphs_without_a_box_are_passed_over_and_move_no_pen():
    """X has no box: B follows A as if X were not there."""
    _, words, lines = read(char("A", 100), char("X", 300, ascent=None), char("B", 110))
    assert (texts(words), texts(lines), words[0].glyphs) == (["AB"], ["AB"], (0, 2))


def test_a_line_end_hyphen_is_written_as_a_hyphen():
    _, words, lines = read(char("A", 100), char("\x02", 110, hyphen=True))
    assert (texts(words), texts(lines)) == (["A-"], ["A-"])


def test_an_unknown_text_leaves_its_word_and_line_unknown():
    _, words, lines = read(char("A", 100, map_error=True), char("B", 120))
    assert isinstance(words[0].text, Unknown) and words[1].text == "B"
    assert isinstance(lines[0].text, Unknown)


def test_a_line_holds_all_its_words():
    """The middle word is twice as tall."""
    _, _, lines = read(
        char("A", 100),
        char("B", 120, size=20.0, loose=(120, 96, 140, 116)),
        char("C", 145),
    )
    assert (texts(lines), lines[0].box) == (["A B C"], Rect(100, 96, 155, 116))


def test_lines_in_content_order():
    _, _, lines = read(char("A", 100, 50), char("B", 100, 200))
    assert texts(lines) == ["A", "B"]


def test_text_upright_on_a_page_turned_by_270():
    """The baseline runs down the page in user space: (x, y) to (x, y - 10)."""
    m = (0.0, -1.0, 1.0, 0.0)
    first = char("A", 100, 300, matrix=m, loose=(98, 290, 108, 300))
    second = char("B", 100, 290, matrix=m, loose=(98, 280, 108, 290))
    _, words, _ = read(first, second, rotation=270)
    assert (texts(words), words[0].box) == (["AB"], Rect(98, 280, 108, 300))
