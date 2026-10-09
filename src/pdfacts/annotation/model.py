"""Annotation: what a file says about one annotation of a page."""

from __future__ import annotations

from dataclasses import dataclass

from pdfacts.core.geometry import DeviceColour, Rect
from pdfacts.core.refs import AnnotationRef
from pdfacts.core.values import Unknown

__all__ = ["Annotation", "AnnotationAppearance"]


@dataclass(frozen=True, slots=True)
class AnnotationAppearance:
    """The appearance an annotation's /AP selects, as the file writes it.

    None for the parts the stream does not name; `ref` is None where the
    appearance is written directly into /AP's dictionary.
    """

    # The object the selected appearance stream is written in; None where it
    # is written directly in the /N entry's place.
    object_number: int | None
    generation: int | None
    # /BBox and /Matrix of the appearance stream, as written; None where
    # absent.
    bbox: tuple[float, float, float, float] | None
    matrix: tuple[float, float, float, float, float, float] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Annotation:
    """One annotation of a page, its place and its own dictionary's flags.

    Widget extends it with the form's view of the same thing. `Unknown`
    compares by value, so two unknown values are equal.
    """

    ref: AnnotationRef
    # The rectangle as QPDF reads it, in user space; PDFium gives zeros for a
    # /Rect it cannot use, so it is not asked.
    rect: Rect | Unknown
    # /F as written, an int from 0 to 2^32 - 1.
    annotation_flags: int | Unknown
    # /Contents, the text a markup annotation carries, decoded as "Text
    # strings and names" decodes; None where absent or not a string, Unknown
    # where QPDF cannot read it. Widgets read their label from /TU instead.
    contents: str | Unknown | None = None
    # /C, the colour to draw the annotation in: 0, 1, 3 or 4 components,
    # each from 0 to 1. None where absent or of another shape; PDFium's
    # colour interface does not answer it (measured), so it is as written.
    colour: DeviceColour | Unknown | None = None
    # The appearance the /AP of the annotation selects, as the forms reader
    # selects a widget's; None where /AP is absent, or where /AS names a
    # state /N does not hold, or where QPDF cannot read it.
    appearance: AnnotationAppearance | Unknown | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, AnnotationRef):
            raise TypeError(f"ref must be an AnnotationRef, not {type(self.ref).__name__}")
        if not (isinstance(self.rect, Rect) or isinstance(self.rect, Unknown)):
            raise TypeError(f"rect must be a Rect or Unknown, not {type(self.rect).__name__}")
        if not isinstance(self.annotation_flags, Unknown):
            if type(self.annotation_flags) is not int:
                raise TypeError(
                    f"annotation_flags must be an int, not {type(self.annotation_flags).__name__}"
                )
            if not 0 <= self.annotation_flags <= 2**32 - 1:
                raise ValueError(
                    f"annotation_flags is a 32-bit flag field, not {self.annotation_flags}"
                )
