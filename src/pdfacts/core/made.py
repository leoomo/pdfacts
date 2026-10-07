"""Facts that pdfacts computes itself, made without the checks of their
constructors.

A constructor checks every value a caller gives it. For the values pdfacts
computes, the computation already holds what the checks would: the reader
passes floats it computed, in the order the fact states. Only finiteness is
not held by the computation, and `point` and `rect` check it, raising what
the constructors raise. A caller who makes a fact goes through the
constructor and keeps every check. ROADMAP.md, M4b.
"""

from __future__ import annotations

import math
from typing import Any

from pdfacts.core.geometry import Point, Quad, Rect


def made[T](kind: type[T], /, **values: Any) -> T:
    """An instance of the frozen fact class `kind` holding `values`, every
    field of it, without its constructor's checks."""
    fact = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(fact, name, value)
    return fact


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, not {value}")
    return value + 0.0  # turns -0.0 into 0.0, as the constructor does


def point(x: float, y: float) -> Point:
    """Point(x, y) of two floats pdfacts computed."""
    return made(Point, x=_finite(x, "x"), y=_finite(y, "y"))


def rect(x0: float, y0: float, x1: float, y1: float) -> Rect:
    """Rect(x0, y0, x1, y1) of four floats pdfacts computed; the corners are
    put in order, as the constructor puts them."""
    x0, y0 = _finite(x0, "x0"), _finite(y0, "y0")
    x1, y1 = _finite(x1, "x1"), _finite(y1, "y1")
    return made(Rect, x0=min(x0, x1), y0=min(y0, y1), x1=max(x0, x1), y1=max(y0, y1))


def quad(p1: Point, p2: Point, p3: Point, p4: Point) -> Quad:
    """Quad(p1, p2, p3, p4) of four points pdfacts made."""
    return made(Quad, p1=p1, p2=p2, p3=p3, p4=p4)
