"""The fact types of paths and images: what they accept and what they refuse."""

from __future__ import annotations

import dataclasses
import math

import pytest

from pdfacts import Unknown, UnknownReason
from pdfacts.core.geometry import Point, Quad, Rect
from pdfacts.core.refs import AnnotationRef, GraphicRef
from pdfacts.graphics.images import ColourSpace, Image
from pdfacts.graphics.paths import (
    Dash,
    DisplayColour,
    FillRule,
    LineCap,
    LineJoin,
    Path,
    Segment,
    SegmentKind,
    ShapeKind,
    Subpath,
)

UNKNOWN = Unknown(UnknownReason.UNREADABLE, "why")
ORIGIN = Point(0, 0)
UNIT = Quad(Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1))
REF = GraphicRef(page_index=0, annotation=None, drawing_index=0)
ANNOTATION = AnnotationRef(
    page_index=0, annotation_index=0, kind="Square", object_number=6, generation=0
)
IN_ANNOTATION = GraphicRef(page_index=0, annotation=ANNOTATION, drawing_index=1)
STROKE = ("stroke_colour", "line_width", "line_cap", "line_join")
NOT_STROKED = dict(stroke=False, stroke_colour=None, line_width=None, line_cap=None, line_join=None)
MOVE = Segment(SegmentKind.MOVE, (ORIGIN,))
LINE = Segment(SegmentKind.LINE, (Point(10, 0),))


def path(**changes):
    values = dict(
        ref=REF,
        subpaths=(Subpath((MOVE, LINE)),),
        fill=None,
        stroke=True,
        fill_colour=None,
        stroke_colour=DisplayColour(0, 0, 0, 255),
        line_width=1.0,
        dash=None,
        line_cap=LineCap.BUTT,
        line_join=LineJoin.MITER,
        annotation_flags=None,
    )
    values.update(changes)
    return Path(**values)


def image(**changes):
    values = dict(
        ref=REF,
        corners=UNIT,
        pixel_width=2,
        pixel_height=2,
        colour_space=ColourSpace.DEVICE_GRAY,
        bits_per_pixel=8,
        annotation_flags=None,
    )
    values.update(changes)
    return Image(**values)


# --- Quad and GraphicRef ------------------------------------------------------------


def test_a_quad_holds_four_points_and_knows_its_bounds():
    quad = Quad(Point(50, 0), Point(100, 50), Point(50, 100), Point(0, 50))
    assert quad.bounds == Rect(0, 0, 100, 100)
    skewed = Quad(Point(0, 0), Point(10, -5), Point(12, 20), Point(-3, 8))
    assert skewed.bounds == Rect(-3, -5, 12, 20)


@pytest.mark.parametrize("position", range(4))
def test_a_quad_takes_points_only(position):
    corners = [Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)]
    corners[position] = (0, 1)
    with pytest.raises(TypeError):
        Quad(*corners)


def test_a_graphic_ref_of_the_page_or_of_an_annotation():
    annotation = AnnotationRef(
        page_index=2, annotation_index=0, kind="Widget", object_number=6, generation=0
    )
    assert GraphicRef(page_index=2, annotation=annotation, drawing_index=7).annotation is annotation
    with pytest.raises(ValueError):
        GraphicRef(page_index=1, annotation=annotation, drawing_index=0)
    with pytest.raises(ValueError):
        GraphicRef(page_index=0, annotation=None, drawing_index=-1)
    with pytest.raises(TypeError):
        GraphicRef(page_index=True, annotation=None, drawing_index=0)
    with pytest.raises(TypeError):
        GraphicRef(page_index=0, annotation="Widget", drawing_index=0)


# --- segments and subpaths ------------------------------------------------------------


def test_a_curve_has_three_points_a_move_and_a_line_one():
    Segment(SegmentKind.CURVE, (ORIGIN, ORIGIN, ORIGIN))
    with pytest.raises(ValueError):
        Segment(SegmentKind.CURVE, (ORIGIN,))
    with pytest.raises(ValueError):
        Segment(SegmentKind.LINE, (ORIGIN, ORIGIN))
    with pytest.raises(TypeError):
        Segment(SegmentKind.LINE, ((0, 0),))
    with pytest.raises(TypeError):
        Segment(SegmentKind.LINE, [ORIGIN])
    with pytest.raises(TypeError):
        Segment("line", (ORIGIN,))
    with pytest.raises(TypeError):
        Segment(SegmentKind.LINE, (ORIGIN,), closes=1)


def test_a_subpath_begins_with_its_only_move():
    with pytest.raises(ValueError):
        Subpath((LINE,))
    with pytest.raises(ValueError):
        Subpath((MOVE, MOVE))
    with pytest.raises(TypeError):
        Subpath(())
    assert Subpath((MOVE, Segment(SegmentKind.LINE, (ORIGIN,), True))).closed
    assert not Subpath((MOVE, LINE)).closed
    with pytest.raises(TypeError):
        Subpath([MOVE, LINE])
    with pytest.raises(TypeError):
        Subpath((MOVE, (10, 0)))


def test_only_the_last_segment_closes_a_subpath():
    closing = Segment(SegmentKind.LINE, (Point(10, 10),), True)
    assert Subpath((MOVE, LINE, closing)).closed
    assert Subpath((Segment(SegmentKind.MOVE, (ORIGIN,), True),)).closed
    with pytest.raises(ValueError):
        Subpath((MOVE, closing, LINE))
    with pytest.raises(ValueError):
        Subpath((Segment(SegmentKind.MOVE, (ORIGIN,), True), LINE))


# --- colours and dashes ---------------------------------------------------------------


@pytest.mark.parametrize("value", [-1, 256])
@pytest.mark.parametrize("channel", range(4))
def test_a_display_colour_is_eight_bits_a_channel(channel, value):
    DisplayColour(0, 128, 255, 255)
    channels = [0, 0, 0, 255]
    channels[channel] = value
    with pytest.raises(ValueError):
        DisplayColour(*channels)


def test_a_display_colour_takes_ints():
    with pytest.raises(TypeError):
        DisplayColour(0.5, 0, 0, 255)
    with pytest.raises(TypeError):
        DisplayColour(True, 0, 0, 255)


def test_a_dash_is_finite_and_its_lengths_are_floats():
    dash = Dash((3, 1.5), 1)
    assert dash.lengths == (3.0, 1.5) and dash.phase == 1.0
    assert type(dash.lengths[0]) is float and type(dash.phase) is float
    with pytest.raises(ValueError):
        Dash((math.inf,), 0)
    with pytest.raises(ValueError):
        Dash((3.0,), math.nan)
    with pytest.raises(TypeError):
        Dash([3.0], 0)


def test_a_dash_has_a_length_none_below_zero_and_not_all_zero():
    assert Dash((0.0, 2.0), -2.0).lengths == (0.0, 2.0)
    with pytest.raises(ValueError):
        Dash((), 0)
    with pytest.raises(ValueError):
        Dash((3.0, -1.0), 0)
    with pytest.raises(ValueError):
        Dash((0.0, 0.0), 0)


# --- Path and Image ---------------------------------------------------------------


def test_a_path_takes_its_values_or_unknown_for_each():
    assert path().stroke is True
    every = {
        name: UNKNOWN
        for name in (
            "subpaths",
            "fill",
            "stroke",
            "fill_colour",
            "stroke_colour",
            "line_width",
            "dash",
            "line_cap",
            "line_join",
            "annotation_flags",
        )
    }
    fact = path(ref=IN_ANNOTATION, **every)
    assert all(getattr(fact, name) is UNKNOWN for name in every)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ref", None),
        ("subpaths", [Subpath((MOVE, LINE))]),
        ("subpaths", (MOVE,)),
        ("fill", "nonzero"),
        ("stroke", 1),
        ("stroke", None),
        ("fill_colour", (0, 0, 0, 255)),
        ("stroke_colour", "black"),
        ("line_width", "1"),
        ("line_width", True),
        ("dash", (3.0,)),
        ("line_cap", 0),
        ("line_join", "miter"),
        ("annotation_flags", 1.0),
    ],
)
def test_a_path_refuses_other_kinds(name, value):
    with pytest.raises(TypeError):
        path(**{name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [("line_width", -1.0), ("line_width", math.nan), ("annotation_flags", 2**32)],
)
def test_a_path_refuses_values_out_of_range(name, value):
    ref = IN_ANNOTATION if name == "annotation_flags" else REF
    with pytest.raises(ValueError):
        path(**{"ref": ref, name: value})


def test_a_path_keeps_a_width_as_a_float():
    assert path(line_width=2).line_width == 2.0
    assert type(path(line_width=2).line_width) is float
    assert path(line_width=0).line_width == 0.0


def test_a_fill_colour_when_filled_then_only():
    filled = path(fill=FillRule.EVEN_ODD, fill_colour=DisplayColour(0, 0, 0, 255))
    assert filled.fill is FillRule.EVEN_ODD
    assert path(fill=UNKNOWN, fill_colour=UNKNOWN).fill_colour is UNKNOWN
    with pytest.raises(ValueError):
        path(fill=None, fill_colour=DisplayColour(0, 0, 0, 255))
    with pytest.raises(ValueError):
        path(fill=FillRule.NONZERO, fill_colour=None)
    with pytest.raises(ValueError):
        path(fill=UNKNOWN, fill_colour=None)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("stroke_colour", DisplayColour(0, 0, 0, 255)),
        ("line_width", 1.0),
        ("line_cap", LineCap.BUTT),
        ("line_join", LineJoin.MITER),
        ("dash", Dash((1.0,), 0)),
    ],
)
def test_a_path_that_is_not_stroked_has_no_values_of_a_stroke(name, value):
    assert path(**NOT_STROKED).line_width is None
    with pytest.raises(ValueError):
        path(**{**NOT_STROKED, name: value})


@pytest.mark.parametrize("stroke", [True, UNKNOWN])
@pytest.mark.parametrize("name", STROKE)
def test_a_stroked_path_has_the_values_of_its_stroke(stroke, name):
    assert path(stroke=stroke, dash=None).dash is None
    with pytest.raises(ValueError):
        path(stroke=stroke, **{name: None})


def test_flags_for_an_appearance_then_only():
    assert path(ref=IN_ANNOTATION, annotation_flags=4).annotation_flags == 4
    assert image(ref=IN_ANNOTATION, annotation_flags=UNKNOWN).annotation_flags is UNKNOWN
    for make in (path, image):
        with pytest.raises(ValueError):
            make(annotation_flags=4)
        with pytest.raises(ValueError):
            make(ref=IN_ANNOTATION, annotation_flags=None)


def test_an_image_takes_its_values_or_unknown_for_each():
    assert image(colour_space=None).colour_space is None
    every = {
        name: UNKNOWN
        for name in (
            "corners",
            "pixel_width",
            "pixel_height",
            "colour_space",
            "bits_per_pixel",
            "annotation_flags",
        )
    }
    fact = image(ref=IN_ANNOTATION, **every)
    assert all(getattr(fact, name) is UNKNOWN for name in every)


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("ref", None, TypeError),
        ("corners", Rect(0, 0, 1, 1), TypeError),
        ("pixel_width", 2.0, TypeError),
        ("pixel_height", -1, ValueError),
        ("pixel_width", 0, ValueError),
        ("pixel_height", 2**31, ValueError),
        ("pixel_width", 2**31, ValueError),
        ("colour_space", 1, TypeError),
        ("colour_space", "DeviceGray", TypeError),
        ("bits_per_pixel", None, TypeError),
        ("bits_per_pixel", 0, ValueError),
        ("annotation_flags", -1, ValueError),
        ("annotation_flags", 1.0, TypeError),
    ],
)
def test_an_image_refuses_other_values(name, value, error):
    ref = IN_ANNOTATION if name == "annotation_flags" else REF
    with pytest.raises(error):
        image(**{"ref": ref, name: value})


def test_no_colour_space_only_for_an_image_pdfium_decodes():
    assert image(colour_space=None, bits_per_pixel=1).colour_space is None
    assert image(colour_space=UNKNOWN, bits_per_pixel=UNKNOWN).bits_per_pixel is UNKNOWN
    with pytest.raises(ValueError):
        image(colour_space=None, bits_per_pixel=UNKNOWN)


def test_unknown_flags_only_for_an_appearance():
    with pytest.raises(ValueError):
        path(annotation_flags=UNKNOWN)
    with pytest.raises(ValueError):
        image(annotation_flags=UNKNOWN)


def test_the_values_of_the_kinds():
    assert [kind.value for kind in SegmentKind] == ["move", "line", "curve"]
    assert [kind.value for kind in ShapeKind] == ["line", "rectangle", "quad", "polyline", "curves"]
    assert [rule.value for rule in FillRule] == ["nonzero", "even-odd"]
    assert [cap.value for cap in LineCap] == ["butt", "round", "square"]
    assert [join.value for join in LineJoin] == ["miter", "round", "bevel"]
    assert [space.value for space in ColourSpace] == [
        "DeviceGray",
        "DeviceRGB",
        "DeviceCMYK",
        "CalGray",
        "CalRGB",
        "Lab",
        "ICCBased",
        "Separation",
        "DeviceN",
        "Indexed",
    ]


def test_an_image_of_the_largest_size():
    largest = image(pixel_width=2**31 - 1, pixel_height=2**31 - 1, bits_per_pixel=1)
    assert (largest.pixel_width, largest.pixel_height) == (2**31 - 1, 2**31 - 1)


def test_facts_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        path().stroke = False


def test_the_types_are_exported():
    import pdfacts

    for name in (
        "ColourSpace",
        "Dash",
        "DisplayColour",
        "FillRule",
        "GraphicRef",
        "Image",
        "LineCap",
        "LineJoin",
        "Path",
        "Quad",
        "Segment",
        "SegmentKind",
        "ShapeKind",
        "Subpath",
    ):
        assert name in pdfacts.__all__
        assert getattr(pdfacts, name) is not None
