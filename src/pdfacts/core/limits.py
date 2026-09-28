"""Limits the caller sets for one document.

The library has no built-in numbers: a limit that is not given is not
checked (docs/conventions.md, "Limits"). Time and memory inside an engine call
cannot be limited from here; a separate process per document can.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    """None means no limit. Each is checked as early as it can be."""

    # The size of the input; checked before anything else.
    max_bytes: int | None = None
    # The page count; checked as soon as PDFium has opened the file.
    max_pages: int | None = None
    # The object count from the trailer; checked as soon as QPDF has opened
    # the file. When the count is unknown, open() raises LimitUncheckedError.
    max_objects: int | None = None
    # Seconds from the start of open() or recover(). open() checks it before
    # each page is read; recover() once the file is rebuilt and once the
    # result is checked.
    deadline_seconds: int | float | None = None
    # Pixels of one rendering, counted from the region and the scale; checked
    # before the engine is called. Memory grows with the square of the scale,
    # and a running engine call cannot be stopped.
    max_render_pixels: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None:
                continue
            numbers = (int, float) if field.name == "deadline_seconds" else (int,)
            if not isinstance(value, numbers) or isinstance(value, bool):
                kinds = "an int or a float" if float in numbers else "an int"
                raise TypeError(f"{field.name} must be {kinds} or None, not {type(value).__name__}")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{field.name} must be finite, not {value}")
            if value <= 0:
                raise ValueError(f"{field.name} must be greater than 0, not {value}")
