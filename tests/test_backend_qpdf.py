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
    FieldTree,
    ObjectReader,
    PageAnnotations,
    PageObject,
    RecoveredBytes,
    Written,
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
        document_.security_handler_revision,
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
            document_.parsed_streams(0),
            document_.warnings(),
        ]
        assert all(plain(result) for result in results), results
        assert plain(document_.close())


def test_it_implements_the_object_reader():
    members = typing.get_protocol_members(ObjectReader)
    assert members == {
        "annotation_count",
        "annotations",
        "field_tree",
        "page_count",
        "object_count",
        "objects_as_written",
        "security_handler_revision",
        "cross_reference_count",
        "page_object",
        "parsed_streams",
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


# --- widgets and fields as written ------------------------------------------------------


def annots_page(annots: bytes | None) -> bytes:
    """The dictionary of a page with /Annots `annots`, or without it for None."""
    extra = b" /Annots %s" % annots if annots is not None else b""
    return b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>%s >>" % extra


def annots_file(
    annots: bytes | None, objects: dict[int, bytes] | None = None, catalog: bytes = CATALOG
) -> bytes:
    """A one-page file whose page has /Annots and `objects` beside the tree."""
    return build(
        {
            1: catalog,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: annots_page(annots),
            **(objects or {}),
        },
        1,
    )


def acroform_file(fields: bytes, objects: dict[int, bytes] | None = None) -> bytes:
    """A one-page file whose catalog holds /AcroForm << /Fields `fields` >>."""
    catalog = b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields %s >> >>" % fields
    return annots_file(None, objects, catalog)


def widget(entries: bytes) -> bytes:
    return b"<< /Type /Annot /Subtype /Widget %s >>" % entries


def annotations_of(data: bytes, index: int = 0) -> PageAnnotations:
    document_ = opened(data)
    try:
        return document_.annotations(index)
    finally:
        document_.close()


def field_tree_of(data: bytes) -> FieldTree | None:
    document_ = opened(data)
    try:
        return document_.field_tree()
    finally:
        document_.close()


def test_no_annots_is_no_entry_and_not_an_array():
    document_ = opened(annots_file(None))
    try:
        assert document_.annotation_count(0) == 0
        page = document_.annotations(0)
    finally:
        document_.close()
    assert page.entries == ()
    assert page.widgets == ()
    assert page.not_an_array is False


def test_annots_that_is_no_array():
    document_ = opened(annots_file(b"5"))
    try:
        assert document_.annotation_count(0) is None
        page = document_.annotations(0)
    finally:
        document_.close()
    assert page.entries == ()
    assert page.not_an_array is True


def test_entries_of_every_kind():
    data = annots_file(
        b"[6 0 R null 7 0 R 42 << /Subtype /Widget /Rect [0 0 1 1] >>]",
        {6: widget(b"/Rect [0 0 1 1]"), 7: b"<< /Type /Annot /Subtype /Link >>"},
    )
    document_ = opened(data)
    try:
        assert document_.annotation_count(0) == 5
        page = document_.annotations(0)
    finally:
        document_.close()
    first, second, third, fourth, fifth = page.entries
    assert (first.index, first.object_number, first.generation) == (0, 6, 0)
    assert (first.dictionary, first.subtype) == (True, b"Widget")
    assert (second.index, second.object_number, second.dictionary, second.subtype) == (
        1,
        None,
        False,
        None,
    )
    assert (third.index, third.object_number, third.subtype) == (2, 7, b"Link")
    assert (fourth.index, fourth.object_number, fourth.dictionary) == (3, None, False)
    assert (fifth.index, fifth.object_number, fifth.dictionary, fifth.subtype) == (
        4,
        None,
        True,
        b"Widget",
    )
    assert [one.annotation.index for one in page.widgets] == [0, 4]


def test_a_widget_listed_twice_is_converted_once():
    page = annotations_of(annots_file(b"[6 0 R 6 0 R]", {6: widget(b"/Rect [0 0 1 1] /T (a)")}))
    first, second = page.widgets
    assert (first.annotation.index, second.annotation.index) == (0, 1)
    assert dataclasses.replace(first, annotation=second.annotation) == second


def test_the_kinds_of_written():
    data = annots_file(
        b"[6 0 R]",
        {
            6: widget(
                b"/Rect [1 2.5 true null] /AS /A#F6 /F 4"
                b" /MK << /BC [1 0 0] >> /BS << /W 2.0 /S /D >>"
            )
        },
    )
    entries = annotations_of(data).widgets[0].entries
    assert entries[b"Rect"].kind == "array"
    assert [(one.kind, one.data) for one in entries[b"Rect"].data] == [
        ("integer", 1),
        ("real", 2.5),
        ("boolean", True),
        ("null", None),
    ]
    assert entries[b"AS"] == Written("name", b"A\xf6")
    assert entries[b"F"] == Written("integer", 4)
    assert [one.data for one in entries[b"MK/BC"].data] == [1, 0, 0]
    assert entries[b"BS/W"] == Written("real", 2.0)
    assert entries[b"BS/S"] == Written("name", b"D")


def test_a_real_beyond_a_double_is_other():
    number = b"1" + b"0" * 400 + b".5"
    page = annotations_of(annots_file(b"[6 0 R]", {6: widget(b"/F " + number)}))
    assert page.widgets[0].entries[b"F"].kind == "other"


def test_arrays_two_levels_deep():
    data = annots_file(b"[6 0 R]", {6: widget(b"/Opt [(a) [(b) (c)] [[(d)]]]")})
    chain = annotations_of(data).widgets[0].chain
    top = chain[0].entries[b"Opt"]
    assert top.kind == "array"
    string, inner, nested = top.data
    assert string == Written("string", b"a")
    assert inner.kind == "array" and [one.data for one in inner.data] == [b"b", b"c"]
    assert nested.kind == "array" and nested.data[0].kind == "other"


def test_names_with_number_sign_escapes():
    data = annots_file(
        b"[6 0 R]",
        {
            6: widget(b"/AS /M#C3#B6nnlich /AP << /N << /M#F6nnlich 9 0 R /Off 9 0 R >> >>"),
            9: stream(b""),
        },
    )
    one = annotations_of(data).widgets[0]
    assert one.entries[b"AS"] == Written("name", b"M\xc3\xb6nnlich")
    assert one.states == (b"M\xf6nnlich", b"Off")


@pytest.mark.parametrize(
    ("body", "states", "invalid"),
    [
        (b"/AP << /N << /A 9 0 R >> >>", (b"A",), False),
        (b"/AP << /N 9 0 R >>", None, False),
        (b"", None, False),
        (b"/AP 5", None, True),
    ],
)
def test_ap_n_shapes(body, states, invalid):
    page = annotations_of(annots_file(b"[6 0 R]", {6: widget(body), 9: stream(b"")}))
    one = page.widgets[0]
    assert one.states == states
    assert one.states_invalid is invalid


def test_mk_and_bs_that_are_no_dictionaries():
    page = annotations_of(annots_file(b"[6 0 R]", {6: widget(b"/MK 1 /BS 2")}))
    one = page.widgets[0]
    assert one.mk_invalid is True
    assert one.bs_invalid is True
    assert not [key for key in one.entries if key.startswith((b"MK/", b"BS/"))]


def test_a_merged_widget_field_and_a_kid():
    data = annots_file(
        b"[6 0 R 7 0 R]",
        {
            6: widget(b"/T (a) /FT /Tx"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (b) /FT /Btn /Kids [7 0 R] >>",
        },
    )
    six, seven = annotations_of(data).widgets
    assert len(six.chain) == 1
    assert six.chain[0].entries[b"T"] == Written("string", b"a")
    assert len(seven.chain) == 2
    assert seven.chain[1].object_number == 8
    assert seven.chain[1].entries[b"T"] == Written("string", b"b")
    assert seven.chain[1].kids is True
    kids = seven.parent_kids
    assert kids is not None
    assert (kids.entries[0].object_number, kids.entries[0].widget) == (7, True)
    assert kids.widget_positions == {(7, 0): (0,)}
    assert kids.all_widgets is True


def test_a_chain_that_loops():
    data = annots_file(
        b"[7 0 R]",
        {7: widget(b"/Parent 8 0 R"), 8: b"<< /Parent 10 0 R >>", 10: b"<< /Parent 8 0 R >>"},
    )
    one = annotations_of(data).widgets[0]
    assert one.chain_loops is True
    assert [entry.object_number for entry in one.chain] == [7, 8, 10]


def test_a_parent_that_is_no_dictionary():
    one = annotations_of(annots_file(b"[6 0 R]", {6: widget(b"/Parent 5")})).widgets[0]
    assert one.parent_invalid is True
    assert len(one.chain) == 1


@pytest.mark.parametrize(("ancestors", "cut"), [(32, False), (33, True)])
def test_the_chain_bound(ancestors, cut):
    objects = {6: widget(b"/Parent 7 0 R")}
    for number in range(7, 7 + ancestors):
        above = b"/Parent %d 0 R" % (number + 1) if number + 1 < 7 + ancestors else b""
        objects[number] = b"<< /T (p) %s >>" % above
    one = annotations_of(annots_file(b"[6 0 R]", objects)).widgets[0]
    assert len(one.chain) == 33
    assert one.chain_cut is cut


def test_parent_kids_counts_widgets():
    data = annots_file(
        b"[7 0 R]",
        {
            5: b"<< /Type /Annot /Subtype /Link >>",
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /Kids [5 0 R << /Subtype /Widget /Parent 8 0 R >> 7 0 R 7 0 R] >>",
        },
    )
    kids = annotations_of(data).widgets[0].parent_kids
    assert kids is not None
    assert kids.all_widgets is False
    assert kids.widget_positions == {(7, 0): (1, 2)}


def test_no_acroform_is_no_tree():
    assert field_tree_of(annots_file(None)) is None


@pytest.mark.parametrize(
    "acroform",
    [b"/AcroForm 5", b"/AcroForm << >>", b"/AcroForm << /Fields [null] >>"],
)
def test_fields_shapes(acroform):
    catalog = b"<< /Type /Catalog /Pages 2 0 R %s >>" % acroform
    tree = field_tree_of(annots_file(None, catalog=catalog))
    assert tree is not None
    assert tree.invalid is True
    if acroform == b"/AcroForm << /Fields [null] >>":
        assert tree.roots == (None,)
    else:
        assert tree.roots == ()
    assert tree.nodes == ()


def test_a_tree_in_order():
    data = acroform_file(
        b"[10 0 R 11 0 R]",
        {
            10: b"<< /T (a) /Kids [12 0 R 13 0 R] >>",
            11: b"<< /T (b) >>",
            12: b"<< /T (c) >>",
            13: b"<< /T (d) >>",
        },
    )
    tree = field_tree_of(data)
    assert [node.entry.object_number for node in tree.nodes] == [10, 12, 13, 11]
    assert tree.roots == (0, 3)
    assert tree.nodes[1].parent == 0


def test_direct_dictionaries_in_kids():
    data = acroform_file(b"[10 0 R]", {10: b"<< /Kids [<< /T (d) >>] >>"})
    tree = field_tree_of(data)
    assert len(tree.nodes) == 2
    assert tree.nodes[1].entry.object_number is None
    assert tree.nodes[1].parent == 0


def test_a_direct_node_in_a_direct_node():
    data = acroform_file(b"[10 0 R]", {10: b"<< /Kids [<< /T (d) /Kids [<< /T (e) >>] >>] >>"})
    tree = field_tree_of(data)
    assert len(tree.nodes) == 3
    assert tree.nodes[1].parent == 0
    assert tree.nodes[2].parent == 1
    assert not any(node.reached_twice for node in tree.nodes)


@pytest.mark.parametrize(
    ("kid_body", "root_body", "matches"),
    [
        (b"<< /Parent 10 0 R >>", b"", True),
        (b"<< /Parent 12 0 R >>", b"", False),
        (b"", b"/Parent 12 0 R", False),
    ],
)
def test_parent_matches(kid_body, root_body, matches):
    data = acroform_file(
        b"[10 0 R]", {10: b"<< %s /Kids [%s] >>" % (root_body, kid_body), 12: b"<< /T (o) >>"}
    )
    tree = field_tree_of(data)
    assert tree.nodes[-1].parent_matches is matches


def test_a_node_reached_twice():
    data = acroform_file(b"[10 0 R 10 0 R]", {10: b"<< /T (a) >>"})
    tree = field_tree_of(data)
    assert tree.nodes[0].reached_twice is True


def test_a_kid_of_two_parents_is_reached_twice():
    data = acroform_file(
        b"[10 0 R 11 0 R]",
        {10: b"<< /Kids [12 0 R] >>", 11: b"<< /Kids [12 0 R] >>", 12: b"<< /T (x) >>"},
    )
    tree = field_tree_of(data)
    assert [node.entry.object_number for node in tree.nodes] == [10, 12, 11]
    assert tree.nodes[1].reached_twice is True


def test_parents_sharing_one_indirect_kids_array():
    data = acroform_file(
        b"[10 0 R 11 0 R]",
        {
            10: b"<< /Kids 20 0 R >>",
            11: b"<< /Kids 20 0 R >>",
            20: b"[12 0 R]",
            12: b"<< /T (x) >>",
        },
    )
    tree = field_tree_of(data)
    assert [node.entry.object_number for node in tree.nodes] == [10, 12, 11]
    assert tree.nodes[1].reached_twice is True
    assert tree.nodes[0].kids == (1,)
    assert tree.nodes[2].kids == (1,)


def test_a_tree_that_loops():
    data = acroform_file(b"[10 0 R]", {10: b"<< /Kids [11 0 R] >>", 11: b"<< /Kids [10 0 R] >>"})
    tree = field_tree_of(data)
    assert [node.entry.object_number for node in tree.nodes] == [10, 11]
    assert tree.nodes[0].reached_twice is True


def test_a_loop_through_an_indirect_kids_array():
    data = acroform_file(
        b"[10 0 R]",
        {10: b"<< /Kids 20 0 R >>", 20: b"[11 0 R]", 11: b"<< /Kids 20 0 R >>"},
    )
    tree = field_tree_of(data)
    assert [node.entry.object_number for node in tree.nodes] == [10, 11]
    assert tree.nodes[1].reached_twice is True
    assert tree.nodes[0].kids == (1,)
    assert tree.nodes[1].kids == (1,)


@pytest.mark.parametrize(("with_kids", "cut"), [(33, True), (32, False)])
def test_the_tree_bound(with_kids, cut):
    objects = {}
    for number in range(10, 10 + with_kids):
        if number + 1 < 10 + with_kids:
            objects[number] = b"<< /Kids [%d 0 R] >>" % (number + 1)
        else:
            objects[number] = b"<< /Kids [<< /T (leaf) >>] >>"
    tree = field_tree_of(acroform_file(b"[10 0 R]", objects))
    assert tree.cut is cut
    assert (tree.nodes[-1].kids is None) is cut
    if not cut:
        assert all(isinstance(node.kids, tuple) for node in tree.nodes)


def counted_module_function(monkeypatch, name):
    """`name` of the qpdf module wrapped to count its calls."""
    calls = []
    real = getattr(qpdf_backend, name)

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(qpdf_backend, name, counting)
    return calls


def test_linear_on_a_shared_kids_array(monkeypatch):
    calls = counted_module_function(monkeypatch, "_tree_entry")
    objects = {20: b"[" + b" ".join(b"<< /T (k%d) >>" % i for i in range(300)) + b"]"}
    for i in range(300):
        objects[30 + i] = b"<< /T (f%d) /Kids 20 0 R >>" % i
    fields = b"[" + b" ".join(b"%d 0 R" % (30 + i) for i in range(300)) + b"]"
    field_tree_of(acroform_file(fields, objects))
    assert len(calls) < 10 * (len(objects) + 3)


def test_linear_on_a_chain_of_shared_kids(monkeypatch):
    calls = counted_module_function(monkeypatch, "_tree_entry")
    objects = {10: b"<< /Kids 21 0 R >>"}
    for number in range(21, 39):
        body = b"/Kids %d 0 R" % (number + 1) if number < 38 else b""
        objects[number] = b"[" + b" ".join(b"<< /T (x) %s >>" % body for _ in range(2)) + b"]"
    field_tree_of(acroform_file(b"[10 0 R]", objects))
    assert len(calls) < 10 * (len(objects) + 3)


def test_linear_on_a_shared_value(monkeypatch):
    calls = counted_module_function(monkeypatch, "_written")
    objects = {20: b"[" + b" ".join(b"(s%d)" % i for i in range(300)) + b"]"}
    for i in range(300):
        objects[30 + i] = b"<< /T (f%d) /FT /Ch /Opt 20 0 R >>" % i
    fields = b"[" + b" ".join(b"%d 0 R" % (30 + i) for i in range(300)) + b"]"
    field_tree_of(acroform_file(fields, objects))
    assert len(calls) < 10 * (len(objects) + 3)


def test_linear_on_shared_widget_entries(monkeypatch):
    calls = counted_module_function(monkeypatch, "_written")
    keys = b" ".join(b"/K%d 9 0 R" % i for i in range(300))
    objects = {
        21: b"[" + b" ".join(b"%d" % i for i in range(300)) + b"]",
        22: b"<< /BC [" + b" ".join(b"0" for _ in range(300)) + b"] >>",
        23: b"<< %s >>" % keys,
        9: stream(b""),
    }
    for i in range(300):
        objects[40 + i] = widget(b"/Rect 21 0 R /MK 22 0 R /AP << /N 23 0 R >>")
    annots = b"[" + b" ".join(b"%d 0 R" % (40 + i) for i in range(300)) + b"]"
    page = annotations_of(annots_file(annots, objects))
    assert len(calls) < 10 * (len(objects) + 3)
    states = page.widgets[0].states
    background = page.widgets[0].entries[b"MK/BC"]
    assert all(one.states is states for one in page.widgets)
    assert all(one.entries[b"MK/BC"] is background for one in page.widgets)


def test_parents_sharing_one_kids_array_share_one_kids():
    objects = {20: b"[" + b" ".join(b"%d 0 R" % (40 + i) for i in range(300)) + b"]"}
    for i in range(300):
        objects[40 + i] = widget(b"/Rect [0 0 1 1] /Parent %d 0 R" % (340 + i))
        objects[340 + i] = b"<< /T (p%d) /FT /Btn /Kids 20 0 R >>" % i
    annots = b"[" + b" ".join(b"%d 0 R" % (40 + i) for i in range(300)) + b"]"
    page = annotations_of(annots_file(annots, objects))
    kids = page.widgets[0].parent_kids
    assert kids is not None
    assert all(one.parent_kids is kids for one in page.widgets)


def test_kept_per_document(monkeypatch):
    page_of = lambda annots: (  # noqa: E731
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
        % annots
    )
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [8 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
            4: page_of(b"[6 0 R]"),
            5: page_of(b"[7 0 R]"),
            6: widget(b"/Parent 8 0 R /Rect [0 0 1 1]"),
            7: widget(b"/Parent 8 0 R /Rect [0 0 1 1]"),
            8: b"<< /T (p) /FT /Tx /Kids [6 0 R 7 0 R] >>",
        },
        1,
    )
    document_ = opened(data)
    try:
        first = document_.field_tree()
        assert first is not None
        assert document_.field_tree() is first
        document_.annotations(0)
        calls = counted_module_function(monkeypatch, "_written")
        document_.annotations(1)
    finally:
        document_.close()
    assert len(calls) == 5


def test_an_explicit_null_is_absent():
    one = annotations_of(annots_file(b"[6 0 R]", {6: widget(b"/T null /Parent null")})).widgets[0]
    assert b"T" not in one.chain[0].entries
    assert len(one.chain) == 1
    assert one.parent_invalid is False


def test_annotations_after_close_is_an_engine_failure():
    document_ = opened(annots_file(b"[6 0 R]", {6: widget(b"")}))
    document_.close()
    with pytest.raises(EngineFailure) as raised:
        document_.annotations(0)
    assert raised.value.kind is FailureKind.OTHER


def test_a_failing_unwalked_view_is_translated():
    document_ = opened(annots_file(b"[6 0 R]", {6: widget(b"")}))
    try:

        class Broken:
            def get_object(self, *args, **kwargs):
                raise pikepdf.PdfError("broken")

        real = document_._unwalked
        document_._unwalked = Broken()
        try:
            with pytest.raises(EngineFailure):
                document_.annotations(0)
        finally:
            document_._unwalked = real
    finally:
        document_.close()


def test_a_page_tree_qpdf_reads_otherwise():
    data = build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R 4 0 R] /Count 2 >>",
            4: page(2),
            9: stream(b""),
        },
        1,
    )
    document_ = opened(data)
    try:
        with pytest.raises(EngineFailure) as raised:
            document_.annotation_count(0)
        assert raised.value.kind is FailureKind.FORMAT
        with pytest.raises(EngineFailure):
            document_.annotations(0)
    finally:
        document_.close()


def test_an_index_outside_the_pages():
    document_ = opened(annots_file(None))
    try:
        with pytest.raises(IndexError):
            document_.annotation_count(1)
        with pytest.raises(IndexError):
            document_.annotations(1)
        with pytest.raises(TypeError):
            document_.annotation_count(True)
    finally:
        document_.close()


def test_the_order_of_states():
    body = b"/AP << /N << /Zz 9 0 R /Yes 9 0 R /Off 9 0 R /M#F6 9 0 R /B 9 0 R /A 9 0 R >> >>"
    page = annotations_of(annots_file(b"[6 0 R]", {6: widget(body), 9: stream(b"")}))
    assert page.widgets[0].states == (b"A", b"B", b"M\xf6", b"Off", b"Yes", b"Zz")


@pytest.mark.parametrize("order", [b"[10 0 R 11 0 R]", b"[11 0 R 10 0 R]"])
def test_one_object_in_two_roles(order):
    data = annots_file(
        order,
        {
            10: widget(b"/AP 30 0 R"),
            11: widget(b"/AP << /N 30 0 R >>"),
            30: b"<< /N << /On 9 0 R /Off 9 0 R >> >>",
            9: stream(b""),
        },
    )
    page = annotations_of(data)
    by_number = {one.chain[0].object_number: one for one in page.widgets}
    assert by_number[10].states == (b"Off", b"On")
    assert by_number[11].states == (b"N",)


def test_mk_and_bs_sharing_one_object():
    data = annots_file(
        b"[6 0 R]",
        {6: widget(b"/MK 22 0 R /BS 22 0 R"), 22: b"<< /BC [1 0 0] /W 2 /S /D >>"},
    )
    entries = annotations_of(data).widgets[0].entries
    assert set(entries) == {b"MK/BC", b"BS/W", b"BS/S"}
    assert [one.data for one in entries[b"MK/BC"].data] == [1, 0, 0]
    assert entries[b"BS/W"] == Written("integer", 2)
    assert entries[b"BS/S"] == Written("name", b"D")


# --- the review of #46 --------------------------------------------------------------------


def test_indirect_names_are_resolved():
    data = annots_file(
        b"[6 0 R 7 0 R]",
        {
            6: widget(b"/AS 21 0 R /BS << /S 21 0 R >> /Parent 8 0 R"),
            7: b"<< /Type /Annot /Subtype 30 0 R >>",
            8: b"<< /T (a) /FT 21 0 R /Kids [6 0 R] >>",
            21: b"/On",
            30: b"/Widget",
        },
    )
    page = annotations_of(data)
    assert [entry.subtype for entry in page.entries] == [b"Widget", b"Widget"]
    assert len(page.widgets) == 2
    first = page.widgets[0]
    assert first.entries[b"AS"] == Written("name", b"On")
    assert first.entries[b"BS/S"] == Written("name", b"On")
    assert first.chain[1].entries[b"FT"] == Written("name", b"On")


def test_linear_on_two_kids_arrays_that_hold_each_other(monkeypatch):
    calls = counted_module_function(monkeypatch, "_tree_entry")
    size = 200
    objects = {
        10: b"<< /Kids 20 0 R >>",
        20: b"[" + b" ".join(b"<< /Kids 21 0 R >>" for _ in range(size)) + b"]",
        21: b"[" + b" ".join(b"<< /Kids 20 0 R >>" for _ in range(size)) + b"]",
    }
    tree = field_tree_of(acroform_file(b"[10 0 R]", objects))
    assert len(calls) == 1 + 2 * size
    in_20 = tree.nodes[0].kids
    in_21 = tree.nodes[in_20[0]].kids
    assert all(tree.nodes[index].kids == in_21 for index in in_20)
    assert all(tree.nodes[index].kids == in_20 for index in in_21)
    assert all(tree.nodes[index].reached_twice for index in in_20)


def test_a_widget_on_many_pages_is_converted_once(monkeypatch):
    calls = counted_module_function(monkeypatch, "_widget_entry")
    pages = 50
    page_of = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots [6 0 R] >>"
    )
    objects = {
        1: CATALOG,
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % (100 + i) for i in range(pages)), pages),
        6: widget(b"/Rect [0 0 1 1]"),
        **{100 + i: page_of for i in range(pages)},
    }
    document_ = opened(build(objects, 1))
    try:
        widgets = [document_.annotations(i).widgets[0] for i in range(pages)]
    finally:
        document_.close()
    assert len(calls) == 1
    assert all(one.annotation.object_number == 6 for one in widgets)


def test_the_fields_array_used_as_a_kids_array_is_one_node():
    data = acroform_file(b"20 0 R", {20: b"[<< /T (d) /Kids 20 0 R >>]"})
    tree = field_tree_of(data)
    assert len(tree.nodes) == 1
    assert tree.roots == (0,)
    assert tree.nodes[0].kids == (0,)
    assert tree.nodes[0].reached_twice is True


def test_subtype_widget_of_a_field_entry():
    data = annots_file(b"[6 0 R]", {6: widget(b"/Parent 8 0 R"), 8: b"<< /T (a) /Kids [6 0 R] >>"})
    (one,) = annotations_of(data).widgets
    assert [entry.subtype_widget for entry in one.chain] == [True, False]


@pytest.mark.parametrize(
    "parent", [b"/Parent << /T (a) /Kids [6 0 R] >>", b"/Parent 8 0 R"], ids=["direct", "no kids"]
)
def test_parent_kids_is_none_for_a_direct_parent_or_one_without_kids(parent):
    data = annots_file(b"[6 0 R]", {6: widget(parent), 8: b"<< /T (a) >>"})
    (one,) = annotations_of(data).widgets
    assert one.parent_kids is None


def test_a_direct_kids_array_is_read_once_per_parent(monkeypatch):
    calls = counted_module_function(monkeypatch, "_kids")
    data = annots_file(
        b"[6 0 R 7 0 R]",
        {
            6: widget(b"/Parent 8 0 R"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (a) /Kids [6 0 R 7 0 R] >>",
        },
    )
    first, second = annotations_of(data).widgets
    assert len(calls) == 1
    assert first.parent_kids is second.parent_kids
