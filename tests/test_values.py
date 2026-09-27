"""An unknown value is a value of its own: not absent, not a boolean, not a guess."""

from __future__ import annotations

import dataclasses
import pickle

import pytest

import pdfacts
from pdfacts import Unknown, UnknownReason


def test_the_reasons_are_exactly_these():
    assert {reason.name: reason.value for reason in UnknownReason} == {
        "ENGINES_DISAGREE": "engines-disagree",
        "UNREADABLE": "unreadable",
        "INVALID": "invalid-in-file",
    }


def test_an_unknown_value_is_not_a_boolean():
    # `if page.geometry.user_unit:` must not treat an unknown value as true.
    with pytest.raises(TypeError, match="isinstance"):
        bool(Unknown(UnknownReason.UNREADABLE))
    with pytest.raises(TypeError):
        if Unknown(UnknownReason.INVALID, "UserUnit is a string"):
            pass


def test_unknown_values_with_equal_fields_are_equal():
    first = Unknown(UnknownReason.ENGINES_DISAGREE, "page count 3 against 2")
    second = Unknown(UnknownReason.ENGINES_DISAGREE, "page count 3 against 2")
    assert first == second
    assert hash(first) == hash(second)
    assert first != Unknown(UnknownReason.ENGINES_DISAGREE, "page count 3 against 1")
    assert first != Unknown(UnknownReason.UNREADABLE, "page count 3 against 2")


def test_an_unknown_value_cannot_be_changed():
    value = Unknown(UnknownReason.UNREADABLE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.reason = UnknownReason.INVALID  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        value.extra = 1  # type: ignore[attr-defined]


def test_an_unknown_value_is_not_absent():
    value = Unknown(UnknownReason.UNREADABLE)
    assert value is not None
    assert value != None  # noqa: E711 - the comparison itself is under test


def test_the_detail_is_optional_and_empty_by_default():
    assert Unknown(UnknownReason.UNREADABLE).detail == ""


def test_an_unknown_value_survives_pickling():
    value = Unknown(UnknownReason.INVALID, "UserUnit is -1")
    assert pickle.loads(pickle.dumps(value)) == value


def test_unknown_is_exported():
    assert pdfacts.Unknown is Unknown
    assert pdfacts.UnknownReason is UnknownReason
    assert {"Unknown", "UnknownReason"} <= set(pdfacts.__all__)
