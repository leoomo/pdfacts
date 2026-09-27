"""Points, rectangles, matrices and the geometry of a page.

Facts are stored in user space, the coordinates written in the file. Display
space is the visible page with its rotation applied, origin bottom left, y
upwards, measured in user space units (docs/conventions.md, "Coordinate
spaces"). There is no space with y pointing downwards.

Every coordinate is a finite float; negative zero is stored as zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pdfacts.core.values import Unknown

ROTATIONS = (0, 90, 180, 270)


def _is_number(value: object) -> bool:
    # bool is an int in Python; a flag is not a coordinate.
    return isinstance(value, int | float) and not isinstance(value, bool)


def _coordinate(value: float, name: str) -> float:
    if not _is_number(value):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} is too large to be a coordinate") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, not {number}")
    return number + 0.0  # turns -0.0 into 0.0


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _coordinate(self.x, "x"))
        object.__setattr__(self, "y", _coordinate(self.y, "y"))


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle with x0 <= x1 and y0 <= y1; the corners are put in order."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        x0, y0 = _coordinate(self.x0, "x0"), _coordinate(self.y0, "y0")
        x1, y1 = _coordinate(self.x1, "x1"), _coordinate(self.y1, "y1")
        object.__setattr__(self, "x0", min(x0, x1))
        object.__setattr__(self, "x1", max(x0, x1))
        object.__setattr__(self, "y0", min(y0, y1))
        object.__setattr__(self, "y1", max(y0, y1))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class Matrix:
    """A PDF matrix: x' = a*x + c*y + e, y' = b*x + d*y + f."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def __post_init__(self) -> None:
        for name in ("a", "b", "c", "d", "e", "f"):
            object.__setattr__(self, name, _coordinate(getattr(self, name), name))

    def apply(self, point: Point) -> Point:
        return Point(
            self.a * point.x + self.c * point.y + self.e,
            self.b * point.x + self.d * point.y + self.f,
        )

    def apply_rect(self, rect: Rect) -> Rect:
        """The bounding rectangle of the four transformed corners."""
        points = [self.apply(Point(x, y)) for x in (rect.x0, rect.x1) for y in (rect.y0, rect.y1)]
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return Rect(min(xs), min(ys), max(xs), max(ys))

    def then(self, other: Matrix) -> Matrix:
        """The matrix that applies self first and other second."""
        return Matrix(
            other.a * self.a + other.c * self.b,
            other.b * self.a + other.d * self.b,
            other.a * self.c + other.c * self.d,
            other.b * self.c + other.d * self.d,
            other.a * self.e + other.c * self.f + other.e,
            other.b * self.e + other.d * self.f + other.f,
        )

    def inverse(self) -> Matrix:
        determinant = self.a * self.d - self.b * self.c
        if determinant == 0 or not math.isfinite(determinant):
            raise ValueError(
                f"{self} has no inverse that can be computed: determinant {determinant}"
            )
        return Matrix(
            self.d / determinant,
            -self.b / determinant,
            -self.c / determinant,
            self.a / determinant,
            (self.c * self.f - self.d * self.e) / determinant,
            (self.b * self.e - self.a * self.f) / determinant,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PageGeometry:
    """Where a page is visible, how it is turned, and the transforms to show it."""

    index: int
    # Media box intersected with crop box, in user space.
    visible_box: Rect
    # Clockwise: 0, 90, 180 or 270.
    rotation: int
    # 1.0 when the file does not state it. It does not scale display space.
    user_unit: float | Unknown

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool):
            raise TypeError(f"index must be an int, not {type(self.index).__name__}")
        if self.index < 0:
            raise ValueError(f"a page index starts at 0, not {self.index}")
        if not isinstance(self.visible_box, Rect):
            raise TypeError(f"visible_box must be a Rect, not {type(self.visible_box).__name__}")
        if not isinstance(self.rotation, int) or isinstance(self.rotation, bool):
            raise TypeError(f"rotation must be an int, not {type(self.rotation).__name__}")
        if self.rotation not in ROTATIONS:
            raise ValueError(f"rotation must be one of {ROTATIONS}, not {self.rotation}")
        if not isinstance(self.user_unit, Unknown):
            if not _is_number(self.user_unit):
                raise TypeError(
                    f"user_unit must be a number or Unknown, not {type(self.user_unit).__name__}"
                )
            value = float(self.user_unit)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"user_unit must be a positive number, not {value}")
            object.__setattr__(self, "user_unit", value)

    @property
    def width(self) -> float:
        box = self.visible_box
        return box.width if self.rotation in (0, 180) else box.height

    @property
    def height(self) -> float:
        box = self.visible_box
        return box.height if self.rotation in (0, 180) else box.width

    @property
    def to_display(self) -> Matrix:
        """User space to display space.

        Built from 0, 1 and -1 and the corners of the visible box, so that it
        carries no rounding from trigonometry.
        """
        x0, y0, x1, y1 = (
            self.visible_box.x0,
            self.visible_box.y0,
            self.visible_box.x1,
            self.visible_box.y1,
        )
        if self.rotation == 0:
            return Matrix(1, 0, 0, 1, -x0, -y0)
        if self.rotation == 90:
            return Matrix(0, -1, 1, 0, -y0, x1)
        if self.rotation == 180:
            return Matrix(-1, 0, 0, -1, x1, y1)
        return Matrix(0, 1, -1, 0, y1, -x0)

    @property
    def to_user(self) -> Matrix:
        """Display space to user space."""
        # The determinant of every matrix above is 1, so the inverse is exact.
        return self.to_display.inverse()
