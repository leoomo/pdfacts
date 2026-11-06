"""Synthetic byte-built staged ownership tests; expectations come from construction."""

import inspect

import pytest

import pdfacts
from pdfacts.backends import analysis_identity as identity
from pdfacts.core.errors import ClosedError, EngineError, SourceEvidenceError
from pdfacts.graphics import source_evidence as feature
from synthetic.builder import PageSpec, document
from test_source_evidence import Budget, source_file, source_limits


class ObservedBudget(Budget):
    def __init__(self):
        super().__init__(8_388_608, 655_360)
        self.observer = None
        self.hits = 0

    def reserve(self, cost):
        super().reserve(cost)
        self.observe("reserve", cost)

    def charge(self, cost=1):
        super().charge(cost)
        self.observe("charge", cost)

    def observe(self, operation, cost):
        if self.observer is not None:
            self.observer(operation, cost)


def bind(opened, owner, handle, bank):
    with opened._access() as access:
        access._qpdf_ready().bind_analysis_identity(owner, handle, bank)


def test_staged_original_objects_complete_out_of_order():
    data = document(
        [
            PageSpec(b"/MediaBox [0 0 200 200]", b"10 20 m 90 20 l S"),
            PageSpec(b"/MediaBox [0 0 200 200]", b"10 40 m 90 40 l S"),
        ]
    )
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        first = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        second = feature.capture_root_path_evidence(doc, 1, bank, limits=limits)
        for paths, images, pending in (second, first):
            assert not images
            ref = paths[0].ref
            token = feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            payload = feature.resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])
            assert payload[0] == "root-association-v2"
            assert paths[0].ref is ref
            with pytest.raises(SourceEvidenceError):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)


def test_eager_close_during_registry_preparation_refuses():
    data = source_file()
    bank = ObservedBudget()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)

        def close_on_registration(operation, cost):
            frame = inspect.currentframe()
            while frame is not None:
                if frame.f_code.co_name in ("_store_path_evidence", "_store_staged_path_evidence"):
                    bank.observer = None
                    bank.hits += 1
                    doc.close()
                    return
                frame = frame.f_back

        bank.observer = close_on_registration
        with pytest.raises((SourceEvidenceError, ClosedError)):
            feature.issue_root_path_evidence(doc, 0, bank, limits=source_limits())
        assert bank.hits == 1
        assert owner not in identity._path_batches


def caller_frame(name):
    frame = inspect.currentframe()
    while frame is not None:
        if frame.f_code.co_name == name:
            return frame
        frame = frame.f_back
    return None


@pytest.mark.parametrize(
    "wrong", ["token", "claimed_list", "drawing", "limits", "bank", "document"]
)
def test_wrong_inputs_do_not_consume_pending(wrong):
    from dataclasses import replace

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
        pdfacts.open(data) as other,
    ):
        bind(doc, owner, handle, bank)
        bind(other, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        node = identity._issued[owner][5][0]
        supplied = pending
        if wrong == "token":
            supplied = object()
        elif wrong == "claimed_list":
            supplied = list(node)
        elif wrong == "drawing":
            supplied = node[2][3]
        with doc._access() as access:
            with pytest.raises(SourceEvidenceError):
                access.complete_staged_root_numbers(
                    other if wrong == "document" else doc,
                    supplied,
                    ObservedBudget() if wrong == "bank" else bank,
                    limits=replace(limits) if wrong == "limits" else limits,
                )
        assert identity._issued[owner][5][0] is node
        assert identity._issued[owner][5][1] is None
        assert feature.complete_root_path_evidence(doc, bank, pending, limits=limits) is not None


@pytest.mark.parametrize("operation", ["reserve", "charge"])
@pytest.mark.parametrize("action", ["insert", "remove_deeper", "recover", "close", "recursive"])
def test_claim_callbacks_cannot_follow_stale_links(operation, action):
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        tokens = [
            feature.capture_root_path_evidence(doc, 0, bank, limits=limits)[2] for _ in range(3)
        ]
        old_generation = identity._issued[owner]

        def interrupt(kind, cost):
            frame = caller_frame("_claim_staged")
            if kind != operation or frame is None or frame.f_locals.get("node") is None:
                return
            bank.observer = None
            bank.hits += 1
            assert not identity._lock._is_owned()
            if action == "insert":
                feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            elif action == "remove_deeper":
                feature.complete_root_path_evidence(doc, bank, tokens[1], limits=limits)
            elif action == "recursive":
                feature.complete_root_path_evidence(doc, bank, tokens[0], limits=limits)
            elif action == "recover":
                identity.recovered_identity(owner, handle, data, bytes(bytearray(data)), bank)
            else:
                doc.close()

        bank.observer = interrupt
        with pytest.raises((SourceEvidenceError, ClosedError)):
            feature.complete_root_path_evidence(doc, bank, tokens[0], limits=limits)
        assert bank.hits == 1
        assert old_generation[5][1] is None
        batches = identity._path_batches.get(owner, {})
        assert len(batches) == (1 if action in ("remove_deeper", "recursive") else 0)


@pytest.mark.parametrize("phase", ["source", "ready", "freeze", "registry", "final"])
@pytest.mark.parametrize("action", ["close", "recover", "rebind", "exhaust"])
def test_revocation_and_exhaustion_clear_only_matching_active(phase, action):
    from test_source_evidence import Exhausted

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        generation = identity._issued[owner]
        sentinel = Exhausted("installed observer")

        def revoke(kind, cost):
            target = {
                "source": "exact_source_ledger",
                "ready": "_ready_staged",
                "freeze": "_freeze",
                "registry": "_store_staged_path_evidence",
                "final": "_store_staged_path_evidence",
            }[phase]
            frame = caller_frame(target)
            if frame is None or (phase == "final" and "root_count" not in frame.f_locals):
                return
            bank.observer = None
            bank.hits += 1
            assert not identity._lock._is_owned()
            assert generation[5][1] is not None
            assert generation[5][1][3] is None
            if action == "close":
                doc.close()
            elif action == "recover":
                identity.recovered_identity(owner, handle, data, bytes(bytearray(data)), bank)
            elif action == "rebind":
                bind(doc, owner, handle, bank)
            else:
                bank.byte_limit = bank.bytes
                bank.work_limit = bank.work
                raise sentinel

        bank.observer = revoke
        with pytest.raises((SourceEvidenceError, ClosedError, EngineError, Exhausted)) as caught:
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        if action == "exhaust":
            assert caught.value is sentinel
        assert bank.hits == 1
        assert generation[5][1] is None
        assert generation[5][0] is None
        assert owner not in identity._path_batches


def test_core_failure_cleans_up_without_graphics_return():

    data = source_file(b"10 20 m 90 20 l S /F1 10 Tf")
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        generation = identity._issued[owner]
        with doc._access() as access, pytest.raises(SourceEvidenceError):
            access.complete_staged_root_numbers(doc, pending, bank, limits=limits)
        assert generation[5][:2] == [None, None]
        with pytest.raises(SourceEvidenceError, match="staged_source_token"):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        assert owner not in identity._path_batches


def test_late_original_ref_mutation_refuses_final_publication():
    from dataclasses import replace

    data = source_file(b"10 20 m 90 20 l S\n" * 3)
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def mutate(kind, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if frame is None or "root_count" not in frame.f_locals:
                return
            bank.observer = None
            bank.hits += 1
            object.__setattr__(paths[-1], "ref", replace(paths[-1].ref))

        bank.observer = mutate
        with pytest.raises(SourceEvidenceError, match="path_evidence_ref_changed"):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        assert bank.hits == 1
        assert identity._issued[owner][5][1] is None
        assert owner not in identity._path_batches


def test_legacy_registry_change_during_final_preparation_is_detected():
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def insert(kind, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if frame is None or "root_count" not in frame.f_locals:
                return
            bank.observer = None
            bank.hits += 1
            # Exercise the existing trusted storage primitive, not a new public proof API.
            feature._register_path_evidence(owner, handle, data, bank, ((paths[0], ()),), limits)

        bank.observer = insert
        with pytest.raises(SourceEvidenceError, match="staged_source_changed"):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        assert bank.hits == 1
        assert len(identity._path_batches[owner]) == 1
        assert identity._issued[owner][5][1] is None


@pytest.mark.parametrize("with_path", [False, True])
@pytest.mark.parametrize("revoke", [False, True])
def test_images_keep_mixed_root_ordinals_and_image_only_completion(with_path, revoke):
    import zlib

    from synthetic.builder import stream
    from test_source_evidence import resource_file

    image = stream(
        zlib.compress(b"\x10\x20\x30"),
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
    )
    content = b"q 10 0 0 10 50 50 cm /Im Do Q"
    if with_path:
        content += b" 10 20 m 90 20 l S"
    data = resource_file(content, b"<< /XObject << /Im 5 0 R >> >>", {5: image})
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        assert len(images) == 1 and images[0].ref.drawing_index == 0
        if revoke:

            def close_at_final(kind, cost):
                frame = caller_frame("_store_staged_path_evidence")
                if frame is not None and "root_count" in frame.f_locals:
                    bank.observer = None
                    bank.hits += 1
                    doc.close()

            bank.observer = close_at_final
            with pytest.raises(SourceEvidenceError, match="staged_source_binding"):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            assert bank.hits == 1
            assert owner not in identity._path_batches
            assert identity._issued[owner][5][1] is None
            return
        token = feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        if with_path:
            assert len(paths) == 1 and paths[0].ref.drawing_index == 1
            assert feature.resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])
        else:
            assert paths == () and token is None
            assert owner not in identity._path_batches
        assert identity._issued[owner][5][:2] == [None, None]


def test_owner_exit_and_recovery_break_pending_cycles():
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        original = identity._issued[owner]
        replacement_data = bytes(bytearray(data))
        replacement_handle = identity.recovered_identity(
            owner, handle, data, replacement_data, bank
        )
        assert replacement_handle is not handle
        assert original[5][:2] == [None, None]
        current = identity._issued[owner]
        assert current is not original and current[5] is not original[5]
    assert current[5][:2] == [None, None]
    assert owner not in identity._issued


@pytest.mark.parametrize("count", [0, 1, 3])
@pytest.mark.parametrize("shortfall", [0, 1])
def test_claim_prepays_each_actual_lock_exit(count, shortfall):
    from pdfacts.backends.exact_stream import UnsupportedExactSource
    from test_source_evidence import Exhausted

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        tokens = [
            feature.capture_root_path_evidence(doc, 0, bank, limits=limits)[2] for _ in range(count)
        ]
        generation = identity._issued[owner]
        original_head = generation[5][0]
        token = tokens[0] if tokens else object()
        bank.reserve(64)  # Caller-owned abort allowance, as in the core completion entry.
        before_bytes, before_work = bank.bytes, bank.work
        exact = 512 + 64 * (count + 1)
        bank.byte_limit = bank.bytes + exact - shortfall
        try:
            if shortfall:
                with pytest.raises(Exhausted):
                    identity._claim_staged(
                        doc,
                        token,
                        bank,
                        limits,
                        feature._STAGED_DOCUMENT_TYPES,
                        feature._STAGED_PROVIDER_TYPES,
                    )
                assert generation[5][0] is original_head and generation[5][1] is None
            elif count:
                node = identity._claim_staged(
                    doc,
                    token,
                    bank,
                    limits,
                    feature._STAGED_DOCUMENT_TYPES,
                    feature._STAGED_PROVIDER_TYPES,
                )
                assert node[0] is token and node[3] is None
                assert generation[5][1] is node
            else:
                with pytest.raises(UnsupportedExactSource, match="staged_source_token"):
                    identity._claim_staged(
                        doc,
                        token,
                        bank,
                        limits,
                        feature._STAGED_DOCUMENT_TYPES,
                        feature._STAGED_PROVIDER_TYPES,
                    )
            if not shortfall:
                assert bank.bytes - before_bytes == exact
                assert bank.work - before_work == 128 + 8 * count
        finally:
            identity._abort_staged(generation, token)


@pytest.mark.parametrize("allowance", [4095, 4096])
def test_explicit_completion_access_is_prepaid_before_construction(monkeypatch, allowance):
    from pdfacts.core.document import Document
    from test_source_evidence import Exhausted

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, token = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        original = Document._access
        calls = []

        def observed(self):
            calls.append(self)
            return original(self)

        monkeypatch.setattr(Document, "_access", observed)
        assert Document._access is observed
        bank.byte_limit = bank.bytes + allowance
        with pytest.raises(Exhausted):
            feature.complete_root_path_evidence(doc, bank, token, limits=limits)
        assert len(calls) == (1 if allowance == 4096 else 0)
        assert identity._issued[owner][5][0][0] is token
        assert identity._issued[owner][5][1] is None


def test_actual_completion_context_inventory_and_one_outer_scope():
    import contextlib
    import sys

    from pdfacts.core.document import Document, FeatureAccess
    from pdfacts.core.runtime import engine

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    contexts = []
    features = []
    constructor = contextlib._GeneratorContextManager.__init__.__code__

    def trace(frame, event, arg):
        if event == "return" and frame.f_code is constructor:
            instance = frame.f_locals["self"]
            if instance.func in (Document._access.__wrapped__, engine.__wrapped__):
                contexts.append(
                    (
                        instance.func,
                        sum(
                            sys.getsizeof(value)
                            for value in (
                                instance,
                                instance.__dict__,
                                instance.gen,
                                instance.args,
                                instance.kwds,
                            )
                        ),
                    )
                )
        if event == "return" and frame.f_code is FeatureAccess.__init__.__code__:
            instance = frame.f_locals["self"]
            features.append(
                sum(
                    sys.getsizeof(value)
                    for value in (
                        instance,
                        instance.__dict__,
                        instance._check_forms,
                    )
                )
            )
        return trace

    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, token = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        old_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            assert feature.complete_root_path_evidence(doc, bank, token, limits=limits) is not None
        finally:
            sys.settrace(old_trace)
        assert len(contexts) == 2 and len(features) == 1
        assert sum(size for _, size in contexts) + sum(features) <= 1760
        assert 1760 + 768 + 1152 <= 4096


@pytest.mark.parametrize("shortfall", [0, 1])
def test_large_final_reference_scan_exact_and_one_below(shortfall):
    from test_source_evidence import Exhausted

    count = 300
    data = source_file(b"10 20 m 90 20 l S\n" * count)
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        assert len(paths) == count
        observations = []

        def limit_at_scan(kind, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if kind != "reserve" or frame is None or "root_count" not in frame.f_locals:
                return
            bank.observer = None
            bank.hits += 1
            assert cost == 192 + 72 * (count - 256)
            observations.append((bank.bytes, bank.work))
            # This callback runs after paying the variable scan fee. Only the
            # final identity-scope exit remains; cleanup was paid at setup.
            bank.byte_limit = bank.bytes + 64 - shortfall

        bank.observer = limit_at_scan
        if shortfall:
            with pytest.raises(Exhausted):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            assert owner not in identity._path_batches
        else:
            assert (
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits) is not None
            )
            assert bank.bytes - observations[0][0] == 64
            assert bank.work - observations[0][1] == 24 * count + 96
        assert bank.hits == 1
        assert identity._issued[owner][5][1] is None


def test_active_completion_rejects_recursive_capture_and_completion():
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def recurse(kind, cost):
            if caller_frame("_freeze") is None:
                return
            bank.observer = None
            bank.hits += 1
            active = identity._issued[owner][5][1]
            with pytest.raises(SourceEvidenceError, match="staged_source_active"):
                feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            with pytest.raises(SourceEvidenceError, match="staged_source_active"):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            assert identity._issued[owner][5][1] is active

        bank.observer = recurse
        assert feature.complete_root_path_evidence(doc, bank, pending, limits=limits) is not None
        assert bank.hits == 1
        assert len(identity._path_batches[owner]) == 1


@pytest.mark.parametrize("boundary", ["ancestry_objgen", "raw_stream"])
def test_staged_close_stops_before_next_native_read(boundary, monkeypatch):
    import sys

    import pikepdf

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    native_after_close = []
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        provider = doc._qpdf
        if boundary == "ancestry_objgen":
            original = pikepdf.Object.objgen

            def observed(obj):
                if sys._getframe(1).f_code.co_name == "exact_root_resources" and provider._closed:
                    native_after_close.append(True)
                    raise AssertionError("intercepted closed native objgen")
                return original.__get__(obj, type(obj))

            replacement = property(observed)
            monkeypatch.setattr(pikepdf.Object, "objgen", replacement)
            assert pikepdf.Object.objgen is replacement
        else:
            original = pikepdf.Object.read_raw_bytes

            def observed(obj, *args, **kwargs):
                if provider._closed:
                    native_after_close.append(True)
                    raise AssertionError("intercepted closed native stream")
                return original(obj, *args, **kwargs)

            monkeypatch.setattr(pikepdf.Object, "read_raw_bytes", observed)
            assert pikepdf.Object.read_raw_bytes is observed

        def close(kind, cost):
            name = (
                "exact_root_resources"
                if boundary == "ancestry_objgen"
                else "_exact_bounded_decoded_stream"
            )
            frame = caller_frame(name)
            expected = (
                512
                if boundary == "ancestry_objgen"
                else (None if frame is None else frame.f_locals.get("source_size"))
            )
            if kind == "reserve" and frame is not None and cost == expected:
                bank.observer = None
                bank.hits += 1
                doc.close()

        bank.observer = close
        with pytest.raises(EngineError, match="closed"):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        assert bank.hits == 1
        assert native_after_close == []
        assert identity._issued[owner][5][1] is None
        assert owner not in identity._path_batches


def test_owner_revocation_during_ready_clears_active_and_cannot_publish():
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    session = identity.classification_identity(data, bank)
    owner, handle, _ = session.__enter__()
    try:
        with pdfacts.open(data) as doc:
            bind(doc, owner, handle, bank)
            _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            generation = identity._issued[owner]

            def exit_owner(kind, cost):
                if caller_frame("_ready_staged") is not None:
                    bank.observer = None
                    bank.hits += 1
                    session.__exit__(None, None, None)

            bank.observer = exit_owner
            with pytest.raises(SourceEvidenceError, match="staged_source_token"):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
            assert bank.hits == 1
            assert generation[5][:2] == [None, None]
            assert owner not in identity._issued and owner not in identity._path_batches
    finally:
        session.__exit__(None, None, None)


def test_old_abort_does_not_clear_new_generation_active():
    from contextlib import ExitStack

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
        ExitStack() as stack,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        old = identity._issued[owner]
        retained = []

        def recover_and_claim(kind, cost):
            if caller_frame("_freeze") is None:
                return
            bank.observer = None
            bank.hits += 1
            recovered = bytes(bytearray(data))
            new_handle = identity.recovered_identity(owner, handle, data, recovered, bank)
            other = stack.enter_context(pdfacts.open(recovered))
            bind(other, owner, new_handle, bank)
            _, _, new_token = feature.capture_root_path_evidence(other, 0, bank, limits=limits)
            with other._access() as access:
                access.complete_staged_root_numbers(other, new_token, bank, limits=limits)
            retained.append((identity._issued[owner], new_token))

        bank.observer = recover_and_claim
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        assert bank.hits == 1 and old[5][:2] == [None, None]
        new, new_token = retained[0]
        assert new[5][1][0] is new_token and new[5][1][4] is not None
        assert owner not in identity._path_batches
        identity._abort_staged(new, new_token)


def test_eager_keeps_repeated_new_objects_and_post_close_resolution():
    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    with identity.classification_identity(data, bank) as (owner, handle, _):
        with pdfacts.open(data) as doc:
            bind(doc, owner, handle, bank)
            first = feature.issue_root_path_evidence(doc, 0, bank, limits=limits)
            second = feature.issue_root_path_evidence(doc, 0, bank, limits=limits)
            assert first[0][0] is not second[0][0]
            assert first[0][0].ref is not second[0][0].ref
            assert first[2] is not second[2]
        for paths, _, token in (first, second):
            assert feature.resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])


def test_actual_completion_lock_exits_match_per_node_inventory():
    import sys

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    exits = []
    names = {"_claim_staged", "_ready_staged", "_store_staged_path_evidence", "_abort_staged_owner"}

    def profile(frame, event, argument):
        if (
            event == "c_call"
            and frame.f_code.co_name in names
            and getattr(argument, "__self__", None) is identity._lock
            and getattr(argument, "__name__", None) == "__exit__"
        ):
            exits.append(frame.f_code.co_name)

    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        tokens = [
            feature.capture_root_path_evidence(doc, 0, bank, limits=limits)[2] for _ in range(3)
        ]
        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            assert (
                feature.complete_root_path_evidence(doc, bank, tokens[0], limits=limits) is not None
            )
        finally:
            sys.setprofile(previous)
        assert exits.count("_claim_staged") == 4
        assert exits.count("_ready_staged") == 1
        assert exits.count("_store_staged_path_evidence") == 2
        assert exits.count("_abort_staged_owner") == 1
        assert len(exits) == 3 + 5


def test_other_analysis_token_cannot_consume_original_pending():
    data = source_file()
    bank = ObservedBudget()
    other_bank = ObservedBudget()
    limits = source_limits()
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        identity.classification_identity(data, other_bank) as (other_owner, other_handle, _),
        pdfacts.open(data) as doc,
        pdfacts.open(data) as other,
    ):
        bind(doc, owner, handle, bank)
        bind(other, other_owner, other_handle, other_bank)
        _, _, token = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        with pytest.raises(SourceEvidenceError, match="staged_source_token"):
            feature.complete_root_path_evidence(other, other_bank, token, limits=limits)
        assert identity._issued[owner][5][0][0] is token
        assert feature.complete_root_path_evidence(doc, bank, token, limits=limits) is not None


@pytest.mark.parametrize(
    "changed", ["ref", "path", "image", "page_ref", "page", "document", "pdfium", "qpdf"]
)
def test_late_same_layout_subclass_cannot_run_getters_or_publish(changed):
    import zlib

    from synthetic.builder import stream
    from test_source_evidence import resource_file

    data = source_file()
    if changed == "image":
        image = stream(
            zlib.compress(b"\x10\x20\x30"),
            b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
        )
        data = resource_file(
            b"q 10 0 0 10 50 50 cm /Im Do Q",
            b"<< /XObject << /Im 5 0 R >> >>",
            {5: image},
        )
    bank = ObservedBudget()
    limits = source_limits()
    getter_lock_states = []
    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        target = images[0] if changed == "image" else paths[0]
        if changed == "ref":
            target = target.ref
        elif changed == "page_ref":
            target = doc.pages[0].ref
        elif changed == "page":
            target = doc.pages[0]
        elif changed == "document":
            target = doc
        elif changed == "pdfium":
            target = doc._pdfium
        elif changed == "qpdf":
            target = doc._qpdf
        original_type = type(target)

        class Changed(type(target)):
            __slots__ = ()

            def __getattribute__(self, name):
                getter_lock_states.append(identity._lock._is_owned())
                return super().__getattribute__(name)

        def mutate(kind, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if frame is None or "root_count" not in frame.f_locals:
                return
            bank.observer = None
            bank.hits += 1
            object.__setattr__(target, "__class__", Changed)
            assert type(target) is Changed

        bank.observer = mutate
        failure = None
        try:
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        except SourceEvidenceError as caught:
            failure = caught
        finally:
            object.__setattr__(target, "__class__", original_type)
        assert bank.hits == 1
        assert getter_lock_states == []
        assert failure is not None
        assert owner not in identity._path_batches
        assert identity._issued[owner][5][1] is None


@pytest.mark.parametrize(
    "changed", ["page_scalar", "page_cache", "closed_flag", "page_ref_identity"]
)
def test_late_binding_values_cannot_call_user_code_or_replace_page_ref(changed):
    from dataclasses import replace

    data = source_file()
    bank = ObservedBudget()
    limits = source_limits()
    callbacks = []

    class ChangedInt(int):
        def __ne__(self, other):
            callbacks.append(identity._lock._is_owned())
            return super().__ne__(other)

    class ChangedDict(dict):
        def get(self, *args):
            callbacks.append(identity._lock._is_owned())
            return super().get(*args)

    class ChangedFlag:
        def __bool__(self):
            callbacks.append(identity._lock._is_owned())
            return False

    with (
        identity.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        page = doc.pages[0]
        ref = page.ref
        number = ref.object_number
        cache = doc._read

        def mutate(kind, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if frame is None or "root_count" not in frame.f_locals:
                return
            bank.observer = None
            bank.hits += 1
            if changed == "page_scalar":
                object.__setattr__(ref, "object_number", ChangedInt(number))
            elif changed == "page_cache":
                doc._read = ChangedDict(cache)
            elif changed == "closed_flag":
                doc._closed = ChangedFlag()
            else:
                page._ref = replace(ref)
                assert page._ref is not ref and page._ref == ref

        bank.observer = mutate
        try:
            with pytest.raises(SourceEvidenceError):
                feature.complete_root_path_evidence(doc, bank, pending, limits=limits)
        finally:
            object.__setattr__(ref, "object_number", number)
            page._ref = ref
            doc._read = cache
            doc._closed = False
        assert bank.hits == 1
        assert callbacks == []
        assert owner not in identity._path_batches
        assert identity._issued[owner][5][1] is None
