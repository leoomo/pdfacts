"""Constructed evidence tuples pin control, depth and local refusal costs."""

from dataclasses import replace
from fractions import Fraction

import pytest

from pdfacts.backends.exact_stream import (
    ExactSourceLimit,
    UnsupportedExactSource,
    check_source_limit,
)
from pdfacts.graphics.source_evidence import _freeze, _same_native
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("count,expected", [(0, 64), (1, 64), (256, 64), (257, 228)])
@pytest.mark.parametrize("operation", ["freeze", "same"])
def test_tuple_exact_control_and_large_counter_boundary(count, expected, operation):
    value = (1,) * count
    bank = Budget(expected, 10000)
    if operation == "freeze":
        assert _freeze(value, bank, source_limits()) is value
    else:
        assert _same_native(value, tuple(list(value)), bank) is True
    assert bank.bytes == expected
    below = Budget(expected - 1, 10000)
    with pytest.raises(Exhausted, match="bytes"):
        if operation == "freeze":
            _freeze(value, below, source_limits())
        else:
            _same_native(value, value, below)


@pytest.mark.parametrize("count", [1, 8, 16, 256])
def test_changed_fraction_tuple_keeps_representation_reservation(count):
    value = (Fraction(1, 2),) * count
    expected = 64 + 128 * count + 128 + 16 * count
    bank = Budget(expected, 10000)
    result = _freeze(value, bank, source_limits())
    assert result == (("fraction", 1, 2),) * count
    assert result is not value
    assert bank.bytes == expected


@pytest.mark.parametrize("left,right,expected", [(1, 257, 64), (257, 1, 192)])
def test_unequal_lengths_keep_large_left_prepaid_before_right(left, right, expected):
    bank = Budget(expected, 10000)
    assert _same_native((1,) * left, (1,) * right, bank) is False
    assert bank.bytes == expected


def test_native_signed_zero_still_compares_each_member():
    bank = Budget(128, 10000)
    assert _same_native((0.0,), (-0.0,), bank) is False
    assert bank.bytes == 128


def test_maximum_payload_depth_uses_cached_depth_values():
    value = 1
    for _ in range(32):
        value = (value,)
    bank = Budget(32 * 64, 10000)
    assert _freeze(value, bank, source_limits()) is value
    assert bank.bytes == 32 * 64


@pytest.mark.parametrize("case", ["depth", "hint", "nonfinite", "type"])
@pytest.mark.parametrize("deny", [False, True])
def test_local_refusals_prepaid_before_exception(case, deny):
    bank = Budget(100000, 10000)
    limits = replace(source_limits(), max_payload_depth=1)
    value, depth, hint = 1, 2, None
    if case == "hint":
        value = (Fraction(1),) * 6
        depth, hint = 1, (value, ())
    elif case == "nonfinite":
        value, depth = float("nan"), 0
    elif case == "type":
        value, depth = [], 0
    reserve = bank.reserve
    costs = []
    sentinel = Exhausted("refusal prepayment")

    def observed(cost):
        costs.append(cost)
        if deny:
            raise sentinel
        reserve(cost)
        # The selected depth rejection must retain the original allowance.
        object.__setattr__(limits, "max_payload_depth", 32)

    bank.reserve = observed
    error = (
        Exhausted
        if deny
        else ExactSourceLimit
        if case in ("depth", "hint")
        else UnsupportedExactSource
    )
    with pytest.raises(error) as caught:
        _freeze(value, bank, limits, depth, hint)
    assert costs == [32768]
    if deny:
        assert caught.value is sentinel
    elif case in ("depth", "hint"):
        assert (caught.value.allowed, caught.value.found) == (1, 2)


def test_depth_33_refusal_has_one_independent_exception_allowance():
    value = 1
    for _ in range(33):
        value = (value,)
    bank = Budget(33 * 64 + 32768, 10000)
    with pytest.raises(ExactSourceLimit) as caught:
        _freeze(value, bank, source_limits())
    assert (caught.value.allowed, caught.value.found) == (32, 33)
    assert bank.bytes == 33 * 64 + 32768


def test_refusal_materialized_frame_inventory_stays_bounded():
    for function in (_freeze, check_source_limit, ExactSourceLimit.__init__):
        code = function.__code__
        slots = code.co_nlocals + len(code.co_cellvars) + len(code.co_freevars) + code.co_stacksize
        assert 208 + 8 * slots <= 512
