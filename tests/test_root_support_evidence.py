"""Synthetic mixed-root source association; expectations come from PDF bytes."""

import zlib

import pytest

import pdfacts
from pdfacts.core.errors import SourceEvidenceError
from pdfacts.graphics import source_evidence as feature
from synthetic.builder import stream
from test_source_evidence import resource_file, source_limits
from test_staged_source_evidence import ObservedBudget, bind


def mixed_file(with_path=True):
    image = stream(
        zlib.compress(b"\x10\x20\x30"),
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
    )
    content = b"q 10 0 0 20 50 60 cm /Im Do Q"
    if with_path:
        content += b" 10 20 m 90 20 l S"
    return resource_file(content, b"<< /XObject << /Im 5 0 R >> >>", {5: image})


@pytest.mark.parametrize("with_path", [False, True])
def test_original_mixed_root_image_payload_and_single_claim(with_path):
    data = mixed_file(with_path)
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        assert type(token) is object
        payload = feature.resolve_root_support_evidence(
            owner, handle, data, bank, token, 0, images[0]
        )
        assert payload[0] == "root-image-association-v1"
        source = payload[5]
        assert source[1] == (5, 0)
        assert source[2] == tuple(("fraction", n, 1) for n in (10, 0, 0, 20, 50, 60))
        assert source[3:] == (None, 1, 1, 2, 24)
        if with_path:
            assert (
                feature.resolve_root_support_evidence(
                    owner, handle, data, bank, token, 1, paths[0]
                )[0]
                == "root-association-v2"
            )
            with pytest.raises(SourceEvidenceError):
                feature.resolve_path_evidence(owner, handle, data, bank, token, 1, paths[0])
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)


def test_old_path_batch_cannot_be_resolved_as_root_support():
    data = mixed_file()
    bank = ObservedBudget()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, token = feature.issue_root_path_evidence(
            doc, 0, bank, limits=source_limits()
        )
        assert (
            feature.resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])[0]
            == "root-association-v2"
        )
        with pytest.raises(SourceEvidenceError):
            feature.resolve_root_support_evidence(owner, handle, data, bank, token, 0, paths[0])


@pytest.mark.parametrize("target", ["image", "ref"])
@pytest.mark.parametrize("phase", ["verification", "publication", "resolution"])
def test_late_domain_mutation_never_calls_getters_or_publishes(target, phase):
    from pdfacts.backends import analysis_identity as identity
    from test_staged_source_evidence import caller_frame

    data = mixed_file()
    bank = ObservedBudget()
    limits = source_limits()
    getters = []
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        selected = images[0] if target == "image" else images[0].ref
        original_type = type(selected)

        class Changed(original_type):
            __slots__ = ()

            def __getattribute__(self, name):
                getters.append(name)
                return super().__getattribute__(name)

        if phase == "resolution":
            token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)

        def mutate(operation, cost):
            frame = caller_frame(
                "_verify_public_image"
                if phase == "verification"
                else "_store_staged_path_evidence"
                if phase == "publication"
                else "_resolve_path_evidence"
            )
            if frame is None or (phase == "publication" and "root_count" not in frame.f_locals):
                return
            bank.observer = None
            bank.hits += 1
            object.__setattr__(selected, "__class__", Changed)

        bank.observer = mutate
        try:
            with pytest.raises(SourceEvidenceError):
                if phase != "resolution":
                    feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
                else:
                    feature.resolve_root_support_evidence(
                        owner, handle, data, bank, token, 0, images[0]
                    )
            assert bank.hits == 1 and getters == []
            if phase != "resolution":
                assert owner not in identity._path_batches
                assert identity._issued[owner][5][1] is None
        finally:
            object.__setattr__(selected, "__class__", original_type)


@pytest.mark.parametrize("action", ["close", "revoke", "replace_ref", "sentinel", "reenter"])
def test_final_callback_atomicity_cleanup_and_original_failure(action):
    from dataclasses import replace

    from pdfacts.backends import analysis_identity as identity
    from pdfacts.core.errors import ClosedError
    from test_source_evidence import Exhausted
    from test_staged_source_evidence import caller_frame

    data = mixed_file()
    bank = ObservedBudget()
    limits = source_limits()
    lifetime = feature.classification_identity(data, bank)
    owner, handle, _ = lifetime.__enter__()
    sentinel = Exhausted("installed callback")
    try:
        with pdfacts.open(data) as doc:
            bind(doc, owner, handle, bank)
            paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            generation = identity._issued[owner]

            def observe(operation, cost):
                frame = caller_frame("_store_staged_path_evidence")
                if frame is None or "root_count" not in frame.f_locals:
                    return
                bank.observer = None
                bank.hits += 1
                if action == "close":
                    doc.close()
                elif action == "revoke":
                    lifetime.__exit__(None, None, None)
                elif action == "replace_ref":
                    object.__setattr__(images[0], "ref", replace(images[0].ref))
                elif action == "sentinel":
                    raise sentinel
                else:
                    with pytest.raises(SourceEvidenceError):
                        feature.complete_root_path_evidence(doc, bank, pending, limits=limits)

            bank.observer = observe
            if action == "reenter":
                token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
                assert feature.resolve_root_support_evidence(
                    owner, handle, data, bank, token, 0, images[0]
                )
            else:
                with pytest.raises((SourceEvidenceError, ClosedError, Exhausted)) as caught:
                    feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
                if action == "sentinel":
                    assert caught.value is sentinel
                assert owner not in identity._path_batches
                assert generation[5][1] is None
                if action not in ("close", "revoke"):
                    with pytest.raises(SourceEvidenceError):
                        feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
            assert bank.hits == 1
    finally:
        lifetime.__exit__(None, None, None)


def test_original_identity_ordinals_owner_and_no_native_reread(monkeypatch):
    from dataclasses import replace

    data = mixed_file()
    limits = source_limits()
    bank = ObservedBudget()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def forbidden(*args, **kwargs):
            pytest.fail("native reread")

        monkeypatch.setattr(type(doc._pdfium), "page_drawing", forbidden)
        monkeypatch.setattr(type(doc._pdfium), "capture_page_drawing", forbidden)
        token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        for ordinal, graphic in (
            (0, replace(images[0])),
            (1, images[0]),
            (0, paths[0]),
            (True, images[0]),
            (-1, images[0]),
        ):
            with pytest.raises(SourceEvidenceError):
                feature.resolve_root_support_evidence(
                    owner, handle, data, bank, token, ordinal, graphic
                )
        with pytest.raises(SourceEvidenceError):
            feature.resolve_root_support_evidence(object(), handle, data, bank, token, 0, images[0])
        assert feature.resolve_root_support_evidence(owner, handle, data, bank, token, 0, images[0])


def test_support_cumulative_exact_and_one_below():
    from test_source_evidence import Budget, Exhausted

    data = mixed_file()
    limits = source_limits()

    def run(bank):
        with (
            feature.classification_identity(data, bank) as (owner, handle, _),
            pdfacts.open(data) as doc,
        ):
            bind(doc, owner, handle, bank)
            paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
            feature.resolve_root_support_evidence(owner, handle, data, bank, token, 0, images[0])
        return bank.bytes, bank.work

    used_bytes, used_work = run(Budget(8388608, 655360))
    assert run(Budget(used_bytes, used_work)) == (used_bytes, used_work)
    with pytest.raises(Exhausted, match="bytes"):
        run(Budget(used_bytes - 1, used_work))
    with pytest.raises(Exhausted, match="work"):
        run(Budget(used_bytes, used_work - 1))


def test_source_failure_spends_claim_without_partial_image_batch():
    from pdfacts.backends import analysis_identity as identity

    # Image is valid, but unsupported page activity must not be omitted.
    data = resource_file(
        b"q 10 0 0 20 50 60 cm /Im Do Q 0 0 10 20 30 40 c S",
        b"<< /XObject << /Im 5 0 R >> >>",
        {
            5: stream(
                zlib.compress(b"\x10\x20\x30"),
                b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
                b"/BitsPerComponent 8 /Filter /FlateDecode",
            )
        },
    )
    bank = ObservedBudget()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=source_limits())
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_support_evidence(doc, bank, pending, limits=source_limits())
        assert owner not in identity._path_batches
        assert identity._issued[owner][5][1] is None


@pytest.mark.parametrize("matrix", [b"-10 0 0 20 50 60", b"10 0 0 -20 50 60"])
def test_reflected_clipped_image_preserves_exact_source(matrix):
    image = stream(
        zlib.compress(b"\x10\x20\x30"),
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /FlateDecode",
    )
    data = resource_file(
        b"q 0 0 100 100 re W n " + matrix + b" cm /Im Do Q",
        b"<< /XObject << /Im 5 0 R >> >>",
        {5: image},
    )
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        source = feature.resolve_root_support_evidence(
            owner, handle, data, bank, token, 0, images[0]
        )[5]
        assert source[2] == tuple(("fraction", int(number), 1) for number in matrix.split())
        assert source[3] == tuple(("fraction", number, 1) for number in (0, 0, 100, 100))


def test_foreign_completion_does_not_consume_original_pending():
    data = mixed_file(False)
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_support_evidence(doc, ObservedBudget(), pending, limits=limits)
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_support_evidence(doc, bank, pending, limits=source_limits())
        token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        assert feature.resolve_root_support_evidence(owner, handle, data, bank, token, 0, images[0])


def test_completed_support_rejects_revoked_owner():
    data = mixed_file(False)
    bank = ObservedBudget()
    limits = source_limits()
    lifetime = feature.classification_identity(data, bank)
    owner, handle, _ = lifetime.__enter__()
    try:
        with pdfacts.open(data) as doc:
            bind(doc, owner, handle, bank)
            _, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
            token = feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
            lifetime.__exit__(None, None, None)
            with pytest.raises(SourceEvidenceError):
                feature.resolve_root_support_evidence(
                    owner, handle, data, bank, token, 0, images[0]
                )
    finally:
        lifetime.__exit__(None, None, None)


@pytest.mark.parametrize(
    "target",
    [
        "native",
        "reconciled",
        "source_record",
        "native_record",
        "fraction",
        "late_source_record",
        "late_native_record",
    ],
)
def test_private_record_callback_domains_refuse_before_getters(target):
    from fractions import Fraction

    from pdfacts.backends import analysis_identity as identity
    from test_staged_source_evidence import caller_frame

    data = mixed_file()
    bank = ObservedBudget()
    limits = source_limits()
    getters = []
    changed = []
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def mutate(operation, cost):
            if target in ("native", "reconciled"):
                frame = caller_frame("_verify_public_image")
                if frame is None or operation != "charge" or cost != 64:
                    return
                record = frame.f_locals[target]
            elif target == "fraction":
                frame = caller_frame("_freeze")
                if (
                    frame is None
                    or operation != "reserve"
                    or cost != 128
                    or type(frame.f_locals.get("value")) is not Fraction
                ):
                    return
                record = frame.f_locals["value"]
            else:
                frame = caller_frame("_record_payload")
                fields = (
                    feature._IMAGE_SOURCE_FIELDS
                    if target.endswith("source_record")
                    else feature._IMAGE_NATIVE_FIELDS
                )
                if frame is None or frame.f_locals.get("names") is not fields:
                    return
                if target.startswith("late_") and frame.f_locals.get("index") != len(fields) - 1:
                    return
                record = frame.f_locals["record"]
            bank.observer = None
            bank.hits += 1
            original_type = type(record)

            class Changed(original_type):
                __slots__ = ()

                def __getattribute__(self, name):
                    getters.append((name, identity._lock._is_owned()))
                    return super().__getattribute__(name)

            object.__setattr__(record, "__class__", Changed)
            changed.append((record, original_type))

        bank.observer = mutate
        try:
            with pytest.raises(SourceEvidenceError):
                feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
            assert bank.hits == 1 and getters == []
            assert owner not in identity._path_batches
            assert identity._issued[owner][5][1] is None
        finally:
            for record, original_type in changed:
                object.__setattr__(record, "__class__", original_type)


@pytest.mark.parametrize("content", [b"q Q", b"BT /F1 10 Tf 10 20 Td (Caption) Tj ET"])
def test_empty_mixed_root_completion_validates_source_and_consumes_claim(content):
    # Synthetic text/state-only pages have zero graphical roots by construction.
    data = resource_file(
        content,
        b"<< /Font << /F1 5 0 R >> >>",
        {5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"},
    )
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        assert paths == () and images == ()
        assert feature.complete_root_support_evidence(doc, bank, pending, limits=limits) is None
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_support_evidence(doc, bank, pending, limits=limits)


def test_empty_mixed_root_inventory_does_not_bypass_unsupported_source():
    data = resource_file(b"unsupported_operator", b"<< >>", {})
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        paths, images, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        assert paths == () and images == ()
        with pytest.raises(SourceEvidenceError):
            feature.complete_root_support_evidence(doc, bank, pending, limits=limits)


def test_empty_inventory_preserves_legacy_path_only_refusal():
    from test_source_evidence import source_file

    data = source_file(b"q Q")
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)
        with pytest.raises(SourceEvidenceError, match="path_evidence_inventory_count"):
            feature.complete_root_path_evidence(doc, bank, pending, limits=limits)


def test_empty_mixed_root_completion_still_checks_owner_at_publication():
    from test_source_evidence import source_file
    from test_staged_source_evidence import caller_frame

    data = source_file(b"q Q")
    bank = ObservedBudget()
    limits = source_limits()
    with (
        feature.classification_identity(data, bank) as (owner, handle, _),
        pdfacts.open(data) as doc,
    ):
        bind(doc, owner, handle, bank)
        _, _, pending = feature.capture_root_path_evidence(doc, 0, bank, limits=limits)

        def close_before_publication(operation, cost):
            frame = caller_frame("_store_staged_path_evidence")
            if frame is not None and "root_count" in frame.f_locals:
                bank.observer = None
                bank.hits += 1
                doc.close()

        bank.observer = close_before_publication
        with pytest.raises(pdfacts.PdfactsError):
            feature.complete_root_support_evidence(doc, bank, pending, limits=limits)
        assert bank.hits == 1
