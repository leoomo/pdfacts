"""Limits: numbers the caller passes in; none is built in."""

from __future__ import annotations

import dataclasses

import pytest

import pdfacts
from pdfacts import Limits

COUNTS = ["max_bytes", "max_pages", "max_objects", "max_render_pixels"]


def test_every_limit_is_off_by_default():
    limits = Limits()
    assert [getattr(limits, field.name) for field in dataclasses.fields(limits)] == [None] * 6
    assert [field.name for field in dataclasses.fields(limits)] == [
        "max_bytes",
        "max_pages",
        "max_objects",
        "deadline_seconds",
        "max_render_pixels",
        "max_items_per_page",
    ]


def test_limits_are_given_by_name():
    with pytest.raises(TypeError):
        Limits(100)  # type: ignore[misc]


def test_limits_cannot_be_changed():
    limits = Limits(max_bytes=100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_bytes = 200  # type: ignore[misc]


def test_a_positive_whole_number_is_a_limit():
    limits = Limits(max_bytes=1, max_pages=3, max_objects=10**12, max_render_pixels=7)
    assert (limits.max_bytes, limits.max_pages, limits.max_objects, limits.max_render_pixels) == (
        1,
        3,
        10**12,
        7,
    )


@pytest.mark.parametrize("value", [1, 0.25, 10**400])
def test_a_deadline_is_a_positive_number_of_seconds(value):
    assert Limits(deadline_seconds=value).deadline_seconds == value


@pytest.mark.parametrize("field", [*COUNTS, "deadline_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_a_negative_number_is_refused(field, value):
    with pytest.raises(ValueError, match=field):
        Limits(**{field: value})


def test_a_deadline_of_zero_seconds_as_a_float_is_refused():
    with pytest.raises(ValueError, match="deadline_seconds"):
        Limits(deadline_seconds=0.0)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -float("inf")])
def test_a_deadline_that_is_not_finite_is_refused(value):
    with pytest.raises(ValueError, match="deadline_seconds"):
        Limits(deadline_seconds=value)


@pytest.mark.parametrize("field", COUNTS)
@pytest.mark.parametrize("value", [1.5, 2.0, "10", True])
def test_a_count_that_is_not_a_whole_number_is_refused(field, value):
    with pytest.raises(TypeError, match=field):
        Limits(**{field: value})


@pytest.mark.parametrize("value", ["10", True, [1]])
def test_a_deadline_that_is_not_a_number_is_refused(value):
    with pytest.raises(TypeError, match="deadline_seconds"):
        Limits(deadline_seconds=value)


def test_limits_are_exported():
    assert "Limits" in pdfacts.__all__
