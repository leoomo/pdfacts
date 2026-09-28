"""recover(): the explicit step for a damaged file.

open() reports a file the engine cannot load as it is written as damaged and
changes nothing (docs/conventions.md, "Damaged files"). recover() has QPDF
rebuild the file, writes it anew and checks the result; the caller opens
those bytes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pdfacts.backends import qpdf
from pdfacts.backends.protocols import EngineFailure, FailureKind, RecoveredBytes
from pdfacts.core import runtime
from pdfacts.core.document import _deadline, classified, open
from pdfacts.core.errors import (
    EngineError,
    InputError,
    LimitExceededError,
    PasswordRequiredError,
    RecoveryFailedError,
)
from pdfacts.core.limits import Limits
from pdfacts.core.values import Unknown


@dataclass(frozen=True, slots=True, kw_only=True)
class Recovery:
    """A damaged file rebuilt, written anew and checked."""

    # The recovered file.
    data: bytes = field(repr=False)
    # Pages in the recovered file.
    page_count: int
    # What QPDF reported while it read the damaged file.
    warnings: tuple[str, ...]


def recover(
    data: bytes,
    *,
    password: str | None = None,
    limits: Limits | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> Recovery:
    """The file QPDF rebuilds from `data`, written anew and checked.

    The input is classified as open() classifies it: EmptyInputError,
    LimitExceededError for max_bytes, NotPdfError; it need not end with
    %%EOF. PasswordRequiredError when a password is needed or wrong. An intact
    file is rewritten as well, with no warnings unless QPDF reports a defect
    in it as written.

    RecoveryFailedError when QPDF cannot rebuild the file, and when the result
    fails a check: it has to open as open() opens a file, with both engines
    agreeing on its pages, without a warning from QPDF, and with /Size in its
    trailer. max_pages and max_objects are checked on the result.
    deadline_seconds counts from the start of recover(); it is checked once
    the file is rebuilt and once the result is checked. EngineError when an
    engine fails otherwise.

    The file is read up to the end of its last whole revision: the last
    startxref outside the data of a stream that QPDF reads through, whose
    offset, counted from the header, names a cross-reference section after
    the revision before and which QPDF, recovery off, reads; a linearized
    file's first-page section is no revision. What follows, a damaged end,
    is left out. Where part of a revision follows, an object, a
    cross-reference table or a trailer, the last revision is unfinished;
    where QPDF cannot read the last revision after another, its startxref
    leads nowhere. Either is RecoveryFailedError, since QPDF would read the
    revision before without a word, or, rebuilding the file from its
    objects, mix the two; it places objects in object streams only through
    a cross-reference stream, which an unfinished update lacks. A file
    without a whole revision, one cut short, is rebuilt from its objects,
    and an object cut short is lost. QPDF cannot rebuild an encrypted file
    whose objects are in object streams. A file that is rebuilt gets a
    comment at its end, longer than QPDF's search for startxref, so that
    QPDF rebuilds it without reading a startxref first and keeps an object
    that only white space follows. Its warnings count the comment: that QPDF
    cannot find startxref, that startxref was more than 1024 bytes before
    the end of the file, and the offsets of the end of the file.

    Pages, and objects a page uses, can be lost without a failure: QPDF has
    repaired the page tree before anything can be read, so neither engine
    tells how many pages the damaged file was meant to have. A caller who
    knows how many to expect compares that number with page_count; the
    warnings may name what was lost. QPDF may repair the page tree while it
    walks it, for example give a page without a media box US letter; the
    repair is written into the result and named among the warnings. The
    object numbers of the result differ from those of the input.

    The metadata keeps its content, as every stream does, though QPDF may
    encode a stream otherwise. An encrypted file stays encrypted, and
    its result differs from run to run; an unencrypted result is the same
    for the same input. When QPDF finds no trailer, it does not know that
    the file is encrypted: if the encryption dictionary is left,
    RecoveryFailedError, also for an unencrypted file that holds one nothing
    names; if the cut took that too, nothing tells, and the result is
    unencrypted with its strings and streams still encrypted
    (docs/conventions.md, "Damaged files").

    `_clock` is for tests.
    """
    started = _clock()
    limits = classified(data, password, limits)
    deadline = _deadline(limits.deadline_seconds, started, _clock)
    # The runtime starts in open(), before PDFium sees the result.
    with runtime.engine():
        try:
            rewritten = qpdf.rewrite_recovered(data, password)
        except EngineFailure as failure:
            raise _recovery_error(failure, password) from failure
    deadline()
    if limits.max_pages is not None and rewritten.page_count > limits.max_pages:
        raise LimitExceededError(
            limit="max_pages", allowed=limits.max_pages, found=rewritten.page_count
        )
    object_count = _verified(rewritten, password)
    deadline()
    if limits.max_objects is not None and object_count > limits.max_objects:
        raise LimitExceededError(
            limit="max_objects", allowed=limits.max_objects, found=object_count
        )
    return Recovery(
        data=rewritten.data, page_count=rewritten.page_count, warnings=rewritten.warnings
    )


def _verified(rewritten: RecoveredBytes, password: str | None) -> int:
    """The object count of the result, once it has passed the checks.

    open() has PDFium open the result as it is written, with a page
    dictionary for every page, and QPDF with recovery off; it compares their
    page counts. Where QPDF cannot open the result or the counts differ, it
    says so among the warnings.
    """
    try:
        document = open(rewritten.data, password=password)
    except (InputError, PasswordRequiredError) as error:
        # An EngineError passes: it says nothing about the result.
        raise RecoveryFailedError(detail=f"the recovered file does not open: {error}") from error
    with document:
        if document.warnings:
            raise RecoveryFailedError(
                detail=f"the recovered file gives warnings: {'; '.join(document.warnings)}"
            )
        if document.page_count != rewritten.page_count:
            raise RecoveryFailedError(
                detail=f"the recovered file has {document.page_count} pages, "
                f"the rebuilt document {rewritten.page_count}"
            )
        object_count = document.object_count
        if isinstance(object_count, Unknown):
            raise RecoveryFailedError(
                detail=f"the recovered file has no usable /Size: {object_count.detail}"
            )
        return object_count


def _recovery_error(failure: EngineFailure, password: str | None) -> Exception:
    if failure.kind is FailureKind.PASSWORD:
        return PasswordRequiredError(password_was_given=password is not None)
    if failure.kind is FailureKind.FORMAT:
        return RecoveryFailedError(detail=f"QPDF cannot rebuild the file: {failure.detail}")
    return EngineError(engine="qpdf", detail=failure.detail)
