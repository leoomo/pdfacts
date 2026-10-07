"""glyphs(), words() and lines(): a page's text.

The rules are those of docs/conventions.md, "Glyphs, words and lines". With
TextRules.V1: the characters PDFium reads from the page's content, in content
order, made into glyphs, words and lines by pdfacts.text.glyphs and
pdfacts.text.layout. With TextRules.V2 (glyphs() only, for now): the codes
the content shows, read on QPDF's tokens.
"""

from __future__ import annotations

from dataclasses import replace

from pdfacts.core.document import Document
from pdfacts.core.errors import LimitExceededError
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.text.glyphs import Glyph, content_glyphs_of, glyphs_of
from pdfacts.text.layout import Line, Word, words_and_lines
from pdfacts.text.rules import TextRules

__all__ = ["glyphs", "lines", "words"]

_TOO_DEEP = "a form lies deeper than PDFium reads forms: its text is not on the text page"


def glyphs(
    document: Document, page_index: int, rules: TextRules = TextRules.V1
) -> tuple[Glyph, ...] | Unknown:
    """The page's glyphs in content order, by the rule set `rules`; or why
    they are unknown."""
    if TextRules(rules) is TextRules.V2:
        return _read_content(document, page_index)
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


_HYPHENS = ("-", "\u00ad")


def _read_content(document: Document, page_index: int) -> tuple[Glyph, ...] | Unknown:
    """The glyphs of TextRules.V2. A glyph is a hyphen at the end of its line
    where its text is a hyphen or a soft hyphen and no glyph of its line that
    is not white space comes after it, the lines being those of
    words_and_lines."""
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    page = document.pages[page_index]  # the index checks of Document._page
    rotation = page.geometry.rotation
    with document._access() as access:
        if access.qpdf_unknown is not None:
            return access.qpdf_unknown
        cap = access.max_items_per_page
        text = access.page_content_text(page_index, cap)
    if text.stopped:
        if cap is None:  # raised, not asserted: python -O removes an assert
            raise AssertionError("the backend stops only at a cap")
        raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
    if text.unread is not None:
        reason, detail = text.unread
        return Unknown(UnknownReason(reason), detail)
    found = list(content_glyphs_of(page_index, rotation, text.glyphs))
    made_words, made_lines = words_and_lines(page_index, found)
    for line in made_lines:
        index = made_words[line.words[-1]].glyphs[-1]
        if found[index].text in _HYPHENS:
            found[index] = replace(found[index], hyphen=True)
    return tuple(found)
