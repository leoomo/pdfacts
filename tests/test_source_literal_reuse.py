"""Constructed literal strings distinguish byte reuse from actual decoding."""

from dataclasses import replace

import pytest

from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


def reader_for(body):
    raw = b"(" + body + b")"
    bank = Budget(8388608, 655360)
    limits = replace(source_limits(), max_token_bytes=20000)
    return ContentParser(raw, bank, limits), bank, raw


@pytest.mark.parametrize("count", [0, 1, 253, 254, 255, 256, 257, 512, 2560, 2816])
def test_plain_literal_exact_fees_and_one_below(count):
    body = b"a" * count
    reader, bank, raw = reader_for(body)
    expected_bytes = 768 + count
    block_size = 256 if count >= 254 else count + 2
    expected_work = 2 * count + 9 * (count // block_size) + 2
    before_bytes, before_work = bank.bytes, bank.work
    bank.byte_limit = bank.bytes + expected_bytes
    bank.work_limit = bank.work + expected_work
    assert reader.decode_literal(0, len(raw)) == body
    assert bank.bytes - before_bytes == expected_bytes
    assert bank.work - before_work == expected_work
    for dimension in ("bytes", "work"):
        reader, bank, raw = reader_for(body)
        if dimension == "bytes":
            bank.byte_limit = bank.bytes + expected_bytes - 1
        else:
            bank.work_limit = bank.work + expected_work - 1
        with pytest.raises(Exhausted, match=dimension):
            reader.decode_literal(0, len(raw))


@pytest.mark.parametrize("body", [b"", b"a\nb", b"\x00\x80\xff", b"a(b(c)d)e", b"x" * 257])
def test_plain_literal_skips_chunk_builder_after_lexical_validation(monkeypatch, body):
    reader, bank, raw = reader_for(body)

    def unexpected(*args):
        raise AssertionError("plain literal reached chunk builder")

    monkeypatch.setattr(reader, "finish_decoded", unexpected)
    token = reader.read()
    assert (token.kind, token.value, token.offset, token.length) == ("string", body, 0, len(raw))


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"a\rb", b"a\nb"),
        (b"a\r\nb", b"a\nb"),
        (b"a\\nb", b"a\nb"),
        (b"a\\\r\nb", b"ab"),
        (b"\\101\\(\\)", b"A()"),
        (b"x" * 255 + b"\\101y", b"x" * 255 + b"Ay"),
        (b"x" * 255 + b"\r\ny", b"x" * 255 + b"\ny"),
    ],
)
def test_transformed_literal_retains_original_decoder(monkeypatch, body, expected):
    reader, bank, raw = reader_for(body)
    finish = reader.finish_decoded
    calls = []

    def observed(*args):
        calls.append(True)
        return finish(*args)

    monkeypatch.setattr(reader, "finish_decoded", observed)
    token = reader.read()
    assert token.value == expected
    assert calls == [True]


@pytest.mark.parametrize(
    "raw,reason", [(b"(abc", "literal_string_unclosed"), (b"(abc\\", "literal_string_escape")]
)
def test_lexical_refusal_precedes_reuse(monkeypatch, raw, reason):
    reader = ContentParser(raw, Budget(8388608, 655360), source_limits())
    calls = []

    def unexpected(*args):
        calls.append(True)
        raise AssertionError("unvalidated literal reached decoder")

    monkeypatch.setattr(reader, "decode_literal", unexpected)
    with pytest.raises(UnsupportedExactSource, match=reason):
        reader.read()
    assert calls == []


def test_token_limit_precedes_reuse(monkeypatch):
    reader = ContentParser(
        b"(1234)", Budget(8388608, 655360), replace(source_limits(), max_token_bytes=5)
    )
    calls = []

    def unexpected(*args):
        calls.append(True)
        raise AssertionError("over-limit literal reached decoder")

    monkeypatch.setattr(reader, "decode_literal", unexpected)
    with pytest.raises(ExactSourceLimit) as caught:
        reader.read()
    assert caught.value.limit == "token_bytes"
    assert calls == []


@pytest.mark.parametrize("body", [b"plain", b"a\\nb"])
def test_membership_scans_prepaid_before_branch_selection(body):
    reader, bank, raw = reader_for(body)
    costs = []
    sentinel = Exhausted("scan work")

    def denied(cost):
        costs.append(cost)
        raise sentinel

    bank.charge = denied
    with pytest.raises(Exhausted) as caught:
        reader.decode_literal(0, len(raw))
    assert caught.value is sentinel
    assert costs == [2 * len(body)]
