"""The QPDF backend: page objects as the file writes them, failures, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import dataclasses
import typing

import pikepdf
import pytest

from pdfacts.backends.protocols import EngineFailure, FailureKind, ObjectReader, PageObject
from pdfacts.backends.qpdf import QpdfDocument
from synthetic import cases
from synthetic.builder import PageSpec, document

# The box QPDF puts on a page that has no usable media box: US letter.
LETTER = (0.0, 0.0, 612.0, 792.0)


def opened(data: bytes, password: str | None = None) -> QpdfDocument:
    return QpdfDocument.open(data, password)


def failure(data: bytes, password: str | None = None) -> EngineFailure:
    with pytest.raises(EngineFailure) as raised:
        opened(data, password)
    return raised.value


def floats(box: cases.Box) -> tuple[float, float, float, float]:
    return tuple(float(value) for value in box)  # type: ignore[return-value]


def single_page(entries: bytes) -> PageObject:
    return opened(document([PageSpec(entries)])).page_object(0)


def plain(value: object) -> bool:
    """Built from int, float, str, bool, None, tuple and PageObject only."""
    if value is None or type(value) in (int, float, str, bool):
        return True
    if type(value) is tuple:
        return all(plain(item) for item in value)
    if type(value) is PageObject:
        return all(plain(getattr(value, field.name)) for field in dataclasses.fields(value))
    return False


def test_the_page_count_of_three_pages():
    case = cases.three_pages()
    assert opened(case.data).page_count() == len(case.page_objects) == 3


def test_the_page_object_numbers_are_the_ones_the_builder_assigned():
    case = cases.three_pages()
    document_ = opened(case.data)
    pages = [document_.page_object(index) for index in range(3)]
    assert [(page.object_number, page.generation) for page in pages] == [
        (number, 0) for number in case.page_objects
    ]


def test_the_boxes_as_written():
    case = cases.crop_box_inside()
    page = opened(case.data).page_object(0)
    assert page.media_box == floats(case.media_boxes[0])
    assert page.crop_box == floats(case.crop_box)
    assert all(type(value) is float for value in page.media_box + page.crop_box)


def test_reversed_corners_come_back_as_written():
    case = cases.reversed_corners()
    assert opened(case.data).page_object(0).media_box == floats(case.media_boxes[0])
    assert case.media_boxes[0] == (400, 300, 0, 0)


def test_a_page_without_a_crop_box():
    page = opened(cases.plain_page().data).page_object(0)
    assert (page.crop_box, page.rotate, page.user_unit, page.user_unit_invalid) == (
        None,
        None,
        None,
        False,
    )


def test_real_corners():
    page = single_page(b"/MediaBox [0.5 0.25 400.75 300]")
    assert page.media_box == (0.5, 0.25, 400.75, 300.0)


def test_inherited_values_sit_on_the_page():
    case = cases.media_box_and_rotation_on_pages()
    page = opened(case.data).page_object(0)
    assert page.media_box == floats(case.pages_media_box)
    assert page.rotate == case.pages_rotate


def test_a_media_box_qpdf_had_to_replace_is_letter_and_a_warning_says_so():
    for data in (cases.no_media_box().data, document([PageSpec(b"/MediaBox [0 0 400]")])):
        document_ = opened(data)
        assert document_.page_object(0).media_box == LETTER
        (warning,) = document_.warnings()
        assert "MediaBox" in warning


def test_asking_for_the_warnings_twice_gives_them_twice():
    # QPDF forgets a warning once it has handed it out.
    document_ = opened(cases.no_media_box().data)
    first = document_.warnings()
    assert len(first) == 1
    assert document_.warnings() == first


@pytest.mark.parametrize("rotation", cases.ROTATIONS + cases.ROTATIONS_AS_WRITTEN)
def test_the_rotation_as_written(rotation):
    assert opened(cases.rotated(rotation).data).page_object(0).rotate == rotation


@pytest.mark.parametrize("text", [b"2", b"2.5"])
def test_a_user_unit_greater_than_zero(text):
    page = opened(cases.user_unit(text).data).page_object(0)
    assert page.user_unit == float(text)
    assert type(page.user_unit) is float
    assert page.user_unit_invalid is False


@pytest.mark.parametrize(
    "text", [b"0", b"-1", b"(text)", b"true", b"1" + b"0" * 400 + b".0"], ids=repr
)
def test_a_user_unit_that_is_not_a_number_greater_than_zero_is_invalid(text):
    page = opened(cases.user_unit(text).data).page_object(0)
    assert (page.user_unit, page.user_unit_invalid) == (None, True)


@pytest.mark.parametrize("entry", [b"/Rotate 90.0", b"/Rotate true", b"/Rotate (90)"])
def test_a_rotation_that_is_not_an_integer_is_none(entry):
    assert single_page(b"/MediaBox [0 0 400 300] " + entry).rotate is None


@pytest.mark.parametrize(
    "entry", [b"/CropBox /Name", b"/CropBox [0 0 400]", b"/CropBox [0 0 (400) 300]"]
)
def test_a_crop_box_that_is_not_four_numbers_is_none(entry):
    assert single_page(b"/MediaBox [0 0 400 300] " + entry).crop_box is None


def test_numbers_are_read_the_same_whatever_conversion_mode_the_caller_chose():
    # In pikepdf's "explicit" mode every number is a pikepdf object, which the
    # backend would take for "not a number".
    case = cases.user_unit(b"2.5")
    previous = pikepdf.get_object_conversion_mode()
    pikepdf.set_object_conversion_mode("explicit")
    try:
        with pikepdf.explicit_conversion():
            document_ = opened(case.data)
            page, count = document_.page_object(0), document_.object_count()
    finally:
        pikepdf.set_object_conversion_mode(previous)
    assert (page.user_unit, page.user_unit_invalid) == (2.5, False)
    assert page.media_box == floats(case.media_boxes[0])
    assert count == case.size


def test_the_object_count_is_the_size_of_the_trailer():
    for case in (cases.plain_page(), cases.three_pages()):
        assert opened(case.data).object_count() == case.size


@pytest.mark.parametrize("size", [b"0", b"-3"])
def test_a_size_that_is_not_greater_than_zero_gives_no_object_count(size):
    data = cases.plain_page().data.replace(b"/Size 6 ", b"/Size " + size + b" ")
    assert data != cases.plain_page().data
    assert opened(data).object_count() is None


def test_an_intact_file_has_no_warnings():
    assert opened(cases.three_pages().data).warnings() == ()


def test_warnings_do_not_depend_on_where_the_bytes_were_in_memory():
    first = opened(cases.no_media_box().data).warnings()
    second = opened(cases.no_media_box().data).warnings()
    assert first == second
    assert "0x" not in first[0]


@pytest.mark.parametrize("name", ["empty", "garbage", "garbage after a header"])
def test_an_unusable_file_is_a_format_failure(name):
    assert failure(cases.UNUSABLE[name]().data).kind is FailureKind.FORMAT


def test_a_file_that_is_only_a_header_is_an_other_failure_today():
    # QPDF raises ValueError("negative seek value -1") here, not one of its own
    # errors, so the backend cannot call it a format failure.
    raised = failure(cases.header_only().data)
    assert raised.kind is FailureKind.OTHER
    assert "negative seek value" in raised.detail


@pytest.mark.parametrize("name", list(cases.TRUNCATED))
def test_a_truncated_file_is_a_format_failure_because_recovery_is_off(name):
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
    with pytest.raises(IndexError):
        opened(cases.three_pages().data).page_object(index)


@pytest.mark.parametrize("index", [True, 1.0, "1"])
def test_an_index_that_is_not_an_int_is_a_type_error(index):
    with pytest.raises(TypeError):
        opened(cases.three_pages().data).page_object(index)


def test_after_close_every_method_fails_and_close_does_nothing(monkeypatch):
    engine_closes = []
    original = pikepdf.Pdf.close

    def counting(self, *args, **kwargs):
        engine_closes.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "close", counting)
    document_ = opened(cases.plain_page().data)
    document_.close()
    calls = [
        document_.page_count,
        document_.object_count,
        lambda: document_.page_object(0),
        document_.warnings,
    ]
    for call in calls:
        with pytest.raises(EngineFailure) as raised:
            call()
        assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")
    document_.close()
    assert len(engine_closes) == 1


def test_the_file_is_opened_with_recovery_off(monkeypatch):
    seen = {}
    original = pikepdf.open

    def watching(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pikepdf, "open", watching)
    opened(cases.plain_page().data)
    assert seen["attempt_recovery"] is False


@pytest.mark.parametrize("exception", [MemoryError, KeyboardInterrupt])
def test_memory_error_and_interrupts_pass_through(monkeypatch, exception):
    def raising(*args, **kwargs):
        raise exception

    monkeypatch.setattr(pikepdf, "open", raising)
    with pytest.raises(exception):
        opened(cases.plain_page().data)


def test_any_other_exception_is_an_other_failure(monkeypatch):
    def raising(*args, **kwargs):
        raise RuntimeError("inside the engine")

    monkeypatch.setattr(pikepdf, "open", raising)
    raised = failure(cases.plain_page().data)
    assert (raised.kind, raised.detail) == (FailureKind.OTHER, "inside the engine")


def test_no_engine_type_leaves_the_backend():
    for case in (cases.crop_box_inside_rotated(), cases.user_unit(b"2.5"), cases.no_media_box()):
        document_ = opened(case.data)
        results = [
            document_.page_count(),
            document_.object_count(),
            document_.page_object(0),
            document_.warnings(),
        ]
        assert all(plain(result) for result in results), results
        assert plain(document_.close())


def test_it_implements_the_object_reader():
    members = typing.get_protocol_members(ObjectReader)
    assert members == {"page_count", "object_count", "page_object", "warnings", "close"}
    assert all(callable(getattr(QpdfDocument, name)) for name in members)
