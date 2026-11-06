"""Constructed callbacks prove actual-fee pairing and source refusal behavior."""

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("endpoint", [False, True])
def test_ordinary_callbacks_keep_four_arguments_and_full_fee(endpoint):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"12", bank, source_limits(), consumer_end_offsets=endpoint)
    reader.index = 2
    before = bank.bytes
    observed = []
    reader.token("number", b"12", 0, lambda *args: observed.append(args))
    assert observed == [("number", b"12", 0, 2)]
    assert bank.bytes - before == 256


@pytest.mark.parametrize(
    "kind,value,start,end,discounted",
    [
        ("number", b"12", 0, 2, True),
        ("number", b"12345678", 0, 8, True),
        ("number", b"123456789", 0, 9, False),
        ("number", b"12", 0, 9, False),
        ("number", "12", 0, 2, False),
        ("number", b"", 0, 2, False),
        ("number", b"12", True, 2, False),
        ("number", b"12", 0, True, False),
        ("number", b"12", 0, 20, False),
        ("operator", "q", 0, 1, False),
        ("name", "/A", 0, 2, False),
    ],
)
def test_explicit_short_number_channel_reports_actual_fee(kind, value, start, end, discounted):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(
        b"123456789",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
    )
    reader.index = end
    before = bank.bytes
    observed = []
    reader.token(kind, value, start, lambda *args: observed.append(args))
    assert observed == [(kind, value, start, end, discounted)]
    assert bank.bytes - before == (64 if discounted else 256)


def test_short_number_channel_freezes_inputs_before_budget_callback():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(
        b"12xxxx",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
    )
    reader.index = 2
    reserve = bank.reserve
    costs, observed = [], []

    def changed(cost):
        costs.append(cost)
        reserve(cost)
        reader.index = 6
        reader.raw_length = 1073741824
        reader.consumer_end_offsets = False
        reader.consumer_short_numbers = False

    bank.reserve = changed
    reader.token("number", b"12", 0, lambda *args: observed.append(args))
    assert costs == [64]
    assert observed == [("number", b"12", 0, 2, True)]


@pytest.mark.parametrize("deny_restoration", [False, True])
@pytest.mark.parametrize(
    "change,reason",
    [
        ("value", "source_token_disagreement"),
        ("long_value", "source_token_disagreement"),
        ("long_span", "source_token_disagreement"),
        ("bool_end", "source_token_span"),
        ("beyond", "source_token_span"),
        ("empty", "source_token_span"),
    ],
)
def test_changed_source_token_restores_the_fee_actually_paid(
    monkeypatch, change, reason, deny_restoration
):
    bank = Budget(8_388_608, 655_360)
    consume = ContentParser.consume
    reserve = bank.reserve
    inside = False
    hits, costs = [], []
    failure = Exhausted("restore sentinel")

    def observed(cost):
        if inside:
            costs.append(cost)
            if deny_restoration:
                raise failure
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal inside
            if kind == "number" and not hits:
                hits.append(discounted)
                if change == "value":
                    value = b"13"
                elif change == "long_value":
                    value = b"123456789"
                elif change == "long_span":
                    end = 10
                elif change == "bool_end":
                    end = True
                elif change == "beyond":
                    end = self.raw_length + 1
                else:
                    end = start
                inside = True
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(Exhausted if deny_restoration else UnsupportedExactSource) as caught:
        source_ledger((((1, 0), b"12 12 m 40 12 l S"),), bank, limits=source_limits())
    if deny_restoration:
        assert caught.value is failure
    else:
        assert str(caught.value) == reason
    assert hits == [True]
    assert costs == [192]


@pytest.mark.parametrize("short,endpoint", [(True, False), (1, True), (None, True)])
def test_short_number_channel_requires_explicit_endpoint_contract(short, endpoint):
    with pytest.raises(UnsupportedExactSource, match="content_consumer_mode"):
        ContentParser(
            b"12",
            Budget(8_388_608, 655_360),
            source_limits(),
            consumer_end_offsets=endpoint,
            consumer_short_numbers=short,
        )


def test_numeric_token_limit_refusal_restores_actual_fee(monkeypatch):
    bank = Budget(8_388_608, 655_360)
    limits = source_limits()
    consume, reserve = ContentParser.consume, bank.reserve
    inside = False
    hits, costs = [], []

    def observed(cost):
        if inside:
            costs.append(cost)
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal inside
            hits.append(discounted)
            # Inject between lexical checking and the consumer's independent
            # bound check, proving that the latter still refuses before append.
            object.__setattr__(limits, "max_token_bytes", 1)
            inside = True
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(UnsupportedExactSource, match="numeric_operand_domain"):
        source_ledger((((1, 0), b"12 12 m"),), bank, limits=limits)
    assert hits == [True]
    assert costs == [192]


def test_seventh_operand_restores_fee_before_refusal(monkeypatch):
    bank = Budget(8_388_608, 655_360)
    consume, reserve = ContentParser.consume, bank.reserve
    numbers = 0
    costs, flags = [], []

    def observed(cost):
        if numbers == 7:
            costs.append(cost)
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal numbers
            if kind == "number":
                numbers += 1
                flags.append(discounted)
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(UnsupportedExactSource, match="numeric_operand_domain"):
        source_ledger((((1, 0), b"1 2 3 4 5 6 7"),), bank, limits=source_limits())
    assert flags == [True] * 7
    assert costs == [192]


def test_short_number_selector_one_below_refuses_before_callback():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(
        b"12",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
    )
    reader.index = 2
    bank.byte_limit = bank.bytes + 63
    observed = []
    with pytest.raises(Exhausted, match="bytes"):
        reader.token("number", b"12", 0, lambda *args: observed.append(args))
    assert observed == []
