"""The M3 gate's constructed cases: what each page draws, as it was built.

The cases are those of tests/synthetic/cases.py, GRAPHICS. The values expected
are the construction's, put into user space by hand (AGENTS.md, rule 2). A
rendering checks those values apart from pdfacts: where everything is filled
in black, the dark pixels lie in the box that holds every point drawn.
"""

from __future__ import annotations

import pypdfium2
import pytest

import pdfacts
from pdfacts import ColourSpace, Point, Quad, Rect, images, paths
from synthetic import cases


def read(case: cases.Drawing):
    with pdfacts.open(case.data) as document:
        return paths(document, 0), images(document, 0)


def annotation_index(ref) -> int | None:
    return None if ref.annotation is None else ref.annotation.annotation_index


@pytest.mark.parametrize("name", list(cases.GRAPHICS))
def test_what_every_graphics_case_draws(name):
    case = cases.GRAPHICS[name]()
    found, placed = read(case)
    drawn_paths = [(i, d) for i, d in enumerate(case.drawn) if isinstance(d, cases.DrawnPath)]
    drawn_images = [(i, d) for i, d in enumerate(case.drawn) if isinstance(d, cases.DrawnImage)]
    assert (len(found), len(placed)) == (len(drawn_paths), len(drawn_images))
    for path, (index, drawn) in zip(found, drawn_paths, strict=True):
        assert path.ref.drawing_index == index
        assert annotation_index(path.ref) == drawn.annotation
        assert len(path.subpaths) == len(drawn.subpaths)
        for subpath, expected in zip(path.subpaths, drawn.subpaths, strict=True):
            points = [(p.x, p.y) for segment in subpath.segments for p in segment.points]
            assert points == [(float(x), float(y)) for x, y in expected.points]
            assert (subpath.shape.value, subpath.closed) == (expected.shape, expected.closed)
            corners = [Point(x, y) for x, y in expected.points[:4]]
            if expected.shape == "rectangle":
                xs, ys = [p.x for p in corners], [p.y for p in corners]
                assert subpath.rect == Rect(min(xs), min(ys), max(xs), max(ys))
            else:
                assert subpath.rect is None
            assert subpath.quad == (Quad(*corners) if expected.shape == "quad" else None)
    for image, (index, drawn) in zip(placed, drawn_images, strict=True):
        assert image.ref.drawing_index == index
        assert annotation_index(image.ref) == drawn.annotation
        assert image.corners == Quad(*(Point(x, y) for x, y in drawn.corners))
        assert (image.pixel_width, image.pixel_height) == (3, 2)
        assert (image.colour_space, image.bits_per_pixel) == (ColourSpace.DEVICE_GRAY, 8)


@pytest.mark.parametrize("name", [name for name, make in cases.GRAPHICS.items() if make().inked])
def test_the_ink_lies_where_the_case_says(name):
    """At scale 1 a pixel is one unit of user space on this page; pixel rows
    count downwards from the top, user space upwards. Within one pixel."""
    case = cases.GRAPHICS[name]()
    points = [
        point
        for drawn in case.drawn
        for point in (
            drawn.corners
            if isinstance(drawn, cases.DrawnImage)
            else [p for subpath in drawn.subpaths for p in subpath.points]
        )
    ]
    bitmap = pypdfium2.PdfDocument(case.data)[0].render(scale=1, grayscale=True, draw_annots=True)
    assert (bitmap.width, bitmap.height) == (400, 300)
    pixels = bytes(bitmap.buffer)
    dark = [
        (column, row)
        for row in range(bitmap.height)
        for column in range(bitmap.width)
        if pixels[row * bitmap.stride + column] < 128
    ]
    assert dark, "nothing was drawn"
    ink = (
        min(column for column, _ in dark),
        300 - max(row for _, row in dark) - 1,
        max(column for column, _ in dark) + 1,
        300 - min(row for _, row in dark),
    )
    box = (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )
    assert all(abs(a - b) <= 1 for a, b in zip(ink, box, strict=True)), (ink, box)
