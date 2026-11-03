"""Synthetic decimal tokens with expected values derived from their digits."""

import sys
from dataclasses import replace
from fractions import Fraction

import pytest

from pdfacts.backends.exact_decimal import parse_pdf_number
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from test_source_evidence import Budget, Exhausted, source_limits


def parse(token, byte_limit=8_388_608):
    return parse_pdf_number(
        token,
        Budget(byte_limit, 655_360),
        limits=replace(source_limits(), max_token_bytes=10_000),
    )


@pytest.mark.parametrize(
    "token, expected",
    [
        (b"0" * 5000 + b".1", Fraction(1, 10)),
        (b"-" + b"0" * 5000 + b".0", Fraction(0)),
        (b"1234567890.0000", Fraction(1234567890)),
        (b"-000000000000000.125", Fraction(-1, 8)),
        (b"000000000000000.32", Fraction(8, 25)),
        (b"000000000000000.07", Fraction(7, 100)),
        (b"+000000001000000", Fraction(1000000)),
    ],
)
def test_explicit_large_token_values(token, expected):
    assert parse(token) == expected


def test_interpreter_digit_limit_does_not_change_conversion():
    token = b"1." + b"0" * 999 + b"1"
    expected = Fraction(10**1000 + 1, 10**1000)
    original = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        assert parse(token) == expected
    finally:
        sys.set_int_max_str_digits(original)


def test_normalized_bound_is_applied_after_cancellation():
    # 5**1800 has more than 4096 bits, but the normalized denominator does not.
    token = b"0." + str(5**1800).zfill(1800).encode("ascii")
    assert parse(token) == Fraction(1, 2**1800)


def test_rational_bound_is_still_enforced():
    with pytest.raises(UnsupportedExactSource, match="rational_bits"):
        parse(b"1" * 1300 + b".")


def test_explicit_token_bound_and_one_below():
    token = b"0" * 5000 + b".1"
    bank = Budget(8_388_608, 655_360)
    assert parse_pdf_number(
        token, bank, limits=replace(source_limits(), max_token_bytes=len(token))
    ) == Fraction(1, 10)
    with pytest.raises(ExactSourceLimit) as caught:
        parse_pdf_number(
            token,
            Budget(8_388_608, 655_360),
            limits=replace(source_limits(), max_token_bytes=len(token) - 1),
        )
    assert caught.value.limit == "token_bytes"
    assert caught.value.found == len(token)


def test_long_conversion_exact_and_one_below_byte_budget():
    token = b"000000000000.125"
    limits = source_limits()
    bank = Budget(8_388_608, 655_360)
    assert parse_pdf_number(token, bank, limits=limits) == Fraction(1, 8)
    assert parse(token, bank.bytes) == Fraction(1, 8)
    with pytest.raises(Exhausted, match="bytes"):
        parse(token, bank.bytes - 1)


@pytest.mark.parametrize(
    "token, disagreement",
    [(b"1." + b"0" * 999 + b"1", False), (b"0" * 5000 + b".1", True)],
    ids=["low_digit_limit", "native_large_token_disagreement"],
)
def test_large_decimal_through_public_document_and_private_issuer(token, disagreement):
    import pdfacts
    from pdfacts.backends.analysis_identity import classification_identity
    from pdfacts.graphics.source_evidence import issue_root_path_evidence
    from test_source_evidence import source_file

    data = source_file(token + b" w 10 20 m 90 20 l S")
    bank = Budget(8_388_608, 655_360)
    limits = replace(source_limits(), max_token_bytes=6000)
    original = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with (
            classification_identity(data, bank) as (owner, handle, _),
            pdfacts.open(data) as opened,
        ):
            with opened._access() as access:
                access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
            if disagreement:
                from pdfacts.core.errors import SourceEvidenceError

                # The exact source value remains 1/10. The pinned native engine
                # disagrees on this long spelling, so authority must be refused.
                with pytest.raises(SourceEvidenceError) as caught:
                    issue_root_path_evidence(opened, 0, bank, limits=limits)
                assert caught.value.reason == "native_width_disagreement"
                return
            paths, images, authority = issue_root_path_evidence(opened, 0, bank, limits=limits)
            assert len(paths) == 1
            assert images == ()
            assert authority is not None
    finally:
        sys.set_int_max_str_digits(original)


@pytest.mark.parametrize("padding", [0, 1023], ids=["short_scale", "caller_admitted_long_scale"])
def test_matrix_witness_uses_source_admitted_decimal_tokens(padding):
    from test_source_evidence import issued

    token = b".1" + b"0" * padding
    content = token + b" 0 0 .1 0 0 cm 1 0 0 1 3522.05 0 cm 0 0 10 10 re S"
    paths, images, authority = issued(
        content, replace(source_limits(), max_token_bytes=2000), Budget(8_388_608, 655_360)
    )
    assert len(paths) == 1
    assert images == ()
    assert authority is not None


def test_long_matrix_token_still_obeys_producer_limit():
    from pdfacts.core.errors import LimitExceededError
    from test_source_evidence import issued

    token = b".1" + b"0" * 1023
    content = token + b" 0 0 .1 0 0 cm 1 0 0 1 3522.05 0 cm 0 0 10 10 re S"
    with pytest.raises(LimitExceededError) as caught:
        issued(content, source_limits(), Budget(8_388_608, 655_360))
    assert caught.value.limit == "token_bytes"
    assert caught.value.allowed == 1024
    assert caught.value.found == 1025


@pytest.mark.parametrize(
    "token, value", [(b"", Fraction(0)), (b"1", 1), (bytearray(b"1"), Fraction(1))]
)
def test_witness_keeps_unsupported_operand_domains_closed(token, value):
    from pdfacts.backends.exact_matrix_witness import _operand

    assert _operand(value, token, Budget(8_388_608, 655_360)) is None


@pytest.mark.parametrize("plan", [None, (), [], (None,)])
def test_forged_matrix_plan_shape_is_rejected(plan):
    from pdfacts.backends import exact_matrix_witness as witness

    assert (
        witness.matches(
            plan,
            witness.EXACT_IDENTITY,
            (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            [None, None, None, None, 0],
            Budget(8_388_608, 655_360),
        )
        is False
    )


def test_matrix_plan_identity_and_all_six_native_values_remain_required():
    from pdfacts.backends import exact_matrix_witness as witness

    clone = tuple(list(witness.EXACT_IDENTITY))
    native = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    memo = [None, None, None, None, 0]
    bank = Budget(8_388_608, 655_360)
    assert witness.matches(witness.IDENTITY_PLAN, clone, native, memo, bank) is False
    forged = (witness.IDENTITY_PLAN, clone, clone, (b"1",) * 6, 1)
    assert witness.matches(forged, witness.EXACT_IDENTITY, native, memo, bank) is False
    assert (
        witness.matches(
            witness.IDENTITY_PLAN, witness.EXACT_IDENTITY, (*native[:5], float("nan")), memo, bank
        )
        is False
    )
    assert (
        witness.matches(
            witness.IDENTITY_PLAN, witness.EXACT_IDENTITY, (*native[:5], 1.0), memo, bank
        )
        is False
    )
