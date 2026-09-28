"""recover(): a damaged file rebuilt by QPDF, written anew and verified.

Inputs are the truncated cases of tests/synthetic/cases.py; expected page
counts are the pages each case was built with and kept whole before the cut.
"""

from __future__ import annotations

import dataclasses
import io
import re
import threading
import time
import warnings

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
from synthetic.builder import build, stream
from synthetic.encryption import encrypt

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


def complete_objects(data: bytes, cut_at: int) -> int:
    """The objects of `data` whose "endobj" lies before `cut_at`."""
    kept = data[:cut_at]
    return sum(
        1
        for match in re.finditer(rb"\n\d+ 0 obj\b", kept)
        if kept.find(b"endobj", match.end()) >= 0
    )


@pytest.mark.parametrize("name", list(RECOVERED))
def test_the_result_keeps_every_object_the_cut_left_whole(name):
    # QPDF numbers the objects it writes from 1, and /Size is one more.
    make, _ = RECOVERED[name]
    case = make()
    expected = complete_objects(case.source.data, case.cut_at) + 1
    with reopened(pdfacts.recover(case.data).data) as pdf:
        assert pdf.trailer.get("/Size") == expected


@pytest.mark.parametrize("name", list(RECOVERED))
def test_every_page_the_cut_left_whole_keeps_the_content_it_was_built_with(name):
    make, _ = RECOVERED[name]
    case = make()
    built = cases.rectangle_content(case.source.rectangle, case.source.colour)
    whole = [
        index
        for index, number in enumerate(case.source.page_objects)
        # Each page's content stream is the object after it.
        if case.source.data.find(b"endobj", case.source.data.index(b"\n%d 0 obj" % (number + 1)))
        < case.cut_at
    ]
    assert whole, "no page was left whole"
    with reopened(pdfacts.recover(case.data).data) as pdf:
        for index in whole:
            assert pdf.pages[index].Contents.read_bytes() == built


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
    # QPDF would make /ID from the current second: the two runs straddle one.
    data = cases.cut_after_first_page().data
    first = pdfacts.recover(data).data
    time.sleep(1.05 - time.time() % 1)
    assert pdfacts.recover(data).data == first


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


def test_a_repair_qpdf_makes_while_walking_the_pages_is_among_the_warnings():
    # QPDF gives a page without a media box US letter and says so; the result
    # carries that media box.
    result = pdfacts.recover(cases.no_media_box().data)
    assert any("MediaBox is undefined" in warning for warning in result.warnings)
    with reopened(result.data) as pdf:
        assert list(pdf.pages[0].obj.MediaBox) == [0, 0, 612, 792]


def with_length(data: bytes, number: int, length: int) -> bytes:
    """`data` with the /Length of object `number` changed to `length`."""
    start = data.index(b"/Length ", data.index(b"\n%d 0 obj" % number))
    end = data.index(b" ", start + len(b"/Length "))
    return data[:start] + b"/Length %d" % length + data[end:]


@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "encrypted"])
def test_a_wrong_stream_length_is_among_the_warnings(encrypted):
    # QPDF reads a stream when it meets it, at the latest when it writes it,
    # and finds the length wrong. Nothing else reads an encrypted file's
    # streams before the save.
    if encrypted:
        case = cases.user_password()
        # The content stream's /Length; two digits for two, so that no offset moves.
        assert case.data.count(b"/Length 48 ") == 1
        data, password = case.data.replace(b"/Length 48 ", b"/Length 99 "), case.user_password
    else:
        plain = cases.three_pages()
        data, password = with_length(plain.data, plain.page_objects[-1] + 1, 99), None
    result = pdfacts.recover(data, password=password)
    assert any("recovered stream length" in warning for warning in result.warnings)


def test_an_engine_error_while_checking_the_result_passes_as_it_is(monkeypatch):
    from pdfacts.backends import pdfium as pdfium_backend

    def failing(cls, data, password):
        raise EngineFailure(FailureKind.OTHER, "broken")

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "open", classmethod(failing))
    with pytest.raises(EngineError) as raised:
        pdfacts.recover(cases.cut_before_xref_table().data)
    assert (raised.value.engine, raised.value.detail) == ("pdfium", "broken")


@pytest.mark.parametrize("make", list(cases.ENCRYPTED.values()), ids=list(cases.ENCRYPTED))
def test_an_encrypted_file_whose_trailer_is_lost_is_not_recovered(make):
    # Cut after the encryption dictionary, before the table and the trailer
    # that names it.
    case = make()
    cut = case.data[: case.data.rindex(b"\nxref\n") + 1]
    with pytest.raises(RecoveryFailedError, match="encrypt"):
        pdfacts.recover(cut, password=case.user_password or None)


def test_an_encrypted_file_cut_before_its_encryption_dictionary_is_the_known_gap():
    # Nothing is left that says the file was encrypted: QPDF rebuilds it
    # unencrypted, its streams still ciphertext (docs/conventions.md, "Damaged
    # files"). The encryption dictionary is the last object of this case.
    case = cases.user_password()
    encryption = case.data.rindex(b"\n6 0 obj")
    assert b"/Standard" in case.data[encryption:]
    assert b"/Standard" not in case.data[:encryption]
    result = pdfacts.recover(case.data[:encryption])
    with reopened(result.data) as pdf:
        assert not pdf.is_encrypted
        content = pdf.pages[0].Contents.read_raw_bytes()
    assert content != cases.rectangle_content(cases.RECTANGLE, cases.BLACK)


def test_a_password_for_a_file_that_needs_none_raises_no_python_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert pdfacts.recover(cases.plain_page().data, password="x").page_count == 1


def test_open_with_a_password_the_file_does_not_need_raises_no_python_warning():
    # pikepdf warns about it for a file QPDF wrote; open() used to fail QPDF
    # then under a filter that makes warnings errors.
    data = pdfacts.recover(cases.plain_page().data).data
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pdfacts.open(data, password="x") as document:
            assert document.warnings == ()
            assert not isinstance(document.object_count, Unknown)


def one_page_with(extra: bytes) -> bytes:
    """A one-page file with `extra` as a further object, cut before its table."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 4 0 R >>",
            4: stream(cases.rectangle_content(cases.RECTANGLE, cases.BLACK)),
            5: extra,
        },
        1,
    )
    return data[: data.rindex(b"\nxref\n") + 1]


@pytest.mark.parametrize(
    "dictionary",
    [
        b"<< /Filter /Adobe.PubSec /SubFilter /adbe.pkcs7.s4 /V 2 /Length 128 "
        b"/Recipients [<0102>] >>",
        b"<< /Filter /Adobe.PubSec /SubFilter /adbe.pkcs7.s5 /V 4 /CF << /DefaultCryptFilter "
        b"<< /CFM /AESV2 /Recipients [<0102>] >> >> /StmF /DefaultCryptFilter "
        b"/StrF /DefaultCryptFilter >>",
        b"<< /Filter /Adobe.APS /V 2 /Length 128 >>",
        b"<< /Filter /Adobe.PubSec /CF << /DefaultCryptFilter << /Recipients [<0102>] >> >> >>",
        b"<< /Filter /Adobe.PubSec /StmF /DefaultCryptFilter >>",
        b"<< /Filter /Adobe.PubSec /StrF /DefaultCryptFilter >>",
        b"<< /Filter /Adobe.PubSec /Recipients [<0102>] >>",
        b"<< /Filter /Standard /R 2 /O <00> /U <00> /P -4 >>",
    ],
    ids=[
        "public key, version 2",
        "public key, version 4",
        "another handler",
        "crypt filters only",
        "a stream filter only",
        "a string filter only",
        "recipients only",
        "the standard handler without /V",
    ],
)
def test_an_encryption_dictionary_of_any_handler_left_without_its_trailer_fails(dictionary):
    # Handlers pikepdf cannot write, built as plain objects: once the trailer
    # is cut off, nothing but the dictionary says the file is encrypted.
    with pytest.raises(RecoveryFailedError, match="encrypt"):
        pdfacts.recover(one_page_with(dictionary))


def test_an_aes_256_file_whose_trailer_is_lost_is_not_recovered():
    data = encrypt(cases.plain_page().data, user="user", owner="owner", R=6)
    with pytest.raises(RecoveryFailedError, match="encrypt"):
        pdfacts.recover(data[: data.rindex(b"\nxref\n") + 1], password="user")


def test_a_signature_dictionary_is_not_taken_for_encryption():
    signature = (
        b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached /V 1 "
        b"/ByteRange [0 10 20 30] /Contents <00> >>"
    )
    assert pdfacts.recover(one_page_with(signature)).page_count == 1


def test_an_intact_file_with_white_space_after_its_end_is_rewritten_without_warnings():
    # startxref 1054 bytes before the end, the most QPDF's search reaches
    # (measured): a byte more, and it would rebuild the table.
    data = cases.three_pages().data
    data += b" " * (1054 - (len(data) - data.rindex(b"startxref")))
    assert pdfacts.recover(data).warnings == ()


def test_the_id_of_a_result_comes_from_its_content():
    ids = []
    for case in (cases.cut_after_first_page(), cases.cut_before_xref_table()):
        with reopened(pdfacts.recover(case.data).data) as pdf:
            ids.append(tuple(bytes(part) for part in pdf.trailer.ID))
    assert ids[0] != ids[1]


def test_a_wrong_password_for_a_file_with_only_an_owner_password_is_refused():
    # As open() refuses it.
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.recover(cases.owner_password_only().data, password="wrong")
    assert raised.value.password_was_given is True


def test_a_form_field_is_not_taken_for_encryption():
    # It has /V, the value, and no security handler in /Filter.
    assert pdfacts.recover(one_page_with(b"<< /FT /Tx /T (name) /V (value) >>")).page_count == 1


def test_a_filter_name_alone_is_not_taken_for_encryption():
    extra = b"<< /Filter /FlateDecode /DecodeParms << /Predictor 12 >> >>"
    assert pdfacts.recover(one_page_with(extra)).page_count == 1


def test_the_seed_values_of_a_signature_field_are_not_taken_for_encryption():
    # A seed value dictionary names a handler in /Filter and has /V, but the
    # form reaches it, and only a trailer names an encryption dictionary.
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> "
            b"/Contents 4 0 R >>",
            4: stream(cases.rectangle_content(cases.RECTANGLE, cases.BLACK)),
            5: b"<< /FT /Sig /T (signature) /SV 6 0 R >>",
            6: b"<< /Type /SV /Filter /Adobe.PPKLite /V 1 >>",
        },
        1,
    )
    cut = data[: data.rindex(b"\nxref\n") + 1]
    assert pdfacts.recover(cut).page_count == 1
    assert pdfacts.recover(data).warnings == ()


def test_a_filter_that_is_not_a_name_is_not_taken_for_encryption():
    assert pdfacts.recover(one_page_with(b"<< /Filter [/FlateDecode] /V 1 >>")).page_count == 1


def cut_after_the_update() -> cases.Updated:
    """incremental_update() cut after the object of its update, before its table."""
    updated = cases.incremental_update()
    end = updated.data.index(b"endobj", len(updated.source.data)) + len(b"endobj\n")
    return dataclasses.replace(updated, data=updated.data[:end])


def test_an_update_cut_after_its_object_is_kept():
    # QPDF would read the earlier revision through its startxref, without a
    # word; it rebuilds the file from its objects, the later one counting.
    case = cut_after_the_update()
    result = pdfacts.recover(case.data)
    assert result.warnings
    with pdfacts.open(result.data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == case.media_box


@pytest.mark.parametrize("kept", [b"startxref", b"startxref\n"])
def test_an_update_cut_after_its_startxref_is_kept(kept):
    # QPDF passes over a startxref without an offset and would read the
    # earlier revision through the one before, without a word.
    updated = cases.incremental_update()
    end = updated.data.rindex(b"startxref") + len(kept)
    result = pdfacts.recover(updated.data[:end])
    assert result.warnings
    with pdfacts.open(result.data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == updated.media_box


@pytest.mark.parametrize("written", [b"startxref%d\n", b"startxref\n%dabc\n"])
def test_an_update_whose_last_startxref_qpdf_passes_over_is_kept(written):
    # A startxref glued to its offset, or one whose offset other characters
    # follow: QPDF would read the earlier revision without a word.
    updated = cases.incremental_update()
    start = updated.data.rindex(b"startxref")
    table = int(updated.data[start:].split()[1])
    result = pdfacts.recover(updated.data[:start] + written % table)
    assert result.warnings
    with pdfacts.open(result.data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == updated.media_box


def test_an_update_cut_inside_the_offset_of_its_table_is_kept():
    # The digits left name the earlier table, which QPDF would read through
    # without a word, and does while it rebuilds the file from its objects:
    # an offset that runs to the end of the file may be cut short.
    case = cases.cut_inside_the_last_offset()
    result = pdfacts.recover(case.data)
    assert result.warnings
    with pdfacts.open(result.data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == case.media_box


def test_an_update_cut_inside_its_object_is_not_returned_as_the_earlier_revision():
    # QPDF takes the later, broken object and finds no page; the earlier
    # revision, without a word about the update, is not what was asked for.
    with pytest.raises(RecoveryFailedError):
        pdfacts.recover(cases.cut_inside_the_update().data)


def test_a_form_cut_off_before_its_acroform_raises_no_python_warning():
    # pikepdf's save() warns about widgets the form does not reach.
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm 6 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 4 0 R "
            b"/Annots [5 0 R] >>",
            4: stream(cases.rectangle_content(cases.RECTANGLE, cases.BLACK)),
            5: b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (field) /Rect [10 10 100 30] "
            b"/P 3 0 R >>",
            6: b"<< /Fields [5 0 R] >>",
        },
        1,
    )
    cut = data[: data.index(b"\n6 0 obj") + 1]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert pdfacts.recover(cut).page_count == 1


def without_its_end(data: bytes) -> bytes:
    """`data` without its last %%EOF, which is all that is damaged."""
    end = data.rindex(b"%%EOF")
    return data[:end] + data[end + len(b"%%EOF") :]


def test_a_file_whose_end_alone_is_damaged_is_read_as_it_is_written():
    # QPDF finds its startxref and nothing after it: no rebuild, no warning.
    case = cases.three_pages()
    result = pdfacts.recover(without_its_end(case.data))
    assert result.warnings == ()
    assert result.page_count == len(case.page_objects)


def test_an_encrypted_file_with_object_streams_whose_end_alone_is_damaged_is_recovered():
    # QPDF cannot rebuild such a file from its objects; read through its
    # startxref it needs no rebuilding.
    case = cases.three_pages()
    data = encrypt(case.data, user="", owner="owner", object_streams=True)
    assert b"/ObjStm" in data
    result = pdfacts.recover(without_its_end(data))
    assert result.page_count == len(case.page_objects)
    with reopened(result.data) as recovered_pdf:
        assert recovered_pdf.is_encrypted


def test_an_update_cut_inside_an_object_the_pages_do_not_need_loses_it():
    # Cut after its obj keyword, the later, broken copy counts, and the
    # earlier one is lost with it: the page keeps no content
    # (docs/conventions.md, "Damaged files").
    source = cases.plain_page()
    content = cases.page_object(0) + 1
    updated = cases.append_update(source.data, {content: stream(b"0 0 1 rg 0 0 10 10 re f")}, 1)
    cut = updated[: updated.index(b"stream\n", len(source.data)) + len(b"stream\n0 0 1")]
    result = pdfacts.recover(cut)
    assert result.page_count == 1
    assert result.warnings
    with reopened(result.data) as pdf:
        assert "/Contents" not in pdf.pages[0].obj
