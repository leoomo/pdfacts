"""References to what a fact belongs to in the file.

Every fact that corresponds to a PDF object carries one (docs/architecture.md,
rule 1). The object number comes from QPDF and is unknown when the engines do
not agree on the page, or QPDF cannot read it: a wrong object number is worse
than none (docs/conventions.md, "Object identity").
"""

from __future__ import annotations

from dataclasses import dataclass

from pdfacts.core.values import Unknown


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True, kw_only=True)
class PageRef:
    """Which page: its index, and its object in the file when that is known."""

    page_index: int
    object_number: int | Unknown
    generation: int | Unknown

    def __post_init__(self) -> None:
        if not _is_int(self.page_index):
            raise TypeError(f"page_index must be an int, not {type(self.page_index).__name__}")
        if self.page_index < 0:
            raise ValueError(f"a page index starts at 0, not {self.page_index}")
        for name, lowest in (("object_number", 1), ("generation", 0)):
            value = getattr(self, name)
            if isinstance(value, Unknown):
                continue
            if not _is_int(value):
                raise TypeError(f"{name} must be an int or Unknown, not {type(value).__name__}")
            if value < lowest:
                raise ValueError(f"{name} must be at least {lowest}, not {value}")
