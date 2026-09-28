"""Limits: numbers the caller passes in; none is built in."""

from __future__ import annotations

import dataclasses

import pytest

import pdfacts
from pdfacts import Limits


def test_every_limit_is_off_by_default():
    limits = Limits()
    assert (limits.max_bytes, limits.max_pages) == (None, None)


def test_limits_are_given_by_name():
    with pytest.raises(TypeError):
        Limits(100)  # type: ignore[misc]


def test_limits_cannot_be_changed():
    limits = Limits(max_bytes=100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_bytes = 200  # type: ignore[misc]


def test_a_positive_whole_number_is_a_limit():
    limits = Limits(max_bytes=1, max_pages=3)
    assert (limits.max_bytes, limits.max_pages) == (1, 3)


@pytest.mark.parametrize("field", ["max_bytes", "max_pages"])
@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_a_negative_number_is_refused(field, value):
    with pytest.raises(ValueError, match=field):
        Limits(**{field: value})


@pytest.mark.parametrize("field", ["max_bytes", "max_pages"])
@pytest.mark.parametrize("value", [1.5, 2.0, "10", True])
def test_anything_but_a_whole_number_is_refused(field, value):
    with pytest.raises(TypeError, match=field):
        Limits(**{field: value})


def test_limits_are_exported():
    assert "Limits" in pdfacts.__all__
