"""Constructed lexical arrays pin text semantics before streaming changes."""

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.exact_text import ExactTextState
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, source_limits


def array_and_state(raw, *, encoding="identity_h"):
    bank = Budget(8_388_608, 655_360)
    limits = source_limits()
    token = ContentParser(raw, bank, limits).read()
    # Unit-level text state: resource selection is covered by source issuer tests.
    state = ExactTextState(None, bank, limits)
    state.active = True
    state.font = encoding
    return token, state


@pytest.mark.parametrize(
    "raw,emitted",
    [
        (b"[]", 0),
        (b"[1 -2 0.5]", 0),
        (b"[() <>]", 0),
        (b"[(AB) 20 <4344>]", 1),
        (b"[(\\101\\102) -3 (CD)]", 1),
    ],
)
def test_array_emission_counts_nonempty_strings_only(raw, emitted):
    token, state = array_and_state(raw)
    assert state.apply("TJ", (token.value,), (token.value, token.length)) == emitted


@pytest.mark.parametrize("raw", [b"[(A)(B)]", b"[<41><42>]", b"[(AB)(C)(D)]"])
def test_identity_h_requires_even_length_of_each_string_not_total(raw):
    token, state = array_and_state(raw)
    assert sum(len(item.value) for item in token.value) % 2 == 0
    with pytest.raises(UnsupportedExactSource, match="text_identity_h_odd"):
        state.apply("TJ", (token.value,), (token.value, token.length))


@pytest.mark.parametrize("raw", [b"[(AB) /Name]", b"[(AB) q]", b"[(AB) [(CD)]]"])
def test_invalid_array_item_cannot_hide_after_a_nonempty_string(raw):
    token, state = array_and_state(raw)
    with pytest.raises(UnsupportedExactSource, match="text_array_item"):
        state.apply("TJ", (token.value,), (token.value, token.length))


@pytest.mark.parametrize(
    "state_change,reason",
    [
        ("outside", "text_outside_object"),
        ("font", "text_font_unset"),
        ("span", "text_operand_span"),
    ],
)
def test_array_does_not_bypass_state_and_operand_span(state_change, reason):
    token, state = array_and_state(b"[(AB)]")
    length = token.length
    if state_change == "outside":
        state.active = False
    elif state_change == "font":
        state.font = None
    else:
        length = 1
    with pytest.raises(UnsupportedExactSource, match=reason):
        state.apply("TJ", (token.value,), (token.value, length))


@pytest.mark.parametrize(
    "raw", [b"12 34 m", b" " * 254 + b"123 456 m", b"[(AB) 12 <4344>] TJ", b"/A#42 (a\\(b) Tj"]
)
def test_explicit_end_offset_channel_preserves_default_values_and_spans(raw):
    ordinary, endpoints = [], []
    ContentParser(raw, Budget(8_388_608, 655_360), source_limits()).consume(
        lambda *args: ordinary.append(args)
    )
    ContentParser(
        raw, Budget(8_388_608, 655_360), source_limits(), consumer_end_offsets=True
    ).consume(lambda *args: endpoints.append(args))
    assert [
        (kind, value, start, start + length) for kind, value, start, length in ordinary
    ] == endpoints


def test_endpoint_channel_reuses_the_existing_non_small_index_object():
    reader = ContentParser(
        b" " * 300 + b"12 12 m",
        Budget(8_388_608, 655_360),
        source_limits(),
        consumer_end_offsets=True,
    )
    observed = []

    def receive(kind, value, start, end):
        assert end > 256
        assert end is reader.index
        observed.append((start, end))

    reader.consume(receive)
    assert observed == [(300, 302), (303, 305), (306, 307)]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("value", "source_token_disagreement"),
        ("end", "source_token_disagreement"),
        ("empty", "source_token_span"),
        ("beyond", "source_token_span"),
        ("bool", "source_token_span"),
    ],
)
def test_source_endpoint_channel_still_authenticates_numeric_bytes(monkeypatch, change, reason):
    consume = ContentParser.consume
    hits = []

    def intercept(self, consumer):
        def changed(kind, value, start, end):
            if kind == "number" and not hits:
                hits.append(True)
                if change == "value":
                    value = b"13"
                elif change == "end":
                    end += 1
                elif change == "empty":
                    end = start
                elif change == "beyond":
                    end = self.raw_length + 1
                else:
                    end = True
            return consumer(kind, value, start, end)

        return consume(self, changed)

    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(UnsupportedExactSource, match=reason):
        source_ledger(
            (((1, 0), b" 12 12 m 40 12 l S"),), Budget(8_388_608, 655_360), limits=source_limits()
        )
    assert hits == [True]
