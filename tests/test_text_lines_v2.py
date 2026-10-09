"""words() and lines() with TextRules.V2, and the page read once for a rule
set (docs/conventions.md, "Glyphs, words and lines").

Each file is built byte by byte. The expected values come from the
construction: positions from the operators, widths from /Widths or from
Helvetica.afm's WX (A 667, B 667, colon 278, space 278), heights from the
font's ascent and descent (Helvetica.afm: 718 and -207, scaled by 1000/925 to
span the size).
"""

from __future__ import annotations

import dataclasses
import math
import sys
from pathlib import Path

import pytest

import pdfacts
import pdfacts.text.reader
from pdfacts import ClosedError, LimitExceededError, Limits, Rect, TextRules, UnknownReason
from pdfacts.core.document import FeatureAccess

sys.path.insert(0, str(Path(__file__).parent))
from synthetic.builder import build, build_with_object_stream, stream  # noqa: E402

HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
# Tab 400 and space 250 wide; ascent 900, descent -200, which span more than an em.
TABS = (
    b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Tabs /Encoding /WinAnsiEncoding"
    b" /FirstChar 9 /LastChar 32 /Widths [400 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 250]"
    b" /FontDescriptor 9 0 R >>"
)
TABS_DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /ABCDEF+Tabs /Flags 32 /FontBBox [0 -200 1000 900]"
    b" /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>"
)
LOW, HIGH = -207 / 925 * 10, 718 / 925 * 10


def page(
    content: bytes, objects: dict[int, bytes] | None = None, fonts: bytes = b"/F1 5 0 R /F8 8 0 R"
) -> bytes:
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 10 0 R] /Count 2 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R"
            b" /Resources << /Font << " + fonts + b" >> >> >>",
            4: stream(content),
            5: HELVETICA,
            8: TABS,
            9: TABS_DESCRIPTOR,
            10: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 11 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>",
            11: stream(b"BT /F1 10 Tf 0 0 Td (B) Tj ET"),
            **(objects or {}),
        },
        1,
    )


def rounded(box: Rect) -> tuple[float, ...]:
    return tuple(round(v, 6) for v in (box.x0, box.y0, box.x1, box.y1))


def read(data: bytes, rules=TextRules.V2):
    with pdfacts.open(data) as document:
        return pdfacts.words(document, 0, rules=rules), pdfacts.lines(document, 0, rules=rules)


# --- words and lines ----------------------------------------------------------------


def test_a_lines_box_takes_the_white_space_after_its_label():
    words, lines = read(page(b"BT /F1 10 Tf 0 100 Td (A:) Tj /F8 10 Tf (\t \t) Tj ET"))
    assert [w.text for w in words] == ["A:"]
    # The word: A 6.67 and colon 2.78 wide, Helvetica's height.
    assert rounded(words[0].box) == tuple(round(v, 6) for v in (0, 100 + LOW, 9.45, 100 + HIGH))
    # The line: the word, and the tab, space and tab after it to 9.45 + 4 +
    # 2.5 + 4; across, from the word's bottom to the top of the tabs' font,
    # which reaches to 9 above the baseline.
    assert [line.text for line in lines] == ["A:"]
    assert rounded(lines[0].box) == tuple(round(v, 6) for v in (0, 100 + LOW, 19.95, 109.0))


def test_white_space_before_a_lines_first_word_is_in_its_box():
    _, lines = read(page(b"BT /F8 10 Tf 0 100 Td (\t) Tj /F1 10 Tf (A) Tj ET"))
    assert rounded(lines[0].box)[0] == 0.0
    assert rounded(lines[0].box)[2] == round(4.0 + 6.67, 6)


def test_white_space_that_begins_a_later_line_is_in_that_lines_box():
    _, lines = read(
        page(b"BT /F1 10 Tf 0 100 Td (A) Tj /F8 10 Tf 0 -50 Td (\t) Tj /F1 10 Tf (B) Tj ET")
    )
    # The second line: the tab from 0 to 4, then B 6.67 wide; across, from
    # B's bottom to the top of the tabs' font, 9 above the baseline at 50.
    assert rounded(lines[1].box) == tuple(round(v, 6) for v in (0, 50 + LOW, 10.67, 59.0))


def test_a_line_of_white_space_alone_is_none():
    words, lines = read(page(b"BT /F8 10 Tf 0 100 Td (\t \t) Tj ET"))
    assert (words, lines) == ((), ())


def test_white_space_on_another_line_is_not_in_this_ones_box():
    _, lines = read(page(b"BT /F1 10 Tf 0 100 Td (A) Tj /F8 10 Tf 0 -50 Td (\t) Tj ET"))
    assert [rounded(line.box) for line in lines] == [
        tuple(round(v, 6) for v in (0, 100 + LOW, 6.67, 100 + HIGH))
    ]


def test_words_and_lines_join_as_the_first_rule_set_does():
    words, lines = read(page(b"BT /F1 10 Tf 0 100 Td (A B) Tj 0 -20 Td (AB) Tj ET"))
    assert [w.text for w in words] == ["A", "B", "AB"]
    assert [(line.text, line.words) for line in lines] == [("A B", (0, 1)), ("AB", (2,))]


def test_a_words_text_is_the_fonts_without_the_first_rule_sets_marker():
    # A code mapped to U+0002 stays U+0002 in the second rule set.
    to_unicode = stream(b"1 beginbfchar <41> <0002> endbfchar")
    font = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+M /FirstChar 65 /LastChar 66"
        b" /Widths [500 500] /Encoding /WinAnsiEncoding /ToUnicode 13 0 R /FontDescriptor 9 0 R >>"
    )
    words, _ = read(
        page(b"BT /F2 10 Tf 0 0 Td (BA) Tj ET", {12: font, 13: to_unicode}, b"/F2 12 0 R")
    )
    assert [w.text for w in words] == ["B\x02"]


def test_the_first_rule_sets_lines_leave_their_white_space_out():
    # Two spaces after the label, which PDFium reads as spaces too.
    data = page(b"BT /F1 10 Tf 0 100 Td (A:  ) Tj ET")
    with pdfacts.open(data) as document:
        first = pdfacts.lines(document, 0)
        assert first == pdfacts.lines(document, 0, rules=TextRules.V1)
        assert first == pdfacts.lines(document, 0, rules="v1")
        second = pdfacts.lines(document, 0, rules="v2")
    assert rounded(first[0].box)[2] == 9.45
    # Helvetica's space, 278/1000 * 10, twice after the colon.
    assert rounded(second[0].box)[2] == round(9.45 + 2 * 2.78, 6)


@pytest.mark.parametrize("read_with", [pdfacts.words, pdfacts.lines])
def test_a_rule_set_that_is_none(read_with):
    with pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET")) as document:
        with pytest.raises(ValueError):
            read_with(document, 0, rules="v4")


def test_the_facts_are_those_the_constructors_make():
    def rechecked(value):
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            fields = {f.name: rechecked(getattr(value, f.name)) for f in dataclasses.fields(value)}
            return type(value)(**fields)
        if isinstance(value, tuple):
            return tuple(rechecked(item) for item in value)
        return value

    words, lines = read(page(b"BT /F1 10 Tf 0 100 Td (A B:) Tj /F8 10 Tf (\t) Tj ET"))
    for facts in (words, lines):
        assert rechecked(facts) == facts
        assert repr(rechecked(facts)) == repr(facts)


def test_no_word_or_line_takes_the_c_librarys_trigonometry(monkeypatch):
    data = page(b"BT /F1 10 Tf 0.9998 0.0175 -0.0175 0.9998 100 100 Tm (A B) Tj ET")
    before = read(data)

    def fails(*arguments):
        raise AssertionError("the C library's trigonometry was taken")

    for name in ("atan", "atan2", "sin", "cos", "tan"):
        monkeypatch.setattr(math, name, fails)
    assert read(data) == before


# --- one read for a page and a rule set ----------------------------------------------


@pytest.fixture
def counted(monkeypatch):
    """How often each engine's text is read."""
    calls = {"pdfium": 0, "qpdf": 0}
    pdfium, qpdf = FeatureAccess.page_text, FeatureAccess.page_content_text

    def page_text(self, index, cap):
        calls["pdfium"] += 1
        return pdfium(self, index, cap)

    def page_content_text(self, index, cap):
        calls["qpdf"] += 1
        return qpdf(self, index, cap)

    monkeypatch.setattr(FeatureAccess, "page_text", page_text)
    monkeypatch.setattr(FeatureAccess, "page_content_text", page_content_text)
    return calls


@pytest.mark.parametrize(("rules", "engine"), [(TextRules.V1, "pdfium"), (TextRules.V2, "qpdf")])
def test_glyphs_words_and_lines_of_a_page_read_it_once(counted, rules, engine):
    with pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET")) as document:
        for read_with in (pdfacts.glyphs, pdfacts.words, pdfacts.lines, pdfacts.glyphs):
            read_with(document, 0, rules=rules)
        assert counted[engine] == 1


def test_another_page_or_rule_set_is_read_again_and_the_last_is_kept(counted):
    with pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET")) as document:
        first = pdfacts.lines(document, 0, rules=TextRules.V2)
        pdfacts.lines(document, 1, rules=TextRules.V2)
        pdfacts.lines(document, 0, rules=TextRules.V1)
        assert pdfacts.lines(document, 0, rules=TextRules.V2) == first
        assert counted == {"pdfium": 1, "qpdf": 3}


def test_a_closed_document_raises_and_keeps_nothing():
    document = pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET"))
    pdfacts.glyphs(document, 0, rules=TextRules.V2)
    assert document._last_text is not None
    document.close()
    assert document._last_text is None
    with pytest.raises(ClosedError):
        pdfacts.words(document, 0, rules=TextRules.V2)


def test_a_close_while_the_facts_are_made_keeps_nothing(monkeypatch):
    document = pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET"))
    made = pdfacts.text.reader.content_glyphs_of

    def closing(*arguments):
        document.close()
        return made(*arguments)

    monkeypatch.setattr(pdfacts.text.reader, "content_glyphs_of", closing)
    pdfacts.lines(document, 0, rules=TextRules.V2)
    assert document._last_text is None


def test_a_closed_document_gives_nothing_it_kept():
    document = pdfacts.open(page(b"BT /F1 10 Tf (A) Tj ET"))
    pdfacts.glyphs(document, 0, rules=TextRules.V2)
    document.close()
    with pytest.raises(ClosedError):
        pdfacts.words(document, 0, rules=TextRules.V2)


def test_the_deadline_is_checked_whatever_was_kept():
    now = [0.0]
    data = page(b"BT /F1 10 Tf (A) Tj ET")
    with pdfacts.open(data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0]) as document:
        pdfacts.glyphs(document, 0, rules=TextRules.V2)
        now[0] = 10.0
        with pytest.raises(LimitExceededError):
            pdfacts.words(document, 0, rules=TextRules.V2)


def test_the_words_and_lines_of_a_document_whose_qpdf_values_are_unknown():
    data = build_with_object_stream(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 5 0 R"
            b" /Resources << /Font << /F1 6 0 R >> >> /UserUnit 9 0 R >>",
            5: stream(b"BT /F1 10 Tf (A) Tj ET"),
            6: HELVETICA,
        },
        [(9, b"2"), (9, b"3")],
        {9: 0},
        1,
    )
    with pdfacts.open(data) as document:
        for read_with in (pdfacts.words, pdfacts.lines):
            found = read_with(document, 0, rules=TextRules.V2)
            assert found == document._qpdf_unknown
            assert found.reason is UnknownReason.INVALID


@pytest.mark.parametrize("read_with", [pdfacts.words, pdfacts.lines])
def test_the_words_and_lines_count_the_codes_against_the_item_limit(read_with):
    data = page(b"BT /F1 10 Tf (AB) Tj ET")
    with pdfacts.open(data, limits=Limits(max_items_per_page=1)) as document:
        with pytest.raises(LimitExceededError):
            read_with(document, 0, rules=TextRules.V2)
