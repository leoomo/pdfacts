"""The digest tool: what is hashed, in which form, and what it refuses.

The expected values of the fixed encodings are written by hand from the
specification; none is output of the code under test.
"""

from __future__ import annotations

import hashlib
import sys
from enum import IntEnum, StrEnum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))

from digest import Digest, digest  # noqa: E402


def test_exact_values_are_hashed():
    assert digest([0.1 + 0.2]) != digest([0.3])


def test_negative_zero_is_zero():
    assert digest([-0.0]) == digest([0.0])


@pytest.mark.parametrize(
    ("first", "second"),
    [([1.0, 23.0], [12.0, 3.0]), ([1, 23], [12, 3]), (["asb"], ["a", "b"])],
)
def test_values_are_separated(first, second):
    assert digest(first) != digest(second)


def test_order_matters():
    assert digest([1.0, 2.0]) != digest([2.0, 1.0])


def test_types_are_kept_apart():
    assert len({digest([1]), digest([1.0]), digest(["1"])}) == 3


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_values_that_are_not_finite(value):
    with pytest.raises(ValueError):
        digest([value])


class Number(IntEnum):
    ONE = 1


class Reason(StrEnum):
    INVALID = "invalid"


@pytest.mark.parametrize(
    "value",
    [True, False, None, b"1", (1,), [1], Number.ONE, Reason.INVALID],
)
def test_other_types(value):
    with pytest.raises(TypeError):
        digest([value])


def test_the_separator_inside_a_string():
    with pytest.raises(ValueError):
        digest(["a\nb"])


def test_the_encoding_is_fixed():
    values = [1, -3, 1.0, -0.0, 2.5e-07, 1e16, "1", "Ä"]
    expected = hashlib.sha256(b"i1\ni-3\nf1.0\nf0.0\nf2.5e-07\nf1e+16\ns1\ns\xc3\x84\n")
    assert digest(values) == expected.hexdigest()


def test_nothing():
    assert digest([]) == hashlib.sha256(b"").hexdigest()


def test_in_parts():
    whole = Digest()
    whole.update(1, 2.0)
    whole.update("x")
    assert whole.hexdigest() == hashlib.sha256(b"i1\nf2.0\nsx\n").hexdigest()
