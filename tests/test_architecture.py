"""The import boundaries hold, and the checker really detects each violation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from check_architecture import find_forbidden, find_violations, main  # noqa: E402


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
        (
            "core/document.py",
            "from pdfacts.inspection import report\n",
            ("core-isolation", "core/document.py", "pdfacts.inspection"),
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


# The rule "forbidden" also covers tests/ and tools/.


def put(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def forbidden(root: Path) -> list[tuple[str, str, str]]:
    return [(v.rule, v.path, v.module) for v in find_forbidden(root, ("tests", "tools"))]


@pytest.fixture
def repository(tmp_path):
    # Both directories exist, so that only the files a test adds are scanned.
    put(tmp_path, "tests/__init__.py", "")
    put(tmp_path, "tools/__init__.py", "")
    return tmp_path


def test_a_test_that_imports_pymupdf_is_found(repository):
    put(repository, "tests/test_x.py", "import fitz\n")
    assert forbidden(repository) == [("forbidden", "tests/test_x.py", "fitz")]


def test_a_tool_that_imports_pymupdf_inside_a_function_is_found(repository):
    put(repository, "tools/t.py", "def f():\n    import pymupdf\n")
    assert forbidden(repository) == [("forbidden", "tools/t.py", "pymupdf")]


def test_an_import_in_a_nested_directory_is_found(repository):
    put(repository, "tests/a/b/test_y.py", "from mupdf import x\n")
    assert forbidden(repository) == [("forbidden", "tests/a/b/test_y.py", "mupdf")]


def test_the_engines_are_allowed_in_tests(repository):
    put(repository, "tests/test_z.py", "import pikepdf\nimport pypdfium2\n")
    assert forbidden(repository) == []


def test_bench_is_not_scanned(repository):
    put(repository, "bench/compare.py", "import fitz\n")
    assert forbidden(repository) == []


def test_the_real_tests_and_tools_are_clean():
    assert find_forbidden(ROOT, ("tests", "tools")) == []


def test_the_real_tests_and_tools_are_actually_scanned():
    # Guards against a check that passes because it looked at nothing.
    for directory in ("tests", "tools"):
        assert list((ROOT / directory).rglob("*.py")), directory


def repository_with(tmp_path: Path, *directories: str) -> Path:
    put(tmp_path, "src/pdfacts/__init__.py", "")
    for directory in directories:
        put(tmp_path, f"{directory}/module.py", "")
    return tmp_path


@pytest.mark.parametrize("missing", ["tests", "tools"])
def test_a_missing_directory_is_an_error(tmp_path, missing):
    present = {"tests", "tools"} - {missing}
    root = repository_with(tmp_path, *present)
    assert main(["check_architecture.py", str(root / "src")]) == 2


@pytest.mark.parametrize("directory", ["tests", "tools"])
def test_a_violation_in_tests_or_tools_fails_the_whole_check(tmp_path, directory):
    root = repository_with(tmp_path, "tests", "tools")
    put(root, f"{directory}/uses_mupdf.py", "import fitz\n")
    assert main(["check_architecture.py", str(root / "src")]) == 1


def test_a_violation_in_the_library_fails_the_whole_check(tmp_path):
    root = repository_with(tmp_path, "tests", "tools")
    put(root, "src/pdfacts/core/document.py", "import pikepdf\n")
    assert main(["check_architecture.py", str(root / "src")]) == 1


def test_a_relative_source_root_finds_the_directories_next_to_it(tmp_path, monkeypatch):
    root = repository_with(tmp_path, "tests", "tools")
    monkeypatch.chdir(root / "src")
    assert main(["check_architecture.py", "."]) == 0


def test_a_clean_repository_passes_the_whole_check(tmp_path):
    put(tmp_path, "src/pdfacts/__init__.py", "")
    put(tmp_path, "tests/test_x.py", "import pikepdf\n")
    put(tmp_path, "tools/t.py", "")
    assert main(["check_architecture.py", str(tmp_path / "src")]) == 0
