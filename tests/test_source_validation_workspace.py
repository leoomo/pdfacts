"""Constructed payload trees pin complete traversal and batch-local storage."""

from dataclasses import replace

import pytest

from pdfacts.backends import analysis_identity as identity
from pdfacts.backends.exact_stream import ExactSourceLimit, UnsupportedExactSource
from test_source_evidence import Budget, Exhausted, source_limits


@pytest.mark.parametrize("value", [(1, (2, (3,))), ((), (), ()), ((1,) * 256, (2,) * 256)])
def test_small_tuple_validation_keeps_one_length_allowance(value):
    bank = Budget(2112, 10000)
    assert identity._validate_evidence_payload(value, bank, source_limits()) is None
    assert bank.bytes == 2112
    with pytest.raises(Exhausted, match="bytes"):
        identity._validate_evidence_payload(value, Budget(2111, 10000), source_limits())


@pytest.mark.parametrize("count", [257, 512, 1024])
def test_large_tuples_replenish_before_counter_arithmetic(count):
    value = (tuple(range(count)), (1,), tuple(range(count)), ())
    expected = 2048 + 64 + 2 * (128 + 32 * count)
    bank = Budget(expected, 100000)
    assert identity._validate_evidence_payload(value, bank, source_limits()) is None
    assert bank.bytes == expected
    with pytest.raises(Exhausted, match="bytes"):
        identity._validate_evidence_payload(value, Budget(expected - 1, 100000), source_limits())


def test_workspace_reuse_clears_parent_references_and_validates_later_payload():
    bank = Budget(100000, 10000)
    validate = identity._make_payload_validator(bank)
    assert bank.bytes == 2048
    for value in (((1,), (2, (3,))), (), ((4,),), 5):
        before = bank.bytes
        assert validate(value, source_limits()) is None
        assert bank.bytes - before == 64
        cells = dict(
            zip(
                validate.__code__.co_freevars,
                (cell.cell_contents for cell in validate.__closure__),
                strict=True,
            )
        )
        assert cells["parents"] == [None] * 33
    with pytest.raises(UnsupportedExactSource, match="path_evidence_payload_domain"):
        validate((1, ([],)), source_limits())


@pytest.mark.parametrize("case", ["depth", "nonfinite", "type"])
@pytest.mark.parametrize("deny", [False, True])
def test_validator_refusal_has_independent_prepaid_inventory(case, deny):
    bank = Budget(100000, 10000)
    limits = replace(source_limits(), max_payload_depth=1)
    value, depth = (
        (None, 2) if case == "depth" else (float("nan"), 0) if case == "nonfinite" else ([], 0)
    )
    reserve = bank.reserve
    costs = []
    sentinel = Exhausted("payload refusal")

    def observed(cost):
        costs.append(cost)
        if cost == 4096:
            if deny:
                raise sentinel
            object.__setattr__(limits, "max_payload_depth", 32)
        reserve(cost)

    bank.reserve = observed
    with pytest.raises(
        Exhausted if deny else ExactSourceLimit if case == "depth" else UnsupportedExactSource
    ) as caught:
        identity._validate_evidence_payload(value, bank, limits, depth)
    assert costs == [2048, 64, 4096]
    if deny:
        assert caught.value is sentinel
    elif case == "depth":
        assert (caught.value.found, caught.value.allowed) == (2, 1)


def test_reentrant_validation_owns_distinct_workspace():
    bank = Budget(100000, 10000)
    outer = identity._make_payload_validator(bank)
    charge = bank.charge
    entered = False
    workspaces = []

    def observed(cost=1):
        nonlocal entered
        charge(cost)
        if not entered:
            entered = True
            inner = identity._make_payload_validator(bank)
            for validator in (outer, inner):
                cells = dict(
                    zip(
                        validator.__code__.co_freevars,
                        (cell.cell_contents for cell in validator.__closure__),
                        strict=True,
                    )
                )
                workspaces.append(cells["parents"])
            inner(((7,), (8,)), source_limits())

    bank.charge = observed
    outer((1, (2,)), source_limits())
    assert entered
    assert workspaces[0] is not workspaces[1]
    assert workspaces == [[None] * 33, [None] * 33]


def test_validator_frame_inventory_includes_closure_slots():
    validate = identity._make_payload_validator(Budget(100000, 10000))
    for function in (validate, identity._validate_evidence_payload):
        code = function.__code__
        slots = code.co_nlocals + len(code.co_cellvars) + len(code.co_freevars) + code.co_stacksize
        assert 208 + 8 * slots <= 512


def test_factory_prepayment_precedes_cell_allocating_function(monkeypatch):
    import dis

    bank = Budget(2047, 10000)
    calls = []

    def unexpected(bank):
        calls.append(True)
        raise AssertionError("cell factory entered before prepayment")

    assert identity._make_payload_validator.__code__.co_cellvars == ()
    assert not any(
        op.opname == "MAKE_CELL" for op in dis.get_instructions(identity._make_payload_validator)
    )
    monkeypatch.setattr(identity, "_new_payload_validator", unexpected)
    with pytest.raises(Exhausted, match="bytes"):
        identity._make_payload_validator(bank)
    assert calls == []


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("corrupt_later", [False, True])
def test_production_batch_reuses_validator_without_skipping_later_payload(
    monkeypatch, staged, corrupt_later
):
    import pdfacts
    from pdfacts.core.errors import SourceEvidenceError
    from pdfacts.graphics import source_evidence as feature
    from test_source_evidence import source_file
    from test_staged_source_evidence import bind

    data = source_file(b"10 20 m 90 20 l S 10 30 m 90 30 l S")
    bank = Budget(8388608, 655360)
    limits = source_limits()
    make = identity._make_payload_validator
    batches = []

    def observed(bank):
        validate = make(bank)
        calls = []
        batches.append(calls)

        def checked(value, limits, depth=0):
            calls.append(True)
            if corrupt_later and len(calls) == 2:
                value = ([],)
            return validate(value, limits, depth)

        return checked

    monkeypatch.setattr(identity, "_make_payload_validator", observed)
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        if staged:
            paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

            def complete():
                return feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        else:

            def complete():
                return feature.issue_root_path_evidence(doc, 0, bank, limits=limits)[2]

        if corrupt_later:
            with pytest.raises(SourceEvidenceError, match="path_evidence_payload_domain"):
                complete()
            assert identity._path_batches.get(owner) is None
        else:
            assert complete() is not None
            assert len(identity._path_batches[owner]) == 1
        assert batches == [[True, True]]
