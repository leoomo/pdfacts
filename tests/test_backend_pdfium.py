"""The PDFium backend: page count, visible box, rotation, failures, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import ctypes
import typing

import pypdfium2
import pytest

from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import EngineFailure, FailureKind, GeometryReader
from synthetic import cases

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


def test_the_page_count_of_three_pages():
    case = cases.three_pages()
    document = opened(case.data)
    assert document.page_count() == len(case.page_objects) == 3


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
    for method in (document.visible_box, document.rotation):
        with pytest.raises(IndexError):
            method(index)


@pytest.mark.parametrize("index", [True, 1.0, "1"])
def test_an_index_that_is_not_an_int_is_a_type_error(index):
    document = opened(cases.three_pages().data)
    for method in (document.visible_box, document.rotation):
        with pytest.raises(TypeError):
            method(index)


def test_after_close_every_method_fails_and_close_does_nothing(monkeypatch):
    engine_closes = []
    original = pypdfium2.PdfDocument.close

    def counting(self, *args, **kwargs):
        engine_closes.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", counting)
    document = opened(cases.plain_page().data)
    document.close()
    calls = [document.page_count, lambda: document.visible_box(0), lambda: document.rotation(0)]
    for call in calls:
        with pytest.raises(EngineFailure) as raised:
            call()
        assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")
    document.close()
    assert len(engine_closes) == 1


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
    assert len(opened_pages) == 2
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
