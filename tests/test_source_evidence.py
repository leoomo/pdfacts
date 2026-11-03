"""Synthetic byte-built source proofs, with expected geometry from construction."""

from dataclasses import replace
from fractions import Fraction

import pytest

import pdfacts
from pdfacts.backends.analysis_identity import classification_identity, resolve_identity
from pdfacts.backends.protocols import SourceEvidenceLimits
from pdfacts.backends.qpdf import decode_exact_name
from pdfacts.core.errors import LimitExceededError, PdfactsError, SourceEvidenceError
from pdfacts.graphics.source_evidence import issue_root_path_evidence
from synthetic.builder import PageSpec, document


class Exhausted(RuntimeError):
    pass


class Budget:
    def __init__(self, byte_limit, work_limit):
        self.byte_limit = byte_limit
        self.work_limit = work_limit
        self.bytes = 0
        self.work = 0

    def reserve(self, cost):
        if self.bytes + cost > self.byte_limit:
            raise Exhausted("bytes")
        self.bytes += cost

    def charge(self, cost=1):
        if self.work + cost > self.work_limit:
            raise Exhausted("work")
        self.work += cost


def source_limits():
    return SourceEvidenceLimits(
        max_source_bytes=8_388_608,
        max_decoded_bytes=8_388_608,
        max_content_bytes=65_536,
        max_streams=1024,
        max_instructions=65_536,
        max_paths=4096,
        max_root_objects=4096,
        max_path_segments=4096,
        max_token_bytes=1024,
        max_token_depth=32,
        max_graphics_depth=256,
        max_marked_depth=32,
        max_resource_depth=32,
        max_font_differences=512,
        max_text_array_items=1024,
        max_payload_depth=32,
    )


def source_file(content=b"2 w 10 20 m 90 20 l S"):
    return document([PageSpec(b"/MediaBox [0 0 200 200]", content)])


def test_source_ledger_is_pure_and_construction_matches():
    data = source_file()
    bank = Budget(8_388_608, 655_360)
    with (
        classification_identity(data, bank) as (owner, handle, digest),
        pdfacts.open(data) as opened,
    ):
        with opened._access() as access:
            provider = access._qpdf_ready()
            provider.bind_analysis_identity(owner, handle, bank)
            actual_digest, _, _, ledger, _ = provider.exact_source_ledger(0, bank, source_limits())
            assert actual_digest == digest
            assert len(ledger) == 1
            assert ledger[0].width == Fraction(2)
            assert ledger[0].operations[0][0] == "m"
            assert ledger[0].operations[0][1] == (Fraction(10), Fraction(20))
        paths, images, token = issue_root_path_evidence(opened, 0, bank, limits=source_limits())
        assert len(paths) == 1
        assert images == ()
        assert token is not None
        assert resolve_identity(owner, handle, data, bank) == digest


@pytest.mark.parametrize("word", [b"/F#31", b"/#41", b"/", b"/#ff"])
def test_name_decoder_returns_plain_lexical_bytes(word):
    bank = Budget(8_388_608, 655_360)
    value = decode_exact_name(word, bank)
    assert type(value) is bytes
    assert value is word


def test_caller_budget_error_propagates_before_name_read():
    bank = Budget(0, 655_360)
    with pytest.raises(Exhausted, match="bytes"):
        decode_exact_name(b"/F#31", bank)
    assert bank.work == 0


def test_escaped_resource_name_uses_same_font():
    # A complete declared single-byte encoding is part of this constructed fixture.
    data = document(
        [
            PageSpec(
                b"/MediaBox [0 0 200 200]",
                b"BT /F#31 10 Tf 10 40 Td (A) Tj ET 2 w 10 20 m 90 20 l S",
            )
        ],
        font=b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    )
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, _, token = issue_root_path_evidence(opened, 0, bank, limits=source_limits())
        assert len(paths) == 1 and token is not None


def issued(content, limits, bank):
    data = source_file(content)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        return issue_root_path_evidence(opened, 0, bank, limits=limits)


@pytest.mark.parametrize(
    "name, value, content, expected",
    [
        ("max_source_bytes", 1, b"2 w 10 20 m 90 20 l S", "source_bytes"),
        ("max_decoded_bytes", 1, b"2 w 10 20 m 90 20 l S", "decoded_size"),
        ("max_content_bytes", 1, b"2 w 10 20 m 90 20 l S", "content_bytes"),
        ("max_root_objects", 1, b"10 20 m 90 20 l S 10 30 m 90 30 l S", "native_root_objects"),
        ("max_path_segments", 1, b"10 20 m 90 20 l S", "native_path_segments"),
        ("max_paths", 1, b"10 20 m 90 20 l S 10 30 m 90 30 l S", "paint_paths"),
        ("max_instructions", 1, b"10 20 m 90 20 l S", "source_instructions"),
        ("max_graphics_depth", 1, b"q q 10 20 m 90 20 l S Q Q", "graphics_depth"),
        ("max_token_bytes", 1, b"10 20 m 90 20 l S", "token_bytes"),
    ],
)
def test_caller_limits_are_typed_and_enforced(name, value, content, expected):
    limits = replace(source_limits(), **{name: value})
    with pytest.raises(LimitExceededError) as caught:
        issued(content, limits, Budget(8_388_608, 655_360))
    assert isinstance(caught.value, PdfactsError)
    assert caught.value.limit == expected
    assert caught.value.allowed == value
    assert caught.value.found > value


def test_budget_exception_identity_is_preserved():
    sentinel = Exhausted("caller sentinel")

    class FailingBudget(Budget):
        def reserve(self, cost):
            raise sentinel

    with pytest.raises(Exhausted) as caught:
        issue_root_path_evidence(None, 0, FailingBudget(1, 1), limits=source_limits())
    assert caught.value is sentinel


def test_unsupported_source_is_a_package_error():
    with pytest.raises(SourceEvidenceError) as caught:
        issued(b"10 20 m 90 20 l S /F1 10 Tf", source_limits(), Budget(8_388_608, 655_360))
    assert isinstance(caught.value, PdfactsError)
    assert caught.value.reason


def test_configuration_identity_cannot_be_substituted_after_capture():
    from pdfacts.backends.exact_stream import UnsupportedExactSource

    data = source_file()
    bank = Budget(8_388_608, 655_360)
    limits = source_limits()
    with pdfacts.open(data) as opened, opened._access() as access:
        native = access._pdfium_ready()
        _, token = native.capture_page_drawing(0, None, bank, limits)
        with pytest.raises(UnsupportedExactSource, match="native_capture_binding"):
            native.consume_root_capture(0, bank, token, replace(limits))
        assert native.consume_root_capture(0, bank, token, limits).objects


def test_budget_exception_inside_native_capture_is_not_wrapped():
    sentinel = Exhausted("native reserve sentinel")

    class NativeFailingBudget(Budget):
        def reserve(self, cost):
            if cost == 192:
                raise sentinel
            super().reserve(cost)

    with pytest.raises(Exhausted) as caught:
        issued(b"2 w 10 20 m 90 20 l S", source_limits(), NativeFailingBudget(8_388_608, 655_360))
    assert caught.value is sentinel


def test_complete_operation_exact_budget_and_one_below():
    content = b"2 w 10 20 m 90 20 l S"
    observed = Budget(8_388_608, 655_360)
    issued(content, source_limits(), observed)
    for byte_limit, work_limit in [
        (observed.bytes - 1, observed.work),
        (observed.bytes, observed.work - 1),
    ]:
        with pytest.raises(Exhausted):
            issued(content, source_limits(), Budget(byte_limit, work_limit))
    assert issued(content, source_limits(), Budget(observed.bytes, observed.work))[2] is not None


def test_resource_depth_is_a_caller_limit():
    from synthetic.builder import build, stream

    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << >> >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
            4: stream(b"10 20 m 90 20 l S"),
        },
        1,
    )
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        with pytest.raises(LimitExceededError) as caught:
            issue_root_path_evidence(
                opened, 0, bank, limits=replace(source_limits(), max_resource_depth=1)
            )
    assert caught.value.limit == "resource_depth"


def test_source_evidence_error_preserves_attributes_when_pickled():
    import pickle

    original = SourceEvidenceError(reason="unsupported_operator")
    copied = pickle.loads(pickle.dumps(original))
    assert type(copied) is SourceEvidenceError
    assert copied.reason == original.reason


def test_non_utf8_resource_name_retains_original_name_bytes():
    from synthetic.builder import build, stream

    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F#ff 5 0 R >> >> /Contents 4 0 R >>",
            4: stream(b"BT /F#ff 10 Tf (A) Tj ET 10 20 m 90 20 l S"),
            5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        },
        1,
    )
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        assert issue_root_path_evidence(opened, 0, bank, limits=source_limits())[2] is not None


def resource_file(content, resources, extra):
    from synthetic.builder import build, stream

    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
        + resources
        + b" /Contents 4 0 R >>",
        4: stream(content),
        **extra,
    }
    return build(objects, 1)


def issue_data(data, limits):
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        return issue_root_path_evidence(opened, 0, bank, limits=limits)


@pytest.mark.parametrize("kind", ["marked", "font", "text_array", "token_depth"])
def test_resource_and_text_limits_exact_and_one_below(kind):
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    resources = b"<< /Font << /F1 5 0 R >> >>"
    content = b"BT /F1 10 Tf (A) Tj ET 10 20 m 90 20 l S"
    if kind == "marked":
        content = b"/Figure /P0 BDC /Figure /P0 BDC 10 20 m 90 20 l S EMC EMC"
        resources = b"<< /Properties << /P0 << /MCID 0 >> >> >>"
        field, reason = "max_marked_depth", "marked_depth"
    elif kind == "font":
        font = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding "
            b"/Differences [65 /A] >> >>"
        )
        field, reason = "max_font_differences", "font_differences"
    elif kind == "text_array":
        content = b"BT /F1 10 Tf [(A)(B)] TJ ET 10 20 m 90 20 l S"
        field, reason = "max_text_array_items", "text_array_items"
    else:
        content = b"BT /F1 10 Tf (A(B)) Tj ET 10 20 m 90 20 l S"
        field, reason = "max_token_depth", "token_depth"
    data = resource_file(content, resources, {5: font})
    assert issue_data(data, replace(source_limits(), **{field: 2}))[2] is not None
    with pytest.raises(LimitExceededError) as caught:
        issue_data(data, replace(source_limits(), **{field: 1}))
    assert caught.value.limit == reason
    assert caught.value.allowed == 1
    assert caught.value.found == 2


def test_stream_count_exact_and_one_below():
    from synthetic.builder import build, stream

    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << >> /Contents [4 0 R 5 0 R] >>",
            4: stream(b"10 20 m "),
            5: stream(b"90 20 l S"),
        },
        1,
    )
    assert issue_data(data, replace(source_limits(), max_streams=2))[2] is not None
    with pytest.raises(LimitExceededError) as caught:
        issue_data(data, replace(source_limits(), max_streams=1))
    assert caught.value.limit == "source_streams"
    assert caught.value.found == 2


def test_limits_and_budget_protocol_are_explicit_and_frozen():
    import dataclasses
    import typing

    from pdfacts.backends.protocols import OperationBudget

    assert typing.get_protocol_members(OperationBudget) == {"reserve", "charge"}
    with pytest.raises(TypeError):
        SourceEvidenceLimits()
    limits = source_limits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_source_bytes = 1
    for field in dataclasses.fields(limits):
        with pytest.raises(ValueError, match=field.name):
            replace(limits, **{field.name: 0})


def test_image_capture_reuses_the_ordinary_metadata_read(monkeypatch):
    import zlib

    import pypdfium2.raw as raw

    from synthetic.builder import stream

    image = stream(
        zlib.compress(b"\x10\x20\x30"),
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
    )
    data = resource_file(
        b"q 20 20 2 2 re W n q 10 0 0 10 50 50 cm /Im Do Q Q 10 20 m 90 20 l S",
        b"<< /XObject << /Im 5 0 R >> >>",
        {5: image},
    )
    calls = []
    original = raw.FPDFImageObj_GetImageMetadata

    def observed(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(raw, "FPDFImageObj_GetImageMetadata", observed)
    with pdfacts.open(data) as opened:
        assert len(pdfacts.images(opened, 0)) == 1
    baseline = len(calls)
    assert baseline > 0
    calls.clear()
    paths, images, token = issue_data(data, source_limits())
    assert len(calls) == baseline
    assert len(paths) == len(images) == 1
    assert token is not None
    corners = images[0].corners
    assert [(point.x, point.y) for point in (corners.p1, corners.p2, corners.p3, corners.p4)] == [
        (50.0, 50.0),
        (60.0, 50.0),
        (60.0, 60.0),
        (50.0, 60.0),
    ]


@pytest.mark.parametrize(
    "mutation", ["wrong_type", "clone", "ref_clone", "ref_value", "foreign_bank", "forged_token"]
)
def test_graphics_resolution_preserves_fact_and_reference_identity(mutation):
    from pdfacts.graphics.source_evidence import resolve_path_evidence

    data = source_file()
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, _, token = issue_root_path_evidence(opened, 0, bank, limits=source_limits())
        path = paths[0]
        assert (
            resolve_path_evidence(owner, handle, data, bank, token, 0, path)[0]
            == "root-association-v2"
        )
        if mutation == "wrong_type":
            path = object()
        elif mutation == "clone":
            path = replace(path)
        elif mutation == "ref_clone":
            object.__setattr__(path, "ref", replace(path.ref))
        elif mutation == "ref_value":
            object.__setattr__(path.ref, "drawing_index", path.ref.drawing_index + 1)
        elif mutation == "foreign_bank":
            bank = Budget(8_388_608, 655_360)
        else:
            token = object()
        with pytest.raises(SourceEvidenceError):
            resolve_path_evidence(owner, handle, data, bank, token, 0, path)


def test_graphics_resolution_rejects_closed_analysis_scope():
    from pdfacts.graphics.source_evidence import resolve_path_evidence

    data = source_file()
    bank = Budget(8_388_608, 655_360)
    with classification_identity(data, bank) as (owner, handle, _), pdfacts.open(data) as opened:
        with opened._access() as access:
            access._qpdf_ready().bind_analysis_identity(owner, handle, bank)
        paths, _, token = issue_root_path_evidence(opened, 0, bank, limits=source_limits())
    with pytest.raises(SourceEvidenceError):
        resolve_path_evidence(owner, handle, data, bank, token, 0, paths[0])


def test_payload_depth_is_explicit_and_fails_before_publication():
    with pytest.raises(LimitExceededError) as caught:
        issued(
            b"10 20 m 90 20 l S",
            replace(source_limits(), max_payload_depth=1),
            Budget(8_388_608, 655_360),
        )
    assert caught.value.limit == "evidence_payload_depth"


def test_qpdf_failure_is_translated_without_catching_caller_errors(monkeypatch):
    import pikepdf

    from pdfacts.backends import qpdf
    from pdfacts.core.errors import EngineError

    def failed(*args):
        raise pikepdf.PdfError("source read failed")

    monkeypatch.setattr(qpdf, "_exact_bounded_decoded_stream", failed)
    with pytest.raises(EngineError) as caught:
        issued(b"10 20 m 90 20 l S", source_limits(), Budget(8_388_608, 655_360))
    assert caught.value.engine == "qpdf"
    assert caught.value.detail == "source read failed"
