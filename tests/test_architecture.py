"""The import boundaries hold, and the checker really detects each violation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from check_architecture import find_violations  # noqa: E402


def write(root: Path, relative: str, source: str) -> None:
    path = root / "pdfacts" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def rules(root: Path) -> list[tuple[str, str, str]]:
    return [(v.rule, v.path, v.module) for v in find_violations(root)]


def test_source_tree_has_no_violations():
    assert find_violations(ROOT / "src") == []


def test_source_tree_is_actually_scanned():
    # Guards against a checker that passes because it looked at nothing.
    scanned = sorted(p.name for p in (ROOT / "src" / "pdfacts").rglob("*.py"))
    assert "pdfium.py" in scanned and "qpdf.py" in scanned


def test_engine_owners_may_import_their_engine(tmp_path):
    write(tmp_path, "backends/pdfium.py", "import pypdfium2\nimport pypdfium2.raw\n")
    write(tmp_path, "backends/qpdf.py", "import pikepdf\n")
    assert rules(tmp_path) == []


@pytest.mark.parametrize(
    ("relative", "source", "expected"),
    [
        (
            "text/glyphs.py",
            "import pypdfium2\n",
            ("engine-boundary", "text/glyphs.py", "pypdfium2"),
        ),
        (
            "text/glyphs.py",
            "from pypdfium2 import raw\n",
            ("engine-boundary", "text/glyphs.py", "pypdfium2"),
        ),
        (
            "backends/pdfium.py",
            "import pikepdf\n",
            ("engine-boundary", "backends/pdfium.py", "pikepdf"),
        ),
        (
            "backends/qpdf.py",
            "import pypdfium2\n",
            ("engine-boundary", "backends/qpdf.py", "pypdfium2"),
        ),
        (
            "forms/reader.py",
            "from pikepdf import Pdf\n",
            ("engine-boundary", "forms/reader.py", "pikepdf"),
        ),
        ("graphics/paths.py", "import fitz\n", ("forbidden", "graphics/paths.py", "fitz")),
        ("backends/pdfium.py", "import pymupdf\n", ("forbidden", "backends/pdfium.py", "pymupdf")),
        (
            "core/document.py",
            "from pdfacts.text import layout\n",
            ("core-isolation", "core/document.py", "pdfacts.text"),
        ),
        (
            "core/document.py",
            "from pdfacts import annotations\n",
            ("core-isolation", "core/document.py", "pdfacts.annotations"),
        ),
        (
            "core/document.py",
            "from ..forms import model\n",
            ("core-isolation", "core/document.py", "pdfacts.forms"),
        ),
        (
            "core/document.py",
            "def load():\n    import pdfacts.render\n",
            ("core-isolation", "core/document.py", "pdfacts.render"),
        ),
    ],
)
def test_each_violation_is_detected(tmp_path, relative, source, expected):
    write(tmp_path, relative, source)
    assert rules(tmp_path) == [expected]


def test_allowed_imports_are_not_flagged(tmp_path):
    write(
        tmp_path,
        "core/document.py",
        "from pdfacts.backends import pdfium\nfrom . import geometry\n",
    )
    write(
        tmp_path,
        "text/layout.py",
        "from pdfacts.core import geometry\nfrom __future__ import annotations\n",
    )
    write(tmp_path, "forms/reader.py", "from pdfacts.annotations import model\n")
    assert rules(tmp_path) == []
