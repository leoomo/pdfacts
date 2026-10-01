"""The M3 gate: the paths and images of the corpus, read twice.

pdfacts.paths and pdfacts.images are read for every page of every file, then
read again from the file opened anew: QPDF's warnings gather as it reads, so a
second read in the same document may know less (docs/conventions.md, "Paths
and images"). PDFium's own count of the path and image objects of each page's
content and of each annotation's appearance, forms expanded, is read with
pypdfium2 and compared. The receipt holds per file what was counted, the
values that are unknown by fact and reason, whether the two reads were equal,
and where the counts differ from PDFium's; never a value and never text of a
document. Run from the repository root:

    uv run python bench/gates/m3.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]

Exit status 0 when every selected file was read without an error, both reads
were equal, and every page and annotation was compared with PDFium and found
equal; 1 otherwise; 2 when the run was refused and no receipt was written.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import platform
import re
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2
import pypdfium2.raw as c

import pdfacts
from pdfacts import Image, ShapeKind, Unknown
from pdfacts import Path as PathFact
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"

SOURCES = ("page", "appearance")
SHAPES = tuple(kind.value for kind in ShapeKind)


def commit_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(f"40 lowercase hex digits expected, not {value!r}")
    return value


def _source(fact: PathFact | Image) -> str:
    return "page" if fact.ref.annotation is None else "appearance"


def count_page(found, placed, counts: Counter[str], unknown: Counter[str]) -> None:
    """Adds what one page's paths() and images() gave to the two tallies."""
    whole = next((value for value in (found, placed) if isinstance(value, Unknown)), None)
    if whole is not None:
        unknown[f"page as a whole {whole.reason.value}"] += 1
        return
    annotations = set()
    for fact in (*found, *placed):
        if fact.ref.annotation is not None:
            annotations.add(fact.ref.annotation.annotation_index)
        for item in dataclasses.fields(fact):
            value = getattr(fact, item.name)
            if isinstance(value, Unknown):
                unknown[f"{_source(fact)} {item.name} {value.reason.value}"] += 1
    for path in found:
        counts[f"{_source(path)} paths"] += 1
        if not isinstance(path.subpaths, Unknown):
            for subpath in path.subpaths:
                counts[f"{_source(path)} {subpath.shape.value}"] += 1
    for image in placed:
        counts[f"{_source(image)} images"] += 1
    counts["annotations with facts"] += len(annotations)


def _walk(obj, tally: list[int]) -> None:
    kind = c.FPDFPageObj_GetType(obj)
    if kind == c.FPDF_PAGEOBJ_FORM:
        for index in range(c.FPDFFormObj_CountObjects(obj)):
            _walk(c.FPDFFormObj_GetObject(obj, index), tally)
    elif kind == c.FPDF_PAGEOBJ_PATH:
        tally[0] += 1
    elif kind == c.FPDF_PAGEOBJ_IMAGE:
        tally[1] += 1


def pdfium_counts(data: bytes) -> dict[str, list[int]]:
    """PDFium's paths and images, forms expanded, read with pypdfium2 alone:
    under "P" for the content of page P, under "P A" for annotation A of page P
    where its appearance holds any. Nothing is rendered: a rendering makes
    PDFium generate appearances."""
    counts: dict[str, list[int]] = {}
    document = pypdfium2.PdfDocument(data)
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                tally = [0, 0]
                for index in range(c.FPDFPage_CountObjects(page.raw)):
                    _walk(c.FPDFPage_GetObject(page.raw, index), tally)
                counts[f"{page_index}"] = tally
                for annotation_index in range(c.FPDFPage_GetAnnotCount(page.raw)):
                    annotation = c.FPDFPage_GetAnnot(page.raw, annotation_index)
                    tally = [0, 0]
                    try:
                        for index in range(c.FPDFAnnot_GetObjectCount(annotation)):
                            _walk(c.FPDFAnnot_GetObject(annotation, index), tally)
                    finally:
                        c.FPDFPage_CloseAnnot(annotation)
                    if tally != [0, 0]:
                        counts[f"{page_index} {annotation_index}"] = tally
            finally:
                page.close()
    finally:
        document.close()
    return counts


def pdfacts_counts(pages: list) -> tuple[dict[str, list[int]], set[str]]:
    """The same counts from what paths() and images() gave, and the keys not to
    compare: a page unknown as a whole, with its annotations, and an annotation
    whose facts are unknown, given as one path and one image."""
    counts: dict[str, list[int]] = {}
    skipped: set[str] = set()
    for page_index, (found, placed) in enumerate(pages):
        if isinstance(found, Unknown) or isinstance(placed, Unknown):
            skipped.add(f"{page_index}")
            continue
        counts[f"{page_index}"] = [0, 0]
        for column, facts in enumerate((found, placed)):
            for fact in facts:
                key = f"{page_index}"
                if fact.ref.annotation is not None:
                    key += f" {fact.ref.annotation.annotation_index}"
                    if isinstance(fact, PathFact) and isinstance(fact.subpaths, Unknown):
                        skipped.add(key)
                counts.setdefault(key, [0, 0])[column] += 1
    return counts, skipped


def differences(
    ours: dict[str, list[int]], theirs: dict[str, list[int]], skipped: set[str]
) -> list[str]:
    """The keys whose counts differ, leaving out the keys skipped and the
    annotations of a page skipped."""
    found = []
    for key in set(ours) | set(theirs):
        page = key.split()[0]
        if key in skipped or page in skipped:
            continue
        if ours.get(key) != theirs.get(key):
            found.append(key)
    return sorted(found, key=lambda key: [int(part) for part in key.split()])


def read_file(data: bytes) -> dict:
    """The receipt entry of one file: every page read twice, each time from the
    file opened anew, the first read counted and compared with PDFium's own."""
    reads = []
    for _ in range(2):
        with pdfacts.open(data) as document:
            pages = range(document.page_count)
            reads.append([(pdfacts.paths(document, i), pdfacts.images(document, i)) for i in pages])
    first, second = reads
    counts: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for found, placed in first:
        count_page(found, placed, counts, unknown)
    ours, skipped = pdfacts_counts(first)
    return {
        "pages": len(first),
        "counts": dict(sorted(counts.items())),
        "unknown": dict(sorted(unknown.items())),
        "stable": first == second,
        "pdfium_differences": differences(ours, pdfium_counts(data), skipped),
        "pages_not_compared": sum(1 for key in skipped if " " not in key),
        "annotations_not_compared": sum(1 for key in skipped if " " in key),
    }


def exit_status(receipt: dict) -> int:
    if receipt["errors"]:
        return 1
    for entry in receipt["files"].values():
        if not entry["stable"] or entry["pdfium_differences"]:
            return 1
        if entry["pages_not_compared"] or entry["annotations_not_compared"]:
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M3 gate: paths and images of the corpus.")
    parser.add_argument("--commit", required=True, type=commit_argument, metavar="SHA")
    parser.add_argument("--out", required=True, type=Path, help="the receipt to write")
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="manifest ids")
    parser.add_argument("--corpus", type=Path, default=ROOT / "bench" / "corpus" / "files")
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
    files: dict[str, dict] = {}
    errors: dict[str, str] = {}
    started = time.perf_counter()
    for row in rows:
        name = row["file"]
        try:
            entry = read_file(checked[name])
        except Exception as error:
            print(f"error {name} {type(error).__name__}")
            print(f"{name}: {error}", file=sys.stderr)
            errors[name] = type(error).__name__
        else:
            files[name] = entry
            paths = sum(entry["counts"].get(f"{source} paths", 0) for source in SOURCES)
            images = sum(entry["counts"].get(f"{source} images", 0) for source in SOURCES)
            print(f"ok {name} {entry['pages']} pages {paths} paths {images} images")
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "environment": {
            "commit": arguments.commit,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_info": engine_info().as_dict(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "errors": errors,
        "files": files,
        "partial": bool(arguments.only),
        "seconds": seconds,
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print_summary(len(rows), files)
    print(f"{len(errors)} errors, {seconds} s; receipt {arguments.out}")
    return exit_status(receipt)


def print_summary(count: int, files: dict[str, dict]) -> None:
    counts: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for entry in files.values():
        counts.update(entry["counts"])
        unknown.update(entry["unknown"])
    pages = sum(entry["pages"] for entry in files.values())
    print(
        f"{count} files, {pages} pages, {counts['annotations with facts']} annotations with facts"
    )
    for source in SOURCES:
        tallied = ", ".join(
            f"{key} {counts[f'{source} {key}']}" for key in ("paths", "images", *SHAPES)
        )
        print(f"{source}: {tallied}")
    print("unknown: " + (", ".join(f"{key} {unknown[key]}" for key in sorted(unknown)) or "none"))
    stable = sum(1 for entry in files.values() if entry["stable"])
    print(f"stable: {stable} of {count} files")
    equal = sum(
        1
        for entry in files.values()
        if not entry["pdfium_differences"]
        and not entry["pages_not_compared"]
        and not entry["annotations_not_compared"]
    )
    left_pages = sum(entry["pages_not_compared"] for entry in files.values())
    left_annotations = sum(entry["annotations_not_compared"] for entry in files.values())
    print(
        f"PDFium's own count: equal in {equal} of {count} files; not compared:"
        f" {left_pages} pages, {left_annotations} annotations"
    )


if __name__ == "__main__":
    raise SystemExit(main())
