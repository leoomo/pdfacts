"""What the core asks the engines, and the plain data they answer with.

One protocol per capability (docs/architecture.md, rule 5). This module
imports no engine and nothing from pdfacts.core: the core depends on the
backends through it, and the backends may not import the public errors.

A backend reports what the engine says. It does not normalize values and
does not decide what is unknown; the core does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

# x0, y0, x1, y1 in user space.
Box = tuple[float, float, float, float]


class FailureKind(StrEnum):
    # The engine could not read the file.
    FORMAT = "format"
    # A password is needed, or the one given is wrong.
    PASSWORD = "password"
    # Anything else, including an engine used after close().
    OTHER = "other"


class EngineFailure(Exception):
    """An engine could not do what it was asked; the core translates it."""

    def __init__(self, kind: FailureKind | str, detail: str) -> None:
        super().__init__(FailureKind(kind), detail)
        self.kind = FailureKind(kind)
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass(frozen=True, slots=True)
class PageObject:
    """What the file says about one page object, read through QPDF."""

    object_number: int
    generation: int
    # As written, corners not put in order; None when absent or not four
    # numbers. Inherited values already sit on the page.
    media_box: Box | None
    crop_box: Box | None
    # As written, for example -90 or 450; None when absent or not an integer.
    rotate: int | None
    # A number greater than 0, as a float; None otherwise.
    user_unit: float | None
    # True when the key is there and holds anything but such a number.
    user_unit_invalid: bool


class GeometryReader(Protocol):
    """Page geometry as an engine that interprets the page sees it."""

    def page_count(self) -> int: ...

    def visible_box(self, index: int) -> Box: ...

    def rotation(self, index: int) -> int: ...

    def close(self) -> None: ...


class ObjectReader(Protocol):
    """The objects of the file as they are written."""

    def page_count(self) -> int: ...

    def object_count(self) -> int | None: ...

    def page_object(self, index: int) -> PageObject: ...

    def warnings(self) -> tuple[str, ...]: ...

    def close(self) -> None: ...
