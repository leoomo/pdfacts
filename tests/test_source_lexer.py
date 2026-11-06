"""Synthetic lexical and issuer cases with construction-defined byte values."""

from dataclasses import replace

import pytest

import pdfacts
from pdfacts.backends.analysis_identity import classification_identity
from pdfacts.backends.exact_stream import ExactSourceLimit
from pdfacts.backends.finite_content import ContentParser
from pdfacts.graphics.source_evidence import issue_root_path_evidence
from synthetic.builder import PageSpec, document
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("size", [255, 256, 257, 1024, 1025, 2560, 2561, 2816, 4096, 6000])
@pytest.mark.parametrize("hexadecimal", [False, True])
def test_large_decoded_value_and_exact_budget(size, hexadecimal):
    expected = b"A" * size
    token = b"<" + b"41" * size + b">" if hexadecimal else b"(" + expected + b")"
    limits = replace(source_limits(), max_token_bytes=len(token))
    bank = Budget(8_388_608, 655_360)
    result = ContentParser(token, bank, limits).read()
    assert (result.kind, result.value, result.offset, result.length) == (
        "string",
        expected,
        0,
        len(token),
    )
    assert ContentParser(token, Budget(bank.bytes, bank.work), limits).read().value == expected
    with pytest.raises(Exhausted, match="bytes"):
        ContentParser(token, Budget(bank.bytes - 1, bank.work), limits).read()
    with pytest.raises(ExactSourceLimit):
        ContentParser(
            token, Budget(8_388_608, 655_360), replace(limits, max_token_bytes=len(token) - 1)
        ).read()


@pytest.mark.parametrize("depth", [255, 256, 257, 300])
def test_literal_uncached_depth(depth):
    token = b"(" * depth + b"A" + b")" * depth
    limits = replace(source_limits(), max_token_depth=depth)
    bank = Budget(8_388_608, 655_360)
    assert ContentParser(token, bank, limits).read().value == b"(" * (depth - 1) + b"A" + b")" * (
        depth - 1
    )
    with pytest.raises(ExactSourceLimit) as caught:
        ContentParser(
            token, Budget(8_388_608, 655_360), replace(limits, max_token_depth=depth - 1)
        ).read()
    assert caught.value.limit == "token_depth"


@pytest.mark.parametrize(
    "token,depth",
    [
        (b"(" + b"A" * 4096 + b")", 32),
        (b"(" + b"A" * 6000 + b")", 32),
        (b"<" + b"41" * 3000 + b">", 32),
        (b"(" * 300 + b"A" + b")" * 300, 300),
    ],
)
def test_large_token_through_byte_built_issuer(token, depth):
    content = b"BT /F1 10 Tf 10 40 Td " + token + b" Tj ET 2 w 10 20 m 90 20 l S"
    data = document(
        [PageSpec(b"/MediaBox [0 0 200 200]", content)],
        font=b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    )
    bank = Budget(8_388_608, 655_360)
    limits = replace(source_limits(), max_token_bytes=max(1024, len(token)), max_token_depth=depth)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, images, authority = issue_root_path_evidence(opened, 0, bank, limits=limits)
        assert len(paths) == 1
        assert images == ()
        assert authority is not None


def test_three_full_whitespace_streams_use_bounded_windows():
    bank = Budget(8_388_608, 655_360)
    for _ in range(3):
        parser = ContentParser(b" " * 65_536, bank, source_limits())
        assert parser.read() is None
        assert parser.index == 65_536
    assert bank.bytes < 1_000_000
    assert bank.work < 655_360


def test_whitespace_raw_length_is_not_reallocated_per_byte(monkeypatch):
    import builtins

    from pdfacts.backends import finite_content

    raw = b" " * 65_536
    reads = 0

    def counted_length(value):
        nonlocal reads
        if value is raw:
            reads += 1
        return builtins.len(value)

    monkeypatch.setattr(finite_content, "len", counted_length, raising=False)
    assert finite_content.len is counted_length
    bank = Budget(8_388_608, 655_360)
    for _ in range(3):
        assert ContentParser(raw, bank, source_limits()).read() is None
    assert reads == 3
