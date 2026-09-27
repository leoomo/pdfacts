"""The runtime: engine start, font policy and serialized engine calls.

The font policy is state of the whole process, so every test that starts the
runtime runs in a process of its own and reports what it saw as JSON. Module
state is never reset between tests; that would test the reset, not the
runtime.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import pdfacts
from pdfacts import ConfigurationError, FontPolicy, UnsupportedError, configure
from pdfacts.core import runtime

TESTS = Path(__file__).resolve().parent

# Glyph boxes of the page cases.verdana_text() with the policy installed,
# measured on macOS arm64 and Linux arm64 (identical there). Compared at 0.001.
FIRST_BOX = (42.016, 200.0, 54.832, 217.016)
LAST_ORIGIN = (292.696, 200.0)

READ_GLYPHS = """
import ctypes, json, sys
sys.path.insert(0, {tests!r})
from synthetic import cases
import pypdfium2
import pypdfium2.raw as c

def glyphs():
    document = pypdfium2.PdfDocument(cases.verdana_text().data)
    page = document[0]
    text = page.get_textpage()
    boxes, origins = [], []
    for i in range(c.FPDFText_CountChars(text.raw)):
        left, right, bottom, top = (ctypes.c_double() for _ in range(4))
        c.FPDFText_GetCharBox(text.raw, i, left, right, bottom, top)
        x, y = ctypes.c_double(), ctypes.c_double()
        c.FPDFText_GetCharOrigin(text.raw, i, x, y)
        boxes.append((left.value, bottom.value, right.value, top.value))
        origins.append((x.value, y.value))
    text.close(); page.close(); document.close()
    return boxes, origins
"""


def run(code: str) -> dict:
    """Run `code` in a fresh interpreter; it prints one JSON object."""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def close(first, second, tolerance=0.001):
    return all(abs(a - b) <= tolerance for a, b in zip(first, second, strict=True))


def test_with_the_policy_the_glyphs_are_the_measured_ones():
    code = (
        READ_GLYPHS.format(tests=str(TESTS))
        + """
from pdfacts.core import runtime
runtime.start()
boxes, origins = glyphs()
print(json.dumps({"first_box": boxes[0], "last_origin": origins[-1]}))
"""
    )
    seen = run(code)
    assert close(seen["first_box"], FIRST_BOX), seen
    assert close(seen["last_origin"], LAST_ORIGIN), seen


def test_rendering_works_with_the_policy_installed():
    # The policy installs a font info whose callbacks are all null. PDFium must
    # fall back to its built-in fonts and still draw the text.
    code = f"""
import json, sys
sys.path.insert(0, {str(TESTS)!r})
from synthetic import cases
from pdfacts.core import runtime
runtime.start()
import pypdfium2
document = pypdfium2.PdfDocument(cases.verdana_text().data)
bitmap = document[0].render(scale=1, grayscale=True)
dark = sum(1 for value in bytes(bitmap.buffer) if value < 128)
print(json.dumps({{"dark": dark}}))
"""
    assert run(code)["dark"] > 100


def test_engine_info_names_the_policy_and_does_not_start_the_runtime():
    code = """
import json
import pdfacts
from pdfacts.core import runtime
before = pdfacts.engine_info().font_policy
still_off = runtime.active_font_policy() is None
runtime.start()
after = pdfacts.engine_info().font_policy
print(json.dumps({"before": before, "still_off": still_off, "after": after}))
"""
    assert run(code) == {"before": "not-started", "still_off": True, "after": "embedded-only"}


def test_start_is_idempotent():
    code = """
import json
from pdfacts.backends import pdfium
from pdfacts.core import runtime
runtime.start()
first = pdfium._FONT_INFO
runtime.start()
runtime.configure(font_policy="embedded-only")
print(json.dumps({"same": pdfium._FONT_INFO is first, "kept": first is not None}))
"""
    assert run(code) == {"same": True, "kept": True}


def test_the_backend_installs_the_font_info_once():
    code = """
import json
import pypdfium2.raw as c
from pdfacts.backends import pdfium
calls = []
original = c.FPDF_SetSystemFontInfo
def counting(info):
    calls.append(info)
    return original(info)
c.FPDF_SetSystemFontInfo = counting
pdfium.install_font_policy("embedded-only")
first = pdfium._FONT_INFO
pdfium.install_font_policy("embedded-only")
print(json.dumps({"calls": len(calls), "same": pdfium._FONT_INFO is first}))
"""
    assert run(code) == {"calls": 1, "same": True}


def test_the_backend_refuses_a_policy_it_does_not_know():
    code = """
import json
from pdfacts.backends import pdfium
try:
    pdfium.install_font_policy("host")
    print(json.dumps({"raised": False}))
except ValueError:
    print(json.dumps({"raised": True, "installed": pdfium._FONT_INFO is not None}))
"""
    assert run(code) == {"raised": True, "installed": False}


def test_the_host_policy_is_unsupported():
    with pytest.raises(UnsupportedError) as raised:
        configure(font_policy=FontPolicy.HOST)
    assert raised.value.capability is pdfacts.Capability.FONT_POLICY_HOST
    with pytest.raises(UnsupportedError):
        configure(font_policy="host")


def test_an_unknown_policy_name_is_refused():
    with pytest.raises(ValueError):
        configure(font_policy="whatever")


def test_configuring_another_policy_after_the_start_is_an_error(monkeypatch):
    # Only one policy exists, so a started runtime with another one is simulated.
    monkeypatch.setattr(runtime, "_active", FontPolicy.HOST)
    with pytest.raises(ConfigurationError):
        configure(font_policy=FontPolicy.EMBEDDED_ONLY)


def test_configuring_the_active_policy_after_the_start_changes_nothing(monkeypatch):
    monkeypatch.setattr(runtime, "_active", FontPolicy.EMBEDDED_ONLY)
    configure(font_policy=FontPolicy.EMBEDDED_ONLY)
    assert runtime.active_font_policy() is FontPolicy.EMBEDDED_ONLY


def test_the_engine_lock_is_reentrant():
    # With a timeout, a lock that is not reentrant fails the test instead of hanging it.
    assert runtime.engine_lock.acquire(timeout=1)
    try:
        assert runtime.engine_lock.acquire(timeout=1)
        runtime.engine_lock.release()
    finally:
        runtime.engine_lock.release()


def test_two_threads_holding_the_engine_lock_do_not_overlap():
    intervals: list[tuple[float, float]] = []

    def work():
        with runtime.engine_lock:
            start = time.monotonic()
            time.sleep(0.05)
            intervals.append((start, time.monotonic()))

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    (first_start, first_end), (second_start, second_end) = sorted(intervals)
    assert first_end <= second_start


def test_the_embedded_only_policy_is_now_a_capability():
    assert pdfacts.capabilities()[pdfacts.Capability.FONT_POLICY_EMBEDDED_ONLY] is True
    assert pdfacts.capabilities()[pdfacts.Capability.FONT_POLICY_HOST] is False


def test_configure_and_font_policy_are_exported_and_start_is_not():
    assert {"configure", "FontPolicy"} <= set(pdfacts.__all__)
    assert "start" not in pdfacts.__all__ and "engine_lock" not in pdfacts.__all__
