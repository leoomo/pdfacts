"""The PDFium backend: page count, visible box, rotation, failures, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import ctypes
import sys
import typing
from functools import partial

import pypdfium2
import pypdfium2.raw
import pytest

from pdfacts.backends import pdfium as pdfium_backend
from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import (
    EngineFailure,
    FailureKind,
    GeometryReader,
    RawBitmap,
    Renderer,
)
from pdfium_handles import watch
from synthetic import cases
from synthetic.builder import build, stream

# The box PDFium uses for a page without any media box: US letter.
LETTER = (0.0, 0.0, 612.0, 792.0)


def opened(data: bytes, password: str | None = None) -> PdfiumDocument:
    return PdfiumDocument.open(data, password)


def ordered(box: cases.Box) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def failure(data: bytes, password: str | None = None) -> EngineFailure:
    with pytest.raises(EngineFailure) as raised:
        opened(data, password)
    return raised.value


def plain(value: object) -> bool:
    """Built from int, float, str, bool, None and tuple only."""
    if value is None or type(value) in (int, float, str, bool):
        return True
    if type(value) is tuple:
        return all(plain(item) for item in value)
    return False


def counting_closes(monkeypatch) -> list[int]:
    """The handles this test loads that PDFium is asked to close, from now on."""
    return watch(monkeypatch).freed


def render(
    document: PdfiumDocument,
    index: int,
    *,
    scale: float = 1,
    crop: tuple[float, float, float, float] = (0, 0, 0, 0),
    rgb: bool = False,
) -> RawBitmap:
    return document.render(index, scale=scale, crop=crop, rgb=rgb)


def test_the_page_count_of_three_pages():
    case = cases.three_pages()
    document = opened(case.data)
    assert document.page_count() == len(case.page_objects) == 3


@pytest.mark.parametrize("name", list(cases.NO_PAGES))
def test_a_file_without_pages_is_a_format_failure_and_its_handle_is_closed(monkeypatch, name):
    closed = counting_closes(monkeypatch)
    raised = failure(cases.NO_PAGES[name]().data)
    assert raised.kind is FailureKind.FORMAT
    assert "no page" in raised.detail
    assert len(closed) == 1


@pytest.mark.parametrize("name", list(cases.REBUILT))
def test_a_file_read_only_by_rebuilding_its_table_is_a_format_failure(monkeypatch, name):
    closed = counting_closes(monkeypatch)
    raised = failure(cases.REBUILT[name]().data)
    assert raised.kind is FailureKind.FORMAT
    assert "rebuilding" in raised.detail
    assert len(closed) == 1


def test_a_valid_incremental_update_opens_and_its_update_counts():
    case = cases.incremental_update()
    assert opened(case.data).visible_box(0) == tuple(float(v) for v in case.media_box)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (pypdfium2.raw.FPDF_ERR_FORMAT, FailureKind.FORMAT),
        (pypdfium2.raw.FPDF_ERR_PASSWORD, FailureKind.PASSWORD),
        (pypdfium2.raw.FPDF_ERR_UNKNOWN, FailureKind.OTHER),
        (pypdfium2.raw.FPDF_ERR_FILE, FailureKind.OTHER),
        (pypdfium2.raw.FPDF_ERR_SECURITY, FailureKind.OTHER),
        (pypdfium2.raw.FPDF_ERR_PAGE, FailureKind.OTHER),
        (99, FailureKind.OTHER),
    ],
)
def test_the_error_code_of_a_failed_load_gives_the_kind(monkeypatch, code, kind):
    monkeypatch.setattr(pypdfium2.raw, "FPDF_LoadMemDocument64", lambda *args: None)
    monkeypatch.setattr(pypdfium2.raw, "FPDF_GetLastError", lambda: code)
    raised = failure(cases.plain_page().data)
    assert raised.kind is kind
    assert f"(code {code})" in raised.detail


def test_the_backend_keeps_the_bytes_pdfium_reads_from():
    # PDFium reads from the buffer for as long as the document is open; the
    # caller may hold no reference of its own.
    data = bytes(bytearray(cases.three_pages().data))
    before = sys.getrefcount(data)
    document = opened(data)
    assert sys.getrefcount(data) > before
    document.close()


def test_a_failure_while_wrapping_the_handle_never_closes_it_twice(monkeypatch):
    # pypdfium2 may have attached its own finalizer before it failed; closing
    # here as well could close one handle twice. A leak is the lesser harm.
    closed = counting_closes(monkeypatch)

    def raising(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr(pypdfium2, "PdfDocument", raising)
    with pytest.raises(MemoryError):
        opened(cases.plain_page().data)
    assert closed == []


@pytest.mark.parametrize("name", list(cases.MISSING_PAGES))
def test_a_page_pdfium_counts_but_cannot_find_fails_the_page_check(name):
    document = opened(cases.MISSING_PAGES[name]().data)
    with pytest.raises(EngineFailure) as raised:
        document.check_pages()
    assert raised.value.kind is FailureKind.FORMAT
    assert "no page dictionary" in raised.value.detail
    document.close()


def test_every_page_of_an_intact_file_passes_the_page_check():
    for case in (cases.three_pages(), cases.media_box_on_pages(), cases.no_media_box()):
        opened(case.data).check_pages()


def test_the_page_check_of_a_closed_document_fails():
    document = opened(cases.three_pages().data)
    document.close()
    with pytest.raises(EngineFailure) as raised:
        document.check_pages()
    assert raised.value.detail == "closed"


def test_a_failing_close_does_not_hide_why_the_file_was_refused(monkeypatch):
    def failing(self, *args, **kwargs):
        raise RuntimeError("closing failed as well")

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", failing)
    raised = failure(cases.empty_page_tree().data)
    assert (raised.kind, raised.detail) == (
        FailureKind.FORMAT,
        "PDFium finds no page in the document",
    )


def every_method_refuses(document: PdfiumDocument) -> None:
    calls = [
        document.page_count,
        document.check_pages,
        lambda: document.visible_box(0),
        lambda: document.rotation(0),
        lambda: render(document, 0),
    ]
    for call in calls:
        with pytest.raises(EngineFailure) as raised:
            call()
        assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")


def test_a_close_interrupted_before_the_engine_closed_is_finished_by_the_next(monkeypatch):
    freed = counting_closes(monkeypatch)
    calls = []
    original = pypdfium2.PdfDocument.close

    def interrupted_once(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", interrupted_once)
    document = opened(cases.plain_page().data)
    with pytest.raises(KeyboardInterrupt):
        document.close()
    every_method_refuses(document)
    assert freed == []
    document.close()
    assert len(freed) == 1


def test_a_close_interrupted_after_the_engine_closed_refuses_every_use(monkeypatch):
    # The interrupt comes after PDFium freed the handle and before pypdfium2
    # forgot it: nothing may use the handle again, nor free it again.
    freed = counting_closes(monkeypatch)
    original = pypdfium2.PdfDocument.close

    def interrupted_after(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", interrupted_after)
    document = opened(cases.three_pages().data)
    with pytest.raises(KeyboardInterrupt):
        document.close()
    every_method_refuses(document)
    monkeypatch.setattr(pypdfium2.PdfDocument, "close", original)
    document.close()
    assert len(freed) == 1


def test_an_interrupt_inside_pypdfium2_after_the_handle_was_freed(monkeypatch):
    # PDFium freed the handle; pypdfium2 has not forgotten it yet.
    handles = watch(monkeypatch)
    watched = pypdfium2.raw.FPDF_CloseDocument

    def interrupted_after(handle):
        watched(handle)
        if handles.owns(handle):
            raise KeyboardInterrupt

    monkeypatch.setattr(pypdfium2.raw, "FPDF_CloseDocument", interrupted_after)
    document = opened(cases.three_pages().data)
    with pytest.raises(KeyboardInterrupt):
        document.close()
    every_method_refuses(document)
    document.close()
    assert len(handles.freed) == 1


def test_an_exception_while_closing_is_an_other_failure(monkeypatch):
    document = opened(cases.plain_page().data)
    original = pypdfium2.PdfDocument.close

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("closing failed")

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", failing)
    with pytest.raises(EngineFailure) as raised:
        document.close()
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closing failed")
    every_method_refuses(document)


def test_a_memory_error_while_closing_a_refused_file_is_not_swallowed(monkeypatch):
    error = MemoryError()

    def failing(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", failing)
    with pytest.raises(MemoryError) as raised:
        opened(cases.empty_page_tree().data)
    assert raised.value is error


def test_an_exception_in_the_page_check_is_an_other_failure(monkeypatch):
    document = opened(cases.three_pages().data)

    def raising(*args):
        raise ctypes.ArgumentError("wrong argument")

    monkeypatch.setattr(pypdfium2.raw, "FPDF_GetPageSizeByIndexF", raising)
    with pytest.raises(EngineFailure) as raised:
        document.check_pages()
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "wrong argument")


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (cases.plain_page, lambda case: case.media_boxes[0]),
        (cases.crop_box_inside, lambda case: case.crop_box),
        (cases.crop_box_larger, lambda case: case.media_boxes[0]),
        (cases.reversed_corners, lambda case: ordered(case.media_boxes[0])),
        (cases.media_box_on_pages, lambda case: case.pages_media_box),
        (cases.shifted_origin, lambda case: case.media_boxes[0]),
        (cases.no_media_box, lambda case: LETTER),
    ],
    ids=[
        "plain",
        "crop box inside",
        "crop box larger than the media box",
        "reversed corners",
        "media box only on the page tree",
        "shifted origin",
        "no media box",
    ],
)
def test_the_visible_box(make, expected):
    case = make()
    box = opened(case.data).visible_box(0)
    assert box == tuple(float(value) for value in expected(case))
    assert all(type(value) is float for value in box)


def test_the_visible_box_of_each_page():
    case = cases.three_pages()
    document = opened(case.data)
    boxes = [document.visible_box(index) for index in range(3)]
    assert boxes == [tuple(float(value) for value in box) for box in case.media_boxes]


@pytest.mark.parametrize(
    ("written", "expected"),
    [(0, 0), (90, 90), (180, 180), (270, 270), (-90, 270), (450, 90), (45, 0)],
)
def test_the_rotation(written, expected):
    assert opened(cases.rotated(written).data).rotation(0) == expected


def test_the_rotation_only_on_the_page_tree():
    case = cases.media_box_and_rotation_on_pages()
    assert opened(case.data).rotation(0) == case.pages_rotate == 90


@pytest.mark.parametrize("name", list(cases.UNUSABLE))
def test_an_unusable_file_is_a_format_failure(name):
    assert failure(cases.UNUSABLE[name]().data).kind is FailureKind.FORMAT


@pytest.mark.parametrize("name", list(cases.TRUNCATED))
def test_a_truncated_file_is_a_format_failure(name):
    assert failure(cases.TRUNCATED[name]().data).kind is FailureKind.FORMAT


def test_a_user_password_is_needed():
    case = cases.user_password()
    assert failure(case.data).kind is FailureKind.PASSWORD
    assert failure(case.data, "wrong").kind is FailureKind.PASSWORD
    assert opened(case.data, case.user_password).page_count() == case.pages_built
    assert opened(case.data, case.owner_password).page_count() == case.pages_built


def test_a_file_with_only_an_owner_password_opens():
    case = cases.owner_password_only()
    assert opened(case.data).page_count() == case.pages_built


@pytest.mark.parametrize("index", [-1, 3])
def test_an_index_outside_the_pages_is_an_index_error(index):
    document = opened(cases.three_pages().data)
    for method in (document.visible_box, document.rotation, partial(render, document)):
        with pytest.raises(IndexError):
            method(index)


@pytest.mark.parametrize("index", [True, 1.0, "1"])
def test_an_index_that_is_not_an_int_is_a_type_error(index):
    document = opened(cases.three_pages().data)
    for method in (document.visible_box, document.rotation, partial(render, document)):
        with pytest.raises(TypeError):
            method(index)


def test_after_close_every_method_fails_and_close_does_nothing(monkeypatch):
    freed = counting_closes(monkeypatch)
    document = opened(cases.plain_page().data)
    document.close()
    every_method_refuses(document)
    document.close()
    assert len(freed) == 1


def test_a_page_is_closed_before_the_method_returns(monkeypatch):
    opened_pages, closed_pages = [], []
    original_init, original_close = pypdfium2.PdfPage.__init__, pypdfium2.PdfPage.close

    def counting_init(self, *args, **kwargs):
        opened_pages.append(self)
        original_init(self, *args, **kwargs)

    def counting_close(self, *args, **kwargs):
        closed_pages.append(self)
        return original_close(self, *args, **kwargs)

    monkeypatch.setattr(pypdfium2.PdfPage, "__init__", counting_init)
    monkeypatch.setattr(pypdfium2.PdfPage, "close", counting_close)
    document = opened(cases.three_pages().data)
    document.visible_box(1)
    document.rotation(2)
    render(document, 0)
    assert len(opened_pages) == 3
    assert all(any(page is closed for closed in closed_pages) for page in opened_pages)


@pytest.mark.parametrize("exception", [MemoryError, KeyboardInterrupt])
def test_memory_error_and_interrupts_pass_through(monkeypatch, exception):
    def raising(*args, **kwargs):
        raise exception

    monkeypatch.setattr(pypdfium2, "PdfDocument", raising)
    with pytest.raises(exception):
        opened(cases.plain_page().data)


@pytest.mark.parametrize(
    "exception", [RuntimeError("inside the engine"), ctypes.ArgumentError("wrong argument")]
)
def test_any_other_exception_is_an_other_failure(monkeypatch, exception):
    def raising(*args, **kwargs):
        raise exception

    monkeypatch.setattr(pypdfium2, "PdfDocument", raising)
    raised = failure(cases.plain_page().data)
    assert raised.kind is FailureKind.OTHER
    assert raised.detail == str(exception)


@pytest.mark.parametrize(
    "code",
    [
        pypdfium2.raw.FPDF_ERR_UNKNOWN,
        pypdfium2.raw.FPDF_ERR_FILE,
        pypdfium2.raw.FPDF_ERR_SECURITY,
        pypdfium2.raw.FPDF_ERR_PAGE,
        None,
    ],
)
def test_error_codes_other_than_format_and_password_are_other_failures(monkeypatch, code):
    def raising(*args, **kwargs):
        raise pypdfium2.PdfiumError("refused", err_code=code)

    monkeypatch.setattr(pypdfium2, "PdfDocument", raising)
    raised = failure(cases.plain_page().data)
    assert (raised.kind, raised.detail) == (FailureKind.OTHER, "refused")


def test_an_exception_while_reading_a_page_is_an_other_failure(monkeypatch):
    document = opened(cases.plain_page().data)

    def raising(*args, **kwargs):
        raise ctypes.ArgumentError("wrong argument")

    monkeypatch.setattr(pypdfium2.PdfPage, "get_rotation", raising)
    with pytest.raises(EngineFailure) as raised:
        document.rotation(0)
    assert raised.value.kind is FailureKind.OTHER


def test_no_engine_type_leaves_the_backend():
    case = cases.crop_box_inside_rotated()
    document = opened(case.data)
    results = [document.page_count(), document.visible_box(0), document.rotation(0)]
    assert all(plain(result) for result in results), results
    assert plain(document.close())


def test_it_implements_the_geometry_reader():
    members = typing.get_protocol_members(GeometryReader)
    assert members == {"page_count", "visible_box", "rotation", "close"}
    assert all(callable(getattr(PdfiumDocument, name)) for name in members)


# --- rendering -----------------------------------------------------------------------


def dark_extent(bitmap: RawBitmap) -> tuple[int, int, int, int]:
    """First and last column, first and last row with every channel below 128."""
    columns, rows = [], []
    for row in range(bitmap.height):
        for column in range(bitmap.width):
            offset = row * bitmap.row_length + column * bitmap.channels
            if all(value < 128 for value in bitmap.data[offset : offset + bitmap.channels]):
                columns.append(column)
                rows.append(row)
    return min(columns), max(columns), min(rows), max(rows)


def test_render_gives_plain_bytes_of_the_size_the_engine_made():
    bitmap = render(opened(cases.one_rectangle().data), 0)
    assert type(bitmap) is RawBitmap
    assert type(bitmap.data) is bytes
    assert (bitmap.width, bitmap.height, bitmap.row_length, bitmap.channels) == (400, 300, 400, 1)
    assert len(bitmap.data) == bitmap.row_length * bitmap.height
    assert dark_extent(bitmap) == (50, 109, 240, 259)


@pytest.mark.parametrize(
    ("scale", "size", "dark"),
    [(1, (80, 40), (10, 69, 10, 29)), (3, (240, 120), (30, 209, 30, 89))],
)
def test_the_crop_cuts_off_each_side_of_the_displayed_page(scale, size, dark):
    # (left, bottom, right, top) in display units before scaling (issue #12).
    bitmap = render(opened(cases.one_rectangle().data), 0, scale=scale, crop=(40, 30, 280, 230))
    assert (bitmap.width, bitmap.height) == size
    assert dark_extent(bitmap) == dark


def test_rgb_is_three_bytes_in_the_order_red_green_blue():
    bitmap = render(opened(cases.red_rectangle().data), 0, rgb=True)
    assert (bitmap.channels, bitmap.row_length) == (3, 1200)
    # The middle of the rectangle: x 80, y 50; row 0 is the top.
    offset = (300 - 50) * bitmap.row_length + 80 * 3
    red, green, blue = bitmap.data[offset : offset + 3]
    assert (red > 200, green < 50, blue < 50) == (True, True, True)


def test_a_crop_that_leaves_less_than_a_pixel_is_a_value_error():
    with pytest.raises(ValueError, match="less than one pixel"):
        render(opened(cases.one_rectangle().data), 0, crop=(50, 0, 349.6, 0))


def test_an_exception_while_rendering_is_an_other_failure(monkeypatch):
    document = opened(cases.one_rectangle().data)

    def raising(*args, **kwargs):
        raise ctypes.ArgumentError("wrong argument")

    monkeypatch.setattr(pypdfium2.PdfPage, "render", raising)
    with pytest.raises(EngineFailure) as raised:
        render(document, 0)
    assert raised.value.kind is FailureKind.OTHER


def counting_bitmaps(monkeypatch) -> tuple[list[tuple], list[tuple]]:
    """The bitmaps PDFium is asked to make, and to destroy, from now on."""
    made: list[tuple] = []
    destroyed: list[tuple] = []
    create, destroy = pypdfium2.raw.FPDFBitmap_CreateEx, pypdfium2.raw.FPDFBitmap_Destroy

    def counting_create(*args):
        made.append(args)
        return create(*args)

    def counting_destroy(*args):
        destroyed.append(args)
        return destroy(*args)

    monkeypatch.setattr(pypdfium2.raw, "FPDFBitmap_CreateEx", counting_create)
    monkeypatch.setattr(pypdfium2.raw, "FPDFBitmap_Destroy", counting_destroy)
    return made, destroyed


def test_every_bitmap_pdfium_makes_is_destroyed_before_render_returns(monkeypatch):
    made, destroyed = counting_bitmaps(monkeypatch)
    document = opened(cases.one_rectangle().data)
    for rgb in (False, True, False):
        render(document, 0, crop=(40, 30, 279, 230), rgb=rgb)
        assert len(destroyed) == len(made)
    assert len(made) == 3


def test_the_bitmap_is_destroyed_also_when_copying_its_bytes_fails(monkeypatch):
    made, destroyed = counting_bitmaps(monkeypatch)

    def failing(buffer):
        raise RuntimeError("copy failed")

    # The backend's own name for bytes; pypdfium2 keeps the built-in one.
    monkeypatch.setattr(pdfium_backend, "bytes", failing, raising=False)
    document = opened(cases.one_rectangle().data)
    with pytest.raises(EngineFailure) as raised:
        render(document, 0)
    # The failure keeps the frame that holds the bitmap alive; it is
    # destroyed all the same.
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "copy failed")
    assert len(made) == len(destroyed) == 1


@pytest.mark.parametrize(
    ("make", "crop"),
    [
        (cases.one_rectangle, (0, 0, 399.99999, 299.99999)),
        (lambda: cases.rotated(90), (0, 0, 299.99999, 399.99999)),
    ],
    ids=["too wide", "too high"],
)
def test_the_page_at_a_scale_beyond_what_pdfium_addresses_is_a_value_error(make, crop):
    # At scale 6 * 10**6 the side of 400 units is 2.4 * 10**9 pixels, more than
    # a C int holds, and the side of 300 units is not. The crop leaves 60 x
    # 60, so that nothing large is allocated.
    with pytest.raises(ValueError, match="more than PDFium addresses"):
        render(opened(make().data), 0, scale=6 * 10**6, crop=crop)


@pytest.mark.parametrize(("side", "allowed"), [(2**31 - 1, True), (2**31, False)])
def test_a_page_side_of_up_to_2_31_minus_1_pixels_goes_to_pdfium(monkeypatch, side, allowed):
    # A stand-in for pypdfium2's render, so that no page of that size is drawn.
    def stand_in(self, **kwargs):
        raise RuntimeError("would have rendered")

    monkeypatch.setattr(pypdfium2.PdfPage, "render", stand_in)
    document = opened(cases.one_rectangle().data)
    # The page is 400 units wide; the product is exact in floating point.
    scale = side / 400
    if allowed:
        with pytest.raises(EngineFailure, match="would have rendered"):
            render(document, 0, scale=scale)
    else:
        with pytest.raises(ValueError, match="more than PDFium addresses"):
            render(document, 0, scale=scale)


def test_the_bitmap_is_destroyed_also_when_an_interrupt_comes_inside_pypdfium2(monkeypatch):
    made, destroyed = counting_bitmaps(monkeypatch)

    def interrupted(*args):
        raise KeyboardInterrupt

    # pypdfium2 has made the bitmap and not yet returned it.
    monkeypatch.setattr(pypdfium2.raw, "FPDF_RenderPageBitmap", interrupted)
    document = opened(cases.one_rectangle().data)
    with pytest.raises(KeyboardInterrupt) as raised:
        render(document, 0)
    # The traceback keeps pypdfium2's frame, and the bitmap, alive; it is
    # destroyed all the same.
    assert len(made) == len(destroyed) == 1
    assert raised.type is KeyboardInterrupt


def test_it_implements_the_renderer():
    assert typing.get_protocol_members(Renderer) == {"render"}
    assert callable(PdfiumDocument.render)


@pytest.mark.parametrize("rgb", [False, True])
def test_rows_are_packed_and_the_bytes_are_the_same_each_time(rgb):
    # 81 pixels: a row PDFium would pad to four bytes is not a multiple of four.
    document = opened(cases.one_rectangle().data)
    bitmaps = [render(document, 0, crop=(40, 30, 279, 230), rgb=rgb) for _ in range(3)]
    assert {(bitmap.width, bitmap.row_length) for bitmap in bitmaps} == {
        (81, 81 * (3 if rgb else 1))
    }
    assert len({bitmap.data for bitmap in bitmaps}) == 1


# --- annotations as PDFium reads them ----------------------------------------------------


def annots_page(annots: bytes) -> bytes:
    return b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Annots %s >>" % annots


def annots_file(annots: bytes, objects: dict[int, bytes] | None = None) -> bytes:
    """A one-page file whose page has /Annots and `objects` beside the tree."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: annots_page(annots),
            **(objects or {}),
        },
        1,
    )


def views_of(data: bytes, index: int = 0) -> tuple:
    document = opened(data)
    try:
        return document.annotation_views(index)
    finally:
        document.close()


FOUR_ENTRIES = b"[6 0 R null 42 7 0 R]"


def test_the_count_counts_every_entry():
    data = annots_file(
        FOUR_ENTRIES,
        {
            6: b"<< /Type /Annot /Subtype /Widget /Rect [10 10 110 30] >>",
            7: b"<< /Type /Annot /Subtype /Link >>",
        },
    )
    document = opened(data)
    try:
        assert document.annotation_count(0) == 4
    finally:
        document.close()


def test_an_entry_pdfium_gives_no_annotation_for():
    data = annots_file(
        FOUR_ENTRIES,
        {
            6: b"<< /Type /Annot /Subtype /Widget /Rect [10 10 110 30] >>",
            7: b"<< /Type /Annot /Subtype /Link >>",
        },
    )
    views = views_of(data)
    assert len(views) == 4
    assert views[1] is None
    assert views[2] is None


def test_a_widget_and_a_link():
    data = annots_file(
        FOUR_ENTRIES,
        {
            6: b"<< /Type /Annot /Subtype /Widget /Rect [10 10 110 30] >>",
            7: b"<< /Type /Annot /Subtype /Link >>",
        },
    )
    first, _, _, fourth = views_of(data)
    assert first.widget is True
    assert first.index == 0
    assert first.rect == (10.0, 10.0, 110.0, 30.0)
    assert fourth.widget is False
    assert fourth.index == 3


def test_the_texts_and_their_types():
    data = annots_file(
        b"[6 0 R]",
        {
            6: b"<< /Type /Annot /Subtype /Widget /T (name) /AS /On"
            b" /TU (Label) /FT /Tx /V (x) /DV (y) >>"
        },
    )
    (view,) = views_of(data)
    assert view.t_type == pypdfium2.raw.FPDF_OBJECT_STRING
    assert view.t == "name"
    assert view.as_type == pypdfium2.raw.FPDF_OBJECT_NAME
    assert view.as_text == "On"
    assert view.values[b"TU"] == (pypdfium2.raw.FPDF_OBJECT_STRING, "Label")
    assert view.values[b"FT"] == (pypdfium2.raw.FPDF_OBJECT_NAME, "Tx")
    assert view.values[b"V"] == (pypdfium2.raw.FPDF_OBJECT_STRING, "x")
    assert view.values[b"DV"] == (pypdfium2.raw.FPDF_OBJECT_STRING, "y")


def test_absent_keys():
    (view,) = views_of(annots_file(b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget >>"}))
    assert (view.t_type, view.as_type, view.parent_type) == (0, 0, 0)
    assert view.t == "" and view.as_text == ""
    assert view.values == {key: (0, "") for key in (b"V", b"DV", b"TU", b"FT")}


def test_explicit_null():
    data = annots_file(
        b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget /T null /AS null /Parent null >>"}
    )
    (view,) = views_of(data)
    null = pypdfium2.raw.FPDF_OBJECT_NULLOBJ
    assert (view.t_type, view.as_type, view.parent_type) == (null, null, null)


def test_a_reference():
    data = annots_file(
        b"[6 0 R]",
        {6: b"<< /Type /Annot /Subtype /Widget /Parent 8 0 R /DV 9 0 R >>", 9: b"(ref)"},
    )
    (view,) = views_of(data)
    reference = pypdfium2.raw.FPDF_OBJECT_REFERENCE
    assert view.parent_type == reference
    assert view.values[b"DV"] == (reference, "ref")


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (b"/T (a\\255b)", "a\x00b"),
        (b"/AS /A#80B", "A•B"),
        (b"/AS /M#C3#B6nnlich", "MÃ¶nnlich"),
        (b"/T <FEFFD800004C>", "\ud800L"),
    ],
)
def test_pdfiums_decoding(entries, expected):
    data = annots_file(b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget %s >>" % entries})
    (view,) = views_of(data)
    if entries.startswith(b"/T"):
        assert view.t == expected
    else:
        assert view.as_text == expected


def test_a_long_name():
    data = annots_file(
        b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget /AS /%s >>" % (b"L" * 300)}
    )
    (view,) = views_of(data)
    assert len(view.as_text) == 255


def test_flags():
    data = annots_file(b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget /F 2147483652 >>"})
    (view,) = views_of(data)
    assert view.flags == -2147483644


def test_rendering_is_unchanged():
    appearance = stream(b"0 g 0 0 100 20 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 100 20]")
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: annots_page(b"[6 0 R]"),
            6: b"<< /Type /Annot /Subtype /Widget /FT /Btn /T (a) /Rect [10 10 110 30]"
            b" /AP << /N 7 0 R >> >>",
            7: appearance,
        },
        1,
    )
    document = opened(data)
    try:
        first = render(document, 0)
        assert all(
            byte == 255
            for row in range(first.height)
            for byte in first.data[row * first.row_length : row * first.row_length + first.width]
        )
        document.annotation_views(0)
        second = render(document, 0)
    finally:
        document.close()
    assert bytes(first.data) == bytes(second.data)


def test_engine_failures():
    document = opened(annots_file(b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget >>"}))
    document.close()
    with pytest.raises(EngineFailure) as raised:
        document.annotation_views(0)
    assert raised.value.kind is FailureKind.OTHER
    document = opened(annots_file(b"[6 0 R]", {6: b"<< /Type /Annot /Subtype /Widget >>"}))
    try:
        with pytest.raises(IndexError):
            document.annotation_views(1)
    finally:
        document.close()


def test_every_annotation_handle_is_closed(monkeypatch):
    from pdfium_handles import watch_annots

    data = annots_file(
        FOUR_ENTRIES,
        {
            6: b"<< /Type /Annot /Subtype /Widget /Rect [10 10 110 30] >>",
            7: b"<< /Type /Annot /Subtype /Link >>",
        },
    )
    handles = watch_annots(monkeypatch)
    views_of(data)
    assert len(handles.opened) == 2
    assert handles.closed == handles.opened
