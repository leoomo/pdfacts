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

    def __bool__(self) -> bool:
        raise TypeError(
            "an unknown value has no truth value; test it with isinstance(value, Unknown)"
        )
