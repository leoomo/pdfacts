"""Annotation: what a file says about one annotation of a page."""

from __future__ import annotations

from dataclasses import dataclass

from pdfacts.core.geometry import Rect
from pdfacts.core.refs import AnnotationRef
from pdfacts.core.values import Unknown

__all__ = ["Annotation"]


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
