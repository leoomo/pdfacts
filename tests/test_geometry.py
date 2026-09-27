"""Geometry types and the transform between user space and display space.

The expected numbers are typed in as literals: hand-computed values, and the
positions where a rectangle on constructed pages was drawn. Each position was
measured with PDFium 153.0.7999.0 and with macOS Quick Look (PDFKit), a
renderer independent of both engines; the two agreed within 0.1 pt. The
numbers are never computed by the code under test, and never by applying a
matrix and its inverse to each other: two mistakes of the same kind would
cancel out in a round trip.

The page of the measurements has the visible box (0, 0, 400, 300) and one
filled rectangle at (50, 40, 110, 60) in user space.
"""

from __future__ import annotations

import math
import pickle

import pytest

import pdfacts
from pdfacts import Matrix, PageGeometry, Point, Rect, Unknown, UnknownReason

RECTANGLE = Rect(50, 40, 110, 60)


def page(box=(0, 0, 400, 300), rotation=0, user_unit=1.0) -> PageGeometry:
    return PageGeometry(index=0, visible_box=Rect(*box), rotation=rotation, user_unit=user_unit)


def corners(rect: Rect) -> tuple[float, float, float, float]:
    return (rect.x0, rect.y0, rect.x1, rect.y1)


# Point and Rect


def test_a_rectangle_puts_its_corners_in_order():
    assert corners(Rect(400, 300, 0, 0)) == (0, 0, 400, 300)
    assert corners(Rect(0, 300, 400, 0)) == (0, 0, 400, 300)


def test_a_rectangle_knows_its_size_and_may_be_empty():
    assert (Rect(10, 20, 50, 80).width, Rect(10, 20, 50, 80).height) == (40, 60)
    assert (Rect(5, 5, 5, 9).width, Rect(5, 5, 5, 9).height) == (0, 4)


@pytest.mark.parametrize(
    "make",
    [
        lambda: Rect(0, 0, float("nan"), 1),
        lambda: Rect(0, float("-inf"), 1, 1),
        lambda: Point(float("inf"), 0),
        lambda: Point(0, float("nan")),
        lambda: Matrix(1, 0, 0, 1, float("inf"), 0),
    ],
)
def test_a_coordinate_that_is_not_finite_is_refused(make):
    with pytest.raises(ValueError):
        make()


def test_coordinates_are_floats_and_never_negative_zero():
    point = Point(-0.0, 3)
    assert isinstance(point.y, float)
    assert math.copysign(1, point.x) == 1
    rect = Rect(-0.0, 0, 1, 1)
    assert math.copysign(1, rect.x0) == 1


def test_the_types_are_values():
    assert Rect(0, 0, 1, 1) == Rect(1, 1, 0, 0)
    assert hash(Point(1, 2)) == hash(Point(1.0, 2.0))
    with pytest.raises(AttributeError):
        Point(1, 2).x = 3  # type: ignore[misc]


# Matrix


def test_the_matrix_has_the_meaning_of_a_pdf_matrix():
    # x' = a*x + c*y + e = 10 + 300 + 5; y' = b*x + d*y + f = 20 + 400 + 6
    assert Matrix(1, 2, 3, 4, 5, 6).apply(Point(10, 100)) == Point(315, 426)


def test_then_applies_the_first_matrix_first():
    move = Matrix(1, 0, 0, 1, 5, 0)
    double = Matrix(2, 0, 0, 2, 0, 0)
    # (1, 1) moved to (6, 1), then doubled to (12, 2); the other order gives (7, 2).
    assert move.then(double).apply(Point(1, 1)) == Point(12, 2)
    assert double.then(move).apply(Point(1, 1)) == Point(7, 2)


def test_then_composes_two_full_matrices():
    # Hand-computed: a = 7*1 + 9*2, b = 8*1 + 10*2, c = 7*3 + 9*4, d = 8*3 + 10*4,
    # e = 7*5 + 9*6 + 11, f = 8*5 + 10*6 + 12.
    assert Matrix(1, 2, 3, 4, 5, 6).then(Matrix(7, 8, 9, 10, 11, 12)) == Matrix(
        25, 28, 57, 64, 100, 112
    )


def test_apply_rect_gives_the_bounding_box_of_the_four_corners():
    turn = Matrix(0, 1, -1, 0, 0, 0)  # 90 degrees counterclockwise about the origin
    assert corners(turn.apply_rect(Rect(1, 2, 3, 5))) == (-5, 1, -2, 3)


def test_the_inverse_of_a_matrix():
    # Hand-computed: the inverse of (2, 0, 0, 4, 6, 8) is (0.5, 0, 0, 0.25, -3, -2).
    inverse = Matrix(2, 0, 0, 4, 6, 8).inverse()
    assert inverse == Matrix(0.5, 0, 0, 0.25, -3, -2)


def test_a_singular_matrix_has_no_inverse():
    with pytest.raises(ValueError):
        Matrix(1, 2, 2, 4, 0, 0).inverse()


def test_an_inverse_whose_determinant_overflows_is_refused():
    # 1e200 squared is inf; dividing by it would give a zero matrix.
    with pytest.raises(ValueError):
        Matrix(1e200, 0, 0, 1e200, 0, 0).inverse()


def test_a_number_too_large_for_a_float_is_refused():
    with pytest.raises(ValueError):
        Point(10**400, 0)
    with pytest.raises(ValueError):
        page(user_unit=10**400)


def test_a_decimal_is_refused_with_a_message_that_says_what_is_accepted():
    # pikepdf returns PDF reals as Decimal; a reader converts with float() first.
    from decimal import Decimal

    with pytest.raises(TypeError, match="an int, a float or Unknown"):
        page(user_unit=Decimal("2.5"))
    with pytest.raises(TypeError, match="int or a float"):
        Point(Decimal("1.5"), 0)


@pytest.mark.parametrize(
    "make",
    [
        lambda: Point("1", 2),
        lambda: Point(True, 0),
        lambda: Rect(0, 0, "400", 300),
        lambda: Matrix(1, 0, 0, 1, None, 0),
        lambda: page(user_unit="2"),
        lambda: page(user_unit=True),
        lambda: page(rotation=90.0),
        lambda: page(rotation=False),
        lambda: PageGeometry(index=0.5, visible_box=Rect(0, 0, 1, 1), rotation=0, user_unit=1.0),
        lambda: PageGeometry(index=True, visible_box=Rect(0, 0, 1, 1), rotation=0, user_unit=1.0),
        lambda: PageGeometry(index=0, visible_box=(0, 0, 400, 300), rotation=0, user_unit=1.0),
    ],
)
def test_a_value_of_the_wrong_type_is_refused(make):
    # A reader has to turn text or a flag from a file into Unknown itself.
    with pytest.raises(TypeError):
        make()


def test_a_page_geometry_takes_keywords_only():
    with pytest.raises(TypeError):
        PageGeometry(0, Rect(0, 0, 1, 1), 0, 1.0)  # type: ignore[misc]


# PageGeometry: the measured positions


@pytest.mark.parametrize(
    ("rotation", "size", "on_display"),
    [
        (0, (400, 300), (50, 40, 110, 60)),
        (90, (300, 400), (40, 290, 60, 350)),
        (180, (400, 300), (290, 240, 350, 260)),
        (270, (300, 400), (240, 50, 260, 110)),
    ],
)
def test_each_rotation_puts_the_rectangle_where_it_was_drawn(rotation, size, on_display):
    geometry = page(rotation=rotation)
    assert (geometry.width, geometry.height) == size
    assert corners(geometry.to_display.apply_rect(RECTANGLE)) == on_display


@pytest.mark.parametrize(
    ("rotation", "size", "on_display"),
    [
        (0, (360, 280), (30, 30, 90, 50)),
        (90, (280, 360), (30, 270, 50, 330)),
        (180, (360, 280), (270, 230, 330, 250)),
        (270, (280, 360), (230, 30, 250, 90)),
    ],
)
def test_a_crop_box_moves_the_origin_to_its_corner(rotation, size, on_display):
    geometry = page(box=(20, 10, 380, 290), rotation=rotation)
    assert (geometry.width, geometry.height) == size
    assert corners(geometry.to_display.apply_rect(RECTANGLE)) == on_display


def test_a_shifted_media_box_moves_the_origin_to_its_corner():
    geometry = page(box=(30, 20, 430, 320))
    assert (geometry.width, geometry.height) == (400, 300)
    assert corners(geometry.to_display.apply_rect(RECTANGLE)) == (20, 20, 80, 40)


def test_user_unit_does_not_scale_display_space():
    geometry = page(user_unit=2.0)
    assert (geometry.width, geometry.height) == (400, 300)
    assert corners(geometry.to_display.apply_rect(RECTANGLE)) == (50, 40, 110, 60)
    assert geometry.user_unit == 2.0


def test_an_unknown_user_unit_is_kept_and_the_transforms_still_work():
    unknown = Unknown(UnknownReason.INVALID, "UserUnit is -1")
    geometry = page(user_unit=unknown)
    assert geometry.user_unit == unknown
    assert corners(geometry.to_display.apply_rect(RECTANGLE)) == (50, 40, 110, 60)


@pytest.mark.parametrize(
    ("box", "rotation", "on_display"),
    [
        ((0, 0, 400, 300), 0, (50, 40, 110, 60)),
        ((0, 0, 400, 300), 90, (40, 290, 60, 350)),
        ((0, 0, 400, 300), 180, (290, 240, 350, 260)),
        ((0, 0, 400, 300), 270, (240, 50, 260, 110)),
        ((20, 10, 380, 290), 0, (30, 30, 90, 50)),
        ((20, 10, 380, 290), 90, (30, 270, 50, 330)),
        ((20, 10, 380, 290), 180, (270, 230, 330, 250)),
        ((20, 10, 380, 290), 270, (230, 30, 250, 90)),
        ((30, 20, 430, 320), 0, (20, 20, 80, 40)),
    ],
)
def test_to_user_brings_each_measured_rectangle_back(box, rotation, on_display):
    geometry = page(box=box, rotation=rotation)
    assert corners(geometry.to_user.apply_rect(Rect(*on_display))) == (50, 40, 110, 60)


# Hand-computed for the box (20.25, 10.5, 380.75, 290.125): to_display and to_user.
EXACT = {
    0: (Matrix(1, 0, 0, 1, -20.25, -10.5), Matrix(1, 0, 0, 1, 20.25, 10.5)),
    90: (Matrix(0, -1, 1, 0, -10.5, 380.75), Matrix(0, 1, -1, 0, 380.75, 10.5)),
    180: (Matrix(-1, 0, 0, -1, 380.75, 290.125), Matrix(-1, 0, 0, -1, 380.75, 290.125)),
    270: (Matrix(0, 1, -1, 0, 290.125, -20.25), Matrix(0, -1, 1, 0, 20.25, 290.125)),
}


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_transforms_are_exact_and_carry_no_rounding(rotation):
    geometry = page(box=(20.25, 10.5, 380.75, 290.125), rotation=rotation)
    assert (geometry.to_display, geometry.to_user) == EXACT[rotation]
    for matrix in (geometry.to_display, geometry.to_user):
        for element in (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f):
            # == cannot tell 0.0 from -0.0; the sign can.
            assert element != 0 or math.copysign(1, element) == 1, (rotation, matrix)


@pytest.mark.parametrize("rotation", [45, 360, -90, 91])
def test_a_rotation_other_than_the_four_is_refused(rotation):
    with pytest.raises(ValueError):
        page(rotation=rotation)


@pytest.mark.parametrize("user_unit", [0.0, -1.0, float("nan"), float("inf")])
def test_a_user_unit_that_is_not_a_positive_number_is_refused(user_unit):
    # A file may hold such a value; the reader turns it into Unknown, never into a number.
    with pytest.raises(ValueError):
        page(user_unit=user_unit)


def test_a_negative_page_index_is_refused():
    with pytest.raises(ValueError):
        PageGeometry(index=-1, visible_box=Rect(0, 0, 1, 1), rotation=0, user_unit=1.0)


def test_a_page_geometry_survives_pickling():
    geometry = page(
        box=(20, 10, 380, 290), rotation=90, user_unit=Unknown(UnknownReason.UNREADABLE)
    )
    assert pickle.loads(pickle.dumps(geometry)) == geometry


def test_the_geometry_types_are_exported():
    for name in ("Point", "Rect", "Matrix", "PageGeometry"):
        assert getattr(pdfacts, name) is not None
        assert name in pdfacts.__all__
