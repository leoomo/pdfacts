"""open(): classification of the input, the first limits, closing.

Inputs are the constructed cases of tests/synthetic/cases.py; expected values
are the numbers the cases were built from.
"""

from __future__ import annotations

import contextlib
import ctypes
import gc
import json
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pypdfium2.raw
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
from pdfacts.core import document as document_module
from pdfacts.core import runtime
from pdfium_handles import watch
from synthetic import builder, cases

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


@pytest.mark.parametrize("name", list(cases.BAD_ENDS))
def test_a_file_not_ending_in_the_end_marker_is_damaged_before_any_engine(monkeypatch, name):
    def must_not_run(*args, **kwargs):
        raise AssertionError("an engine was called")

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", must_not_run)
    monkeypatch.setattr(runtime, "start", must_not_run)
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.BAD_ENDS[name]().data)
    assert "%%EOF" in raised.value.detail


def test_white_space_after_the_end_marker_is_fine():
    assert pdfacts.open(cases.white_space_after_the_end().data).page_count == 1


def test_a_valid_incremental_update_opens():
    assert pdfacts.open(cases.incremental_update().data).page_count == 1


def last_startxref(data: bytes, replace: Callable[[bytes, bytes, bytes], bytes]) -> bytes:
    """`data` with its last "startxref, offset, %%EOF" rewritten by `replace`.

    `replace` gets the white space before the offset, the offset and what
    follows it, and returns the new text from startxref to the end.
    """
    at = data.rfind(b"startxref")
    found = re.fullmatch(rb"startxref(\s+)(\d+)(\s+%%EOF\s*)", data[at:])
    assert found, data[at:]
    return data[:at] + replace(*found.groups())


def one_page() -> bytes:
    return builder.document([builder.PageSpec(b"/MediaBox [0 0 400 300]")])


@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(
            lambda space, offset, end: b"startxref" + offset + end, id="glued to its offset"
        ),
        pytest.param(lambda space, offset, end: b"startxref" + end, id="without an offset"),
    ],
)
def test_a_last_startxref_that_the_engines_pass_over_is_damaged(replace):
    data = last_startxref(cases.incremental_update().data, replace)
    with pytest.raises(DamagedFileError):
        pdfacts.open(data)


def test_a_last_startxref_with_other_characters_after_its_offset_stays_damaged():
    data = last_startxref(
        cases.incremental_update().data,
        lambda space, offset, end: b"startxref" + space + offset + b"abc" + end,
    )
    with pytest.raises(DamagedFileError):
        pdfacts.open(data)


@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(lambda space, offset, end: b"startxref % c\n" + offset + end, id="a comment"),
        pytest.param(
            lambda space, offset, end: b"startxref" + space + b"+" + offset + end,
            id="a plus sign",
        ),
    ],
)
def test_a_last_startxref_that_qpdf_reads_through_still_opens(replace):
    data = last_startxref(cases.incremental_update().data, replace)
    with pdfacts.open(data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == cases.UPDATED_MEDIA_BOX


@pytest.mark.parametrize(
    "part",
    [b"trailer\n<< >>\n%%EOF\n", b"xref\n0 1\n%%EOF\n", b"5 0 obj\n<< >>\nendobj\n%%EOF\n"],
    ids=["a trailer", "a cross-reference section", "an object"],
)
def test_an_unfinished_update_after_the_last_revision_is_damaged(part):
    with pytest.raises(DamagedFileError):
        pdfacts.open(one_page() + part)


def test_words_alone_after_the_last_revision_are_no_revision():
    assert pdfacts.open(one_page() + b"obj stream trailer\n%%EOF\n").page_count == 1


def test_a_comment_that_looks_like_an_object_after_the_last_revision_is_damaged():
    with pytest.raises(DamagedFileError):
        pdfacts.open(one_page() + b"% 5 0 obj\n%%EOF\n")


def test_an_incremental_update_opens_with_the_page_of_the_update():
    with pdfacts.open(cases.incremental_update().data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == cases.UPDATED_MEDIA_BOX


def test_a_linearized_file_still_opens():
    assert pdfacts.open(cases.linearized().data).page_count == 1


def test_part_of_a_revision_after_the_last_is_refused_before_any_engine(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("an engine was called")

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", must_not_run)
    monkeypatch.setattr(runtime, "start", must_not_run)
    glued = last_startxref(
        cases.incremental_update().data, lambda space, offset, end: b"startxref" + offset + end
    )
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(glued)
    assert "last whole revision" in raised.value.detail


@pytest.mark.parametrize(
    "replace",
    [
        pytest.param(
            lambda space, offset, end: b"startxref" + offset + end, id="glued to its offset"
        ),
        pytest.param(lambda space, offset, end: b"startxref" + end, id="without an offset"),
    ],
)
def test_a_file_without_a_whole_revision_is_left_to_the_engines(replace):
    data = last_startxref(one_page(), replace)
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(data)
    assert "last whole revision" not in raised.value.detail


def test_a_linearized_file_with_two_objects_before_its_first_page_table_is_damaged():
    # A known gap (issue #32, decision 4): the first-page trailer is found
    # only with at most one object before the first-page table.
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.linearized(first_page_offset=True, between=2).data)
    assert "last whole revision" in raised.value.detail


def tables(case: cases.Updated) -> tuple[int, int]:
    """The offsets of the cross-reference tables of `case.source` and of the update."""
    (first,) = re.findall(rb"startxref\n([0-9]+)\n", case.source.data)
    last = re.findall(rb"startxref\n([0-9]+)\n", case.data)[-1]
    return int(first), int(last)


def startxref(offset: int) -> bytes:
    return b"startxref\n%d\n%%%%EOF\n" % offset


STALE: dict[str, Callable[[int, int], bytes]] = {
    "after the update": lambda first, last: startxref(first),
    "in a comment": lambda first, last: b"%% startxref %d\n%%%%EOF\n" % first,
    "in a comment after a startxref without an offset": (
        lambda first, last: b"startxref\n%% startxref %d\n%%%%EOF\n" % first
    ),
    "after one naming the update": lambda first, last: startxref(last) + startxref(first),
    "before one without an offset": lambda first, last: startxref(first) + b"startxref\n%%EOF\n",
    "before one of 21 digits": lambda first, last: startxref(first) + startxref(10**20),
    "with a sign and thirty leading zeros": (
        lambda first, last: b"startxref\n+%s%d\n%%%%EOF\n" % (b"0" * 30, first)
    ),
}


def opens_as_the_update(data: bytes) -> None:
    with pdfacts.open(data) as document:
        box = document.pages[0].geometry.visible_box
    assert (box.x0, box.y0, box.x1, box.y1) == cases.UPDATED_MEDIA_BOX


@pytest.mark.parametrize("name", list(STALE))
def test_a_startxref_that_names_an_earlier_table_is_damaged(name):
    first, last = tables(cases.incremental_update())
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.incremental_update().data + STALE[name](first, last))
    assert "last whole revision" in raised.value.detail


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(lambda first, last: startxref(last), id="the last table again"),
        pytest.param(lambda first, last: startxref(last - 1), id="the same, one byte early"),
        pytest.param(
            lambda first, last: startxref(first) + startxref(last),
            id="the first table, then the last",
        ),
        pytest.param(
            lambda first, last: b"%% startxref %d\n%%%%EOF\n" % first + startxref(last),
            id="the first table in a comment, then the last",
        ),
    ],
)
def test_a_startxref_that_names_the_last_table_again_still_opens(tail):
    first, last = tables(cases.incremental_update())
    opens_as_the_update(cases.incremental_update().data + tail(first, last))


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(lambda first, last: b"startxref\n%%EOF\n", id="without an offset"),
        pytest.param(
            lambda first, last: b"startxref%d\n%%%%EOF\n" % first, id="glued to its offset"
        ),
    ],
)
def test_a_startxref_the_engines_pass_over_after_the_last_revision_still_opens(tail):
    first, last = tables(cases.incremental_update())
    opens_as_the_update(cases.incremental_update().data + tail(first, last))


@pytest.mark.parametrize(
    "offset",
    [0, None, 2**63 - 64, 2**63, 10**20 - 1],
    ids=["the header", "past the end", "2**63 - 64", "2**63", "10**20 - 1"],
)
def test_a_startxref_that_names_no_section_is_left_to_the_engines(offset):
    case = cases.incremental_update()
    at = 2 * len(case.data) if offset is None else offset
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(case.data + startxref(at))
    assert "last whole revision" not in raised.value.detail


def test_a_startxref_that_names_an_earlier_table_is_refused_before_any_engine(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("an engine was called")

    monkeypatch.setattr(pdfium.PdfiumDocument, "open", must_not_run)
    monkeypatch.setattr(runtime, "start", must_not_run)
    first, last = tables(cases.incremental_update())
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.incremental_update().data + startxref(first))
    assert "last whole revision" in raised.value.detail


def test_one_revision_with_its_startxref_repeated_still_opens():
    (t,) = re.findall(rb"startxref\n([0-9]+)\n", one_page())
    assert pdfacts.open(one_page() + startxref(int(t))).page_count == 1


def test_a_stale_startxref_after_bytes_before_the_header_is_damaged():
    first, last = tables(cases.incremental_update())
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(b"x" * 100 + cases.incremental_update().data + startxref(first))
    assert "last whole revision" in raised.value.detail


def test_the_last_table_named_again_after_bytes_before_the_header_still_opens():
    first, last = tables(cases.incremental_update())
    opens_as_the_update(b"x" * 100 + cases.incremental_update().data + startxref(last))


def test_a_startxref_that_names_an_object_is_damaged():
    case = cases.incremental_update()
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(case.data + startxref(case.data.index(b"1 0 obj")))
    assert "last whole revision" in raised.value.detail


@pytest.mark.parametrize("name", list(cases.REBUILT))
def test_a_file_pdfium_reads_only_by_rebuilding_its_table_is_damaged(name):
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.REBUILT[name]().data)
    assert "rebuilding" in raised.value.detail


@pytest.mark.parametrize("name", list(cases.NO_PAGES))
def test_a_file_without_pages_is_damaged(name):
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.NO_PAGES[name]().data)
    assert "no page" in raised.value.detail


@pytest.mark.parametrize("name", list(cases.MISSING_PAGES))
def test_a_file_with_a_page_pdfium_counts_but_cannot_find_is_damaged(monkeypatch, name):
    closed = recording_closes(monkeypatch)
    with pytest.raises(DamagedFileError) as raised:
        pdfacts.open(cases.MISSING_PAGES[name]().data)
    assert "no page dictionary" in raised.value.detail
    assert len(closed) == 1


def test_an_exception_in_the_page_check_is_an_engine_error(monkeypatch):
    def raising(*args):
        raise ctypes.ArgumentError("wrong argument")

    monkeypatch.setattr(pypdfium2.raw, "FPDF_GetPageSizeByIndexF", raising)
    with pytest.raises(EngineError) as raised:
        pdfacts.open(cases.three_pages().data)
    assert raised.value.detail == "wrong argument"


def test_the_page_limit_is_checked_before_every_page_dictionary_is_read(monkeypatch):
    def must_not_run(self):
        raise AssertionError("every page dictionary was read")

    monkeypatch.setattr(pdfium.PdfiumDocument, "check_pages", must_not_run)
    with pytest.raises(LimitExceededError):
        pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=2))


def test_a_file_over_the_page_limit_and_with_a_page_missing_is_over_the_limit():
    data = cases.missing_kid_in_the_middle().data
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.open(data, limits=Limits(max_pages=2))
    assert raised.value.found == 3


def test_another_failure_of_the_page_check_is_an_engine_error(monkeypatch):
    def failing(self):
        raise EngineFailure(FailureKind.OTHER, "check failed")

    monkeypatch.setattr(pdfium.PdfiumDocument, "check_pages", failing)
    with pytest.raises(EngineError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert raised.value.detail == "check failed"


@pytest.mark.parametrize("password", ["user\x00junk", "\x00"])
def test_a_password_with_nul_is_refused(password):
    # PDFium would read the password only up to the NUL.
    with pytest.raises(ValueError, match="NUL"):
        pdfacts.open(cases.user_password().data, password=password)


def test_a_password_that_is_not_valid_text_is_refused():
    with pytest.raises(ValueError, match="UTF-8"):
        pdfacts.open(cases.user_password().data, password="user\ud800")


def test_a_file_that_ends_right_with_nothing_readable_inside_is_damaged():
    data = b"%PDF-1.7\n" + b"this is not a PDF object\n" * 40 + b"%%EOF\n"
    with pytest.raises(DamagedFileError):
        pdfacts.open(data)


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
    closed = recording_closes(monkeypatch)
    case = cases.three_pages()
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.open(case.data, limits=Limits(max_pages=2))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == ("max_pages", 2, 3)
    assert len(closed) == 1


def test_as_many_pages_as_allowed_open():
    assert pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=3)).page_count == 3


def test_close_twice(monkeypatch):
    closed = recording_closes(monkeypatch)
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


def test_a_closed_document_cannot_be_entered():
    document = pdfacts.open(cases.plain_page().data)
    document.close()
    with pytest.raises(ClosedError):
        with document:
            pass


def test_a_document_nobody_closed_is_closed_under_the_engine_lock(monkeypatch):
    # Only this test's handles count: pypdfium2 frees those that backend tests
    # left to the garbage collector without the lock, at any moment.
    handles = watch(monkeypatch, on_free=lock_is_held)
    held = handles.seen
    document = pdfacts.open(cases.plain_page().data)
    del document
    gc.collect()
    assert held == [True]


def test_the_finalizer_does_not_wait_for_the_lock_and_the_next_holder_closes(monkeypatch):
    handles = watch(monkeypatch, on_free=lock_is_held)
    held = handles.seen
    document = pdfacts.open(cases.plain_page().data)
    taken, release = threading.Event(), threading.Event()

    def holder():
        with runtime.engine_lock:
            taken.set()
            release.wait(timeout=30)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert taken.wait(timeout=10)
        del document
        gc.collect()
        # The finalizer returned without closing: the lock was taken.
        assert held == []
    finally:
        # A failure above must not keep the lock from the tests that follow.
        release.set()
        thread.join(timeout=10)
    # The next operation that takes the lock closes what was handed over.
    pdfacts.open(cases.plain_page().data).close()
    assert held == [True, True]


def test_the_interpreter_exits_while_another_thread_holds_the_lock():
    code = f"""
import sys, threading, time
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from synthetic import cases
import pdfacts
from pdfacts.core import runtime
document = pdfacts.open(cases.plain_page().data)
taken = threading.Event()
def hold():
    with runtime.engine_lock:
        taken.set()
        time.sleep(3600)
threading.Thread(target=hold, daemon=True).start()
taken.wait()
print("exiting")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "exiting"


def test_close_interrupted_while_waiting_for_the_lock_leaves_the_document_open(monkeypatch):
    document = pdfacts.open(cases.plain_page().data)

    @contextlib.contextmanager
    def interrupted():
        raise KeyboardInterrupt
        yield  # pragma: no cover

    monkeypatch.setattr(runtime, "engine", interrupted)
    with pytest.raises(KeyboardInterrupt):
        document.close()
    assert document.closed is False
    monkeypatch.undo()
    document.close()
    assert document.closed is True


def test_close_interrupted_while_the_engine_closes_is_closed_and_finished_later(monkeypatch):
    gc.collect()  # no document of an earlier test is closed while this one runs
    document = pdfacts.open(cases.plain_page().data)
    calls = []
    original = pdfium.PdfiumDocument.close

    def interrupted_once(self):
        calls.append(self)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return original(self)

    monkeypatch.setattr(pdfium.PdfiumDocument, "close", interrupted_once)
    with pytest.raises(KeyboardInterrupt):
        document.close()
    # No use from the start of close(), whatever happened during it.
    assert document.closed is True
    with pytest.raises(ClosedError):
        _ = document.page_count
    document.close()
    assert len(calls) == 2


def test_a_close_handed_over_just_as_the_lock_came_free_is_run_at_once(monkeypatch):
    class Lock:
        """Taken by someone else at the first attempt, free at the second."""

        def __init__(self):
            self.attempts = 0

        def acquire(self, blocking=True):
            self.attempts += 1
            return self.attempts > 1

        def release(self):
            pass

    closes = []
    monkeypatch.setattr(runtime, "engine_lock", Lock())
    runtime.close_soon(lambda: closes.append("closed"))
    assert closes == ["closed"]
    assert runtime._deferred == []


def test_a_failure_while_making_the_document_closes_the_engine_document(monkeypatch):
    closed = recording_closes(monkeypatch)
    error = MemoryError()

    def failing(*args, **kwargs):
        raise error

    monkeypatch.setattr(document_module, "Document", failing)
    with pytest.raises(MemoryError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert raised.value is error
    assert len(closed) == 1


def recording_closes(monkeypatch, *, raising: BaseException | None = None) -> list[object]:
    """Every close of a PDFium backend document; with `raising`, each close raises it.

    The garbage of earlier tests is collected first: its documents would be
    closed by their finalizers during this test and counted.
    """
    gc.collect()
    closed: list[object] = []
    original = pdfium.PdfiumDocument.close

    def recording(self):
        closed.append(self)
        if raising is not None:
            original(self)
            raise raising
        return original(self)

    monkeypatch.setattr(pdfium.PdfiumDocument, "close", recording)
    return closed


def test_an_interrupt_after_the_engine_opened_closes_the_document(monkeypatch):
    closed = recording_closes(monkeypatch)

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(pdfium.PdfiumDocument, "page_count", interrupted)
    with pytest.raises(KeyboardInterrupt):
        pdfacts.open(cases.plain_page().data)
    assert len(closed) == 1


def test_a_failing_close_does_not_hide_the_exceeded_limit(monkeypatch):
    recording_closes(monkeypatch, raising=EngineFailure(FailureKind.OTHER, "close failed"))
    with pytest.raises(LimitExceededError):
        pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=2))


def test_memory_error_while_closing_after_a_failure_is_not_swallowed(monkeypatch):
    error = MemoryError()
    recording_closes(monkeypatch, raising=error)
    with pytest.raises(MemoryError) as raised:
        pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=2))
    assert raised.value is error


def test_memory_error_while_counting_pages_arrives_unchanged_and_closes(monkeypatch):
    closed = recording_closes(monkeypatch)
    error = MemoryError()

    def raising(self):
        raise error

    monkeypatch.setattr(pdfium.PdfiumDocument, "page_count", raising)
    with pytest.raises(MemoryError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert raised.value is error
    assert len(closed) == 1


def test_an_engine_failure_while_counting_pages_is_an_engine_error(monkeypatch):
    def failing(self):
        raise EngineFailure(FailureKind.OTHER, "count failed")

    monkeypatch.setattr(pdfium.PdfiumDocument, "page_count", failing)
    with pytest.raises(EngineError) as raised:
        pdfacts.open(cases.plain_page().data)
    assert (raised.value.engine, raised.value.detail) == ("pdfium", "count failed")


def test_an_engine_failure_while_closing_is_an_engine_error_and_the_document_is_closed(
    monkeypatch,
):
    document = pdfacts.open(cases.plain_page().data)
    recording_closes(monkeypatch, raising=EngineFailure(FailureKind.OTHER, "close failed"))
    with pytest.raises(EngineError) as raised:
        document.close()
    assert raised.value.detail == "close failed"
    assert document.closed is True
    document.close()


def failing_engine_close(monkeypatch) -> None:
    """pypdfium2's close frees the handle and then raises something unexpected."""
    original = pypdfium2.PdfDocument.close

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("closing failed")

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", failing)


def test_an_unexpected_failure_of_the_engine_close_does_not_hide_the_limit(monkeypatch):
    failing_engine_close(monkeypatch)
    with pytest.raises(LimitExceededError):
        pdfacts.open(cases.three_pages().data, limits=Limits(max_pages=2))


def test_an_unexpected_failure_of_the_engine_close_is_an_engine_error(monkeypatch):
    document = pdfacts.open(cases.plain_page().data)
    failing_engine_close(monkeypatch)
    with pytest.raises(EngineError) as raised:
        document.close()
    assert raised.value.detail == "closing failed"
    assert document.closed is True
    document.close()


def test_an_empty_password_counts_as_given():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.open(cases.user_password().data, password="")
    assert raised.value.password_was_given is True


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
    original_check = cls.check_pages

    def watching_open(klass, data, password):
        held["open"] = lock_is_held()
        return original_open(klass, data, password)

    def watching_count(self):
        held.setdefault("page_count", lock_is_held())
        return original_count(self)

    def watching_check(self):
        held["check_pages"] = lock_is_held()
        return original_check(self)

    def watching_close(self):
        held["close"] = lock_is_held()
        return original_close(self)

    monkeypatch.setattr(cls, "open", classmethod(watching_open))
    monkeypatch.setattr(cls, "page_count", watching_count)
    monkeypatch.setattr(cls, "check_pages", watching_check)
    monkeypatch.setattr(cls, "close", watching_close)
    document = pdfacts.open(cases.plain_page().data, limits=Limits(max_pages=5))
    assert document.page_count == 1
    document.close()
    assert held == {"open": True, "page_count": True, "check_pages": True, "close": True}


def test_open_and_document_are_exported():
    assert {"open", "Document", "Limits"} <= set(pdfacts.__all__)
