"""The rule sets of glyphs(), words() and lines() (docs/conventions.md,
"Glyphs, words and lines")."""

from __future__ import annotations

from enum import StrEnum


class TextRules(StrEnum):
    # M4a's rules: the characters PDFium's text interface reads.
    V1 = "v1"
    # M4b's rules: the codes the content shows, read on QPDF's tokens.
    V2 = "v2"
