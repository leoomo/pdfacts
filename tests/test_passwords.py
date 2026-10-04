"""A password outside ASCII, as each engine is given it (docs/conventions.md,
"Errors").

The files are built with the byte-level builder and encrypted by pikepdf,
which writes a password of revision 2, 3 or 4 as PDFDocEncoding bytes, the
same as Latin-1 for the letters of Latin-1, and the euro sign as 0xA0, and
one of revision 6 as UTF-8, or as the bytes it is given; the expected values
are the passwords chosen and what was built (AGENTS.md, rule 2).
"""

from __future__ import annotations

import contextlib
import io

import pikepdf
import pytest

import pdfacts
from pdfacts import PasswordRequiredError
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, stream
from synthetic.encryption import encrypt

BODY = b"0 0 m 10 10 l S"
PLAIN = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> "
        b"/Contents 4 0 R >>",
        4: stream(BODY),
    },
    1,
)
REVISIONS = [
    {"R": 2, "aes": False, "metadata": False},
    {"R": 3, "aes": False, "metadata": False},
    {"R": 4},
    {"R": 6},
]
IDS = ["revision 2", "revision 3", "revision 4", "revision 6"]
WORD = "pässwörd"


def encrypted(user: str, options: dict, owner: str = "owner") -> bytes:
    return encrypt(PLAIN, user=user, owner=owner, **options)


def opened_by_both(data: bytes, password: str) -> None:
    """open() reads the file without a warning, QPDF's values known."""
    with pdfacts.open(data, password=password) as document:
        assert document.warnings == ()
        assert isinstance(document.object_count, int)


@pytest.mark.parametrize("options", REVISIONS, ids=IDS)
def test_a_user_password_outside_ascii(options):
    """Both engines open the file with it, and the values from QPDF are known."""
    with pdfacts.open(encrypted(WORD, options), password=WORD) as document:
        assert document.warnings == ()
        assert isinstance(document.object_count, int)
        encryption = pdfacts.inspect(document).encryption
        assert encryption.user_password_matched is True
        assert encryption.r == options["R"]


@pytest.mark.parametrize("options", REVISIONS, ids=IDS)
def test_a_wrong_password_outside_ascii(options):
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.open(encrypted(WORD, options), password="passwörd")
    assert raised.value.password_was_given is True


@pytest.mark.parametrize("options", REVISIONS[:3], ids=IDS[:3])
def test_a_character_latin_1_lacks(options):
    """The euro sign: pikepdf writes it as PDFDocEncoding's 0xA0 below revision
    5, and neither engine opens the file with it there (measured)."""
    with pytest.raises(PasswordRequiredError):
        pdfacts.open(encrypted("€uro", options), password="€uro")


def test_a_character_latin_1_lacks_at_revision_6():
    with pdfacts.open(encrypted("€uro", {"R": 6}), password="€uro") as document:
        assert document.warnings == ()


@pytest.mark.parametrize("options", REVISIONS[:3], ids=IDS[:3])
@pytest.mark.parametrize(
    "given, written",
    [("€uro", "¬uro"), ("Ĥx", "$x"), ("ä€", "ä¬")],
    ids=["the euro sign", "H with circumflex", "a Latin-1 letter and the euro sign"],
)
def test_the_low_byte_of_each_character(given, written, options):
    """Below revision 5 PDFium takes the low byte of each character's code:
    0x20AC, the euro sign, as 0xAC, the not sign; 0x0124 as 0x24, the dollar
    sign (measured). The file's password is that of the low bytes."""
    data = encrypted(written, options)
    opened_by_both(data, given)
    result = pdfacts.decrypt(data, password=given)
    with pikepdf.open(io.BytesIO(result.data), attempt_recovery=False) as pdf:
        assert pdf.pages[0].Contents.read_bytes() == BODY


def test_utf_8_read_as_latin_1_at_revision_6():
    """From revision 5 on PDFium takes the UTF-8 bytes read as Latin-1 and
    written as UTF-8 again (measured): the file's password is that text."""
    written = "pässwörd".encode().decode("latin-1")
    assert written == "pÃ¤sswÃ¶rd"
    opened_by_both(encrypted(written, {"R": 6}), "pässwörd")


def test_latin_1_bytes_at_revision_6():
    """A password of revision 6 written as Latin-1 bytes, which neither engine
    takes from the text: the low bytes open the file in QPDF, at a revision
    they are not for."""
    data = encrypt(PLAIN, user=b"p\xe4ssw\xf6rd", owner="owner", R=6)
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, WORD)
    assert raised.value.kind is FailureKind.PASSWORD
    with pytest.raises(PasswordRequiredError):
        pdfacts.recover(data, password=WORD)
    with pytest.raises(PasswordRequiredError):
        pdfacts.open(data, password=WORD)


@pytest.mark.parametrize(
    "written, given, revision",
    [("user", "userĀjunk", 4), ("user", "user一", 3), ("", "Ā", 2)],
    ids=["after the user password", "at the end", "for an empty user password"],
)
def test_a_low_byte_of_nul(written, given, revision):
    """U+0100 and U+4E00 have the low byte 0. QPDF reads a password up to its
    first NUL, so their low bytes would open a file whose password is what
    precedes it, which PDFium refuses (measured): they are not given."""
    options = {"R": revision, "aes": False, "metadata": False} if revision < 4 else {"R": 4}
    data = encrypted(written, options)
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, given)
    assert raised.value.kind is FailureKind.PASSWORD
    with pytest.raises(PasswordRequiredError):
        pdfacts.recover(data, password=given)
    with pytest.raises(PasswordRequiredError):
        pdfacts.open(data, password=given)


@pytest.mark.parametrize("revision", [2, 4])
def test_a_low_byte_of_nul_after_32_bytes(revision):
    """Below revision 5 the first 32 bytes of a password are all that count, in
    both engines: a NUL after them is no reason to hold the low bytes back."""
    options = {"R": revision, "aes": False, "metadata": False} if revision < 4 else {"R": 4}
    written = "é" + "a" * 31
    opened_by_both(encrypted(written, options), written + "Ā")


def test_revision_5():
    """Revision 5 takes a password as UTF-8, as revision 6 does (measured)."""
    for written in (WORD, WORD.encode().decode("latin-1")):
        with pytest.warns(UserWarning, match="R=5 is deprecated"):
            data = encrypted(written, {"R": 5})
        opened_by_both(data, WORD)


def test_the_conversion_of_revision_6_at_revision_4():
    """A file of revision 4 whose password is the bytes the conversion of
    revision 5 on makes of `ä`, written through PDFDocEncoding: neither engine
    takes them below revision 5 (measured)."""
    again = "ä".encode().decode("latin-1").encode()
    data = encrypted(again.decode("pdfdoc"), {"R": 4})
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, "ä")
    assert raised.value.kind is FailureKind.PASSWORD


@pytest.mark.parametrize("revision", [5, 6])
@pytest.mark.parametrize(
    "written, given",
    [
        ("a" * 127, "a" * 128),
        (("ä" * 43).encode().decode("latin-1").encode()[:127], "ä" * 43),
    ],
    ids=["a text of 128 bytes", "a conversion of 172 bytes"],
)
def test_a_password_longer_than_127_bytes(written, given, revision):
    """From revision 5 on QPDF takes the first 127 bytes of a password and
    PDFium all of them (measured): the file's password is 127 bytes long, the
    first of the text given or of the 172 bytes it converts to, which pikepdf
    writes as given there. Neither engine opens the file."""
    with pytest.warns(UserWarning) if revision == 5 else contextlib.nullcontext():
        data = encrypt(PLAIN, user=written, owner="owner", R=revision)
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, given)
    assert raised.value.kind is FailureKind.PASSWORD
    with pytest.raises(PasswordRequiredError):
        pdfacts.recover(data, password=given)
    with pytest.raises(PasswordRequiredError):
        pdfacts.open(data, password=given)


def test_a_version_of_5_and_a_revision_of_4():
    """A file written at revision 5, its password the Latin-1 bytes of
    `pässwörd`, and /R made 4 at the same length: QPDF reads the password by
    /V, PDFium by /R (measured), so no conversion counts for it."""
    with pytest.warns(UserWarning):
        data = encrypt(PLAIN, user=b"p\xe4ssw\xf6rd", owner="owner", R=5)
    assert data.count(b"/R 5") == 1
    data = data.replace(b"/R 5", b"/R 4")
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, WORD)
    assert raised.value.kind is FailureKind.PASSWORD
    with pytest.raises(PasswordRequiredError):
        pdfacts.recover(data, password=WORD)


def test_a_password_qpdf_cannot_take():
    """At revision 6 a password of 128 bytes opens the file in PDFium alone: a
    known gap, every value from QPDF unknown."""
    with pdfacts.open(encrypted("a" * 128, {"R": 6}), password="a" * 128) as document:
        assert isinstance(document.object_count, pdfacts.Unknown)
        assert document.object_count.reason is pdfacts.UnknownReason.UNREADABLE
        assert document.warnings[0].startswith("QPDF could not open the file")


@pytest.mark.parametrize("options", REVISIONS, ids=IDS)
def test_an_owner_password_outside_ascii(options):
    data = encrypted("user", options, owner=WORD)
    opened_by_both(data, WORD)
    with pdfacts.open(data, password=WORD) as document:
        assert pdfacts.inspect(document).encryption.owner_password_matched is True


@pytest.mark.parametrize("options", REVISIONS, ids=IDS)
def test_qpdf_given_the_password(options):
    """The QPDF backend alone, as recover() reaches it first."""
    document = QpdfDocument.open(encrypted(WORD, options), WORD)
    try:
        assert document.security_handler_revision() == options["R"]
    finally:
        document.close()


@pytest.mark.parametrize("options", REVISIONS[:3], ids=IDS[:3])
def test_qpdf_given_a_character_latin_1_lacks(options):
    """The QPDF backend alone refuses it as a wrong password."""
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(encrypted("€uro", options), "€uro")
    assert raised.value.kind is FailureKind.PASSWORD


def test_recover_with_a_character_latin_1_lacks():
    """recover() has QPDF read the file first."""
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.recover(encrypted("€uro", {"R": 4}), password="€uro")
    assert raised.value.password_was_given is True


def test_recover_with_the_password():
    result = pdfacts.recover(encrypted(WORD, {"R": 4}), password=WORD)
    assert result.page_count == 1
    with pdfacts.open(result.data, password=WORD) as document:
        assert document.warnings == ()


@pytest.mark.parametrize("options", REVISIONS, ids=IDS)
def test_decrypt_with_the_password(options):
    result = pdfacts.decrypt(encrypted(WORD, options), password=WORD)
    with pikepdf.open(io.BytesIO(result.data), attempt_recovery=False) as pdf:
        assert not pdf.is_encrypted
        assert pdf.pages[0].Contents.read_bytes() == BODY


def test_an_ascii_password_is_given_once(monkeypatch):
    """A wrong ASCII password is not tried again: its Latin-1 bytes are its
    UTF-8 bytes."""
    data = encrypted("user", {"R": 4})
    tried = []
    opened = pikepdf.open

    def recording(source, *, password, **options):
        tried.append(password)
        return opened(source, password=password, **options)

    monkeypatch.setattr(pikepdf, "open", recording)
    with pytest.raises(EngineFailure) as raised:
        QpdfDocument.open(data, "wrong")
    assert raised.value.kind is FailureKind.PASSWORD
    assert tried == ["", "wrong"]
