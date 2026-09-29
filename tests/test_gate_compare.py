"""The comparison of gate receipts: what it refuses and what it reports.

The receipts are built here in the shape of section 2.3 of the #14 handover;
no test reads a receipt from disk that some other tool wrote.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import compare  # noqa: E402

ENGINE_INFO = {
    "font_policy": "embedded-only",
    "pdfacts": "0.0.1",
    "pdfium": "153.0.7999.0",
    "pdfium_flags": [],
    "pdfium_origin": "pdfium-binaries",
    "pikepdf": "10.14.0",
    "pypdfium2": "5.13.0",
    "qpdf": "12.4.1",
}


def file_entry(**changes) -> dict:
    entry = {
        "cross_check_failed": [],
        "geometry": "1" * 64,
        "glyph_count": 3,
        "glyphs": "2" * 64,
        "page_count": 2,
        "warning_count": 0,
    }
    entry.update(changes)
    return entry


def receipt(**changes) -> dict:
    """A fresh receipt with two files, `a.pdf` and `b.pdf`, and changes applied."""
    data = {
        "environment": {
            "commit": "0" * 40,
            "date": "2026-09-29T08:15:00+00:00",
            "engine_info": dict(ENGINE_INFO),
            "machine": "arm64",
            "platform": "macOS-15-arm64-arm64",
            "python": "3.13.5",
        },
        "errors": {},
        "files": {"a.pdf": file_entry(), "b.pdf": file_entry()},
        "partial": False,
        "seconds": 21.3,
    }
    data.update(changes)
    return data


def run(tmp_path, capsys, *receipts):
    """Write the receipts, compare them, and return the status and the output."""
    paths = []
    for number, content in enumerate(receipts):
        path = tmp_path / f"receipt-{number}.json"
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(str(path))
    return compare.main(paths), capsys.readouterr()


def row(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def test_identical_receipts(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, receipt(), receipt())
    assert status == 0
    for column in ("geometry", "glyphs"):
        assert f"{column}: identical in 2 of 2 files" in captured.out


@pytest.mark.parametrize("key", ["geometry", "glyphs"])
def test_a_digest_differs(tmp_path, capsys, key):
    first, second = receipt(), receipt()
    second["files"]["b.pdf"][key] = "4" * 64
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 1
    assert row(["a.pdf", "identical", "identical"]) in captured.out
    cells = ["identical"] * 2
    cells[["geometry", "glyphs"].index(key)] = "differs"
    assert row(["b.pdf", *cells]) in captured.out


def test_the_third_receipt_is_compared(tmp_path, capsys):
    receipts = receipt(), receipt(), receipt()
    receipts[2]["files"]["a.pdf"]["glyphs"] = "4" * 64
    status, captured = run(tmp_path, capsys, *receipts)
    assert status == 1
    assert row(["a.pdf", "identical", "differs"]) in captured.out


def test_a_file_missing_in_a_later_receipt(tmp_path, capsys):
    first, second = receipt(), receipt()
    del second["files"]["b.pdf"]
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 1
    assert row(["b.pdf", "missing", "missing"]) in captured.out


def test_a_file_only_in_a_later_receipt(tmp_path, capsys):
    first, second = receipt(), receipt()
    second["files"]["c.pdf"] = file_entry()
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 1
    assert row(["c.pdf", "missing", "missing"]) in captured.out


def test_a_file_failed_on_one_platform(tmp_path, capsys):
    first, second = receipt(), receipt()
    del second["files"]["b.pdf"]
    second["errors"]["b.pdf"] = "DamagedFileError"
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 1
    assert row(["b.pdf", "error", "error"]) in captured.out


def test_a_file_failed_alike_everywhere(tmp_path, capsys):
    first, second = receipt(), receipt()
    for data in (first, second):
        del data["files"]["b.pdf"]
        data["errors"]["b.pdf"] = "GlyphReadError"
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 1
    assert row(["b.pdf", "error", "error"]) in captured.out


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("font_policy", "host"),
        ("pdfacts", "0.0.2"),
        ("pdfium", "153.0.8000.0"),
        ("pdfium_flags", ["v8"]),
        ("pdfium_origin", "chromium"),
        ("pikepdf", "10.15.0"),
        ("pypdfium2", "5.14.0"),
        ("qpdf", "12.5.0"),
    ],
)
def test_different_engines(tmp_path, capsys, key, value):
    first, second = receipt(), receipt()
    second["environment"]["engine_info"][key] = value
    status, captured = run(tmp_path, capsys, first, second)
    assert status == 2
    assert key in captured.err


@pytest.mark.parametrize("partial", [True, 1, "yes", None])
def test_a_partial_receipt(tmp_path, capsys, partial):
    status, _ = run(tmp_path, capsys, receipt(), receipt(partial=partial))
    assert status == 2


def test_different_commits(tmp_path, capsys):
    first, second = receipt(), receipt()
    second["environment"]["commit"] = "1" * 40
    status, _ = run(tmp_path, capsys, first, second)
    assert status == 2


def test_no_files(tmp_path, capsys):
    status, _ = run(tmp_path, capsys, receipt(files={}, errors={}), receipt(files={}, errors={}))
    assert status == 2


def test_one_receipt(tmp_path, capsys):
    status, _ = run(tmp_path, capsys, receipt())
    assert status == 2


def _as_a_list(data):
    return [data]


def _without_files(data):
    return {key: value for key, value in data.items() if key != "files"}


def _without_a_file_digest(data):
    del data["files"]["a.pdf"]["glyphs"]
    return data


def _without_seconds(data):
    del data["seconds"]
    return data


def _without_platform(data):
    del data["environment"]["platform"]
    return data


def _page_count_as_text(data):
    data["files"]["a.pdf"]["page_count"] = "2"
    return data


def _cross_check_failed_not_a_list(data):
    data["files"]["a.pdf"]["cross_check_failed"] = 2
    return data


@pytest.mark.parametrize(
    "mangle",
    [
        _as_a_list,
        _without_files,
        _without_a_file_digest,
        _without_seconds,
        _without_platform,
        _page_count_as_text,
        _cross_check_failed_not_a_list,
    ],
    ids=[
        "a json list",
        "without files",
        "entry without glyphs",
        "without seconds",
        "without platform",
        "page_count as text",
        "cross_check_failed not a list",
    ],
)
def test_not_a_receipt(tmp_path, capsys, mangle):
    status, _ = run(tmp_path, capsys, receipt(), mangle(receipt()))
    assert status == 2


def test_not_json(tmp_path, capsys):
    status, _ = run(tmp_path, capsys, receipt(), "{")
    assert status == 2
