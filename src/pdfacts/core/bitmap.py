"""A rendered page or region: raw bytes and where they lie in display space.

No type of an imaging library appears here (docs/conventions.md,
"Rendering"); a caller who wants an image file converts the bytes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from pdfacts.core.geometry import Matrix, Rect


class Colour(StrEnum):
    # One byte per pixel.
    GREY = "grey"
    # Three bytes per pixel: red, green, blue.
    RGB = "rgb"


CHANNELS = {Colour.GREY: 1, Colour.RGB: 3}


def checked_scale(scale: object) -> float:
    """Pixels per user space unit as a float: finite and greater than 0."""
    if not isinstance(scale, int | float) or isinstance(scale, bool):
        raise TypeError(f"scale must be an int or a float, not {type(scale).__name__}")
    try:
        number = float(scale)
    except OverflowError:
        raise ValueError("scale is too large for a float") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"scale must be finite and greater than 0, not {scale}")
    return number


@dataclass(frozen=True, slots=True, kw_only=True)
class Bitmap:
    """The pixels of a page or a region of it.

    Pixel coordinates: x is the column, y the row, both counted from the top
    left corner. display_to_pixel maps display space onto them. The bytes are
    a copy; the bitmap holds no memory of the engine.
    """

    width: int
    height: int
    # Bytes per row; may be more than width * channels.
    row_length: int
    channels: int
    colour: Colour
    # row_length * height bytes; row 0 is the top.
    data: bytes = field(repr=False)
    # Pixels per user space unit, as asked for.
    scale: float
    # The rendered region in display space, cut to the page.
    region: Rect
    display_to_pixel: Matrix

    def __post_init__(self) -> None:
        for name in ("width", "height", "row_length", "channels"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an int, not {type(value).__name__}")
            if value < 1:
                raise ValueError(f"{name} must be at least 1, not {value}")
        colour = Colour(self.colour)
        object.__setattr__(self, "colour", colour)
        if self.channels != CHANNELS[colour]:
            raise ValueError(f"{colour} has {CHANNELS[colour]} channels, not {self.channels}")
        if self.row_length < self.width * self.channels:
            raise ValueError(
                f"a row of {self.width} pixels needs {self.width * self.channels} bytes, "
                f"not {self.row_length}"
            )
        if type(self.data) is not bytes:
            raise TypeError(f"data must be bytes, not {type(self.data).__name__}")
        if len(self.data) != self.row_length * self.height:
            raise ValueError(
                f"data must be {self.row_length * self.height} bytes, not {len(self.data)}"
            )
        object.__setattr__(self, "scale", checked_scale(self.scale))
        if not isinstance(self.region, Rect):
            raise TypeError(f"region must be a Rect, not {type(self.region).__name__}")
        if not isinstance(self.display_to_pixel, Matrix):
            raise TypeError(
                f"display_to_pixel must be a Matrix, not {type(self.display_to_pixel).__name__}"
            )
