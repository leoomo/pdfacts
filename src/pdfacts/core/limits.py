"""Limits the caller sets for one document.

The library has no built-in numbers: a limit that is not given is not
checked (docs/conventions.md, "Limits"). Time and memory inside an engine call
cannot be limited from here; a separate process per document can.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    """None means no limit. Each is checked as early as it can be."""

    # The size of the input; checked before anything else.
    max_bytes: int | None = None
    # The page count; checked as soon as PDFium has opened the file.
    max_pages: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field.name} must be an int or None, not {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field.name} must be greater than 0, not {value}")
