"""Constructed source-state transitions pin fees and refusal ordering."""

from dataclasses import replace

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from pdfacts.backends.exact_text import ExactTextState
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize(
    "value,selected",
    [("BT", True), ("ET", True), ("EMC", True), ("Tm", False), ("q", False), ("BDC", False)],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_state_mode_explicit_selection(value, selected, enabled):
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        value.encode(),
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
        consumer_short_states=enabled,
    )
    reader.index = len(value)
    before = bank.bytes
    calls = []
    reader.token("operator", value, 0, lambda *args: calls.append(args))
    actual = enabled and selected
    assert calls == [("operator", value, 0, len(value), actual)]
    assert bank.bytes - before == (64 if actual else 256)


@pytest.mark.parametrize("mode,numbers", [(True, False), (1, True), (None, True)])
def test_state_mode_requires_actual_boolean_and_five_argument_channel(mode, numbers):
    with pytest.raises(UnsupportedExactSource, match="content_consumer_mode"):
        ContentParser(
            b"BT",
            Budget(8388608, 655360),
            source_limits(),
            consumer_end_offsets=True,
            consumer_short_numbers=numbers,
            consumer_short_states=mode,
        )


def test_state_mode_frozen_before_callback_and_one_below():
    bank = Budget(8388608, 655360)
    reader = ContentParser(
        b"BT",
        bank,
        source_limits(),
        consumer_end_offsets=True,
        consumer_short_numbers=True,
        consumer_short_states=True,
    )
    reader.index = 2
    reserve = bank.reserve
    calls, costs = [], []

    def changed(cost):
        costs.append(cost)
        reserve(cost)
        reader.consumer_short_states = False
        reader.index = 1
        reader.raw_length = 1073741824

    bank.reserve = changed
    reader.token("operator", "BT", 0, lambda *args: calls.append(args))
    assert costs == [64]
    assert calls == [("operator", "BT", 0, 2, True)]
    reader.consumer_short_states = True
    reader.index, reader.raw_length = 2, 2
    bank.byte_limit = bank.bytes + 63
    with pytest.raises(Exhausted, match="bytes"):
        reader.token("operator", "BT", 0, lambda *args: calls.append(args))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw,target,reason",
    [
        (b"BT BT", 2, "text_nested"),
        (b"ET", 1, "text_outside_object"),
        (b"1 BT", 1, "text_operator_shape"),
        (b"BT 1 ET", 2, "text_operator_shape"),
        (b"EMC", 1, "marked_underflow"),
        (b"/A EMC", 1, "marked_operator_shape"),
        (b"BT EMC", 2, "graphics_inside_text"),
        (b"0 0 10 10 re W BT", 1, "clip_pending"),
    ],
)
@pytest.mark.parametrize("deny", [False, True])
def test_state_refusals_restore_before_raising(monkeypatch, raw, target, reason, deny):
    bank = Budget(8388608, 655360)
    consume, reserve = ContentParser.consume, bank.reserve
    active = False
    calls, costs = [], []
    sentinel = Exhausted("state restoration")

    def observed(cost):
        if active:
            costs.append(cost)
            if deny:
                raise sentinel
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal active
            if kind == "operator" and value in ("BT", "ET", "EMC"):
                calls.append(discounted)
                active = len(calls) == target
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(Exhausted if deny else UnsupportedExactSource) as caught:
        source_ledger((((1, 0), raw),), bank, limits=source_limits())
    assert calls == [True] * target
    assert costs == [192]
    assert caught.value is sentinel if deny else str(caught.value) == reason


def test_instruction_limit_keeps_selected_found_and_allowed(monkeypatch):
    bank = Budget(8388608, 655360)
    limits = replace(source_limits(), max_instructions=1)
    consume, reserve = ContentParser.consume, bank.reserve
    active = False
    current = None
    costs = []

    def observed(cost):
        if active:
            costs.append(cost)
            object.__setattr__(limits, "max_instructions", 100)
            current.index = 0
        reserve(cost)

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            nonlocal active, current
            if value == "ET":
                assert discounted is True
                active, current = True, consumer.__self__
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(ExactSourceLimit) as caught:
        source_ledger((((1, 0), b"BT ET"),), bank, limits=limits)
    assert (caught.value.found, caught.value.allowed) == (2, 1)
    assert costs == [192]


def test_large_marked_depth_and_instruction_counter(monkeypatch):
    class ConstructedResources:
        def marked_property(self, tag, name, bank):
            assert (tag, name) == ("/P", "/M")

    bank = Budget(8388608, 655360)
    consume = ContentParser.consume
    transitions = []

    def intercept(self, consumer):
        def changed(kind, value, start, end, discounted):
            if value == "EMC":
                assert discounted is True
                parser = consumer.__self__
                depth, index, before = parser.marked_depth, parser.index, bank.bytes
                result = consumer(kind, value, start, end, discounted)
                transitions.append(
                    (depth, parser.marked_depth, index, parser.index, bank.bytes - before)
                )
                return result
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    monkeypatch.setattr(ContentParser, "consume", intercept)
    raw = b"/P /M BDC " * 258 + b"EMC " * 258
    assert (
        source_ledger(
            (((1, 0), raw),),
            bank,
            ConstructedResources(),
            limits=replace(source_limits(), max_marked_depth=300),
        )
        == ()
    )
    # Operand list identity is retained; no replacement list is allocated here.
    assert transitions[0] == (258, 257, 258, 259, 0)
    assert transitions[1] == (257, 256, 259, 260, 0)
    assert len(transitions) == 258


@pytest.mark.parametrize(
    "change,reason",
    [
        ("span", "source_token_span"),
        ("kind", "non_numeric_operand"),
        ("operator", "operator_domain"),
    ],
)
@pytest.mark.parametrize("deny", [False, True])
def test_state_adapter_preserves_actual_fee(monkeypatch, change, reason, deny):
    bank = Budget(8388608, 655360)
    consume, reserve = ContentParser.consume, bank.reserve
    inside = False
    flags, costs = [], []
    sentinel = Exhausted("state fallback")

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
            if change == "span":
                end = start
            elif change == "kind":
                kind = "boolean"
            else:
                value = "zz"
            return consumer(kind, value, start, end, discounted)

        return consume(self, changed)

    bank.reserve = observed
    monkeypatch.setattr(ContentParser, "consume", intercept)
    with pytest.raises(Exhausted if deny else UnsupportedExactSource) as caught:
        source_ledger((((1, 0), b"BT"),), bank, limits=source_limits())
    assert flags == [True]
    assert costs == [192]
    assert caught.value is sentinel if deny else str(caught.value) == reason


@pytest.mark.parametrize(
    "operator,active,reason", [("BT", True, "text_nested"), ("ET", False, "text_outside_object")]
)
def test_selected_text_refusal_cannot_be_repaired_by_budget_callback(operator, active, reason):
    bank = Budget(8388608, 655360)
    state = ExactTextState(None, bank, source_limits())
    state.active = active
    reserve = bank.reserve
    costs = []

    def changed(cost):
        costs.append(cost)
        reserve(cost)
        state.active = not active

    bank.reserve = changed
    with pytest.raises(UnsupportedExactSource, match=reason):
        state.apply(operator, [], None, True)
    assert costs == [192]


def test_direct_text_fallback_restores_before_other_operator():
    bank = Budget(8388608, 655360)
    state = ExactTextState(None, bank, source_limits())
    with pytest.raises(UnsupportedExactSource, match="text_outside_object"):
        state.apply("Td", [0, 0], None, True)
    assert bank.bytes == 192
