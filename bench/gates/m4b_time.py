"""The M4b time gate: processor time per corpus file of three ways to a page's lines.

ROADMAP.md, M4b, "Gate, for TextRules.V2": the median time per file, from
pdfacts.open to the page's lines, is at most 1.5 times that of the reference
engine. Per file, three ways take turns for a number of rounds, and the fastest
processor time of each is kept:

- the reference, PyMuPDF as the first consumer reads text: open the document,
  and per page a text page with TEXTFLAGS_DICT, its dict, raw dict and words;
- V2: pdfacts.open, and for every page lines() with TextRules.V2;
- V1: the same with the default rules.

The receipt holds per file the page count and the three times, and none of a
document's text; compare.py does not compare it, since times differ. It is run
on the host alone, on a quiet machine, with PyMuPDF from the dependency group
"bench", imported only where it is used so that the tests load this file
without it. Run from the repository root:

    uv run --group bench python bench/gates/m4b_time.py --commit SHA --out PATH \
        [--only ID ...] [--corpus DIR] [--rounds N]

Exit status 0 when every selected file was timed, at least one was, and the ratio of the
medians of V2 over the reference's is at most 1.5; 1 otherwise; 2 when the run was
refused and no receipt was written, as without PyMuPDF.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from m4a import commit_argument

import pdfacts
from pdfacts import TextRules
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"

# The rounds a file is timed for, unless --rounds says otherwise, and the ratio of V2's median
# to the reference's that the gate allows.
ROUNDS = 5
LIMIT = 1.5
WAYS = ("reference", "v2", "v1")


def reference(data: bytes) -> None:
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page in document:
            textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_DICT)
            textpage.extractDICT()
            textpage.extractRAWDICT()
            textpage.extractWORDS()
    finally:
        document.close()


def reference_version() -> str:
    """The version of PyMuPDF; ImportError where it is not installed. MuPDF's errors and
    warnings are not printed, as the output is the gate's own."""
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    return pymupdf.VersionBind


def v2(data: bytes) -> None:
    with pdfacts.open(data) as document:
        for index in range(document.page_count):
            pdfacts.lines(document, index, rules=TextRules.V2)


def v1(data: bytes) -> None:
    with pdfacts.open(data) as document:
        for index in range(document.page_count):
            pdfacts.lines(document, index)


def fastest(data: bytes, rounds: int) -> tuple[float, float, float]:
    """The ways in turn, for `rounds` rounds: the fastest processor time of each, in the order
    of WAYS."""
    ways = (reference, v2, v1)
    best = [math.inf] * len(ways)
    for _ in range(rounds):
        for number, way in enumerate(ways):
            started = time.process_time()
            way(data)
            best[number] = min(best[number], time.process_time() - started)
    return best[0], best[1], best[2]


def read_file(data: bytes, rounds: int) -> dict:
    """The receipt entry of one file: its pages and the fastest time of each way."""
    with pdfacts.open(data) as document:
        pages = document.page_count
    times = fastest(data, rounds)
    if times[0] <= 0:
        raise RuntimeError("the reference took no processor time that the clock can tell")
    return {"pages": pages, **{way: round(time, 6) for way, time in zip(WAYS, times, strict=True)}}


@dataclass(frozen=True)
class Summary:
    """The timed files together. The medians are of the seconds per file, of the reference, V2
    and V1; the ratios of V2 and of V1 over the reference, of the medians and the median of the
    ratios per file; `above` counts the files where V2 takes more than LIMIT times the
    reference."""

    files: int
    medians: tuple[float, float, float]
    ratio_of_medians: tuple[float, float]
    median_of_ratios: tuple[float, float]
    above: int


def summarize(times: Sequence[tuple[float, float, float]]) -> Summary:
    """The summary of the (reference, V2, V1) times of the files; ZeroDivisionError for a
    reference time of 0."""
    medians = tuple(statistics.median(row[way] for row in times) for way in range(3))
    return Summary(
        files=len(times),
        medians=medians,  # type: ignore[arg-type]
        ratio_of_medians=(medians[1] / medians[0], medians[2] / medians[0]),
        median_of_ratios=tuple(  # type: ignore[arg-type]
            statistics.median(row[way] / row[0] for row in times) for way in (1, 2)
        ),
        above=sum(1 for row in times if row[1] / row[0] > LIMIT),
    )


def within(summary: Summary) -> bool:
    """Whether V2's median is at most LIMIT times the reference's."""
    return summary.ratio_of_medians[0] <= LIMIT


def times_of(files: dict[str, dict]) -> list[tuple[float, float, float]]:
    return [tuple(entry[way] for way in WAYS) for entry in files.values()]  # type: ignore[misc]


def exit_status(receipt: dict) -> int:
    if receipt["errors"] or not receipt["files"]:
        return 1
    return 0 if within(summarize(times_of(receipt["files"]))) else 1


def rounds_argument(value: str) -> int:
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"a number of rounds from 1 up expected, not {value!r}")
    return int(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M4b time gate: pdfacts against PyMuPDF.")
    parser.add_argument("--commit", required=True, type=commit_argument, metavar="SHA")
    parser.add_argument("--out", required=True, type=Path, help="the receipt to write")
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="manifest ids")
    parser.add_argument("--corpus", type=Path, default=ROOT / "bench" / "corpus" / "files")
    parser.add_argument("--rounds", type=rounds_argument, default=ROUNDS, metavar="N")
    arguments = parser.parse_args(argv)

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if arguments.only:
        known = {row["id"] for row in rows}
        unknown = [given for given in dict.fromkeys(arguments.only) if given not in known]
        if unknown:
            print(", ".join(unknown) + ": not in the manifest", file=sys.stderr)
            return 2
        wanted = set(arguments.only)
        rows = [row for row in rows if row["id"] in wanted]

    checked: dict[str, bytes] = {}
    for row in rows:
        path = arguments.corpus / row["file"]
        try:
            data = path.read_bytes()
        except OSError as error:
            print(f"{row['file']}: cannot be read: {error}", file=sys.stderr)
            return 2
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            print(f"{row['file']}: the SHA-256 is not {row['sha256']}", file=sys.stderr)
            return 2
        checked[row["file"]] = data

    runtime.start()
    if engine_info().font_policy != "embedded-only":
        print("the font policy of the runtime is not embedded-only", file=sys.stderr)
        return 2
    try:
        version = reference_version()
    except ImportError:
        print("PyMuPDF is not installed: run with uv run --group bench", file=sys.stderr)
        return 2

    files: dict[str, dict] = {}
    errors: dict[str, str] = {}
    started = time.perf_counter()
    for row in rows:
        name = row["file"]
        try:
            entry = read_file(checked[name], arguments.rounds)
        except Exception as error:
            print(f"error {name} {type(error).__name__}")
            print(f"{name}: {error}", file=sys.stderr)
            errors[name] = type(error).__name__
        else:
            files[name] = entry
            over = {way: entry[way] / entry["reference"] for way in ("v2", "v1")}
            print(
                f"ok {name} {entry['pages']} pages, seconds: reference {entry['reference']:.4f},"
                f" V2 {entry['v2']:.4f}, V1 {entry['v1']:.4f}; over the reference:"
                f" V2 {over['v2']:.2f}, V1 {over['v1']:.2f}"
            )
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "gate": "m4b-time",
        "environment": {
            "commit": arguments.commit,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_info": engine_info().as_dict() | {"pymupdf": version},
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "errors": errors,
        "files": files,
        "partial": bool(arguments.only),
        "rounds": arguments.rounds,
        "seconds": seconds,
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print_summary(len(rows), files)
    print(f"{len(errors)} errors, {seconds} s; receipt {arguments.out}")
    return exit_status(receipt)


def print_summary(count: int, files: dict[str, dict]) -> None:
    pages = sum(entry["pages"] for entry in files.values())
    print(f"{count} files, {len(files)} timed, {pages} pages")
    if not files:
        print("no file was timed")
        return
    summary = summarize(times_of(files))
    reference_median, v2_median, v1_median = summary.medians
    print(
        f"median seconds per file: reference {reference_median:.4f}, V2 {v2_median:.4f},"
        f" V1 {v1_median:.4f}"
    )
    print(
        f"ratio of the medians over the reference: V2 {summary.ratio_of_medians[0]:.3f},"
        f" V1 {summary.ratio_of_medians[1]:.3f}"
    )
    print(
        f"median of the ratios per file over the reference: V2 {summary.median_of_ratios[0]:.3f},"
        f" V1 {summary.median_of_ratios[1]:.3f}"
    )
    print(
        f"files where V2 takes more than {LIMIT} times the reference:"
        f" {summary.above} of {len(files)}"
    )
    print(
        f"median time per file, ratio of the medians: {summary.ratio_of_medians[0]:.3f},"
        f" at most {LIMIT}: {'yes' if within(summary) else 'no'};"
        f" median of the ratios per file: {summary.median_of_ratios[0]:.3f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
