"""The M5b verification: the new facts of M5b identical on the platform it runs on.

For every page of every file, after the runtime installed the font policy,
the annotations other than widgets are read with annotations(), the words and
lines of TextRules.V3 with words() and lines(); and, per file, an EditSession
with Decrypt alone and one with Rewrite alone are applied and compared with
decrypt() and recover(). The receipt holds per file the counts and one digest
of each fact kind, for comparing platforms; it holds no value and no text of a
document.

Exit status 0 when every file was read and written without an error, no page's
facts are unknown as a whole and each session's result equals the function's;
1 otherwise. Run from the repository root:

    uv run python bench/gates/m5b.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]
"""

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import platform
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pikepdf

import pdfacts
from pdfacts import TextRules, Unknown
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info
from pdfacts.edit import Decrypt, EditSession, Rewrite

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))

from digest import Digest  # noqa: E402
from gates.m4a import commit_argument  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"
KINDS = ("annotations", "words", "lines")


def _put(digest, value) -> None:
    """A fact's value into the digest, its type told from its Python kind."""
    if value is None:
        digest.update("none")
    elif isinstance(value, Unknown):
        digest.update(f"unknown {value.reason.value} {value.detail}")
    elif isinstance(value, bool):
        digest.update(f"bool {value}")
    elif isinstance(value, int):
        digest.update(f"int {value}")
    elif isinstance(value, float):
        digest.update(f"float {value!r}")
    elif isinstance(value, str):
        # Hex, not the bytes: a digest takes int, float or str, and the text of
        # a PDF string may hold the separator.
        digest.update(value.encode("utf-8", "surrogatepass").hex())
    elif isinstance(value, tuple):
        digest.update("tuple")
        for item in value:
            _put(digest, item)
    else:
        # Point, Rect, DeviceColour, AnnotationAppearance, AnnotationRef: a
        # dataclass or named tuple of plain values.
        digest.update(type(value).__name__)
        for field in dataclasses.fields(value):
            _put(digest, getattr(value, field.name))


def read_page(document, index: int) -> dict:
    """One page's annotations and V3 facts into a count and a digest of each."""
    entry = {kind: {"count": 0, "digest": Digest()} for kind in KINDS}
    for kind, facts in (
        ("annotations", pdfacts.annotations(document, index)),
        ("words", pdfacts.words(document, index, rules=TextRules.V3)),
        ("lines", pdfacts.lines(document, index, rules=TextRules.V3)),
    ):
        if isinstance(facts, Unknown):
            entry[kind]["unknown"] = f"{facts.reason.value} {facts.detail}"
            continue
        entry[kind]["count"] = len(facts)
        digest = entry[kind]["digest"]
        digest.update(len(facts))
        for fact in facts:
            _put(digest, fact)
    return entry


def read_file(data: bytes) -> dict:
    """One file: every page's facts into one count and one digest of each
    kind, the pages unknown as a whole, and the session's equivalence with
    the functions, compared byte for byte."""
    pages = []
    with pdfacts.open(data) as document:
        for page in document.pages:
            pages.append(read_page(document, page.index))
    entry = {
        "page_count": len(pages),
        # A page is named once whatever kinds of its facts are unknown.
        "pages_unknown": [
            str(index)
            for index, page in enumerate(pages)
            if any("unknown" in page[kind] for kind in KINDS)
        ],
    }
    for kind in KINDS:
        whole = Digest()
        count = 0
        for index, page in enumerate(pages):
            whole.update(index)
            if "unknown" in page[kind]:
                whole.update(page[kind]["unknown"].encode("utf-8", "surrogatepass").hex())
            else:
                count += page[kind]["count"]
                whole.update(page[kind]["digest"].hexdigest())
        entry[f"{kind.removesuffix('s')}_count"] = count
        entry[kind] = whole.hexdigest()
    entry["session"] = session_of(data)
    return entry


def session_of(data: bytes) -> dict:
    """The session with Decrypt alone and the one with Rewrite alone, each
    compared with the function it replaces; an encrypted write is never the
    same twice, so the rewrite is compared only where the file is not
    encrypted."""
    session = {}
    try:
        expected = pdfacts.decrypt(data)
    except Exception:
        expected = None
    if expected is not None:
        found = EditSession(data).add(Decrypt()).apply()
        session["decrypt equal"] = found.data == expected.data and (
            found.page_count == expected.page_count
        )
    try:
        expected = pdfacts.recover(data)
    except Exception:
        expected = None
    if expected is not None:
        with pikepdf.open(io.BytesIO(data)) as probe:
            encrypted = probe.is_encrypted
        if not encrypted:
            found = EditSession(data).add(Rewrite()).apply()
            session["rewrite equal"] = found.data == expected.data
    return session


def exit_status(receipt: dict) -> int:
    if receipt["errors"]:
        return 1
    for entry in receipt["files"].values():
        if entry["pages_unknown"] or not all(entry["session"].values()):
            return 1
    return 0


def print_summary(count: int, files: dict[str, dict]) -> None:
    pages = sum(entry["page_count"] for entry in files.values())
    counted = ", ".join(
        f"{sum(entry[key] for entry in files.values())} {key.removesuffix('_count')}"
        for key in ("annotation_count", "word_count", "line_count")
    )
    unknown = sum(len(entry["pages_unknown"]) for entry in files.values())
    decrypt = sum(1 for e in files.values() if "decrypt equal" in e["session"])
    rewrite = sum(1 for e in files.values() if "rewrite equal" in e["session"])
    print(
        f"{count} files, {pages} pages: {counted}, {unknown} pages unknown;"
        f" the session as decrypt() in {decrypt} files, as recover() in {rewrite}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M5b verification: M5b's facts measured.")
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
    if engine_info().font_policy != "embedded-only":
        print("the font policy of the runtime is not embedded-only", file=sys.stderr)
        return 2

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
            print(
                f"ok {name} {entry['page_count']} pages,"
                f" {len(entry['pages_unknown'])} unknown, session {entry['session']}"
            )
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "gate": "m5b",
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


if __name__ == "__main__":
    raise SystemExit(main())
