"""The shape a subpath finds for its segments (docs/conventions.md, "Paths and images").

Each subpath is built from the points written here; the shape expected follows
from them under the rules (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

from pdfacts.core.geometry import Point, Quad, Rect
from pdfacts.graphics.paths import Segment, SegmentKind, ShapeKind, Subpath

M, L, C = SegmentKind.MOVE, SegmentKind.LINE, SegmentKind.CURVE


def segments(*items: tuple) -> tuple[Segment, ...]:
    """(kind, x, y), or (kind, x, y, closes); a curve (kind, x1, y1, x2, y2, x3, y3[, closes])."""
    out = []
    for item in items:
        kind = item[0]
        if kind is C:
            xs = item[1:7]
            points = (Point(xs[0], xs[1]), Point(xs[2], xs[3]), Point(xs[4], xs[5]))
            closes = item[7] if len(item) > 7 else False
        else:
            points = (Point(item[1], item[2]),)
            closes = item[3] if len(item) > 3 else False
        out.append(Segment(kind, points, closes))
    return tuple(out)


def rectangle(x0, y0, x1, y1, *, closes=True):
    """`re` as PDFium gives it: five points, the last back at the first."""
    return segments((M, x0, y0), (L, x1, y0), (L, x1, y1), (L, x0, y1), (L, x0, y0, closes))


def test_a_move_and_a_line_is_a_line():
    shape = Subpath(segments((M, 10, 20), (L, 110, 20)))
    assert (shape.shape, shape.rect, shape.quad) == (ShapeKind.LINE, None, None)


def test_a_move_and_a_line_that_closes_is_a_polyline():
    assert Subpath(segments((M, 10, 20), (L, 110, 20, True))).shape is ShapeKind.POLYLINE


@pytest.mark.parametrize("closes", [True, False], ids=["closed", "not closed"])
def test_four_lines_back_to_the_start_along_the_axes_are_a_rectangle(closes):
    shape = Subpath(rectangle(10, 20, 110, 70, closes=closes))
    assert shape.shape is ShapeKind.RECTANGLE
    assert shape.rect == Rect(10, 20, 110, 70)
    assert shape.quad is None


def test_a_rectangle_drawn_the_other_way_round_is_a_rectangle():
    shape = Subpath(rectangle(110, 70, 10, 20))
    assert (shape.shape, shape.rect) == (ShapeKind.RECTANGLE, Rect(10, 20, 110, 70))


def test_a_rectangle_without_height_is_a_rectangle():
    shape = Subpath(rectangle(10, 20, 110, 20))
    assert (shape.shape, shape.rect) == (ShapeKind.RECTANGLE, Rect(10, 20, 110, 20))


def test_a_rectangle_whose_first_edge_keeps_x_is_a_rectangle():
    shape = Subpath(segments((M, 10, 20), (L, 10, 70), (L, 110, 70), (L, 110, 20), (L, 10, 20)))
    assert (shape.shape, shape.rect) == (ShapeKind.RECTANGLE, Rect(10, 20, 110, 70))


def test_a_quad_that_begins_along_x_whose_only_slanted_edge_is_the_last():
    """(10, 20) up to (10, 70), across, down, then from (110, 30) back at a slant."""
    shape = Subpath(segments((M, 10, 20), (L, 10, 70), (L, 110, 70), (L, 110, 30), (L, 10, 20)))
    assert shape.shape is ShapeKind.QUAD


def test_a_z_that_begins_along_x_is_a_quad():
    shape = Subpath(segments((M, 0, 0), (L, 0, 10), (L, 10, 0), (L, 10, 10), (L, 0, 0)))
    assert shape.shape is ShapeKind.QUAD


def test_an_edge_along_x_off_by_a_thousandth_is_along_it():
    shape = Subpath(segments((M, 10, 20), (L, 10.001, 70), (L, 110, 70), (L, 110, 20), (L, 10, 20)))
    assert (shape.shape, shape.rect) == (ShapeKind.RECTANGLE, Rect(10, 20, 110, 70))


def test_the_rect_holds_all_four_corners():
    """The second corner lies lowest, within the tolerance of the first."""
    shape = Subpath(
        segments((M, 10, 20), (L, 110, 19.9995), (L, 110, 70), (L, 10, 70), (L, 10, 20))
    )
    assert (shape.shape, shape.rect) == (ShapeKind.RECTANGLE, Rect(10, 19.9995, 110, 70))


@pytest.mark.parametrize(
    "corners",
    [
        [(0, 0), (10, 0), (0, 0), (0, 10)],
        [(0, 0), (10, 0), (10, 10), (10, 0)],
    ],
    ids=["an L", "a T"],
)
def test_edges_that_do_not_alternate_are_a_quad(corners):
    """Each edge keeps x or y, but two in a row keep the same one; PDFium gives the L as drawn."""
    shape = Subpath(segments((M, *corners[0]), *((L, x, y) for x, y in corners[1:]), (L, 0, 0)))
    assert shape.shape is ShapeKind.QUAD
    assert shape.quad == Quad(*(Point(x, y) for x, y in corners))


def test_four_lines_back_to_the_start_turned_are_a_quad():
    corners = [(50, 0), (100, 50), (50, 100), (0, 50)]
    shape = Subpath(
        segments(
            (M, *corners[0]),
            (L, *corners[1]),
            (L, *corners[2]),
            (L, *corners[3]),
            (L, *corners[0], True),
        )
    )
    assert shape.shape is ShapeKind.QUAD
    assert shape.quad == Quad(*(Point(x, y) for x, y in corners))
    assert shape.rect is None


def test_corners_in_a_z_along_the_axes_are_a_quad():
    """(0, 0), (10, 0), (0, 10), (10, 10): two edges are diagonal."""
    shape = Subpath(segments((M, 0, 0), (L, 10, 0), (L, 0, 10), (L, 10, 10), (L, 0, 0)))
    assert shape.shape is ShapeKind.QUAD


def test_four_lines_that_do_not_come_back_are_a_polyline():
    shape = Subpath(segments((M, 10, 20), (L, 110, 20), (L, 110, 70), (L, 10, 70), (L, 10, 21)))
    assert shape.shape is ShapeKind.POLYLINE


@pytest.mark.parametrize("lines", [2, 3, 5, 6])
def test_other_counts_of_lines_are_a_polyline(lines):
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0), (5, 5), (0, 0)]
    shape = Subpath(segments((M, 0, 0), *((L, x, y) for x, y in points[1 : lines + 1])))
    assert shape.shape is ShapeKind.POLYLINE


def test_a_lone_move_is_a_polyline():
    assert Subpath(segments((M, 5, 5, True))).shape is ShapeKind.POLYLINE


def test_a_curve_makes_curves_whatever_else():
    shape = Subpath(segments((M, 0, 0), (L, 10, 0), (C, 10, 5, 5, 10, 0, 10), (L, 0, 0, True)))
    assert (shape.shape, shape.rect, shape.quad) == (ShapeKind.CURVES, None, None)


def test_a_rectangle_whose_last_point_misses_the_first_by_a_thousandth_is_one():
    shape = Subpath(
        segments((M, 10, 20), (L, 110, 20), (L, 110, 70), (L, 10, 70), (L, 10.001, 20.001))
    )
    assert shape.shape is ShapeKind.RECTANGLE


def test_one_that_misses_by_more_is_a_polyline():
    shape = Subpath(
        segments((M, 10, 20), (L, 110, 20), (L, 110, 70), (L, 10, 70), (L, 10.0011, 20))
    )
    assert shape.shape is ShapeKind.POLYLINE


def test_an_edge_off_the_axis_by_a_thousandth_is_along_it():
    shape = Subpath(segments((M, 10, 20), (L, 110, 20.001), (L, 110, 70), (L, 10, 70), (L, 10, 20)))
    assert shape.shape is ShapeKind.RECTANGLE
    assert shape.rect == Rect(10, 20, 110, 70)


def test_an_edge_off_by_more_makes_a_quad():
    shape = Subpath(
        segments((M, 10, 20), (L, 110, 20.0011), (L, 110, 70), (L, 10, 70), (L, 10, 20))
    )
    assert shape.shape is ShapeKind.QUAD


@pytest.mark.parametrize(
    ("start", "end", "shape"),
    [
        ((0.0, 0.0), (0.001, 0.001), ShapeKind.RECTANGLE),
        ((0.0, 0.0), (0.0011, 0.0), ShapeKind.POLYLINE),
        ((10000.0, 0.0), (10000.011, 0.0), ShapeKind.RECTANGLE),  # 0.001 + 1e-6 * 10000.011
        ((10000.0, 0.0), (10000.0112, 0.0), ShapeKind.POLYLINE),
        ((-10000.0, 0.0), (-10000.011, 0.0), ShapeKind.RECTANGLE),
        ((-10000.0, 0.0), (-10000.0112, 0.0), ShapeKind.POLYLINE),
    ],
    ids=[
        "0.001 off at 0",
        "0.0011 off at 0",
        "0.011 off at 10000",
        "0.0112 off at 10000",
        "0.011 off at -10000",
        "0.0112 off at -10000",
    ],
)
def test_the_tolerance_grows_with_the_size_of_the_numbers(start, end, shape):
    """A 10 by 10 square from `start` whose last line ends at `end`."""
    x, y = start
    square = segments((M, x, y), (L, x + 10, y), (L, x + 10, y + 10), (L, x, y + 10), (L, *end))
    assert Subpath(square).shape is shape


def test_a_quad_whose_only_slanted_edge_is_the_last():
    """(20, 70) back to (10, 20): the edge from the fourth corner to the move."""
    shape = Subpath(segments((M, 10, 20), (L, 110, 20), (L, 110, 70), (L, 20, 70), (L, 10, 20)))
    assert shape.shape is ShapeKind.QUAD
    assert shape.quad == Quad(Point(10, 20), Point(110, 20), Point(110, 70), Point(20, 70))


def test_the_shape_is_not_given_but_found():
    with pytest.raises(TypeError):
        Subpath(segments((M, 0, 0), (L, 1, 0)), ShapeKind.POLYLINE)
