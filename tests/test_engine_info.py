"""The running engines are the pinned ones, and both really work."""

from __future__ import annotations

import io
import re
import tomllib
from importlib import metadata
from pathlib import Path

import pikepdf
import pypdfium2

import pdfacts

ROOT = Path(__file__).resolve().parent.parent


def pinned() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = {}
    for requirement in project["dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", requirement)
        assert match, f"dependency is not pinned exactly: {requirement}"
        pins[match.group(1)] = match.group(2)
    return pins


def test_engines_match_the_pins():
    info = pdfacts.engine_info()
    pins = pinned()
    assert info.pypdfium2 == pins["pypdfium2"]
    assert info.pikepdf == pins["pikepdf"]
    assert info.pdfacts == pdfacts.__version__


def test_the_version_is_the_project_s():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert pdfacts.__version__ == project["version"] == metadata.version("pdfacts")


def test_engine_identity_is_complete():
    info = pdfacts.engine_info()
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", info.pdfium), info.pdfium
    assert re.fullmatch(r"\d+\.\d+\.\d+", info.qpdf), info.qpdf
    assert info.pdfium_origin
    # The engine must not be able to run document scripts.
    assert "V8" not in info.pdfium_flags and "XFA" not in info.pdfium_flags
    assert set(info.as_dict()) == {
        "pdfacts", "pypdfium2", "pdfium", "pdfium_origin", "pdfium_flags", "pikepdf", "qpdf",
        "font_policy",
    }  # fmt: skip


def test_both_engines_read_a_document_built_here():
    # Built in memory: the expected values are the construction parameters.
    document = pikepdf.new()
    document.add_blank_page(page_size=(400, 300))
    document.add_blank_page(page_size=(200, 500))
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    with pikepdf.open(io.BytesIO(data)) as reopened:
        assert len(reopened.pages) == 2

    loaded = pypdfium2.PdfDocument(data)
    try:
        assert len(loaded) == 2
        assert [tuple(round(v, 3) for v in loaded[i].get_size()) for i in range(2)] == [
            (400.0, 300.0),
            (200.0, 500.0),
        ]
    finally:
        loaded.close()
