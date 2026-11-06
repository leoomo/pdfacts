"""Payload representation reuse preserves constructed values and finite guards."""

from dataclasses import replace
from fractions import Fraction

import pytest

from pdfacts.backends.exact_numbers import SourcePathNumbers
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from pdfacts.graphics.source_evidence import _SOURCE_FIELDS, _freeze, _record_payload, _same_native
from test_source_evidence import Budget, source_limits


def budget():
    return Budget(8_388_608, 655_360)


def matrix():
    return tuple(Fraction(value) for value in (1, 0, 0, 1, 0, 0))


def test_processed_matrix_is_reused_only_for_same_object():
    value = matrix()
    clone = tuple(list(value))
    source = SourcePathNumbers((), 0, 0, value, Fraction(1), ((value, clone),), "S")
    cache = {"matrix": None}
    frozen = _record_payload(source, _SOURCE_FIELDS, budget(), source_limits(), cache)
    expected = tuple(("fraction", number, 1) for number in (1, 0, 0, 1, 0, 0))
    assert frozen == (
        (),
        0,
        0,
        expected,
        ("fraction", 1, 1),
        ((expected, expected),),
        "S",
        0,
        0,
        ("fraction", 10, 1),
    )
    assert frozen[5][0][0] is frozen[3]
    assert frozen[5][0][1] is not frozen[3]
    uncached = _record_payload(source, _SOURCE_FIELDS, budget(), source_limits())
    assert uncached == frozen
    assert uncached[5][0][0] is not uncached[3]
    with pytest.raises(UnsupportedExactSource, match="path_evidence_record_domain"):
        _record_payload(source, ("matrix", "operations"), budget(), source_limits())


@pytest.mark.parametrize("maximum", [1, 2, 16, 32])
def test_matrix_hint_still_has_input_height_one(maximum):
    value = matrix()
    frozen = _freeze(value, budget(), source_limits())
    limits = replace(source_limits(), max_payload_depth=maximum)
    assert _freeze(value, budget(), limits, maximum - 1, (value, frozen)) is frozen
    with pytest.raises(ExactSourceLimit) as caught:
        _freeze(value, budget(), limits, maximum, (value, frozen))
    assert caught.value.found == maximum + 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_identity_does_not_bypass_native_finiteness(value):
    item = (value,)
    assert _same_native(item, item, budget()) is False
    with pytest.raises(UnsupportedExactSource, match="path_evidence_nonfinite"):
        _freeze(item, budget(), source_limits())


def test_native_signed_zero_remains_distinct():
    assert _same_native((0.0,), (-0.0,), budget()) is False
    assert _same_native((-0.0,), (-0.0,), budget()) is True


def test_large_tuple_identity_and_counter_budget():
    value = tuple(range(1000))
    bank = budget()
    assert _freeze(value, bank, source_limits()) is value
    assert bank.bytes >= 36 * (1000 - 256)
    assert _same_native(value, value, budget()) is True
