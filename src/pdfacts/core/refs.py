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


@dataclass(frozen=True, slots=True, kw_only=True)
class AnnotationRef:
    """Which annotation: its page and place in /Annots, and its object."""

    page_index: int  # from 0
    annotation_index: int  # position in the page's /Annots, from 0
    kind: str  # the /Subtype as text, not empty
    # From 1; None for an annotation written directly into /Annots.
    object_number: int | None | Unknown
    # From 0; None exactly when object_number is None.
    generation: int | None | Unknown

    def __post_init__(self) -> None:
        for name in ("page_index", "annotation_index"):
            value = getattr(self, name)
            if not _is_int(value):
                raise TypeError(f"{name} must be an int, not {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} starts at 0, not {value}")
        if type(self.kind) is not str:
            raise TypeError(f"kind must be a str, not {type(self.kind).__name__}")
        if not self.kind:
            raise ValueError("kind is the /Subtype as text, not empty")
        if (self.object_number is None) != (self.generation is None):
            raise ValueError("object_number and generation are both None or neither")
        for name, lowest in (("object_number", 1), ("generation", 0)):
            value = getattr(self, name)
            if value is None or isinstance(value, Unknown):
                continue
            if not _is_int(value):
                raise TypeError(
                    f"{name} must be an int, None or Unknown, not {type(value).__name__}"
                )
            if value < lowest:
                raise ValueError(f"{name} must be at least {lowest}, not {value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldRef:
    """Which field: its object in the file when that is known."""

    # None for a field written directly into its parent's /Kids.
    object_number: int | None | Unknown
    generation: int | None | Unknown

    def __post_init__(self) -> None:
        if (self.object_number is None) != (self.generation is None):
            raise ValueError("object_number and generation are both None or neither")
        for name, lowest in (("object_number", 1), ("generation", 0)):
            value = getattr(self, name)
            if value is None or isinstance(value, Unknown):
                continue
            if not _is_int(value):
                raise TypeError(
                    f"{name} must be an int, None or Unknown, not {type(value).__name__}"
                )
            if value < lowest:
                raise ValueError(f"{name} must be at least {lowest}, not {value}")
