"""Angles the same on every platform.

The C library's atan2, sin and cos may differ in the last bit between
platforms: on the corpus, macOS and Linux gave one glyph a direction that
differed so. These do not use them. An angle that is a multiple of 90 degrees
is exact; any other is computed in decimal arithmetic, which is done the same
way everywhere, to far more digits than a float holds, and rounded once. The
decimal context is this module's own, every field of it given, so that a
caller's context changes nothing.
"""

from __future__ import annotations

import math
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from functools import lru_cache

# Digits of the decimal arithmetic; a float holds 17.
_DIGITS = 50
_CONTEXT = Context(
    prec=_DIGITS,
    rounding=ROUND_HALF_EVEN,
    Emin=MIN_EMIN,
    Emax=MAX_EMAX,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459")
_SMALL = Decimal("0.125")


def _arctan(t: Decimal) -> Decimal:
    """The arctangent of t, in radians; the decimal context set by the caller."""
    if t < 0:
        return -_arctan(-t)
    if t > 1:
        return _PI / 2 - _arctan(1 / t)
    halvings = 0
    while t > _SMALL:
        # arctan(t) = 2 arctan(t / (1 + sqrt(1 + t^2)))
        t = t / (1 + (1 + t * t).sqrt())
        halvings += 1
    square = t * t
    term = t
    total = t
    n = 1
    limit = Decimal(10) ** -(_DIGITS + 5)
    while abs(term) > limit:
        term *= -square
        n += 2
        total += term / n
    return total * (2**halvings)


@lru_cache(maxsize=1024)
def degrees_of(x: float, y: float) -> float:
    """The angle of the vector (x, y), in degrees, from 0 up to 360. A vector
    that is not finite, or (0, 0), has none: ValueError."""
    if not (math.isfinite(x) and math.isfinite(y)) or x == y == 0:
        raise ValueError(f"the vector ({x!r}, {y!r}) has no angle")
    if y == 0:
        return 0.0 if x > 0 else 180.0
    if x == 0:
        return 90.0 if y > 0 else 270.0
    with localcontext(_CONTEXT):
        exact_x, exact_y = Decimal(x), Decimal(y)
        angle = _arctan(exact_y / exact_x)
        if exact_x < 0:
            angle += _PI
        degrees = angle * 180 / _PI % 360
        if degrees < 0:
            degrees += 360
        result = float(degrees)
    # An angle a hair below 0 comes back as 360.
    return 0.0 if result >= 360 else result


@lru_cache(maxsize=1024)
def unit_of(degrees: float) -> tuple[float, float]:
    """The cosine and the sine of an angle given in degrees; one that is not
    finite has none: ValueError."""
    if not math.isfinite(degrees):
        raise ValueError(f"the angle {degrees!r} has no cosine or sine")
    quarters, rest = divmod(degrees, 90)
    if rest == 0:
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(quarters) % 4]
    with localcontext(_CONTEXT):
        angle = Decimal(degrees) % 360 * _PI / 180
        if angle > _PI:
            angle -= 2 * _PI
        cosine = Decimal(0)
        sine = Decimal(0)
        term = Decimal(1)
        n = 0
        limit = Decimal(10) ** -(_DIGITS + 5)
        while abs(term) > limit:
            # term is angle^n / n!, with the sign of the series
            cosine += term
            term = term * angle / (n + 1)
            sine += term
            term = -term * angle / (n + 2)
            n += 2
        return float(cosine), float(sine)
