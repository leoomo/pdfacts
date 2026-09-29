"""The M1 gate: page geometry and glyph data of the corpus, as one receipt.

Geometry is read through pdfacts.open, glyph data through PDFium itself after
the runtime installed the font policy. The receipt holds a digest per file,
never the values and never text of a document. Run from the repository root:

    uv run python bench/gates/m1.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]

Exit status 0 when every selected file was processed, 1 when a file is under
errors in the receipt, 2 when the run was refused and no receipt was written.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import platform
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2
import pypdfium2.raw as c

import pdfacts
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))

from digest import Digest, digest  # noqa: E402


class GlyphReadError(Exception):
    """PDFium refused to describe a character of a page; the message names both."""


def commit_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(f"40 lowercase hex digits expected, not {value!r}")
    return value


def geometry_digests(data: bytes) -> tuple[str, int, list[int], int]:
    """The geometry digest of a file, its page count, the pages whose cross-check
    failed, and the number of warnings."""
    values: list[int | float | str] = []
    cross_check_failed: list[int] = []
    with pdfacts.open(data) as document:
        page_count = document.page_count
        values.append(page_count)
        for page in document.pages:
            geometry = page.geometry
            user_unit = geometry.user_unit
            if isinstance(user_unit, pdfacts.Unknown):
                user_unit = user_unit.reason.value
            box = geometry.visible_box
            values.extend(
                [geometry.index, box.x0, box.y0, box.x1, box.y1, geometry.rotation, user_unit]
            )
            if isinstance(page.ref.object_number, pdfacts.Unknown):
                cross_check_failed.append(geometry.index)
        warning_count = len(document.warnings)
    return digest(values), page_count, cross_check_failed, warning_count


def glyph_digests(data: bytes) -> tuple[str, str, int]:
    """The glyph digests of a file (exact, and rounded to three decimals) and the count.

    Per kept glyph, in the order of the text page: character code, origin, tight
    box, loose box, font size. The digest takes both boxes as left, bottom,
    right, top, although PDFium fills them in another order (FS_RECTF) and
    hands the tight box out as left, right, bottom, top.
    """
    doubles = [ctypes.c_double() for _ in range(6)]
    rect = c.FS_RECTF()
    exact = Digest()
    rounded = Digest()
    kept_total = 0
    document = pypdfium2.PdfDocument(data)
    try:
        page_count = len(document)
        exact.update(page_count)
        rounded.update(page_count)
        for index in range(page_count):
            page = document[index]
            try:
                text = page.get_textpage()
                try:
                    count = c.FPDFText_CountChars(text.raw)
                    if count < 0:
                        raise GlyphReadError(f"page {index}: FPDFText_CountChars returned {count}")
                    kept: list[tuple[int | float, ...]] = []
                    for char in range(count):
                        generated = c.FPDFText_IsGenerated(text.raw, char)
                        if generated == -1:
                            raise GlyphReadError(
                                f"page {index}, character {char}: FPDFText_IsGenerated returned -1"
                            )
                        if generated == 1:
                            continue
                        if not c.FPDFText_GetCharOrigin(
                            text.raw, char, ctypes.byref(doubles[0]), ctypes.byref(doubles[1])
                        ):
                            raise GlyphReadError(
                                f"page {index}, character {char}: FPDFText_GetCharOrigin failed"
                            )
                        if not c.FPDFText_GetCharBox(
                            text.raw, char, *map(ctypes.byref, doubles[2:])
                        ):
                            raise GlyphReadError(
                                f"page {index}, character {char}: FPDFText_GetCharBox failed"
                            )
                        if not c.FPDFText_GetLooseCharBox(text.raw, char, ctypes.byref(rect)):
                            raise GlyphReadError(
                                f"page {index}, character {char}: FPDFText_GetLooseCharBox failed"
                            )
                        kept.append(
                            (
                                c.FPDFText_GetUnicode(text.raw, char),
                                doubles[0].value,
                                doubles[1].value,
                                doubles[2].value,
                                doubles[4].value,
                                doubles[3].value,
                                doubles[5].value,
                                rect.left,
                                rect.bottom,
                                rect.right,
                                rect.top,
                                c.FPDFText_GetFontSize(text.raw, char),
                            )
                        )
                    exact.update(index, len(kept))
                    rounded.update(index, len(kept))
                    for glyph in kept:
                        exact.update(*glyph)
                        rounded.update(glyph[0], *(round(value, 3) for value in glyph[1:]))
                    kept_total += len(kept)
                finally:
                    text.close()
            finally:
                page.close()
    finally:
        document.close()
    return exact.hexdigest(), rounded.hexdigest(), kept_total


def read_file(data: bytes) -> dict:
    """The receipt entry of one file: geometry through pdfacts, then glyphs through PDFium."""
    geometry, page_count, cross_check_failed, warning_count = geometry_digests(data)
    glyphs, glyphs_rounded3, glyph_count = glyph_digests(data)
    return {
        "cross_check_failed": cross_check_failed,
        "geometry": geometry,
        "glyph_count": glyph_count,
        "glyphs": glyphs,
        "glyphs_rounded3": glyphs_rounded3,
        "page_count": page_count,
        "warning_count": warning_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M1 gate: geometry and glyphs of the corpus.")
    parser.add_argument("--commit", required=True, type=commit_argument, metavar="SHA")
    parser.add_argument("--out", required=True, type=Path, help="the receipt to write")
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="manifest ids")
    parser.add_argument("--corpus", type=Path, default=ROOT / "bench" / "corpus" / "files")
    arguments = parser.parse_args(argv)

    with (ROOT / "bench" / "corpus" / "manifest.csv").open(encoding="utf-8", newline="") as handle:
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
            print(f"ok {name} {entry['page_count']} pages {entry['glyph_count']} glyphs")
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

    pages = sum(entry["page_count"] for entry in files.values())
    glyphs = sum(entry["glyph_count"] for entry in files.values())
    print(
        f"{len(rows)} files: {len(files)} processed, {len(errors)} errors, "
        f"{pages} pages, {glyphs} glyphs, {seconds} s; receipt {arguments.out}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
