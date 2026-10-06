"""paths() and images(): what a page draws, appearances included.

The rules are those of docs/conventions.md, "Paths and images".

Each file is built here with the byte-level builder; the values expected
follow from the construction (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import (
    Capability,
    ColourSpace,
    DisplayColour,
    FillRule,
    LimitExceededError,
    Limits,
    Point,
    Rect,
    ShapeKind,
    Unknown,
    UnknownReason,
    capabilities,
    images,
    paths,
)
from synthetic.builder import build, stream

FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
GREY = stream(
    b"\x00\xff\x80\x40\x20\x10",
    b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
    b" /BitsPerComponent 8",
)
BLACK = DisplayColour(0, 0, 0, 255)


def page_file(
    content: bytes,
    *,
    resources: bytes = b"",
    annots: bytes = b"",
    objects: dict[int, bytes] | None = None,
) -> bytes:
    """A one-page file of 400 x 300: its content, resources and /Annots as given."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
    if resources:
        page += b" /Resources " + resources
    if annots:
        page += b" /Annots " + annots
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: page + b" >>",
            **(objects or {}),
        },
        1,
    )


def read(data: bytes, limits: Limits | None = None):
    with pdfacts.open(data, limits=limits) as document:
        return paths(document, 0), images(document, 0)


def points(subpath) -> list[tuple[float, float]]:
    return [(p.x, p.y) for segment in subpath.segments for p in segment.points]


def square(entries: bytes = b"") -> bytes:
    return b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 50] /AP << /N 7 0 R >> %s >>" % (
        entries
    )


def appearance(content: bytes, entries: bytes = b"") -> bytes:
    return stream(content, b"/Type /XObject /Subtype /Form /BBox [0 0 100 20] " + entries)


# --- the page's content ----------------------------------------------------------------


def test_a_filled_rectangle():
    (path,), found = read(page_file(b"10 20 100 50 re f"))
    assert found == ()
    (subpath,) = path.subpaths
    assert (subpath.shape, subpath.rect) == (ShapeKind.RECTANGLE, Rect(10, 20, 110, 70))
    assert (path.fill, path.stroke, path.fill_colour) == (FillRule.NONZERO, False, BLACK)
    assert (path.stroke_colour, path.line_width, path.dash) == (None, None, None)
    assert (path.ref.page_index, path.ref.annotation, path.ref.drawing_index) == (0, None, 0)
    assert path.annotation_flags is None


def test_a_line_in_user_space_with_its_width_and_dash():
    (path,), _ = read(page_file(b"3 0 0 3 10 20 cm 2 w [4 2] 1 d 1 0 0 RG 0 0 m 10 0 l S"))
    (subpath,) = path.subpaths
    assert subpath.shape is ShapeKind.LINE
    assert points(subpath) == [(10.0, 20.0), (40.0, 20.0)]
    assert (path.line_width, path.dash.lengths, path.dash.phase) == (6.0, (12.0, 6.0), 3.0)
    assert path.stroke_colour == DisplayColour(255, 0, 0, 255)
    assert (path.fill, path.fill_colour) == (None, None)


def test_a_curve_with_its_control_points_in_user_space():
    (path,), _ = read(page_file(b"1 0 0 1 5 5 cm 0 0 m 10 20 30 20 40 0 c S"))
    (subpath,) = path.subpaths
    assert subpath.shape is ShapeKind.CURVES
    assert points(subpath) == [(5.0, 5.0), (15.0, 25.0), (35.0, 25.0), (45.0, 5.0)]


def test_a_width_under_an_uneven_scale_is_unknown():
    (path,), _ = read(page_file(b"2 0 0 3 0 0 cm 0 0 m 10 0 l S"))
    assert isinstance(path.line_width, Unknown)
    assert path.line_width.reason is UnknownReason.UNREADABLE


def test_a_negative_width_is_invalid():
    (path,), _ = read(page_file(b"-2 w 0 0 m 10 0 l S"))
    assert isinstance(path.line_width, Unknown)
    assert path.line_width.reason is UnknownReason.INVALID


def test_a_form_where_it_is_drawn_and_the_matrices_composed():
    """The path moves by (3, 4), then by /Matrix (5, 5), then doubles and moves by (100, 100)."""
    data = page_file(
        b"2 0 0 2 100 100 cm /X Do",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={5: stream(b"1 0 0 1 3 4 cm 0 0 m 10 0 l S", FORM + b" /Matrix [1 0 0 1 5 5]")},
    )
    (path,), _ = read(data)
    (subpath,) = path.subpaths
    assert points(subpath) == [(116.0, 118.0), (136.0, 118.0)]
    assert path.line_width == 2.0


def test_one_image_placed_twice_is_two_facts():
    data = page_file(
        b"q 30 0 0 20 5 5 cm /Im Do Q q 0 20 -30 0 100 50 cm /Im Do Q",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        objects={5: GREY},
    )
    found, (first, second) = read(data)
    assert found == ()
    assert (first.corners.p1, first.corners.p3) == (Point(5, 5), Point(35, 25))
    # Turned: (0, 0) at (100, 50), (1, 0) at (100, 70), (0, 1) at (70, 50).
    assert (second.corners.p1, second.corners.p2, second.corners.p4) == (
        Point(100, 50),
        Point(100, 70),
        Point(70, 50),
    )
    for image in (first, second):
        assert (image.pixel_width, image.pixel_height) == (3, 2)
        assert (image.colour_space, image.bits_per_pixel) == (ColourSpace.DEVICE_GRAY, 8)
    assert [image.ref.drawing_index for image in (first, second)] == [0, 1]


def test_paths_and_images_share_the_drawing_order():
    data = page_file(
        b"0 0 m 1 0 l S q 30 0 0 20 5 5 cm /Im Do Q 0 0 m 2 0 l S",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        objects={5: GREY},
    )
    found, placed = read(data)
    assert [path.ref.drawing_index for path in found] == [0, 2]
    assert [image.ref.drawing_index for image in placed] == [1]


def test_an_image_pdfium_cannot_decode_has_unknown_values():
    broken = stream(
        b"\x00\xff\x80\x40\x20\x10",
        b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
        b" /BitsPerComponent 8 /Filter /NoSuchDecode",
    )
    data = page_file(
        b"q 30 0 0 20 5 5 cm /Im Do Q",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        objects={5: broken},
    )
    _, (image,) = read(data)
    assert (image.pixel_width, image.pixel_height) == (3, 2)
    for value in (image.colour_space, image.bits_per_pixel):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.UNREADABLE


def test_a_pattern_colour_makes_the_colours_unknown():
    cell = stream(
        b"1 0 0 rg 0 0 5 5 re f",
        b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
        b" /BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
    )
    data = page_file(
        b"/Pattern cs /P0 scn 0 0 100 100 re f",
        resources=b"<< /Pattern << /P0 5 0 R >> >>",
        objects={5: cell},
    )
    (path,), _ = read(data)
    assert isinstance(path.fill_colour, Unknown)
    assert path.fill is FillRule.NONZERO


def test_a_width_under_a_skew_with_equal_columns_is_unknown():
    """The columns (1, 0) and (0.6, 0.8) are of length 1 but not at a right angle."""
    (path,), _ = read(page_file(b"1 0 0.6 0.8 0 0 cm 0 0 m 10 0 l S"))
    assert isinstance(path.line_width, Unknown)


def test_an_image_mask_has_no_colour_space():
    mask = stream(b"\x00\x00", b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ImageMask true")
    data = page_file(
        b"q 30 0 0 20 5 5 cm /M Do Q", resources=b"<< /XObject << /M 5 0 R >> >>", objects={5: mask}
    )
    _, (image,) = read(data)
    assert (image.colour_space, image.bits_per_pixel) == (None, 1)


def test_an_image_in_a_pattern_colour_space_is_invalid():
    image = stream(
        b"\x00\x00\x00\x00",
        b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /Pattern"
        b" /BitsPerComponent 8",
    )
    data = page_file(
        b"q 30 0 0 20 5 5 cm /I Do Q",
        resources=b"<< /XObject << /I 5 0 R >> >>",
        objects={5: image},
    )
    _, (found,) = read(data)
    assert isinstance(found.colour_space, Unknown)
    assert found.colour_space.reason is UnknownReason.INVALID


def test_colours_are_unknown_where_qpdf_misreads_an_object():
    """QPDF warns about the page's /PieceInfo, a hex string it cannot read."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(b"0 0 m 10 0 l S"),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /PieceInfo <zz> >>",
        },
        1,
    )
    (path,), _ = read(data)
    assert isinstance(path.stroke_colour, Unknown)
    assert path.stroke_colour.reason is UnknownReason.UNREADABLE


def test_a_page_without_content_draws_nothing():
    assert read(page_file(b"")) == ((), ())


def test_a_width_under_a_scale_even_within_a_thousandth_is_the_mean():
    (path,), _ = read(page_file(b"3 0 0 3.002 0 0 cm 2 w 0 0 m 10 0 l S"))
    assert path.line_width == pytest.approx(2 * (3 * 3.002) ** 0.5)


def test_a_line_after_a_closed_subpath_begins_a_new_one_where_it_began():
    (path,), _ = read(page_file(b"10 20 100 50 re 200 200 l S"))
    rectangle, line = path.subpaths
    assert rectangle.shape is ShapeKind.RECTANGLE
    assert (line.shape, points(line)) == (ShapeKind.LINE, [(10.0, 20.0), (200.0, 200.0)])


def test_a_form_deeper_than_pdfium_reads_leaves_the_page_unknown():
    chain = {}
    for level in range(41):
        last = level == 40
        content = b"0 0 m 1 0 l S" + (b"" if last else b" /X Do")
        held = b"" if last else b" /Resources << /XObject << /X %d 0 R >> >>" % (101 + level)
        chain[100 + level] = stream(content, FORM + held)
    data = page_file(b"/X Do", resources=b"<< /XObject << /X 100 0 R >> >>", objects=chain)
    found, placed = read(data)
    for value in (found, placed):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.UNREADABLE


# --- appearances --------------------------------------------------------------------------


def test_an_appearance_placed_on_its_rect_with_the_annotation_and_its_flags():
    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: square(b"/F 4"), 7: appearance(b"0 0 100 20 re f")}
    )
    (path,), _ = read(data)
    (subpath,) = path.subpaths
    assert (subpath.shape, subpath.rect) == (ShapeKind.RECTANGLE, Rect(10, 10, 210, 50))
    assert path.annotation_flags == 4
    annotation = path.ref.annotation
    assert (annotation.kind, annotation.annotation_index, annotation.object_number) == (
        "Square",
        0,
        6,
    )


def test_an_appearance_turned_by_its_matrix_still_fills_its_rect():
    """/Matrix [0 1 -1 0 0 0] turns /BBox [0 0 100 20] into (-20, 0, 0, 100), placed on /Rect."""
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={6: square(), 7: appearance(b"0 0 100 20 re f", b"/Matrix [0 1 -1 0 0 0]")},
    )
    (path,), _ = read(data)
    (subpath,) = path.subpaths
    assert subpath.rect == Rect(10, 10, 210, 50)
    # (0, 0) of the stream lands on (210, 10): /Matrix first, then the placement.
    assert subpath.segments[0].points[0] == Point(210, 10)


def test_an_appearance_read_through_pdfdocencoding_agrees():
    """A comment with bytes PDFDocEncoding maps (0x80, 0xA0) and one it leaves undefined (0x7F)."""
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={6: square(), 7: appearance(b"% \x80 \xa0 \x7f\n0 0 100 20 re f")},
    )
    (path,), _ = read(data)
    assert path.subpaths[0].rect == Rect(10, 10, 210, 50)


def test_an_appearance_whose_text_pdfium_reads_otherwise_is_unknown():
    """Data that begins with FE FF: PDFium gives its text as UTF-16BE (issue #60)."""
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={6: square(), 7: appearance(b"\xfe\xff\n0 0 100 20 re f")},
    )
    (path,), _ = read(data)
    assert isinstance(path.subpaths, Unknown)
    assert path.subpaths.reason is UnknownReason.ENGINES_DISAGREE


def test_flags_pdfium_reads_otherwise_leave_the_annotation_unknown(monkeypatch):
    import pypdfium2.raw

    monkeypatch.setattr(pypdfium2.raw, "FPDFAnnot_GetFlags", lambda annot: 99)
    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: square(b"/F 4"), 7: appearance(b"0 0 100 20 re f")}
    )
    (path,), _ = read(data)
    assert isinstance(path.subpaths, Unknown)
    assert path.subpaths.reason is UnknownReason.ENGINES_DISAGREE
    assert isinstance(path.annotation_flags, Unknown)
    assert path.annotation_flags.reason is UnknownReason.ENGINES_DISAGREE


def test_a_pattern_colour_in_an_appearance_makes_its_colours_unknown():
    cell = stream(
        b"1 0 0 rg 0 0 5 5 re f",
        b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
        b" /BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
    )
    data = page_file(
        b"0 0 m 1 0 l S",
        annots=b"[6 0 R]",
        objects={
            6: square(),
            7: appearance(
                b"/Pattern cs /P0 scn 0 0 100 20 re f", b"/Resources << /Pattern << /P0 8 0 R >> >>"
            ),
            8: cell,
        },
    )
    (on_page, in_appearance), _ = read(data)
    assert on_page.stroke_colour == BLACK
    assert isinstance(in_appearance.fill_colour, Unknown)


def test_an_object_of_an_appearance_with_its_own_matrix():
    """The rectangle is drawn at half size under 2 0 0 2 0 0 cm, then placed on /Rect."""
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={6: square(), 7: appearance(b"2 0 0 2 0 0 cm 0 0 50 10 re f")},
    )
    (path,), _ = read(data)
    assert path.subpaths[0].rect == Rect(10, 10, 210, 50)


def test_an_annotation_without_f_has_flags_0():
    objects = {6: square(), 7: appearance(b"0 0 100 20 re f")}
    (path,), _ = read(page_file(b"", annots=b"[6 0 R]", objects=objects))
    assert path.annotation_flags == 0


def test_an_appearance_deeper_than_pdfium_reads_leaves_the_annotation_unknown():
    chain = {}
    for level in range(41):
        last = level == 40
        content = b"0 0 m 1 0 l S" + (b"" if last else b" /X Do")
        held = b"" if last else b" /Resources << /XObject << /X %d 0 R >> >>" % (101 + level)
        chain[100 + level] = stream(content, FORM + held)
    data = page_file(
        b"0 0 m 1 0 l S",
        annots=b"[6 0 R]",
        objects={
            6: square(),
            7: appearance(b"/X Do", b"/Resources << /XObject << /X 100 0 R >> >>"),
            **chain,
        },
    )
    (on_page, in_appearance), _ = read(data)
    assert on_page.ref.annotation is None
    assert isinstance(in_appearance.subpaths, Unknown)
    assert in_appearance.subpaths.reason is UnknownReason.UNREADABLE


def test_a_placement_that_is_not_finite_is_invalid():
    huge = b"1" + b"0" * 300 + b".0"
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: square(),
            7: stream(
                b"0 0 100 20 re f",
                b"/Type /XObject /Subtype /Form /BBox [0 0 10000000000 10000000000]"
                b" /Matrix [%s 0 0 %s 0 0]" % (huge, huge),
            ),
        },
    )
    (path,), _ = read(data)
    assert isinstance(path.subpaths, Unknown) and path.subpaths.reason is UnknownReason.INVALID


def test_an_empty_subtype_is_no_subtype():
    annot = b"<< /Type /Annot /Subtype / /Rect [10 10 210 50] /AP << /N 7 0 R >> >>"
    data = page_file(b"", annots=b"[6 0 R]", objects={6: annot, 7: appearance(b"0 0 100 20 re f")})
    found, _ = read(data)
    assert isinstance(found, Unknown) and found.reason is UnknownReason.INVALID


@pytest.mark.parametrize("f", [b"4.0", b"-1"], ids=["a real number", "an integer below 0"])
def test_flags_that_are_no_32_bit_integer_are_invalid_and_the_graphics_given(f):
    objects = {6: square(b"/F " + f), 7: appearance(b"0 0 100 20 re f")}
    (path,), _ = read(page_file(b"", annots=b"[6 0 R]", objects=objects))
    assert path.subpaths[0].rect == Rect(10, 10, 210, 50)
    assert isinstance(path.annotation_flags, Unknown)
    assert path.annotation_flags.reason is UnknownReason.INVALID


def test_a_hidden_annotation_is_given_with_its_flags():
    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: square(b"/F 2"), 7: appearance(b"0 0 100 20 re f")}
    )
    (path,), _ = read(data)
    assert path.annotation_flags == 2


def test_the_page_first_then_each_annotation():
    data = page_file(
        b"0 0 m 1 0 l S",
        annots=b"[6 0 R 8 0 R]",
        objects={6: square(), 7: appearance(b"0 0 100 20 re f"), 8: square(b"/F 2")},
    )
    found, _ = read(data)
    assert [path.ref.drawing_index for path in found] == [0, 1, 2]
    assert [path.ref.annotation for path in found][0] is None
    assert [path.ref.annotation.annotation_index for path in found[1:]] == [0, 1]


def test_an_appearance_pdfium_draws_where_qpdf_selects_none_is_unknown(monkeypatch):
    """The engines disagree on the appearance: QPDF is made to select none."""
    from pdfacts.backends import qpdf as qpdf_backend

    monkeypatch.setattr(qpdf_backend, "_selected_appearance", lambda annot: None)
    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: square(), 7: appearance(b"0 0 100 20 re f")}
    )
    (path,), _ = read(data)
    assert path.subpaths == Unknown(
        UnknownReason.ENGINES_DISAGREE, "PDFium draws an appearance where QPDF selects none"
    )


def test_a_state_named_by_a_real_number_leaves_the_annotation_unknown():
    entries = b"/AS 5.5 /AP << /N << /5.5 7 0 R /Off 7 0 R >> >>"
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 50] %s >>" % entries,
            7: appearance(b"0 0 100 20 re f"),
        },
    )
    (path,), (image,) = read(data)
    for value in (path.subpaths, path.fill, image.corners):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.UNREADABLE
    assert path.ref.annotation.kind == "Square"


@pytest.mark.parametrize(
    ("rect", "bbox"),
    [(b"[10 10 210]", b"[0 0 100 20]"), (b"[10 10 210 50]", b"[0 0 100 0]")],
    ids=["a /Rect of three numbers", "a /BBox without area"],
)
def test_an_appearance_that_cannot_be_placed_is_invalid(rect, bbox):
    annot = b"<< /Type /Annot /Subtype /Square /Rect %s /AP << /N 7 0 R >> >>" % rect
    ap = stream(b"0 0 100 20 re f", b"/Type /XObject /Subtype /Form /BBox " + bbox)
    data = page_file(b"", annots=b"[6 0 R]", objects={6: annot, 7: ap})
    (path,), _ = read(data)
    assert isinstance(path.subpaths, Unknown) and path.subpaths.reason is UnknownReason.INVALID


def test_an_annotation_without_a_subtype_that_pdfium_draws_leaves_the_page_unknown():
    annot = b"<< /Type /Annot /Rect [10 10 210 50] /AP << /N 7 0 R >> >>"
    data = page_file(
        b"0 0 m 1 0 l S", annots=b"[6 0 R]", objects={6: annot, 7: appearance(b"0 0 100 20 re f")}
    )
    found, placed = read(data)
    for value in (found, placed):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.INVALID


# --- the page as a whole ------------------------------------------------------------------


def test_annots_that_are_no_array_leave_the_page_unknown():
    found, placed = read(page_file(b"0 0 m 1 0 l S", annots=b"5"))
    for value in (found, placed):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.INVALID


def test_engines_that_count_different_annotations_leave_the_page_unknown(monkeypatch):
    import pypdfium2.raw

    real = pypdfium2.raw.FPDFPage_GetAnnotCount
    monkeypatch.setattr(pypdfium2.raw, "FPDFPage_GetAnnotCount", lambda page: real(page) + 1)
    data = page_file(b"0 0 m 1 0 l S", annots=b"[6 0 R]", objects={6: square(), 7: appearance(b"")})
    found, placed = read(data)
    for value in (found, placed):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.ENGINES_DISAGREE


def test_a_file_qpdf_cannot_read_leaves_the_page_unknown():
    """Junk before the header: PDFium opens the file, QPDF does not (M1)."""
    with pdfacts.open(b"x" * 1024 + page_file(b"0 0 m 1 0 l S")) as document:
        assert isinstance(paths(document, 0), Unknown)
        assert isinstance(images(document, 0), Unknown)


# --- limits, arguments, the interface -------------------------------------------------------


def test_the_page_s_own_paths_count_against_the_item_limit():
    data = page_file(b"0 0 m 1 0 l S 0 0 m 2 0 l S 0 0 m 3 0 l S")
    with pytest.raises(LimitExceededError) as raised:
        read(data, Limits(max_items_per_page=2))
    assert raised.value.found == 3


def test_paths_and_images_count_against_the_item_limit():
    data = page_file(
        b"0 0 m 1 0 l S q 30 0 0 20 5 5 cm /Im Do Q",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        annots=b"[6 0 R]",
        objects={5: GREY, 6: square(), 7: appearance(b"0 0 100 20 re f")},
    )
    found, placed = read(data, Limits(max_items_per_page=3))
    assert (len(found), len(placed)) == (2, 1)
    with pytest.raises(LimitExceededError) as raised:
        read(data, Limits(max_items_per_page=2))
    assert raised.value.found == 3


def test_a_document_of_pdfacts_and_a_page_it_has():
    with pdfacts.open(page_file(b"")) as document:
        with pytest.raises(IndexError):
            paths(document, 1)
        with pytest.raises(IndexError):
            images(document, -1)
    with pytest.raises(TypeError):
        paths(b"%PDF-1.7", 0)


def test_the_entry_points_and_the_capability():
    assert {"paths", "images"} <= set(pdfacts.__all__)
    assert capabilities()[Capability.GRAPHICS] is True
