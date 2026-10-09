"""glyphs(), words() and lines(): a page's text.

The rules are those of docs/conventions.md, "Glyphs, words and lines". With
TextRules.V1: the characters PDFium reads from the page's content, in content
order, made into glyphs, words and lines by pdfacts.text.glyphs and
pdfacts.text.layout. From TextRules.V2 on: the codes the content shows, read
on QPDF's tokens, made into glyphs, words and lines likewise; with
TextRules.V3 the rotated glyphs take part in the words and lines.

Called one after another, they read a page once for one rule set: the last
page read is kept with its glyphs, words and lines until another is read or
the document is closed.
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
_HYPHENS = ("-", "\u00ad")

_Read = tuple[tuple[Glyph, ...], tuple[Word, ...], tuple[Line, ...]] | Unknown


def glyphs(
    document: Document, page_index: int, rules: TextRules = TextRules.V1
) -> tuple[Glyph, ...] | Unknown:
    """The page's glyphs in content order, by the rule set `rules`; or why
    they are unknown."""
    read = _read(document, page_index, rules)
    return read if isinstance(read, Unknown) else read[0]


def words(
    document: Document, page_index: int, rules: TextRules = TextRules.V1
) -> tuple[Word, ...] | Unknown:
    """The page's words in content order, by the rule set `rules`; or why
    they are unknown."""
    read = _read(document, page_index, rules)
    return read if isinstance(read, Unknown) else read[1]


def lines(
    document: Document, page_index: int, rules: TextRules = TextRules.V1
) -> tuple[Line, ...] | Unknown:
    """The page's lines in content order, by the rule set `rules`; or why
    they are unknown."""
    read = _read(document, page_index, rules)
    return read if isinstance(read, Unknown) else read[2]


def _read(document: Document, page_index: int, rules: TextRules) -> _Read:
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    rules = TextRules(rules)
    page = document.pages[page_index]  # the index checks of Document._page
    rotation = page.geometry.rotation
    key = (page_index, rules)
    with document._access() as access:
        last = document._last_text
        if last is not None and last[0] == key:
            return last[1]  # type: ignore[return-value]
        cap = access.max_items_per_page
        if rules is not TextRules.V1:
            unknown = access.qpdf_unknown
            content = access.page_content_text(page_index, cap) if unknown is None else None
        else:
            characters = access.page_text(page_index, cap)
    if rules is not TextRules.V1:
        read = (
            unknown if content is None else _from_content(page_index, rotation, content, cap, rules)
        )
    else:
        read = _from_pdfium(page_index, rotation, characters, cap)
    document._last_text = (key, read)
    # A close() while the facts were made has found nothing to release.
    if document.closed:
        document._last_text = None
    return read


def _stopped(cap: int | None) -> LimitExceededError:
    if cap is None:  # raised, not asserted: python -O removes an assert
        raise AssertionError("the backend stops only at a cap")
    return LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)


def _from_pdfium(page_index: int, rotation: int, text, cap: int | None) -> _Read:
    """TextRules.V1: PDFium's characters."""
    if text.stopped:
        raise _stopped(cap)
    if text.too_deep:
        return Unknown(UnknownReason.UNREADABLE, _TOO_DEEP)
    found = glyphs_of(page_index, rotation, text.characters)
    made_words, made_lines = words_and_lines(page_index, found)
    return found, made_words, made_lines


def _from_content(page_index: int, rotation: int, text, cap: int | None, rules: TextRules) -> _Read:
    """TextRules.V2 and V3: the codes of the content. A glyph is a hyphen at
    the end of its line where its text is a hyphen or a soft hyphen and no
    glyph of its line that is not white space comes after it."""
    if text.stopped:
        raise _stopped(cap)
    if text.unread is not None:
        reason, detail = text.unread
        return Unknown(UnknownReason(reason), detail)
    found = list(content_glyphs_of(page_index, rotation, text.glyphs))
    made_words, made_lines = words_and_lines(page_index, found, rules)
    for line in made_lines:
        index = made_words[line.words[-1]].glyphs[-1]
        if found[index].text in _HYPHENS:
            found[index] = replace(found[index], hyphen=True)
    return tuple(found), made_words, made_lines
