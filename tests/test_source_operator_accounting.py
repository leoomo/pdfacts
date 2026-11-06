"""Constructed cold-loop cases pin common-operator allocation and grammar."""

import dis
from types import FunctionType

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


def cold_read():
    original = ContentParser.read
    return FunctionType(
        original.__code__.replace(), original.__globals__, original.__name__, original.__defaults__
    )


@pytest.mark.parametrize(
    "word",
    [
        b"BT",
        b"B*",
        b"ET",
        b"Tf",
        b"Tm",
        b"Tw",
        b"Tj",
        b"TJ",
        b"RG",
        b"b*",
        b"cm",
        b"f*",
        b"gs",
        b"re",
        b"rg",
        b"BDC",
        b"EMC",
    ],
)
def test_cold_common_operator_fits_exact_bound(word):
    start = 300
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b" " * start + word, bank, source_limits())
    reader.index = start
    reader.load_window(start)
    before = bank.bytes
    # Compact word scan 64, one candidate iterator 64, fresh token 512.
    bank.byte_limit = before + 640
    token = cold_read()(reader)
    assert (token.kind, token.value, token.offset, token.length) == (
        "operator",
        word.decode("ascii"),
        start,
        len(word),
    )
    assert bank.bytes - before == 640


def test_cold_candidate_loop_has_no_unpack_iterator():
    instructions = list(dis.get_instructions(cold_read()))
    assert any(item.opname == "GET_ITER" for item in instructions)
    assert all(item.opname != "UNPACK_SEQUENCE" for item in instructions)


def test_cold_operator_one_below_refuses_without_publication():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"TJ", bank, source_limits())
    reader.load_window(0)
    # Callback channel costs 256, in addition to word scan and candidate scan.
    bank.byte_limit = bank.bytes + 64 + 64 + 256 - 1
    emitted = []
    with pytest.raises(Exhausted, match="bytes"):
        cold_read()(reader, consumer=lambda *args: emitted.append(args))
    assert emitted == []


@pytest.mark.parametrize("word", [b"Tz", b"Bz", b"BDz"])
def test_all_candidate_misses_preserve_scalar_copy_and_cache_reuse(word):
    reader = ContentParser(word + b" " + word, Budget(8_388_608, 655_360), source_limits())
    first = cold_read()(reader)
    second = cold_read()(reader)
    assert (first.value, first.offset, first.length) == (word.decode("ascii"), 0, len(word))
    assert (second.value, second.offset, second.length) == (
        word.decode("ascii"),
        len(word) + 1,
        len(word),
    )
    assert first is not second and first.value is second.value
    with pytest.raises(UnsupportedExactSource, match="operator_domain"):
        source_ledger((((1, 0), word),), Budget(8_388_608, 655_360), limits=source_limits())
