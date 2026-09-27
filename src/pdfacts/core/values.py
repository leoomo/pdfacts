"""Values that pdfacts could not determine.

"Absent", "unknown" and "unsupported" are three different answers
(docs/conventions.md, "Three answers"). Absent is None. Unsupported is the
capability table and UnsupportedError. Unknown is this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UnknownReason(StrEnum):
    # The two engines describe the object differently.
    ENGINES_DISAGREE = "engines-disagree"
    # The value could not be read from the file.
    UNREADABLE = "unreadable"
    # The file contains a value that the PDF specification does not allow.
    INVALID = "invalid-in-file"


@dataclass(frozen=True, slots=True)
class Unknown:
    """A value that could not be determined, and why."""

    reason: UnknownReason
    detail: str = ""

    def __post_init__(self) -> None:
        # UnknownReason(...) accepts a member or its value and rejects anything else.
        object.__setattr__(self, "reason", UnknownReason(self.reason))
        if not isinstance(self.detail, str):
            raise TypeError(f"detail must be a string, not {type(self.detail).__name__}")

    def __bool__(self) -> bool:
        raise TypeError(
            "an unknown value has no truth value; test it with isinstance(value, Unknown)"
        )
