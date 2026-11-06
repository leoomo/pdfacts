"""Synthetic scalar words preserve lexical values, spans and source validation."""

import dis
from dataclasses import FrozenInstanceError, replace

import pytest

from pdfacts.backends import finite_content
from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, issued, source_limits


def parser(raw, bank=None, limits=None):
    return ContentParser(raw, bank or Budget(8_388_608, 655_360), limits or source_limits())


@pytest.mark.parametrize("byte", [*range(48, 58), 43, 45, 46, 65, 92, 126])
def test_single_byte_uses_fixed_grammar_values(byte, monkeypatch):
    original = finite_content.numeric_word
    calls = []

    def observed(word):
        calls.append(word)
        return original(word)

    monkeypatch.setattr(finite_content, "numeric_word", observed)
    assert finite_content.numeric_word is observed
    parser(b"12").read()
    assert calls == [b"12"]
    calls.clear()
    raw = bytes([byte])
    actual = parser(raw).read()
    expected = ("number", raw) if 48 <= byte <= 57 else ("operator", chr(byte))
    assert (actual.kind, actual.value, actual.offset, actual.length) == (*expected, 0, 1)
    assert calls == []


def test_repeated_scalar_reuses_value_with_fresh_token_and_span(monkeypatch):
    original = finite_content.numeric_word
    calls = []

    def observed(word):
        calls.append(word)
        return original(word)

    monkeypatch.setattr(finite_content, "numeric_word", observed)
    assert finite_content.numeric_word is observed
    bank = Budget(8_388_608, 655_360)
    reader = parser(b"12 12", bank)
    first = reader.read()
    before = bank.bytes
    # Compact whitespace scan 64 + word scan 64 + fresh SourceToken 512.
    bank.byte_limit = before + 640
    second = reader.read()
    assert calls == [b"12"]
    assert first is not second and first.value is second.value
    assert (first.kind, first.offset, first.length) == ("number", 0, 2)
    assert (second.kind, second.offset, second.length) == ("number", 3, 2)
    assert bank.bytes - before == 640
    with pytest.raises(FrozenInstanceError):
        first.offset = 3


@pytest.mark.parametrize("word", [b"12345678", b"123456789"])
def test_cache_length_boundary_preserves_longer_words(word):
    reader = parser(word + b" " + word)
    first, second = reader.read(), reader.read()
    assert first.value == second.value == word
    assert (first.value is second.value) is (len(word) == 8)


def test_full_bytes_are_compared_on_a_slot_collision():
    reader = parser(b"12 13 13 12")
    values = [reader.read().value for _ in range(4)]
    assert values == [b"12", b"13", b"13", b"12"]
    assert values[1] is values[2]
    assert values[0] is not values[3]
    # Different keys also collide under the fixed mapping.
    assert (97 + 13 * 2) & 63 == (84 + 13 * 3) & 63
    reader = parser(b"aa Tzz aa Tzz")
    assert [reader.read().value for _ in range(4)] == ["aa", "Tzz", "aa", "Tzz"]


def test_spelling_and_context_are_preserved_on_each_occurrence():
    raw = b"-0 -.0 +0 0.0 -0 -.0 +0 0.0"
    records = []
    parser(raw).consume(lambda *arguments: records.append(arguments))
    assert [record[1] for record in records] == raw.split()
    assert [record[2] for record in records] == [0, 3, 7, 10, 14, 17, 21, 24]
    calls = []

    def consume(*arguments):
        calls.append(arguments)
        if len(calls) == 2:
            raise UnsupportedExactSource("constructed_context_refusal")

    with pytest.raises(UnsupportedExactSource, match="constructed_context_refusal"):
        parser(b"XX XX").consume(consume)
    assert calls == [("operator", "XX", 0, 2), ("operator", "XX", 3, 2)]


def test_names_strings_arrays_and_invalid_bytes_keep_their_paths():
    reader = parser(b"/Ab /Ab (Ab) (Ab) [12 12]")
    first, second, third, fourth, array = [reader.read() for _ in range(5)]
    assert first.kind == second.kind == "name"
    assert third.kind == fourth.kind == "string"
    assert third.value == fourth.value == b"Ab" and third.value is not fourth.value
    assert array.value[0] is not array.value[1]
    assert array.value[0].value is array.value[1].value
    for byte in range(128, 256):
        reader = parser(b"ab a" + bytes([byte]))
        reader.read()
        with pytest.raises(UnsupportedExactSource, match="bare_token_byte"):
            reader.read()


@pytest.mark.parametrize("word", [b"true", b"false", b"null"])
def test_unsupported_words_are_never_cached(word):
    reader = parser(b"12 " + word)
    reader.read()
    with pytest.raises(UnsupportedExactSource, match="non_numeric_operand"):
        reader.read()


def test_caller_token_limit_precedes_reuse():
    reader = parser(b"12 12")
    reader.read()
    reader.limits = replace(reader.limits, max_token_bytes=1)
    with pytest.raises(ExactSourceLimit) as caught:
        reader.read()
    assert caught.value.limit == "token_bytes" and caught.value.found == 2


def test_cache_entry_is_prepaid_immutable_and_local_to_its_parser():
    bank = Budget(8_388_608, 655_360)
    reader = parser(b"12 ", bank)
    first = reader.read()
    other = parser(b"12 ")
    assert other.read().value is not first.value
    assert reader._scalar_cache is not other._scalar_cache
    slot = (49 + 13 * 2) & 63
    entry = reader._scalar_cache[slot]
    assert type(entry) is tuple
    with pytest.raises(TypeError):
        entry[0] = b"13"
    before = bank.bytes
    bank.byte_limit = before + 127
    with pytest.raises(Exhausted, match="bytes"):
        reader._remember_scalar(b"13", "number", b"13", 2)
    assert reader._scalar_cache[slot] is entry and bank.bytes == before
    bank.byte_limit = before + 256
    reader._remember_scalar(b"13", "number", b"13", 2)
    reader._remember_scalar(b"12", "number", b"12", 2)
    assert bank.bytes == before + 256 and reader._scalar_cache[slot] is not entry


def test_lookup_reuses_entry_and_retains_work_limits():
    bank = Budget(8_388_608, 655_360)
    reader = parser(b"12 12", bank)
    reader.read()
    operations = {instruction.opname for instruction in dis.get_instructions(reader._cached_scalar)}
    assert not operations & {"BUILD_LIST", "BUILD_TUPLE", "BUILD_SLICE", "GET_ITER"}
    before = bank.bytes
    bank.work_limit = bank.work + 10
    assert reader._cached_scalar(3, 5, 2, 49) is reader._scalar_cache[(49 + 13 * 2) & 63]
    assert bank.bytes == before
    bank.work_limit = bank.work + 9
    with pytest.raises(Exhausted, match="work"):
        reader._cached_scalar(3, 5, 2, 49)


def test_source_state_rejection_and_byte_built_issuer_are_preserved():
    with pytest.raises(UnsupportedExactSource, match="paint_path_domain"):
        source_ledger(
            (((1, 0), b"12 12 m 40 12 l S S"),),
            Budget(8_388_608, 655_360),
            limits=source_limits(),
        )
    paths, images, authority = issued(
        b"12 12 m 40 12 l S 12 24 m 40 24 l S",
        source_limits(),
        Budget(8_388_608, 655_360),
    )
    assert len(paths) == 2 and images == () and authority is not None
    assert [(segment.end.x, segment.end.y) for segment in paths[0].subpaths[0].segments] == [
        (12, 12),
        (40, 12),
    ]
    assert [(segment.end.x, segment.end.y) for segment in paths[1].subpaths[0].segments] == [
        (12, 24),
        (40, 24),
    ]
