"""The edit session: operations collected, applied in one verified write
(docs/architecture.md, rules 3 and 4; docs/conventions.md, "The edit session").

Files are built with the byte-level builder and encrypted by pikepdf; the
expected values are those written (AGENTS.md, rule 2).
"""

from __future__ import annotations

import io

import pikepdf
import pytest
from pikepdf import Encryption

import pdfacts
from pdfacts import decrypt, recover
from pdfacts.edit import Decrypt, EditSession, Rewrite
from synthetic.builder import build, stream

PLAIN = build(
    {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << >> >>",
        4: stream(b"0 0 m 10 10 l S"),
    },
    1,
)


def encrypted(data: bytes, user: str = "", owner: str = "o") -> bytes:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pdf.save(
            out,
            encryption=Encryption(user=user, owner=owner, R=4, metadata=False),
            compress_streams=False,
        )
    return out.getvalue()


def damaged(data: bytes) -> bytes:
    return data + b"% garbage tail that hides the end of the file"


def test_the_session_takes_only_operations():
    with pytest.raises(TypeError):
        EditSession(PLAIN).add("decrypt")


def test_the_session_takes_only_bytes():
    with pytest.raises(TypeError):
        EditSession("not bytes")


def test_an_empty_session_rewrites():
    """No operation is one Rewrite: the file written anew, checked."""
    found = EditSession(PLAIN).apply()
    recovery = recover(PLAIN)
    assert found.data == recovery.data
    assert found.page_count == recovery.page_count == 1


def test_rewrite_alone_is_recover_on_an_intact_file():
    found = EditSession(PLAIN).add(Rewrite()).apply()
    assert found == EditSession(PLAIN).apply()


def test_decrypt_alone_is_decrypt():
    data = encrypted(PLAIN)
    found = EditSession(data).add(Decrypt()).apply()
    expected = decrypt(data)
    assert found.data == expected.data
    assert found.page_count == expected.page_count


def test_a_file_that_is_not_encrypted_is_refused():
    with pytest.raises(pdfacts.NotEncryptedError):
        EditSession(PLAIN).add(Decrypt()).apply()


def test_a_damaged_encrypted_file_both_operations():
    """What the functions cannot do apart: a damaged encrypted file rebuilt
    and decrypted in one write, checked on its own."""
    data = damaged(encrypted(PLAIN))
    with pytest.raises(pdfacts.DamagedFileError):
        decrypt(data)
    recovery = recover(data)
    with pikepdf.open(io.BytesIO(recovery.data)) as pdf:
        assert pdf.is_encrypted
    found = EditSession(data).add(Rewrite()).add(Decrypt()).apply()
    with pikepdf.open(io.BytesIO(found.data)) as pdf:
        assert not pdf.is_encrypted
    assert found.page_count == 1
    with pdfacts.open(found.data) as document:
        assert document.warnings == ()


def test_operations_in_any_order():
    data = damaged(encrypted(PLAIN))
    one = EditSession(data).add(Rewrite()).add(Decrypt()).apply()
    two = EditSession(data).add(Decrypt()).add(Rewrite()).apply()
    assert one.data == two.data


def test_a_wrong_password_is_refused():
    data = encrypted(PLAIN, user="u")
    with pytest.raises(pdfacts.PasswordRequiredError):
        EditSession(data, password="wrong").add(Decrypt()).apply()


def test_the_page_limit_of_the_result():
    from synthetic.builder import build as _build

    two = _build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
            b" /Resources << >> >>",
            4: stream(b"0 0 m 10 10 l S"),
            5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 6 0 R"
            b" /Resources << >> >>",
            6: stream(b"0 0 m 10 10 l S"),
        },
        1,
    )
    with pytest.raises(pdfacts.LimitExceededError) as raised:
        EditSession(two).add(Rewrite()).apply(limits=pdfacts.Limits(max_pages=1))
    assert raised.value.limit == "max_pages"


def test_the_object_limit_of_the_result():
    with pytest.raises(pdfacts.LimitExceededError) as raised:
        EditSession(PLAIN).add(Rewrite()).apply(limits=pdfacts.Limits(max_objects=1))
    assert raised.value.limit == "max_objects"


def test_the_result_is_frozen():
    found = EditSession(PLAIN).apply()
    with pytest.raises(AttributeError):
        found.page_count = 2


def test_the_session_can_be_reused():
    session = EditSession(PLAIN)
    session.add(Rewrite())
    assert session.apply().data == session.apply().data


def test_it_is_exported():
    for name in ("Decrypt", "EditSession", "Edited", "Rewrite"):
        assert hasattr(pdfacts, name)


def test_an_empty_session_writes_the_file():
    """An empty session is one Rewrite: it writes the file anew, checked —
    the bytes differ from the input's (a new /ID) but open cleanly."""
    found = EditSession(PLAIN).apply()
    assert found.data != PLAIN
    with pdfacts.open(found.data) as document:
        assert document.page_count == 1


def test_a_damaged_input_with_decrypt_alone_is_refused():
    data = damaged(PLAIN)
    with pytest.raises(pdfacts.DamagedFileError):
        EditSession(data).add(Decrypt()).apply()


def test_the_written_file_is_not_encrypted():
    """A Decrypt session's written file, read by PDFium alone: no
    encryption. A fault that keeps the encryption must fail here."""
    data = encrypted(PLAIN)
    found = EditSession(data).add(Decrypt()).apply()
    with pdfacts.open(found.data) as document:
        with document._access() as access:
            assert access.security_handler_revision() is None


def test_the_written_file_opens_with_the_page_count_qpdf_wrote():
    data = damaged(encrypted(PLAIN))
    found = EditSession(data).add(Rewrite()).add(Decrypt()).apply()
    with pdfacts.open(found.data) as document:
        assert document.page_count == found.page_count == 1


def test_the_written_file_has_a_usable_size():
    data = damaged(encrypted(PLAIN))
    found = EditSession(data).add(Rewrite()).add(Decrypt()).apply()
    with pdfacts.open(found.data) as document:
        assert isinstance(document.object_count, int)


def test_the_written_file_gives_no_warning():
    data = damaged(encrypted(PLAIN))
    found = EditSession(data).add(Rewrite()).add(Decrypt()).apply()
    with pdfacts.open(found.data) as document:
        assert document.warnings == ()


def test_a_damaged_decrypt_result_encrypted_refused(monkeypatch):
    """The check of the damaged path: a written file PDFium still reads as
    encrypted is refused (a fault that keeps the encryption)."""

    class FakeDoc:
        warnings: tuple[str, ...] = ()
        page_count = 1
        object_count = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            pass

        def _access(self):
            import contextlib

            @contextlib.contextmanager
            def access():
                class A:
                    security_handler_revision = staticmethod(lambda: 4)

                yield A()

            return access()

    calls = []

    def open_or_damaged(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            # The input check (#1) and _verify's source (#2): this input is
            # damaged, and a Rewrite stands beside the Decrypt, so both go the
            # damaged way — _verify's source open also raises DamagedFileError
            # only when _check_decryption opens it; but _check_decryption
            # catches DamagedFileError itself. Give #2 a doc whose access says
            # encrypted to satisfy _check_encrypted... simpler: raise
            # DamagedFileError for both, which _check_decryption handles.
            raise pdfacts.DamagedFileError(detail="damaged in the test")
        # The result check (#3): a written file PDFium still reads as
        # encrypted.
        return FakeDoc()

    monkeypatch.setattr("pdfacts.edit.session.open", open_or_damaged)
    data = damaged(encrypted(PLAIN))
    with pytest.raises(pdfacts.DecryptionFailedError, match="PDFium reads the written file"):
        EditSession(data).add(Rewrite()).add(Decrypt()).apply()


def test_a_damaged_decrypt_result_with_other_pages_refused(monkeypatch):
    """The written file must have as many pages as QPDF wrote for it: a
    result whose pages differ is refused (the damaged path's page check)."""

    class FakeDoc:
        warnings: tuple[str, ...] = ()
        page_count = 0  # QPDF wrote one page; the result claims none
        object_count = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            pass

        def _access(self):
            import contextlib

            @contextlib.contextmanager
            def access():
                class A:
                    security_handler_revision = staticmethod(lambda: None)

                yield A()

            return access()

    calls = []

    def open_or_damaged(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise pdfacts.DamagedFileError(detail="damaged in the test")
        return FakeDoc()

    monkeypatch.setattr("pdfacts.edit.session.open", open_or_damaged)
    data = damaged(encrypted(PLAIN))
    with pytest.raises(
        pdfacts.DecryptionFailedError, match="the written file has 0 pages, QPDF wrote 1"
    ):
        EditSession(data).add(Rewrite()).add(Decrypt()).apply()
