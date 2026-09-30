"""Image: the images a page places."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pdfacts.core.geometry import Quad
from pdfacts.core.refs import GraphicRef, _is_int
from pdfacts.core.values import Unknown
from pdfacts.graphics.paths import _check, _check_flags

__all__ = ["ColourSpace", "Image"]


class ColourSpace(StrEnum):
    """The families of colour space PDFium reports for an image.

    Pattern is not among them: an image's colour space is never a pattern
    (ISO 32000-1, 8.9.5.1, Table 89).
    """

    DEVICE_GRAY = "DeviceGray"
    DEVICE_RGB = "DeviceRGB"
    DEVICE_CMYK = "DeviceCMYK"
    CAL_GRAY = "CalGray"
    CAL_RGB = "CalRGB"
    LAB = "Lab"
    ICC_BASED = "ICCBased"
    SEPARATION = "Separation"
    DEVICE_N = "DeviceN"
    INDEXED = "Indexed"


@dataclass(frozen=True, slots=True, kw_only=True)
class Image:
    """An image placed on the page: where, and what PDFium reports of it.

    `corners` are the unit square of the image space in user space, in the
    order (0, 0), (1, 0), (1, 1), (0, 1); the image's first row begins at
    (0, 1) (ISO 32000-1, 8.9.4). The pixel size is from 1 to 2^31 - 1 either
    way. `colour_space` is None when PDFium decodes the image and reports no
    colour space: an image mask, or an image without /ColorSpace; so it is
    None only where `bits_per_pixel` is known. `bits_per_pixel` is as PDFium
    decodes the image, not as the file writes it.
    """

    ref: GraphicRef
    corners: Quad | Unknown
    pixel_width: int | Unknown
    pixel_height: int | Unknown
    colour_space: ColourSpace | None | Unknown
    bits_per_pixel: int | Unknown
    annotation_flags: int | None | Unknown

    def __post_init__(self) -> None:
        if not isinstance(self.ref, GraphicRef):
            raise TypeError(f"ref must be a GraphicRef, not {type(self.ref).__name__}")
        _check(self.corners, (Quad,), "corners", none=False)
        for name, most in (
            ("pixel_width", 2**31 - 1),
            ("pixel_height", 2**31 - 1),
            ("bits_per_pixel", None),
        ):
            value = getattr(self, name)
            if isinstance(value, Unknown):
                continue
            if not _is_int(value):
                raise TypeError(f"{name} must be an int or Unknown, not {type(value).__name__}")
            if value < 1 or (most is not None and value > most):
                limit = "" if most is None else " to 2^31 - 1"
                raise ValueError(f"{name} is from 1{limit}, not {value}")
        _check(self.colour_space, (ColourSpace,), "colour_space", none=True)
        _check_flags(self.annotation_flags, self.ref)
        if self.colour_space is None and isinstance(self.bits_per_pixel, Unknown):
            raise ValueError("colour_space is None for an image PDFium decodes, then only")
