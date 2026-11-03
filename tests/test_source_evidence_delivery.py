"""Byte-built delivery regressions and cumulative allocation lower bounds."""

import sys
from dataclasses import replace

import pytest

import pdfacts
from pdfacts.backends import qpdf
from pdfacts.backends.analysis_identity import classification_identity
from pdfacts.core.errors import SourceEvidenceError
from pdfacts.graphics import source_evidence as feature
from synthetic.builder import build, stream
from test_source_evidence import Budget, source_file, source_limits


def ancestry_file(depth):
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    for index in range(2, depth + 2):
        parent = b"" if index == 2 else b"/Parent %d 0 R " % (index - 1)
        resources = b"/Resources << >> " if index == 2 else b""
        objects[index] = (
            b"<< /Type /Pages " + parent + resources + b"/Kids [%d 0 R] /Count 1 >>" % (index + 1)
        )
    page = depth + 2
    objects[page] = b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 200 200] /Contents %d 0 R >>" % (
        page - 1,
        page + 1,
    )
    objects[page + 1] = stream(b"10 20 m 90 20 l S")
    return build(objects, 1)


def test_long_resource_ancestry_storage_is_prepaid():
    data = ancestry_file(96)
    bank = Budget(8_388_608, 655_360)
    observations = []
    code = qpdf.QpdfDocument.exact_root_resources.__code__

    def trace(frame, event, arg):
        if frame.f_code is code and event == "return" and "seen" in frame.f_locals:
            seen = frame.f_locals["seen"]
            identities = [item for item in seen if item is not None]
            observations.append(
                (
                    len(identities),
                    sys.getsizeof(seen) + sum(sys.getsizeof(item) for item in identities),
                )
            )
        return trace

    with pdfacts.open(data) as opened, opened._access() as access:
        previous = sys.gettrace()
        sys.settrace(trace)
        try:
            access._qpdf_ready().exact_root_resources(
                0, bank, replace(source_limits(), max_resource_depth=98)
            )
        finally:
            sys.settrace(previous)
    assert len(observations) == 1
    count, retained = observations[0]
    assert count == 97
    assert bank.bytes >= retained


def test_deep_arrays_reach_semantic_rejection_without_python_recursion():
    data = source_file(b"[" * 1200 + b"]" * 1200 + b" TJ")
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        # The ordinary QPDF parser warns on this deliberately invalid text
        # operand. Assert that warning while checking our typed rejection.
        with pytest.warns(UserWarning, match="Unexpected end of stream"):
            with pytest.raises(SourceEvidenceError) as caught:
                feature.issue_root_path_evidence(
                    opened, 0, bank, limits=replace(source_limits(), max_token_depth=2000)
                )
    assert caught.value.reason == "text_outside_object"


def test_explicit_root_capacity_above_4096_is_honored():
    count = 4097
    content = b"10 20 m 90 20 l S\n" * count
    data = source_file(content)
    bank = Budget(512 * 1024 * 1024, 10_000_000)
    limits = replace(
        source_limits(), max_paths=5000, max_root_objects=5000, max_content_bytes=len(content) + 1
    )
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, images, token = feature.issue_root_path_evidence(opened, 0, bank, limits=limits)
    assert len(paths) == count and not images and token is not None


@pytest.mark.parametrize("entry", ["core", "graphics"])
def test_corrupt_flate_is_a_typed_source_failure(entry):
    raw = b"not-a-zlib-stream"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R /Resources << >> >>",
            4: stream(raw, b"/Filter /FlateDecode"),
        },
        1,
    )
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
            with pytest.raises(SourceEvidenceError) as caught:
                if entry == "core":
                    access.exact_root_numbers(0, bank, limits=source_limits())
                else:
                    feature.issue_root_path_evidence(opened, 0, bank, limits=source_limits())
    assert caught.value.reason == "flate_decode"


def test_registration_containers_are_cumulatively_prepaid():
    data = source_file(b"10 20 m 90 20 l S\n" * 1000)
    with pdfacts.open(data) as opened:
        paths = pdfacts.paths(opened, 0)
    assert len(paths) == 1000
    entries = tuple((path, ()) for path in paths)
    bank = Budget(100_000_000, 10_000_000)
    capacities = []
    observations = []
    start = 0

    def trace(frame, event, arg):
        if frame.f_code is feature._register_path_evidence.__code__ and event == "line":
            checked = frame.f_locals.get("checked")
            if checked is not None:
                size = sys.getsizeof(checked)
                if not capacities or capacities[-1] != size:
                    capacities.append(size)
        if frame.f_code is feature._store_path_evidence.__code__ and event == "call":
            items = frame.f_locals["entries"]
            immutable = sys.getsizeof(items)
            for pair in items:
                immutable += (
                    sys.getsizeof(pair)
                    + sys.getsizeof(pair[0])
                    + sys.getsizeof(pair[1])
                    + sys.getsizeof(pair[1][0])
                )
            cumulative_list = 56 + sum(size - 56 for size in capacities if size > 56)
            observations.append((bank.bytes - start, immutable + cumulative_list))
        return trace

    with classification_identity(data, bank) as (owner, handle, _):
        start = bank.bytes
        previous = sys.gettrace()
        sys.settrace(trace)
        try:
            feature._register_path_evidence(owner, handle, data, bank, entries, source_limits())
        finally:
            sys.settrace(previous)
    assert len(observations) == 1 and capacities
    paid, lower_bound = observations[0]
    assert paid >= lower_bound


def test_private_entry_points_reject_unproved_runtime_before_meter_access(monkeypatch):
    from pdfacts.backends import analysis_identity, exact_numbers, pdfium, source_runtime
    from pdfacts.backends.exact_stream import UnsupportedExactSource
    from pdfacts.backends.finite_content import ContentParser
    from pdfacts.graphics.reader import paths_with_root_inventory

    class UntouchedBudget:
        def reserve(self, cost):
            raise AssertionError("reserve ran outside the proved runtime")

        def charge(self, cost=1):
            raise AssertionError("charge ran outside the proved runtime")

    bank = UntouchedBudget()
    limits = source_limits()
    assert source_runtime._SUPPORTED_RUNTIME
    monkeypatch.setattr(source_runtime, "_SUPPORTED_RUNTIME", False)
    package_entries = [
        lambda: feature.issue_root_path_evidence(None, 0, bank, limits=limits),
        lambda: feature._issue_root_path_evidence(None, 0, bank, limits=limits),
        lambda: feature.resolve_path_evidence(None, None, b"", bank, None, 0, None),
        lambda: feature.classification_identity(b"", bank),
        lambda: feature._register_path_evidence(None, None, b"", bank, (), limits),
        lambda: paths_with_root_inventory(None, 0, bank, limits=limits),
    ]
    for entry in package_entries:
        with pytest.raises(SourceEvidenceError, match="source_runtime_unsupported"):
            entry()
    backend_entries = [
        lambda: analysis_identity.classification_identity(b"", bank),
        lambda: analysis_identity.resolve_identity(None, None, b"", bank),
        lambda: analysis_identity.recovered_identity(None, None, b"", b"", bank),
        lambda: analysis_identity._store_path_evidence(None, None, b"", bank, (), limits),
        lambda: analysis_identity._resolve_path_evidence(
            None, None, b"", bank, None, 0, None, None
        ),
        lambda: ContentParser(b"", bank, limits),
        lambda: exact_numbers.source_ledger((), bank, limits=limits),
        lambda: exact_numbers.reconcile_root_paths((), None, bank, limits=limits),
        lambda: qpdf.decode_exact_name(b"/A", bank),
        lambda: qpdf.QpdfDocument.exact_source_ledger(None, 0, bank, limits),
        lambda: qpdf.QpdfDocument.exact_root_resources(None, 0, bank, limits),
        lambda: qpdf.QpdfDocument.exact_root_stream(None, 0, bank, limits),
        lambda: qpdf.QpdfDocument.exact_numeric_cache(None, bank),
        lambda: qpdf.QpdfDocument.bind_analysis_identity(None, None, None, bank),
        lambda: qpdf.QpdfDocument.validate_analysis_issuance(None, None, bank),
        lambda: pdfium.PdfiumDocument.exact_root_inventory(None, 0, bank, limits),
        lambda: pdfium.PdfiumDocument.capture_page_drawing(None, 0, None, bank, limits),
        lambda: pdfium.PdfiumDocument.consume_root_capture(None, 0, bank, None, limits),
    ]
    for entry in backend_entries:
        with pytest.raises(UnsupportedExactSource, match="source_runtime_unsupported"):
            entry()
    # Ordinary public facts retain their behavior on the simulated runtime.
    with pdfacts.open(source_file()) as opened, opened._access() as access:
        assert len(pdfacts.paths(opened, 0)) == 1
        with pytest.raises(SourceEvidenceError, match="source_runtime_unsupported"):
            access.capture_page_drawing(0, None, bank, limits)
        with pytest.raises(SourceEvidenceError, match="source_runtime_unsupported"):
            access.exact_root_numbers(0, bank, limits=limits)


def test_iterative_arrays_preserve_structure_and_exact_depth_boundary():
    from pdfacts.backends.exact_stream import ExactSourceLimit
    from pdfacts.backends.finite_content import ContentParser

    depth = 1200
    content = b"[" * depth + b"7" + b"]" * depth
    bank = Budget(8_388_608, 655_360)
    parser = ContentParser(content, bank, replace(source_limits(), max_token_depth=depth))
    token = parser.read()
    for _ in range(depth):
        assert token.kind == "array" and len(token.value) == 1
        token = token.value[0]
    assert token.kind == "number" and token.value == b"7"
    assert parser.index == len(content)
    parser = ContentParser(
        content, Budget(8_388_608, 655_360), replace(source_limits(), max_token_depth=depth - 1)
    )
    with pytest.raises(ExactSourceLimit) as caught:
        parser.read()
    assert caught.value.limit == "token_depth" and caught.value.found == depth


def test_ancestry_and_registration_exact_budget_and_one_below():
    from test_source_evidence import Exhausted

    data = ancestry_file(96)
    limits = replace(source_limits(), max_resource_depth=98)
    observed = Budget(8_388_608, 655_360)
    with pdfacts.open(data) as opened, opened._access() as access:
        access._qpdf_ready().exact_root_resources(0, observed, limits)
    for bytes_allowed in (observed.bytes, observed.bytes - 1):
        bank = Budget(bytes_allowed, observed.work)
        with pdfacts.open(data) as opened, opened._access() as access:
            if bytes_allowed == observed.bytes:
                access._qpdf_ready().exact_root_resources(0, bank, limits)
            else:
                with pytest.raises(Exhausted):
                    access._qpdf_ready().exact_root_resources(0, bank, limits)

    data = source_file(b"10 20 m 90 20 l S\n" * 1000)
    with pdfacts.open(data) as opened:
        paths = pdfacts.paths(opened, 0)
    entries = tuple((path, ()) for path in paths)
    bank = Budget(100_000_000, 10_000_000)
    with classification_identity(data, bank) as (owner, handle, _):
        previous = bank.bytes
        feature._register_path_evidence(owner, handle, data, bank, entries, source_limits())
        cost = bank.bytes - previous
    for remaining in (cost, cost - 1):
        bank = Budget(100_000_000, 10_000_000)
        with classification_identity(data, bank) as (owner, handle, _):
            bank.byte_limit = bank.bytes + remaining
            if remaining == cost:
                feature._register_path_evidence(owner, handle, data, bank, entries, source_limits())
            else:
                with pytest.raises(Exhausted):
                    feature._register_path_evidence(
                        owner, handle, data, bank, entries, source_limits()
                    )


def test_decoder_does_not_translate_a_budget_raised_zlib_error():
    import zlib

    sentinel = zlib.error("raised by caller budget")

    class FailingBudget(Budget):
        reached = False

        def reserve(self, cost):
            if cost == 65536 + 64:
                self.reached = True
                raise sentinel
            super().reserve(cost)

    raw = zlib.compress(b"10 20 m 90 20 l S")
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R /Resources << >> >>",
            4: stream(raw, b"/Filter /FlateDecode"),
        },
        1,
    )
    bank = FailingBudget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        with pytest.raises(zlib.error) as caught:
            feature.issue_root_path_evidence(opened, 0, bank, limits=source_limits())
    assert bank.reached
    assert caught.value is sentinel


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "prerelease",
        "serial",
        "pointer",
        "digits",
        "digit_size",
        "free_threaded",
        "debug",
        "trace_refs",
        "implementation",
    ],
)
def test_runtime_predicate_checks_each_proof_requirement(monkeypatch, change):
    import importlib
    import struct
    import sysconfig
    from collections import namedtuple
    from types import SimpleNamespace

    from pdfacts.backends import source_runtime

    assert source_runtime._SUPPORTED_RUNTIME
    try:
        with monkeypatch.context() as patch:
            if change in {"version", "prerelease", "serial"}:
                version = namedtuple("Version", "major minor micro releaselevel serial")
                values = {
                    "version": {"micro": 6},
                    "prerelease": {"releaselevel": "candidate"},
                    "serial": {"serial": 1},
                }
                patch.setattr(
                    sys, "version_info", version(*sys.version_info)._replace(**values[change])
                )
            elif change == "pointer":
                original = struct.calcsize
                patch.setattr(
                    struct, "calcsize", lambda format: 4 if format == "P" else original(format)
                )
            elif change in {"digits", "digit_size"}:
                integer = namedtuple(
                    "Integer",
                    "bits_per_digit sizeof_digit default_max_str_digits str_digits_check_threshold",
                )
                values = {"bits_per_digit": 15} if change == "digits" else {"sizeof_digit": 2}
                patch.setattr(sys, "int_info", integer(*sys.int_info)._replace(**values))
            elif change in {"free_threaded", "debug"}:
                original = sysconfig.get_config_var
                key = "Py_GIL_DISABLED" if change == "free_threaded" else "Py_DEBUG"
                patch.setattr(
                    sysconfig, "get_config_var", lambda name: 1 if name == key else original(name)
                )
            elif change == "trace_refs":
                patch.setattr(sys, "getobjects", lambda: [], raising=False)
            else:
                values = dict(vars(sys.implementation))
                values["name"] = "other"
                patch.setattr(sys, "implementation", SimpleNamespace(**values))
            importlib.reload(source_runtime)
            assert not source_runtime._SUPPORTED_RUNTIME
    finally:
        importlib.reload(source_runtime)
    assert source_runtime._SUPPORTED_RUNTIME


@pytest.mark.parametrize("callback", ["outer_reserve", "outer_charge", "lower_charge"])
def test_resolver_rejects_reference_replaced_by_budget_callback(callback):
    from pdfacts.backends import analysis_identity

    class ReentrantBudget(Budget):
        armed = False
        hits = 0
        path = None
        replacement = None

        def mutate(self, operation):
            caller = sys._getframe(2).f_code
            target = (
                analysis_identity._resolve_path_evidence.__code__
                if callback == "lower_charge"
                else feature.resolve_path_evidence.__code__
            )
            expected = "reserve" if callback == "outer_reserve" else "charge"
            if self.armed and operation == expected and caller is target:
                self.armed = False
                self.hits += 1
                object.__setattr__(self.path, "ref", self.replacement)

        def reserve(self, cost):
            super().reserve(cost)
            self.mutate("reserve")

        def charge(self, cost=1):
            super().charge(cost)
            self.mutate("charge")

    data = source_file()
    bank = ReentrantBudget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, _, token = feature.issue_root_path_evidence(opened, 0, bank, limits=source_limits())
        path = paths[0]
        original = path.ref
        bank.path = path
        bank.replacement = replace(original)
        assert bank.replacement == original and bank.replacement is not original
        bank.armed = True
        with pytest.raises(SourceEvidenceError):
            feature.resolve_path_evidence(owner, handle, data, bank, token, 0, path)
        assert bank.hits == 1 and path.ref is bank.replacement
