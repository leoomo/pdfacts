"""Constructed scalar copies pin cached fee arithmetic and prepaid refusals."""

import pytest

from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.finite_content import ContentParser
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("size,amount", [(2, 1160), (8, 1184), (16, 1088), (17, 1348)])
@pytest.mark.parametrize("numeric", [False, True])
def test_scalar_copy_formula_exact_boundary(size, amount, numeric):
    word = (b"1" if numeric else b"x") * size
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(word, bank, source_limits())
    reader.load_window(0)
    before = bank.bytes
    bank.byte_limit = before + amount
    token = reader.read()
    assert token.kind == ("number" if numeric else "operator")
    assert token.value == (word if numeric else word.decode("ascii"))
    assert (token.offset, token.length) == (0, size)
    assert bank.bytes - before == amount


@pytest.mark.parametrize("word,expected", [(b"/Ab", "/Ab"), (b"/A#42", b"/A#42"), (b"/", b"/")])
def test_plain_escaped_and_repeated_names_keep_lexical_identity(word, expected):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(word + b" " + word, bank, source_limits())
    reader.load_window(0)
    reserve = bank.reserve
    costs = []

    def observed(cost):
        costs.append(cost)
        reserve(cost)

    bank.reserve = observed
    first = reader.read()
    assert costs[:2] == [64, 192 + 4 * len(word)]
    costs.clear()
    second = reader.read()
    assert costs == [64, 64, 192 + 4 * len(word), 512]
    assert first.value == second.value == expected
    assert first.value is second.value and first is not second
    assert (first.offset, first.length) == (0, len(word))
    assert (second.offset, second.length) == (len(word) + 1, len(word))


@pytest.mark.parametrize("word", [b"true", b"false", b"null"])
@pytest.mark.parametrize("deny_restoration", [False, True])
def test_reserved_literals_restore_original_fee_before_error(word, deny_restoration):
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(word, bank, source_limits())
    reader.load_window(0)
    reserve = bank.reserve
    failure = Exhausted("literal restoration")
    costs, emitted = [], []

    def observed(cost):
        costs.append(cost)
        if cost == 256 and deny_restoration:
            raise failure
        reserve(cost)

    bank.reserve = observed
    with pytest.raises(Exhausted if deny_restoration else UnsupportedExactSource) as caught:
        reader.read(consumer=lambda *args: emitted.append(args))
    if deny_restoration:
        assert caught.value is failure
    else:
        assert str(caught.value) == "non_numeric_operand"
    assert costs == [64, 192 + 4 * len(word), 256]
    assert emitted == []


def test_short_copy_exact_budget_one_below_refuses_without_token():
    bank = Budget(8_388_608, 655_360)
    reader = ContentParser(b"x" * 16, bank, source_limits())
    reader.load_window(0)
    # The ordinary callback uses 256 rather than the token object's 512.
    bank.byte_limit = bank.bytes + 1088 - 256 - 1
    emitted = []
    with pytest.raises(Exhausted, match="bytes"):
        reader.read(consumer=lambda *args: emitted.append(args))
    assert emitted == []
