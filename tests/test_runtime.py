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
from pdfacts import ConfigurationError, FontPolicy, configure
from pdfacts.core import runtime

TESTS = Path(__file__).resolve().parent

# Glyph boxes of the page cases.verdana_text() with the policy installed,
# measured on macOS arm64 and Linux arm64 (identical there). Compared at 0.001.
# A host without Verdana gives the same values without the policy; the pinned
# Linux image has no fonts at all. There these values cannot show that the
# policy is missing: the test of the structure handed to PDFium covers that.
FIRST_BOX = (42.016, 200.0, 54.832, 217.016)
LAST_ORIGIN = (292.696, 200.0)

# Where macOS keeps Verdana. PDFium finds it there when host fonts are used.
VERDANA = Path("/System/Library/Fonts/Supplemental/Verdana.ttf")

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


def read_glyphs(*, start: bool) -> dict:
    """The first glyph box and the last origin, read in a fresh process."""
    starting = "from pdfacts.core import runtime\nruntime.start()\n" if start else ""
    code = (
        READ_GLYPHS.format(tests=str(TESTS))
        + starting
        + """
boxes, origins = glyphs()
print(json.dumps({"first_box": boxes[0], "last_origin": origins[-1]}))
"""
    )
    return run(code)


def test_with_the_policy_the_glyphs_are_the_measured_ones():
    seen = read_glyphs(start=True)
    assert close(seen["first_box"], FIRST_BOX), seen
    assert close(seen["last_origin"], LAST_ORIGIN), seen


@pytest.mark.skipif(
    not VERDANA.exists(),
    reason="Verdana is not where macOS keeps it; elsewhere this test cannot tell "
    "whether the host has the font",
)
def test_on_a_host_with_verdana_the_policy_changes_the_glyphs():
    without = read_glyphs(start=False)
    with_policy = read_glyphs(start=True)
    assert not close(without["last_origin"], with_policy["last_origin"]), (without, with_policy)


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
    assert run(code) == {"before": None, "still_off": True, "after": "embedded-only"}


def test_start_is_idempotent():
    code = """
import json
from pdfacts.backends import pdfium
from pdfacts.core import runtime
runtime.start()
runtime.start()
runtime.configure(font_policy="embedded-only")
print(json.dumps({
    "installed": pdfium._font_policy_installed,
    "handed_to_pdfium": len(pdfium._FONT_INFOS),
    "active": runtime.active_font_policy(),
}))
"""
    assert run(code) == {"installed": True, "handed_to_pdfium": 1, "active": "embedded-only"}


def test_start_holds_the_engine_lock_while_it_installs():
    # Another thread must not get the lock while start() calls into PDFium.
    code = """
import json, threading
from pdfacts.backends import pdfium
from pdfacts.core import runtime
original = pdfium.install_font_policy
held = []
def watching(policy):
    got = []
    def probe():
        if runtime.engine_lock.acquire(blocking=False):
            runtime.engine_lock.release()
            got.append(True)
        else:
            got.append(False)
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    held.append(not got[0])
    return original(policy)
pdfium.install_font_policy = watching
runtime.start()
print(json.dumps({"held": held}))
"""
    assert run(code) == {"held": [True]}


def test_the_engine_lock_is_free_after_every_way_out():
    # A path that kept the lock would make every engine call from another
    # thread wait for ever.
    code = """
import json, threading
from pdfacts import ConfigurationError, FontPolicy
from pdfacts.backends import pdfium
from pdfacts.core import runtime

def free():
    got = []
    def probe():
        if runtime.engine_lock.acquire(blocking=False):
            runtime.engine_lock.release()
            got.append(True)
        else:
            got.append(False)
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return got[0]

seen = {}
runtime.configure(font_policy="embedded-only")
seen["configured"] = free()
original = pdfium.install_font_policy
def failing(policy):
    raise RuntimeError("the engine refused")
pdfium.install_font_policy = failing
try:
    runtime.start()
except RuntimeError:
    pass
seen["start failed"] = free()
pdfium.install_font_policy = original
runtime.start()
seen["started"] = free()
runtime.start()
seen["started again"] = free()
runtime.configure(font_policy="embedded-only")
seen["configured after the start"] = free()
runtime._active = object()  # a started runtime with another policy
try:
    runtime.configure(font_policy="embedded-only")
except ConfigurationError:
    pass
seen["configure refused"] = free()
print(json.dumps(seen))
"""
    assert run(code) == {
        "configured": True,
        "start failed": True,
        "started": True,
        "started again": True,
        "configured after the start": True,
        "configure refused": True,
    }


def test_the_backend_installs_the_font_info_once():
    # PDFium ignores a font info whose version is not 1 or 2 without an error,
    # so the structure itself is checked: on a host without the font, the
    # glyph values could not tell.
    code = """
import json
import pypdfium2.raw as c
from pdfacts.backends import pdfium
calls = []
original = c.FPDF_SetSystemFontInfo
def counting(info):
    calls.append(info._obj)
    return original(info)
c.FPDF_SetSystemFontInfo = counting
pdfium.install_font_policy("embedded-only")
pdfium.install_font_policy("embedded-only")
passed = calls[0]
names = [name for name, _ in type(passed)._fields_ if name != "version"]
print(json.dumps({
    "calls": len(calls),
    "kept": len(pdfium._FONT_INFOS) == 1 and pdfium._FONT_INFOS[0] is passed,
    "version": passed.version,
    "callbacks": [name for name in names if getattr(passed, name)],
}))
"""
    assert run(code) == {"calls": 1, "kept": True, "version": 1, "callbacks": []}


def test_a_font_info_handed_to_pdfium_is_never_dropped():
    # An exception can arrive right after PDFium took the pointer, raised by a
    # signal handler for example. The installation then counts as not done and
    # is repeated, and both structures stay referenced.
    code = """
import json
import pypdfium2.raw as c
from pdfacts.backends import pdfium
original = c.FPDF_SetSystemFontInfo
during = []
def interrupted(info):
    during.append(any(kept is info._obj for kept in pdfium._FONT_INFOS))
    original(info)
    raise TimeoutError("raised by a signal handler after the call")
c.FPDF_SetSystemFontInfo = interrupted
try:
    pdfium.install_font_policy("embedded-only")
except TimeoutError:
    pass
after_failure = [pdfium._font_policy_installed, len(pdfium._FONT_INFOS)]
c.FPDF_SetSystemFontInfo = original
pdfium.install_font_policy("embedded-only")
after_retry = [pdfium._font_policy_installed, len(pdfium._FONT_INFOS)]
print(json.dumps({"during": during, "after_failure": after_failure, "after_retry": after_retry}))
"""
    assert run(code) == {
        "during": [True],
        "after_failure": [False, 1],
        "after_retry": [True, 2],
    }


def test_a_start_that_failed_can_be_repeated():
    code = """
import json
from pdfacts.backends import pdfium
from pdfacts.core import runtime
original = pdfium.install_font_policy
def failing(policy):
    raise RuntimeError("the engine refused")
pdfium.install_font_policy = failing
try:
    runtime.start()
    raised = False
except RuntimeError:
    raised = True
after_failure = runtime.active_font_policy()
pdfium.install_font_policy = original
runtime.start()
print(json.dumps({
    "raised": raised,
    "after_failure": after_failure,
    "after_retry": runtime.active_font_policy(),
    "installed": pdfium._font_policy_installed,
}))
"""
    assert run(code) == {
        "raised": True,
        "after_failure": None,
        "after_retry": "embedded-only",
        "installed": True,
    }


def test_the_backend_refuses_a_policy_it_does_not_know():
    code = """
import json
from pdfacts.backends import pdfium
try:
    pdfium.install_font_policy("host")
    print(json.dumps({"raised": False}))
except ValueError:
    print(json.dumps({
        "raised": True,
        "installed": pdfium._font_policy_installed,
        "handed_to_pdfium": len(pdfium._FONT_INFOS),
    }))
"""
    assert run(code) == {"raised": True, "installed": False, "handed_to_pdfium": 0}


@pytest.mark.parametrize("started", [False, True], ids=["before the start", "after it"])
def test_a_name_that_is_no_policy_is_refused(monkeypatch, started):
    # "host" named a policy that was never implemented and is gone (issue A2).
    if started:
        monkeypatch.setattr(runtime, "_active", FontPolicy.EMBEDDED_ONLY)
    for name in ("host", "bogus"):
        with pytest.raises(ValueError):
            configure(font_policy=name)
    assert [policy.value for policy in FontPolicy] == ["embedded-only"]


def test_an_unknown_policy_name_is_refused():
    with pytest.raises(ValueError):
        configure(font_policy="whatever")


def test_configuring_another_policy_after_the_start_is_an_error(monkeypatch):
    # Only one policy exists, so a started runtime with another one is simulated.
    monkeypatch.setattr(runtime, "_active", object())
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
    refused: list[str] = []

    def work():
        # With a timeout, a lock left held by an earlier test fails this test
        # instead of hanging the run.
        if not runtime.engine_lock.acquire(timeout=5):
            refused.append(threading.current_thread().name)
            return
        try:
            start = time.monotonic()
            time.sleep(0.05)
            intervals.append((start, time.monotonic()))
        finally:
            runtime.engine_lock.release()

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not refused, f"the engine lock was held elsewhere: {refused}"
    (first_start, first_end), (second_start, second_end) = sorted(intervals)
    assert first_end <= second_start


def test_the_embedded_only_policy_is_now_a_capability():
    assert pdfacts.capabilities()[pdfacts.Capability.FONT_POLICY_EMBEDDED_ONLY] is True


def test_configure_and_font_policy_are_exported_and_start_is_not():
    assert {"configure", "FontPolicy"} <= set(pdfacts.__all__)
    assert "start" not in pdfacts.__all__ and "engine_lock" not in pdfacts.__all__
