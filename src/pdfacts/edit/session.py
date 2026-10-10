"""The edit session (docs/architecture.md, rules 3 and 4; M5b).

A change is expressed as an operation; an edit session collects operations
and applies them in one step, reopening the result and checking every
operation against its expected outcome. recover() and decrypt() stay as
they are (M5a): each is one operation applied alone, and what they do is
expressible here beside them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pdfacts.backends import qpdf
from pdfacts.backends.protocols import EngineFailure
from pdfacts.core import runtime
from pdfacts.core.decryption import _check_encrypted, _verify
from pdfacts.core.document import _deadline, classified, open
from pdfacts.core.errors import (
    DamagedFileError,
    DecryptionFailedError,
    LimitExceededError,
)
from pdfacts.core.limits import Limits
from pdfacts.core.recovery import _recovery_error, _verified
from pdfacts.core.values import Unknown

__all__ = ["Decrypt", "EditSession", "Edited", "Rewrite"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Edited:
    """A file written by the edit session, checked against its operations."""

    # The written file.
    data: bytes = field(repr=False)
    # Pages in the written file.
    page_count: int
    # What QPDF reported while it read and wrote.
    warnings: tuple[str, ...]


class EditSession:
    """Operations on one file, applied in one write when apply() is called.

    The input is classified as open() classifies it, with its errors and
    its limits, when apply() is called. The operations are applied in the
    order they were given; each is checked against its expected outcome
    before the written file is returned, and a file that failed a check is
    never returned. `Rewrite` alone is recover()'s write and check;
    `Decrypt` alone is decrypt()'s; the two in one session rebuild a
    damaged encrypted file and decrypt it in one write (measured: one QPDF
    write equals the two saves one after the other, byte for byte).
    """

    def __init__(self, data: bytes, *, password: str | None = None) -> None:
        if not isinstance(data, bytes):
            raise TypeError(f"data must be bytes, not {type(data).__name__}")
        self._data = data
        self._password = password
        self._operations: list[Rewrite | Decrypt] = []

    def add(self, operation: Rewrite | Decrypt) -> EditSession:
        """One more operation, applied after those given before it."""
        if not isinstance(operation, (Rewrite, Decrypt)):
            raise TypeError(
                f"an operation is a Rewrite or a Decrypt, not {type(operation).__name__}"
            )
        self._operations.append(operation)
        return self

    def apply(
        self,
        *,
        limits: Limits | None = None,
        _clock: Callable[[], float] = time.monotonic,
    ) -> Edited:
        """Write the file with every operation applied, and check each.

        The errors are the operations' own, in their order: for `Rewrite`
        RecoveryFailedError, for `Decrypt` NotEncryptedError (from pdfacts)
        and DecryptionFailedError; LimitExceededError for max_bytes on the
        input and max_pages and max_objects on the result; EngineError when
        an engine fails otherwise. deadline_seconds counts from the start of
        apply(), checked once the file is written and after each operation's
        check. An empty session writes the file anew as it is, checked as
        `Rewrite` checks it.
        """
        if not self._operations:
            self._operations.append(Rewrite())
        started = _clock()
        limits = classified(self._data, self._password, limits)
        deadline = _deadline(limits.deadline_seconds, started, _clock)
        decrypting = any(isinstance(operation, Decrypt) for operation in self._operations)
        if decrypting:
            self._check_decrypt_input()
        # The runtime starts before PDFium sees the result.
        with runtime.engine():
            try:
                written = qpdf.rewrite_written(self._data, self._password, decrypting)
            except EngineFailure as failure:
                raise self._failure(failure) from failure
        deadline()
        if limits.max_pages is not None and written.page_count > limits.max_pages:
            raise LimitExceededError(
                limit="max_pages", allowed=limits.max_pages, found=written.page_count
            )
        if decrypting:
            deadline()
            self._check_decryption(written, limits, deadline)
        else:
            self._check_rewrite(written, limits, deadline)
        return Edited(data=written.data, page_count=written.page_count, warnings=written.warnings)

    def _failure(self, failure: EngineFailure) -> Exception:
        decrypting = any(isinstance(operation, Decrypt) for operation in self._operations)
        from pdfacts.core.decryption import _decryption_error

        if decrypting:
            return _decryption_error(failure)
        return _recovery_error(failure, self._password)

    def _check_decrypt_input(self) -> DamagedFileError | None:
        """The Decrypt operation's reading of its input, as decrypt() reads
        it: a password that does not open it and a file that is not
        encrypted are refused here, before anything is written. A damaged
        input is refused too — unless a Rewrite stands beside the Decrypt,
        which is there to rebuild it; the DamagedFileError is returned then,
        and the checks after the write take its shape into account."""
        try:
            source = open(self._data, password=self._password)
        except DamagedFileError as error:
            if any(isinstance(operation, Rewrite) for operation in self._operations):
                return error
            raise
        with source:
            _check_encrypted(source)
        return None

    def _check_rewrite(self, written, limits: Limits, deadline: Callable[[], None]) -> None:
        object_count = _verified(written, self._password)
        deadline()
        if limits.max_objects is not None and object_count > limits.max_objects:
            raise LimitExceededError(
                limit="max_objects", allowed=limits.max_objects, found=object_count
            )

    def _check_decryption(self, written, limits: Limits, deadline: Callable[[], None]) -> None:
        try:
            source = open(self._data, password=self._password)
        except DamagedFileError:
            # The session holds a Rewrite beside the Decrypt: the input may
            # be damaged, which is why it is rebuilt. Against an input that
            # opens, decrypt()'s checks stand whole; against one that does
            # not, the pages cannot be compared with what no engine reads as
            # written, and the check falls to what the written file itself
            # must be: open, not encrypted, with /Size and no warning, its
            # pages as many as QPDF wrote for it (rule 4: the outcome the
            # operation names, not more than the input can tell).
            self._check_written_alone(written)
            return
        with source:
            _verify(source, written.data, limits, deadline)

    def _check_written_alone(self, written) -> None:
        result = open(written.data)
        try:
            if result.warnings:
                raise DecryptionFailedError(
                    detail=f"the written file gives warnings: {'; '.join(result.warnings)}"
                )
            if isinstance(result.object_count, Unknown):
                raise DecryptionFailedError(
                    detail=f"the written file has no usable /Size: {result.object_count.detail}"
                )
            if result.page_count != written.page_count:
                raise DecryptionFailedError(
                    detail=f"the written file has {result.page_count} pages, "
                    f"QPDF wrote {written.page_count}"
                )
            with result._access() as access:
                if access.security_handler_revision() is not None:
                    raise DecryptionFailedError(detail="PDFium reads the written file as encrypted")
        finally:
            result.close()


@dataclass(frozen=True, slots=True)
class Rewrite:
    """QPDF rebuilds the file and writes it anew (recover()'s operation)."""


@dataclass(frozen=True, slots=True)
class Decrypt:
    """QPDF writes the file anew without its encryption (decrypt()'s operation)."""
