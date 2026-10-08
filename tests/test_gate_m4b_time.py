"""The M4b time gate: how it sums up times, what it refuses, and when it fails.

The gate (bench/gates/m4b_time.py) times three ways to a page's lines per file and
keeps the fastest of several rounds. The tests give it times made here, so that no
test depends on how fast the machine is, and run it without PyMuPDF: the way that
would use it is replaced, and the module is loaded without it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

import gate_corpus
import pdfacts
from pdfacts import TextRules

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m4b_time  # noqa: E402

from synthetic.builder import PageSpec, document  # noqa: E402


def one_page() -> bytes:
    return document([PageSpec(b"/MediaBox [0 0 400 300]", b"BT /F1 10 Tf 100 200 Td (AB) Tj ET")])


def two_pages() -> bytes:
    page = PageSpec(b"/MediaBox [0 0 400 300]", b"BT /F1 10 Tf 100 200 Td (AB) Tj ET")
    return document([page, page])


corpus = partial(gate_corpus.corpus, m4b_time)
run = partial(gate_corpus.run, m4b_time)


def timed(
    monkeypatch, times: dict[bytes, tuple[float, float, float]], rounds: list[int] | None = None
):
    """The ways of the gate replaced: the PyMuPDF version is "9.9", and the times of a file are
    the ones given for its bytes."""
    monkeypatch.setattr(m4b_time, "reference_version", lambda: "9.9")

    def fastest(data: bytes, number: int) -> tuple[float, float, float]:
        if rounds is not None:
            rounds.append(number)
        return times[data]

    monkeypatch.setattr(m4b_time, "fastest", fastest)


# --- the summary ----------------------------------------------------------------------------


def test_1_the_summary_of_times_made_here():
    """Reference 1, 2, 4; V2 1.2, 3, 4; V1 2, 4, 8: medians 2, 3, 4; the ratios of V2 over the
    reference 1.2, 1.5, 1; of V1, 2, 2, 2."""
    summary = m4b_time.summarize([(1.0, 1.2, 2.0), (2.0, 3.0, 4.0), (4.0, 4.0, 8.0)])
    assert summary.files == 3
    assert summary.medians == (2.0, 3.0, 4.0)
    assert summary.ratio_of_medians == (1.5, 2.0)
    assert summary.median_of_ratios == pytest.approx((1.2, 2.0))
    assert summary.above == 0
    assert m4b_time.within(summary)


def test_2_the_median_of_an_even_number_of_files_is_the_mean_of_the_two_middle_ones():
    summary = m4b_time.summarize(
        [(1.0, 1.0, 1.0), (2.0, 4.0, 6.0), (3.0, 6.0, 9.0), (4.0, 9.0, 9.0)]
    )
    assert summary.medians == (2.5, 5.0, 7.5)
    assert summary.ratio_of_medians == (2.0, 3.0)


def test_3_one_and_a_half_is_within_and_a_hair_above_is_not():
    at = m4b_time.summarize([(2.0, 3.0, 3.0)])
    assert at.ratio_of_medians[0] == 1.5
    assert m4b_time.within(at)
    above = m4b_time.summarize([(2.0, 3.0000001, 3.0)])
    assert above.ratio_of_medians[0] > 1.5
    assert not m4b_time.within(above)
    below = m4b_time.summarize([(2.0, 2.9999999, 3.0)])
    assert m4b_time.within(below)


def test_4_the_files_above_one_and_a_half_are_those_whose_own_ratio_is_above():
    rows = [(1.0, 1.5, 1.0), (1.0, 1.5000001, 1.0), (2.0, 7.0, 1.0), (4.0, 1.0, 1.0)]
    assert m4b_time.summarize(rows).above == 2


def test_5_the_gate_is_the_ratio_of_the_medians_not_the_median_of_the_ratios():
    """References 1, 10, 100 and V2 1.4, 40, 110: the ratios per file are 1.4, 4 and 1.1, their
    median 1.4; the medians are 10 and 40, their ratio 4."""
    rows = [(1.0, 1.4, 1.0), (10.0, 40.0, 1.0), (100.0, 110.0, 1.0)]
    summary = m4b_time.summarize(rows)
    assert summary.ratio_of_medians[0] == 4.0
    assert summary.median_of_ratios[0] == pytest.approx(1.4)
    assert not m4b_time.within(summary)
    files = {
        f"{number}.pdf": {"pages": 1, "reference": r, "v2": v2, "v1": v1}
        for number, (r, v2, v1) in enumerate(rows)
    }
    assert m4b_time.exit_status({"errors": {}, "files": files}) == 1


def test_6_the_times_of_the_files_in_the_order_of_the_ways():
    files = {"a.pdf": {"pages": 4, "reference": 1.0, "v2": 2.0, "v1": 3.0}}
    assert m4b_time.times_of(files) == [(1.0, 2.0, 3.0)]


def test_7_the_summary_printed(capsys):
    files = {
        "a.pdf": {"pages": 1, "reference": 1.0, "v2": 1.2, "v1": 2.0},
        "b.pdf": {"pages": 3, "reference": 2.0, "v2": 3.0, "v1": 4.0},
        "c.pdf": {"pages": 2, "reference": 4.0, "v2": 4.0, "v1": 8.0},
    }
    m4b_time.print_summary(4, files)
    assert capsys.readouterr().out.splitlines() == [
        "4 files, 3 timed, 6 pages",
        "median seconds per file: reference 2.0000, V2 3.0000, V1 4.0000",
        "ratio of the medians over the reference: V2 1.500, V1 2.000",
        "median of the ratios per file over the reference: V2 1.200, V1 2.000",
        "files where V2 takes more than 1.5 times the reference: 0 of 3",
        "median time per file, ratio of the medians: 1.500, at most 1.5: yes;"
        " median of the ratios per file: 1.200",
    ]
    files["c.pdf"]["v2"] = 9.0
    m4b_time.print_summary(3, files)
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "files where V2 takes more than 1.5 times the reference: 1 of 3",
        "median time per file, ratio of the medians: 1.500, at most 1.5: yes;"
        " median of the ratios per file: 1.500",
    ]
    files["b.pdf"]["v2"] = 3.5
    m4b_time.print_summary(3, files)
    assert capsys.readouterr().out.splitlines()[-1] == (
        "median time per file, ratio of the medians: 1.750, at most 1.5: no;"
        " median of the ratios per file: 1.750"
    )


@pytest.mark.parametrize(("v2", "status"), [(1.5, 0), (1.4, 0), (1.5000001, 1), (3.0, 1)])
def test_8_exit_status_follows_the_ratio_of_the_medians_of_v2(v2, status):
    files = {"a.pdf": {"pages": 1, "reference": 1.0, "v2": v2, "v1": 100.0}}
    assert m4b_time.exit_status({"errors": {}, "files": files}) == status


def test_9_an_error_or_no_file_timed_fails_the_run():
    files = {"a.pdf": {"pages": 1, "reference": 1.0, "v2": 1.0, "v1": 1.0}}
    assert m4b_time.exit_status({"errors": {}, "files": files}) == 0
    assert m4b_time.exit_status({"errors": {"b.pdf": "RuntimeError"}, "files": files}) == 1
    assert m4b_time.exit_status({"errors": {}, "files": {}}) == 1


# --- the ways and their turns ---------------------------------------------------------------


def test_10_the_ways_take_turns_and_the_fastest_time_of_each_is_kept(monkeypatch):
    """Two rounds. Reference 3 then 2; V2 2 then 5; V1 1 then 0.5."""
    calls = []
    for name in ("reference", "v2", "v1"):
        monkeypatch.setattr(m4b_time, name, lambda data, name=name: calls.append((name, data)))
    clock = iter([0.0, 3.0, 10.0, 12.0, 20.0, 21.0, 30.0, 32.0, 40.0, 45.0, 50.0, 50.5])
    monkeypatch.setattr(
        m4b_time,
        "time",
        SimpleNamespace(process_time=lambda: next(clock), perf_counter=lambda: 0.0),
    )
    assert m4b_time.fastest(b"data", 2) == (2.0, 2.0, 0.5)
    assert calls == [(name, b"data") for _ in range(2) for name in ("reference", "v2", "v1")]


def test_11_v2_and_v1_read_the_lines_of_every_page_with_their_rules(monkeypatch):
    calls = []

    def lines(document, page_index, rules=TextRules.V1):
        calls.append((page_index, rules))
        return ()

    monkeypatch.setattr(pdfacts, "lines", lines)
    m4b_time.v2(two_pages())
    assert calls == [(0, TextRules.V2), (1, TextRules.V2)]
    calls.clear()
    m4b_time.v1(two_pages())
    assert calls == [(0, TextRules.V1), (1, TextRules.V1)]


def test_12_v2_and_v1_open_the_document_inside_the_time(monkeypatch):
    opened = []
    real = pdfacts.open

    def counting(data, *arguments, **keywords):
        opened.append(data)
        return real(data, *arguments, **keywords)

    monkeypatch.setattr(pdfacts, "open", counting)
    m4b_time.v2(one_page())
    m4b_time.v1(one_page())
    assert len(opened) == 2


def test_13_the_entry_of_a_file_is_its_pages_and_the_times_to_the_microsecond(monkeypatch):
    seen = []
    monkeypatch.setattr(
        m4b_time, "fastest", lambda data, rounds: seen.append(rounds) or (0.1234567891, 2.5, 3.0)
    )
    assert m4b_time.read_file(two_pages(), 4) == {
        "pages": 2,
        "reference": 0.123457,
        "v2": 2.5,
        "v1": 3.0,
    }
    assert seen == [4]


def test_14_a_reference_that_took_no_time_the_clock_can_tell_is_an_error(monkeypatch):
    monkeypatch.setattr(m4b_time, "fastest", lambda data, rounds: (0.0, 1.0, 1.0))
    with pytest.raises(RuntimeError):
        m4b_time.read_file(one_page(), 1)


# --- PyMuPDF --------------------------------------------------------------------------------


def test_15_the_module_is_loaded_without_pymupdf(monkeypatch):
    """The tests run without the dependency group "bench": PyMuPDF is imported only where the
    reference reads."""
    for name in ("pymupdf", "fitz"):
        monkeypatch.setitem(sys.modules, name, None)
    spec = importlib.util.spec_from_file_location(
        "m4b_time_alone", ROOT / "bench" / "gates" / "m4b_time.py"
    )
    alone = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, alone)
    spec.loader.exec_module(alone)
    with pytest.raises(ImportError):
        alone.reference_version()
    with pytest.raises(ImportError):
        alone.reference(one_page())
    # The ways that are pdfacts' read without it.
    alone.v2(one_page())
    alone.v1(one_page())


def fake_pymupdf(monkeypatch, pages: int = 2) -> list[tuple]:
    """A module standing for PyMuPDF in sys.modules, which records what is called on it."""
    calls: list[tuple] = []

    class TextPage:
        def extractDICT(self):  # noqa: N802
            calls.append(("extractDICT",))

        def extractRAWDICT(self):  # noqa: N802
            calls.append(("extractRAWDICT",))

        def extractWORDS(self):  # noqa: N802
            calls.append(("extractWORDS",))

    class Page:
        def get_textpage(self, flags):
            calls.append(("get_textpage", flags))
            return TextPage()

    class Document:
        def __iter__(self):
            return iter([Page() for _ in range(pages)])

        def close(self):
            calls.append(("close",))

    class Tools:
        def mupdf_display_errors(self, value):
            calls.append(("errors", value))

        def mupdf_display_warnings(self, value):
            calls.append(("warnings", value))

    def open_(**keywords):
        calls.append(("open", tuple(sorted(keywords))))
        assert keywords["filetype"] == "pdf"
        return Document()

    module = SimpleNamespace(open=open_, TOOLS=Tools(), TEXTFLAGS_DICT=7, VersionBind="1.2.3")
    monkeypatch.setitem(sys.modules, "pymupdf", module)
    return calls


def test_16_pymupdf_is_told_to_print_neither_errors_nor_warnings(monkeypatch):
    calls = fake_pymupdf(monkeypatch)
    assert m4b_time.reference_version() == "1.2.3"
    assert sorted(calls) == [("errors", False), ("warnings", False)]


def test_17_the_reference_reads_a_stream_as_a_pdf_and_every_page_three_ways(monkeypatch):
    calls = fake_pymupdf(monkeypatch, pages=2)
    m4b_time.reference(b"data")
    page = [("get_textpage", 7), ("extractDICT",), ("extractRAWDICT",), ("extractWORDS",)]
    assert calls == [("open", ("filetype", "stream")), *page, *page, ("close",)]


def test_18_a_run_without_pymupdf_is_refused_and_writes_no_receipt(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": one_page()})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 2
    assert not out.exists()
    assert "PyMuPDF is not installed" in capsys.readouterr().err


# --- the run --------------------------------------------------------------------------------


def test_19_a_run_writes_the_receipt_and_the_summary(monkeypatch, tmp_path, capsys):
    first, second = one_page(), two_pages()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": first, "b.pdf": second})
    rounds: list[int] = []
    timed(monkeypatch, {first: (1.0, 1.2, 2.0), second: (2.0, 3.0, 4.0)}, rounds)
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    assert rounds == [5, 5]
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["gate"] == "m4b-time"
    assert receipt["files"] == {
        "a.pdf": {"pages": 1, "reference": 1.0, "v2": 1.2, "v1": 2.0},
        "b.pdf": {"pages": 2, "reference": 2.0, "v2": 3.0, "v1": 4.0},
    }
    assert (receipt["errors"], receipt["partial"], receipt["rounds"]) == ({}, False, 5)
    environment = receipt["environment"]
    assert environment["commit"] == "0" * 40
    assert environment["engine_info"]["pymupdf"] == "9.9"
    assert environment["engine_info"]["font_policy"] == "embedded-only"
    assert set(environment) == {"commit", "date", "engine_info", "machine", "platform", "python"}
    assert capsys.readouterr().out.splitlines() == [
        "ok a.pdf 1 pages, seconds: reference 1.0000, V2 1.2000, V1 2.0000;"
        " over the reference: V2 1.20, V1 2.00",
        "ok b.pdf 2 pages, seconds: reference 2.0000, V2 3.0000, V1 4.0000;"
        " over the reference: V2 1.50, V1 2.00",
        "2 files, 2 timed, 3 pages",
        "median seconds per file: reference 1.5000, V2 2.1000, V1 3.0000",
        "ratio of the medians over the reference: V2 1.400, V1 2.000",
        "median of the ratios per file over the reference: V2 1.350, V1 2.000",
        "files where V2 takes more than 1.5 times the reference: 0 of 2",
        "median time per file, ratio of the medians: 1.400, at most 1.5: yes;"
        " median of the ratios per file: 1.350",
        f"0 errors, {receipt['seconds']} s; receipt {out}",
    ]


def test_20_a_run_above_one_and_a_half_writes_its_receipt_and_fails(monkeypatch, tmp_path, capsys):
    data = one_page()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    timed(monkeypatch, {data: (1.0, 1.6, 2.0)})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    assert out.exists()
    assert "at most 1.5: no;" in capsys.readouterr().out


def test_21_rounds_are_given_to_the_timing_and_written_to_the_receipt(monkeypatch, tmp_path):
    data = one_page()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    rounds: list[int] = []
    timed(monkeypatch, {data: (1.0, 1.0, 1.0)}, rounds)
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--rounds", "3") == 0
    assert rounds == [3]
    assert json.loads(out.read_text(encoding="utf-8"))["rounds"] == 3


@pytest.mark.parametrize("value", ["0", "-1", "x", "1.5", ""])
def test_22_rounds_are_a_whole_number_from_one(tmp_path, value):
    with pytest.raises(SystemExit) as raised:
        m4b_time.main(["--commit", "0" * 40, "--out", str(tmp_path / "r.json"), "--rounds", value])
    assert raised.value.code == 2
    with pytest.raises(argparse.ArgumentTypeError):
        m4b_time.rounds_argument(value)
    assert not (tmp_path / "r.json").exists()


def test_23_only_the_files_named_and_the_receipt_partial(monkeypatch, tmp_path):
    first, second = one_page(), two_pages()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": first, "b.pdf": second})
    timed(monkeypatch, {first: (1.0, 1.0, 1.0), second: (1.0, 1.0, 1.0)})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (list(receipt["files"]), receipt["partial"]) == (["b.pdf"], True)


def test_24_an_error_is_recorded_and_the_run_fails(monkeypatch, tmp_path):
    first, second = one_page(), two_pages()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": first, "b.pdf": second})

    def fastest(data: bytes, rounds: int) -> tuple[float, float, float]:
        if data == first:
            raise RuntimeError("no")
        return 1.0, 1.0, 1.0

    monkeypatch.setattr(m4b_time, "reference_version", lambda: "9.9")
    monkeypatch.setattr(m4b_time, "fastest", fastest)
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (receipt["errors"], list(receipt["files"])) == ({"a.pdf": "RuntimeError"}, ["b.pdf"])


def test_25_a_run_in_which_no_file_was_timed_says_so_and_fails(monkeypatch, tmp_path, capsys):
    data = one_page()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    monkeypatch.setattr(m4b_time, "reference_version", lambda: "9.9")

    def fails(data: bytes, rounds: int):
        raise RuntimeError("no")

    monkeypatch.setattr(m4b_time, "fastest", fails)
    assert run(folder, tmp_path / "receipt.json") == 1
    assert "no file was timed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "refusal", ["a file missing", "another SHA-256", "an id not listed", "fonts of the host"]
)
def test_26_a_refused_run_writes_no_receipt(monkeypatch, tmp_path, refusal):
    data = one_page()
    listed = {"a.pdf": data + b"\n"} if refusal == "another SHA-256" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data}, listed)
    timed(monkeypatch, {data: (1.0, 1.0, 1.0)})
    if refusal == "a file missing":
        (folder / "a.pdf").unlink()
    if refusal == "fonts of the host":
        monkeypatch.setattr(m4b_time, "engine_info", lambda: SimpleNamespace(font_policy="host"))
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()


def test_27_a_commit_is_forty_lowercase_hex_digits(tmp_path):
    with pytest.raises(SystemExit) as raised:
        m4b_time.main(["--commit", "A" * 40, "--out", str(tmp_path / "receipt.json")])
    assert raised.value.code == 2
    assert not (tmp_path / "receipt.json").exists()
