"""Constructed word spans and refusals pin compact scanner prepayments."""

from dataclasses import replace

import pytest

from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("size", [1, 255, 256, 257, 511, 512, 513])
@pytest.mark.parametrize("terminated", [False, True])
def test_compact_word_scan_fits_exact_bound(size, terminated):
    start = 300
    raw = b" " * start + b"x" * size + (b" " if terminated else b"")
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(raw, bank, source_limits())
    reader.index = start
    reader.load_window(start)
    expected = 64 + 768 * ((size if terminated else size - 1) // 256)
    before = bank.bytes
    bank.byte_limit = before + expected
    reader.scan_word(start, False)
    assert reader.index == start + size
    assert bank.bytes - before == expected


@pytest.mark.parametrize(
    "word,reason,error_offset,maximum",
    [
        (b"/A#4G", "name_escape", 2, 1024),
        (b"A\x80", "bare_token_byte", 1, 1024),
        (b"/A#4", "name_escape", 2, 1024),
        (b"1234 ", "token_bytes", 4, 2),
        (b"\x801 ", "bare_token_byte", 2, 1024),
    ],
)
def test_word_refusals_restore_original_reservation(word, reason, error_offset, maximum):
    start = 300
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(
        b" " * start + word, bank, replace(source_limits(), max_token_bytes=maximum)
    )
    reader.index = start
    reader.load_window(start)
    reserve = bank.reserve
    costs = []

    def observed(cost):
        costs.append(cost)
        reserve(cost)

    bank.reserve = observed
    with pytest.raises((UnsupportedExactSource, ExactSourceLimit)) as caught:
        reader.read()
    assert str(caught.value) == reason
    assert reader.index == start + error_offset
    assert costs == [64, 192]


def test_error_restoration_failure_preserves_caller_exception_without_publishing():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"A\x80", bank, source_limits())
    reader.load_window(0)
    reserve = bank.reserve
    failure = Exhausted("restore sentinel")
    costs, emitted = [], []

    def observed(cost):
        costs.append(cost)
        if cost == 192:
            raise failure
        reserve(cost)

    bank.reserve = observed
    with pytest.raises(Exhausted) as caught:
        reader.read(consumer=lambda *args: emitted.append(args))
    assert caught.value is failure
    assert costs == [64, 192]
    assert reader.index == 0
    assert emitted == []


def test_error_position_is_frozen_before_restoration_callback():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b" " * 300 + b"A\x80", bank, source_limits())
    reader.index = 300
    reader.load_window(300)
    reserve = bank.reserve
    hits = []

    def observed(cost):
        reserve(cost)
        if cost == 192:
            hits.append(True)
            reader.window_start = 900

    bank.reserve = observed
    with pytest.raises(UnsupportedExactSource, match="bare_token_byte"):
        reader.read()
    assert hits == [True]
    assert reader.index == 301


def test_token_limit_is_frozen_before_restoration_callback():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"1234 ", bank, replace(source_limits(), max_token_bytes=2))
    reader.load_window(0)
    reserve = bank.reserve
    hits = []

    def observed(cost):
        reserve(cost)
        if cost == 192:
            hits.append(True)
            reader.limits = replace(reader.limits, max_token_bytes=1024)

    bank.reserve = observed
    with pytest.raises(ExactSourceLimit) as caught:
        reader.read()
    assert hits == [True]
    assert (caught.value.limit, caught.value.allowed, caught.value.found) == ("token_bytes", 2, 4)


@pytest.mark.parametrize(
    "declared_length,expected", [(1073741823, 64), (1073741824, 256), (1073741825, 256)]
)
def test_word_fee_threshold_stops_before_scanning(declared_length, expected):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"x", bank, source_limits())
    # Metadata-only fee probe; no claim of parsing an allocated gigabyte input.
    reader.raw_length = declared_length
    failure = Exhausted("stop before scan")
    costs = []

    def stop(cost):
        costs.append(cost)
        raise failure

    bank.reserve = stop
    with pytest.raises(Exhausted) as caught:
        reader.scan_word(0, False)
    assert caught.value is failure
    assert costs == [expected]
