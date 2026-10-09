"""The annotations of a page other than widgets, their own dictionary's values.

The rules are those of docs/conventions.md, "Annotations" (M5b). The
dictionary is read through QPDF; the colour is as written, for PDFium's
colour interface does not answer /C (measured, 2026-10-09).
"""

from __future__ import annotations

from pdfacts.annotation.model import Annotation, AnnotationAppearance
from pdfacts.backends.protocols import SelectedAppearance, Written
from pdfacts.core.document import Document
from pdfacts.core.errors import LimitExceededError
from pdfacts.core.geometry import DeviceColour
from pdfacts.core.refs import AnnotationRef
from pdfacts.core.strings import decode_text_string
from pdfacts.core.values import Unknown, UnknownReason

__all__ = ["annotations"]

_INVALID = UnknownReason.INVALID
_UNREADABLE = UnknownReason.UNREADABLE
_DISAGREE = UnknownReason.ENGINES_DISAGREE


def annotations(document: Document, page_index: int) -> tuple[Annotation, ...] | Unknown:
    """The annotations of the page other than widgets, in /Annots order.

    Each with its place and object, its rectangle and flags, and its
    /Contents, its /C colour and the appearance its /AP selects, as the file
    writes them. Or why they are unknown.
    """
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    page = document.pages[page_index]  # the index checks of Document._page
    with document._access() as access:
        if access.qpdf_unknown is not None:
            return access.qpdf_unknown
        if isinstance(page.ref.object_number, Unknown):
            return page.ref.object_number
        count = access.annotation_count(page_index)
        if count is None:
            return Unknown(_INVALID, "/Annots is not an array")
        theirs = access.pdfium_annotation_count(page_index)
        if access.max_items_per_page is not None and max(count, theirs) > access.max_items_per_page:
            raise LimitExceededError(
                limit="max_items_per_page",
                allowed=access.max_items_per_page,
                found=max(count, theirs),
            )
        if count != theirs:
            return Unknown(
                _DISAGREE, f"QPDF finds {count} annotations on the page, PDFium {theirs}"
            )
        found = access.annotations_other(page_index)
    return tuple(_annotation(entry, page_index) for entry in found)


def _annotation(entry, page_index: int) -> Annotation:
    """One annotation's facts from QPDF's plain data, unchecked."""
    values = entry.entries
    appearance = _appearance(entry.appearance)
    return Annotation(
        ref=AnnotationRef(
            page_index=page_index,
            annotation_index=entry.annotation.index,
            kind=entry.annotation.subtype.decode("ascii", "replace"),
            object_number=entry.annotation.object_number,
            generation=entry.annotation.generation,
        ),
        rect=_rect(values.get(b"Rect")),
        annotation_flags=_flags(values.get(b"F")),
        contents=_contents(values.get(b"Contents")),
        colour=_colour(values.get(b"C")),
        appearance=appearance,
    )


def _rect(written: Written | None):
    """The rectangle as the widgets reader reads it."""
    if written is None:
        return Unknown(_INVALID, "/Rect is absent")
    if written.kind != "array" or len(written.data) != 4:
        return Unknown(_INVALID, "/Rect is not an array of four numbers")
    numbers = []
    for item in written.data:
        if item.kind not in ("integer", "real"):
            return Unknown(_INVALID, "/Rect is not an array of four numbers")
        numbers.append(float(item.data))
    from pdfacts.core.geometry import Rect

    return Rect(numbers[0], numbers[1], numbers[2], numbers[3])


def _flags(written: Written | None):
    if written is None:
        return Unknown(_INVALID, "/F is absent")
    if written.kind != "integer":
        return Unknown(_INVALID, "/F is not an integer")
    return written.data


def _contents(written: Written | None) -> str | Unknown | None:
    if written is None:
        return None
    if written.kind != "string":
        return Unknown(_INVALID, "/Contents is not a string")
    return decode_text_string(written.data)


def _colour(written: Written | None) -> DeviceColour | Unknown | None:
    if written is None:
        return None
    if written.kind != "array":
        return Unknown(_INVALID, "/C is not an array")
    numbers = []
    for item in written.data:
        if item.kind not in ("integer", "real"):
            return Unknown(_INVALID, "/C is not an array of numbers")
        numbers.append(float(item.data))
    if len(numbers) not in (0, 1, 3, 4):
        return Unknown(_INVALID, "/C has not 0, 1, 3 or 4 components")
    for number in numbers:
        if not 0 <= number <= 1:
            return Unknown(_INVALID, "a component of /C is not between 0 and 1")
    return DeviceColour(tuple(numbers))


def _appearance(selected: SelectedAppearance | str | None) -> AnnotationAppearance | Unknown | None:
    if selected is None or isinstance(selected, str):
        return None if selected is None else Unknown(_UNREADABLE, selected)
    bbox = selected.bbox
    matrix = selected.matrix
    return AnnotationAppearance(
        object_number=selected.object_number,
        generation=selected.generation,
        bbox=None
        if bbox is None or bbox.kind != "array"
        else tuple(float(i.data) for i in bbox.data),
        matrix=None
        if matrix is None or matrix.kind != "array"
        else tuple(float(i.data) for i in matrix.data),
    )
