"""The M4b gate, rendering: the text alone, the word boxes against the ink, the owners
of the components, the tolerance, the mapping of the rendering, and the boxes of V2
against those of the descriptor's height.

The measurements are tried on pictures drawn here, and on files whose glyphs are
rectangles of a size written into them (Type 3 fonts), so that the ink is known
from the construction; the numbers expected are what was written.
"""

from __future__ import annotations

import dataclasses
import math
import sys
from pathlib import Path

import numpy as np
import pypdfium2
import pytest

import pdfacts
from m4b_cases import (
    SMALL,
    file_of,
    geometry_of,
    squares,
    upright,
    word_of,
    written,
)
from pdfacts import Line, Rect, Unknown, Word

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m4b  # noqa: E402

from synthetic.builder import stream  # noqa: E402


def measured_page(data: bytes):
    """The words of the first page of a file placed and measured against the rendering
    of its text alone."""
    with pdfacts.open(data) as document:
        geometry = document.pages[0].geometry
        facts = m4b.read_page(document, 0)
    assert not isinstance(facts, Unknown)
    pdf = pypdfium2.PdfDocument(data)
    try:
        page = pdf[0]
        try:
            ink = m4b.text_ink(page)
        finally:
            page.close()
    finally:
        pdf.close()
    found = m4b.components_of(ink)
    placed, measured = m4b.measure_words(geometry, facts, found)
    return placed, measured, found


def picture(*rows: str) -> np.ndarray:
    """Ink drawn as text: # is ink."""
    return np.array([[character == "#" for character in row] for row in rows])


def placed_at(rect, size=10.0) -> m4b.Placed:
    return m4b.Placed("measured", size, "none", rect)


# A rectangle over the word, one beside it that no word's band reaches, an inline image, and a
# form that holds a word and a rectangle of its own.
PAINTED = squares(
    b"BT /F1 10 Tf 50 60 Td (A) Tj ET"
    b" 0 g 40 55 30 20 re f 0 g 120 10 30 20 re f"
    b" q 50 0 0 50 100 20 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI Q"
    b" /Fm Do",
    resources=b"/Font << /F1 5 0 R >> /XObject << /Fm 9 0 R >>",
    objects={
        9: stream(
            b"0 g 150 80 20 10 re f BT /F1 10 Tf 100 70 Td (A) Tj ET",
            b"/Type /XObject /Subtype /Form /BBox [0 0 200 100]"
            b" /Resources << /Font << /F1 5 0 R >> >>",
        )
    },
)


def test_1_the_text_alone_is_rendered_paths_images_and_forms_left_out():
    """Two words of 10 x 7 at 4 to the point: 40 x 28 = 1120 pixels of ink each, and no other."""
    placed, measured, found = measured_page(PAINTED)
    assert found.ink == 2 * 1120
    assert found.count == 2
    assert measured.own.tolist() == [1120, 1120]
    assert (measured.unattributed_components, measured.unattributed_pixels) == (0, 0)
    counts, good = m4b.judge(placed, measured)
    assert (counts["judged"], counts["correct"]) == (2, 2)
    assert good.tolist() == [True, True]


def test_2_the_background_is_transparent_and_annotations_are_not_drawn():
    """An annotation whose appearance fills a square beside the word adds no ink."""
    data = squares(
        b"BT /F1 10 Tf 50 60 Td (A) Tj ET",
        entries=b"/Annots [9 0 R]",
        objects={
            9: b"<< /Type /Annot /Subtype /Square /Rect [120 10 150 30] /AP << /N 10 0 R >> >>",
            10: stream(b"0 g 0 0 30 20 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 30 20]"),
        },
    )
    _, measured, found = measured_page(data)
    assert found.ink == 1120
    assert measured.own.tolist() == [1120]


def test_3_a_pixel_is_ink_from_an_opacity_of_64():
    """A rectangle 40 pixels wide whose left edge leaves 3 / 16 of its first pixel column covered,
    an opacity of 48, and 13 / 16 of the column after the fortieth: 40 columns are ink. One that
    leaves 5 / 16 of its first column covered, 80, and 11 / 16 of the last: 41."""
    faint = squares(b"BT /F1 10 Tf 50.203125 60 Td (A) Tj ET")
    firmer = squares(b"BT /F1 10 Tf 50.171875 60 Td (A) Tj ET")
    assert measured_page(faint)[2].ink == 40 * 28
    assert measured_page(firmer)[2].ink == 41 * 28


def test_4_a_plain_word_in_a_standard_font_is_correct():
    """ "Hello" at size 20 at (20, 40) in Helvetica: the box runs from the origin to the
    advance, widths 722 556 222 222 556, and across from the descent to the ascent, 207 and 718
    thousandths scaled to span one em (Helvetica.afm)."""
    data = file_of(b"BT /F1 20 Tf 20 40 Td (Hello) Tj ET", media=SMALL)
    placed, measured, _ = measured_page(data)
    (word,) = placed
    assert (word.kind, word.size, word.invisible) == ("measured", 20.0, "none")
    x0, y0, x1, y1 = word.rect
    assert x0 == 80
    assert x1 == math.ceil(4 * (20 + 20 * 2278 / 1000))
    assert y0 == math.floor(4 * (100 - (40 + 20 * 718 / 925)))
    assert y1 == math.ceil(4 * (100 - (40 - 20 * 207 / 925)))
    assert measured.own[0] > 0
    counts, good = m4b.judge(placed, measured)
    assert (counts["judged"], counts["correct"], counts["into another line"]) == (1, 1, 0)
    assert good.tolist() == [True]


def test_5_a_glyph_drawn_above_its_font_bbox_fails_vertically_by_the_cut():
    """Type 3, /FontBBox up to 1000, the rectangle up to 1200: at size 10, 2 above the box,
    8 pixels."""
    placed, measured, _ = measured_page(squares(b"BT /F1 10 Tf 50 30 Td (A) Tj ET", height=1200))
    assert placed[0].rect == (200, 240, 240, 280)
    assert abs(int(measured.up[0]) - 8) <= 1
    assert (measured.down[0], measured.left[0], measured.right[0]) == (0, 0, 0)
    counts, good = m4b.judge(placed, measured)
    assert (counts["judged"], counts["failing vertically"], counts["correct"]) == (1, 1, 0)
    assert counts["failing horizontally"] == 0
    assert good.tolist() == [False]


def test_6_a_glyph_drawn_beside_its_advance_fails_horizontally_by_the_cut():
    """The rectangle is 1000 wide and the width written 500: the ink reaches 5 beyond the box
    to the right, 20 pixels."""
    font = (
        b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000]"
        b" /FontMatrix [0.001 0 0 0.001 0 0] /CharProcs << /A 7 0 R >>"
        b" /Encoding << /Type /Encoding /Differences [65 /A] >>"
        b" /FirstChar 65 /LastChar 65 /Widths [500] /Resources << >> >>"
    )
    data = file_of(
        b"BT /F1 10 Tf 50 30 Td (A) Tj ET",
        font=font,
        objects={7: stream(b"500 0 d0 0 0 1000 1000 re f")},
        media=SMALL,
    )
    placed, measured, _ = measured_page(data)
    assert placed[0].rect == (200, 240, 220, 280)
    assert abs(int(measured.right[0]) - 20) <= 1
    assert (measured.up[0], measured.down[0], measured.left[0]) == (0, 0, 0)
    counts, _ = m4b.judge(placed, measured)
    assert (counts["failing horizontally"], counts["failing vertically"]) == (1, 0)


def test_7_invisible_text_is_not_judged_and_a_visible_word_without_ink_is_not_correct():
    """Word 0, "A" in render mode 3: all invisible. Word 1, "AB" with A invisible and B visible
    but with no ink, and word 2, "B" alone, are visible words without ink of their own."""
    data = squares(
        b"BT /F1 10 Tf 3 Tr 10 80 Td (A) Tj ET"
        b" BT /F1 10 Tf 3 Tr 10 50 Td (A) Tj 0 Tr (B) Tj ET"
        b" BT /F1 10 Tf 10 20 Td (B) Tj ET"
    )
    placed, measured, _ = measured_page(data)
    assert [word.invisible for word in placed] == ["all", "some", "none"]
    assert measured.own.tolist() == [0, 0, 0]
    counts, good = m4b.judge(placed, measured)
    assert (counts["measured"], counts["no ink, all invisible"]) == (3, 1)
    assert (counts["judged"], counts["judged without ink of their own"]) == (2, 2)
    assert (counts["correct"], counts["failing vertically"], counts["failing horizontally"]) == (
        0,
        0,
        0,
    )
    assert good.tolist() == [False, False, False]


def test_8_an_invisible_word_under_a_visible_one_takes_none_of_its_ink():
    """ "A" in render mode 3, then "A" at the same place: two boxes holding the ink alike, a tie
    that the lower word would win. The invisible word paints nothing and owns nothing; the
    visible one has the ink, is judged and correct."""
    data = squares(b"BT /F1 10 Tf 3 Tr 50 60 Td (A) Tj ET BT /F1 10 Tf 0 Tr 50 60 Td (A) Tj ET")
    placed, measured, _ = measured_page(data)
    assert [(word.kind, word.invisible) for word in placed] == [
        ("measured", "all"),
        ("measured", "none"),
    ]
    assert measured.own.tolist() == [0, 1120]
    counts, good = m4b.judge(placed, measured)
    assert (counts["no ink, all invisible"], counts["judged"], counts["correct"]) == (1, 1, 1)
    assert counts["judged without ink of their own"] == 0
    assert good.tolist() == [False, True]


def overlapping_lines(gap: float) -> bytes:
    """Two words "A" in lines `gap` ems apart, the second below the first: a square 1 em wide
    and 0.7 tall on a box 1 em tall, so that the second's box reaches 1 - gap em up into the
    first's ink."""
    return squares(
        b"BT /F1 10 Tf 50 60 Td (A) Tj ET BT /F1 10 Tf 50 %s Td (A) Tj ET"
        % (b"%.2f" % (60 - 10 * gap))
    )


def test_9_ink_of_another_line_deeper_than_a_tenth_of_an_em_is_counted_apart():
    """The lines 0.85 em apart: the second word's box reaches 0.15 em up, 1.5 or 6 pixels, into
    the first line's ink. Still, both boxes hold their own ink."""
    placed, measured, _ = measured_page(overlapping_lines(0.85))
    assert [word.rect for word in placed] == [(200, 120, 240, 160), (200, 154, 240, 194)]
    assert measured.own.tolist() == [1120, 1120]
    assert measured.depth.tolist() == [0, 6]
    counts, good = m4b.judge(placed, measured)
    assert (counts["judged"], counts["correct"], counts["into another line"]) == (2, 2, 1)
    assert good.tolist() == [True, False]


def test_10_ink_of_another_line_within_a_tenth_of_an_em_is_not():
    """The lines 0.95 em apart: 0.05 em, 2 pixels."""
    placed, measured, _ = measured_page(overlapping_lines(0.95))
    assert measured.depth.tolist() == [0, 2]
    counts, good = m4b.judge(placed, measured)
    assert (counts["correct"], counts["into another line"]) == (2, 0)
    assert good.tolist() == [True, True]


def test_11_a_word_on_a_rotated_page_is_mapped_and_correct():
    """/Rotate 90 on a page of 200 x 100: user (x, y) is displayed at (y, 200 - x), and the pixel
    at (4y, 4x). The text runs up the page, 90 degrees, and is shown left to right. The glyph
    at (50, 30) covers user x 40 to 50 and y 30 to 40, pixels 120 to 160 across and 160 to 200
    down."""
    data = squares(b"BT /F1 10 Tf 0 1 -1 0 50 30 Tm (A) Tj ET", height=1000, entries=b"/Rotate 90")
    placed, measured, found = measured_page(data)
    assert found.labels.shape == (800, 400)
    assert placed[0].rect == (120, 160, 160, 200)
    assert measured.own.tolist() == [1600]
    counts, good = m4b.judge(placed, measured)
    assert (counts["correct"], counts["not horizontal"]) == (1, 0)
    assert good.tolist() == [True]


def test_12_a_page_of_no_whole_number_of_pixels_is_mapped_by_the_renderings_own_scale():
    """100.125 x 50.125 is 400.5 x 200.5 pixels at 4 to the point. PDFium makes the rendering
    401 x 201 and stretches the page over it: 401 / 100.125 across and 201 / 50.125 down, to the
    point. A glyph 2 wide and 2 tall at (98, 0.125) lies from 98 * 401 / 100.125 = 392.49 to
    100 * 401 / 100.125 = 400.50, columns 392 to 400; and from 48 * 201 / 50.125 = 192.48 down to
    50 * 201 / 50.125 = 200.50, rows 192 to 200: the ink fills the box, edge pixels that half
    a pixel covers included. At exactly 4 to the point its box would end at column 400 and at
    row 200, a pixel short of its ink each way."""
    data = squares(b"BT /F1 2 Tf 98 0.125 Td (A) Tj ET", height=1000, media=b"[0 0 100.125 50.125]")
    placed, measured, found = measured_page(data)
    assert found.labels.shape == (201, 401)
    assert placed[0].rect == (392, 192, 401, 201)
    assert found.boxes.tolist() == [[192, 201, 392, 401]]
    counts, _ = m4b.judge(placed, measured)
    assert (counts["correct"], counts["failing horizontally"]) == (1, 0)
    assert counts["failing vertically"] == 0


@pytest.mark.parametrize(
    ("rotation", "direction", "horizontal"),
    [
        (0, 0.0, True),
        (0, 90.0, False),
        (0, 180.0, False),
        (0, 270.0, False),
        (90, 90.0, True),
        (90, 0.0, False),
        (90, 180.0, False),
        (90, 270.0, False),
    ],
)
def test_13_a_word_is_measured_where_its_first_glyph_runs_along_x_as_shown(
    rotation, direction, horizontal
):
    """On a page not turned a glyph at 0 degrees runs along +x as shown; on a page turned by 90, one
    at 90 degrees. The rest, backwards among them, are not horizontal."""
    first = upright("A", 40.0, 30.0, 50.0, 40.0, direction=direction)
    word = word_of((40.0, 30.0, 50.0, 40.0))
    # The rendering of the page of 200 x 100 at 4 to the point, shown turned or not.
    width, height = (800, 400) if rotation == 0 else (400, 800)
    (placed,) = m4b.place_words(geometry_of(rotation=rotation), [first], [word], width, height)
    assert placed.kind == ("measured" if horizontal else "not horizontal")
    assert (placed.rect is not None) == horizontal


def test_14_a_word_on_a_turned_page_is_placed_by_the_turn():
    up = upright("A", 40.0, 30.0, 50.0, 40.0, direction=90.0)
    word = word_of((40.0, 30.0, 50.0, 40.0))
    (placed,) = m4b.place_words(geometry_of(rotation=90), [up], [word], 400, 800)
    assert placed.rect == (120, 160, 160, 200)


def test_15_a_word_wholly_outside_the_rendering_is_counted_not_measured():
    geometry = geometry_of()
    inside = upright("A", 10.0, 10.0, 20.0, 20.0)
    outside = upright("B", 500.0, 10.0, 510.0, 20.0, index=1)
    above = upright("C", 10.0, 500.0, 20.0, 510.0, index=2)
    words = [
        word_of((10.0, 10.0, 20.0, 20.0), (0,)),
        word_of((500.0, 10.0, 510.0, 20.0), (1,)),
        word_of((10.0, 500.0, 20.0, 510.0), (2,)),
    ]
    placed = m4b.place_words(geometry, [inside, outside, above], words, 800, 400)
    assert [word.kind for word in placed] == ["measured", "outside the page", "outside the page"]
    assert [word.rect for word in placed] == [(40, 320, 80, 360), None, None]


def test_16_a_word_partly_outside_is_clipped_to_the_rendering():
    geometry = geometry_of()
    edge = upright("A", 190.0, 90.0, 210.0, 110.0)
    (placed,) = m4b.place_words(geometry, [edge], [word_of((190.0, 90.0, 210.0, 110.0))], 800, 400)
    assert placed.rect == (760, 0, 800, 40)


def test_17_a_word_takes_the_largest_size_of_its_glyphs_and_a_band_from_it():
    glyphs = [
        upright("A", 10.0, 10.0, 20.0, 20.0),
        upright("B", 20.0, 10.0, 30.0, 20.0, index=1, size=12.0),
    ]
    word = word_of((10.0, 10.0, 30.0, 20.0), (0, 1))
    (placed,) = m4b.place_words(geometry_of(), glyphs, [word], 800, 400)
    assert placed.size == 12.0
    # Half the size in pixels, 24, rounded up; and at least 2.
    assert placed.band == 24
    assert m4b.Placed("measured", 10.0, "none", None).band == 20
    assert m4b.Placed("measured", 2.5, "none", None).band == 5
    assert m4b.Placed("measured", 0.25, "none", None).band == 2
    # Rounded up: half of 2.75 in pixels is 5.5.
    assert m4b.Placed("measured", 2.75, "none", None).band == 6


def test_18_a_component_goes_to_the_word_whose_rectangle_holds_most_of_its_pixels():
    ink = picture("##########", "##########", "##########")
    found = m4b.components_of(ink)
    # The first rectangle holds 6 columns of the 10, the second 4.
    placed = [placed_at((0, 0, 6, 3)), placed_at((6, 0, 10, 3))]
    measured = m4b.measure(found, placed, [0, 1])
    assert measured.own.tolist() == [30, 0]
    placed = [placed_at((0, 0, 4, 3)), placed_at((4, 0, 10, 3))]
    measured = m4b.measure(found, placed, [0, 1])
    assert measured.own.tolist() == [0, 30]
    assert (measured.unattributed_components, measured.unattributed_pixels) == (0, 0)


def test_19_two_words_that_hold_as_many_pixels_give_the_component_to_the_lower_one():
    ink = picture("########", "########")
    found = m4b.components_of(ink)
    halves = [placed_at((0, 0, 4, 2)), placed_at((4, 0, 8, 2))]
    assert m4b.measure(found, halves, [0, 1]).own.tolist() == [16, 0]
    # The lower index, whichever place it has.
    assert m4b.measure(found, halves[::-1], [0, 1]).own.tolist() == [16, 0]


def test_20_ink_is_8_connected():
    ink = picture("#...", ".#..", "..#.", "....")
    found = m4b.components_of(ink)
    assert found.count == 1
    assert found.pixels.tolist() == [13, 3]
    assert found.boxes.tolist() == [[0, 3, 0, 3]]
    measured = m4b.measure(found, [placed_at((0, 0, 4, 4))], [0])
    assert measured.own.tolist() == [3]
    # Two pixels with a pixel between them are two components; three that turn a corner, one.
    assert m4b.components_of(picture("#.#")).count == 2
    assert m4b.components_of(picture("##", ".#")).count == 1


def test_21_a_component_reaches_beyond_a_rectangle_by_its_extreme_pixels():
    rows = ["." * 30] * 30
    for row in range(7, 23):
        rows[row] = "." * 8 + "#" * 17 + "." * 5
    ink = picture(*rows)
    measured = m4b.measure(m4b.components_of(ink), [placed_at((10, 10, 20, 20))], [0])
    assert measured.own.tolist() == [16 * 17]
    # Rows 7 to 22 and columns 8 to 24 against the rectangle's rows 10 to 19, columns 10 to 19.
    assert (measured.up[0], measured.down[0]) == (3, 3)
    assert (measured.left[0], measured.right[0]) == (2, 5)


def test_22_a_component_in_no_rectangle_goes_to_the_nearest_word_within_its_band():
    """A word of size 2.5 has a band of 5 pixels. A pixel 4 columns beyond the rectangle's edge is
    4.5 from it, centre to edge; 6 columns beyond, 6.5."""
    word = placed_at((10, 10, 20, 20), size=2.5)
    assert word.band == 5
    near = picture(*(["." * 30] * 15 + ["." * 24 + "#" + "." * 5] + ["." * 30] * 14))
    far = picture(*(["." * 30] * 15 + ["." * 26 + "#" + "." * 3] + ["." * 30] * 14))
    owned = m4b.measure(m4b.components_of(near), [word], [0])
    assert (owned.own.tolist(), owned.unattributed_components) == ([1], 0)
    left_out = m4b.measure(m4b.components_of(far), [word], [0])
    assert left_out.own.tolist() == [0]
    assert (left_out.unattributed_components, left_out.unattributed_pixels) == (1, 1)
    # The reach of an owned component beyond the rectangle counts, as for any.
    assert owned.right[0] == 5


def test_23_the_nearest_word_is_chosen_first_and_its_own_band_decides():
    """Word 0 is nearest at 6.5 and its band is 5; word 1 is at 13.5 with a band of 40. The
    component is no one's."""
    near = placed_at((10, 10, 20, 20), size=2.5)
    far = placed_at((40, 10, 50, 20), size=20.0)
    assert (near.band, far.band) == (5, 40)
    ink = picture(*(["." * 60] * 15 + ["." * 26 + "#" + "." * 33] + ["." * 60] * 14))
    measured = m4b.measure(m4b.components_of(ink), [near, far], [0, 1])
    assert measured.own.tolist() == [0, 0]
    assert (measured.unattributed_components, measured.unattributed_pixels) == (1, 1)
    # With the band of the near word wide enough it owns the component.
    wide = placed_at((10, 10, 20, 20), size=10.0)
    assert m4b.measure(m4b.components_of(ink), [wide, far], [0, 1]).own.tolist() == [1, 0]


def test_24_the_distance_is_that_of_the_nearest_pixel_not_of_the_components_box():
    """A diagonal of 10 pixels, whose box reaches a rectangle in the corner that no pixel of it
    comes near: the nearest pixels, (4, 4) and (5, 5), have their centres 18.5 away, squared. A
    band of 4 does not reach, one of 5 does."""
    rows = [["."] * 12 for _ in range(12)]
    for i in range(10):
        rows[i][i] = "#"
    found = m4b.components_of(picture(*("".join(r) for r in rows)))
    assert found.count == 1
    corner = (8, 0, 12, 2)
    narrow = m4b.measure(found, [placed_at(corner, size=2.0)], [0])
    assert (narrow.own.tolist(), narrow.unattributed_components) == ([0], 1)
    wide = m4b.measure(found, [placed_at(corner, size=2.5)], [0])
    assert (wide.own.tolist(), wide.unattributed_components) == ([10], 0)


def test_25_the_nearest_of_two_words_at_one_distance_is_the_lower():
    ink = picture(*(["." * 30] * 15 + ["." * 15 + "#" + "." * 14] + ["." * 30] * 14))
    left = placed_at((5, 10, 10, 20), size=10.0)
    right = placed_at((21, 10, 26, 20), size=10.0)
    assert m4b.measure(m4b.components_of(ink), [left, right], [0, 1]).own.tolist() == [1, 0]
    assert m4b.measure(m4b.components_of(ink), [right, left], [0, 1]).own.tolist() == [1, 0]


def test_26_the_depth_of_another_lines_ink_inside_a_rectangle():
    """Word 1's ink, rows 12 to 30, columns 5 to 14, reaches into word 0's rectangle (rows 0 to
    19) by rows 12 to 19. It is deepest at row 12, whose distance to the rectangle's bottom edge
    is 8, as 20 - 12; and a pixel's depth is its distance to the nearest edge."""
    rows = ["." * 20] * 40
    for row in range(12, 31):
        rows[row] = "." * 5 + "#" * 10 + "." * 5
    found = m4b.components_of(picture(*rows))
    placed = [placed_at((0, 0, 20, 20)), placed_at((0, 10, 20, 40))]
    other = m4b.measure(found, placed, [0, 1])
    assert other.own.tolist() == [0, 19 * 10]
    assert other.depth.tolist() == [8, 0]
    # In one line it is not another line's.
    same = m4b.measure(found, placed, [0, 0])
    assert same.depth.tolist() == [0, 0]


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize(
    ("row", "depth"),
    [
        # Word 0's rectangle is columns 10 to 29, rows 10 to 29. A bar of ink along one row
        # runs from its column 25 to the middle of word 1's rectangle, on the right, which owns
        # it. The bar's pixels are 1 to 5 from the right edge; `row` is row - 9 from the top
        # edge and 30 - row from the bottom edge. The depth is the least of these, at the
        # bar's deepest pixel.
        (10, 1),
        (12, 3),
        (20, 5),
        (26, 4),
        (27, 3),
        (29, 1),
    ],
)
def test_27_the_depth_is_the_distance_to_the_nearest_edge_on_every_side(row, depth, mirrored):
    rows = [["."] * 70 for _ in range(40)]
    for column in range(25, 60):
        rows[row][column] = "#"
    ink = picture(*("".join(r) for r in rows))
    placed = [placed_at((10, 10, 30, 30)), placed_at((30, 0, 70, 40))]
    if mirrored:
        ink = ink[:, ::-1]
        placed = [placed_at((40, 10, 60, 30)), placed_at((0, 0, 40, 40))]
    found = m4b.components_of(ink)
    measured = m4b.measure(found, placed, [0, 1])
    assert measured.own.tolist() == [0, 35]
    assert measured.depth.tolist() == [depth, 0]


def test_28_all_the_ink_is_owned_or_unattributed():
    ink = picture("##..#", "##...", "....#")
    found = m4b.components_of(ink)
    measured = m4b.measure(found, [placed_at((0, 0, 2, 2), size=0.5)], [0])
    assert int(measured.own.sum()) + measured.unattributed_pixels == found.ink == 6
    assert (measured.unattributed_components, measured.unattributed_pixels) == (2, 2)
    # With no word measured, none of it is owned.
    nobody = m4b.measure(found, [m4b.Placed("not horizontal", 10.0, "none", None)], [0])
    assert (nobody.own.tolist(), nobody.unattributed_pixels) == ([0], 6)


def test_29_a_word_whose_glyphs_are_all_invisible_owns_no_component():
    """The invisible word's rectangle holds all 16 pixels, the visible word's 4 of them."""
    found = m4b.components_of(picture("########", "########"))
    hidden = m4b.Placed("measured", 10.0, "all", (0, 0, 8, 2))
    measured = m4b.measure(found, [hidden, placed_at((6, 0, 8, 2))], [0, 1])
    assert measured.own.tolist() == [0, 16]
    assert measured.unattributed_pixels == 0


def test_30_a_word_whose_glyphs_are_all_invisible_is_not_the_nearest():
    """One pixel 6.5 from the invisible word's rectangle and 13.5 from the visible word's, whose
    band of 40 reaches it. Without the visible word the pixel is no one's."""
    rows = ["." * 60] * 15 + ["." * 26 + "#" + "." * 33] + ["." * 60] * 14
    found = m4b.components_of(picture(*rows))
    hidden = m4b.Placed("measured", 10.0, "all", (10, 10, 20, 20))
    visible = placed_at((40, 10, 50, 20), size=20.0)
    assert m4b.measure(found, [hidden, visible], [0, 1]).own.tolist() == [0, 1]
    alone = m4b.measure(found, [hidden], [0])
    assert (alone.own.tolist(), alone.unattributed_components) == ([0], 1)


def measured_of(**reaches) -> m4b.Measured:
    """For one word: its own ink, and the reaches given in pixels."""
    base = {name: np.array([0]) for name in ("up", "down", "left", "right", "depth")}
    base.update({name: np.array([value]) for name, value in reaches.items()})
    own = base.pop("own", np.array([100]))
    return m4b.Measured(own, **base, unattributed_components=0, unattributed_pixels=0)


@pytest.mark.parametrize(
    ("size", "pixels", "side", "correct"),
    [
        # At size 10 a tenth is a point, 4 pixels: 4 are within it on either axis, and 5 are not
        # on any of the four sides.
        (10.0, 4, "up", True),
        (10.0, 4, "right", True),
        (10.0, 5, "up", False),
        (10.0, 5, "down", False),
        (10.0, 5, "left", False),
        (10.0, 5, "right", False),
        # A tenth of the size: 8 pixels at 20, a pixel and a fifth at 3, 40 at 100.
        (20.0, 8, "down", True),
        (20.0, 9, "down", False),
        (3.0, 1, "left", True),
        (3.0, 2, "left", False),
        (100.0, 40, "up", True),
        (100.0, 41, "up", False),
    ],
)
def test_31_a_cut_is_correct_within_a_tenth_of_the_size_in_points(size, pixels, side, correct):
    placed = [placed_at((0, 0, 1, 1), size=size)]
    counts, good = m4b.judge(placed, measured_of(**{side: pixels}))
    assert (counts["correct"], good.tolist()) == (int(correct), [correct])
    vertical = side in ("up", "down")
    assert counts["failing vertically"] == (vertical and not correct)
    assert counts["failing horizontally"] == (not vertical and not correct)
    assert counts["judged"] == 1


def test_32_each_word_has_the_tolerance_of_its_own_size():
    """Cuts of 4 and 8 pixels, 1 and 2 points, are within a tenth of sizes 10 and 20 in that
    order; with the sizes the other way round, the second, 8 pixels at size 10, is not."""
    placed = [placed_at((0, 0, 1, 1), size=10.0), placed_at((0, 0, 1, 1), size=20.0)]
    cuts = np.array([4, 8])
    measured = m4b.Measured(np.array([9, 9]), cuts, cuts, cuts, cuts, cuts, 0, 0)
    counts, good = m4b.judge(placed, measured)
    assert (counts["correct"], good.tolist()) == (2, [True, True])
    placed = placed[::-1]
    counts, good = m4b.judge(placed, measured)
    assert (counts["correct"], good.tolist()) == (1, [True, False])


@pytest.mark.parametrize(("pixels", "into"), [(4, False), (5, True), (0, False), (40, True)])
def test_33_ink_of_another_line_inside_is_counted_apart_from_correct(pixels, into):
    counts, good = m4b.judge([placed_at((0, 0, 1, 1))], measured_of(depth=pixels))
    assert counts["into another line"] == into
    assert counts["correct"] == 1
    assert good.tolist() == [not into]


def test_34_a_word_without_ink_of_its_own_is_judged_and_not_correct_whatever_its_cuts():
    counts, good = m4b.judge([placed_at((0, 0, 1, 1))], measured_of(own=0, up=99, depth=99))
    assert (counts["judged"], counts["judged without ink of their own"], counts["correct"]) == (
        1,
        1,
        0,
    )
    assert (counts["failing vertically"], counts["into another line"]) == (0, 0)
    assert good.tolist() == [False]


def test_35_a_word_whose_glyphs_are_all_invisible_is_not_judged_not_even_with_ink():
    hidden = m4b.Placed("measured", 10.0, "all", (0, 0, 1, 1))
    counts, good = m4b.judge([hidden], measured_of(own=0))
    assert (counts["no ink, all invisible"], counts["judged"], counts["correct"]) == (1, 0, 0)
    assert counts["judged without ink of their own"] == 0
    assert good.tolist() == [False]
    counts, good = m4b.judge([hidden], measured_of(own=50))
    assert (counts["judged"], counts["correct"]) == (0, 0)
    assert good.tolist() == [False]


def test_36_the_words_not_measured_are_counted_by_kind_and_hold_no_verdict():
    placed = [
        m4b.Placed("not horizontal", 10.0, "none", None),
        m4b.Placed("outside the page", 10.0, "all", None),
        placed_at((0, 0, 1, 1)),
    ]
    measured = m4b.Measured(
        np.array([0, 0, 50]),
        *(np.array([0, 0, 0]) for _ in range(5)),
        unattributed_components=3,
        unattributed_pixels=7,
    )
    counts, good = m4b.judge(placed, measured)
    assert counts["words"] == 3
    assert (counts["not horizontal"], counts["outside the page"], counts["measured"]) == (1, 1, 1)
    assert (counts["judged"], counts["correct"]) == (1, 1)
    assert counts["no ink, all invisible"] + counts["no ink, none invisible"] == 0
    assert (counts["unattributed components"], counts["unattributed pixels"]) == (3, 7)
    assert good.tolist() == [False, False, True]


def first_page_of(data: bytes):
    pdf = pypdfium2.PdfDocument(data)
    return pdf, pdf[0]


@pytest.mark.parametrize(
    ("shape", "box", "rotation", "holds"),
    [
        ((400, 800), (0.0, 0.0, 200.0, 100.0), 0, True),
        ((401, 799), (0.0, 0.0, 200.0, 100.0), 0, True),
        ((402, 800), (0.0, 0.0, 200.0, 100.0), 0, False),
        ((400, 802), (0.0, 0.0, 200.0, 100.0), 0, False),
        ((400, 798), (0.0, 0.0, 200.0, 100.0), 0, False),
        ((800, 400), (0.0, 0.0, 200.0, 100.0), 0, False),
        ((400, 800), (0.0, 0.0, 200.0, 100.0), 90, False),
        # The size as written, the corners not where PDFium puts them.
        ((400, 800), (10.0, 0.0, 210.0, 100.0), 0, False),
        ((400, 800), (0.0, 5.0, 200.0, 105.0), 0, False),
    ],
)
def test_37_the_rendering_is_mapped_as_pdfium_maps_it(shape, box, rotation, holds):
    pdf, page = first_page_of(file_of(b"", media=SMALL))
    try:
        assert m4b.mapping_holds(page, geometry_of(box, rotation), shape) == holds
    finally:
        page.close()
        pdf.close()


def test_38_the_rendering_of_a_rotated_page_with_a_shifted_box_is_mapped():
    data = file_of(b"", media=b"[0 0 300 300]", entries=b"/CropBox [20 30 220 130] /Rotate 270")
    with pdfacts.open(data) as document:
        geometry = document.pages[0].geometry
    pdf, page = first_page_of(data)
    try:
        ink = m4b.text_ink(page)
        assert ink.shape == (800, 400)
        assert (geometry.width, geometry.height) == (100.0, 200.0)
        assert m4b.mapping_holds(page, geometry, ink.shape)
    finally:
        page.close()
        pdf.close()


def test_39_the_rendering_of_a_page_of_no_whole_number_of_pixels_is_mapped():
    data = squares(b"", media=b"[0 0 100.125 50.125]")
    with pdfacts.open(data) as document:
        geometry = document.pages[0].geometry
    pdf, page = first_page_of(data)
    try:
        ink = m4b.text_ink(page)
        assert ink.shape == (201, 401)
        assert m4b.mapping_holds(page, geometry, ink.shape)
    finally:
        page.close()
        pdf.close()


def test_40_a_page_whose_mapping_fails_is_counted_and_not_measured(monkeypatch):
    monkeypatch.setattr(m4b, "mapping_holds", lambda *arguments: False)
    entry = m4b.read_file(written())
    assert entry["render"] == {**dict.fromkeys(m4b.RENDER, 0), "pages not mapped": 1}
    assert entry["glyph_count"] == 5
    assert m4b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_41_the_boxes_of_v2_hold_what_the_descriptors_do_not():
    """A glyph 0.7 em tall in a font whose descriptor spans 0.5 em, /FontBBox up to 500: V2's box
    is one em tall and holds the ink; D's, half an em, leaves 0.2 em of it above, 8 pixels."""
    data = squares(b"BT /F1 10 Tf 50 30 Td (A) Tj ET", height=700, bbox=b"[0 0 1000 500]")
    entry = m4b.read_file(data)
    render = entry["render"]
    assert (render["V2 judged"], render["V2 correct"], render["V2 failing vertically"]) == (1, 1, 0)
    assert (render["D judged"], render["D correct"], render["D failing vertically"]) == (1, 0, 1)
    assert (render["good in V2 only"], render["good in D only"]) == (1, 0)
    assert not m4b.descriptor_better({"a.pdf": entry})
    assert m4b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 0


def test_42_the_boxes_of_d_overlap_less_where_the_boxes_of_v2_overlap_deeply():
    """Two lines 0.85 em apart, the ink half an em tall: V2's boxes, one em, overlap by 0.15 em and
    the lower box holds 6 pixels of the upper line's ink; D's, half an em, do not meet."""
    data = squares(
        b"BT /F1 10 Tf 50 60 Td (A) Tj ET BT /F1 10 Tf 50 51.5 Td (A) Tj ET",
        height=500,
        bbox=b"[0 0 1000 500]",
    )
    entry = m4b.read_file(data)
    render = entry["render"]
    assert (render["V2 judged"], render["V2 correct"], render["V2 into another line"]) == (2, 2, 1)
    assert (render["D judged"], render["D correct"], render["D into another line"]) == (2, 2, 0)
    assert (render["good in V2 only"], render["good in D only"]) == (0, 1)
    assert m4b.descriptor_better({"a.pdf": entry})


def test_43_pages_where_d_groups_otherwise_are_counted_and_not_compared(monkeypatch):
    real = m4b.same_grouping
    monkeypatch.setattr(m4b, "same_grouping", lambda facts, other: False)
    data = squares(b"BT /F1 10 Tf 50 30 Td (A) Tj ET", height=700, bbox=b"[0 0 1000 500]")
    render = m4b.read_file(data)["render"]
    assert render["pages where D groups otherwise"] == 1
    assert (render["good in V2 only"], render["good in D only"]) == (0, 0)
    assert render["V2 judged"] == render["D judged"] == 1
    monkeypatch.setattr(m4b, "same_grouping", real)
    assert m4b.read_file(data)["render"]["pages where D groups otherwise"] == 0


def test_44_the_same_grouping_is_the_same_glyphs_in_words_and_words_in_lines():
    word = Word(page_index=0, glyphs=(0, 1), text="AB", box=Rect(0.0, 0.0, 1.0, 1.0))
    line = Line(page_index=0, words=(0,), text="AB", box=Rect(0.0, 0.0, 1.0, 1.0))
    other_word = dataclasses.replace(word, glyphs=(0, 2))
    other_line = dataclasses.replace(line, words=(0, 1))
    facts = ((), (word,), (line,))
    assert m4b.same_grouping(facts, ((), (word,), (line,)))
    assert not m4b.same_grouping(facts, ((), (other_word,), (line,)))
    assert not m4b.same_grouping(facts, ((), (word,), (other_line,)))
    assert not m4b.same_grouping(facts, ((), (word, word), (line,)))
