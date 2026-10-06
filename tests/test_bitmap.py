"""Bitmap and Colour: the plain data Page.render returns."""

from __future__ import annotations

import dataclasses

import pytest

from pdfacts import Bitmap, Colour, Matrix, Rect

FIELDS = {
    "width": 2,
    "height": 3,
    "row_length": 2,
    "channels": 1,
    "colour": Colour.GREY,
    "data": bytes(6),
    "scale": 1.0,
    "display_region": Rect(0, 0, 2, 3),
    "display_to_pixel": Matrix(1, 0, 0, -1, 0, 3),
}


def test_a_bitmap_holds_what_it_was_given():
    bitmap = Bitmap(**FIELDS)
    assert {field.name: getattr(bitmap, field.name) for field in dataclasses.fields(bitmap)} == (
        FIELDS
    )


def test_it_is_given_by_name_and_cannot_be_changed():
    with pytest.raises(TypeError):
        Bitmap(*FIELDS.values())  # type: ignore[misc]
    bitmap = Bitmap(**FIELDS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bitmap.width = 3  # type: ignore[misc]


def test_rgb_has_three_channels_and_rows_may_be_longer_than_the_pixels():
    bitmap = Bitmap(**FIELDS | {"colour": "rgb", "channels": 3, "row_length": 8, "data": bytes(24)})
    assert bitmap.colour is Colour.RGB


def test_the_bytes_are_not_part_of_the_repr():
    assert "data" not in repr(Bitmap(**FIELDS))


def test_the_scale_is_a_float():
    assert type(Bitmap(**FIELDS | {"scale": 2}).scale) is float


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"width": 0}, ValueError),
        ({"height": True}, TypeError),
        ({"row_length": 1, "data": bytes(3)}, ValueError),
        ({"channels": 3, "row_length": 6, "data": bytes(18)}, ValueError),
        ({"colour": "cmyk"}, ValueError),
        ({"data": bytes(5)}, ValueError),
        ({"data": bytearray(6)}, TypeError),
        ({"scale": 0}, ValueError),
        ({"scale": float("nan")}, ValueError),
        ({"scale": "1"}, TypeError),
        ({"display_region": (0, 0, 2, 3)}, TypeError),
        ({"display_to_pixel": (1, 0, 0, -1, 0, 3)}, TypeError),
    ],
    ids=repr,
)
def test_what_is_not_a_bitmap_is_refused(fields, error):
    with pytest.raises(error):
        Bitmap(**FIELDS | fields)
