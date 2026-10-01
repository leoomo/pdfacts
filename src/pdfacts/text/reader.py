"""glyphs(), words() and lines(): a page's text, read through PDFium.

The rules are those of docs/conventions.md, "Glyphs, words and lines": the
characters PDFium reads from the page's content, in content order, made into
glyphs, words and lines by pdfacts.text.glyphs and pdfacts.text.layout.
"""

from __future__ import annotations

from pdfacts.core.document import Document
from pdfacts.core.errors import LimitExceededError
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.text.glyphs import Glyph, glyphs_of
from pdfacts.text.layout import Line, Word, words_and_lines

__all__ = ["glyphs", "lines", "words"]

_TOO_DEEP = "a form lies deeper than PDFium reads forms: its text is not on the text page"


def glyphs(document: Document, page_index: int) -> tuple[Glyph, ...] | Unknown:
    """The page's glyphs in content order; or why they are unknown."""
    read = _read(document, page_index)
    return read if isinstance(read, Unknown) else read[0]


def words(document: Document, page_index: int) -> tuple[Word, ...] | Unknown:
    """The page's words in content order; or why they are unknown."""
    read = _read(document, page_index)
    return read if isinstance(read, Unknown) else read[1]


def lines(document: Document, page_index: int) -> tuple[Line, ...] | Unknown:
    """The page's lines in content order; or why they are unknown."""
    read = _read(document, page_index)
    return read if isinstance(read, Unknown) else read[2]


def _read(
    document: Document, page_index: int
) -> tuple[tuple[Glyph, ...], tuple[Word, ...], tuple[Line, ...]] | Unknown:
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    page = document.pages[page_index]  # the index checks of Document._page
    rotation = page.geometry.rotation
    with document._access() as access:
        cap = access.max_items_per_page
        text = access.page_text(page_index, cap)
    if text.stopped:
        if cap is None:  # raised, not asserted: python -O removes an assert
            raise AssertionError("the backend stops only at a cap")
        raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
    if text.too_deep:
        return Unknown(UnknownReason.UNREADABLE, _TOO_DEEP)
    found = glyphs_of(page_index, rotation, text.characters)
    made_words, made_lines = words_and_lines(page_index, found)
    return found, made_words, made_lines
