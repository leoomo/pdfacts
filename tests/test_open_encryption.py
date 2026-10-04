"""open() checks that the engines agree on the encryption (docs/conventions.md,
"Object identity").

The files are built with the byte-level builder, then encrypted by pikepdf
with the revision the test chooses; the expected values are those chosen
(AGENTS.md, rule 2). An update whose trailer leaves out /Encrypt is appended
by the builder; one that lists an object twice is written byte by byte.
"""

from __future__ import annotations

import gc
import re

import pytest

import pdfacts
from pdfacts import EngineError, Limits, LimitUncheckedError, Unknown, UnknownReason
from pdfacts.backends import pdfium as pdfium_backend
from pdfacts.backends import qpdf as qpdf_backend
from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import append_update, build, build_with_object_stream, stream
from synthetic.encryption import encrypt

PLAIN = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        4: stream(b"0 0 m 10 10 l S"),
    },
    1,
)
REVISIONS = [
    ({"R": 2, "aes": False, "metadata": False}, 2),
    ({"R": 3, "aes": False, "metadata": False}, 3),
    ({"R": 4, "aes": False, "metadata": False}, 4),
    ({"R": 4}, 4),
    ({"R": 6}, 6),
]
IDS = ["revision 2", "revision 3", "revision 4, RC4", "revision 4, AES", "revision 6"]


def hidden() -> bytes:
    """Revision 4 with RC4, and an update that writes object `/Size` anew, its
    trailer without /Encrypt."""
    data = encrypt(PLAIN, user="", owner="o", R=4, aes=False, metadata=False, compress=False)
    sizes = {int(size) for size in re.findall(rb"/Size (\d+)", data)}
    assert len(sizes) == 1
    updated = append_update(data, {sizes.pop(): b"42"}, 1)
    trailer = updated[updated.rindex(b"trailer") :]
    assert b"/Encrypt" not in trailer
    return updated


def listed_twice(last: bytes | None = None) -> bytes:
    """Revision 4, and an update that lists the encryption dictionary's number
    twice in one section: first the revision-4 dictionary, last one of
    revision 6, written for the same file, or `last`. QPDF reads the first
    entry, PDFium the last (docs/conventions.md, "Object identity", a known
    gap)."""

    def dictionary(data: bytes) -> tuple[int, bytes]:
        number = int(re.findall(rb"/Encrypt (\d+) 0 R", data)[0])
        found = re.findall(rb"(?<!\d)%d 0 obj\n(.*?)\nendobj" % number, data, re.S)
        assert len(found) == 1
        return number, found[0]

    data = encrypt(PLAIN, user="", owner="o", R=4, compress=False)
    number, first = dictionary(data)
    if last is None:
        _, last = dictionary(encrypt(PLAIN, user="", owner="o", R=6, compress=False))
    trailer = data[data.rindex(b"trailer") :]
    size = int(re.findall(rb"/Size (\d+)", trailer)[0])
    identifier = re.findall(rb"/ID ?\[[^\]]*\]", trailer)[0]
    previous = int(data[data.rindex(b"startxref") :].split()[1])
    out = bytearray(data)
    at_first = len(out)
    out += b"%d 0 obj\n%s\nendobj\n" % (number, first)
    at_last = len(out)
    out += b"%d 0 obj\n%s\nendobj\n" % (number, last)
    table = len(out)
    out += b"xref\n%d 1\n%010d 00000 n \n%d 1\n%010d 00000 n \n" % (
        number,
        at_first,
        number,
        at_last,
    )
    out += b"trailer\n<< /Size %d /Root 1 0 R /Encrypt %d 0 R %s /Prev %d >>\n" % (
        size,
        number,
        identifier,
        previous,
    )
    out += b"startxref\n%d\n%%%%EOF\n" % table
    return bytes(out)


def revisions(data: bytes) -> tuple[int | None, int | None]:
    """What each backend reads: PDFium's, then QPDF's."""
    pdfium = PdfiumDocument.open(data, None)
    try:
        found = pdfium.security_handler_revision()
    finally:
        pdfium.close()
    qpdf = QpdfDocument.open(data, None)
    try:
        return found, qpdf.security_handler_revision()
    finally:
        qpdf.close()


def test_a_file_that_is_not_encrypted():
    assert revisions(PLAIN) == (None, None)


@pytest.mark.parametrize("options, revision", REVISIONS, ids=IDS)
def test_each_revision_as_both_engines_read_it(options, revision):
    data = encrypt(PLAIN, user="", owner="o", **options)
    assert revisions(data) == (revision, revision)
    with pdfacts.open(data) as document:
        assert not isinstance(document.object_count, Unknown)


def test_the_owners_password():
    data = encrypt(PLAIN, user="u", owner="o", R=4)
    pdfium = PdfiumDocument.open(data, "o")
    try:
        assert pdfium.security_handler_revision() == 4
    finally:
        pdfium.close()
    qpdf = QpdfDocument.open(data, "o")
    try:
        assert qpdf.security_handler_revision() == 4
    finally:
        qpdf.close()


def test_an_update_that_hides_the_encryption_from_qpdf():
    """QPDF takes /Encrypt from the newest trailer only, PDFium from the newest
    trailer that writes it (measured): PDFium decrypts the file, QPDF reads it
    as not encrypted."""
    assert revisions(hidden()) == (4, None)


def test_engines_that_disagree_on_the_encryption_leave_every_value_from_qpdf_unknown():
    reason = Unknown(
        UnknownReason.ENGINES_DISAGREE,
        "PDFium reads the file as encrypted, revision 4, QPDF as not encrypted",
    )
    with pdfacts.open(hidden()) as document:
        assert document.object_count == reason
        assert reason.detail in document.warnings
        assert document.pages[0].ref.object_number == reason
        assert document.page_count == 1


def test_engines_that_read_another_revision_leave_every_value_from_qpdf_unknown():
    reason = Unknown(
        UnknownReason.ENGINES_DISAGREE,
        "PDFium reads the file as encrypted, revision 6, QPDF as encrypted, revision 4",
    )
    data = listed_twice()
    assert revisions(data) == (6, 4)
    with pdfacts.open(data) as document:
        assert document.object_count == reason


@pytest.mark.parametrize("password", ["u", "o"], ids=["the user's", "the owner's"])
def test_the_check_with_a_password_given(password):
    """The update that hides the encryption, on a file whose user password is
    u and owner password o."""
    data = encrypt(PLAIN, user="u", owner="o", R=4, aes=False, metadata=False, compress=False)
    sizes = {int(size) for size in re.findall(rb"/Size (\d+)", data)}
    assert len(sizes) == 1
    with pdfacts.open(append_update(data, {sizes.pop(): b"42"}, 1), password=password) as document:
        assert document.object_count == Unknown(
            UnknownReason.ENGINES_DISAGREE,
            "PDFium reads the file as encrypted, revision 4, QPDF as not encrypted",
        )


def test_qpdf_encrypted_and_pdfium_not():
    """The number listed twice, null last: QPDF reads the dictionary of
    revision 4, PDFium null."""
    data = listed_twice(b"null")
    assert revisions(data) == (None, 4)
    with pdfacts.open(data) as document:
        assert document.object_count == Unknown(
            UnknownReason.ENGINES_DISAGREE,
            "PDFium reads the file as not encrypted, QPDF as encrypted, revision 4",
        )


def test_the_encryption_compared_before_the_object_streams():
    """Object 5 compressed in object stream 7, the catalog and the pages not;
    revision 4, and the update that hides the encryption from QPDF, which
    then reads the object stream from bytes PDFium decrypts: the encryption
    is the reason, not the object streams."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /Extra 5 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        4: stream(b"0 0 m 10 10 l S"),
    }
    plain = build_with_object_stream(objects, [(5, b"<< /A 1 >>")], {5: 0}, 1)
    data = encrypt(plain, user="", owner="o", R=4, aes=False, metadata=False, compress=False)
    assert b"/ObjStm" in data
    sizes = {int(size) for size in re.findall(rb"/Size (\d+)", data)}
    with pdfacts.open(append_update(data, {max(sizes): b"42"}, 1)) as document:
        assert document.object_count == Unknown(
            UnknownReason.ENGINES_DISAGREE,
            "PDFium reads the file as encrypted, revision 4, QPDF as not encrypted",
        )


@pytest.mark.parametrize(
    "limits, limit",
    [
        (Limits(max_objects=100), "max_objects"),
        (Limits(max_forms_per_page=100), "max_forms_per_page"),
    ],
    ids=["max_objects", "max_forms_per_page"],
)
def test_a_set_limit_unchecked_where_the_engines_disagree(limits, limit):
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(hidden(), limits=limits)
    assert raised.value.limit == limit
    assert raised.value.reason == Unknown(
        UnknownReason.ENGINES_DISAGREE,
        "PDFium reads the file as encrypted, revision 4, QPDF as not encrypted",
    )


@pytest.mark.parametrize(
    "failing, closed",
    [
        (qpdf_backend.QpdfDocument, ["PdfiumDocument", "QpdfDocument"]),
        (pdfium_backend.PdfiumDocument, ["PdfiumDocument"]),
    ],
    ids=["QPDF's", "PDFium's"],
)
def test_a_failure_reading_a_revision_closes_what_is_open(monkeypatch, failing, closed):
    gc.collect()
    closes: list[str] = []
    for cls in (qpdf_backend.QpdfDocument, pdfium_backend.PdfiumDocument):
        original = cls.close

        def recording(self, original=original):
            closes.append(type(self).__name__)
            return original(self)

        monkeypatch.setattr(cls, "close", recording)

    def fails(self):
        raise EngineFailure(FailureKind.OTHER, "the revision failed")

    monkeypatch.setattr(failing, "security_handler_revision", fails)
    with pytest.raises(EngineError):
        pdfacts.open(PLAIN)
    assert sorted(closes) == closed


@pytest.mark.parametrize("written", [b"null", b"99 0 R"], ids=["null", "a reference to no object"])
def test_an_encryption_hidden_from_both_engines(written):
    """An update whose trailer writes /Encrypt null, or a reference to no
    object: neither engine decrypts the file, and the check sees nothing;
    recover() refuses it (docs/conventions.md, "Object identity")."""
    data = encrypt(PLAIN, user="", owner="o", R=4, aes=False, metadata=False, compress=False)
    sizes = {int(size) for size in re.findall(rb"/Size (\d+)", data)}
    updated = append_update(data, {max(sizes): b"42"}, 1)
    at = updated.rindex(b"/Root 1 0 R")
    updated = updated[:at] + b"/Encrypt " + written + b" " + updated[at:]
    startxref = updated.rindex(b"startxref")
    table = int(updated[startxref:].split()[1])
    assert updated[table : table + 4] == b"xref"
    assert revisions(updated) == (None, None)
    with pytest.raises(pdfacts.RecoveryFailedError):
        pdfacts.recover(updated)
