"""Constructed private token values retain their value and copy contracts."""

import copy
import pickle
from dataclasses import FrozenInstanceError

import pytest

from pdfacts.backends import finite_content
from pdfacts.backends.finite_content import ContentParser, SourceToken
from test_source_evidence import Budget, Exhausted, source_limits


def test_token_named_fields_equality_hash_and_pattern():
    token = SourceToken(kind="number", value=b"12", offset=300, length=2)
    assert (token.kind, token.value, token.offset, token.length) == ("number", b"12", 300, 2)
    assert token == SourceToken("number", b"12", 300, 2)
    assert hash(token) == hash(("number", b"12", 300, 2))
    assert repr(token) == "SourceToken(kind='number', value=b'12', offset=300, length=2)"
    match token:
        case SourceToken("number", b"12", 300, 2):
            pass
        case _:
            pytest.fail("positional pattern did not match")
    assert not hasattr(token, "__dict__")


@pytest.mark.parametrize("name", ["kind", "value", "offset", "length", "_payload"])
def test_token_remains_frozen(name):
    token = SourceToken("number", b"12", 300, 2)
    with pytest.raises(FrozenInstanceError):
        setattr(token, name, None)
    with pytest.raises(FrozenInstanceError):
        delattr(token, name)


@pytest.mark.parametrize("method", [copy.copy, copy.deepcopy, pickle.loads])
def test_token_copy_protocol(method):
    value = [b"one"]
    token = SourceToken("string", value, 300, 20)
    result = method(pickle.dumps(token)) if method is pickle.loads else method(token)
    assert result == token
    assert result is not token
    assert (result.kind, result.offset, result.length) == ("string", 300, 20)
    assert (result.value is value) is (method is copy.copy)


@pytest.mark.parametrize("method", [copy.deepcopy, pickle.loads])
def test_token_recursive_copy_and_repr(method):
    value = []
    token = SourceToken("array", value, 0, 3)
    value.append(token)
    result = method(pickle.dumps(token)) if method is pickle.loads else method(token)
    assert result.value[0] is result
    assert repr(result) == "SourceToken(kind='array', value=[...], offset=0, length=3)"


def test_materialized_token_prepaid_exact_boundary(monkeypatch):
    bank = Budget(8_388_608, 655_360)
    parser = ContentParser(b"1", bank, source_limits())
    parser.index = 1
    bank.byte_limit = bank.bytes + 512
    token = parser.token("number", b"1", 0, None)
    assert token == SourceToken("number", b"1", 0, 1)

    calls = []

    def observed(*arguments):
        calls.append(arguments)
        return SourceToken(*arguments)

    monkeypatch.setattr(finite_content, "SourceToken", observed)
    assert finite_content.SourceToken is observed
    bank.byte_limit = bank.bytes + 511
    with pytest.raises(Exhausted, match="bytes"):
        parser.token("number", b"1", 0, None)
    assert calls == []
