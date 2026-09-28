"""recover(): a damaged file rebuilt by QPDF, written anew and verified.

Inputs are the truncated cases of tests/synthetic/cases.py; expected page
counts are the pages each case was built with and kept whole before the cut.
"""

from __future__ import annotations

import dataclasses
import io
import threading

import pikepdf
import pytest

import pdfacts
from pdfacts import (
    EmptyInputError,
    EngineError,
    LimitExceededError,
    Limits,
    NotPdfError,
    PasswordRequiredError,
    Recovery,
    RecoveryFailedError,
    Rect,
    Unknown,
)
from pdfacts.backends import qpdf as qpdf_backend
from pdfacts.backends.protocols import EngineFailure, FailureKind, RecoveredBytes
from pdfacts.core import runtime
from synthetic import cases

# The cases QPDF rebuilds, and the pages each keeps.
RECOVERED = {
    "cut inside the xref table": (cases.cut_inside_xref_table, 3),
    "cut before the xref table": (cases.cut_before_xref_table, 3),
    "cut inside the last page": (cases.cut_inside_last_page, 3),
    "cut after the first page": (cases.cut_after_first_page, 1),
}


def recovered(name: str) -> Recovery:
    make, _ = RECOVERED[name]
    return pdfacts.recover(make().data)


def reopened(data: bytes, password: str = "") -> pikepdf.Pdf:
    """The result as QPDF reads it with recovery off, attributes where they are written."""
    return pikepdf.open(
        io.BytesIO(data), password=password, attempt_recovery=False, inherit_page_attributes=False
    )


# --- the table of issue #13 ----------------------------------------------------------


@pytest.mark.parametrize("name", list(RECOVERED))
def test_a_truncated_file_is_recovered_with_the_pages_it_kept(name):
    make, pages = RECOVERED[name]
    case = make()
    assert case.pages_built == 3
    result = pdfacts.recover(case.data)
    assert result.page_count == pages
    assert type(result.data) is bytes


def test_a_cut_after_the_first_page_warns_more_than_a_cut_before_the_table():
    lost = recovered("cut after the first page")
    kept = recovered("cut before the xref table")
    assert len(lost.warnings) > len(kept.warnings)


@pytest.mark.parametrize(
    "data",
    [cases.cut_inside_catalog().data, cases.header_only().data],
    ids=["cut inside the catalog", "header only"],
)
def test_a_file_qpdf_cannot_rebuild_is_a_recovery_failure(data):
    with pytest.raises(RecoveryFailedError) as raised:
        pdfacts.recover(data)
    assert raised.value.detail


def test_empty_input_is_refused():
    with pytest.raises(EmptyInputError):
        pdfacts.recover(cases.empty().data)


def test_input_without_a_header_is_refused():
    with pytest.raises(NotPdfError):
        pdfacts.recover(cases.garbage().data)


@pytest.mark.parametrize("name", list(RECOVERED))
def test_the_result_opens_with_recovery_off_and_without_warnings(name):
    with reopened(recovered(name).data) as pdf:
        assert len(pdf.pages) == RECOVERED[name][1]
        assert pdf.get_warnings() == []


@pytest.mark.parametrize("name", list(RECOVERED))
def test_the_result_has_the_size_of_its_objects_in_the_trailer(name):
    with reopened(recovered(name).data) as pdf:
        highest = max(obj.objgen[0] for obj in pdf.objects if obj.objgen[0] > 0)
        assert pdf.trailer.get("/Size") == highest + 1


@pytest.mark.parametrize("name", list(RECOVERED))
def test_the_result_opens_and_every_page_passes_the_cross_check(name):
    result = recovered(name)
    with pdfacts.open(result.data) as document:
        assert document.page_count == result.page_count
        assert document.warnings == ()
        for page in document.pages:
            assert not isinstance(page.ref.object_number, Unknown), page.ref


def test_the_geometry_survives_recovery():
    case = cases.cut_before_xref_table_rotated_cropped()
    source = case.source
    result = pdfacts.recover(case.data)
    with pdfacts.open(result.data) as document:
        (page,) = document.pages
        geometry = page.geometry
    assert geometry.rotation == source.rotate == 90
    assert geometry.visible_box == Rect(*source.crop_box)


@pytest.mark.parametrize("name", list(RECOVERED))
def test_the_warnings_are_reported_without_memory_addresses(name):
    result = recovered(name)
    assert result.warnings
    assert all(type(warning) is str for warning in result.warnings)
    assert not any("0x" in warning for warning in result.warnings)


def test_an_intact_file_is_rewritten_without_warnings():
    case = cases.three_pages()
    result = pdfacts.recover(case.data)
    assert result.warnings == ()
    assert result.page_count == len(case.page_objects)


def test_the_input_is_not_changed():
    data = cases.cut_before_xref_table().data
    before = bytes(data)
    pdfacts.recover(data)
    assert data == before


def fake_rewrite(monkeypatch, data: bytes, page_count: int) -> None:
    def rewrite(source: bytes, password: str | None) -> RecoveredBytes:
        return RecoveredBytes(data=data, page_count=page_count, warnings=())

    monkeypatch.setattr(qpdf_backend, "rewrite_recovered", rewrite)


def without_size(data: bytes) -> bytes:
    """`data` with the trailer's /Size removed; everything else as it was."""
    trailer = data.rindex(b"trailer")
    start = data.index(b"/Size", trailer)
    end = start + len(b"/Size")
    while end < len(data) and data[end : end + 1] in b" 0123456789":
        end += 1
    return data[:start] + data[end:]


def test_the_verification_catches_a_result_without_a_size(monkeypatch):
    fake_rewrite(monkeypatch, without_size(cases.three_pages().data), 3)
    with pytest.raises(RecoveryFailedError):
        pdfacts.recover(cases.cut_before_xref_table().data)


def test_the_verification_catches_a_result_without_pages(monkeypatch):
    fake_rewrite(monkeypatch, cases.empty_page_tree().data, 0)
    with pytest.raises(RecoveryFailedError):
        pdfacts.recover(cases.cut_before_xref_table().data)


def test_a_user_password_set_and_none_given_is_refused():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.recover(cases.user_password().data)
    assert raised.value.password_was_given is False


def test_more_pages_than_allowed_are_refused():
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.recover(cases.cut_before_xref_table().data, limits=Limits(max_pages=2))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == ("max_pages", 2, 3)


# --- beyond the table ----------------------------------------------------------------


def test_a_wrong_password_is_refused():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.recover(cases.user_password().data, password="wrong")
    assert raised.value.password_was_given is True


def test_an_encrypted_file_stays_encrypted():
    case = cases.user_password()
    result = pdfacts.recover(case.data, password=case.user_password)
    with pytest.raises(pikepdf.PasswordError):
        reopened(result.data)
    with reopened(result.data, case.user_password) as pdf:
        assert pdf.is_encrypted
        assert len(pdf.pages) == case.pages_built


def test_a_file_with_only_an_owner_password_stays_encrypted_and_opens_without_one():
    case = cases.owner_password_only()
    result = pdfacts.recover(case.data)
    with reopened(result.data) as pdf:
        assert pdf.is_encrypted
    assert pdfacts.open(result.data).page_count == case.pages_built


def test_more_objects_than_allowed_are_refused():
    result = pdfacts.recover(cases.cut_before_xref_table().data)
    with reopened(result.data) as pdf:
        size = int(pdf.trailer["/Size"])
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.recover(cases.cut_before_xref_table().data, limits=Limits(max_objects=size - 1))
    assert (raised.value.limit, raised.value.found) == ("max_objects", size)
    limits = Limits(max_objects=size, max_pages=3)
    assert pdfacts.recover(cases.cut_before_xref_table().data, limits=limits).page_count == 3


def test_more_bytes_than_allowed_are_refused_before_the_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(qpdf_backend, "rewrite_recovered", lambda *args: calls.append(args))
    data = cases.cut_before_xref_table().data
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.recover(data, limits=Limits(max_bytes=len(data) - 1))
    assert raised.value.limit == "max_bytes"
    assert calls == []


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"data": bytearray(b"%PDF-1.7")}, TypeError),
        ({"password": b"user"}, TypeError),
        ({"password": "us\x00er"}, ValueError),
        ({"limits": {"max_pages": 2}}, TypeError),
    ],
    ids=["data not bytes", "password not str", "password with NUL", "limits not Limits"],
)
def test_arguments_of_the_wrong_kind_are_refused(arguments, error):
    values = {"data": cases.cut_before_xref_table().data} | arguments
    data = values.pop("data")
    with pytest.raises(error):
        pdfacts.recover(data, **values)


def test_an_unencrypted_result_is_the_same_bytes_each_time():
    data = cases.cut_after_first_page().data
    assert pdfacts.recover(data).data == pdfacts.recover(data).data


def test_the_file_need_not_end_with_eof():
    # open() refuses such a file as damaged; recover() is for exactly those.
    data = cases.cut_before_xref_table().data
    assert b"%%EOF" not in data
    assert pdfacts.recover(data).page_count == 3


def test_every_call_into_qpdf_holds_the_engine_lock(monkeypatch):
    held = []
    original = qpdf_backend.rewrite_recovered

    def watching(data, password):
        free = []

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
        return original(data, password)

    monkeypatch.setattr(qpdf_backend, "rewrite_recovered", watching)
    pdfacts.recover(cases.cut_before_xref_table().data)
    assert held == [True]


def test_recovery_is_frozen_and_exported():
    result = recovered("cut before the xref table")
    assert [field.name for field in dataclasses.fields(Recovery)] == [
        "data",
        "page_count",
        "warnings",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.page_count = 4  # type: ignore[misc]
    assert {"recover", "Recovery"} <= set(pdfacts.__all__)
    assert "data" not in repr(result)


def test_recovery_is_a_capability():
    assert pdfacts.capabilities()[pdfacts.Capability.RECOVERY] is True


def test_no_object_streams_are_generated_and_nothing_is_linearized():
    result = recovered("cut before the xref table")
    with reopened(result.data) as pdf:
        assert not pdf.is_linearized
        assert not any(
            isinstance(obj, pikepdf.Stream) and obj.get("/Type") == "/ObjStm" for obj in pdf.objects
        )


def test_the_verification_catches_a_result_with_warnings(monkeypatch):
    # QPDF opens a page without a media box, and warns about it.
    fake_rewrite(monkeypatch, cases.no_media_box().data, 1)
    with pytest.raises(RecoveryFailedError, match="warnings"):
        pdfacts.recover(cases.cut_before_xref_table().data)


def test_the_verification_catches_other_pages_than_the_rebuilt_document_had(monkeypatch):
    fake_rewrite(monkeypatch, cases.three_pages().data, 2)
    with pytest.raises(RecoveryFailedError, match="pages"):
        pdfacts.recover(cases.cut_before_xref_table().data)


def test_the_verification_catches_a_result_without_a_usable_size(monkeypatch):
    monkeypatch.setattr(qpdf_backend.QpdfDocument, "object_count", lambda self: None)
    with pytest.raises(RecoveryFailedError, match="/Size"):
        pdfacts.recover(cases.cut_before_xref_table().data)


def test_another_failure_of_qpdf_is_an_engine_error(monkeypatch):
    def failing(data, password):
        raise EngineFailure(FailureKind.OTHER, "broken")

    monkeypatch.setattr(qpdf_backend, "rewrite_recovered", failing)
    with pytest.raises(EngineError) as raised:
        pdfacts.recover(cases.cut_before_xref_table().data)
    assert (raised.value.engine, raised.value.detail) == ("qpdf", "broken")


def test_inherited_attributes_stay_where_the_file_writes_them():
    case = cases.media_box_and_rotation_on_pages()
    result = pdfacts.recover(case.data)
    with reopened(result.data) as pdf:
        page = pdf.pages[0].obj
        assert "/MediaBox" not in page and "/Rotate" not in page
        assert pdf.Root.Pages.Rotate == case.pages_rotate
    with pdfacts.open(result.data) as document:
        assert document.pages[0].geometry.rotation == case.pages_rotate
