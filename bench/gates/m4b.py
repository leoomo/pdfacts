"""The M4b gate: the glyphs of TextRules.V2 against PDFium's characters and against rendering.

For every page of every file, after the runtime installed the font policy,
glyphs(), words() and lines() are read with TextRules.V2, and glyphs() also
with the default rules, PDFium's characters. The receipt holds per file the
counts and a digest of each fact of V2, for comparing platforms; the values
that are unknown, by name and reason; the pages whose text is unknown as a
whole, which are measured no further; and two measurements. It holds no value
and no text of a document.

Agreement. Each character of PDFium that has a text and an origin, white space
included, is looked for among the glyphs of V2 at its place, the origins within
0.01 in x and in y, and counted once in the first of eight classes, from "same
text" to "no glyph at the place". Each glyph of V2 that is no white space is
counted once in the first of four: a PDFium character at its place, drawn
again, invisible, other.

Word boxes against rendering. The text alone is rendered by PDFium at about 4
pixels per point, in ceil(4 w) x ceil(4 h) pixels over which PDFium stretches
the page, so that points are mapped with the rendering's own scale: every
object that is neither text nor a form is made inactive, annotations and form
fields are not drawn, the background is transparent, a pixel is ink from an
opacity of 64. The 8-connected components of the ink go to the words: a
component to the word whose rectangle holds most of its pixels, else to the
word nearest to it within a band, else to none. A word whose glyphs are all
invisible takes no ink and is not judged. Any other word is judged: it is
correct when its own ink lies within 0.1 em of its box; a word with no ink of
its own is judged and not correct, and counted apart; ink of another line
inside a box is counted apart. Two kinds of box are measured: those of V2, one
em tall at the least, and variant D, from the font's descent to its ascent, the
font's heights unscaled (ROADMAP.md, M4b: if the descriptor's height does
better, the question goes back to the maintainer). Run from the repository
root:

    uv run python bench/gates/m4b.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]

Exit status 0 when every selected file was read without an error, no page is
unknown as a whole, the rendering of every page was mapped as PDFium maps it,
at least 99% of the words judged for V2 are correct, and the descriptor's height
does not do better; 1 otherwise, the descriptor's height doing better being a
question for the maintainer; 2 when the run was refused and no receipt was
written.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import platform
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import numpy as np
import pypdfium2
import pypdfium2.raw as c
import scipy
from scipy import ndimage

import pdfacts
from pdfacts import Glyph, Line, PageGeometry, TextRules, Unknown, Word
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info
from pdfacts.text import glyphs as glyphs_module
from pdfacts.text.angles import unit_of

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "bench" / "corpus" / "manifest.csv"
sys.path.insert(0, str(ROOT / "bench"))

# _objects is m4a's walk of the page objects, forms expanded.
from m4a import GLYPH_VALUES, INVISIBLE, _objects, commit_argument, digest_page  # noqa: E402

from digest import Digest  # noqa: E402

# Pixels per point of the rendering, and the opacity, of 255, from which a pixel is ink.
SCALE = 4
INK = 64
# Two glyphs are at one place when their origins differ by at most this in x and in y, as
# floats differ, no rounding. The cells that find them are wider, so that a glyph at the place
# stands in the same cell or the next.
PLACE = 0.01
CELL = 0.02
# A word box is correct when its ink lies within this many em of it (decision of the
# maintainer, 2026-10-07); the gate holds when this percentage of the words are correct.
TOLERANCE = 0.1
SHARE = 99
# A word is horizontal as shown when its direction in display space is (1, 0) within this.
HORIZONTAL = 1e-6
# The pixels that touch a pixel, diagonals included.
EIGHT = np.ones((3, 3), dtype=int)

VARIANTS = ("V2", "D")
# The classes of PDFium's characters, and of the glyphs of V2, in the order they are tried.
FOUND = (
    "same text",
    "line-end hyphen marker",
    "soft hyphen read as a hyphen",
    "same text under NFKC",
    "V2 text unknown",
    "same first character",
    "other text at the place",
    "no glyph at the place",
)
LEFT = ("a PDFium character at the place", "drawn again", "invisible", "other")
# What falls in no class is counted under a key of its own. A V2 glyph whose text is unknown
# is counted by the reason, under "V2: text unknown <reason>".
APART = (
    "PDFium: text unknown",
    "PDFium: origin unknown",
    "V2: white space",
    "V2: origin unknown",
    "pages without PDFium's characters",
)
AGREEMENT = (*(f"PDFium: {name}" for name in FOUND), *(f"V2: {name}" for name in LEFT), *APART)
# What the receipt counts of the words of each variant, of the pages, and of the two together.
KINDS = ("not horizontal", "outside the page", "measured")
WORDS = (
    "words",
    *KINDS,
    "no ink, all invisible",
    "judged",
    "judged without ink of their own",
    "correct",
    "failing vertically",
    "failing horizontally",
    "into another line",
    "unattributed components",
    "unattributed pixels",
)
PAGES = ("pages measured", "pages not mapped", "pages where D groups otherwise")
COMPARED = ("good in V2 only", "good in D only")
RENDER = (*PAGES, *(f"{variant} {name}" for variant in VARIANTS for name in WORDS), *COMPARED)


# --- the facts of V2 ------------------------------------------------------------------------


def read_page(
    document: pdfacts.Document, index: int
) -> tuple[tuple[Glyph, ...], tuple[Word, ...], tuple[Line, ...]] | Unknown:
    """The glyphs, words and lines of a page by TextRules.V2; or what the page's text is
    unknown as a whole."""
    found = pdfacts.glyphs(document, index, rules=TextRules.V2)
    made = pdfacts.words(document, index, rules=TextRules.V2)
    joined = pdfacts.lines(document, index, rules=TextRules.V2)
    for facts in (found, made, joined):
        if isinstance(facts, Unknown):
            return facts
    return found, made, joined  # type: ignore[return-value]


def tally_unknown(
    unknown: Counter[str], found: Sequence[Glyph], made: Sequence[Word], joined: Sequence[Line]
) -> None:
    """The values of a page's facts that are unknown, by name and reason, as the M4a gate
    tallies them."""
    for glyph in found:
        for name in GLYPH_VALUES:
            value = getattr(glyph, name)
            if isinstance(value, Unknown):
                unknown[f"glyph {name} {value.reason.value}"] += 1
    for kind, facts in (("word", made), ("line", joined)):
        for fact in facts:
            if isinstance(fact.text, Unknown):
                unknown[f"{kind} text {fact.text.reason.value}"] += 1


# --- agreement with PDFium's characters -----------------------------------------------------


class Places:
    """The glyphs with a known origin, found by place."""

    def __init__(self, glyphs: Sequence[Glyph]) -> None:
        self.origins: list[tuple[float, float] | None] = []
        self.cells: dict[tuple[float, float], list[int]] = {}
        for index, glyph in enumerate(glyphs):
            origin = glyph.origin
            self.origins.append(None if isinstance(origin, Unknown) else (origin.x, origin.y))
            if not isinstance(origin, Unknown):
                self.cells.setdefault((origin.x // CELL, origin.y // CELL), []).append(index)

    def at(self, x: float, y: float) -> list[int]:
        """The indices, in order, of the glyphs at the place (x, y)."""
        found = []
        for column in (-1.0, 0.0, 1.0):
            for row in (-1.0, 0.0, 1.0):
                for index in self.cells.get((x // CELL + column, y // CELL + row), ()):
                    there = self.origins[index]
                    assert there is not None
                    if abs(there[0] - x) <= PLACE and abs(there[1] - y) <= PLACE:
                        found.append(index)
        return sorted(found)


def found_class(text: str, nearby: Sequence[str | Unknown]) -> str:
    """The first class of FOUND that holds for the text of a PDFium character and the texts of
    the glyphs of V2 at its place."""
    texts = [t for t in nearby if isinstance(t, str)]
    if text in texts:
        return "same text"
    if text == "\x02" and any(t in ("-", "\u00ad") for t in texts):
        return "line-end hyphen marker"
    if text == "-" and "\u00ad" in texts:
        return "soft hyphen read as a hyphen"
    folded = unicodedata.normalize("NFKC", text)
    if any(unicodedata.normalize("NFKC", t) == folded for t in texts):
        return "same text under NFKC"
    if len(texts) < len(nearby):
        return "V2 text unknown"
    if any(t[:1] == text[:1] for t in texts):
        return "same first character"
    return "other text at the place" if nearby else "no glyph at the place"


def agreement(pdfium: Sequence[Glyph], v2: Sequence[Glyph]) -> Counter[str]:
    """Where PDFium's characters are among the glyphs of V2, and what V2 has that PDFium leaves
    out. Each character of PDFium with a text and an origin, white space included, is counted
    in the first class of FOUND that holds, each glyph of V2 that is no white space likewise in
    the first of LEFT, under the key of its class with its side in front. White space of V2
    and unknown texts and origins are counted apart, under keys of their own."""
    counts: Counter[str] = Counter()
    of_v2, of_pdfium = Places(v2), Places(pdfium)
    for glyph in pdfium:
        if isinstance(glyph.text, Unknown):
            counts["PDFium: text unknown"] += 1
        elif isinstance(glyph.origin, Unknown):
            counts["PDFium: origin unknown"] += 1
        else:
            nearby = [v2[i].text for i in of_v2.at(glyph.origin.x, glyph.origin.y)]
            counts["PDFium: " + found_class(glyph.text, nearby)] += 1
    for index, glyph in enumerate(v2):
        if isinstance(glyph.text, Unknown):
            counts[f"V2: text unknown {glyph.text.reason.value}"] += 1
        elif glyph.text.isspace():
            counts["V2: white space"] += 1
        elif isinstance(glyph.origin, Unknown):
            counts["V2: origin unknown"] += 1
        elif of_pdfium.at(glyph.origin.x, glyph.origin.y):
            counts["V2: a PDFium character at the place"] += 1
        elif any(
            i < index and v2[i].text == glyph.text for i in of_v2.at(glyph.origin.x, glyph.origin.y)
        ):
            counts["V2: drawn again"] += 1
        elif glyph.render_mode in INVISIBLE:
            counts["V2: invisible"] += 1
        else:
            counts["V2: other"] += 1
    return counts


# --- variant D: the box from the descent to the ascent, the font's heights unscaled ---------


@contextmanager
def descriptor_heights() -> Iterator[None]:
    """For the block, the glyph boxes of TextRules.V2 are made from the font's descent to its
    ascent at the glyph's size, however little they span. _content_box uses the size for the
    heights alone, and scales the heights to span one size where they span less; given the
    size times the span, it scales them back to the size. Where the heights are invalid or
    unknown, or span one or more, it is called as it is."""
    original = glyphs_module._content_box

    def box(origin, along, size, advance, code):
        if code.heights_invalid is None and code.ascent is not None and code.descent is not None:
            span = code.ascent - code.descent
            if 0 < span < 1:
                return original(origin, along, size * span, advance, code)
        return original(origin, along, size, advance, code)

    glyphs_module._content_box = box
    try:
        yield
    finally:
        glyphs_module._content_box = original


# --- the text alone, rendered ---------------------------------------------------------------


def text_ink(page: pypdfium2.PdfPage) -> np.ndarray:
    """The ink of the page's text alone, SCALE pixels to the point, rounded up to whole pixels:
    every object that is neither text nor a form is made inactive, in forms too; annotations
    and form fields are not drawn; the background is transparent."""
    for obj in _objects(c.FPDFPage_CountObjects(page.raw), partial(c.FPDFPage_GetObject, page.raw)):
        if c.FPDFPageObj_GetType(obj) != c.FPDF_PAGEOBJ_TEXT:
            if not c.FPDFPageObj_SetIsActive(obj, False):
                raise RuntimeError("PDFium would not make an object inactive")
    bitmap = page.render(
        scale=SCALE,
        rotation=0,
        draw_annots=False,
        may_draw_forms=False,
        fill_color=(255, 255, 255, 0),
    )
    pixels = bitmap.to_numpy()
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise RuntimeError("the rendering has no opacity channel")
    return pixels[:, :, 3] >= INK


def pixel(
    geometry: PageGeometry, shape: tuple[int, ...], x: float, y: float
) -> tuple[float, float]:
    """A point of user space in a rendering of `shape`, (rows, columns): to display space, then
    to the pixels. PDFium makes the rendering ceil(SCALE * size) pixels in each direction and
    stretches the page over them, so a point lies at the rendering's size over the page's,
    not at SCALE, to the point. The rows run down."""
    m = geometry.to_display
    rows, columns = shape
    return (
        columns / geometry.width * (m.a * x + m.c * y + m.e),
        rows / geometry.height * (geometry.height - (m.b * x + m.d * y + m.f)),
    )


def mapping_holds(page: pypdfium2.PdfPage, geometry: PageGeometry, shape: tuple[int, ...]) -> bool:
    """Whether the rendering is as pixel() maps it: it is the size of the displayed page at
    SCALE within one pixel, and PDFium puts the corners of the visible box within one pixel of
    where pixel() does."""
    height, width = shape
    if abs(width - round(SCALE * geometry.width)) > 1:
        return False
    if abs(height - round(SCALE * geometry.height)) > 1:
        return False
    box = geometry.visible_box
    for x, y in ((box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)):
        column, row = ctypes.c_int(), ctypes.c_int()
        if not c.FPDF_PageToDevice(
            page.raw, 0, 0, width, height, 0, x, y, ctypes.byref(column), ctypes.byref(row)
        ):
            return False
        ours = pixel(geometry, shape, x, y)
        if max(abs(column.value - ours[0]), abs(row.value - ours[1])) > 1.0:
            return False
    return True


# --- word boxes against the ink -------------------------------------------------------------


@dataclass(frozen=True)
class Placed:
    """A word as the rendering is measured against it: its kind, one of KINDS; the largest size
    of its glyphs; how many of them paint nothing, "all", "some" or "none"; and its box in
    pixels, rounded outward and clipped to the rendering, (x0, y0, x1, y1), half open, where
    the word is measured."""

    kind: str
    size: float
    invisible: str
    rect: tuple[int, int, int, int] | None

    @property
    def band(self) -> int:
        """How far from its rectangle a component may lie and still be the word's."""
        # Half an em, at least 2 pixels, so that the smallest text still has a band.
        return max(2, math.ceil(0.5 * self.size * SCALE))


def place_words(
    geometry: PageGeometry,
    found: Sequence[Glyph],
    made: Sequence[Word],
    width: int,
    height: int,
) -> list[Placed]:
    """Each word of a page placed in a rendering of width x height pixels. A word is measured
    when its first glyph runs along +x in display space and its box is not wholly outside."""
    m = geometry.to_display
    placed = []
    for word in made:
        glyphs = [found[index] for index in word.glyphs]
        sizes = [glyph.size for glyph in glyphs if not isinstance(glyph.size, Unknown)]
        direction = glyphs[0].direction
        # A word is made of glyphs whose box is known, and that needs both.
        assert len(sizes) == len(glyphs) and not isinstance(direction, Unknown)
        hidden = sum(1 for glyph in glyphs if glyph.render_mode in INVISIBLE)
        invisible = "all" if hidden == len(glyphs) else "none" if hidden == 0 else "some"
        ux, uy = unit_of(direction)
        shown = (m.a * ux + m.c * uy, m.b * ux + m.d * uy)
        kind, rect = "not horizontal", None
        if abs(shown[1]) <= HORIZONTAL and abs(shown[0] - 1.0) <= HORIZONTAL:
            box = word.box
            shape = (height, width)
            corners = [
                pixel(geometry, shape, x, y) for x in (box.x0, box.x1) for y in (box.y0, box.y1)
            ]
            columns, rows = [x for x, _ in corners], [y for _, y in corners]
            x0 = math.floor(min(max(min(columns), 0.0), width))
            x1 = math.ceil(min(max(max(columns), 0.0), width))
            y0 = math.floor(min(max(min(rows), 0.0), height))
            y1 = math.ceil(min(max(max(rows), 0.0), height))
            if x1 > x0 and y1 > y0:
                kind, rect = "measured", (x0, y0, x1, y1)
            else:
                kind = "outside the page"
        placed.append(Placed(kind, max(sizes), invisible, rect))
    return placed


@dataclass(frozen=True)
class Components:
    """The 8-connected components of a page's ink, labelled from 1; 0 is no ink. `pixels`
    counts the pixels of each label, 0 included; `boxes` has a row for each label from 1: the
    first row and the row after the last, the first column and the column after the last."""

    labels: np.ndarray
    pixels: np.ndarray
    boxes: np.ndarray
    ink: int

    @property
    def count(self) -> int:
        return len(self.boxes)


def components_of(ink: np.ndarray) -> Components:
    """The 8-connected components of the ink of a page, given as booleans, rows down."""
    labels, count = ndimage.label(ink, structure=EIGHT)
    spans = ndimage.find_objects(labels)
    boxes = np.array(
        [(rows.start, rows.stop, columns.start, columns.stop) for rows, columns in spans],
        dtype=np.int64,
    ).reshape(count, 4)
    pixels = np.bincount(labels.ravel(), minlength=count + 1)
    return Components(labels, pixels, boxes, int(np.count_nonzero(ink)))


@dataclass(frozen=True)
class Measured:
    """For each word, in pixels: its own ink; how far its ink reaches beyond its rectangle up,
    down, to the left and to the right; how deep inside its rectangle lies ink that a word of
    another line owns. And the ink that no word owns."""

    own: np.ndarray
    up: np.ndarray
    down: np.ndarray
    left: np.ndarray
    right: np.ndarray
    depth: np.ndarray
    unattributed_components: int
    unattributed_pixels: int


def nearest(found: Components, label: int, edges: np.ndarray) -> tuple[int, float]:
    """The row of `edges`, rectangles (x0, y0, x1, y1), that is nearest to a component, and its
    squared distance: from the centre of a pixel of the component to the rectangle as a
    continuous area, the least over the pixels. A tie goes to the lowest row."""
    first_row, end_row, first_column, end_column = found.boxes[label - 1]
    # No pixel lies nearer to a rectangle than the box of the pixel centres does, so the
    # rectangles are tried by that bound, and the search ends where it passes the best distance.
    gap_x = np.maximum(
        np.maximum(edges[:, 0] - (end_column - 0.5), (first_column + 0.5) - edges[:, 2]), 0.0
    )
    gap_y = np.maximum(
        np.maximum(edges[:, 1] - (end_row - 0.5), (first_row + 0.5) - edges[:, 3]), 0.0
    )
    bound = gap_x * gap_x + gap_y * gap_y
    rows, columns = np.nonzero(found.labels[first_row:end_row, first_column:end_column] == label)
    x, y = columns + (first_column + 0.5), rows + (first_row + 0.5)
    best, best_row = math.inf, -1
    for row in np.argsort(bound, kind="stable"):
        if bound[row] > best:
            break
        dx = np.maximum(np.maximum(edges[row, 0] - x, x - edges[row, 2]), 0.0)
        dy = np.maximum(np.maximum(edges[row, 1] - y, y - edges[row, 3]), 0.0)
        distance = float(np.min(dx * dx + dy * dy))
        if distance < best or (distance == best and row < best_row):
            best, best_row = distance, int(row)
    return best_row, best


def measure(found: Components, placed: Sequence[Placed], line_of: Sequence[int]) -> Measured:
    """The ink of a page given to its words, and measured per word. A component goes to the word
    whose rectangle holds most of its pixels, the lowest word where two hold as many. One that
    no rectangle holds a pixel of goes to the word whose rectangle is nearest, if that lies
    within the word's band, else to none. A word whose glyphs are all invisible takes no ink: it
    holds no component, and none goes to it as the nearest. `line_of` is the line each word is
    in."""
    words = len(placed)
    measured = [
        i for i, word in enumerate(placed) if word.rect is not None and word.invisible != "all"
    ]
    rects = np.zeros((words, 4), dtype=np.int64)
    for i in measured:
        rects[i] = placed[i].rect
    owner = np.full(found.count + 1, -1, dtype=np.int64)
    most = np.zeros(found.count + 1, dtype=np.int64)
    holds: dict[int, np.ndarray] = {}
    for word in measured:
        x0, y0, x1, y1 = rects[word]
        part = found.labels[y0:y1, x0:x1]
        labels, pixels = np.unique(part[part > 0], return_counts=True)
        holds[word] = labels
        more = pixels > most[labels]
        owner[labels[more]] = word
        most[labels[more]] = pixels[more]
    unattributed = [0, 0]
    for label in np.nonzero(most[1:] == 0)[0] + 1:
        row, distance = nearest(found, int(label), rects[measured]) if measured else (-1, math.inf)
        if row >= 0 and distance <= placed[measured[row]].band ** 2:
            owner[label] = measured[row]
        else:
            unattributed[0] += 1
            unattributed[1] += int(found.pixels[label])
    owned = np.nonzero(owner[1:] >= 0)[0]
    to = owner[1:][owned]
    boxes = found.boxes[owned]
    own = np.bincount(to, weights=found.pixels[1:][owned], minlength=words).astype(np.int64)
    reach = {name: np.zeros(words, dtype=np.int64) for name in ("up", "down", "left", "right")}
    x0, y0, x1, y1 = rects[to].T
    np.maximum.at(reach["up"], to, np.maximum(y0 - boxes[:, 0], 0))
    np.maximum.at(reach["down"], to, np.maximum(boxes[:, 1] - y1, 0))
    np.maximum.at(reach["left"], to, np.maximum(x0 - boxes[:, 2], 0))
    np.maximum.at(reach["right"], to, np.maximum(boxes[:, 3] - x1, 0))
    depth = np.zeros(words, dtype=np.int64)
    lines = np.asarray(line_of)
    for word, labels in holds.items():
        if not np.any((owner[labels] >= 0) & (owner[labels] != word)):
            continue
        x0, y0, x1, y1 = rects[word]
        owners = owner[found.labels[y0:y1, x0:x1]]
        rows, columns = np.nonzero((owners >= 0) & (owners != word))
        # How far inside the rectangle a pixel lies: its distance to the nearest edge, the
        # outermost row and column being 1.
        inside = np.minimum(
            np.minimum(columns + 1, (x1 - x0) - columns), np.minimum(rows + 1, (y1 - y0) - rows)
        )
        other = lines[owners[rows, columns]] != lines[word]
        if other.any():
            depth[word] = int(inside[other].max())
    return Measured(
        own, reach["up"], reach["down"], reach["left"], reach["right"], depth, *unattributed
    )


def line_index(made: Sequence[Word], joined: Sequence[Line]) -> list[int]:
    """The line each word is in: a word is in one line."""
    line_of = [-1] * len(made)
    for number, line in enumerate(joined):
        for word in line.words:
            assert line_of[word] == -1, "a word is in two lines"
            line_of[word] = number
    assert -1 not in line_of, "a word is in no line"
    return line_of


def measure_words(
    geometry: PageGeometry,
    facts: tuple[Sequence[Glyph], Sequence[Word], Sequence[Line]],
    found: Components,
) -> tuple[list[Placed], Measured]:
    """The words of a page, placed in the rendering whose components are `found`, and measured."""
    glyphs, words, lines = facts
    height, width = found.labels.shape
    placed = place_words(geometry, glyphs, words, width, height)
    measured = measure(found, placed, line_index(words, lines))
    if int(measured.own.sum()) + measured.unattributed_pixels != found.ink:
        raise AssertionError("the ink is not all owned or unattributed")
    return placed, measured


def judge(placed: Sequence[Placed], measured: Measured) -> tuple[Counter[str], np.ndarray]:
    """What the words of one variant of a page come to, by the keys of WORDS; and for each word
    whether its box is good: it has ink of its own, all within TOLERANCE of it, and no ink of
    another line lies deeper than that inside it. The tolerance of a word is TOLERANCE times its
    size, in points. A measured word is judged unless all its glyphs are invisible, which are
    counted as having no ink; a judged word without ink of its own is counted so and is not
    correct, nor does it fail by a cut or by depth."""
    counts: Counter[str] = Counter(words=len(placed))
    counts.update(word.kind for word in placed)
    counts["unattributed components"] = measured.unattributed_components
    counts["unattributed pixels"] = measured.unattributed_pixels
    tolerance = TOLERANCE * np.array([word.size for word in placed], dtype=np.float64) + 1e-9
    kept = np.array([word.kind == "measured" for word in placed], dtype=bool)
    hidden = np.array([word.invisible == "all" for word in placed], dtype=bool)
    ink = measured.own > 0
    judged = kept & ~hidden
    counts["no ink, all invisible"] = int((kept & hidden).sum())
    counts["judged without ink of their own"] = int((judged & ~ink).sum())
    vertical = np.maximum(measured.up, measured.down) / SCALE <= tolerance
    horizontal = np.maximum(measured.left, measured.right) / SCALE <= tolerance
    clear = measured.depth / SCALE <= tolerance
    correct = judged & ink & vertical & horizontal
    counts["judged"] = int(judged.sum())
    counts["correct"] = int(correct.sum())
    counts["failing vertically"] = int((judged & ink & ~vertical).sum())
    counts["failing horizontally"] = int((judged & ink & ~horizontal).sum())
    counts["into another line"] = int((judged & ink & ~clear).sum())
    return counts, correct & clear


def same_grouping(facts: tuple, other: tuple) -> bool:
    """Whether two readings of a page make their words of the same glyphs and their lines of
    the same words."""
    return [word.glyphs for word in facts[1]] == [word.glyphs for word in other[1]] and [
        line.words for line in facts[2]
    ] == [line.words for line in other[2]]


def measure_file(
    data: bytes,
    geometries: Sequence[PageGeometry],
    pages: Sequence[tuple | Unknown],
    heights: Sequence[tuple | Unknown],
) -> Counter[str]:
    """The word boxes of V2 (`pages`) and of D (`heights`) of a file against the rendering of the
    text alone, by the keys of RENDER. A page unknown as a whole is not rendered; a page whose
    rendering is not mapped as pixel() maps it is counted, and its words are not measured."""
    counts: Counter[str] = Counter()
    document = pypdfium2.PdfDocument(data)
    try:
        if len(document) != len(geometries):
            raise RuntimeError("pdfacts and PDFium find different numbers of pages")
        for index, (facts, other) in enumerate(zip(pages, heights, strict=True)):
            if isinstance(facts, Unknown):
                continue
            page = document[index]
            try:
                ink = text_ink(page)
                mapped = mapping_holds(page, geometries[index], ink.shape)
            finally:
                page.close()
            if not mapped:
                counts["pages not mapped"] += 1
                continue
            counts["pages measured"] += 1
            found = components_of(ink)
            good = {}
            for variant, variant_facts in zip(VARIANTS, (facts, other), strict=True):
                placed, measured = measure_words(geometries[index], variant_facts, found)
                tallies, good[variant] = judge(placed, measured)
                counts.update({f"{variant} {key}": value for key, value in tallies.items()})
            if same_grouping(facts, other):
                counts["good in V2 only"] += int(np.count_nonzero(good["V2"] & ~good["D"]))
                counts["good in D only"] += int(np.count_nonzero(good["D"] & ~good["V2"]))
            else:
                counts["pages where D groups otherwise"] += 1
    finally:
        document.close()
    return counts


# --- a file, and the run --------------------------------------------------------------------


def read_file(data: bytes) -> dict:
    """The receipt entry of one file."""
    digests = {key: Digest() for key in ("glyphs", "words", "lines")}
    totals: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    compared: Counter[str] = Counter()
    pages_unknown: list[str] = []
    with pdfacts.open(data) as document:
        page_count = document.page_count
        geometries = [document.pages[index].geometry for index in range(page_count)]
        pages = [read_page(document, index) for index in range(page_count)]
        pdfium = [pdfacts.glyphs(document, index) for index in range(page_count)]
    with descriptor_heights(), pdfacts.open(data) as document:
        if document.page_count != page_count:
            raise RuntimeError("the two reads find different numbers of pages")
        heights = [read_page(document, index) for index in range(page_count)]
    for index, facts in enumerate(pages):
        if isinstance(facts, Unknown) != isinstance(heights[index], Unknown):
            raise RuntimeError("the descriptor's height changes which pages are unknown")
        if isinstance(facts, Unknown):
            digest_page(digests, facts, facts, facts)
            unknown[f"page as a whole {facts.reason.value}"] += 1
            pages_unknown.append(str(index))
            continue
        found, made, joined = facts
        digest_page(digests, found, made, joined)
        totals.update(glyphs=len(found), words=len(made), lines=len(joined))
        tally_unknown(unknown, found, made, joined)
        characters = pdfium[index]
        if isinstance(characters, Unknown):
            compared["pages without PDFium's characters"] += 1
        else:
            compared.update(agreement(characters, found))
    render = measure_file(data, geometries, pages, heights)
    return {
        "page_count": page_count,
        "glyph_count": totals["glyphs"],
        "word_count": totals["words"],
        "line_count": totals["lines"],
        **{key: digest.hexdigest() for key, digest in digests.items()},
        "unknown": dict(sorted(unknown.items())),
        "agreement": {key: compared[key] for key in AGREEMENT}
        | {key: n for key, n in sorted(compared.items()) if key not in AGREEMENT},
        "render": {key: render[key] for key in RENDER},
        "pages_unknown": pages_unknown,
    }


def correct_share(files: dict[str, dict]) -> tuple[int, int]:
    """The words of V2 that are correct, and those judged, over the files."""
    return (
        sum(entry["render"]["V2 correct"] for entry in files.values()),
        sum(entry["render"]["V2 judged"] for entry in files.values()),
    )


def enough(correct: int, judged: int) -> bool:
    """Whether the correct words are SHARE percent of those judged; if none was judged, no."""
    return judged > 0 and correct * 100 >= SHARE * judged


def descriptor_better(files: dict[str, dict]) -> bool:
    """Whether the descriptor's height does better than V2's: the words good in D and not in V2
    outnumber those good in V2 and not in D."""
    only_v2, only_d = (sum(entry["render"][key] for entry in files.values()) for key in COMPARED)
    return only_d > only_v2


def exit_status(receipt: dict) -> int:
    """1 where a file had an error, a page is unknown or not mapped, too few words of V2 are
    correct, or the descriptor's height does better, which the maintainer is to be asked."""
    if receipt["errors"]:
        return 1
    for entry in receipt["files"].values():
        if entry["pages_unknown"] or entry["render"]["pages not mapped"]:
            return 1
    if descriptor_better(receipt["files"]):
        return 1
    return 0 if enough(*correct_share(receipt["files"])) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M4b gate: TextRules.V2 measured.")
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
                + (
                    f" {len(entry['pages_unknown'])} pages unknown"
                    if entry["pages_unknown"]
                    else ""
                )
            )
    seconds = round(time.perf_counter() - started, 1)

    versions = {"numpy": np.__version__, "scipy": scipy.__version__}
    receipt = {
        "gate": "m4b",
        "environment": {
            "commit": arguments.commit,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_info": engine_info().as_dict() | versions,
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


def _sum(files: dict[str, dict], group: str, key: str) -> int:
    return sum(entry[group].get(key, 0) for entry in files.values())


def _counts(files: dict[str, dict], group: str, keys: Sequence[str], strip: str = "") -> str:
    return ", ".join(f"{key.removeprefix(strip)} {_sum(files, group, key)}" for key in keys)


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
    whole = [len(entry["pages_unknown"]) for entry in files.values()]
    print(f"pages unknown as a whole: {sum(whole)} in {sum(1 for n in whole if n)} files")
    print(
        "PDFium's characters, among the glyphs of V2: "
        + _counts(files, "agreement", [f"PDFium: {name}" for name in FOUND], "PDFium: ")
    )
    print(
        "glyphs of V2 that are no white space: "
        + _counts(files, "agreement", [f"V2: {name}" for name in LEFT], "V2: ")
    )
    extra = sorted({k for e in files.values() for k in e["agreement"]} - set(AGREEMENT))
    print("counted apart: " + _counts(files, "agreement", [*APART, *extra]))
    print(_counts(files, "render", PAGES))
    labels = ("V2, one em at the least", "D, the descriptor's height")
    for variant, label in zip(VARIANTS, labels, strict=True):
        n = {name: _sum(files, "render", f"{variant} {name}") for name in WORDS}
        print(
            f"word boxes of {label}: {n['words']} words, {n['not horizontal']} not horizontal,"
            f" {n['outside the page']} outside the page, {n['measured']} measured;"
            f" {n['no ink, all invisible']} all invisible, not judged;"
            f" judged {n['judged']}: correct {n['correct']}, without ink of their own"
            f" {n['judged without ink of their own']}, failing vertically"
            f" {n['failing vertically']}, failing horizontally {n['failing horizontally']},"
            f" into another line {n['into another line']}; unattributed ink:"
            f" {n['unattributed components']} components, {n['unattributed pixels']} pixels"
        )
    print(
        "good in V2 and not in D: "
        + str(_sum(files, "render", COMPARED[0]))
        + ", good in D and not in V2: "
        + str(_sum(files, "render", COMPARED[1]))
    )
    correct, judged = correct_share(files)
    share = f"{100 * correct / judged:.2f}%" if judged else "n/a"
    print(
        f"word boxes correct: {correct} of {judged} ({share}), at least {SHARE}%:"
        f" {'yes' if enough(correct, judged) else 'no'}"
    )
    print(f"the descriptor's height does better: {'yes' if descriptor_better(files) else 'no'}")


if __name__ == "__main__":
    raise SystemExit(main())
