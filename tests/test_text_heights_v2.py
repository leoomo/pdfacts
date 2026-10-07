"""The glyph boxes of TextRules.V2 for fonts whose ascent and descent come from
a font descriptor (docs/conventions.md, "Glyphs, words and lines"): a
/Descent above 0 is read as its negation; an /Ascent at or below 0 is the top
of the descriptor's /FontBBox, the larger of its second and fourth numbers;
where that is no top above 0 either, the box of every glyph of the font is
unknown.

Each file is built byte by byte. The expected values come from the
construction: every glyph is one code shown at the size of 10 at the origin,
advancing 5 along the baseline (one width of 500); across, the /Ascent,
/Descent and /FontBBox written, in thousandths of the size, scaled to span one
size where they span less.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import pdfacts
from pdfacts import Glyph, Quad, TextRules, Unknown, UnknownReason

sys.path.insert(0, str(Path(__file__).parent))
from synthetic.builder import build, stream  # noqa: E402

HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
SIMPLE = (
    b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+H /FirstChar 65 /LastChar 65"
    b" /Widths [500] /FontDescriptor 11 0 R >>"
)
STANDARD = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding"
    b" /FontDescriptor 11 0 R >>"
)
COMPOSITE = (
    b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+H /Encoding /Identity-H"
    b" /DescendantFonts [11 0 R] >>"
)
DESCENDANT = (
    b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+H /CIDSystemInfo"
    b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 500"
    b" /FontDescriptor 12 0 R >>"
)
TYPE3 = (
    b"<< /Type /Font /Subtype /Type3 /FontMatrix [0.001 0 0 -0.001 0 0]"
    b" /FontBBox [0 100 500 -700] /Encoding << /Differences [65 /A] >>"
    b" /FirstChar 65 /LastChar 65 /Widths [500] /CharProcs << /A 13 0 R >> /Resources << >>"
    b" /FontDescriptor 11 0 R >>"
)
INVALID_HEIGHTS = (
    "the font descriptor's /Ascent is not above the baseline, nor is the top of its /FontBBox"
)
NOT_ABOVE_DESCENT = "the font's ascent is not above its descent"
NO_HEIGHTS = "the font gives no ascent or descent"
# Helvetica.afm: Ascender 718, Descender -207, which span less than an em.
HELVETICA_LOW, HELVETICA_HIGH = -207 / 925 * 10, 718 / 925 * 10


def descriptor(entries: bytes) -> bytes:
    return b"<< /Type /FontDescriptor /FontName /ABCDEF+H /Flags 32 " + entries + b" >>"


def page(objects: dict[int, bytes], content: bytes) -> bytes:
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R /F2 10 0 R >> >> >>",
            4: stream(content),
            5: HELVETICA,
            **objects,
        },
        1,
    )


def shown(objects: dict[int, bytes], content: bytes = b"BT /F2 10 Tf 0 0 Td (A) Tj ET"):
    with pdfacts.open(page(objects, content)) as document:
        return pdfacts.glyphs(document, 0, rules=TextRules.V2)


def simple(entries: bytes) -> Glyph:
    return shown({10: SIMPLE, 11: descriptor(entries)})[0]


def composite(entries: bytes) -> Glyph:
    objects = {10: COMPOSITE, 11: DESCENDANT, 12: descriptor(entries)}
    return shown(objects, b"BT /F2 10 Tf 0 0 Td <0041> Tj ET")[0]


def corners(glyph: Glyph) -> tuple[float, ...]:
    assert isinstance(glyph.box, Quad)
    return tuple(
        round(v, 6)
        for p in (glyph.box.p1, glyph.box.p2, glyph.box.p3, glyph.box.p4)
        for v in (p.x, p.y)
    )


def around(low: float, high: float, advance: float = 5.0) -> tuple[float, ...]:
    """The corners of a box from the origin to `advance`, and across from
    `low` to `high`."""
    return tuple(round(v, 6) for v in (0.0, low, advance, low, advance, high, 0.0, high))


# --- a /Descent above 0 ---------------------------------------------------------------


def test_a_descent_above_zero_is_read_as_its_negation():
    # -205 and 899 thousandths of the size span 1104: no scaling.
    assert corners(simple(b"/Ascent 899 /Descent 205")) == around(-2.05, 8.99)


def test_a_descent_below_zero_is_kept():
    # -300 and 800 span 1100.
    assert corners(simple(b"/Ascent 800 /Descent -300")) == around(-3.0, 8.0)


def test_a_descent_of_zero_is_kept():
    # 0 and 900 span 900: scaled by 1000/900.
    assert corners(simple(b"/Ascent 900 /Descent 0")) == around(0.0, 900 / 900 * 10)


def test_the_descent_of_a_composite_fonts_descendant_is_read_so_too():
    # -200 and 900 span 1100.
    assert corners(composite(b"/Ascent 900 /Descent 200")) == around(-2.0, 9.0)


# --- an /Ascent at or below 0 ---------------------------------------------------------


@pytest.mark.parametrize(
    "box",
    [b"[-243 -202 1431 745]", b"[1431 745 -243 -202]"],
    ids=["corners in order", "corners reversed"],
)
def test_an_ascent_of_zero_is_the_top_of_the_font_box(box):
    # The top 745 and the /Descent -250, not the box's bottom -202, span 995: scaled by 1000/995.
    entries = b"/Ascent 0 /Descent -250 /FontBBox " + box
    assert corners(simple(entries)) == around(-250 / 995 * 10, 745 / 995 * 10)


def test_an_ascent_below_zero_is_the_top_of_the_font_box():
    # The top 700 and the /Descent -350, not the box's bottom -400, span 1050: no scaling.
    entries = b"/Ascent -100 /Descent -350 /FontBBox [0 -400 1000 700]"
    assert corners(simple(entries)) == around(-3.5, 7.0)


def test_the_top_of_the_font_box_is_the_ascent_of_a_composite_fonts_descendant_too():
    # As above: 700 and -350 span 1050, the box's bottom being -400.
    entries = b"/Ascent 0 /Descent -350 /FontBBox [0 -400 1000 700]"
    assert corners(composite(entries)) == around(-3.5, 7.0)


def test_a_font_box_the_ascent_does_not_need_is_not_used():
    # The /Ascent of 800 and the /Descent of -300 hold, not the box's 950 and -400; they span 1100.
    entries = b"/Ascent 800 /Descent -300 /FontBBox [-100 -400 1000 950]"
    assert corners(simple(entries)) == around(-3.0, 8.0)
    assert corners(simple(b"/Ascent 800 /Descent -300 /FontBBox [0 0 0]")) == around(-3.0, 8.0)


def test_a_standard_font_takes_its_descent_from_afm_where_the_descriptor_gives_none():
    # The top 750 of the box, Helvetica.afm's Descender -207: span 957; its A
    # is 667 wide.
    found = shown({10: STANDARD, 11: descriptor(b"/Ascent 0 /FontBBox [-100 -300 1000 750]")})
    assert corners(found[0]) == around(-207 / 957 * 10, 750 / 957 * 10, 6.67)


# --- an absent /Ascent ----------------------------------------------------------------


def test_an_absent_ascent_is_not_the_top_of_the_font_box():
    # With the box's top of 700 as the ascent, the heights would be 700 and -200.
    glyph = simple(b"/Descent -200 /FontBBox [0 -200 1000 700]")
    assert glyph.box == Unknown(UnknownReason.UNREADABLE, NO_HEIGHTS)
    # What the font gives otherwise is read.
    assert (glyph.text, glyph.origin.x, glyph.size) == ("A", 0.0, 10.0)


def test_a_standard_fonts_absent_ascent_is_afms_not_the_top_of_the_font_box():
    # The descriptor's box has a top of 750; Helvetica.afm's Ascender is 718.
    found = shown({10: STANDARD, 11: descriptor(b"/FontBBox [-100 -300 1000 750]")})
    assert corners(found[0]) == around(HELVETICA_LOW, HELVETICA_HIGH, 6.67)


# --- no top above 0 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "entries",
    [
        b"/Ascent 0 /Descent -200 /FontBBox [0 -200 1000 0]",
        b"/Ascent -100 /Descent -200 /FontBBox [-500 -300 1000 -100]",
        b"/Ascent 0 /Descent -200",
        b"/Ascent 0 /Descent -200 /FontBBox [0 -200 1000]",
        b"/Ascent 0 /Descent -200 /FontBBox [0 -200 1000 700 800]",
        b"/Ascent 0 /Descent -200 /FontBBox [0 -200 /X 700]",
        b"/Ascent 0 /Descent -200 /FontBBox 700",
    ],
    ids=[
        "a top of 0",
        "a top below 0",
        "no font box",
        "three numbers",
        "five numbers",
        "a name among the numbers",
        "not an array",
    ],
)
def test_an_ascent_at_or_below_zero_without_a_top_above_zero_leaves_the_box_unknown(entries):
    glyph = simple(entries)
    assert glyph.box == Unknown(UnknownReason.INVALID, INVALID_HEIGHTS)
    # What the font gives otherwise is read.
    assert (glyph.text, glyph.origin.x, glyph.size) == ("A", 0.0, 10.0)


def test_the_box_of_every_glyph_of_the_font_is_unknown_and_that_of_no_other_font():
    objects = {10: SIMPLE, 11: descriptor(b"/Ascent 0 /Descent -200")}
    found = shown(objects, b"BT /F2 10 Tf 0 0 Td (AA) Tj /F1 10 Tf (A) Tj ET")
    assert [g.box for g in found[:2]] == [Unknown(UnknownReason.INVALID, INVALID_HEIGHTS)] * 2
    # Helvetica's A is 667 wide, and it starts where the two others end.
    assert corners(found[2]) == tuple(
        round(v, 6)
        for v in (10.0, HELVETICA_LOW, 16.67, HELVETICA_LOW)
        + (16.67, HELVETICA_HIGH, 10.0, HELVETICA_HIGH)
    )


def test_an_ascent_at_or_below_zero_is_invalid_where_the_font_gives_no_descent_too():
    # No /Descent alone would leave the box unreadable; the /Ascent of 0 comes first.
    glyph = simple(b"/Ascent 0")
    assert glyph.box == Unknown(UnknownReason.INVALID, INVALID_HEIGHTS)
    assert (glyph.text, glyph.origin.x, glyph.size) == ("A", 0.0, 10.0)


def test_a_composite_font_without_a_top_above_zero_leaves_the_box_unknown():
    glyph = composite(b"/Ascent 0 /Descent -200")
    assert glyph.box == Unknown(UnknownReason.INVALID, INVALID_HEIGHTS)


def test_a_standard_font_whose_descriptor_gives_no_ascent_above_zero_takes_none_from_afm():
    found = shown({10: STANDARD, 11: descriptor(b"/Ascent 0")})
    assert found[0].box == Unknown(UnknownReason.INVALID, INVALID_HEIGHTS)


# --- an /Ascent not above the descent -------------------------------------------------


def test_an_ascent_above_zero_that_is_not_above_the_descent_leaves_the_box_unknown():
    # 5e-324 is above 0, so the heights are valid; its quotient with 1000 underflows to 0.0.
    tiny = b"0." + b"0" * 323 + b"5"
    glyph = simple(b"/Ascent " + tiny + b" /Descent 0")
    assert glyph.box == Unknown(UnknownReason.INVALID, NOT_ABOVE_DESCENT)
    # What the font gives otherwise is read.
    assert (glyph.text, glyph.origin.x, glyph.size) == ("A", 0.0, 10.0)


# --- a Type 3 font --------------------------------------------------------------------


def test_a_type3_font_takes_its_heights_from_its_font_box_whatever_a_descriptor_says():
    objects = {10: TYPE3, 11: descriptor(b"/Ascent 0 /Descent 205"), 13: stream(b"500 0 d0")}
    found = shown(objects, b"BT /F2 10 Tf 0 0 Td (A) Tj ET")
    # The box's y values 100 and -700 through d = -0.001: -0.1 and 0.7, which
    # span 0.8 of the size: scaled by 1/0.8; width 500 * 0.001 = 0.5.
    assert corners(found[0]) == around(-0.1 / 0.8 * 10, 0.7 / 0.8 * 10)
