"""glyphs() with TextRules.V2: the codes the content shows, read on QPDF's
tokens (docs/conventions.md, "Glyphs, words and lines").

Each file is built byte by byte. The expected values come from the
construction: the positions from the operators written, the widths from
/Widths or from Helvetica.afm's WX (A 667, B 667, space 278, hyphen 333), the
heights from the font's ascent and descent (Helvetica.afm: 718 and -207,
which span less than an em and are scaled to span one).
"""

from __future__ import annotations

import dataclasses
import math
import sys
from pathlib import Path

import pytest

import pdfacts
from pdfacts import Glyph, LimitExceededError, Limits, Quad, TextRules, Unknown, UnknownReason

sys.path.insert(0, str(Path(__file__).parent))
from synthetic.builder import build, build_with_object_stream, stream  # noqa: E402

HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
# A font with widths for the tab and the space: a code below 32 the encoding
# names no glyph for, and FirstChar 9 gives its width.
TABS = (
    b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Tabs /Encoding /WinAnsiEncoding"
    b" /FirstChar 9 /LastChar 32 /Widths [400 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 250]"
    b" /FontDescriptor 9 0 R >>"
)
TABS_DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /ABCDEF+Tabs /Flags 32 /FontBBox [0 -200 1000 900]"
    b" /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>"
)


def page(
    content: bytes,
    *,
    rotate: int = 0,
    fonts: bytes = b"/F1 5 0 R",
    xobjects: bytes = b"",
    objects: dict[int, bytes] | None = None,
) -> bytes:
    resources = b"/Font << " + fonts + b" >>"
    if xobjects:
        resources += b" /XObject << " + xobjects + b" >>"
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Rotate %d"
            b" /Contents 4 0 R /Resources << %s >> >>" % (rotate, resources),
            4: stream(content),
            5: HELVETICA,
            8: TABS,
            9: TABS_DESCRIPTOR,
            **(objects or {}),
        },
        1,
    )


def v2(data: bytes, limits: Limits | None = None):
    with pdfacts.open(data, limits=limits) as document:
        return pdfacts.glyphs(document, 0, rules=TextRules.V2)


def corners(glyph: Glyph) -> tuple[float, ...]:
    assert isinstance(glyph.box, Quad)
    return tuple(
        round(v, 6)
        for p in (glyph.box.p1, glyph.box.p2, glyph.box.p3, glyph.box.p4)
        for v in (p.x, p.y)
    )


def rechecked(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value)(
            **{f.name: rechecked(getattr(value, f.name)) for f in dataclasses.fields(value)}
        )
    if isinstance(value, tuple):
        return tuple(rechecked(item) for item in value)
    return value


# --- positions, boxes, text ----------------------------------------------------------


def test_a_line_of_text():
    found = v2(page(b"BT /F1 10 Tf 100 700 Td (A B) Tj ET"))
    assert [g.text for g in found] == ["A", " ", "B"]
    # A: 667/1000 * 10; the space: 278/1000 * 10.
    assert [(round(g.origin.x, 6), g.origin.y) for g in found] == [
        (100.0, 700.0),
        (106.67, 700.0),
        (109.45, 700.0),
    ]
    assert all(
        (g.direction, g.size, g.upright, g.render_mode) == (0.0, 10.0, True, 0) for g in found
    )
    # From the origin to the advance; across, 718 and -207 scaled by 1000/925
    # to span the size of 10.
    low, high = -207 / 925 * 10, 718 / 925 * 10
    assert corners(found[0]) == tuple(
        round(v, 6)
        for v in (100, 700 + low, 106.67, 700 + low, 106.67, 700 + high, 100, 700 + high)
    )


def test_a_font_whose_ascent_and_descent_span_an_em_keeps_them():
    found = v2(page(b"BT /F8 10 Tf 0 0 Td (\t) Tj ET", fonts=b"/F8 8 0 R"))
    # 900 and -200 span more than an em: no scaling.
    assert corners(found[0]) == (0.0, -2.0, 4.0, -2.0, 4.0, 9.0, 0.0, 9.0)


def test_the_white_space_after_a_label_is_glyphs():
    found = v2(
        page(b"BT /F1 10 Tf 0 0 Td (A:) Tj /F8 10 Tf (\t \t) Tj ET", fonts=b"/F1 5 0 R /F8 8 0 R")
    )
    assert [g.text for g in found] == ["A", ":", "\t", " ", "\t"]
    # Helvetica: A 667, colon 278; the tab 400 and the space 250 of /Widths.
    xs = [round(g.origin.x, 6) for g in found]
    assert xs == [0.0, 6.67, 9.45, 13.45, 15.95]
    assert corners(found[-1])[2] == round(15.95 + 4.0, 6)


def test_the_content_order_is_kept():
    found = v2(page(b"BT /F1 10 Tf 300 0 Td (B) Tj -300 0 Td (A) Tj ET"))
    assert [(g.text, g.origin.x) for g in found] == [("B", 300.0), ("A", 0.0)]


def test_a_hyphen_at_the_end_of_a_line():
    found = v2(page(b"BT /F1 10 Tf 0 100 Td (A-) Tj 0 -20 Td (B-B) Tj ET"))
    assert [(g.text, g.hyphen) for g in found] == [
        ("A", False),
        ("-", True),
        ("B", False),
        ("-", False),
        ("B", False),
    ]


def test_a_soft_hyphen_at_the_end_of_a_line():
    to_unicode = stream(b"1 beginbfchar <41> <00AD> endbfchar")
    font = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Soft /FirstChar 65 /LastChar 65"
        b" /Widths [300] /ToUnicode 11 0 R /FontDescriptor 9 0 R >>"
    )
    found = v2(
        page(
            b"BT /F2 10 Tf 0 0 Td (A) Tj ET",
            fonts=b"/F2 10 0 R",
            objects={10: font, 11: to_unicode},
        )
    )
    assert [(g.text, g.hyphen) for g in found] == [("­", True)]


def test_text_turned_by_the_page_is_upright_as_the_page_is_shown():
    # A page turned by 90: text whose baseline points up is upright as shown.
    found = v2(page(b"BT /F1 10 Tf 0 1 -1 0 300 100 Tm (A) Tj ET", rotate=90))
    assert (found[0].direction, found[0].upright) == (90.0, True)
    found = v2(page(b"BT /F1 10 Tf 0 1 -1 0 300 100 Tm (A) Tj ET"))
    assert (found[0].direction, found[0].upright) == (90.0, False)


def test_the_render_mode_and_an_invalid_one():
    found = v2(page(b"BT /F1 10 Tf 3 Tr (A) Tj 9 Tr (A) Tj ET"))
    assert found[0].render_mode == 3
    assert found[1].render_mode == Unknown(
        UnknownReason.INVALID, "Tr sets a render mode ISO 32000-1 does not allow"
    )


# A real of 201 digits, which a float holds; two of them multiplied do not.
BIG = b"1" + b"0" * 200 + b".0"
BEYOND = Unknown(UnknownReason.UNREADABLE, "the glyph lies beyond what a float holds")


def test_a_glyph_beyond_what_a_float_holds():
    found = v2(page(BIG + b" 0 0 1 0 0 cm " + BIG + b" 0 0 1 0 0 cm BT /F1 10 Tf (A) Tj ET"))
    assert (found[0].origin, found[0].size, found[0].box) == (BEYOND, BEYOND, BEYOND)
    found = v2(page(b"BT /F1 " + BIG + b" Tf [(A) -" + BIG + b" (A)] TJ ET"))
    assert found[1].origin == BEYOND


def test_a_box_beyond_what_a_float_holds_at_a_known_origin():
    # Width 1e300 / 1000 at size 1e100 reaches past what a float holds; the
    # origin and the size stay known.
    huge = b"1" + b"0" * 100 + b".0"
    font = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+X /FirstChar 65 /LastChar 65"
        b" /Widths [1" + b"0" * 300 + b".0] /FontDescriptor 9 0 R >>"
    )
    found = v2(page(b"BT /F2 " + huge + b" Tf (A) Tj ET", fonts=b"/F2 10 0 R", objects={10: font}))
    assert (found[0].origin.x, found[0].box) == (0.0, BEYOND)


def test_a_font_without_ascent_or_descent_and_one_whose_ascent_is_below():
    without = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+X /FirstChar 65 /LastChar 65"
        b" /Widths [500] >>"
    )
    below = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Y /FirstChar 65 /LastChar 65"
        b" /Widths [500] /FontDescriptor 11 0 R >>"
    )
    descriptor = (
        b"<< /Type /FontDescriptor /FontName /ABCDEF+Y /Flags 32 /Ascent -100 /Descent 0 >>"
    )
    found = v2(
        page(
            b"BT /F2 10 Tf (A) Tj /F3 10 Tf (A) Tj ET",
            fonts=b"/F2 10 0 R /F3 12 0 R",
            objects={10: without, 11: descriptor, 12: below},
        )
    )
    assert found[0].box == Unknown(UnknownReason.UNREADABLE, "the font gives no ascent or descent")
    # An /Ascent below the baseline, and no /FontBBox to take the top from.
    assert found[1].box == Unknown(
        UnknownReason.INVALID,
        "the font descriptor's /Ascent is not above the baseline, nor is the top of its /FontBBox",
    )


def test_a_stream_the_text_needs_that_qpdf_cannot_decode():
    font = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+X /FirstChar 65 /LastChar 65"
        b" /Widths [500] /ToUnicode 11 0 R >>"
    )
    broken = b"<< /Length 6 /Filter /FlateDecode >>\nstream\nabcdef\nendstream"
    found = v2(page(b"BT /F2 10 Tf (A) Tj ET", fonts=b"/F2 10 0 R", objects={10: font, 11: broken}))
    assert found.reason is UnknownReason.UNREADABLE
    assert found.detail.startswith("QPDF cannot read a stream the text needs")


def test_a_code_without_text():
    found = v2(page(b"BT /F1 10 Tf (\x01) Tj ET", fonts=b"/F1 8 0 R"))
    assert found[0].text == Unknown(UnknownReason.UNREADABLE, "the font maps the code to no text")


def test_a_type3_font():
    font = (
        b"<< /Type /Font /Subtype /Type3 /FontMatrix [0.001 0 0 -0.001 0 0]"
        b" /FontBBox [0 100 500 -700] /Encoding << /Differences [65 /A] >>"
        b" /FirstChar 65 /LastChar 65 /Widths [500] /CharProcs << /A 11 0 R >> /Resources << >> >>"
    )
    found = v2(
        page(
            b"BT /F3 10 Tf 0 0 Td (A) Tj ET",
            fonts=b"/F3 10 0 R",
            objects={10: font, 11: stream(b"500 0 d0")},
        )
    )
    # Width 500 * 0.001 = 0.5 of the size; the box's y values 100 and -700
    # through d = -0.001 give a descent of -0.1 and an ascent of 0.7, spanning
    # less than an em: scaled by 1/0.8.
    assert corners(found[0]) == (0.0, -1.25, 5.0, -1.25, 5.0, 8.75, 0.0, 8.75)


def test_two_bytes_of_identity_h():
    descendant = (
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+Two /CIDSystemInfo"
        b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 /W [65 [600]]"
        b" /FontDescriptor 9 0 R >>"
    )
    font = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Two /Encoding /Identity-H"
        b" /DescendantFonts [11 0 R] /ToUnicode 12 0 R >>"
    )
    cmap = stream(b"1 beginbfrange <0041> <0042> <0061> endbfrange")
    found = v2(
        page(
            b"BT /F4 10 Tf 0 0 Td <00410042> Tj ET",
            fonts=b"/F4 10 0 R",
            objects={10: font, 11: descendant, 12: cmap},
        )
    )
    assert [(g.text, g.origin.x) for g in found] == [("a", 0.0), ("b", 6.0)]


def test_the_facts_are_those_the_constructors_make():
    found = v2(page(b"BT /F1 10 Tf 2 Tc 50 Tz 100 700 Td [(A) -300 (B-)] TJ ET"))
    assert rechecked(found) == found
    assert repr(rechecked(found)) == repr(found)


# --- forms --------------------------------------------------------------------------


def form(content: bytes, entries: bytes = b"") -> bytes:
    return stream(content, b"/Type /XObject /Subtype /Form /BBox [0 0 600 800] " + entries)


def test_a_form_is_read_where_it_is_drawn_each_time():
    found = v2(
        page(
            b"q 1 0 0 1 10 0 cm /X1 Do Q q 1 0 0 1 0 50 cm /X1 Do Q",
            xobjects=b"/X1 10 0 R",
            objects={10: form(b"BT /F1 10 Tf (A) Tj ET", b"/Matrix [1 0 0 1 5 5]")},
        )
    )
    assert [(g.origin.x, g.origin.y) for g in found] == [(15.0, 5.0), (5.0, 55.0)]


def test_a_forms_matrix_comes_before_the_matrix_where_it_is_drawn():
    found = v2(
        page(
            b"q 1 0 0 1 100 0 cm /X1 Do Q",
            xobjects=b"/X1 10 0 R",
            objects={10: form(b"BT /F1 10 Tf 10 20 Td (AB) Tj ET", b"/Matrix [2 0 0 2 0 0]")},
        )
    )
    # (10, 20) scaled by 2, then moved by 100: (120, 40); B 667/1000 * 10 * 2
    # further, and the size 20.
    assert [(round(g.origin.x, 6), g.origin.y, g.size) for g in found] == [
        (120.0, 40.0, 20.0),
        (133.34, 40.0, 20.0),
    ]


def test_capital_td_sets_the_leading_that_t_star_and_quote_take():
    found = v2(page(b"BT /F1 10 Tf 0 100 Td 0 -14 TD (A) Tj T* (B) Tj (C) ' ET"))
    assert [(g.text, g.origin.y) for g in found] == [("A", 86.0), ("B", 72.0), ("C", 58.0)]


def test_double_quote_sets_the_word_spacing_then_the_character_spacing():
    found = v2(page(b'BT /F1 10 Tf 0 100 Td 14 TL 3 1 (A A) " ET'))
    # On the next line: A, then the space after 6.67 + 1, then A after the
    # space's 2.78 + 1 + 3.
    assert [(round(g.origin.x, 6), g.origin.y) for g in found] == [
        (0.0, 86.0),
        (7.67, 86.0),
        (14.45, 86.0),
    ]


def test_a_form_keeps_its_own_resources_and_takes_the_pages_without_them():
    fonts = b"/Font << /G 8 0 R >>"
    found = v2(
        page(
            b"/X1 Do /X2 Do",
            fonts=b"/F1 5 0 R /F8 8 0 R",
            xobjects=b"/X1 10 0 R /X2 11 0 R",
            objects={
                10: form(b"BT /G 10 Tf (\t) Tj ET", b"/Resources << " + fonts + b" >>"),
                11: form(b"BT /F1 10 Tf (A) Tj ET"),
            },
        )
    )
    assert [g.text for g in found] == ["\t", "A"]


def test_the_text_state_a_form_sets_ends_with_it():
    found = v2(
        page(
            b"BT /F1 10 Tf ET /X1 Do BT (AA) Tj ET",
            fonts=b"/F1 5 0 R /F8 8 0 R",
            xobjects=b"/X1 10 0 R",
            objects={10: form(b"BT /F8 10 Tf 5 Tc (\t) Tj ET")},
        )
    )
    # After the form, Helvetica at 10 again, without the form's spacing:
    # the second A 667/1000 * 10 after the first.
    assert [g.text for g in found] == ["\t", "A", "A"]
    assert [round(g.origin.x, 6) for g in found[1:]] == [0.0, 6.67]


def test_a_forms_extra_q_leaves_the_state_it_was_drawn_in():
    found = v2(
        page(
            b"1 0 0 1 50 0 cm q 1 0 0 1 100 0 cm /X1 Do BT /F1 10 Tf 0 0 Td (A) Tj ET Q",
            xobjects=b"/X1 10 0 R",
            objects={10: form(b"Q Q")},
        )
    )
    assert [g.origin.x for g in found] == [150.0]


def test_a_forms_matrix_that_is_not_six_numbers_is_invalid():
    found = v2(page(b"/X1 Do", xobjects=b"/X1 10 0 R", objects={10: form(b"", b"/Matrix [1 0 0]")}))
    assert found == Unknown(UnknownReason.INVALID, "a form whose /Matrix is not six numbers")


def test_a_form_that_draws_itself_is_invalid():
    found = v2(
        page(
            b"/X1 Do",
            xobjects=b"/X1 10 0 R",
            objects={10: form(b"/X1 Do", b"/Resources << /XObject << /X1 10 0 R >> >>")},
        )
    )
    assert found == Unknown(UnknownReason.INVALID, "a form draws itself")


def test_a_form_deeper_than_40_levels_is_unreadable():
    objects = {}
    for level in range(42):
        number = 10 + level
        inner = b"/X Do" if level < 41 else b"BT /F1 10 Tf (A) Tj ET"
        objects[number] = form(
            inner, b"/Resources << /XObject << /X %d 0 R >> /Font << /F1 5 0 R >> >>" % (number + 1)
        )
    found = v2(page(b"/X Do", xobjects=b"/X 10 0 R", objects=objects))
    assert found == Unknown(UnknownReason.UNREADABLE, "a form lies deeper than 40 levels")


def test_text_forty_forms_deep_is_read():
    objects = {}
    for level in range(40):
        number = 10 + level
        inner = b"/X Do" if level < 39 else b"BT /F1 10 Tf (A) Tj ET"
        objects[number] = form(
            inner, b"/Resources << /XObject << /X %d 0 R >> /Font << /F1 5 0 R >> >>" % (number + 1)
        )
    found = v2(page(b"/X Do", xobjects=b"/X 10 0 R", objects=objects))
    assert [g.text for g in found] == ["A"]


def test_an_image_draws_no_text_and_an_inline_image_is_passed_over():
    image = stream(
        b"\x00",
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray"
        b" /BitsPerComponent 8",
    )
    found = v2(
        page(
            b"/I1 Do BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI BT /F1 10 Tf (A) Tj ET",
            xobjects=b"/I1 10 0 R",
            objects={10: image},
        )
    )
    assert [g.text for g in found] == ["A"]


# --- what makes the page's text unknown ----------------------------------------------


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        (b"BT (A) Tj ET", "the content shows text before Tf sets a font"),
        (b"BT /F9 10 Tf (A) Tj ET", "Tf names a font the resources do not hold"),
        (b"BT /F1 10 Tf 5 Tj ET", "Tj with operands ISO 32000-1 does not allow"),
        (b"BT /F1 Tf (A) Tj ET", "Tf with operands ISO 32000-1 does not allow"),
        (b"BT /F1 10 Tf [(A) /N] TJ ET", "TJ with operands ISO 32000-1 does not allow"),
        (b"BT /F1 10 Tf 1.5 Tr (A) Tj ET", "Tr with operands ISO 32000-1 does not allow"),
        (b"1 0 0 cm", "cm with operands ISO 32000-1 does not allow"),
    ],
)
def test_text_written_against_the_specification_is_invalid(content, detail):
    assert v2(page(content)) == Unknown(UnknownReason.INVALID, detail)


def test_a_font_pdfacts_does_not_read():
    font = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-V"
        b" /DescendantFonts [11 0 R] >>"
    )
    descendant = b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /X >>"
    found = v2(
        page(b"BT /F4 10 Tf <0041> Tj ET", fonts=b"/F4 10 0 R", objects={10: font, 11: descendant})
    )
    assert found == Unknown(UnknownReason.NOT_READ, "pdfacts does not read the CMap /Identity-V")


def test_a_font_entry_written_against_the_specification():
    font = b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+X /FirstChar 32 /Widths 5 >>"
    found = v2(page(b"BT /F2 10 Tf (A) Tj ET", fonts=b"/F2 10 0 R", objects={10: font}))
    assert found == Unknown(
        UnknownReason.INVALID, "a font whose /Widths ISO 32000-1 does not allow"
    )


@pytest.mark.parametrize(
    "content",
    [
        stream(b"BT /F1 12 Tf 10 10 Td (abc Tj ET"),
        b"<< /Length 5 >>\nstream\nBT /F1 12 Tf 10 10 Td (ab) Tj ET\nendstream",
    ],
    ids=["an unterminated string", "a wrong /Length"],
)
def test_content_qpdf_warns_about_is_unreadable_and_its_warning_stays_inside(content, recwarn):
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>",
            4: content,
            5: HELVETICA,
        },
        1,
    )
    found = v2(data)
    assert isinstance(found, Unknown) and found.reason is UnknownReason.UNREADABLE
    assert not [w for w in recwarn if "stream" in str(w.message)]


def test_every_value_from_qpdf_unknown_leaves_the_text_unknown():
    data = build_with_object_stream(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 5 0 R"
            b" /Resources << /Font << /F1 6 0 R >> >> /UserUnit 9 0 R >>",
            5: stream(b"BT /F1 10 Tf (A) Tj ET"),
            6: HELVETICA,
        },
        [(9, b"2"), (9, b"3")],
        {9: 0},
        1,
    )
    with pdfacts.open(data) as document:
        found = pdfacts.glyphs(document, 0, rules=TextRules.V2)
        assert found == document._qpdf_unknown
        assert found.reason is UnknownReason.INVALID


# --- limits and arguments -----------------------------------------------------------


def test_the_codes_count_against_the_item_limit():
    data = page(b"BT /F1 10 Tf (AB) Tj ET")
    assert len(v2(data, Limits(max_items_per_page=2))) == 2
    with pytest.raises(LimitExceededError):
        v2(data, Limits(max_items_per_page=1))


def test_the_forms_are_counted_before_the_text_is_read():
    data = page(
        b"/X1 Do /X1 Do", xobjects=b"/X1 10 0 R", objects={10: form(b"BT /F1 10 Tf (A) Tj ET")}
    )
    with pytest.raises(LimitExceededError):
        v2(data, Limits(max_forms_per_page=1))


def test_the_rules_by_their_value_and_v1_by_default():
    data = page(b"BT /F1 10 Tf (A B) Tj ET")
    with pdfacts.open(data) as document:
        assert pdfacts.glyphs(document, 0, rules="v2") == pdfacts.glyphs(
            document, 0, rules=TextRules.V2
        )
        first = pdfacts.glyphs(document, 0)
        assert (
            first
            == pdfacts.glyphs(document, 0, rules=TextRules.V1)
            == pdfacts.glyphs(document, 0, rules="v1")
        )
        assert [g.text for g in first] == ["A", " ", "B"]
        with pytest.raises(ValueError):
            pdfacts.glyphs(document, 0, rules="v3")


def test_no_glyph_takes_the_c_librarys_trigonometry(monkeypatch):
    data = page(b"BT /F1 10 Tf 0.9998 0.0175 -0.0175 0.9998 100 100 Tm (AB) Tj ET", rotate=90)
    before = v2(data)

    def fails(*arguments):
        raise AssertionError("the C library's trigonometry was taken")

    for name in ("atan", "atan2", "sin", "cos", "tan"):
        monkeypatch.setattr(math, name, fails)
    assert v2(data) == before
