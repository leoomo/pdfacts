"""The M4a gate: the glyphs, words and lines of the corpus, and what they miss.

pdfacts.glyphs, words and lines are read for every page of every file, after
the runtime installed the font policy, as the M1 gate reads glyphs. The
receipt holds per file their counts and a digest of each, for comparing
platforms; the values that are unknown, by name and reason; and the counts of
what docs/conventions.md, "Glyphs, words and lines", says is not given or
known to go wrong. PDFium's own count of the characters of each page's
content is read with pypdfium2, apart from pdfacts, and compared with the
characters the glyphs hold; the same read tells which text object each glyph
comes from, which text objects PDFium leaves out, and which annotations hold
text in their appearance. The receipt holds no value and no text of a
document. Run from the repository root:

    uv run python bench/gates/m4a.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]

Exit status 0 when every selected file was read without an error and every
page was compared with PDFium and found equal; 1 otherwise; 2 when the run
was refused and no receipt was written.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import platform
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pypdfium2
import pypdfium2.raw as c

import pdfacts
from pdfacts import Glyph, Line, Point, Quad, Rect, Unknown, Word
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info
from pdfacts.text.layout import LINE

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"
sys.path.insert(0, str(ROOT / "bench"))

from digest import Digest  # noqa: E402

GLYPH_VALUES = ("text", "origin", "direction", "size", "box", "upright", "render_mode", "hyphen")
# Render modes that paint nothing.
INVISIBLE = (3, 7)
# What the receipt counts of each file, every key in every entry.
KNOWN = (
    "annotations with text in their appearance",
    "glyphs not upright",
    "left out",
    "left out over invisible text",
)
BREAKS = (
    "after another glyph back",
    "after another glyph forward",
    "after white space back",
    "after white space forward",
)


def commit_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(f"40 lowercase hex digits expected, not {value!r}")
    return value


def _put(digest: Digest, value: object) -> None:
    """One value of a fact: an unknown by its reason and detail, a bool as a
    word, a text by its length and code points, a point or a box by its
    numbers."""
    if isinstance(value, Unknown):
        digest.update(f"unknown {value.reason.value}", value.detail)
    elif isinstance(value, bool):
        digest.update("true" if value else "false")
    elif isinstance(value, str):
        digest.update(len(value), *(ord(ch) for ch in value))
    elif isinstance(value, (int, float)):
        digest.update(value)
    elif isinstance(value, Quad):
        for point in (value.p1, value.p2, value.p3, value.p4):
            digest.update(point.x, point.y)
    elif isinstance(value, Point):
        digest.update(value.x, value.y)
    elif isinstance(value, Rect):
        digest.update(value.x0, value.y0, value.x1, value.y1)
    else:
        raise TypeError(f"no digest for a value of {type(value).__name__}")


def digest_page(
    digests: dict[str, Digest],
    found: tuple[Glyph, ...] | Unknown,
    made: tuple[Word, ...] | Unknown,
    joined: tuple[Line, ...] | Unknown,
) -> None:
    """Adds one page's glyphs, words and lines to their digests, every value
    of every fact in order."""
    for key, facts in (("glyphs", found), ("words", made), ("lines", joined)):
        digest = digests[key]
        digest.update("page")
        if isinstance(facts, Unknown):
            _put(digest, facts)
            continue
        digest.update(len(facts))
        for fact in facts:
            if isinstance(fact, Glyph):
                digest.update(fact.ref.page_index, fact.ref.glyph_index)
                for name in GLYPH_VALUES:
                    _put(digest, getattr(fact, name))
            elif isinstance(fact, Word):
                digest.update(fact.page_index, len(fact.glyphs), *fact.glyphs)
                _put(digest, fact.text)
                _put(digest, fact.box)
            else:
                digest.update(fact.page_index, len(fact.words), *fact.words)
                _put(digest, fact.text)
                _put(digest, fact.box)


def _address(obj: c.FPDF_PAGEOBJECT) -> int:
    return ctypes.cast(obj, ctypes.c_void_p).value  # type: ignore[return-value]


def _objects(count: int, get: Callable[[int], c.FPDF_PAGEOBJECT]) -> Iterator:
    """The objects in the order a walk meets them, forms expanded where they
    are drawn; the forms themselves left out."""
    for index in range(count):
        obj = get(index)
        if c.FPDFPageObj_GetType(obj) == c.FPDF_PAGEOBJ_FORM:
            yield from _objects(
                c.FPDFFormObj_CountObjects(obj), partial(c.FPDFFormObj_GetObject, obj)
            )
        else:
            yield obj


def _bounds(obj: c.FPDF_PAGEOBJECT) -> tuple[float, float, float, float] | None:
    """The object's box, where PDFium gives one with an area."""
    values = [ctypes.c_float() for _ in range(4)]
    if not c.FPDFPageObj_GetBounds(obj, *(ctypes.byref(value) for value in values)):
        return None
    left, bottom, right, top = (value.value for value in values)
    return (left, bottom, right, top) if right > left and top > bottom else None


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _units(text: str) -> list[int]:
    """The UTF-16 code units of a text, as PDFium's text page gives characters."""
    data = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def pdfium_page(raw: c.FPDF_PAGE, textpage: c.FPDF_TEXTPAGE) -> dict:
    """What PDFium's objects and text page tell of one page, read with
    pypdfium2 alone: the characters of its content, each with its origin, its
    code unit and its text object, in content order (by text object, in the
    order a walk of the page's objects meets them; within one, as the text
    page gives them); the text objects that paint and have no character on
    the text page, and how many of them overlap a text object on it that
    paints nothing; and how many annotations hold a text object in their
    appearance."""
    texts = [
        obj
        for obj in _objects(c.FPDFPage_CountObjects(raw), partial(c.FPDFPage_GetObject, raw))
        if c.FPDFPageObj_GetType(obj) == c.FPDF_PAGEOBJ_TEXT
    ]
    order = {_address(obj): position for position, obj in enumerate(texts)}
    characters = []
    x, y = ctypes.c_double(), ctypes.c_double()
    for index in range(c.FPDFText_CountChars(textpage)):
        if c.FPDFText_IsGenerated(textpage, index) != 0:
            continue
        obj = c.FPDFText_GetTextObject(textpage, index)
        if not obj or _address(obj) not in order:
            raise AssertionError("PDFium gives a character of no text object on the page")
        if not c.FPDFText_GetCharOrigin(textpage, index, ctypes.byref(x), ctypes.byref(y)):
            raise AssertionError("PDFium gives no origin of a character")
        unit = c.FPDFText_GetUnicode(textpage, index)
        characters.append((order[_address(obj)], index, (x.value, y.value), unit, _address(obj)))
    characters.sort()
    used = {character[4] for character in characters}
    invisible = []
    left_out = []
    for obj in texts:
        box = _bounds(obj)
        if box is None:
            continue
        mode = c.FPDFTextObj_GetTextRenderMode(obj)
        if _address(obj) in used:
            if mode in INVISIBLE:
                invisible.append(box)
        elif mode not in INVISIBLE:
            left_out.append(box)
    appearances = 0
    for index in range(c.FPDFPage_GetAnnotCount(raw)):
        annotation = c.FPDFPage_GetAnnot(raw, index)
        try:
            held = _objects(
                c.FPDFAnnot_GetObjectCount(annotation), partial(c.FPDFAnnot_GetObject, annotation)
            )
            appearances += any(c.FPDFPageObj_GetType(obj) == c.FPDF_PAGEOBJ_TEXT for obj in held)
        finally:
            c.FPDFPage_CloseAnnot(annotation)
    return {
        "characters": [(origin, unit, obj) for _, _, origin, unit, obj in characters],
        "left out": len(left_out),
        "left out over invisible text": sum(
            any(_overlap(box, other) for other in invisible) for box in left_out
        ),
        "annotations with text in their appearance": appearances,
    }


def owners(found: tuple[Glyph, ...], characters: list) -> dict[int, int] | None:
    """Each glyph's text object, that of its first character: the glyphs hold
    the characters one after another, as many as their text has code units,
    each at the glyph's origin. None where they hold other characters than
    PDFium's text page, or other numbers of them."""
    owner: dict[int, int] = {}
    at = 0
    for glyph in found:
        assert isinstance(glyph.text, str)
        units = _units(glyph.text)
        held = characters[at : at + len(units)]
        if [unit for _, unit, _ in held] != units:
            return None
        if not isinstance(glyph.origin, Unknown) and any(
            origin != (glyph.origin.x, glyph.origin.y) for origin, _, _ in held
        ):
            return None
        owner[glyph.ref.glyph_index] = held[0][2]
        at += len(units)
    return owner if at == len(characters) else None


def breaks(found: tuple[Glyph, ...], owner: dict[int, int]) -> Counter[str]:
    """The steps of LINE or more along the baseline, and less across it, that
    begin a new line between two glyphs of one text object, measured as
    docs/conventions.md, "Glyphs, words and lines", says; by whether the glyph
    before is white space, and by the step's direction."""
    counts: Counter[str] = Counter()
    before: Glyph | None = None
    pen = (0.0, 0.0)
    for glyph in found:
        if glyph.upright is not True or isinstance(glyph.box, Unknown):
            continue
        assert not isinstance(glyph.origin, Unknown)
        assert not isinstance(glyph.direction, Unknown) and not isinstance(glyph.size, Unknown)
        ux = math.cos(math.radians(glyph.direction))
        uy = math.sin(math.radians(glyph.direction))
        if before is not None and owner[before.ref.glyph_index] == owner[glyph.ref.glyph_index]:
            dx, dy = glyph.origin.x - pen[0], glyph.origin.y - pen[1]
            along = (dx * ux + dy * uy) / glyph.size
            across = (-dx * uy + dy * ux) / glyph.size
            if abs(across) < LINE <= abs(along):
                space = isinstance(before.text, str) and before.text.isspace()
                after = "after white space" if space else "after another glyph"
                counts[f"{after} {'forward' if along > 0 else 'back'}"] += 1
        end = max(
            (p.x - glyph.origin.x) * ux + (p.y - glyph.origin.y) * uy
            for p in (glyph.box.p1, glyph.box.p2, glyph.box.p3, glyph.box.p4)
        )
        pen = (glyph.origin.x + end * ux, glyph.origin.y + end * uy)
        before = glyph
    return counts


def read_file(data: bytes) -> dict:
    """The receipt entry of one file."""
    digests = {key: Digest() for key in ("glyphs", "words", "lines")}
    totals: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    known: Counter[str] = Counter()
    split: Counter[str] = Counter()
    differences: list[str] = []
    not_compared = 0
    with pdfacts.open(data) as document:
        page_count = document.page_count
        pages = [
            (
                pdfacts.glyphs(document, index),
                pdfacts.words(document, index),
                pdfacts.lines(document, index),
            )
            for index in range(page_count)
        ]
    document = pypdfium2.PdfDocument(data)
    try:
        for index, (found, made, joined) in enumerate(pages):
            digest_page(digests, found, made, joined)
            page = document[index]
            textpage = page.get_textpage()
            try:
                pdfium = pdfium_page(page.raw, textpage.raw)
            finally:
                textpage.close()
                page.close()
            for key in (
                "left out",
                "left out over invisible text",
                "annotations with text in their appearance",
            ):
                known[key] += pdfium[key]
            whole = next((v for v in (found, made, joined) if isinstance(v, Unknown)), None)
            if whole is not None:
                unknown[f"page as a whole {whole.reason.value}"] += 1
                not_compared += 1
                continue
            assert not isinstance(found, Unknown)
            assert not isinstance(made, Unknown) and not isinstance(joined, Unknown)
            totals.update(glyphs=len(found), words=len(made), lines=len(joined))
            for glyph in found:
                for name in GLYPH_VALUES:
                    value = getattr(glyph, name)
                    if isinstance(value, Unknown):
                        unknown[f"glyph {name} {value.reason.value}"] += 1
                known["glyphs not upright"] += glyph.upright is False
            for kind, facts in (("word", made), ("line", joined)):
                for fact in facts:
                    if isinstance(fact.text, Unknown):
                        unknown[f"{kind} text {fact.text.reason.value}"] += 1
            if any(isinstance(glyph.text, Unknown) for glyph in found):
                not_compared += 1
                continue
            owner = owners(found, pdfium["characters"])
            if owner is None:
                differences.append(f"{index}")
                continue
            split.update(breaks(found, owner))
    finally:
        document.close()
    return {
        "page_count": page_count,
        "glyph_count": totals["glyphs"],
        "word_count": totals["words"],
        "line_count": totals["lines"],
        **{key: digest.hexdigest() for key, digest in digests.items()},
        "unknown": dict(sorted(unknown.items())),
        "known": {key: known[key] for key in KNOWN},
        "breaks": {key: split[key] for key in BREAKS},
        "pdfium_differences": differences,
        "pages_not_compared": not_compared,
    }


def exit_status(receipt: dict) -> int:
    if receipt["errors"]:
        return 1
    for entry in receipt["files"].values():
        if entry["pdfium_differences"] or entry["pages_not_compared"]:
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M4a gate: glyphs, words and lines.")
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
                f"ok {name} {entry['page_count']} pages {entry['glyph_count']} glyphs"
                f" {entry['word_count']} words {entry['line_count']} lines"
            )
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "gate": "m4a",
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


def _tally(files: dict[str, dict], group: str, key: str) -> str:
    """A count over the files, and in how many files it is not 0."""
    counts = [entry[group].get(key, 0) for entry in files.values()]
    return f"{sum(counts)} in {sum(1 for count in counts if count)} files"


def print_summary(count: int, files: dict[str, dict]) -> None:
    pages = sum(entry["page_count"] for entry in files.values())
    totals = ", ".join(
        f"{sum(entry[f'{kind}_count'] for entry in files.values())} {kind}s"
        for kind in ("glyph", "word", "line")
    )
    print(f"{count} files, {pages} pages: {totals}")
    unknown: Counter[str] = Counter()
    for entry in files.values():
        unknown.update(entry["unknown"])
    print("unknown: " + (", ".join(f"{key} {unknown[key]}" for key in sorted(unknown)) or "none"))
    print(
        "not given: glyphs not upright "
        + _tally(files, "known", "glyphs not upright")
        + ", annotations with text in their appearance "
        + _tally(files, "known", "annotations with text in their appearance")
    )
    print(
        "left out by PDFium as drawn again: text objects "
        + _tally(files, "known", "left out")
        + ", over invisible text "
        + _tally(files, "known", "left out over invisible text")
    )
    print(
        "new lines inside one text object: after white space, forward "
        + _tally(files, "breaks", "after white space forward")
        + ", back "
        + _tally(files, "breaks", "after white space back")
        + "; after another glyph, forward "
        + _tally(files, "breaks", "after another glyph forward")
        + ", back "
        + _tally(files, "breaks", "after another glyph back")
    )
    equal = sum(
        1
        for entry in files.values()
        if not entry["pdfium_differences"] and not entry["pages_not_compared"]
    )
    left = sum(entry["pages_not_compared"] for entry in files.values())
    print(
        f"PDFium's own count of characters: equal in {equal} of {count} files;"
        f" not compared: {left} pages"
    )


if __name__ == "__main__":
    raise SystemExit(main())
