"""Constructed scanner inputs distinguish the compact-index allocation bound."""

import pytest

from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("space", [b"\x00", b"\t", b"\n", b"\x0c", b"\r", b" "])
@pytest.mark.parametrize("size", [1, 255, 256, 257, 513])
def test_compact_whitespace_scan_fits_exact_prepaid_bound(space, size):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(space * size + b"Q", bank, source_limits())
    reader.load_window(0)
    before = bank.bytes
    # Existing window is prepaid; every further window still costs 768 bytes.
    expected = 64 + 768 * (size // 256)
    bank.byte_limit = before + expected
    reader.skip_ignorable()
    assert reader.index == size
    assert bank.bytes - before == expected


@pytest.mark.parametrize("ending", [b"\r", b"\n", b"\r\n", b""])
def test_compact_comment_scan_preserves_terminator_and_eof(ending):
    raw = b"%" + b"x" * 510 + ending
    if ending:
        raw += b"Q"
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(raw, bank, source_limits())
    reader.load_window(0)
    expected_index = len(raw) - 1 if ending else len(raw)
    expected = 64 + 768 * ((len(raw) - 1) // 256)
    before = bank.bytes
    bank.byte_limit = before + expected
    reader.skip_ignorable()
    assert reader.index == expected_index
    assert bank.bytes - before == expected


def test_compact_scan_one_byte_below_bound_refuses_before_advancing():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b" Q", bank, source_limits())
    reader.load_window(0)
    bank.byte_limit = bank.bytes + 63
    with pytest.raises(Exhausted, match="bytes"):
        reader.skip_ignorable()
    assert reader.index == 0


def test_compact_scan_preserves_budget_exception_identity():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b" Q", bank, source_limits())
    failure = Exhausted("caller sentinel")
    costs = []

    def refuse(cost):
        costs.append(cost)
        raise failure

    bank.reserve = refuse
    with pytest.raises(Exhausted) as caught:
        reader.skip_ignorable()
    assert caught.value is failure
    assert costs == [64]
    assert reader.index == 0


@pytest.mark.parametrize(
    "declared_length,expected", [(1073741823, 64), (1073741824, 256), (1073741825, 256)]
)
def test_noncompact_length_keeps_original_fee_before_scanning(declared_length, expected):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b" Q", bank, source_limits())
    # Instrument fee selection only: abort before scanning the substituted
    # metadata. This is not a claim to parse an allocated gigabyte input.
    reader.raw_length = declared_length
    costs = []
    failure = Exhausted("stop before scan")

    def stop(cost):
        costs.append(cost)
        raise failure

    bank.reserve = stop
    with pytest.raises(Exhausted) as caught:
        reader.skip_ignorable()
    assert caught.value is failure
    assert costs == [expected]
    assert reader.index == 0
