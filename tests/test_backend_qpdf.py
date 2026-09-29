"""The QPDF backend: page objects as the file writes them, failures, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import dataclasses
import io
import re
import threading
import typing

import pikepdf
import pytest

from pdfacts.backends import qpdf as qpdf_backend
from pdfacts.backends.protocols import (
    EngineFailure,
    FailureKind,
    ObjectReader,
    PageObject,
    RecoveredBytes,
)
from pdfacts.backends.qpdf import QpdfDocument, rewrite_recovered
from synthetic import cases
from synthetic.builder import PageSpec, build, document, stream

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"


def opened(data: bytes, password: str | None = None) -> QpdfDocument:
    return QpdfDocument.open(data, password)


def with_generation(data: bytes, number: int, generation: int) -> bytes:
    """`data` with object `number` in `generation`; every length stays the same."""
    data = data.replace(b"\n%d 0 obj" % number, b"\n%d %d obj" % (number, generation))
    data = re.sub(rb"(?<![0-9])%d 0 R" % number, b"%d %d R" % (number, generation), data)
    table = data.rindex(b"\nxref\n") + 1
    lines = data[table:].split(b"\n")
    # "xref", "0 <count>", then one entry per object: offset, generation, type.
    entry = lines[2 + number]
    lines[2 + number] = entry[:11] + b"%05d" % generation + entry[16:]
    return data[:table] + b"\n".join(lines)


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


def test_a_page_without_crop_box_rotation_or_user_unit():
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


def test_media_box_and_rotation_are_inherited():
    case = cases.media_box_and_rotation_on_pages()
    page = opened(case.data).page_object(0)
    assert page.media_box == floats(case.pages_media_box)
    assert page.rotate == case.pages_rotate


def test_the_crop_box_is_inherited():
    data = document(
        [PageSpec(b"")], pages_entries=b"/MediaBox [0 0 400 300] /CropBox [20 10 380 290]"
    )
    page = opened(data).page_object(0)
    assert (page.media_box, page.crop_box) == ((0.0, 0.0, 400.0, 300.0), (20.0, 10.0, 380.0, 290.0))


def test_the_user_unit_is_not_inherited():
    # The PDF specification does not list UserUnit among the inheritable keys.
    data = document([PageSpec(b"/MediaBox [0 0 400 300]")], pages_entries=b"/UserUnit 2")
    page = opened(data).page_object(0)
    assert (page.user_unit, page.user_unit_invalid) == (None, False)


def read_in_a_thread(document_: QpdfDocument) -> PageObject:
    """page_object(0), so that a loop fails the test instead of hanging the run."""
    pages: list[PageObject] = []
    reader = threading.Thread(target=lambda: pages.append(document_.page_object(0)), daemon=True)
    reader.start()
    reader.join(timeout=10)
    assert not reader.is_alive(), "following /Parent did not end"
    return pages[0]


def test_a_parent_chain_that_returns_to_the_page_ends():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 4 0 R /Rotate 90 /Contents 5 0 R >>",
            5: stream(b""),
        },
        1,
    )
    page_ = read_in_a_thread(opened(data))
    assert (page_.media_box, page_.rotate) == (None, 90)


def test_a_parent_chain_that_returns_in_two_steps_ends():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 7 0 R /Rotate 90 /Contents 5 0 R >>",
            5: stream(b""),
            7: b"<< /Type /Pages /Parent 4 0 R >>",
        },
        1,
    )
    page_ = read_in_a_thread(opened(data))
    assert (page_.media_box, page_.rotate) == (None, 90)


def test_a_value_on_the_page_that_is_not_usable_is_not_replaced_by_the_parent_one():
    # As in PDFium: the first value found counts.
    data = document([PageSpec(b"/MediaBox [0 0 200]")], pages_entries=b"/MediaBox [0 0 400 300]")
    assert opened(data).page_object(0).media_box is None


def test_an_explicit_null_is_absent_and_the_parent_value_counts():
    # ISO 32000: a null value is the same as no entry. PDFium stops at it.
    data = document([PageSpec(b"/MediaBox null")], pages_entries=b"/MediaBox [0 0 400 300]")
    assert opened(data).page_object(0).media_box == (0.0, 0.0, 400.0, 300.0)


def with_offset_zero(data: bytes, number: int) -> bytes:
    """`data` whose cross-reference entry for `number` says offset 0.

    QPDF then reads the object as null and warns each time a view reads it.
    """
    table = data.rindex(b"\nxref\n") + 1
    lines = data[table:].split(b"\n")
    lines[2 + number] = b"%010d" % 0 + lines[2 + number][10:]
    return data[:table] + b"\n".join(lines)


def test_a_warning_both_views_give_is_kept_once():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox 6 0 R /Contents 5 0 R >>",
            5: stream(b""),
            6: b"[0 0 400 300]",
        },
        1,
    )
    # QPDF's walk reads the media box, and so does the backend.
    document_ = opened(with_offset_zero(data, 6))
    assert document_.page_object(0).media_box is None
    offsets = [warning for warning in document_.warnings() if "has offset 0" in warning]
    assert len(offsets) == 1


def test_a_warning_only_the_unwalked_view_gives_is_kept():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /CropBox 6 0 R "
            b"/Contents 5 0 R >>",
            5: stream(b""),
            6: b"[0 0 400 300]",
        },
        1,
    )
    # QPDF's walk does not read the crop box; only the backend does.
    document_ = opened(with_offset_zero(data, 6))
    assert not any("has offset 0" in warning for warning in document_.warnings())
    assert document_.page_object(0).crop_box is None
    offsets = [warning for warning in document_.warnings() if "has offset 0" in warning]
    assert len(offsets) == 1


@pytest.mark.parametrize(
    "entries",
    [b"", b"/MediaBox [0 0 400]", b"/MediaBox [0 0 (400) 300]", b"/MediaBox []", b"/MediaBox null"],
    ids=repr,
)
def test_a_page_without_a_usable_media_box_has_none_although_qpdf_put_one_in(entries):
    # QPDF gives such a page US letter in memory and says so in a warning.
    document_ = opened(document([PageSpec(entries)]))
    assert document_.page_object(0).media_box is None
    (warning,) = document_.warnings()
    assert "MediaBox is undefined" in warning


def page(parent: int, box: bytes = b"[0 0 400 300]") -> bytes:
    return b"<< /Type /Page /Parent %d 0 R /MediaBox %s /Contents 9 0 R >>" % (parent, box)


def refused_pages(data: bytes) -> list[str]:
    """The detail of the failure of every page; a page with values fails the test."""
    document_ = opened(data)
    details = []
    for index in range(document_.page_count()):
        with pytest.raises(EngineFailure) as raised:
            document_.page_object(index)
        assert raised.value.kind is FailureKind.FORMAT
        details.append(raised.value.detail)
    return details


def test_a_page_tree_with_levels_is_read_in_order():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [7 0 R 8 0 R] /Count 3 >>",
            7: b"<< /Type /Pages /Parent 2 0 R /Kids [4 0 R] /Count 1 >>",
            8: b"<< /Type /Pages /Parent 2 0 R /Kids [5 0 R 6 0 R] /Count 2 >>",
            4: page(7, b"[0 0 100 100]"),
            5: page(8, b"[0 0 200 200]"),
            6: page(8, b"[0 0 300 300]"),
            9: stream(b""),
        },
        1,
    )
    document_ = opened(data)
    pages = [document_.page_object(index) for index in range(document_.page_count())]
    assert [(p.object_number, p.media_box) for p in pages] == [
        (4, (0.0, 0.0, 100.0, 100.0)),
        (5, (0.0, 0.0, 200.0, 200.0)),
        (6, (0.0, 0.0, 300.0, 300.0)),
    ]


def test_a_page_written_directly_into_the_kids_leaves_every_page_without_values():
    kid = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 9 0 R >>"
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R %s] /Count 2 >>" % kid,
            4: page(2),
            9: stream(b""),
        },
        1,
    )
    details = refused_pages(data)
    assert len(details) == 2
    assert all("page tree lists" in detail for detail in details)
    assert any("is direct" in warning for warning in opened(data).warnings())


def test_a_page_listed_twice_leaves_every_page_without_values():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R 4 0 R] /Count 2 >>",
            4: page(2),
            9: stream(b""),
        },
        1,
    )
    assert len(refused_pages(data)) == 2
    assert any("more than once" in warning for warning in opened(data).warnings())


def test_a_kid_that_is_missing_leaves_every_page_without_values():
    # QPDF drops the kid and moves the pages after it one index forward, so
    # its page 1 would be object 6 where the file lists object 4.
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [20 0 R 4 0 R 6 0 R] /Count 2 >>",
            4: page(2),
            6: page(2),
            9: stream(b""),
        },
        1,
    )
    assert opened(data).page_count() == 2
    assert len(refused_pages(data)) == 2


def test_a_loop_in_the_tree_the_catalog_names_ends():
    # QPDF walks from the root it finds through /Parent, where there is no
    # loop; the tree the catalog names lists its own root again.
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R 2 0 R] /Count 1 /Parent 7 0 R >>",
            7: b"<< /Type /Pages /Kids [6 0 R] /Count 1 >>",
            4: page(2),
            6: page(7),
            9: stream(b""),
        },
        1,
    )
    documents: list[QpdfDocument] = []
    # In a thread, so that a loop fails the test instead of hanging the run.
    reader = threading.Thread(target=lambda: documents.append(opened(data)), daemon=True)
    reader.start()
    reader.join(timeout=10)
    assert not reader.is_alive(), "reading the page tree did not end"
    assert documents[0].page_count() == 1
    with pytest.raises(EngineFailure) as raised:
        documents[0].page_object(0)
    assert "page tree lists" in raised.value.detail


def test_a_root_with_a_parent_leaves_every_page_without_values():
    # QPDF then walks from another node than the one the catalog names.
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 /Parent 7 0 R >>",
            7: b"<< /Type /Pages /Kids [6 0 R] /Count 1 >>",
            4: page(2),
            6: page(7),
            9: stream(b""),
        },
        1,
    )
    document_ = opened(data)
    assert document_.page_count() == 1
    with pytest.raises(EngineFailure) as raised:
        document_.page_object(0)
    assert "page tree lists" in raised.value.detail


def test_qpdf_finding_more_pages_than_the_file_lists_leaves_every_page_without_values():
    # The catalog names a page as the root of the page tree; QPDF finds the
    # real root through /Parent and both pages, the file's tree has one.
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 4 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
            4: page(2),
            5: page(2),
            9: stream(b""),
        },
        1,
    )
    assert opened(data).page_count() == 2
    assert len(refused_pages(data)) == 2


def test_an_index_outside_the_pages_is_an_index_error_also_when_there_are_no_values():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R 4 0 R] /Count 2 >>",
            4: page(2),
            9: stream(b""),
        },
        1,
    )
    with pytest.raises(IndexError):
        opened(data).page_object(2)


def test_values_are_inherited_from_further_up_than_the_parent():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [7 0 R] /Count 1 /MediaBox [0 0 400 300] /Rotate 180 >>",
            7: b"<< /Type /Pages /Parent 2 0 R /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 7 0 R /Contents 9 0 R >>",
            9: stream(b""),
        },
        1,
    )
    page_ = opened(data).page_object(0)
    assert (page_.media_box, page_.rotate) == ((0.0, 0.0, 400.0, 300.0), 180)


def test_the_generation_comes_from_the_file():
    data = with_generation(cases.plain_page().data, 4, 1)
    assert b"4 1 obj" in data
    page = opened(data).page_object(0)
    assert (page.object_number, page.generation) == (4, 1)


def test_asking_for_the_warnings_twice_gives_them_twice():
    # QPDF forgets a warning once it has handed it out.
    document_ = opened(cases.no_media_box().data)
    first = document_.warnings()
    assert len(first) == 1
    assert document_.warnings() == first


def test_the_warnings_keep_the_order_qpdf_gave_them_in():
    # QPDF warns about each page without resources in the order of the page
    # tree, which here is not the order of the text of the warnings.
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [7 0 R 4 0 R] /Count 2 >>",
            4: page(2),
            7: page(2),
            9: stream(b""),
        },
        1,
    )
    warnings = opened(data).warnings()
    assert [re.search(r"object (\d+) 0", warning).group(1) for warning in warnings] == ["7", "4"]
    assert all("Resources is missing" in warning for warning in warnings)
    assert list(warnings) != sorted(warnings)


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
    # Both views of the file, each once.
    assert len(engine_closes) == 2
    assert engine_closes[0] is not engine_closes[1]
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
    assert len(engine_closes) == 2


def test_close_closes_the_second_view_when_the_first_fails(monkeypatch):
    closed = []
    original = pikepdf.Pdf.close

    def first_fails(self, *args, **kwargs):
        closed.append(self)
        if len(closed) == 1:
            original(self, *args, **kwargs)
            raise RuntimeError("the first view failed to close")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "close", first_fails)
    document_ = opened(cases.plain_page().data)
    with pytest.raises(EngineFailure):
        document_.close()
    assert len(closed) == 2


def test_a_failed_open_closes_the_view_already_opened(monkeypatch):
    engine_closes, opens = [], []
    original_open, original_close = pikepdf.open, pikepdf.Pdf.close

    def second_fails(*args, **kwargs):
        if opens:
            raise pikepdf.PdfError("the second view failed")
        opens.append(original_open(*args, **kwargs))
        return opens[-1]

    def counting(self, *args, **kwargs):
        engine_closes.append(self)
        return original_close(self, *args, **kwargs)

    monkeypatch.setattr(pikepdf, "open", second_fails)
    monkeypatch.setattr(pikepdf.Pdf, "close", counting)
    assert failure(cases.plain_page().data).kind is FailureKind.FORMAT
    assert len(engine_closes) == 1
    assert engine_closes[0] is opens[0]


def test_a_failed_open_reports_its_own_failure_when_closing_fails_too(monkeypatch):
    opens = []
    original_open, original_close = pikepdf.open, pikepdf.Pdf.close

    def second_fails(*args, **kwargs):
        if opens:
            raise pikepdf.PdfError("the second view failed")
        opens.append(original_open(*args, **kwargs))
        return opens[-1]

    def failing_close(self, *args, **kwargs):
        original_close(self, *args, **kwargs)
        raise RuntimeError("closing failed as well")

    monkeypatch.setattr(pikepdf, "open", second_fails)
    monkeypatch.setattr(pikepdf.Pdf, "close", failing_close)
    raised = failure(cases.plain_page().data)
    assert (raised.kind, raised.detail) == (FailureKind.FORMAT, "the second view failed")


def test_a_memory_error_while_closing_after_a_failed_open_is_not_swallowed(monkeypatch):
    opens = []
    error = MemoryError()
    original_open, original_close = pikepdf.open, pikepdf.Pdf.close

    def second_fails(*args, **kwargs):
        if opens:
            raise pikepdf.PdfError("the second view failed")
        opens.append(original_open(*args, **kwargs))
        return opens[-1]

    def failing_close(self, *args, **kwargs):
        original_close(self, *args, **kwargs)
        raise error

    monkeypatch.setattr(pikepdf, "open", second_fails)
    monkeypatch.setattr(pikepdf.Pdf, "close", failing_close)
    with pytest.raises(MemoryError) as raised:
        opened(cases.plain_page().data)
    assert raised.value is error


def test_both_views_are_opened_with_recovery_off(monkeypatch):
    calls = []
    original = pikepdf.open

    def watching(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pikepdf, "open", watching)
    opened(cases.plain_page().data)
    assert [call["attempt_recovery"] for call in calls] == [False, False]


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
    assert members == {
        "page_count",
        "object_count",
        "objects_as_written",
        "page_object",
        "warnings",
        "close",
    }
    assert all(callable(getattr(QpdfDocument, name)) for name in members)


# --- rewrite_recovered -----------------------------------------------------------------


def test_a_truncated_file_is_rebuilt_and_written_anew():
    case = cases.cut_before_xref_table()
    rewritten = rewrite_recovered(case.data, None)
    assert type(rewritten) is RecoveredBytes
    assert type(rewritten.data) is bytes and rewritten.data != case.data
    assert rewritten.page_count == case.pages_built
    assert rewritten.warnings and all(type(w) is str for w in rewritten.warnings)
    # The input is named without a memory address.
    assert all("0x" not in warning for warning in rewritten.warnings)
    assert any(warning.startswith("stream <input>") for warning in rewritten.warnings)


def test_the_rewritten_file_has_a_size_and_opens_with_recovery_off():
    rewritten = rewrite_recovered(cases.cut_before_xref_table().data, None)
    with pikepdf.open(io.BytesIO(rewritten.data), attempt_recovery=False) as pdf:
        assert "/Size" in pdf.trailer
        assert pdf.get_warnings() == []


@pytest.mark.parametrize(
    "data",
    [cases.cut_inside_catalog().data, cases.header_only().data],
    ids=["cut inside the catalog", "header only"],
)
def test_a_file_qpdf_cannot_rebuild_is_a_format_failure(data):
    with pytest.raises(EngineFailure) as raised:
        rewrite_recovered(data, None)
    assert raised.value.kind is FailureKind.FORMAT


def test_a_missing_password_is_a_password_failure():
    with pytest.raises(EngineFailure) as raised:
        rewrite_recovered(cases.user_password().data, None)
    assert raised.value.kind is FailureKind.PASSWORD


def test_encryption_is_kept():
    case = cases.user_password()
    rewritten = rewrite_recovered(case.data, case.user_password)
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(io.BytesIO(rewritten.data))
    with pikepdf.open(io.BytesIO(rewritten.data), password=case.user_password) as pdf:
        assert pdf.is_encrypted


def test_an_unencrypted_file_stays_unencrypted():
    rewritten = rewrite_recovered(cases.three_pages().data, None)
    with pikepdf.open(io.BytesIO(rewritten.data)) as pdf:
        assert not pdf.is_encrypted
    assert rewritten.warnings == ()


def watching_opens(monkeypatch) -> tuple[list[pikepdf.Pdf], list[pikepdf.Pdf], list[object]]:
    """The documents pikepdf opens, those closed, and every input the backend makes."""
    opened: list[pikepdf.Pdf] = []
    closed: list[pikepdf.Pdf] = []
    inputs: list[object] = []
    original_open, original_close = pikepdf.open, pikepdf.Pdf.close

    def opening(*args, **kwargs):
        pdf = original_open(*args, **kwargs)
        opened.append(pdf)
        return pdf

    def closing(self):
        closed.append(self)
        return original_close(self)

    class Recorded(qpdf_backend._Input):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            inputs.append(self)

    monkeypatch.setattr(pikepdf, "open", opening)
    monkeypatch.setattr(pikepdf.Pdf, "close", closing)
    monkeypatch.setattr(qpdf_backend, "_Input", Recorded)
    return opened, closed, inputs


@pytest.mark.parametrize(
    ("make", "password", "opens"),
    [
        # The recovery opens the file twice, once with recovery off to see
        # that it reads as written; QpdfDocument opens it twice.
        (cases.owner_password_only, None, 4),
        # Each opened without the password first, then again with it.
        (cases.owner_password_only, "owner", 8),
        # Each first open, without a password, is refused.
        (cases.user_password, "user", 4),
    ],
    ids=["owner password, none given", "owner password given", "user password given"],
)
def test_every_document_and_input_the_backend_opens_is_closed(monkeypatch, make, password, opens):
    # Built before the watching starts: the encrypted cases are made with pikepdf.
    data = make().data
    opened, closed, inputs = watching_opens(monkeypatch)
    rewrite_recovered(data, password)
    opened_once = QpdfDocument.open(data, password)
    opened_once.close()
    assert len(opened) == opens
    assert all(any(pdf is done for done in closed) for pdf in opened)
    assert inputs and all(source.closed for source in inputs)
