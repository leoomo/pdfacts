"""Page.render: a page or a region of it as raw bytes.

Inputs are the constructed cases of tests/synthetic/cases.py, each with one
filled rectangle. The expected pixels are computed from the numbers a page was
built with, by the formulas of issue #12, decision 5, typed out here:

    column = (x - rx0) * scale        row = (ry1 - y) * scale

for a point (x, y) in display space and the rendered region
(rx0, ry0, rx1, ry1). They are never taken from Bitmap.display_to_pixel.
"""

from __future__ import annotations

import dataclasses
import math
import threading

import pytest

import pdfacts
from engine_lock import after_waiting
from pdfacts import (
    Bitmap,
    ClosedError,
    Colour,
    EngineError,
    LimitExceededError,
    Limits,
    Matrix,
    Rect,
)
from pdfacts.backends import pdfium as pdfium_backend
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.core import runtime
from pdfium_handles import watch
from synthetic import cases
from synthetic.builder import build, stream

DARK = 128
# A byte below DARK becomes 1, any other 0.
IS_DARK = bytes(1 if value < DARK else 0 for value in range(256))


def first_page(data: bytes, limits: Limits | None = None) -> pdfacts.Page:
    return pdfacts.open(data, limits=limits).pages[0]


def dark_area(bitmap: Bitmap) -> tuple[int, int, int, int]:
    """First and last column, first and last row with every channel below DARK."""
    columns: list[int] = []
    rows: list[int] = []
    step = bitmap.channels
    for row in range(bitmap.height):
        start = row * bitmap.row_length
        line = bitmap.data[start : start + bitmap.width * step].translate(IS_DARK)
        if step > 1:
            line = bytes(min(line[index : index + step]) for index in range(0, len(line), step))
        first = line.find(1)
        if first >= 0:
            columns += [first, line.rfind(1)]
            rows.append(row)
    assert columns, "nothing dark was drawn"
    return min(columns), max(columns), min(rows), max(rows)


def expected_dark_area(
    rectangle: tuple[float, float, float, float],
    region: tuple[float, float, float, float],
    scale: float,
) -> tuple[int, int, int, int]:
    """The pixels a rectangle in display space covers, by decision 5."""
    x0, y0, x1, y1 = rectangle
    rx0, _, _, ry1 = region
    return (
        round((x0 - rx0) * scale),
        round((x1 - rx0) * scale) - 1,
        round((ry1 - y1) * scale),
        round((ry1 - y0) * scale) - 1,
    )


def recording_renders(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = pdfium_backend.PdfiumDocument.render

    def recording(self, index, **kwargs):
        calls.append(index)
        return original(self, index, **kwargs)

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "render", recording)
    return calls


# --- the table of issue #12 --------------------------------------------------------


def test_the_whole_page_in_grey_at_scale_1():
    bitmap = first_page(cases.one_rectangle().data).render()
    assert (bitmap.width, bitmap.height, bitmap.channels) == (400, 300, 1)
    assert bitmap.colour is Colour.GREY
    assert dark_area(bitmap) == expected_dark_area(cases.RECTANGLE, (0, 0, 400, 300), 1)
    assert dark_area(bitmap) == (50, 109, 240, 259)


def test_the_whole_page_at_scale_3():
    bitmap = first_page(cases.one_rectangle().data).render(3)
    assert (bitmap.width, bitmap.height) == (1200, 900)
    assert dark_area(bitmap) == expected_dark_area(cases.RECTANGLE, (0, 0, 400, 300), 3)
    assert dark_area(bitmap) == (150, 329, 720, 779)
    assert bitmap.scale == 3.0


def test_rgb_has_three_channels_and_dark_is_dark_in_each():
    bitmap = first_page(cases.one_rectangle().data).render(colour=Colour.RGB)
    assert (bitmap.width, bitmap.height, bitmap.channels) == (400, 300, 3)
    assert bitmap.colour is Colour.RGB
    assert dark_area(bitmap) == expected_dark_area(cases.RECTANGLE, (0, 0, 400, 300), 1)


def test_rgb_is_red_green_blue_in_that_order():
    case = cases.red_rectangle()
    assert case.colour == cases.RED
    bitmap = first_page(case.data).render(colour=Colour.RGB)
    # The middle of the rectangle: x 80, y 50 in user and display space.
    column, row = 80, 300 - 50
    offset = row * bitmap.row_length + column * 3
    red, green, blue = bitmap.data[offset : offset + 3]
    assert red > 200
    assert green < 50
    assert blue < 50


def test_a_region_in_user_space():
    region = Rect(40, 30, 120, 70)
    bitmap = first_page(cases.one_rectangle().data).render(region=region)
    assert (bitmap.width, bitmap.height) == (80, 40)
    assert bitmap.display_region == region
    assert dark_area(bitmap) == expected_dark_area(cases.RECTANGLE, (40, 30, 120, 70), 1)
    assert dark_area(bitmap) == (10, 69, 10, 29)


def test_a_region_on_a_rotated_page_is_dark_where_to_display_puts_the_rectangle():
    page = first_page(cases.rotated(90).data)
    region = Rect(40, 30, 120, 70)
    bitmap = page.render(region=region)
    to_display = page.geometry.to_display
    shown = to_display.apply_rect(region)
    rectangle = to_display.apply_rect(Rect(*cases.RECTANGLE))
    assert bitmap.display_region == shown
    assert (bitmap.width, bitmap.height) == (40, 80)
    assert dark_area(bitmap) == expected_dark_area(
        (rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y1),
        (shown.x0, shown.y0, shown.x1, shown.y1),
        1,
    )


@pytest.mark.parametrize(
    ("region", "inside"),
    [
        (Rect(350, 250, 500, 400), Rect(350, 250, 400, 300)),
        (Rect(-50, -50, 50, 50), Rect(0, 0, 50, 50)),
    ],
    ids=["at the top right", "at the bottom left"],
)
def test_a_region_partly_outside_is_cut_to_the_page(region, inside):
    bitmap = first_page(cases.one_rectangle().data).render(region=region)
    assert bitmap.display_region == inside
    assert (bitmap.width, bitmap.height) == (50, 50)


@pytest.mark.parametrize(
    "region",
    [
        Rect(500, 500, 600, 600),
        Rect(-200, -200, -100, -100),
        Rect(400, 0, 500, 300),
        Rect(0, 300, 400, 400),
        Rect(-100, 0, 0, 300),
        Rect(0, -100, 400, 0),
        Rect(100, 100, 100, 200),
        Rect(100, 100, 200, 100),
    ],
    ids=[
        "outside at the top right",
        "outside at the bottom left",
        "touching the right edge",
        "touching the top",
        "touching the left edge",
        "touching the bottom",
        "no width",
        "no height",
    ],
)
def test_a_region_without_area_on_the_page_is_refused_before_the_engine(monkeypatch, region):
    calls = recording_renders(monkeypatch)
    page = first_page(cases.one_rectangle().data)
    with pytest.raises(ValueError, match="region"):
        page.render(region=region)
    assert calls == []


@pytest.mark.parametrize(
    ("make", "region", "scale", "colour"),
    [
        (cases.one_rectangle, None, 1, Colour.GREY),
        (cases.one_rectangle, None, 3, Colour.GREY),
        (cases.one_rectangle, None, 1, Colour.RGB),
        (cases.one_rectangle, Rect(40, 30, 120, 70), 1, Colour.GREY),
        (cases.one_rectangle, Rect(40, 30, 120, 70), 3, Colour.GREY),
        (lambda: cases.rotated(90), Rect(40, 30, 120, 70), 1, Colour.GREY),
        (lambda: cases.rotated(270), None, 2, Colour.GREY),
        (cases.crop_box_inside, None, 1, Colour.GREY),
        (cases.one_rectangle, Rect(40, 30, 500, 400), 2, Colour.GREY),
        (cases.one_rectangle, Rect(-50, -50, 120, 70), 1, Colour.GREY),
    ],
    ids=[
        "whole page",
        "scale 3",
        "rgb",
        "region",
        "region at scale 3",
        "region, rotation 90",
        "rotation 270, scale 2",
        "crop box",
        "region partly outside at the top right, scale 2",
        "region partly outside at the bottom left",
    ],
)
def test_display_to_pixel_maps_the_rectangle_onto_the_dark_area(make, region, scale, colour):
    page = first_page(make().data)
    bitmap = page.render(scale, region=region, colour=colour)
    corners = bitmap.display_to_pixel.apply_rect(
        page.geometry.to_display.apply_rect(Rect(*cases.RECTANGLE))
    )
    first_column, last_column, first_row, last_row = dark_area(bitmap)
    # The dark area covers pixels; its outer edges are one further.
    for mapped, drawn in zip(
        (corners.x0, corners.x1, corners.y0, corners.y1),
        (first_column, last_column + 1, first_row, last_row + 1),
        strict=True,
    ):
        assert abs(mapped - drawn) <= 1, (corners, dark_area(bitmap))


def test_display_to_pixel_is_the_matrix_of_decision_5():
    bitmap = first_page(cases.one_rectangle().data).render(2, region=Rect(40, 30, 120, 70))
    # (s, 0, 0, -s, -rx0 * s, ry1 * s)
    assert bitmap.display_to_pixel == Matrix(2, 0, 0, -2, -80, 140)


@pytest.mark.parametrize("colour", [Colour.GREY, Colour.RGB])
def test_the_data_is_row_length_times_height(colour):
    # 81 units wide: a row of 243 bytes in RGB is not a multiple of four.
    bitmap = first_page(cases.one_rectangle().data).render(
        region=Rect(40, 30, 121, 70), colour=colour
    )
    assert bitmap.width == 81
    assert bitmap.row_length >= bitmap.width * bitmap.channels
    assert len(bitmap.data) == bitmap.row_length * bitmap.height
    assert type(bitmap.data) is bytes


def test_the_bitmap_holds_no_native_memory():
    document = pdfacts.open(cases.one_rectangle().data)
    bitmap = document.pages[0].render()
    document.close()
    assert dark_area(bitmap) == (50, 109, 240, 259)
    assert sum(bitmap.data) > 0


@pytest.mark.parametrize("scale", [0, -1, math.inf, math.nan, 0.0])
def test_a_scale_that_is_not_finite_and_above_zero_is_refused(monkeypatch, scale):
    calls = recording_renders(monkeypatch)
    page = first_page(cases.one_rectangle().data)
    with pytest.raises(ValueError, match="scale"):
        page.render(scale)
    assert calls == []


def test_the_pixel_limit_is_checked_before_the_engine(monkeypatch):
    calls = recording_renders(monkeypatch)
    page = first_page(cases.one_rectangle().data, Limits(max_render_pixels=400 * 300 - 1))
    with pytest.raises(LimitExceededError) as raised:
        page.render()
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_render_pixels",
        400 * 300 - 1,
        400 * 300,
    )
    assert calls == []


def test_a_closed_page_is_refused():
    document = pdfacts.open(cases.one_rectangle().data)
    page = document.pages[0]
    document.close()
    with pytest.raises(ClosedError):
        page.render()


# --- beyond the table ---------------------------------------------------------------


def test_as_many_pixels_as_the_limit_allows():
    page = first_page(cases.one_rectangle().data, Limits(max_render_pixels=400 * 300))
    assert page.render().width == 400


def test_the_pixel_limit_counts_the_region_at_its_scale():
    # 80 x 40 units at scale 2: 160 x 80 pixels.
    page = first_page(cases.one_rectangle().data, Limits(max_render_pixels=160 * 80 - 1))
    with pytest.raises(LimitExceededError) as raised:
        page.render(2, region=Rect(40, 30, 120, 70))
    assert raised.value.found == 160 * 80


def test_the_page_turned_by_its_rotation():
    page = first_page(cases.rotated(90).data)
    bitmap = page.render()
    assert (bitmap.width, bitmap.height) == (300, 400)
    rectangle = page.geometry.to_display.apply_rect(Rect(*cases.RECTANGLE))
    assert dark_area(bitmap) == expected_dark_area(
        (rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y1), (0, 0, 300, 400), 1
    )


def test_the_whole_page_is_the_visible_box():
    case = cases.crop_box_inside()
    bitmap = first_page(case.data).render()
    x0, y0, x1, y1 = case.crop_box
    assert (bitmap.width, bitmap.height) == (x1 - x0, y1 - y0)
    assert bitmap.display_region == Rect(0, 0, x1 - x0, y1 - y0)


def test_the_colour_may_be_given_by_its_name():
    assert first_page(cases.one_rectangle().data).render(colour="rgb").colour is Colour.RGB


def test_a_colour_that_is_not_one_is_refused():
    with pytest.raises(ValueError):
        first_page(cases.one_rectangle().data).render(colour="cmyk")


@pytest.mark.parametrize("scale", [True, "2", None])
def test_a_scale_that_is_not_a_number_is_a_type_error(scale):
    with pytest.raises(TypeError, match="scale"):
        first_page(cases.one_rectangle().data).render(scale)


def test_a_region_that_is_not_a_rect_is_a_type_error():
    with pytest.raises(TypeError, match="region"):
        first_page(cases.one_rectangle().data).render(region=(40, 30, 120, 70))


def test_a_region_smaller_than_a_pixel_is_a_value_error():
    # 0.4 units at scale 1: the engine leaves no whole pixel of it.
    with pytest.raises(ValueError, match="pixel"):
        first_page(cases.one_rectangle().data).render(region=Rect(50, 40, 50.4, 60))


def test_the_bitmap_is_frozen():
    bitmap = first_page(cases.one_rectangle().data).render()
    with pytest.raises(dataclasses.FrozenInstanceError):
        bitmap.width = 1  # type: ignore[misc]


def test_rendering_holds_the_engine_lock(monkeypatch):
    held: list[bool] = []
    original = pdfium_backend.PdfiumDocument.render

    def watching(self, index, **kwargs):
        free: list[bool] = []

        def probe() -> None:
            if runtime.engine_lock.acquire(blocking=False):
                runtime.engine_lock.release()
                free.append(True)
            else:
                free.append(False)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        held.append(not free[0])
        return original(self, index, **kwargs)

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "render", watching)
    first_page(cases.one_rectangle().data).render()
    assert held == [True]


def test_rendering_is_a_capability_and_bitmap_and_colour_are_exported():
    assert pdfacts.capabilities()[pdfacts.Capability.RENDERING] is True
    pdfacts.require("rendering")
    assert {"Bitmap", "Colour"} <= set(pdfacts.__all__)
    assert [colour.value for colour in Colour] == ["grey", "rgb"]


def test_a_render_asked_for_while_the_document_closes_is_refused_as_closed(monkeypatch):
    document = pdfacts.open(cases.one_rectangle().data)
    page = document.pages[0]
    outcome = after_waiting(monkeypatch, page.render, document.close)
    assert isinstance(outcome, ClosedError)


def test_a_closed_page_is_refused_before_its_arguments_are_looked_at():
    document = pdfacts.open(cases.one_rectangle().data)
    page = document.pages[0]
    document.close()
    with pytest.raises(ClosedError):
        page.render(0, region=(1, 2, 3, 4), colour="cmyk")  # type: ignore[arg-type]


def test_a_failure_of_the_engine_is_an_engine_error(monkeypatch):
    def failing(self, index, **kwargs):
        raise EngineFailure(FailureKind.OTHER, "render failed")

    page = first_page(cases.one_rectangle().data)
    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "render", failing)
    with pytest.raises(EngineError) as raised:
        page.render()
    assert (raised.value.engine, raised.value.detail) == ("pdfium", "render failed")


@pytest.mark.parametrize(("allowed", "refused"), [(81 * 41 - 1, True), (81 * 41, False)])
def test_the_pixel_limit_counts_each_side_rounded_up(allowed, refused):
    # 80.5 x 40.2 units at scale 1: 81 x 41 pixels by decision 8.
    page = first_page(cases.one_rectangle().data, Limits(max_render_pixels=allowed))
    region = Rect(40, 30, 120.5, 70.2)
    if refused:
        with pytest.raises(LimitExceededError) as raised:
            page.render(region=region)
        assert raised.value.found == 81 * 41
    else:
        assert page.render(region=region).display_region == region


@pytest.mark.parametrize(
    ("make", "scale", "region"),
    [
        (cases.one_rectangle, 1e307, None),
        (cases.one_rectangle, 1e306, Rect(50, 40, 51, 41)),
        (cases.one_rectangle, 5e305, None),
        (lambda: cases.rotated(90), 5e305, None),
    ],
    ids=["page", "small region", "only the width", "only the height"],
)
def test_a_scale_at_which_the_page_has_no_finite_size_is_refused_before_the_engine(
    monkeypatch, make, scale, region
):
    # The engine scales the whole page, not only the region. At 5e305 the
    # side of 400 units overflows and the side of 300 units does not.
    calls = recording_renders(monkeypatch)
    page = first_page(make().data)
    with pytest.raises(ValueError, match="finite size"):
        page.render(scale, region=region)
    assert calls == []


@pytest.mark.parametrize(
    ("make", "scale"),
    [(cases.one_rectangle, 6 * 10**6), (lambda: cases.rotated(90), 6 * 10**6)],
    ids=["too wide", "too high"],
)
def test_a_scale_at_which_the_page_is_more_pixels_than_the_engine_addresses_is_a_value_error(
    make, scale
):
    # A side of more than 2**31 - 1 pixels, more than a C int holds: at
    # 6 * 10**6 the side of 400 units is, the side of 300 units is not; the
    # page is 400 wide, or on the turned page 400 high. The region stays small.
    with pytest.raises(ValueError, match="more than PDFium addresses"):
        first_page(make().data).render(scale, region=Rect(50, 40, 50.0001, 40.0001))


# --- what is drawn ----------------------------------------------------------------

# A form that fills its whole box black, the appearance of each annotation.
BLACK_FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 40 40]"
ANNOTATION = b" /Rect [10 10 50 50] /AP << /N 6 0 R >>"


@pytest.mark.parametrize(
    ("annotation", "drawn"),
    [
        (b"/Subtype /Square" + ANNOTATION, True),
        (b"/Subtype /Square /F 1" + ANNOTATION, True),
        (b"/Subtype /Link" + ANNOTATION, True),
        (b"/Subtype /Square /Rect [10 10 50 50] /C [0 0 0] /IC [0 0 0]", True),
        (b"/Subtype /Square /F 2" + ANNOTATION, False),
        (b"/Subtype /Square /F 32" + ANNOTATION, False),
        (b"/Subtype /Popup" + ANNOTATION, False),
        (b"/Subtype /Widget /FT /Tx /T (a)" + ANNOTATION, False),
        (
            b"/Subtype /Widget /FT /Btn /T (b) /AS /On /Rect [10 10 50 50]"
            b" /AP << /N << /On 6 0 R >> >>",
            False,
        ),
    ],
    ids=[
        "an appearance",
        "flagged invisible",
        "a link",
        "a square without an appearance, PDFium's own",
        "flagged hidden",
        "flagged not to be viewed",
        "a popup",
        "a text field",
        "a check box",
    ],
)
def test_what_render_draws(annotation, drawn):
    """docs/conventions.md, "Rendering": the content and the annotations'
    normal appearances as PDFium draws them by default (measured, 2026-10-06)."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [5 0 R] >>",
            5: b"<< /Type /Annot " + annotation + b" >>",
            6: stream(b"0 g 0 0 40 40 re f", BLACK_FORM),
        },
        1,
    )
    with pdfacts.open(data) as document:
        bitmap = document.pages[0].render(scale=1)
    # (30, 30) in user space, inside the annotation: column 30, row 100 - 30.
    value = bitmap.data[70 * bitmap.row_length + 30 * bitmap.channels]
    assert (value < DARK) is drawn


# --- rendering changes no fact (issue A1) -------------------------------------------

# Annotations without an appearance to which PDFium adds one as it draws them
# (measured, issue A1), with the entries each is built with.
COMPLETED = {
    "Square": b"/C [1 0 0]",
    "Circle": b"/C [1 0 0]",
    "Ink": b"/C [1 0 0] /InkList [[60 60 80 80]]",
    "Highlight": b"/C [1 1 0] /QuadPoints [60 80 80 80 60 60 80 60]",
    "Underline": b"/C [1 0 0] /QuadPoints [60 80 80 80 60 60 80 60]",
    "Squiggly": b"/C [1 0 0] /QuadPoints [60 80 80 80 60 60 80 60]",
    "StrikeOut": b"/C [1 0 0] /QuadPoints [60 80 80 80 60 60 80 60]",
    "Text": b"/C [1 1 0]",
    "FreeText": b"/DA (/Helv 12 Tf 0 g) /Contents (x)",
}


def every_fact(document: pdfacts.Document) -> tuple:
    readers = (
        pdfacts.paths,
        pdfacts.images,
        pdfacts.glyphs,
        pdfacts.words,
        pdfacts.lines,
        pdfacts.widgets,
    )
    return tuple(read(document, 0) for read in readers)


@pytest.mark.parametrize("kind", list(COMPLETED))
def test_rendering_changes_no_fact(kind):
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [5 0 R] >>",
            5: b"<< /Type /Annot /Subtype /"
            + kind.encode()
            + b" /Rect [60 60 80 80] "
            + COMPLETED[kind]
            + b" >>",
        },
        1,
    )
    with pdfacts.open(data) as document:
        before = every_fact(document)
        document.pages[0].render()
        after = every_fact(document)
    # The annotation has no appearance: no path and no image come from it.
    assert before[:2] == ((), ())
    assert after == before


def test_the_document_that_renders_is_opened_once_and_closed_with_the_others(monkeypatch):
    handles = watch(monkeypatch)
    document = pdfacts.open(cases.one_rectangle().data)
    opened = len(handles.loaded)
    document.pages[0].render()
    document.pages[0].render(scale=2)
    assert len(handles.loaded) == opened + 1
    document.close()
    assert sorted(handles.freed) == sorted(handles.loaded)


def test_an_encrypted_document_renders_with_its_password():
    case = cases.user_password()
    with pdfacts.open(case.data, password=case.user_password) as document:
        bitmap = document.pages[0].render()
    assert dark_area(bitmap) == expected_dark_area(cases.RECTANGLE, (0, 0, 400, 300), 1)
