"""Constructed names exercise an explicitly selected callback accounting domain."""

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize(
    "kind,value,end,discounted",
    [
        ("name", "/A", 2, True),
        ("name", "/ABCDEFG", 8, True),
        ("name", "/ABCDEFGH", 9, False),
        ("name", "/A", 9, False),
        ("name", b"/A#42", 5, False),
        ("name", "", 2, False),
        ("name", "/A", True, False),
        ("name", "/A", 20, False),
        ("operator", "q", 1, False),
        ("string", b"a", 3, False),
        ("array", (), 2, False),
        ("number", b"12", 2, True),
    ],
)
def test_name_selector_pairs_actual_fee(kind, value, end, discounted):
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        b"/ABCDEFGH",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
        consumer_short_names=True,
    )
    reader.index = end
    before = bank.bytes
    calls = []
    reader.token(kind, value, 0, lambda *args: calls.append(args))
    assert calls == [(kind, value, 0, end, discounted)]
    assert bank.bytes - before == (64 if discounted else 256)


@pytest.mark.parametrize("endpoint,numbers", [(False, False), (True, False), (True, True)])
def test_name_discount_is_explicit(endpoint, numbers):
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        b"/A",
        bank,
        source_limits(),
        consumer_end_offsets=endpoint,
        consumer_short_numbers=numbers,
    )
    reader.index = 2
    before = bank.bytes
    calls = []
    reader.token("name", "/A", 0, lambda *args: calls.append(args))
    assert calls == [("name", "/A", 0, 2, False) if numbers else ("name", "/A", 0, 2)]
    assert bank.bytes - before == 256


@pytest.mark.parametrize("names,numbers", [(True, False), (1, True), (None, True)])
def test_name_mode_requires_the_five_argument_channel(names, numbers):
    with pytest.raises(UnsupportedExactSource, match="content_consumer_mode"):
        ContentParser(
            b"/A",
            Budget(8388608, 655360),
            source_limits(),
            consumer_end_offsets=True,
            consumer_short_numbers=numbers,
            consumer_short_names=names,
        )


def test_name_mode_and_span_are_frozen_before_reservation():
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        b"/Axxxx",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
        consumer_short_names=True,
    )
    reader.index = 2
    reserve = bank.reserve
    costs, calls = [], []

    def changed(cost):
        costs.append(cost)
        reserve(cost)
        reader.consumer_short_names = False
        reader.consumer_short_numbers = False
        reader.index = 6
        reader.raw_length = 1073741824

    bank.reserve = changed
    reader.token("name", "/A", 0, lambda *args: calls.append(args))
    assert costs == [64]
    assert calls == [("name", "/A", 0, 2, True)]


def test_name_selector_one_below_prevents_callback():
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        b"/A",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
        consumer_short_names=True,
    )
    reader.index = 2
    bank.byte_limit = bank.bytes + 63
    calls = []
    with pytest.raises(Exhausted, match="bytes"):
        reader.token("name", "/A", 0, lambda *args: calls.append(args))
    assert calls == []


@pytest.mark.parametrize("deny", [False, True])
@pytest.mark.parametrize(
    "change,reason",
    [
        ("span", "source_token_span"),
        ("kind", "non_numeric_operand"),
        ("operator", "operator_domain"),
        ("seventh", "non_numeric_operand"),
    ],
)
def test_name_consumer_restores_before_refusal(monkeypatch, change, reason, deny):
    bank = Budget(8388608, 655360)
    consume, reserve = ContentParser.consume, bank.reserve
    inside = False
    flags, costs = [], []
    sentinel = Exhausted("name restoration")

    def observed(cost):
        if inside:
            costs.append(cost)
            if deny:
                raise sentinel
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal inside
            if kind == "name":
                flags.append(discounted)
                if change != "seventh" or len(flags) == 7:
                    inside = True
                    if change == "span":
                        end = start
                    elif change == "kind":
                        kind = "boolean"
                    elif change == "operator":
                        kind, value = "operator", "zz"
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    raw = b"/A /B /C /D /E /F /G" if change == "seventh" else b"/A"
    with pytest.raises(Exhausted if deny else UnsupportedExactSource) as caught:
        source_ledger((((1, 0), raw),), bank, limits=source_limits())
    assert flags == [True] * (7 if change == "seventh" else 1)
    assert costs == [192]
    if deny:
        assert caught.value is sentinel
    else:
        assert str(caught.value) == reason


@pytest.mark.parametrize("replacement", ["/ABCDEFGHI", b"/A#42"])
@pytest.mark.parametrize("deny", [False, True])
def test_name_leaving_value_domain_restores_once_before_append(monkeypatch, replacement, deny):
    bank = Budget(8388608, 655360)
    consume, reserve = ContentParser.consume, bank.reserve
    inside = False
    flags, costs = [], []
    sentinel = Exhausted("name restoration")
    stopped = RuntimeError("stopped after original consumer returned")

    def observed(cost):
        if inside:
            costs.append(cost)
            if deny:
                raise sentinel
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal inside
            flags.append(discounted)
            inside = True
            consumer(kind, replacement, start, end, discounted)
            # This probes only the existing name append, not resource acceptance.
            raise stopped

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(RuntimeError) as caught:
        source_ledger((((1, 0), b"/A"),), bank, limits=source_limits())
    assert flags == [True]
    assert costs == ([192] if deny else [192, 64])
    assert caught.value is (sentinel if deny else stopped)
