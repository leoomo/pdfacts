"""Angles the same on every platform: pdfacts.text.angles.

The values expected are worked out here apart from the module: with exact
rational arithmetic (fractions.Fraction), Euler's series for the arctangent
and the Taylor series of sine and cosine, rounded once to a float; or, for
45 and 30 degrees, from square roots, which every platform rounds correctly
(AGENTS.md, rule 2). Three of the vectors are those of glyphs of the
corpus, turned by 13, by 1.8 and by 269.9 degrees. The first one's direction,
from the C library's atan2 turned into degrees, differed in its last bit
between macOS and Linux; the third one's was the same on both and one bit
off, from math.degrees and % 360 rounding the angle twice more.
"""

from __future__ import annotations

import ast
import decimal
import math
from fractions import Fraction
from pathlib import Path

import pytest

import pdfacts
from pdfacts.backends.protocols import TextCharacter
from pdfacts.text import angles
from pdfacts.text.angles import degrees_of, unit_of
from pdfacts.text.glyphs import glyphs_of
from synthetic import cases

ROOT = Path(__file__).resolve().parent.parent

PI = Fraction("3.14159265358979323846264338327950288419716939937510582097494459230781640628620")
# Relative to the value: far below half a unit in its last place as a float.
TOLERANCE = Fraction(1, 10**45)


def arctan(t: Fraction) -> Fraction:
    """Euler's series, for 0 <= t <= 1: the sum over n of
    2^(2n) (n!)^2 / (2n+1)! * t^(2n+1) / (1+t^2)^(n+1)."""
    ratio = t * t / (1 + t * t)
    term = t / (1 + t * t)
    total = Fraction(0)
    n = 0
    while term > t * TOLERANCE:
        total += term
        term = term * ratio * (2 * n + 2) / (2 * n + 3)
        n += 1
    return total


def exact_degrees(x: float, y: float) -> float:
    """The angle of (x, y) in degrees, from 0 up to 360, rounded once."""
    fx, fy = Fraction(x), Fraction(y)
    t = abs(fy / fx)
    angle = arctan(t) if t <= 1 else PI / 2 - arctan(1 / t)
    if fx < 0:
        angle = PI - angle
    if fy < 0:
        angle = 2 * PI - angle
    return float(angle * 180 / PI)


def exact_unit(degrees: float) -> tuple[float, float]:
    """The cosine and sine of an angle in degrees, rounded once."""
    angle = Fraction(degrees) * PI / 180
    if angle > PI:
        angle -= 2 * PI
    cosine, sine = Fraction(0), Fraction(0)
    term, n = Fraction(1), 0
    limit = TOLERANCE * min(1, abs(angle))
    while abs(term) > limit or n < 4:
        cosine += term
        term = term * angle / (n + 1)
        sine += term
        term = -term * angle / (n + 2)
        n += 2
    return float(cosine), float(sine)


VECTORS = [
    (0.9743673934652296, 0.2249626247885956),
    (0.9994942578392484, 0.031799819910971444),
    (-0.002004003960530069, -0.999997991982047),
    (3.0, 4.0),
    (4.0, 3.0),
    (-5.0, 12.0),
    (-12.0, -5.0),
    (5.0, -12.0),
    (1.0, 1e-08),
    (1.0, 1e-13),
    (1.0, 1e-300),
    (-1e-08, -1.0),
    (0.6, 0.8),
]


@pytest.mark.parametrize(("x", "y"), VECTORS)
def test_the_angle_of_a_vector(x, y):
    assert degrees_of(x, y) == exact_degrees(x, y)


@pytest.mark.parametrize(
    ("x", "y", "degrees"),
    [
        (1.0, 0.0, 0.0),
        (2.5, 0.0, 0.0),
        (0.0, 1.0, 90.0),
        (-1.0, 0.0, 180.0),
        (0.0, -3.0, 270.0),
        (1.0, 1.0, 45.0),
        (-1.0, 1.0, 135.0),
        (-1.0, -1.0, 225.0),
        (1.0, -1.0, 315.0),
    ],
)
def test_the_angles_that_are_exact(x, y, degrees):
    assert degrees_of(x, y) == degrees


def test_an_angle_a_hair_below_0_is_0():
    assert degrees_of(1.0, -1e-300) == 0.0


@pytest.mark.parametrize(
    "degrees",
    [
        13.00068037656621,
        1.8223026856330222,
        269.8851789540799,
        5.729577951308232e-07,
        0.09,
        0.14,
        1.0,
        359.9,
        123.456,
    ],
)
def test_the_unit_vector_of_an_angle(degrees):
    assert unit_of(degrees) == exact_unit(degrees)


@pytest.mark.parametrize(
    ("degrees", "unit"),
    [
        (0.0, (1.0, 0.0)),
        (90.0, (0.0, 1.0)),
        (180.0, (-1.0, 0.0)),
        (270.0, (0.0, -1.0)),
        (450.0, (0.0, 1.0)),
        (45.0, (math.sqrt(0.5), math.sqrt(0.5))),
        (30.0, (math.sqrt(3) / 2, 0.5)),
    ],
)
def test_the_unit_vectors_that_are_exact(degrees, unit):
    assert unit_of(degrees) == unit


def test_no_glyph_word_or_line_takes_the_c_librarys_trigonometry(monkeypatch):
    """Text turned by 0.9 and 1.1 degrees, and a page turned by 90, read with
    the C library's trigonometry made to fail, give what they give with it."""
    pages = [
        cases.TEXT[name]().data
        for name in ("upright within one degree", "upright as the page is shown")
    ]

    def read_all():
        read = []
        for data in pages:
            with pdfacts.open(data) as document:
                read.append(
                    (
                        pdfacts.glyphs(document, 0),
                        pdfacts.words(document, 0),
                        pdfacts.lines(document, 0),
                    )
                )
        return read

    before = read_all()

    def fails(*arguments):
        raise AssertionError("the C library's trigonometry was taken")

    for name in ("atan", "atan2", "sin", "cos", "tan"):
        monkeypatch.setattr(math, name, fails)
    assert read_all() == before


@pytest.mark.parametrize(
    ("x", "y"), [(math.nan, 0.0), (0.0, math.inf), (-math.inf, 1.0), (0.0, 0.0), (-0.0, 0.0)]
)
def test_a_vector_without_an_angle(x, y):
    with pytest.raises(ValueError):
        degrees_of(x, y)


@pytest.mark.parametrize("degrees", [math.nan, math.inf, -math.inf])
def test_an_angle_without_a_cosine_or_sine(degrees):
    with pytest.raises(ValueError):
        unit_of(degrees)


def test_the_callers_decimal_context_changes_nothing():
    """A context that rounds down, holds 5 digits, a narrow range of exponents
    and traps every inexact result: the values are the same, and so is the
    text read."""
    expected_degrees = [exact_degrees(x, y) for x, y in VECTORS]
    expected_units = [exact_unit(degrees) for degrees in (13.00068037656621, 0.09, 123.456)]
    page = cases.TEXT["upright within one degree"]().data
    with pdfacts.open(page) as document:
        before = pdfacts.lines(document, 0)
    with decimal.localcontext() as context:
        context.prec = 5
        context.rounding = decimal.ROUND_DOWN
        context.Emin, context.Emax = -10, 10
        context.traps[decimal.Inexact] = True
        context.traps[decimal.Rounded] = True
        degrees_of.cache_clear()
        unit_of.cache_clear()
        assert [degrees_of(x, y) for x, y in VECTORS] == expected_degrees
        assert [unit_of(d) for d in (13.00068037656621, 0.09, 123.456)] == expected_units
        with pdfacts.open(page) as document:
            assert pdfacts.lines(document, 0) == before


TRIGONOMETRY = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "degrees", "radians"}


def test_no_module_of_text_takes_the_c_librarys_trigonometry():
    """The modules of pdfacts.text and the M4a gate: neither a name imported
    from math nor math.<name> for any of them, which a patched math.cos would
    not see, as it would not see `from math import cos`."""
    paths = sorted((ROOT / "src" / "pdfacts" / "text").glob("*.py"))
    assert len(paths) >= 5
    for path in [*paths, ROOT / "bench" / "gates" / "m4a.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "math", f"{path.name}: from math import ..."
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "math" or alias.asname is None, path.name
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "math":
                    assert node.attr not in TRIGONOMETRY, f"{path.name}: math.{node.attr}"


@pytest.mark.parametrize("size", [10.0, -10.0])
@pytest.mark.parametrize(("x", "y"), VECTORS[:3])
def test_a_glyphs_direction_is_the_angle_of_its_unit_vector(x, y, size):
    """The direction of a glyph whose matrix's x axis is (x, y): the angle of
    the unit vector along its baseline, (x, y) over its length, turned half
    round where the font size is below 0, rounded once."""
    character = TextCharacter(
        code=ord("A"),
        map_error=False,
        hyphen=False,
        origin=(100.0, 200.0),
        matrix=(x, y, -y, x, 100.0, 200.0),
        font_size=size,
        loose_box=None,
        render_mode=0,
        ascent=0.8,
        descent=-0.2,
    )
    (glyph,) = glyphs_of(0, 0, [character])
    sign = 1.0 if size > 0 else -1.0
    length = math.hypot(x, y)
    assert glyph.direction == exact_degrees(sign * x / length, sign * y / length)


def test_the_modules_decimal_context():
    """Every field given, none taken from decimal.DefaultContext."""
    context = angles._CONTEXT
    assert (context.prec, context.rounding, context.Emin, context.Emax, context.clamp) == (
        50,
        decimal.ROUND_HALF_EVEN,
        decimal.MIN_EMIN,
        decimal.MAX_EMAX,
        0,
    )
    trapped = {signal for signal, on in context.traps.items() if on}
    assert trapped == {decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow}
