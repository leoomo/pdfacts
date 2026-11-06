"""Constructed short decimals pin exact values and prepaid conversion boundaries."""

from dataclasses import replace
from fractions import Fraction

import pytest

from pdfacts.backends.exact_decimal import parse_pdf_number
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize(
    "token,numerator,denominator",
    [
        (b"0", 0, 1),
        (b"-0", 0, 1),
        (b"+0", 0, 1),
        (b".0000001", 1, 10000000),
        (b"-.000001", -1, 1000000),
        (b"+.000001", 1, 1000000),
        (b"99999999", 99999999, 1),
        (b"-9999999", -9999999, 1),
        (b"+9999999", 9999999, 1),
        (b"12345.67", 1234567, 100),
        (b"-1234.50", -2469, 2),
        (b"00000000", 0, 1),
        (b"0000.125", 1, 8),
        (b"1234567.", 1234567, 1),
    ],
)
def test_short_decimal_exact_budget_and_one_below(token, numerator, denominator):
    expected_bytes = 256 + 1280 + 8 * len(token)
    bank = Budget(expected_bytes, 10000)
    result = parse_pdf_number(token, bank, limits=source_limits())
    assert (result.numerator, result.denominator) == (numerator, denominator)
    assert bank.bytes == expected_bytes
    assert bank.work == 4 * len(token) + 8
    with pytest.raises(Exhausted, match="bytes"):
        parse_pdf_number(token, Budget(expected_bytes - 1, 10000), limits=source_limits())


@pytest.mark.parametrize("token", [b"+", b"-", b".", b"+.", b"1.2.3", b"--1", b"1a", b"\xff"])
@pytest.mark.parametrize("deny", [False, True])
def test_short_invalid_decimal_restores_before_error(token, deny):
    bank = Budget(10000, 10000)
    reserve = bank.reserve
    costs = []
    sentinel = Exhausted("refusal restoration")

    def observed(cost):
        costs.append(cost)
        if len(costs) == 3 and deny:
            raise sentinel
        reserve(cost)

    bank.reserve = observed
    with pytest.raises(Exhausted if deny else UnsupportedExactSource) as caught:
        parse_pdf_number(token, bank, limits=source_limits())
    assert costs == [256, 1280 + 8 * len(token), 256]
    if deny:
        assert caught.value is sentinel
    else:
        assert str(caught.value) == "numeric_token_domain"


def test_nine_byte_decimal_retains_long_preallocation():
    bank = Budget(100000, 10000)
    reserve = bank.reserve
    costs = []

    def observed(cost):
        costs.append(cost)
        reserve(cost)

    bank.reserve = observed
    assert parse_pdf_number(b"12345678.", bank, limits=source_limits()) == Fraction(12345678)
    assert costs[:2] == [256, 4096 + 8 * 9]


def test_token_bound_refuses_before_short_allocation():
    bank = Budget(256, 10000)
    with pytest.raises(ExactSourceLimit) as caught:
        parse_pdf_number(b"12345678", bank, limits=replace(source_limits(), max_token_bytes=7))
    assert caught.value.found == 8
    assert caught.value.allowed == 7
    assert bank.bytes == 256
