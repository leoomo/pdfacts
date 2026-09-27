"""The errors that pdfacts raises.

Every error is a PdfactsError. The attributes of an error are given as
keywords and are available under the same names; the message is built from
them. MemoryError is never caught and never wrapped.
"""

from __future__ import annotations

from typing import Any

from pdfacts.core.capabilities import Capability
from pdfacts.core.values import Unknown

# PDFium accepts up to 1024 bytes before "%PDF-", and refuses 1025 (measured);
# the header is looked for in the first 1024 + len(b"%PDF-") bytes.
HEADER_SEARCH_BYTES = 1029


def _with_detail(text: str, detail: str) -> str:
    return f"{text}: {detail}" if detail else text


def _rebuild(cls: type[PdfactsError], message: str) -> PdfactsError:
    error = cls.__new__(cls)
    Exception.__init__(error, message)
    return error


class PdfactsError(Exception):
    """Base class of every error pdfacts raises."""

    def __init__(self, message: str, **fields: Any) -> None:
        super().__init__(message)
        for name, value in fields.items():
            setattr(self, name, value)

    def __reduce__(self) -> tuple[Any, ...]:
        # The attributes are keyword-only, which the default pickling cannot
        # pass back to __init__. The message is passed, and every attribute,
        # including notes added with add_note(), travels as state.
        return (_rebuild, (type(self), str(self)), dict(vars(self)))


class InputError(PdfactsError):
    """The bytes are not a PDF file that can be read as it is."""


class EmptyInputError(InputError):
    def __init__(self) -> None:
        super().__init__("the input is empty: 0 bytes")


class NotPdfError(InputError):
    def __init__(self) -> None:
        super().__init__(
            f"not a PDF file: no %PDF- header in the first {HEADER_SEARCH_BYTES} bytes"
        )


class DamagedFileError(InputError):
    detail: str

    def __init__(self, *, detail: str) -> None:
        super().__init__(
            _with_detail("the file has a PDF header but cannot be read", detail), detail=detail
        )


class PasswordRequiredError(PdfactsError):
    password_was_given: bool

    def __init__(self, *, password_was_given: bool) -> None:
        message = "the password given is wrong" if password_was_given else "a password is needed"
        super().__init__(message, password_was_given=password_was_given)


class LimitError(PdfactsError):
    """A limit set by the caller was exceeded or could not be checked."""

    limit: str
    allowed: int | float

    def __init__(self, message: str, *, limit: str, allowed: int | float, **fields: Any) -> None:
        super().__init__(message, limit=limit, allowed=allowed, **fields)


class LimitExceededError(LimitError):
    found: int | float

    def __init__(self, *, limit: str, allowed: int | float, found: int | float) -> None:
        super().__init__(
            f"limit {limit} exceeded: {found} found, {allowed} allowed",
            limit=limit,
            allowed=allowed,
            found=found,
        )


class LimitUncheckedError(LimitError):
    """A limit was set and could not be checked; it does not count as passed."""

    reason: Unknown

    def __init__(self, *, limit: str, allowed: int | float, reason: Unknown) -> None:
        if not isinstance(reason, Unknown):
            raise TypeError(f"reason must be an Unknown, not {type(reason).__name__}")
        super().__init__(
            f"limit {limit} ({allowed} allowed) could not be checked: "
            + _with_detail(f"the value is unknown ({reason.reason})", reason.detail),
            limit=limit,
            allowed=allowed,
            reason=reason,
        )


class RecoveryFailedError(PdfactsError):
    detail: str

    def __init__(self, *, detail: str) -> None:
        super().__init__(_with_detail("the file could not be recovered", detail), detail=detail)


class UnsupportedError(PdfactsError):
    capability: Capability

    def __init__(self, *, capability: Capability | str) -> None:
        # Capability(...) accepts a member or its value and rejects anything else.
        member = Capability(capability)
        super().__init__(f"this version of pdfacts does not support {member}", capability=member)


class ClosedError(PdfactsError):
    def __init__(self) -> None:
        super().__init__("the document or page was used after it was closed")


class ConfigurationError(PdfactsError):
    """The runtime was configured too late, or twice with different values."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EngineError(PdfactsError):
    """An engine failed in a way that none of the other errors describes."""

    engine: str
    detail: str

    def __init__(self, *, engine: str, detail: str) -> None:
        super().__init__(_with_detail(f"{engine} failed", detail), engine=engine, detail=detail)
