"""The PDFium backend: the paths and images of a page and of its appearances, as plain data.

Each page is built here with the byte-level builder; the values expected are
those the content writes, the defaults of ISO 32000-1 (8.4.1, Table 52) where
it writes none, and what PDFium was measured to do where the test says so.
"""

from __future__ import annotations

import dataclasses
import struct
import typing

import pypdfium2.raw as pdfium_c
import pytest

from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import (
    AppearanceDrawing,
    Drawing,
    DrawingReader,
    DrawnImage,
    DrawnPath,
    EngineFailure,
    FailureKind,
)
from synthetic.builder import build, stream

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MOVE, LINE, CURVE = (
    pdfium_c.FPDF_SEGMENT_MOVETO,
    pdfium_c.FPDF_SEGMENT_LINETO,
    pdfium_c.FPDF_SEGMENT_BEZIERTO,
)
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
GREY_IMAGE = stream(
    b"\x00\xff\x80\x40\x20\x10",
    b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
    b" /BitsPerComponent 8",
)
THREE_LINES = b"0 0 m 1 0 l S 0 0 m 2 0 l S 0 0 m 3 0 l S"


def page_file(
    content: bytes,
    *,
    resources: bytes = b"",
    annots: bytes = b"",
    objects: dict[int, bytes] | None = None,
) -> bytes:
    """A one-page file: its content, resources and /Annots as given, `objects` beside."""
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


def drawing_of(data: bytes, cap: int | None = None) -> Drawing:
    document = PdfiumDocument.open(data, None)
    try:
        return document.page_drawing(0, cap)
    finally:
        document.close()


def appearances_of(data: bytes, cap: int | None = None) -> tuple[AppearanceDrawing | None, ...]:
    document = PdfiumDocument.open(data, None)
    try:
        return document.appearance_drawings(0, cap)
    finally:
        document.close()


def only_path(content: bytes, **page) -> DrawnPath:
    (path,) = drawing_of(page_file(content, **page)).objects
    assert isinstance(path, DrawnPath)
    return path


def as_float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def plain(value: object) -> bool:
    """Built from int, float, str, bool, None, tuple and the dataclasses of the protocol."""
    if value is None or type(value) in (int, float, str, bool):
        return True
    if type(value) is tuple:
        return all(plain(item) for item in value)
    if type(value) in (Drawing, AppearanceDrawing, DrawnPath, DrawnImage):
        return all(plain(getattr(value, f.name)) for f in dataclasses.fields(value))
    return False


# --- paths --------------------------------------------------------------------------


def test_a_rectangle_is_five_points_the_last_closing():
    path = only_path(b"10 20 100 50 re S")
    assert path.segments == (
        (MOVE, 10.0, 20.0, False),
        (LINE, 110.0, 20.0, False),
        (LINE, 110.0, 70.0, False),
        (LINE, 10.0, 70.0, False),
        (LINE, 10.0, 20.0, True),
    )
    assert path.matrix == IDENTITY


def test_a_curve_is_three_points_in_a_row():
    path = only_path(b"10 20 m 20 60 90 60 100 20 c S")
    assert path.segments == (
        (MOVE, 10.0, 20.0, False),
        (CURVE, 20.0, 60.0, False),
        (CURVE, 90.0, 60.0, False),
        (CURVE, 100.0, 20.0, False),
    )


def test_two_subpaths_in_one_path():
    path = only_path(b"10 20 m 110 20 l 10 40 m 110 40 l S")
    assert [segment[0] for segment in path.segments] == [MOVE, LINE, MOVE, LINE]


def test_points_are_32_bit_floats():
    path = only_path(b"0 0 m 14400.3 0.1 l S")
    assert path.segments[1][1:3] == (as_float32(14400.3), as_float32(0.1))


@pytest.mark.parametrize(
    ("paint", "fill_mode", "stroke"),
    [
        (b"S", pdfium_c.FPDF_FILLMODE_NONE, True),
        (b"f", pdfium_c.FPDF_FILLMODE_WINDING, False),
        (b"f*", pdfium_c.FPDF_FILLMODE_ALTERNATE, False),
        (b"B", pdfium_c.FPDF_FILLMODE_WINDING, True),
        (b"B*", pdfium_c.FPDF_FILLMODE_ALTERNATE, True),
    ],
)
def test_the_draw_mode(paint, fill_mode, stroke):
    path = only_path(b"10 20 100 50 re " + paint)
    assert (path.fill_mode, path.stroke) == (fill_mode, stroke)


def test_the_line_as_the_content_sets_it():
    path = only_path(b"3 w [4 2] 1 d 1 J 2 j 0 0 m 50 0 l S")
    assert (path.width, path.dash, path.dash_phase, path.cap, path.join) == (
        3.0,
        (4.0, 2.0),
        1.0,
        pdfium_c.FPDF_LINECAP_ROUND,
        pdfium_c.FPDF_LINEJOIN_BEVEL,
    )


def test_the_line_where_the_content_sets_nothing():
    path = only_path(b"0 0 m 50 0 l S")
    assert (path.width, path.dash, path.dash_phase, path.cap, path.join) == (
        1.0,
        (),
        0.0,
        pdfium_c.FPDF_LINECAP_BUTT,
        pdfium_c.FPDF_LINEJOIN_MITER,
    )
    assert (path.fill_colour, path.stroke_colour) == ((0, 0, 0, 255), (0, 0, 0, 255))


def test_a_negative_width_and_dash_as_the_file_writes_them():
    # Measured: PDFium passes them through (issue #58, "Facts").
    assert only_path(b"-2 w 0 0 m 50 0 l S").width == -2.0
    assert only_path(b"[3 -1] 0 d 0 0 m 50 0 l S").dash == (3.0, -1.0)


def test_colours_in_8_bit_red_green_blue_alpha():
    path = only_path(b"1 0 0 RG 0 0 1 rg 0 0 m 50 0 l B")
    assert (path.stroke_colour, path.fill_colour) == ((255, 0, 0, 255), (0, 0, 255, 255))


def test_alpha_from_the_graphics_state():
    path = only_path(
        b"/G gs 0 0 m 50 0 l B", resources=b"<< /ExtGState << /G << /ca 0 /CA 1 >> >> >>"
    )
    assert (path.fill_colour, path.stroke_colour) == ((0, 0, 0, 0), (0, 0, 0, 255))


def test_the_matrix_and_the_points_in_the_path_s_own_space():
    path = only_path(b"2 0 0 3 10 20 cm 0 0 m 50 0 l S")
    assert path.matrix == (2.0, 0.0, 0.0, 3.0, 10.0, 20.0)
    assert path.segments[1][1:3] == (50.0, 0.0)


def test_what_draws_nothing_is_no_object():
    assert drawing_of(page_file(b"0 0 m 50 0 l n")).objects == ()
    (path,) = drawing_of(page_file(b"0 0 100 100 re W n 0 0 m 50 0 l S")).objects
    assert path.segments[1][1:3] == (50.0, 0.0)


def test_text_and_shadings_are_not_read():
    shading = (
        b"<< /ShadingType 2 /ColorSpace /DeviceGray /Coords [0 0 100 0]"
        b" /Function << /FunctionType 2 /Domain [0 1] /C0 [0] /C1 [1] /N 1 >> >>"
    )
    data = page_file(
        b"BT /F1 12 Tf 10 10 Td (a) Tj ET /Sh sh 0 0 m 50 0 l S",
        resources=b"<< /Font << /F1 5 0 R >> /Shading << /Sh 6 0 R >> >>",
        objects={5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", 6: shading},
    )
    (path,) = drawing_of(data).objects
    assert isinstance(path, DrawnPath)


# --- forms -----------------------------------------------------------------------------


def test_a_form_s_matrix_and_the_one_it_is_drawn_with_are_composed():
    data = page_file(
        b"2 0 0 2 100 100 cm /X Do",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={5: stream(b"1 0 0 1 3 4 cm 0 0 m 10 0 l S", FORM + b" /Matrix [1 0 0 1 5 5]")},
    )
    (path,) = drawing_of(data).objects
    # (0, 0) moves by (3, 4), then by (5, 5), then doubles and moves by (100, 100).
    assert path.matrix == (2.0, 0.0, 0.0, 2.0, 116.0, 118.0)


def test_matrices_are_composed_through_two_forms_drawn_twice():
    """/A has /Matrix [1 0.5 0 1 0 0] and draws /B; /B has [0.5 0 0 0.5 7 11] and a line.

    Composed, /B then /A: (0.5, 0.25, 0, 0.5, 7, 14.5); then the first place,
    turned by (0, 1, -1, 0, 300, 0): (-0.25, 0.5, -0.5, 0, 285.5, 7); then the
    second, moved by (10, 20): (0.5, 0.25, 0, 0.5, 17, 34.5).
    """
    data = page_file(
        b"q 0 1 -1 0 300 0 cm /A Do Q q 1 0 0 1 10 20 cm /A Do Q",
        resources=b"<< /XObject << /A 5 0 R >> >>",
        objects={
            5: stream(
                b"/B Do",
                FORM + b" /Matrix [1 0.5 0 1 0 0] /Resources << /XObject << /B 6 0 R >> >>",
            ),
            6: stream(b"0 0 m 10 0 l S", FORM + b" /Matrix [0.5 0 0 0.5 7 11]"),
        },
    )
    first, second = drawing_of(data).objects
    assert first.matrix == (-0.25, 0.5, -0.5, 0.0, 285.5, 7.0)
    assert second.matrix == (0.5, 0.25, 0.0, 0.5, 17.0, 34.5)


def forms_deep(levels: int) -> dict[int, bytes]:
    """Forms 100 to 100 + levels - 1, each drawing a line and then the next."""
    objects = {}
    for level in range(levels):
        last = level == levels - 1
        content = b"0 0 m 1 0 l S" + (b"" if last else b" /X Do")
        nested = b"" if last else b" /Resources << /XObject << /X %d 0 R >> >>" % (101 + level)
        objects[100 + level] = stream(content, FORM + nested)
    return objects


@pytest.mark.parametrize(("levels", "paths", "too_deep"), [(40, 40, False), (41, 40, True)])
def test_a_form_deeper_than_pdfium_reads_is_said(levels, paths, too_deep):
    # Measured: PDFium reads forms 40 levels deep; the 41st is a form without objects.
    resources = b"<< /XObject << /X 100 0 R >> >>"
    page = drawing_of(page_file(b"/X Do", resources=resources, objects=forms_deep(levels)))
    assert (len(page.objects), page.too_deep) == (paths, too_deep)
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: square(7),
            7: appearance(b"/X Do", b"/Resources " + resources),
            **forms_deep(levels),
        },
    )
    ((drawn,),) = [appearances_of(data)]
    assert (len(drawn.drawing.objects), drawn.drawing.too_deep) == (paths, too_deep)


def test_forms_are_read_where_they_are_drawn():
    data = page_file(
        b"0 0 m 1 0 l S /X Do 0 0 m 4 0 l S",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={
            5: stream(b"0 0 m 2 0 l S /Y Do", FORM + b" /Resources << /XObject << /Y 6 0 R >> >>"),
            6: stream(b"0 0 m 3 0 l S", FORM),
        },
    )
    ends = [path.segments[1][1] for path in drawing_of(data).objects]
    assert ends == [1.0, 2.0, 3.0, 4.0]


# --- images ----------------------------------------------------------------------------


def test_an_image_where_it_maps_the_unit_square_and_its_metadata():
    data = page_file(
        b"q 30 0 0 20 5 5 cm /Im Do Q",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        objects={5: GREY_IMAGE},
    )
    (image,) = drawing_of(data).objects
    assert image == DrawnImage(
        matrix=(30.0, 0.0, 0.0, 20.0, 5.0, 5.0),
        width=3,
        height=2,
        colour_space=pdfium_c.FPDF_COLORSPACE_DEVICEGRAY,
        bits_per_pixel=8,
    )


def test_an_image_mask_has_no_colour_space():
    mask = stream(b"\x00\x00", b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ImageMask true")
    data = page_file(
        b"q 30 0 0 20 5 5 cm /M Do Q",
        resources=b"<< /XObject << /M 5 0 R >> >>",
        objects={5: mask},
    )
    (image,) = drawing_of(data).objects
    assert (image.colour_space, image.bits_per_pixel) == (pdfium_c.FPDF_COLORSPACE_UNKNOWN, 1)


def test_an_image_pdfium_cannot_decode_has_colour_space_0_and_0_bits():
    # Measured: the metadata call returns true all the same (issue #58, "Facts").
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
    (image,) = drawing_of(data).objects
    assert (image.width, image.height, image.colour_space, image.bits_per_pixel) == (3, 2, 0, 0)


def test_an_image_in_a_form_and_the_order_of_paths_and_images():
    data = page_file(
        b"0 0 m 1 0 l S /X Do 0 0 m 2 0 l S",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={
            5: stream(
                b"q 30 0 0 20 0 0 cm /Im Do Q",
                FORM + b" /Matrix [1 0 0 1 5 5] /Resources << /XObject << /Im 6 0 R >> >>",
            ),
            6: GREY_IMAGE,
        },
    )
    first, image, last = drawing_of(data).objects
    assert (type(first), type(image), type(last)) == (DrawnPath, DrawnImage, DrawnPath)
    assert image.matrix == (30.0, 0.0, 0.0, 20.0, 5.0, 5.0)


# --- the cap ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cap", "count", "stopped"), [(None, 3, False), (3, 3, False), (2, 2, True), (0, 0, True)]
)
def test_reading_stops_at_the_cap(cap, count, stopped):
    drawing = drawing_of(page_file(THREE_LINES), cap)
    assert (len(drawing.objects), drawing.stopped) == (count, stopped)
    assert [path.segments[1][1] for path in drawing.objects] == [1.0, 2.0, 3.0][:count]


def test_a_form_counts_what_it_draws_not_itself():
    data = page_file(
        b"/X Do",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={5: stream(b"0 0 m 1 0 l S 0 0 m 2 0 l S", FORM)},
    )
    drawing = drawing_of(data, 2)
    assert (len(drawing.objects), drawing.stopped) == (2, False)


def test_images_count_against_the_cap():
    data = page_file(
        b"q 30 0 0 20 5 5 cm /Im Do Q q 30 0 0 20 50 5 cm /Im Do Q",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        objects={5: GREY_IMAGE},
    )
    drawing = drawing_of(data, 1)
    assert (len(drawing.objects), drawing.stopped) == (1, True)


def test_a_page_without_objects_does_not_stop_at_zero():
    assert drawing_of(page_file(b""), 0) == Drawing(objects=(), stopped=False, too_deep=False)


def test_a_cap_below_zero_is_a_value_error():
    document = PdfiumDocument.open(page_file(THREE_LINES), None)
    try:
        for read in (document.page_drawing, document.appearance_drawings):
            with pytest.raises(ValueError):
                read(0, -1)
    finally:
        document.close()


# --- appearances -------------------------------------------------------------------------


def square(appearance: int, entries: bytes = b"") -> bytes:
    return b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 50] /AP << /N %d 0 R >> %s >>" % (
        appearance,
        entries,
    )


def appearance(content: bytes, entries: bytes = b"") -> bytes:
    return stream(content, b"/Type /XObject /Subtype /Form /BBox [0 0 100 20] " + entries)


def test_an_appearance_s_objects_come_in_the_stream_s_own_space():
    # Measured: PDFium applies neither the stream's /Matrix nor the placement on /Rect.
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: square(7, b"/F 4"),
            7: appearance(b"0 0 100 20 re f", b"/Matrix [2 0 0 2 0 0]"),
        },
    )
    (drawn,) = appearances_of(data)
    assert (drawn.index, drawn.flags, drawn.appearance_text) == (0, 4, "0 0 100 20 re f")
    (path,) = drawn.drawing.objects
    assert path.matrix == IDENTITY
    assert path.segments[2][1:3] == (100.0, 20.0)


def test_an_entry_pdfium_gives_no_annotation_for_is_none():
    data = page_file(
        b"",
        annots=b"[6 0 R null 42 8 0 R]",
        objects={6: square(7), 7: appearance(b"0 0 m 1 0 l S"), 8: square(7)},
    )
    first, second, third, fourth = appearances_of(data)
    assert second is None and third is None
    assert (first.index, fourth.index) == (0, 3)
    assert len(first.drawing.objects) == len(fourth.drawing.objects) == 1


def test_an_annotation_without_appearance_has_no_objects_and_no_text():
    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: b"<< /Type /Annot /Subtype /Square /Rect [0 0 9 9] >>"}
    )
    (drawn,) = appearances_of(data)
    assert (drawn.appearance_text, drawn.drawing) == (
        "",
        Drawing(objects=(), stopped=False, too_deep=False),
    )


def test_a_hidden_annotation_is_read_and_its_flags_come_as_pdfium_gives_them():
    data = page_file(
        b"",
        annots=b"[6 0 R 8 0 R]",
        objects={
            6: square(7, b"/F 2"),
            7: appearance(b"0 0 m 1 0 l S"),
            8: square(7, b"/F 2147483652"),
        },
    )
    hidden, high = appearances_of(data)
    assert (hidden.flags, len(hidden.drawing.objects)) == (2, 1)
    assert high.flags == -2147483644


def test_an_image_in_an_appearance_and_its_metadata():
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: square(7),
            7: appearance(
                b"q 30 0 0 20 0 0 cm /Im Do Q", b"/Resources << /XObject << /Im 8 0 R >> >>"
            ),
            8: GREY_IMAGE,
        },
    )
    ((image,),) = [drawn.drawing.objects for drawn in appearances_of(data)]
    assert (image.width, image.height, image.colour_space, image.bits_per_pixel) == (
        3,
        2,
        pdfium_c.FPDF_COLORSPACE_DEVICEGRAY,
        8,
    )


def test_a_form_in_an_appearance_is_read_in_the_stream_s_space():
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: square(7),
            7: appearance(b"2 0 0 2 0 0 cm /X Do", b"/Resources << /XObject << /X 8 0 R >> >>"),
            8: stream(b"0 0 m 5 0 l S", FORM + b" /Matrix [1 0 0 1 3 0]"),
        },
    )
    ((path,),) = [drawn.drawing.objects for drawn in appearances_of(data)]
    assert path.matrix == (2.0, 0.0, 0.0, 2.0, 6.0, 0.0)


@pytest.mark.parametrize(
    ("cap", "expected"),
    [
        (None, [(1, False), (1, False), (1, False)]),
        (3, [(1, False), (1, False), (1, False)]),
        (2, [(1, False), (1, False), (0, True)]),
        (1, [(1, False), (0, True)]),
        (0, [(0, True)]),
    ],
)
def test_the_cap_counts_the_objects_of_all_appearances(cap, expected):
    """Three annotations of one line each; the drawing that stops is the last one given."""
    data = page_file(
        b"",
        annots=b"[6 0 R 8 0 R 9 0 R]",
        objects={6: square(7), 7: appearance(b"0 0 m 1 0 l S"), 8: square(7), 9: square(7)},
    )
    drawn = appearances_of(data, cap)
    assert [(len(d.drawing.objects), d.drawing.stopped) for d in drawn] == expected


def test_every_annotation_handle_is_closed(monkeypatch):
    from pdfium_handles import watch_annots

    data = page_file(
        b"",
        annots=b"[6 0 R null 8 0 R]",
        objects={6: square(7), 7: appearance(b"0 0 m 1 0 l S"), 8: square(7)},
    )
    handles = watch_annots(monkeypatch)
    appearances_of(data)
    assert len(handles.opened) == 2
    assert handles.closed == handles.opened


def test_every_annotation_handle_is_closed_when_a_read_fails(monkeypatch):
    from pdfium_handles import watch_annots

    data = page_file(
        b"", annots=b"[6 0 R]", objects={6: square(7), 7: appearance(b"0 0 m 1 0 l S")}
    )
    handles = watch_annots(monkeypatch)
    monkeypatch.setattr(pdfium_c, "FPDFAnnot_GetObjectCount", lambda annot: -1)
    with pytest.raises(EngineFailure):
        appearances_of(data)
    assert len(handles.opened) == 1
    assert handles.closed == handles.opened


def test_reading_the_appearances_leaves_the_rendering_as_it_was():
    """The appearance is turned, so a /Matrix lost in the reading would show."""
    data = page_file(
        b"",
        annots=b"[6 0 R]",
        objects={6: square(7), 7: appearance(b"0 g 0 0 60 20 re f", b"/Matrix [0 1 -1 0 20 0]")},
    )
    document = PdfiumDocument.open(data, None)
    try:
        first = document.render(0, scale=1, crop=(0, 0, 0, 0), rgb=False)
        document.appearance_drawings(0, None)
        second = document.render(0, scale=1, crop=(0, 0, 0, 0), rgb=False)
    finally:
        document.close()
    assert min(first.data) < 128  # the appearance is drawn
    assert bytes(first.data) == bytes(second.data)


# --- what PDFium does not give -------------------------------------------------------------


PAGE_WITH_ALL = page_file(
    b"[4 2] 1 d 0 0 m 50 0 l B /X Do q 30 0 0 20 5 5 cm /Im Do Q",
    resources=b"<< /XObject << /X 5 0 R /Im 6 0 R >> >>",
    objects={5: stream(b"0 0 m 5 0 l S", FORM), 6: GREY_IMAGE},
)


@pytest.mark.parametrize(
    ("function", "returns", "field"),
    [
        ("FPDFPageObj_GetMatrix", 0, "matrix"),
        ("FPDFPath_CountSegments", -1, "segments"),
        ("FPDFPathSegment_GetPoint", 0, "segments"),
        ("FPDFPath_GetDrawMode", 0, "fill_mode"),
        ("FPDFPath_GetDrawMode", 0, "stroke"),
        ("FPDFPageObj_GetFillColor", 0, "fill_colour"),
        ("FPDFPageObj_GetStrokeColor", 0, "stroke_colour"),
        ("FPDFPageObj_GetStrokeWidth", 0, "width"),
        ("FPDFPageObj_GetDashCount", -1, "dash"),
        ("FPDFPageObj_GetDashArray", 0, "dash"),
        ("FPDFPageObj_GetDashPhase", 0, "dash_phase"),
        ("FPDFPageObj_GetLineCap", -1, "cap"),
        ("FPDFPageObj_GetLineJoin", -1, "join"),
    ],
)
def test_a_value_pdfium_does_not_give_is_none(monkeypatch, function, returns, field):
    monkeypatch.setattr(pdfium_c, function, lambda *args: returns)
    first, second, _ = drawing_of(PAGE_WITH_ALL).objects
    assert getattr(first, field) is None
    if function != "FPDFPageObj_GetDashArray":  # the form's path has no dash to read
        assert getattr(second, field) is None


def test_a_matrix_pdfium_does_not_give_for_a_form_is_none_for_what_it_holds(monkeypatch):
    real = pdfium_c.FPDFPageObj_GetMatrix

    def no_form_matrix(obj, matrix):
        if pdfium_c.FPDFPageObj_GetType(obj) == pdfium_c.FPDF_PAGEOBJ_FORM:
            return 0
        return real(obj, matrix)

    monkeypatch.setattr(pdfium_c, "FPDFPageObj_GetMatrix", no_form_matrix)
    first, second, image = drawing_of(PAGE_WITH_ALL).objects
    assert first.matrix == IDENTITY
    assert second.matrix is None
    assert image.matrix == (30.0, 0.0, 0.0, 20.0, 5.0, 5.0)


def test_image_metadata_pdfium_does_not_give_are_none(monkeypatch):
    monkeypatch.setattr(pdfium_c, "FPDFImageObj_GetImageMetadata", lambda *args: 0)
    *_, image = drawing_of(PAGE_WITH_ALL).objects
    assert image == DrawnImage(
        matrix=(30.0, 0.0, 0.0, 20.0, 5.0, 5.0),
        width=None,
        height=None,
        colour_space=None,
        bits_per_pixel=None,
    )


@pytest.mark.parametrize(
    ("function", "returns"),
    [
        ("FPDFPage_CountObjects", -1),
        ("FPDFFormObj_CountObjects", -1),
        ("FPDFPage_GetObject", None),
        ("FPDFFormObj_GetObject", None),
    ],
)
def test_an_object_pdfium_counts_but_does_not_give_is_an_engine_failure(
    monkeypatch, function, returns
):
    monkeypatch.setattr(pdfium_c, function, lambda *args: returns)
    with pytest.raises(EngineFailure) as raised:
        drawing_of(PAGE_WITH_ALL)
    assert raised.value.kind is FailureKind.OTHER


def test_after_close_and_outside_the_pages():
    document = PdfiumDocument.open(page_file(THREE_LINES), None)
    try:
        with pytest.raises(IndexError):
            document.page_drawing(1, None)
        with pytest.raises(IndexError):
            document.appearance_drawings(1, None)
    finally:
        document.close()
    for read in (document.page_drawing, document.appearance_drawings):
        with pytest.raises(EngineFailure) as raised:
            read(0, None)
        assert raised.value.kind is FailureKind.OTHER


def test_no_engine_type_leaves_the_backend():
    data = page_file(
        b"[4 2] 1 d 0 0 m 50 0 l B /X Do q 30 0 0 20 5 5 cm /Im Do Q",
        resources=b"<< /XObject << /X 5 0 R /Im 6 0 R >> >>",
        annots=b"[8 0 R null]",
        objects={5: stream(b"0 0 m 5 0 l S", FORM), 6: GREY_IMAGE, 8: square(5)},
    )
    assert plain(drawing_of(data))
    assert plain(appearances_of(data))


def test_it_implements_the_drawing_reader():
    members = typing.get_protocol_members(DrawingReader)
    assert members == {"page_drawing", "appearance_drawings"}
    assert all(callable(getattr(PdfiumDocument, name)) for name in members)


def test_the_drawing_types_have_their_fields_and_are_frozen():
    assert [f.name for f in dataclasses.fields(DrawnPath)] == [
        "matrix",
        "segments",
        "fill_mode",
        "stroke",
        "fill_colour",
        "stroke_colour",
        "width",
        "dash",
        "dash_phase",
        "cap",
        "join",
    ]
    assert [f.name for f in dataclasses.fields(DrawnImage)] == [
        "matrix",
        "width",
        "height",
        "colour_space",
        "bits_per_pixel",
    ]
    assert [f.name for f in dataclasses.fields(Drawing)] == ["objects", "stopped", "too_deep"]
    assert [f.name for f in dataclasses.fields(AppearanceDrawing)] == [
        "index",
        "appearance_text",
        "flags",
        "drawing",
    ]
    drawing = Drawing(objects=(), stopped=False, too_deep=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        drawing.stopped = True  # type: ignore[misc]
