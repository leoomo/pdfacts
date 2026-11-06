"""Constructed streams pin invocation-local operand reuse and retained records."""

from fractions import Fraction

import pytest

from pdfacts.backends.exact_numbers import source_ledger
from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


class ConstructedResources:
    def marked_property(self, tag, name, bank):
        assert (tag, name) == ("/P", "/MC")

    def image_metadata(self, name, bank):
        assert name == "/Image"
        return ((20, 0), 1, 1, "gray", 8)


@pytest.mark.parametrize(
    "raw",
    [b"q Q", b"BT ET", b"/P /MC BDC EMC", b"/Image Do", b"1 2 m 3 4 l S"],
)
def test_operand_list_reused_across_every_success_branch(monkeypatch, raw):
    consume = ContentParser.consume
    operands = []
    seen = []

    def observe(reader, consumer):
        parser = consumer.__self__
        operands.append(parser.args)

        def token(kind, value, start, end, discounted):
            result = consumer(kind, value, start, end, discounted)
            if kind == "operator":
                seen.append(value)
                assert parser.args is operands[0]
                assert not parser.args
            return result

        return consume(reader, token)

    monkeypatch.setattr(ContentParser, "consume", observe)
    source_ledger(
        (((1, 0), raw),),
        Budget(8_388_608, 655_360),
        resources=ConstructedResources(),
        with_emissions=True,
        limits=source_limits(),
    )
    assert seen
    assert operands == [[]]


def test_operand_reuse_keeps_prior_geometry_and_matrix_records():
    records = source_ledger(
        (((1, 0), b"q 2 0 0 2 10 20 cm 1 2 m 3 4 l S Q 5 6 m 7 8 l S"),),
        Budget(8_388_608, 655_360),
        limits=source_limits(),
    )
    assert len(records) == 2
    assert records[0].matrix == tuple(map(Fraction, (2, 0, 0, 2, 10, 20)))
    assert records[1].matrix == tuple(map(Fraction, (1, 0, 0, 1, 0, 0)))
    assert tuple(op[1] for op in records[0].operations) == (
        (Fraction(1), Fraction(2)),
        (Fraction(3), Fraction(4)),
    )
    assert tuple(op[1] for op in records[1].operations) == (
        (Fraction(5), Fraction(6)),
        (Fraction(7), Fraction(8)),
    )


def test_operand_workspaces_are_separate_for_nested_ledgers(monkeypatch):
    consume = ContentParser.consume
    operands = []

    def observe(reader, consumer):
        operands.append(consumer.__self__.args)
        if len(operands) == 1:
            source_ledger((((2, 0), b"BT ET"),), Budget(8_388_608, 655_360), limits=source_limits())
        return consume(reader, consumer)

    monkeypatch.setattr(ContentParser, "consume", observe)
    source_ledger((((1, 0), b"q Q"),), Budget(8_388_608, 655_360), limits=source_limits())
    assert len(operands) == 2
    assert operands[0] is not operands[1]


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"q 1 Q", "operator_shape"),
        (b"BT 1 ET", "text_operator_shape"),
        (b"1 2 m /Name 4 l S", "geometry_numeric_operand"),
        (b"1 2 m 3 4 l S 5", "incomplete_state"),
    ],
)
def test_reused_operands_keep_malformed_followup_refusals(raw, reason):
    with pytest.raises(UnsupportedExactSource, match=reason):
        source_ledger((((1, 0), raw),), Budget(8_388_608, 655_360), limits=source_limits())


def test_reused_operands_exact_budget_and_one_below():
    parts = (((1, 0), b"q 1 2 m 3 4 l S Q 5 6 m 7 8 l S"),)
    measured = Budget(8_388_608, 655_360)
    expected = source_ledger(parts, measured, limits=source_limits())
    assert len(expected) == 2
    assert (
        source_ledger(parts, Budget(measured.bytes, measured.work), limits=source_limits())
        == expected
    )
    for size, work in ((measured.bytes - 1, measured.work), (measured.bytes, measured.work - 1)):
        with pytest.raises(Exhausted):
            source_ledger(parts, Budget(size, work), limits=source_limits())
