"""Criterion 2 of the probe: processor time per corpus file of three ways to
a page's text, each the fastest of five runs, the four ways taking turns.

- reference: PyMuPDF, as the first consumer reads text: open, and per page
  a text page with TEXTFLAGS_DICT, its dict, raw dict and words.
- pdfium: pdfacts as released: open, and per page lines() (glyphs, words
  and lines are made once).
- interpreter: pikepdf open, and per page the interpreter's glyphs, made
  into pdfacts glyphs, words and lines by the rules of M4a. pdfacts.open is
  not in it: a pdfacts that uses the interpreter opens QPDF anyway.
"""
import math
import statistics
import sys
import time
from pathlib import Path

import fitz
import pikepdf

import pdfacts
from interpreter import Unreadable, glyphs_of, page_glyphs
from pdfacts.text.layout import words_and_lines


def reference(data):
    doc = fitz.open(stream=data, filetype="pdf")
    for page in doc:
        tp = page.get_textpage(flags=fitz.TEXTFLAGS_DICT)
        tp.extractDICT()
        tp.extractRAWDICT()
        tp.extractWORDS()


def pdfium(data):
    with pdfacts.open(data) as doc:
        for page in doc.pages:
            pdfacts.lines(doc, page.index)


def interpreter(data, rotations, checked=True):
    pdf = pikepdf.open(__import__("io").BytesIO(data))
    for index, rotation in enumerate(rotations):
        try:
            raws = page_glyphs(pdf, index)
        except Unreadable:
            continue
        glyphs, _ = glyphs_of(raws, index, rotation, checked)
        words_and_lines(index, glyphs)


ROUNDS = 5


def fastest(data, rotations):
    """The four ways in turn, ROUNDS rounds; the fastest run of each."""
    ways = (
        (reference, (data,)),
        (pdfium, (data,)),
        (interpreter, (data, rotations)),
        (interpreter, (data, rotations, False)),
    )
    best = [math.inf] * len(ways)
    for _ in range(ROUNDS):
        for number, (run, args) in enumerate(ways):
            started = time.process_time()
            run(*args)
            best[number] = min(best[number], time.process_time() - started)
    return tuple(best)


rows = []
for path in sorted(Path(sys.argv[1]).glob("*.pdf")):
    data = path.read_bytes()
    with pdfacts.open(data) as doc:
        rotations = [page.geometry.rotation for page in doc.pages]
    t = fastest(data, rotations)
    rows.append(t)
    print(f"{path.name[:40]:40} pages={len(rotations):3d} reference={t[0]:7.3f} pdfium={t[1]:7.3f} interpreter={t[2]:7.3f} unchecked={t[3]:7.3f}  ratio={t[2] / t[0]:5.2f} {t[3] / t[0]:5.2f}")
med = [statistics.median(r[i] for r in rows) for i in range(4)]
print(f"files {len(rows)}; median seconds: reference {med[0]:.3f}, pdfium {med[1]:.3f}, interpreter {med[2]:.3f}, interpreter unchecked {med[3]:.3f}")
print(f"medians over reference: pdfium {med[1] / med[0]:.2f}, interpreter {med[2] / med[0]:.2f}, interpreter unchecked {med[3] / med[0]:.2f}")
print(f"median of per-file ratios over reference: interpreter {statistics.median(r[2] / r[0] for r in rows):.2f}, unchecked {statistics.median(r[3] / r[0] for r in rows):.2f}")
