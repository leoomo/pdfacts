"""Real byte-built PDF, intercepted native boundary, same-test close regression."""

import sys

import pikepdf
import pytest

import pdfacts
from pdfacts.backends.protocols import EngineFailure
from test_source_evidence import Budget, source_file, source_limits


@pytest.mark.parametrize("boundary", ["ancestry_objgen", "raw_stream"])
def test_callback_close_refuses_before_next_native_source_consumer(boundary, monkeypatch):
    callbacks = []
    native_after_close = []

    class ClosingBudget(Budget):
        document = None

        def reserve(self, cost):
            super().reserve(cost)
            caller = sys._getframe(1)
            selected = (
                boundary == "ancestry_objgen"
                and caller.f_code.co_name == "exact_root_resources"
                and cost == 512
            ) or (
                boundary == "raw_stream"
                and caller.f_code.co_name == "_exact_bounded_decoded_stream"
                and cost == caller.f_locals["source_size"]
            )
            if selected and not callbacks:
                callbacks.append(True)
                self.document.close()

    bank = ClosingBudget(8_388_608, 655_360)
    with pdfacts.open(source_file()) as document:
        bank.document = document
        with document._access() as access:
            provider = access._qpdf_ready()
            if boundary == "ancestry_objgen":
                original = pikepdf.Object.objgen

                def observed(obj):
                    if (
                        sys._getframe(1).f_code.co_name == "exact_root_resources"
                        and provider._closed
                    ):
                        native_after_close.append(True)
                        raise AssertionError("intercepted_closed_native_objgen")
                    return original.__get__(obj, type(obj))

                replacement = property(observed)
                monkeypatch.setattr(pikepdf.Object, "objgen", replacement)
                assert pikepdf.Object.objgen is replacement
            else:
                original = pikepdf.Object.read_raw_bytes

                def observed(obj, *args, **kwargs):
                    if (
                        sys._getframe(1).f_code.co_name == "_exact_bounded_decoded_stream"
                        and provider._closed
                    ):
                        native_after_close.append(True)
                        raise AssertionError("intercepted_closed_native_stream")
                    return original(obj, *args, **kwargs)

                monkeypatch.setattr(pikepdf.Object, "read_raw_bytes", observed)
                assert pikepdf.Object.read_raw_bytes is observed
            with pytest.raises(EngineFailure, match="closed"):
                if boundary == "ancestry_objgen":
                    provider.exact_root_resources(0, bank, source_limits())
                else:
                    provider.exact_source_ledger(0, bank, source_limits())
            assert callbacks == [True]
            assert native_after_close == []


_SWEEP_CASES = [
    ("resources", "exact_root_resources", "reserve", 2048, 1),
    ("resources", "exact_root_resources", "reserve", 128, 1),
    ("resources", "exact_root_resources", "reserve", 512, 1),
    ("resources", "exact_root_resources", "charge", 8, 1),
    ("resources", "exact_root_resources", "charge", 1, 1),
    ("stream", "exact_root_stream", "reserve", 5120, 1),
    ("stream", "exact_root_stream", "charge", 4, 1),
    ("array", "exact_root_stream", "reserve", 6144, 1),
    ("array", "exact_root_stream", "charge", 8, 1),
    ("stream", "_exact_bounded_decoded_stream", "charge", 1, 1),
    ("stream", "_exact_bounded_decoded_stream", "reserve", 64, 1),
    ("stream", "_exact_bounded_decoded_stream", "charge", 1, 2),
    ("stream", "_exact_bounded_decoded_stream", "reserve", "source_size", 1),
    ("compressed", "_exact_bounded_decoded_stream", "reserve", "source_size", 1),
    ("compressed", "_exact_bounded_decoded_stream", "charge", 1, 3),
    ("font", "_exact_font_encoding", "reserve", 1024, 1),
    ("font", "_exact_font_encoding", "charge", 8, 1),
    ("font", "_exact_font_encoding", "charge", 3, 1),
    ("font", "_exact_font_encoding", "charge", 2, 1),
    ("marked", "_exact_validate_marked_property", "reserve", 1024, 1),
    ("marked", "_exact_validate_marked_property", "charge", 24, 1),
    ("graphics", "_exact_validate_graphics_state", "reserve", 1024, 1),
    ("graphics", "_exact_validate_graphics_state", "charge", 24, 1),
    ("image", "_exact_image_metadata", "reserve", 8192, 1),
    ("image", "_exact_image_metadata", "charge", 64, 1),
    ("image", "_exact_image_metadata", "charge", 8, 1),
    ("image", "_exact_image_metadata", "charge", 1, 1),
    ("image", "_exact_image_metadata", "charge", 1, 4),
    ("image", "_exact_image_metadata", "charge", 1, 7),
]


def _source_lifetime_fixture(scenario):
    import zlib

    from synthetic.builder import build, stream

    content = b"10 20 m 90 20 l S"
    encoded = zlib.compress(content) if scenario == "compressed" else content
    stream_properties = b"/Filter /FlateDecode" if scenario == "compressed" else b""
    contents = b"[4 0 R 7 0 R]" if scenario == "array" else b"4 0 R"
    resources = (
        b"<< /Font << /F1 5 0 R >> /Properties << /P0 << /MCID 0 >> >> "
        b"/ExtGState << /GS << /Type /ExtGState /OP false /op false /OPM 0 /SA false >> >> "
        b"/XObject << /Im 6 0 R >> >>"
    )
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            + resources
            + b" /Contents "
            + contents
            + b" >>",
            4: stream(encoded, stream_properties),
            5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding "
            b"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [65 /A] >> >>",
            6: stream(
                zlib.compress(b"\x10\x20\x30"),
                b"/Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
            ),
            7: stream(b"10 30 m 90 30 l S"),
        },
        1,
    )


@pytest.mark.parametrize("scenario,function,event,cost,occurrence", _SWEEP_CASES)
def test_source_proxy_callback_close_sweep(
    scenario,
    function,
    event,
    cost,
    occurrence,
    monkeypatch,
):
    from pdfacts.backends import qpdf

    native_after_close = []
    installed = []

    class ClosingBudget(Budget):
        document = None
        armed = False
        matching = 0
        closed_count = 0

        def after(self, kind, value, caller):
            expected_cost = caller.f_locals.get("source_size") if cost == "source_size" else cost
            if (
                self.armed
                and not self.closed_count
                and kind == event
                and caller.f_code.co_name == function
                and value == expected_cost
            ):
                self.matching += 1
                if self.matching == occurrence:
                    self.closed_count += 1
                    self.document.close()

        def reserve(self, value):
            super().reserve(value)
            self.after("reserve", value, sys._getframe(1))

        def charge(self, value=1):
            super().charge(value)
            self.after("charge", value, sys._getframe(1))

    bank = ClosingBudget(8_388_608, 655_360)
    with pdfacts.open(_source_lifetime_fixture(scenario)) as document:
        bank.document = document
        with document._access() as access:
            provider = access._qpdf_ready()
            resources = provider.exact_root_resources(0, bank, source_limits())
            operations = qpdf._ExactResources(provider, resources, bank, source_limits())

            def check_boundary(name, caller):
                if (
                    provider._closed
                    and caller.f_code.co_filename == qpdf.__file__
                    and caller.f_code.co_name != "close"
                ):
                    native_after_close.append(name)
                    raise AssertionError("intercepted_closed_source_proxy")

            def observe_method(name, original):
                def observed(obj, *args, **kwargs):
                    check_boundary(name, sys._getframe(1))
                    return original(obj, *args, **kwargs)

                return observed

            def observe_property(name, original):
                def observed(obj):
                    check_boundary(name, sys._getframe(1))
                    return original.__get__(obj, type(obj))

                return property(observed)

            for name in ("get", "__contains__", "__iter__", "__int__", "read_raw_bytes"):
                original = getattr(pikepdf.Object, name)
                observed = observe_method(name, original)
                monkeypatch.setattr(pikepdf.Object, name, observed)
                assert getattr(pikepdf.Object, name) is observed
                installed.append(name)
            for name in ("objgen", "is_indirect", "stream_dict"):
                original = getattr(pikepdf.Object, name)
                observed = observe_property(name, original)
                monkeypatch.setattr(pikepdf.Object, name, observed)
                assert getattr(pikepdf.Object, name) is observed
                installed.append(name)
            meta = type(pikepdf.Dictionary)
            original_instancecheck = meta.__instancecheck__

            def observed_instancecheck(cls, obj):
                check_boundary("native_type_check", sys._getframe(1))
                return original_instancecheck(cls, obj)

            monkeypatch.setattr(meta, "__instancecheck__", observed_instancecheck)
            assert meta.__instancecheck__ is observed_instancecheck
            installed.append("native_type_check")
            assert len(installed) == 9
            bank.armed = True
            with pytest.raises(EngineFailure, match="closed"):
                if scenario == "resources":
                    provider.exact_root_resources(0, bank, source_limits())
                elif scenario in ("stream", "array", "compressed"):
                    provider.exact_source_ledger(0, bank, source_limits())
                elif scenario == "font":
                    operations.font_encoding("/F1", bank)
                elif scenario == "marked":
                    operations.marked_property("/Figure", "/P0", bank)
                elif scenario == "graphics":
                    operations.graphics_state("/GS", bank)
                else:
                    operations.image_metadata("/Im", bank)
            assert bank.closed_count == 1
            assert native_after_close == []
