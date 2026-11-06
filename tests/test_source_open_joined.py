"""Synthetic open joined strokes; all expected geometry comes from construction."""

import pytest

import pdfacts
from pdfacts.backends.analysis_identity import classification_identity
from pdfacts.graphics import source_evidence as feature
from test_source_evidence import Budget, source_file, source_limits


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("shift", [0, -100])
def test_open_joined_stroke_associates_original_operations(staged, shift):
    # A constructed acute join and its translation exercise association only.
    content = b"5 w 0 J 0 j 10 M %d 100 m %d 104 l %d 108 l S" % (
        200 + shift,
        230 + shift,
        200 + shift,
    )
    data = source_file(content)
    bank = Budget(8_388_608, 655_360)
    limits = source_limits()
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as doc:
        with doc._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        if staged:
            paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            token = feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        else:
            paths, images, token = feature.issue_root_path_evidence(doc, 0, bank, limits=limits)
        assert len(paths) == 1 and images == ()
        path = paths[0]
        payload = feature.resolve_path_evidence(owner, handle, data, bank, token, 0, path)
        assert payload[0] == "root-association-v2"
        source = payload[5]
        assert tuple(operation[0] for operation in source[5]) == ("m", "l", "l")
        assert source[6] == "S"
        assert len(path.subpaths) == 1
        assert str(path.subpaths[0].shape) == "polyline"
        assert not path.subpaths[0].closed
        assert tuple((segment.end.x, segment.end.y) for segment in path.subpaths[0].segments) == (
            (200 + shift, 100),
            (230 + shift, 104),
            (200 + shift, 108),
        )


@pytest.mark.parametrize(
    "content, counts, closes",
    [
        (b"10 20 m 30 20 l S", (2,), (False,)),
        (b"10 20 m 30 20 l 40 20 m 60 20 l S", (2, 2), (False, False)),
        (b"10 20 m 30 20 l h S", (3,), (True,)),
        (b"10 20 m 30 20 l s", (3,), (True,)),
        (b"10 20 m 30 25 l 50 20 l 60 20 m 80 20 l S", (3, 2), (False, False)),
        (b"10 20 m 30 25 l 50 20 l 60 20 m 80 25 l 90 20 l S", (3, 3), (False, False)),
        (b"10 20 m 30 20 l 30 40 l 10 40 l 10 20 l S", (5,), (False,)),
        (b"q -1 0 0 -1 100 100 cm 10 20 m 30 25 l 50 20 l S Q", (3,), (False,)),
    ],
)
def test_open_and_existing_path_grammars_keep_segment_topology(content, counts, closes):
    from test_source_evidence import issued

    paths, _, token = issued(content, source_limits(), Budget(8_388_608, 655_360))
    assert token is not None
    assert tuple(len(s.segments) for s in paths[0].subpaths) == counts
    assert tuple(s.closed for s in paths[0].subpaths) == closes


@pytest.mark.parametrize(
    "content",
    [
        b"10 20 m 30 25 l 50 20 l h S",
        b"10 20 m 30 25 l 50 20 l s",
        b"10 20 m 30 25 l 50 20 l B",
        b"10 20 m 10 20 l 30 25 l S",
        b"16777216 20 m 16777217 20 l 16777220 25 l S",
        b"10 20 m 30 25 l 50 20 l 60 20 m S",
        b"10 20 m 20 20 m 30 25 l 50 20 l S",
        b"10 20 m 2 0 0 2 0 0 cm 30 25 l 50 20 l S",
        b"10 20 m 30 25 l 50 20 l S /F1 10 Tf",
        b"10 20 m 30 25 l 50 20 l S 60 20 m 70 30 80 30 90 20 c S",
    ],
)
def test_joined_support_does_not_filter_unsupported_original_activity(content):
    from pdfacts.core.errors import SourceEvidenceError
    from test_source_evidence import issued

    with pytest.raises(SourceEvidenceError):
        issued(content, source_limits(), Budget(8_388_608, 655_360))


@pytest.mark.parametrize(
    "mutation",
    ["coordinate", "close", "kind", "extra", "matrix", "width", "cap", "join", "stroke", "fill"],
)
def test_joined_path_keeps_native_comparison_tail(monkeypatch, mutation):
    from dataclasses import replace

    from pdfacts.backends import exact_numbers
    from pdfacts.core.errors import SourceEvidenceError
    from test_source_evidence import issued

    original = exact_numbers.reconcile_root_paths
    hits = []

    def changed(ledger, drawing, bank, emissions=None, *, limits):
        hits.append(True)
        path = drawing.objects[0]
        if mutation in ("coordinate", "close", "kind", "extra"):
            segments = list(path.segments)
            kind, x, y, close = segments[1]
            if mutation == "coordinate":
                x += 1
            elif mutation == "close":
                close = True
            elif mutation == "kind":
                kind = 2
            else:
                segments.append(segments[-1])
            segments[1] = (kind, x, y, close)
            path = replace(path, segments=tuple(segments))
        else:
            changes = {
                "matrix": {"matrix": (1.0, 0.0, 0.0, 1.0, 1.0, 0.0)},
                "width": {"width": 3.0},
                "cap": {"cap": 1},
                "join": {"join": 1},
                "stroke": {"stroke": False},
                "fill": {"fill_mode": 2},
            }
            path = replace(path, **changes[mutation])
        drawing = replace(drawing, objects=(path,))
        return original(ledger, drawing, bank, emissions, limits=limits)

    monkeypatch.setattr(exact_numbers, "reconcile_root_paths", changed)
    assert exact_numbers.reconcile_root_paths is changed
    with pytest.raises(SourceEvidenceError):
        issued(b"2 w 10 20 m 30 25 l 50 20 l S", source_limits(), Budget(8_388_608, 655_360))
    assert hits == [True]


def test_joined_complete_operation_exact_and_one_below():
    from test_source_evidence import Exhausted, issued

    content = b"2 w 10 20 m 30 25 l 50 20 l S"
    observed = Budget(8_388_608, 655_360)
    issued(content, source_limits(), observed)
    assert issued(content, source_limits(), Budget(observed.bytes, observed.work))[2] is not None
    for byte_limit, work_limit in [
        (observed.bytes - 1, observed.work),
        (observed.bytes, observed.work - 1),
    ]:
        with pytest.raises(Exhausted):
            issued(content, source_limits(), Budget(byte_limit, work_limit))


def test_fraction_comparison_wide_limbs_prepaid_and_exact():
    from fractions import Fraction

    from pdfacts.backends.exact_numbers import _same_source_fraction
    from test_source_evidence import Exhausted

    left = Fraction((1 << 4095) + 1, (1 << 4094) + 1)
    right = Fraction(left.numerator, left.denominator)
    fee = 32 + 16 + 4 * ((4096 + 29) // 30)
    bank = Budget(512, fee)
    assert _same_source_fraction(left, right, bank)
    assert (bank.bytes, bank.work) == (512, fee)
    for bytes_, work in [(511, fee), (512, fee - 1)]:
        with pytest.raises(Exhausted):
            _same_source_fraction(left, right, Budget(bytes_, work))
    assert not _same_source_fraction(left, Fraction(1), Budget(512, fee))


def test_new_helper_constructor_inventory_has_no_hidden_generators():
    import dis

    from pdfacts.backends import exact_numbers

    allocations = {
        name: [
            (i.opname, i.arg)
            for i in dis.get_instructions(getattr(exact_numbers, name))
            if i.opname.startswith("BUILD_") or i.opname in ("MAKE_FUNCTION", "RETURN_GENERATOR")
        ]
        for name in (
            "_has_open_joined_subpath",
            "_same_source_fraction",
            "_open_joined_subpath_segments",
        )
    }
    assert allocations == {
        "_has_open_joined_subpath": [],
        "_same_source_fraction": [],
        "_open_joined_subpath_segments": [("BUILD_LIST", 1), ("BUILD_TUPLE", 4)],
    }


def constructed_source(distinct_matrix=False):
    from fractions import Fraction

    from pdfacts.backends.exact_numbers import IDENTITY, SourcePathNumbers

    matrix = tuple(list(IDENTITY)) if distinct_matrix else IDENTITY
    operations = (
        ("m", (Fraction(10), Fraction(20)), matrix),
        ("l", (Fraction(30), Fraction(25)), matrix),
        ("l", (Fraction(50), Fraction(20)), matrix),
    )
    return SourcePathNumbers((), 1, 0, IDENTITY, Fraction(2), operations, "S")


@pytest.mark.parametrize("distinct_matrix", [False, True])
def test_helper_every_reservation_precedes_dependent_work(distinct_matrix):
    from pdfacts.backends.exact_numbers import _open_joined_subpath_segments
    from test_source_evidence import Exhausted

    source = constructed_source(distinct_matrix)
    converted = []
    checkpoints = []

    def held(value):
        converted.append(value)
        return float(value)

    class Observed(Budget):
        def reserve(self, cost):
            super().reserve(cost)
            checkpoints.append(("bytes", self.bytes, len(converted)))

        def charge(self, cost=1):
            super().charge(cost)
            checkpoints.append(("work", self.work, len(converted)))

    bank = Observed(8_388_608, 655_360)
    expected = ((2, 10.0, 20.0, False), (0, 30.0, 25.0, False), (0, 50.0, 20.0, False))
    assert _open_joined_subpath_segments(source, bank, held, source_limits()) == expected
    assert len(converted) == 6 and checkpoints
    for kind, allowance, conversions in checkpoints:
        converted.clear()
        limited = Budget(
            allowance - 1 if kind == "bytes" else bank.bytes,
            allowance - 1 if kind == "work" else bank.work,
        )
        with pytest.raises(Exhausted, match=kind):
            _open_joined_subpath_segments(source, limited, held, source_limits())
        assert len(converted) == conversions
    converted.clear()
    assert (
        _open_joined_subpath_segments(source, Budget(bank.bytes, bank.work), held, source_limits())
        == expected
    )


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("action", ["close", "exhaust"])
@pytest.mark.parametrize("grammar", ["independent", "joined"])
def test_callback_revocation_during_joined_helper_never_publishes(staged, action, grammar):
    from pdfacts.backends import analysis_identity as identity
    from pdfacts.core.errors import ClosedError, EngineError, SourceEvidenceError
    from test_source_evidence import Exhausted
    from test_staged_source_evidence import ObservedBudget, bind, caller_frame

    content = (
        b"2 w 10 20 m 30 25 l 50 20 l S"
        if grammar == "joined"
        else b"2 w 10 20 m 30 20 l 40 20 m 60 20 l S"
    )
    target = (
        "_open_joined_subpath_segments" if grammar == "joined" else "_straight_subpath_segments"
    )
    data = source_file(content)
    bank = ObservedBudget()
    limits = source_limits()
    sentinel = Exhausted("joined observer")
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        pending = (
            feature.capture_root_path_evidence(doc, 0, bank, limits=limits)[2] if staged else None
        )

        def revoke(kind, cost):
            if caller_frame(target) is None:
                return
            bank.observer = None
            bank.hits += 1
            if action == "close":
                doc.close()
            else:
                raise sentinel

        bank.observer = revoke
        with pytest.raises((ClosedError, EngineError, SourceEvidenceError, Exhausted)) as caught:
            if staged:
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            else:
                feature.issue_root_path_evidence(doc, 0, bank, limits=limits)
        assert bank.hits == 1
        if action == "exhaust":
            assert caught.value is sentinel
        assert owner not in identity._path_batches


def test_source_grammar_keeps_open_rectangle_operations_in_payload():
    data = source_file(b"10 20 m 30 20 l 30 40 l 10 40 l 10 20 l S")
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as doc:
        with doc._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, _, token = feature.issue_root_path_evidence(doc, 0, bank, limits=source_limits())
        payload = feature.resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])
        assert str(paths[0].subpaths[0].shape) == "rectangle"
        assert not paths[0].subpaths[0].closed
        assert tuple(operation[0] for operation in payload[5][5]) == ("m", "l", "l", "l", "l")


@pytest.mark.parametrize("backend_failure", [False, True])
@pytest.mark.parametrize("staged", [False, True, "legacy"])
def test_post_reconciliation_provider_validation_preserves_failure_boundary(
    monkeypatch, backend_failure, staged
):
    from pdfacts.backends.protocols import EngineFailure, FailureKind
    from pdfacts.backends.qpdf import QpdfDocument
    from pdfacts.core.errors import EngineError
    from test_source_evidence import Exhausted
    from test_staged_source_evidence import bind

    data = source_file(b"10 20 m 30 20 l S")
    bank = Budget(8_388_608, 655_360)
    limits = source_limits()
    failure = (
        EngineFailure(FailureKind.OTHER, "validation probe")
        if backend_failure
        else Exhausted("bank probe")
    )
    hits = []
    original = QpdfDocument.validate_analysis_issuance

    def failed(self, expected, bank):
        import sys

        if sys._getframe(1).f_code.co_name not in (
            "complete_staged_root_numbers",
            "exact_root_numbers",
        ):
            return original(self, expected, bank)
        hits.append(True)
        raise failure

    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as doc:
        bind(doc, owner, handle, bank)
        pending = (
            feature.capture_root_path_evidence(doc, 0, bank, limits=limits)[2]
            if staged is True
            else None
        )
        monkeypatch.setattr(QpdfDocument, "validate_analysis_issuance", failed)
        assert QpdfDocument.validate_analysis_issuance is failed
        with pytest.raises(EngineError if backend_failure else Exhausted) as caught:
            if staged == "legacy":
                with doc._access() as access:
                    access.exact_root_numbers(0, bank, limits=limits)
            elif staged is True:
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            else:
                feature.issue_root_path_evidence(doc, 0, bank, limits=limits)
        assert hits == [True]
        if backend_failure:
            assert caught.value.engine == "qpdf" and caught.value.detail == "validation probe"
            assert caught.value.__cause__ is failure
        else:
            assert caught.value is failure


def test_joined_segment_limit_is_not_widened():
    from dataclasses import replace

    from pdfacts.core.errors import LimitExceededError
    from test_source_evidence import issued

    content = b"10 20 m 30 25 l 50 20 l S"
    assert (
        issued(content, replace(source_limits(), max_path_segments=3), Budget(8_388_608, 655_360))[
            2
        ]
        is not None
    )
    with pytest.raises(LimitExceededError):
        issued(content, replace(source_limits(), max_path_segments=2), Budget(8_388_608, 655_360))


def test_helper_rejects_changed_exact_matrix_before_conversion():
    from dataclasses import replace
    from fractions import Fraction

    from pdfacts.backends.exact_numbers import _open_joined_subpath_segments
    from pdfacts.backends.exact_stream import UnsupportedExactSource

    source = constructed_source()
    changed = (Fraction(2),) + source.matrix[1:]
    operations = (("m", source.operations[0][1], changed),) + source.operations[1:]
    calls = []
    with pytest.raises(UnsupportedExactSource, match="native_path_domain"):
        _open_joined_subpath_segments(
            replace(source, operations=operations),
            Budget(8_388_608, 655_360),
            calls.append,
            source_limits(),
        )
    assert calls == []


def test_fraction_comparator_rejects_subclass_without_equality_or_properties():
    from fractions import Fraction

    from pdfacts.backends.exact_numbers import _same_source_fraction
    from pdfacts.backends.exact_stream import UnsupportedExactSource

    class Forbidden(Fraction):
        @property
        def numerator(self):
            pytest.fail("subclass numerator was read")

        def __eq__(self, other):
            pytest.fail("subclass equality was invoked")

    with pytest.raises(UnsupportedExactSource, match="native_path_domain"):
        _same_source_fraction(Forbidden(1), Fraction(1), Budget(512, 32))


def test_joined_helper_noncached_indices_use_prepaid_known_count_storage():
    from dataclasses import replace
    from fractions import Fraction

    from pdfacts.backends.exact_numbers import _open_joined_subpath_segments

    source = constructed_source()
    count = 300
    operations = tuple(
        ("m" if i == 0 else "l", (Fraction(i), Fraction(20)), source.matrix) for i in range(count)
    )
    source = replace(source, operations=operations)
    bank = Budget(8_388_608, 655_360)
    result = _open_joined_subpath_segments(source, bank, float, source_limits())
    assert len(result) == count
    assert result[0] == (2, 0.0, 20.0, False)
    assert result[-1] == (0, 299.0, 20.0, False)
    assert bank.bytes == 1024 + 256 + 192 + 16 * count + 512 * count + 512 * (count - 1)
