"""words() and lines() with TextRules.V3: the rotated glyphs take part
(docs/conventions.md, "Glyphs, words and lines").

Files are built with the byte-level builder; the values expected are those
written (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import TextRules
from synthetic.builder import build, stream

# Turn a direction by this much and stay on the line; by more and begin one.
NEAR = 1.0
# The corpus's measured near-misses: 1.82 stays a turn (begins a line), and
# text at a small slope rides one line when its turn stays within NEAR.
COS = b"0.9998"
SIN = b"0.0175"  # sin(1°), the slope of text one degree off


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


def reads(data: bytes, rules: TextRules = TextRules.V3):
    with pdfacts.open(data) as document:
        words = pdfacts.words(document, 0, rules=rules)
        lines = pdfacts.lines(document, 0, rules=rules)
    return words, lines


def texts(words) -> list[str]:
    return [w.text for w in words]


def test_rotated_text_is_one_word_and_line():
    """Text set at 90°: one word of two glyphs, one line, under V3; V2 passes
    the glyphs over and gives none."""
    data = page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB) Tj ET")
    words, lines = reads(data)
    assert texts(words) == ["AB"]
    assert len(lines) == 1
    v2_words, v2_lines = reads(data, rules=TextRules.V2)
    assert (v2_words, v2_lines) == ((), ())


def test_two_rotated_words():
    data = page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB cd) Tj ET")
    words, lines = reads(data)
    assert texts(words) == ["AB", "cd"]
    assert len(lines) == 1


def test_rotated_text_two_lines_by_leading():
    """A Td down the rotated baseline: the second Tj is another line."""
    data = page(b"BT /F1 10 Tf 0 1 -1 0 100 200 Tm (AB) Tj 0 -14 Td (cd) Tj ET")
    words, lines = reads(data)
    assert texts(words) == ["AB", "cd"]
    assert len(lines) == 2


def test_mixed_directions_on_one_page():
    """Upright text and text at 90° on one page: two lines, the upright one
    and the rotated one, each holding its own direction."""
    data = page(b"BT /F1 10 Tf 1 0 0 1 20 100 Tm (up) Tj 0 1 -1 0 200 20 Tm (side) Tj ET")
    words, lines = reads(data)
    assert texts(words) == ["up", "side"]
    assert len(lines) == 2


def test_a_turn_begins_a_line():
    """Two glyphs at 0°, then two at 90°, all beside each other: the turn
    begins the second line even where the step and off would not."""
    data = page(b"BT /F1 10 Tf 1 0 0 1 10 100 Tm (AB) Tj 0 1 -1 0 15 100 Tm (CD) Tj ET")
    words, lines = reads(data)
    assert texts(words) == ["AB", "CD"]
    assert len(lines) == 2


@pytest.mark.parametrize("degrees", [NEAR, NEAR - 0.001])
def test_a_turn_within_the_threshold_keeps_the_line(degrees):
    """Upright text, then text sloped by up to UPRIGHT_DEGREES: the turn
    stays within the threshold and both ride one line."""
    import math

    radians = math.radians(degrees)
    cos = f"{math.cos(radians):.6f}".encode()
    sin = f"{math.sin(radians):.6f}".encode()
    data = page(
        b"BT /F1 10 Tf 1 0 0 1 10 100 Tm (AB) Tj "
        b"BT /F1 10 Tf " + cos + b" " + sin + b" -" + sin + b" " + cos + b" 30 100 Tm (cd) Tj ET"
    )
    words, lines = reads(data)
    assert texts(words) == ["AB", "cd"]
    assert len(lines) == 1


def test_text_at_the_corpus_near_miss_splits():
    """The corpus holds text at 1.82° (measured): the turn from upright text
    is beyond UPRIGHT_DEGREES, so the sloped text begins its own line."""
    import math

    radians = math.radians(1.82)
    cos = f"{math.cos(radians):.6f}".encode()
    sin = f"{math.sin(radians):.6f}".encode()
    data = page(
        b"BT /F1 10 Tf 1 0 0 1 10 100 Tm (AB) Tj "
        b"BT /F1 10 Tf " + cos + b" " + sin + b" -" + sin + b" " + cos + b" 30 100 Tm (cd) Tj ET"
    )
    words, lines = reads(data)
    assert texts(words) == ["AB", "cd"]
    assert len(lines) == 2


def test_a_hyphen_at_a_rotated_lines_end():
    """A rotated line ending in a hyphen: the glyph is flagged, as upright."""
    data = page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB-) Tj ET")
    with pdfacts.open(data) as document:
        glyphs = pdfacts.glyphs(document, 0, rules=TextRules.V3)
        words, lines = reads(data)
    assert texts(words) == ["AB-"]
    assert glyphs[-1].hyphen is True
    assert len(lines) == 1


def test_rotated_white_space_holds_the_line_box_open():
    """A rotated line's box holds its white space too (V2's rule carried to
    V3): the line of `AB ` reaches past the space."""
    _, tight_lines = reads(page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB) Tj ET"))
    _, spaced_lines = reads(page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB ) Tj ET"))
    assert len(tight_lines) == len(spaced_lines) == 1
    assert spaced_lines[0].box != tight_lines[0].box


def test_upright_text_unchanged_between_v2_and_v3():
    """A page of upright text alone: V3 gives what V2 gives."""
    data = page(b"BT /F1 10 Tf 1 0 0 1 20 100 Tm (two words) Tj ET")
    assert reads(data) == reads(data, rules=TextRules.V2)


def test_the_glyphs_of_v3_are_v2s():
    """V3 changes the words and lines, not the glyphs."""
    data = page(b"BT /F1 10 Tf 0 1 -1 0 100 20 Tm (AB) Tj ET")
    with pdfacts.open(data) as document:
        second = pdfacts.glyphs(document, 0, rules=TextRules.V3)
        third = pdfacts.glyphs(document, 0, rules=TextRules.V2)
    assert second == third


def test_the_turn_measured_the_short_way_round():
    """Text at -0.49° followed by text at +0.49°: the turn is under a degree
    the short way round, 359 the long way; the short way holds the line."""
    import math

    half = 0.49
    cos = f"{math.cos(math.radians(half)):.6f}".encode()
    sin = f"{math.sin(math.radians(half)):.6f}".encode()
    data = page(
        b"BT /F1 10 Tf " + cos + b" -" + sin + b" " + sin + b" " + cos + b" 10 100 Tm (AB) Tj "
        b"BT /F1 10 Tf " + cos + b" " + sin + b" -" + sin + b" " + cos + b" 30 100 Tm (cd) Tj ET"
    )
    words, lines = reads(data)
    assert texts(words) == ["AB", "cd"]
    assert len(lines) == 1
