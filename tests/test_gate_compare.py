"""The comparison of gate receipts: what it refuses and what it reports.

The receipts are built here in the shapes bench/gates/m1.py, m4a.py and m4b.py
write; no test reads a receipt from disk that some other tool wrote.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import compare  # noqa: E402
import m1  # noqa: E402
import m4a  # noqa: E402
import m4b  # noqa: E402

from synthetic import cases  # noqa: E402

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


def m4a_entry(**changes) -> dict:
    entry = {
        "breaks": {"after white space forward": 1},
        "glyph_count": 3,
        "glyphs": "2" * 64,
        "known": {"glyphs not upright": 0, "left out": 1},
        "line_count": 1,
        "lines": "4" * 64,
        "page_count": 2,
        "pages_not_compared": 0,
        "pdfium_differences": [],
        "unknown": {},
        "word_count": 2,
        "words": "3" * 64,
    }
    entry.update(changes)
    return entry


def m4b_entry(**changes) -> dict:
    entry = {
        "agreement": {"PDFium: same text": 3, "V2: other": 1},
        "glyph_count": 3,
        "glyphs": "2" * 64,
        "line_count": 1,
        "lines": "4" * 64,
        "page_count": 2,
        "pages_unknown": [],
        "render": {"V2 judged": 2, "V2 correct": 2, "D judged": 2, "D correct": 1},
        "unknown": {},
        "word_count": 2,
        "words": "3" * 64,
    }
    entry.update(changes)
    return entry


def receipt(**changes) -> dict:
    """A fresh receipt of the M1 gate with two files, `a.pdf` and `b.pdf`, and
    changes applied."""
    data = {
        "gate": "m1",
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


def m4a_receipt(**changes) -> dict:
    """A fresh receipt of the M4a gate with two files, and changes applied."""
    return receipt(gate="m4a", files={"a.pdf": m4a_entry(), "b.pdf": m4a_entry()}, **changes)


def m4b_receipt(**changes) -> dict:
    """A fresh receipt of the M4b gate with two files, and changes applied."""
    return receipt(gate="m4b", files={"a.pdf": m4b_entry(), "b.pdf": m4b_entry()}, **changes)


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
    lines = captured.out.splitlines()
    assert "| file | geometry | glyphs |" in lines
    assert "| --- | --- | --- |" in lines
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


def _without_gate(data):
    del data["gate"]
    return data


def _a_gate_not_known(data):
    data["gate"] = "m2"
    return data


def _a_gate_not_a_string(data):
    data["gate"] = ["m1"]
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
        _without_gate,
        _a_gate_not_known,
        _a_gate_not_a_string,
    ],
    ids=[
        "a json list",
        "without files",
        "entry without glyphs",
        "without seconds",
        "without platform",
        "page_count as text",
        "cross_check_failed not a list",
        "without gate",
        "a gate not known",
        "a gate not a string",
    ],
)
def test_not_a_receipt(tmp_path, capsys, mangle):
    status, _ = run(tmp_path, capsys, receipt(), mangle(receipt()))
    assert status == 2


def test_not_json(tmp_path, capsys):
    status, _ = run(tmp_path, capsys, receipt(), "{")
    assert status == 2


def test_the_numbers_of_each_receipt(tmp_path, capsys):
    first, second = receipt(), m4a_receipt()
    first["files"]["b.pdf"]["cross_check_failed"] = [0, 1]
    second["files"]["b.pdf"]["pdfium_differences"] = ["1"]
    _, captured = run(tmp_path, capsys, first, receipt())
    assert "  2 files processed, 0 errors, 4 pages, 6 glyphs, 0 warnings, 21.3 s" in captured.out
    assert "  pages whose cross-check failed: b.pdf: 0, 1" in captured.out
    _, captured = run(tmp_path, capsys, second, m4a_receipt())
    assert (
        "  2 files processed, 0 errors, 4 pages, 6 glyphs, 4 words, 2 lines,"
        " 0 pages not compared, 21.3 s" in captured.out
    )
    assert "  pages whose characters differ from PDFium's: b.pdf: 1" in captured.out


def test_identical_receipts_of_the_m4a_gate(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, m4a_receipt(), m4a_receipt(), m4a_receipt())
    assert status == 0
    lines = captured.out.splitlines()
    assert "| file | glyphs | words | lines | tallies |" in lines
    assert row(["a.pdf", "identical", "identical", "identical", "identical"]) in lines
    for column in ("glyphs", "words", "lines", "tallies"):
        assert f"{column}: identical in 2 of 2 files" in captured.out


@pytest.mark.parametrize(
    ("key", "value", "column"),
    [
        ("glyphs", "5" * 64, "glyphs"),
        ("words", "5" * 64, "words"),
        ("lines", "5" * 64, "lines"),
        ("known", {"glyphs not upright": 1, "left out": 1}, "tallies"),
        ("breaks", {"after white space forward": 2}, "tallies"),
        ("unknown", {"glyph box unreadable": 1}, "tallies"),
        ("pdfium_differences", ["1"], "tallies"),
        ("pages_not_compared", 1, "tallies"),
    ],
    ids=[
        "glyphs",
        "words",
        "lines",
        "known",
        "breaks",
        "unknown",
        "pdfium_differences",
        "pages_not_compared",
    ],
)
def test_a_column_of_the_m4a_gate_differs(tmp_path, capsys, key, value, column):
    receipts = m4a_receipt(), m4a_receipt(), m4a_receipt()
    receipts[2]["files"]["b.pdf"][key] = value
    status, captured = run(tmp_path, capsys, *receipts)
    assert status == 1
    cells = ["identical"] * 4
    cells[["glyphs", "words", "lines", "tallies"].index(column)] = "differs"
    assert row(["b.pdf", *cells]) in captured.out
    assert row(["a.pdf", "identical", "identical", "identical", "identical"]) in captured.out


def test_identical_receipts_of_the_m4b_gate(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, m4b_receipt(), m4b_receipt(), m4b_receipt())
    assert status == 0
    lines = captured.out.splitlines()
    assert "| file | glyphs | words | lines | agreement | render |" in lines
    assert row(["a.pdf", "identical", "identical", "identical", "identical", "identical"]) in lines
    for column in ("glyphs", "words", "lines", "agreement", "render"):
        assert f"{column}: identical in 2 of 2 files" in captured.out


@pytest.mark.parametrize(
    ("key", "value", "column"),
    [
        ("glyphs", "5" * 64, "glyphs"),
        ("words", "5" * 64, "words"),
        ("lines", "5" * 64, "lines"),
        ("agreement", {"PDFium: same text": 2, "V2: other": 2}, "agreement"),
        ("unknown", {"glyph box unreadable": 1}, "agreement"),
        ("pages_unknown", ["1"], "agreement"),
        ("render", {"V2 judged": 2, "V2 correct": 1, "D judged": 2, "D correct": 1}, "render"),
    ],
    ids=["glyphs", "words", "lines", "agreement", "unknown", "pages_unknown", "render"],
)
def test_a_column_of_the_m4b_gate_differs(tmp_path, capsys, key, value, column):
    receipts = m4b_receipt(), m4b_receipt(), m4b_receipt()
    receipts[2]["files"]["b.pdf"][key] = value
    status, captured = run(tmp_path, capsys, *receipts)
    assert status == 1
    cells = ["identical"] * 5
    cells[["glyphs", "words", "lines", "agreement", "render"].index(column)] = "differs"
    assert row(["b.pdf", *cells]) in captured.out
    assert row(["a.pdf", *["identical"] * 5]) in captured.out


def test_the_numbers_of_each_receipt_of_the_m4b_gate(tmp_path, capsys):
    receipts = m4b_receipt(), m4b_receipt()
    receipts[0]["files"]["b.pdf"]["pages_unknown"] = ["1", "3"]
    _, captured = run(tmp_path, capsys, *receipts)
    assert (
        "  2 files processed, 0 errors, 4 pages, 6 glyphs, 4 words, 2 lines, 21.3 s" in captured.out
    )
    assert "  pages whose text is unknown as a whole: b.pdf: 1, 3" in captured.out
    assert "  pages whose text is unknown as a whole: none" in captured.out


def test_receipts_of_the_m4b_time_gate_are_not_compared(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, receipt(gate="m4b-time"), receipt(gate="m4b-time"))
    assert status == 2
    assert "no gate is called 'm4b-time'" in captured.err


def test_receipts_of_the_m4a_and_m4b_gates_are_not_compared_with_each_other(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, m4a_receipt(), m4b_receipt())
    assert status == 2
    assert "m4b" in captured.err


def _m4b_without_render(data):
    del data["files"]["a.pdf"]["render"]
    return data


def _m4b_render_not_a_dictionary(data):
    data["files"]["a.pdf"]["render"] = [1]
    return data


def _m4b_render_a_count_not_an_int(data):
    data["files"]["a.pdf"]["render"]["V2 judged"] = "2"
    return data


def _m4b_agreement_nested(data):
    data["files"]["a.pdf"]["agreement"] = {"PDFium": {"same text": 1}}
    return data


def _m4b_pages_unknown_as_ints(data):
    data["files"]["a.pdf"]["pages_unknown"] = [1]
    return data


def _m4b_without_pages_unknown(data):
    del data["files"]["a.pdf"]["pages_unknown"]
    return data


def _m4b_word_count_as_text(data):
    data["files"]["a.pdf"]["word_count"] = "2"
    return data


@pytest.mark.parametrize(
    "mangle",
    [
        _m4b_without_render,
        _m4b_render_not_a_dictionary,
        _m4b_render_a_count_not_an_int,
        _m4b_agreement_nested,
        _m4b_pages_unknown_as_ints,
        _m4b_without_pages_unknown,
        _m4b_word_count_as_text,
    ],
    ids=[
        "without render",
        "render not a dictionary",
        "a count of render not an int",
        "agreement nested",
        "pages_unknown as ints",
        "without pages_unknown",
        "word_count as text",
    ],
)
def test_not_a_receipt_of_the_m4b_gate(tmp_path, capsys, mangle):
    status, _ = run(tmp_path, capsys, m4b_receipt(), mangle(m4b_receipt()))
    assert status == 2


def test_receipts_of_a_gate_not_known(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, receipt(gate="m2"), receipt(gate="m2"))
    assert status == 2
    assert "no gate is called 'm2'" in captured.err


def test_receipts_of_two_gates(tmp_path, capsys):
    status, captured = run(tmp_path, capsys, receipt(), m4a_receipt())
    assert status == 2
    assert "m4a" in captured.err


def _m4a_without_words(data):
    del data["files"]["a.pdf"]["words"]
    return data


def _m4a_words_not_a_string(data):
    data["files"]["a.pdf"]["words"] = 3
    return data


def _m4a_word_count_as_text(data):
    data["files"]["a.pdf"]["word_count"] = "2"
    return data


def _m4a_known_not_a_dictionary(data):
    data["files"]["a.pdf"]["known"] = [1]
    return data


def _m4a_a_count_not_an_int(data):
    data["files"]["a.pdf"]["breaks"]["after white space forward"] = "1"
    return data


def _m4a_differences_as_ints(data):
    data["files"]["a.pdf"]["pdfium_differences"] = [0]
    return data


def _m4a_without_pages_not_compared(data):
    del data["files"]["a.pdf"]["pages_not_compared"]
    return data


def _m4a_pages_not_compared_as_text(data):
    data["files"]["a.pdf"]["pages_not_compared"] = "0"
    return data


def _m4a_unknown_not_a_dictionary(data):
    data["files"]["a.pdf"]["unknown"] = None
    return data


def _m4a_lines_not_a_string(data):
    data["files"]["a.pdf"]["lines"] = 4
    return data


@pytest.mark.parametrize(
    "mangle",
    [
        _m4a_without_words,
        _m4a_words_not_a_string,
        _m4a_word_count_as_text,
        _m4a_known_not_a_dictionary,
        _m4a_a_count_not_an_int,
        _m4a_differences_as_ints,
        _m4a_without_pages_not_compared,
        _m4a_pages_not_compared_as_text,
        _m4a_unknown_not_a_dictionary,
        _m4a_lines_not_a_string,
    ],
    ids=[
        "without words",
        "words not a string",
        "word_count as text",
        "known not a dictionary",
        "a count not an int",
        "pdfium_differences as ints",
        "without pages_not_compared",
        "pages_not_compared as text",
        "unknown not a dictionary",
        "lines not a string",
    ],
)
def test_not_a_receipt_of_the_m4a_gate(tmp_path, capsys, mangle):
    status, _ = run(tmp_path, capsys, m4a_receipt(), mangle(m4a_receipt()))
    assert status == 2


@pytest.mark.parametrize("name", ["m1", "m4a", "m4b"])
def test_the_receipts_a_gate_writes(tmp_path, capsys, monkeypatch, name):
    """The gate run twice over a corpus of one file: compare.py takes both
    receipts, and every cell is identical."""
    data = cases.TEXT["a space ends a word and moves the pen"]().data
    folder = tmp_path / "bench" / "corpus"
    (folder / "files").mkdir(parents=True)
    (folder / "files" / "a.pdf").write_bytes(data)
    manifest = folder / "manifest.csv"
    manifest.write_text(
        f"id,file,sha256\n01,a.pdf,{hashlib.sha256(data).hexdigest()}\n", encoding="utf-8"
    )
    monkeypatch.setattr(m1, "ROOT", tmp_path)
    monkeypatch.setattr(m4a, "MANIFEST", manifest)
    monkeypatch.setattr(m4b, "MANIFEST", manifest)
    gate = {"m1": m1, "m4a": m4a, "m4b": m4b}[name]
    paths = []
    for number in range(2):
        out = tmp_path / f"{name}-{number}.json"
        arguments = ["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder / "files")]
        assert gate.main(arguments) == 0
        paths.append(str(out))
    capsys.readouterr()
    assert compare.main(paths) == 0
    output = capsys.readouterr().out
    for label, _ in compare.GATES[name].columns:
        assert f"{label}: identical in 1 of 1 files" in output
