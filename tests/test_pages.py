"""Pages: geometry from PDFium, references and UserUnit from QPDF, the cross-check.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import threading

import pypdfium2
import pytest

import pdfacts
from engine_lock import after_waiting
from pdfacts import (
    ClosedError,
    DamagedFileError,
    EngineError,
    LimitExceededError,
    Limits,
    LimitUncheckedError,
    Page,
    PageGeometry,
    PageRef,
    Rect,
    Unknown,
    UnknownReason,
)
from pdfacts.backends import pdfium as pdfium_backend
from pdfacts.backends import qpdf
from pdfacts.backends.protocols import EngineFailure, FailureKind, PageObject
from pdfacts.core import runtime
from synthetic import cases
from synthetic.builder import PageSpec, build, document, stream

# The box PDFium uses for a page without any media box: US letter.
LETTER = (0.0, 0.0, 612.0, 792.0)
# PDFium accepts at most this many bytes before the header (measured); QPDF,
# with recovery off, refuses the file.
JUNK_ACCEPTED = 1024


def ordered(box: tuple[float, ...]) -> tuple[float, ...]:
    return (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))


def expected_visible_box(case: cases.Pages, index: int = 0) -> tuple[float, ...]:
    """The media box, US letter without one, intersected with the crop box; corners in order."""
    x0, y0, x1, y1 = ordered(case.media_boxes[index] or case.pages_media_box or LETTER)
    if case.crop_box is not None:
        c0, d0, c1, d1 = ordered(case.crop_box)
        x0, y0, x1, y1 = max(x0, c0), max(y0, d0), min(x1, c1), min(y1, d1)
    return (float(x0), float(y0), float(x1), float(y1))


def expected_rotation(case: cases.Pages) -> int:
    written = case.rotate if case.rotate is not None else case.pages_rotate
    if written is None:
        return 0
    turned = written % 360
    return turned if turned % 90 == 0 else 0


def first_page(data: bytes) -> Page:
    return pdfacts.open(data).pages[0]


def known(ref: PageRef) -> bool:
    return not isinstance(ref.object_number, Unknown) and not isinstance(ref.generation, Unknown)


# --- geometry, references, UserUnit -------------------------------------------


@pytest.mark.parametrize("name", list(cases.GEOMETRY))
def test_the_geometry_of_every_geometry_case(name):
    case = cases.GEOMETRY[name]()
    document_ = pdfacts.open(case.data)
    for index, page in enumerate(document_.pages):
        geometry = page.geometry
        box = expected_visible_box(case, index)
        assert (geometry.visible_box.x0, geometry.visible_box.y0) == box[:2]
        assert (geometry.visible_box.x1, geometry.visible_box.y1) == box[2:]
        assert geometry.rotation == expected_rotation(case)
        width, height = box[2] - box[0], box[3] - box[1]
        turned = geometry.rotation in (90, 270)
        assert (geometry.width, geometry.height) == ((height, width) if turned else (width, height))
        assert geometry.index == index == page.index
        assert known(page.ref), page.ref


@pytest.mark.parametrize(
    "make",
    [
        *(lambda rotation=rotation: cases.rotated(rotation) for rotation in cases.ROTATIONS),
        cases.crop_box_inside,
        cases.crop_box_inside_rotated,
    ],
    ids=["rotation 0", "rotation 90", "rotation 180", "rotation 270", "crop box", "crop box, 90"],
)
def test_the_ink_is_where_the_geometry_says(make):
    # What PDFium draws is placed by the transform of P5: the rectangle the
    # page was built with, mapped to display space, is where the dark pixels
    # are, within one pixel. At scale 1 a pixel is one unit of display space.
    case = make()
    geometry = first_page(case.data).geometry
    expected = geometry.to_display.apply_rect(Rect(*case.rectangle))
    bitmap = pypdfium2.PdfDocument(case.data)[0].render(scale=1, grayscale=True)
    pixels = bytes(bitmap.buffer)
    dark = [
        (column, row)
        for row in range(bitmap.height)
        for column in range(bitmap.width)
        if pixels[row * bitmap.stride + column] < 128
    ]
    assert dark, "nothing was drawn"
    columns = [column for column, _ in dark]
    rows = [row for _, row in dark]
    # Pixel rows count downwards from the top; display space y upwards.
    ink = Rect(
        min(columns), bitmap.height - max(rows) - 1, max(columns) + 1, bitmap.height - min(rows)
    )
    assert (bitmap.width, bitmap.height) == (round(geometry.width), round(geometry.height))
    for drawn, computed in zip(
        (ink.x0, ink.y0, ink.x1, ink.y1),
        (expected.x0, expected.y0, expected.x1, expected.y1),
        strict=True,
    ):
        assert abs(drawn - computed) <= 1, (ink, expected)


def test_the_page_references_are_the_objects_the_builder_assigned():
    case = cases.three_pages()
    refs = [page.ref for page in pdfacts.open(case.data).pages]
    assert refs == [
        PageRef(page_index=index, object_number=number, generation=0)
        for index, number in enumerate(case.page_objects)
    ]


@pytest.mark.parametrize(("text", "expected"), [(None, 1.0), (b"2", 2.0), (b"2.5", 2.5)])
def test_a_user_unit_that_is_absent_or_a_number_above_zero(text, expected):
    case = cases.plain_page() if text is None else cases.user_unit(text)
    user_unit = first_page(case.data).geometry.user_unit
    assert user_unit == expected
    assert type(user_unit) is float


@pytest.mark.parametrize("text", [b"0", b"-1", b"(text)"])
def test_a_user_unit_that_is_not_a_number_above_zero_is_invalid(text):
    user_unit = first_page(cases.user_unit(text).data).geometry.user_unit
    assert isinstance(user_unit, Unknown)
    assert user_unit.reason is UnknownReason.INVALID


@pytest.mark.parametrize(("written", "expected"), [(-90, 270), (450, 90), (45, 0)])
def test_rotations_as_written_pass_the_cross_check(written, expected):
    page = first_page(cases.rotated(written).data)
    assert page.geometry.rotation == expected
    assert known(page.ref)


def test_reversed_corners_pass_the_cross_check():
    assert known(first_page(cases.reversed_corners().data).ref)


def test_a_page_without_any_media_box_is_letter_and_passes_the_cross_check():
    document_ = pdfacts.open(cases.no_media_box().data)
    page = document_.pages[0]
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == LETTER
    assert known(page.ref)
    assert any("MediaBox is undefined" in warning for warning in document_.warnings)


def test_boxes_that_differ_by_no_more_than_a_thousandth_agree():
    # A4 in points: PDFium holds 595.276 as a 32-bit number, QPDF as decimal text.
    page = first_page(document([PageSpec(b"/MediaBox [0 0 595.276 841.89]")]))
    assert page.geometry.visible_box.x1 != 595.276
    assert known(page.ref)


def test_boxes_that_differ_by_more_than_a_thousandth_disagree(monkeypatch):
    original = qpdf.QpdfDocument.page_object

    def off_by_a_hundredth(self, index):
        page = original(self, index)
        return PageObject(
            page.object_number, page.generation, (0.0, 0.0, 400.01, 300.0), None, None, None, False
        )

    monkeypatch.setattr(qpdf.QpdfDocument, "page_object", off_by_a_hundredth)
    ref = first_page(cases.plain_page().data).ref
    assert ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


def test_without_a_media_box_in_qpdf_the_rotation_is_still_compared(monkeypatch):
    original = qpdf.QpdfDocument.page_object

    def turned(self, index):
        page = original(self, index)
        return PageObject(
            page.object_number, page.generation, None, None, 180, page.user_unit, False
        )

    monkeypatch.setattr(qpdf.QpdfDocument, "page_object", turned)
    ref = first_page(cases.no_media_box().data).ref
    assert isinstance(ref.object_number, Unknown)
    assert ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


# --- disagreement and QPDF not available ----------------------------------------


def test_engines_that_disagree_on_the_page_count_leave_every_value_from_qpdf_unknown(monkeypatch):
    original = qpdf.QpdfDocument.page_count
    monkeypatch.setattr(qpdf.QpdfDocument, "page_count", lambda self: original(self) - 1)
    case = cases.three_pages()
    document_ = pdfacts.open(case.data)
    assert isinstance(document_.object_count, Unknown)
    assert document_.object_count.reason is UnknownReason.ENGINES_DISAGREE
    for index, page in enumerate(document_.pages):
        for value in (page.ref.object_number, page.ref.generation, page.geometry.user_unit):
            assert isinstance(value, Unknown)
            assert value.reason is UnknownReason.ENGINES_DISAGREE
        box = page.geometry.visible_box
        assert (box.x0, box.y0, box.x1, box.y1) == expected_visible_box(case, index)
    assert any("PDFium counts 3 pages, QPDF 2" in warning for warning in document_.warnings)


def test_engines_that_disagree_on_one_page_leave_only_that_page_unknown(monkeypatch):
    original = qpdf.QpdfDocument.page_object

    def other_box_for_page_1(self, index):
        page = original(self, index)
        if index != 1:
            return page
        return PageObject(
            page.object_number, page.generation, (0.0, 0.0, 10.0, 10.0), None, None, None, False
        )

    monkeypatch.setattr(qpdf.QpdfDocument, "page_object", other_box_for_page_1)
    case = cases.three_pages()
    document_ = pdfacts.open(case.data)
    pages = list(document_.pages)
    assert known(pages[0].ref) and known(pages[2].ref)
    assert pages[1].ref.object_number.reason is UnknownReason.ENGINES_DISAGREE
    assert pages[1].geometry.user_unit.reason is UnknownReason.ENGINES_DISAGREE
    box = pages[1].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == expected_visible_box(case, 1)
    assert document_.object_count == case.size
    assert any(warning.startswith("page 1:") for warning in document_.warnings)


def test_a_file_qpdf_cannot_open_opens_with_its_values_unknown():
    case = cases.three_pages()
    document_ = pdfacts.open(b"x" * JUNK_ACCEPTED + case.data)
    assert document_.page_count == 3
    assert document_.object_count.reason is UnknownReason.UNREADABLE
    for page in document_.pages:
        assert page.ref.object_number.reason is UnknownReason.UNREADABLE
        assert page.geometry.user_unit.reason is UnknownReason.UNREADABLE
    (reason,) = document_.warnings
    assert reason.startswith("QPDF could not open the file:")


def test_a_page_tree_qpdf_changed_leaves_the_values_of_pages_unreadable():
    kid = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 9 0 R >>"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R %s] /Count 2 >>" % kid,
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 9 0 R >>",
            9: stream(b""),
        },
        1,
    )
    document_ = pdfacts.open(data)
    for page in document_.pages:
        assert page.ref.object_number.reason is UnknownReason.UNREADABLE
    notes = [w for w in document_.warnings if w.startswith("QPDF cannot give the values of pages:")]
    assert len(notes) == 1
    assert document_.warnings[0] == notes[0]


# --- limits ---------------------------------------------------------------------


def test_more_objects_than_allowed():
    case = cases.three_pages()
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.open(case.data, limits=Limits(max_objects=case.size - 1))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_objects",
        case.size - 1,
        case.size,
    )


def test_as_many_objects_as_allowed():
    case = cases.three_pages()
    assert pdfacts.open(case.data, limits=Limits(max_objects=case.size)).object_count == case.size


def test_an_object_limit_that_cannot_be_checked():
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(
            b"x" * JUNK_ACCEPTED + cases.three_pages().data, limits=Limits(max_objects=100)
        )
    assert raised.value.limit == "max_objects"
    assert raised.value.reason.reason is UnknownReason.UNREADABLE


def test_the_deadline_is_checked_before_each_page_is_read():
    now = [0.0]
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    first = document_.pages[0]
    now[0] = 6.0
    # A page already read is not read again.
    assert document_.pages[0] is first
    with pytest.raises(LimitExceededError) as raised:
        document_.pages[1]
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "deadline_seconds",
        5,
        6.0,
    )


def test_the_deadline_counts_from_the_start_of_open():
    now = [100.0]

    def clock():
        now[0] += 1.0
        return now[0]

    # open() reads the clock once, the first page once more: 1 second.
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=1), _clock=clock
    )
    document_.pages[0]
    with pytest.raises(LimitExceededError):
        document_.pages[1]


def test_time_spent_in_open_counts_toward_the_deadline():
    now = [0.0]
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    now[0] = 10.0
    with pytest.raises(LimitExceededError):
        document_.pages[0]


def test_an_index_outside_the_pages_is_refused_before_anything_is_read():
    now = [0.0]
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    now[0] = 10.0
    for index in (-1, 3):
        with pytest.raises(IndexError, match="outside the 3 pages"):
            document_.pages[index]


# --- the page sequence and closing ----------------------------------------------


def test_the_pages_in_order_and_each_read_once():
    document_ = pdfacts.open(cases.three_pages().data)
    pages = list(document_.pages)
    assert [page.index for page in pages] == [0, 1, 2]
    assert len(document_.pages) == 3
    assert all(document_.pages[index] is page for index, page in enumerate(pages))


def test_pages_take_no_slices_and_no_negative_index():
    document_ = pdfacts.open(cases.three_pages().data)
    with pytest.raises(TypeError, match="slices"):
        document_.pages[0:1]
    with pytest.raises(IndexError):
        document_.pages[-1]
    with pytest.raises(IndexError):
        document_.pages[3]
    with pytest.raises(TypeError):
        document_.pages[True]


def test_facts_outlive_the_document_and_the_page_does_not():
    document_ = pdfacts.open(cases.three_pages().data)
    page = document_.pages[1]
    geometry, ref = page.geometry, page.ref
    document_.close()
    assert isinstance(geometry, PageGeometry)
    assert geometry.to_display.apply_rect(geometry.visible_box).x0 == 0.0
    assert ref.object_number == cases.three_pages().page_objects[1]
    for member in ("geometry", "ref", "index"):
        with pytest.raises(ClosedError):
            getattr(page, member)
    for member in ("pages", "object_count", "warnings", "page_count"):
        with pytest.raises(ClosedError):
            getattr(document_, member)


# --- pages that are damaged -----------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        (b"/MediaBox [0 0 " + b"1" + b"0" * 40 + b".0 400]", "not finite"),
        (b"/MediaBox [0 0 400 300] /CropBox [500 500 600 600]", "no visible area"),
        (b"/MediaBox [0 0 0 300]", "no visible area"),
        (b"/MediaBox [0 0 400 300] /CropBox [0 0 0 300]", "no visible area"),
        (b"/MediaBox [0 0 400 300] /CropBox [400 0 500 300]", "no visible area"),
        (b"/MediaBox [0 0 400 0]", "no visible area"),
        (b"/MediaBox [0 0 400 300] /CropBox [0 0 400 0]", "no visible area"),
        (b"/MediaBox [0 0 400 300] /CropBox [0 300 400 400]", "no visible area"),
    ],
    ids=[
        "a number beyond what PDFium holds",
        "crop box outside the media box",
        "flat media box",
        "flat crop box",
        "crop box touching the media box",
        "media box without height",
        "crop box without height",
        "crop box touching the top of the media box",
    ],
)
def test_a_page_whose_box_is_not_one_is_damaged_and_the_others_are_read(entries, reason):
    data = document([PageSpec(b"/MediaBox [0 0 400 300]"), PageSpec(entries)])
    document_ = pdfacts.open(data)
    assert document_.pages[0].geometry.width == 400
    with pytest.raises(DamagedFileError) as raised:
        document_.pages[1]
    assert reason in raised.value.detail
    assert "page 1" in raised.value.detail


def test_a_crop_box_outside_the_media_box_is_damaged_also_without_qpdf():
    data = document([PageSpec(b"/MediaBox [0 0 400 300] /CropBox [500 500 600 600]")])
    document_ = pdfacts.open(b"x" * JUNK_ACCEPTED + data)
    with pytest.raises(DamagedFileError) as raised:
        document_.pages[0]
    assert "no visible area: (0.0, 0.0, 0.0, 0.0)" in raised.value.detail


def test_a_flat_media_box_in_a_file_qpdf_cannot_open_is_letter_from_pdfium():
    # Without QPDF nothing shows that the file writes a flat box; PDFium puts
    # in US letter, as it does for a page without any media box.
    data = document([PageSpec(b"/MediaBox [0 0 0 300]")])
    page = first_page(b"x" * JUNK_ACCEPTED + data)
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == LETTER
    assert page.ref.object_number.reason is UnknownReason.UNREADABLE


def test_page_and_page_ref_are_exported():
    assert {"Page", "PageRef"} <= set(pdfacts.__all__)


# --- engines: lock, closing, failures ---------------------------------------------


def lock_is_held() -> bool:
    free = []

    def probe():
        if runtime.engine_lock.acquire(blocking=False):
            runtime.engine_lock.release()
            free.append(True)
        else:
            free.append(False)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return not free[0]


def test_every_call_into_qpdf_holds_the_engine_lock(monkeypatch):
    held = {}
    cls = qpdf.QpdfDocument
    originals = {
        name: getattr(cls, name)
        for name in ("page_count", "object_count", "page_object", "warnings", "close")
    }
    original_open = cls.open.__func__

    def watching_open(klass, data, password):
        held["open"] = lock_is_held()
        return original_open(klass, data, password)

    def watch(name):
        def watching(self, *args):
            held.setdefault(name, lock_is_held())
            return originals[name](self, *args)

        return watching

    monkeypatch.setattr(cls, "open", classmethod(watching_open))
    for name in originals:
        monkeypatch.setattr(cls, name, watch(name))
    document_ = pdfacts.open(cases.three_pages().data)
    list(document_.pages)
    assert document_.warnings == ()
    document_.close()
    assert held == dict.fromkeys(("open", *originals), True)


def record_closes(monkeypatch) -> list[str]:
    """The backend closes from now on; earlier tests' garbage is collected first."""
    import gc

    gc.collect()
    closes: list[str] = []
    for cls in (qpdf.QpdfDocument, pdfium_backend.PdfiumDocument):
        original = cls.close

        def recording(self, original=original):
            closes.append(type(self).__name__)
            return original(self)

        monkeypatch.setattr(cls, "close", recording)
    return closes


def test_close_closes_both_engines_once(monkeypatch):
    closes = record_closes(monkeypatch)
    document_ = pdfacts.open(cases.plain_page().data)
    document_.close()
    document_.close()
    assert sorted(closes) == ["PdfiumDocument", "QpdfDocument"]


def test_a_document_nobody_closed_closes_both_engines(monkeypatch):
    import gc

    closes = record_closes(monkeypatch)
    document_ = pdfacts.open(cases.plain_page().data)
    del document_
    gc.collect()
    assert sorted(closes) == ["PdfiumDocument", "QpdfDocument"]


def test_a_failing_qpdf_close_still_closes_the_document_and_is_an_engine_error(monkeypatch):
    def failing(self):
        raise EngineFailure(FailureKind.OTHER, "qpdf close failed")

    document_ = pdfacts.open(cases.plain_page().data)
    monkeypatch.setattr(qpdf.QpdfDocument, "close", failing)
    with pytest.raises(EngineError) as raised:
        document_.close()
    assert (raised.value.engine, raised.value.detail) == ("qpdf", "qpdf close failed")
    assert document_.closed is True


def test_a_failure_of_qpdf_after_it_opened_is_an_engine_error(monkeypatch):
    def failing(self):
        raise EngineFailure(FailureKind.OTHER, "count failed")

    monkeypatch.setattr(qpdf.QpdfDocument, "page_count", failing)
    with pytest.raises(EngineError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert (raised.value.engine, raised.value.detail) == ("qpdf", "count failed")


def test_a_failure_after_qpdf_opened_closes_both_engines(monkeypatch):
    closes = record_closes(monkeypatch)
    case = cases.three_pages()
    with pytest.raises(LimitExceededError):
        pdfacts.open(case.data, limits=Limits(max_objects=1))
    assert sorted(closes) == ["PdfiumDocument", "QpdfDocument"]


def test_the_reasons_come_before_what_qpdf_reported():
    document_ = pdfacts.open(cases.no_media_box().data)
    original = qpdf.QpdfDocument.page_object
    try:
        qpdf.QpdfDocument.page_object = lambda self, index: PageObject(  # type: ignore[method-assign]
            4, 0, (0.0, 0.0, 10.0, 10.0), None, None, None, False
        )
        document_.pages[0]
    finally:
        qpdf.QpdfDocument.page_object = original  # type: ignore[method-assign]
    warnings = document_.warnings
    assert warnings[0].startswith("page 0:")
    assert "MediaBox is undefined" in warnings[1]


# --- from the review of #29 --------------------------------------------------------


def test_time_the_engines_spend_in_open_counts_toward_the_deadline(monkeypatch):
    now = [0.0]
    original = pdfium_backend.PdfiumDocument.open.__func__

    def slow(cls, data, password):
        now[0] = 10.0
        return original(cls, data, password)

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "open", classmethod(slow))
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    with pytest.raises(LimitExceededError):
        document_.pages[0]


def test_a_page_past_the_deadline_is_not_read(monkeypatch):
    now = [0.0]
    document_ = pdfacts.open(
        cases.three_pages().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    read = []
    original = pdfium_backend.PdfiumDocument.visible_box

    def watching(self, index):
        read.append(index)
        return original(self, index)

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "visible_box", watching)
    now[0] = 10.0
    with pytest.raises(LimitExceededError):
        document_.pages[1]
    assert read == []


@pytest.mark.parametrize(
    ("theirs", "agree"),
    [
        ((0.0, 0.0, 400.0009, 300.0), True),
        ((0.0, 0.0, 400.0011, 300.0), False),
        # 0.001 away from 0 is exactly the tolerance, also in binary.
        ((0.001, 0.0, 400.0, 300.0), True),
    ],
    ids=["less", "more", "exactly"],
)
def test_boxes_agree_within_a_thousandth_and_no_further(monkeypatch, theirs, agree):
    original = qpdf.QpdfDocument.page_object

    def moved(self, index):
        page = original(self, index)
        return PageObject(page.object_number, page.generation, theirs, None, None, None, False)

    monkeypatch.setattr(qpdf.QpdfDocument, "page_object", moved)
    assert known(first_page(cases.plain_page().data).ref) is agree


def test_a_password_failure_of_qpdf_leaves_its_values_unreadable(monkeypatch):
    def refusing(cls, data, password):
        raise EngineFailure(FailureKind.PASSWORD, "invalid password")

    monkeypatch.setattr(qpdf.QpdfDocument, "open", classmethod(refusing))
    document_ = pdfacts.open(cases.plain_page().data)
    assert document_.pages[0].ref.object_number.reason is UnknownReason.UNREADABLE
    assert document_.warnings == ("QPDF could not open the file: invalid password",)


def test_a_failing_pdfium_close_still_closes_qpdf(monkeypatch):
    closes = record_closes(monkeypatch)
    original = pdfium_backend.PdfiumDocument.close

    def failing(self):
        original(self)
        raise EngineFailure(FailureKind.OTHER, "pdfium close failed")

    document_ = pdfacts.open(cases.plain_page().data)
    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "close", failing)
    with pytest.raises(EngineError) as raised:
        document_.close()
    assert raised.value.engine == "pdfium"
    assert "QpdfDocument" in closes


def test_when_both_closes_fail_the_second_failure_is_noted(monkeypatch):
    def failing(name):
        def close(self):
            raise EngineFailure(FailureKind.OTHER, f"{name} close failed")

        return close

    document_ = pdfacts.open(cases.plain_page().data)
    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "close", failing("pdfium"))
    monkeypatch.setattr(qpdf.QpdfDocument, "close", failing("qpdf"))
    with pytest.raises(EngineError) as raised:
        document_.close()
    assert raised.value.engine == "pdfium"
    assert any("qpdf close failed" in note for note in raised.value.__notes__)


def test_a_document_nobody_closed_closes_qpdf_when_pdfium_fails_to_close(monkeypatch):
    import gc

    closes = record_closes(monkeypatch)
    original = pdfium_backend.PdfiumDocument.close

    def failing(self):
        original(self)
        raise EngineFailure(FailureKind.OTHER, "pdfium close failed")

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "close", failing)
    document_ = pdfacts.open(cases.plain_page().data)
    del document_
    gc.collect()
    assert "QpdfDocument" in closes


@pytest.mark.parametrize(
    ("failure", "raised_as"),
    [
        (lambda: EngineFailure(FailureKind.OTHER, "count failed"), EngineError),
        (KeyboardInterrupt, KeyboardInterrupt),
    ],
    ids=["engine failure", "interrupt"],
)
def test_a_failure_of_qpdf_counting_pages_closes_qpdf(monkeypatch, failure, raised_as):
    closes = record_closes(monkeypatch)

    def failing(self):
        raise failure()

    monkeypatch.setattr(qpdf.QpdfDocument, "page_count", failing)
    with pytest.raises(raised_as):
        pdfacts.open(cases.plain_page().data)
    assert sorted(closes) == ["PdfiumDocument", "QpdfDocument"]


def test_a_media_box_pdfium_reads_otherwise_than_qpdf_disagrees():
    # QPDF finds no usable media box; PDFium reads the string as 0.
    page = first_page(document([PageSpec(b"/MediaBox [(50) 0 400 300]")]))
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == (0.0, 0.0, 400.0, 300.0)
    assert page.ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


@pytest.mark.parametrize("crop_box", [b"[0 0 0 300]", b"[0 0 400 0]"], ids=["width", "height"])
def test_no_media_box_and_a_flat_crop_box_is_damaged(crop_box):
    data = document([PageSpec(b"/CropBox " + crop_box)])
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(data).pages[0]
    assert "no visible area" in raised.value.detail


@pytest.mark.parametrize(
    ("crop_box", "visible"),
    [
        (b"[400 0 500 300]", "(400.0, 0.0, 400.0, 300.0)"),
        (b"[0 300 400 400]", "(0.0, 300.0, 400.0, 300.0)"),
    ],
    ids=["at the right", "at the top"],
)
def test_a_crop_box_touching_the_media_box_is_damaged_also_without_qpdf(crop_box, visible):
    data = document([PageSpec(b"/MediaBox [0 0 400 300] /CropBox " + crop_box)])
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(b"x" * JUNK_ACCEPTED + data).pages[0]
    assert f"no visible area: {visible}" in raised.value.detail


@pytest.mark.parametrize("reason", ["engines disagree", "invalid"])
def test_an_object_limit_with_any_unknown_count_cannot_be_checked(monkeypatch, reason):
    if reason == "engines disagree":
        original = qpdf.QpdfDocument.page_count
        monkeypatch.setattr(qpdf.QpdfDocument, "page_count", lambda self: original(self) - 1)
        data, expected = cases.three_pages().data, UnknownReason.ENGINES_DISAGREE
    else:
        data = cases.plain_page().data.replace(b"/Size 6 ", b"/Size 0 ")
        expected = UnknownReason.INVALID
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(data, limits=Limits(max_objects=100))
    assert raised.value.reason.reason is expected


def test_two_threads_reading_a_page_for_the_first_time_get_the_same_page(monkeypatch):
    import time

    original = pdfium_backend.PdfiumDocument.visible_box

    def slow(self, index):
        time.sleep(0.05)
        return original(self, index)

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "visible_box", slow)
    document_ = pdfacts.open(cases.plain_page().data)
    pages: list[Page] = []
    threads = [threading.Thread(target=lambda: pages.append(document_.pages[0])) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(pages) == 2
    assert pages[0] is pages[1]


def test_a_rotation_pdfium_brings_into_its_form_otherwise_disagrees():
    # PDFium turns 135 into 90; decision 7 counts it as 0.
    page = first_page(cases.rotated(135).data)
    assert page.geometry.rotation == 90
    assert page.ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


# --- from the second review of #29 -------------------------------------------------


@pytest.mark.parametrize(
    ("crop_box", "visible", "agree"),
    [
        (b"[100 100 400 400]", (100.0, 100.0, 400.0, 400.0), True),
        (b"[700 900 1000 1000]", (700.0, 900.0, 1000.0, 1000.0), False),
        (b"[612 0 700 792]", (612.0, 0.0, 700.0, 792.0), False),
    ],
    ids=["inside US letter", "outside US letter", "touching US letter"],
)
def test_without_a_media_box_qpdf_can_use_letter_and_the_crop_box_are_compared(
    crop_box, visible, agree
):
    # QPDF finds no usable media box; PDFium reads the string as 0. US letter
    # standing in for it says nothing about the file: where it leaves no area
    # with the crop box, the page disagrees and is not damaged.
    entries = b"/MediaBox [(0) 0 1000 1000] /CropBox " + crop_box
    page = first_page(document([PageSpec(entries)]))
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == visible
    if agree:
        assert known(page.ref)
    else:
        assert page.ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


def test_a_reversed_crop_box_passes_the_cross_check():
    page = first_page(document([PageSpec(b"/MediaBox [0 0 400 300] /CropBox [380 290 20 10]")]))
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == (20.0, 10.0, 380.0, 290.0)
    assert known(page.ref)


@pytest.mark.parametrize(
    "theirs",
    [(10.0, 0.0, 410.0, 300.0), (0.0, 10.0, 400.0, 310.0)],
    ids=["moved right", "moved up"],
)
def test_a_box_of_the_same_size_elsewhere_disagrees(monkeypatch, theirs):
    original = qpdf.QpdfDocument.page_object

    def moved(self, index):
        page = original(self, index)
        return PageObject(page.object_number, page.generation, theirs, None, None, None, False)

    monkeypatch.setattr(qpdf.QpdfDocument, "page_object", moved)
    ref = first_page(cases.plain_page().data).ref
    assert ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


@pytest.mark.parametrize(
    ("entries", "visible"),
    [
        (b"/MediaBox [0 0 4294967296 300]", LETTER),
        (b"/MediaBox [0 0 -2147483649 300]", LETTER),
        (b"/MediaBox [0 0 +2147483648 300]", LETTER),
        (b"/MediaBox [0 0 400 300] /CropBox [100 0 100.000001 300]", (0.0, 0.0, 400.0, 300.0)),
    ],
    ids=["2**32", "-2**31 - 1", "+2**31", "a real 32 bits make flat"],
)
def test_a_box_only_pdfium_reads_as_flat_is_the_known_gap(entries, visible):
    # Measured: PDFium reads an integer outside its range as 0 and holds
    # reals in 32 bits. It puts in US letter for the flat media box and
    # ignores the flat crop box; QPDF reads the box as written. The engines
    # disagree, and the page is not counted as damaged (docs/conventions.md,
    # "Damaged files").
    page = first_page(document([PageSpec(entries)]))
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == visible
    assert page.ref.object_number.reason is UnknownReason.ENGINES_DISAGREE


@pytest.mark.parametrize("number", [b"2147483648", b"-2147483648"])
def test_an_integer_within_pdfiums_range_is_read(number):
    page = first_page(document([PageSpec(b"/MediaBox [0 0 " + number + b" 300]")]))
    box = page.geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == ordered((0.0, 0.0, float(number), 300.0))
    assert known(page.ref)


@pytest.mark.parametrize(
    ("number", "dropped"),
    [
        (b"9223372036854775807", False),
        (b"9223372036854775808", True),
        (b"-9223372036854775808", False),
        (b"-9223372036854775809", True),
    ],
)
def test_an_integer_beyond_what_qpdf_holds_drops_the_page_in_qpdf(number, dropped):
    # Measured: QPDF reads integers from -2**63 to 2**63 - 1. Outside, it
    # treats the page as null and counts one page fewer: every value from
    # QPDF is unknown.
    first = PageSpec(b"/MediaBox [0 0 " + number + b" 300]")
    document_ = pdfacts.open(document([first, PageSpec(b"/MediaBox [0 0 400 300]")]))
    assert ("PDFium counts 2 pages, QPDF 1" in document_.warnings) is dropped
    assert known(document_.pages[1].ref) is not dropped


def test_a_crop_box_outside_the_media_box_the_file_writes_is_damaged_where_pdfium_reads_another():
    # /MediaBox null counts as absent (ISO 32000, 7.3.9): the media box comes
    # from the page tree node, and the crop box lies outside it. PDFium puts
    # in US letter for the null, which the crop box overlaps: only the file's
    # boxes, read through QPDF, show the damage.
    data = document(
        [PageSpec(b"/MediaBox null /CropBox [500 500 600 600]")],
        pages_entries=b"/MediaBox [0 0 400 300]",
    )
    alone = first_page(b"x" * JUNK_ACCEPTED + data).geometry.visible_box
    assert (alone.x0, alone.y0, alone.x1, alone.y1) == (500.0, 500.0, 600.0, 600.0)
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(data).pages[0]
    assert "media box (0.0, 0.0, 400.0, 300.0)" in raised.value.detail


def read_first_page_after_waiting(monkeypatch, document_, meanwhile) -> object:
    """Page 0 asked for in another thread, which waits for the lock while `meanwhile` runs."""
    return after_waiting(monkeypatch, lambda: document_.pages[0], meanwhile)


def test_a_page_asked_for_while_the_document_closes_is_refused_as_closed(monkeypatch):
    document_ = pdfacts.open(cases.plain_page().data)
    outcome = read_first_page_after_waiting(monkeypatch, document_, document_.close)
    assert isinstance(outcome, ClosedError)


def test_a_thread_that_waited_past_the_deadline_does_not_read_the_page(monkeypatch):
    now = [0.0]
    document_ = pdfacts.open(
        cases.plain_page().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )

    def time_passes() -> None:
        now[0] = 10.0

    outcome = read_first_page_after_waiting(monkeypatch, document_, time_passes)
    assert isinstance(outcome, LimitExceededError)


def test_a_thread_that_waited_while_the_page_was_read_gets_it_also_past_the_deadline(
    monkeypatch,
):
    # The deadline is checked before a page is read, not before one is given.
    now = [0.0]
    document_ = pdfacts.open(
        cases.plain_page().data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    )
    read: list[Page] = []

    def read_then_time_passes() -> None:
        read.append(document_.pages[0])
        now[0] = 10.0

    outcome = read_first_page_after_waiting(monkeypatch, document_, read_then_time_passes)
    assert outcome is read[0]
