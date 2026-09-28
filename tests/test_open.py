"""open(): classification of the input, the first limits, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import pdfacts
from pdfacts import (
    ClosedError,
    DamagedFileError,
    Document,
    EmptyInputError,
    EngineError,
    LimitExceededError,
    Limits,
    NotPdfError,
    PasswordRequiredError,
)
from pdfacts.backends import pdfium
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.core import runtime
from synthetic import cases

# PDFium accepts at most this many bytes before the header (measured).
JUNK_ACCEPTED = 1024


def lock_is_held() -> bool:
    """Whether another thread would have to wait for the engine lock."""
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


def test_an_empty_input():
    with pytest.raises(EmptyInputError):
        pdfacts.open(b"")


def test_a_header_and_nothing_else_is_a_damaged_file():
    with pytest.raises(DamagedFileError):
        pdfacts.open(cases.header_only().data)


def test_text_without_a_header_is_not_a_pdf():
    with pytest.raises(NotPdfError):
        pdfacts.open(cases.garbage().data)


def test_garbage_after_a_header_is_a_damaged_file():
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.garbage_after_header().data)
    assert raised.value.detail


def test_a_header_after_1024_bytes_opens():
    case = cases.three_pages()
    document = pdfacts.open(b"x" * JUNK_ACCEPTED + case.data)
    assert document.page_count == len(case.page_objects)


def test_a_header_after_1025_bytes_is_not_a_pdf():
    with pytest.raises(NotPdfError):
        pdfacts.open(b"x" * (JUNK_ACCEPTED + 1) + cases.three_pages().data)


@pytest.mark.parametrize("name", list(cases.TRUNCATED))
def test_a_truncated_file_is_a_damaged_file_never_an_empty_document(name):
    with pytest.raises(DamagedFileError):
        pdfacts.open(cases.TRUNCATED[name]().data)


def test_a_user_password_that_is_not_given():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.open(cases.user_password().data)
    assert raised.value.password_was_given is False


def test_a_user_password_that_is_wrong():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.open(cases.user_password().data, password="wrong")
    assert raised.value.password_was_given is True


def test_the_right_user_password():
    case = cases.user_password()
    assert pdfacts.open(case.data, password=case.user_password).page_count == case.pages_built


def test_a_file_with_only_an_owner_password_opens_without_one():
    case = cases.owner_password_only()
    assert pdfacts.open(case.data).page_count == case.pages_built


@pytest.mark.parametrize(
    "data",
    [cases.plain_page().data.decode("latin-1"), bytearray(cases.plain_page().data), Path("x.pdf")],
    ids=["str", "bytearray", "path"],
)
def test_the_input_has_to_be_bytes(data):
    with pytest.raises(TypeError, match="bytes"):
        pdfacts.open(data)  # type: ignore[arg-type]


@pytest.mark.parametrize("password", [b"user", 1])
def test_a_password_has_to_be_text(password):
    with pytest.raises(TypeError, match="password"):
        pdfacts.open(cases.user_password().data, password=password)  # type: ignore[arg-type]


@pytest.mark.parametrize("limits", [{"max_bytes": 10}, 10])
def test_limits_have_to_be_a_limits_object(limits):
    with pytest.raises(TypeError, match="limits"):
        pdfacts.open(cases.plain_page().data, limits=limits)  # type: ignore[arg-type]


def test_more_bytes_than_allowed_are_refused_before_any_engine_is_called(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("an engine was called")

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", must_not_run)
    monkeypatch.setattr(runtime, "start", must_not_run)
    data = cases.plain_page().data
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.open(data, limits=Limits(max_bytes=len(data) - 1))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_bytes",
        len(data) - 1,
        len(data),
    )


def test_as_many_bytes_as_allowed_open():
    data = cases.plain_page().data
    assert pdfacts.open(data, limits=Limits(max_bytes=len(data))).page_count == 1


def test_the_size_is_checked_before_the_header():
    data = cases.garbage().data
    with pytest.raises(LimitExceededError):
        pdfacts.open(data, limits=Limits(max_bytes=len(data) - 1))


def test_an_empty_input_is_empty_whatever_the_limits():
    with pytest.raises(EmptyInputError):
        pdfacts.open(b"", limits=Limits(max_bytes=1))


def test_more_pages_than_allowed_are_refused_and_the_document_is_closed(monkeypatch):
    closed = []
    original = pdfium.PdfiumDocument.close

    def recording(self):
        closed.append(self)
        return original(self)

    monkeypatch.setattr(pdfium.PdfiumDocument, "close", recording)
    case = cases.three_pages()
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.open(case.data, limits=Limits(max_pages=2))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == ("max_pages", 2, 3)
    assert len(closed) == 1


def test_as_many_pages_as_allowed_open():
    assert pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=3)).page_count == 3


def test_close_twice(monkeypatch):
    closed = []
    original = pdfium.PdfiumDocument.close

    def recording(self):
        closed.append(self)
        return original(self)

    monkeypatch.setattr(pdfium.PdfiumDocument, "close", recording)
    document = pdfacts.open(cases.plain_page().data)
    document.close()
    document.close()
    assert document.closed is True
    assert len(closed) == 1


def test_a_closed_document_cannot_be_used():
    document = pdfacts.open(cases.plain_page().data)
    assert document.closed is False
    document.close()
    with pytest.raises(ClosedError):
        _ = document.page_count


def test_the_context_manager_closes():
    with pdfacts.open(cases.plain_page().data) as document:
        assert isinstance(document, Document)
        assert document.closed is False
    assert document.closed is True


def test_the_context_manager_closes_when_the_block_raised():
    with pytest.raises(RuntimeError, match="in the block"):
        with pdfacts.open(cases.plain_page().data) as document:
            raise RuntimeError("in the block")
    assert document.closed is True


def test_memory_error_arrives_unchanged(monkeypatch):
    error = MemoryError()

    def raising(*args, **kwargs):
        raise error

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", raising)
    with pytest.raises(MemoryError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert raised.value is error


def test_another_engine_failure_is_an_engine_error(monkeypatch):
    def raising(*args, **kwargs):
        raise EngineFailure(FailureKind.OTHER, "inside the engine")

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", raising)
    with pytest.raises(EngineError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert (raised.value.engine, raised.value.detail) == ("pdfium", "inside the engine")


def test_the_runtime_is_started_before_the_engine_opens_the_file(monkeypatch):
    order = []
    original = pdfium.PdfiumDocument.open.__func__

    monkeypatch.setattr(runtime, "start", lambda: order.append("start"))

    def recording(cls, data, password):
        order.append("open")
        return original(cls, data, password)

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", classmethod(recording))
    pdfacts.open(cases.plain_page().data)
    assert order == ["start", "open"]


def test_open_starts_the_runtime():
    # The font policy is state of the process: a fresh one shows it.
    code = f"""
import json, sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from synthetic import cases
import pdfacts
from pdfacts.core import runtime
before = runtime.active_font_policy()
pdfacts.open(cases.plain_page().data)
print(json.dumps({{"before": before, "after": runtime.active_font_policy()}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "before": None,
        "after": "embedded-only",
    }


def test_every_engine_call_holds_the_engine_lock(monkeypatch):
    held = {}
    cls = pdfium.PdfiumDocument
    original_open, original_count, original_close = cls.open.__func__, cls.page_count, cls.close

    def watching_open(klass, data, password):
        held["open"] = lock_is_held()
        return original_open(klass, data, password)

    def watching_count(self):
        held.setdefault("page_count", lock_is_held())
        return original_count(self)

    def watching_close(self):
        held["close"] = lock_is_held()
        return original_close(self)

    monkeypatch.setattr(cls, "open", classmethod(watching_open))
    monkeypatch.setattr(cls, "page_count", watching_count)
    monkeypatch.setattr(cls, "close", watching_close)
    document = pdfacts.open(cases.plain_page().data, limits=Limits(max_pages=5))
    assert document.page_count == 1
    document.close()
    assert held == {"open": True, "page_count": True, "close": True}


def test_open_and_document_are_exported():
    assert {"open", "Document", "Limits"} <= set(pdfacts.__all__)
