"""The M4a gate's constructed cases: the glyphs, words and lines of each page, as written.

The cases are those of tests/synthetic/cases.py, TEXT: each rule and each
threshold of docs/conventions.md, "Glyphs, words and lines", on both sides,
drawn by a content stream and read through PDFium. The values expected are
the construction's (AGENTS.md, rule 2). A rendering checks the boxes apart
from pdfacts: the dark pixels lie in the words' boxes, and each word's box
holds some.
"""

from __future__ import annotations

import pypdfium2
import pytest

import pdfacts
from pdfacts import Rect, glyphs, lines, words
from synthetic import cases


@pytest.mark.parametrize("name", list(cases.TEXT))
def test_what_every_text_case_holds(name):
    case = cases.TEXT[name]()
    with pdfacts.open(case.data) as document:
        found, made, joined = glyphs(document, 0), words(document, 0), lines(document, 0)
    assert tuple(glyph.text for glyph in found) == case.glyphs
    assert [[made[i].text for i in line.words] for line in joined] == [
        [text for text, _ in written] for written in case.lines
    ]
    for line, written in zip(joined, case.lines, strict=True):
        for i, (_, box) in zip(line.words, written, strict=True):
            if box is not None:
                assert made[i].box == Rect(*box)


@pytest.mark.parametrize("name", [name for name, make in cases.TEXT.items() if make().inked])
def test_the_ink_lies_in_the_words(name):
    """At scale 1 a pixel is one unit of user space on this page; pixel rows
    count downwards from the top, user space upwards. A pixel's centre lies in
    a box within one unit."""
    case = cases.TEXT[name]()
    boxes = [box for written in case.lines for _, box in written]
    assert None not in boxes
    bitmap = pypdfium2.PdfDocument(case.data)[0].render(scale=1, grayscale=True)
    assert (bitmap.width, bitmap.height) == (400, 300)
    pixels = bytes(bitmap.buffer)
    dark = [
        (column + 0.5, 300 - row - 0.5)
        for row in range(bitmap.height)
        for column in range(bitmap.width)
        if pixels[row * bitmap.stride + column] < 128
    ]
    assert dark, "nothing was drawn"

    def near(point, box, margin):
        x, y = point
        return box[0] - margin <= x <= box[2] + margin and box[1] - margin <= y <= box[3] + margin

    stray = [point for point in dark if not any(near(point, box, 1) for box in boxes)]
    assert not stray, stray[:5]
    empty = [box for box in boxes if not any(near(point, box, 0) for point in dark)]
    assert not empty, empty
