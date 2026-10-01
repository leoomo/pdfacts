"""glyphs(), words() and lines(): a page's text through the public interface.

The rules are those of docs/conventions.md, "Glyphs, words and lines". Each
file is built here with the byte-level builder; its font has metrics of its
own, which PDFium takes from the file: every character 1000 thousandths wide,
ascent 800, descent -200. The values expected follow from these numbers and
the content (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import (
    Capability,
    ClosedError,
    LimitExceededError,
    Limits,
    Point,
    Quad,
    Rect,
    Unknown,
    UnknownReason,
    capabilities,
    glyphs,
    lines,
    words,
)
from synthetic.builder import build, stream

WIDTHS = b"[" + b" ".join([b"1000"] * 95) + b"]"
FONT = (
    b"<< /Type /Font /Subtype /TrueType /BaseFont /Gate /FirstChar 32 /LastChar 126"
    b" /Widths " + WIDTHS + b" /Encoding /WinAnsiEncoding /FontDescriptor 6 0 R >>"
)
DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /Gate /Flags 32 /FontBBox [0 -200 1000 800]"
    b" /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>"
)
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"


def page_file(content: bytes, *, page: bytes = b"", resources: bytes = b"", objects=None):
    """A one-page file of 400 x 300 whose font /F1 is object 5."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /Resources << /Font << /F1 5 0 R >> " + resources + b" >> " + page + b" >>",
            5: FONT,
            6: DESCRIPTOR,
            **(objects or {}),
        },
        1,
    )


def read(data: bytes, limits: Limits | None = None):
    with pdfacts.open(data, limits=limits) as document:
        return glyphs(document, 0), words(document, 0), lines(document, 0)


def texts(items) -> list:
    return [item.text for item in items]


def box(x: float, y: float = 200.0, size: float = 10.0) -> Quad:
    """The advance box of a glyph 1000 wide at (x, y)."""
    return Quad(
        Point(x, y - 0.2 * size),
        Point(x + size, y - 0.2 * size),
        Point(x + size, y + 0.8 * size),
        Point(x, y + 0.8 * size),
    )


def test_a_line_of_two_words():
    found, made, joined = read(page_file(b"BT /F1 10 Tf 100 200 Td (AB CD) Tj ET"))
    assert texts(found) == ["A", "B", " ", "C", "D"]
    assert [g.box for g in found] == [box(100), box(110), box(120), box(130), box(140)]
    assert [g.ref.glyph_index for g in found] == [0, 1, 2, 3, 4]
    assert all(g.ref.page_index == 0 and g.upright and g.size == 10.0 for g in found)
    assert (texts(made), [w.glyphs for w in made]) == (["AB", "CD"], [(0, 1), (3, 4)])
    assert [w.box for w in made] == [Rect(100, 198, 120, 208), Rect(130, 198, 150, 208)]
    assert (texts(joined), joined[0].words, joined[0].box) == (
        ["AB CD"],
        (0, 1),
        Rect(100, 198, 150, 208),
    )


def test_the_size_and_the_box_through_the_content_matrix():
    (glyph,), _, _ = read(page_file(b"2 0 0 2 0 0 cm BT /F1 10 Tf 50 100 Td (A) Tj ET"))
    assert (glyph.origin, glyph.size, glyph.box) == (Point(100, 200), 20.0, box(100, 200, 20))


def test_two_lines():
    _, made, joined = read(page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj 0 -20 Td (CD) Tj ET"))
    assert (texts(made), texts(joined)) == (["AB", "CD"], ["AB", "CD"])


def test_a_hyphen_at_a_lines_end():
    found, made, _ = read(page_file(b"BT /F1 10 Tf 100 200 Td (AB-) Tj 0 -20 Td (CD) Tj ET"))
    assert (found[2].text, found[2].hyphen) == ("\x02", True)
    assert texts(made) == ["AB-", "CD"]


@pytest.mark.parametrize("mode", [3, 7])
def test_invisible_text_is_given_with_its_render_mode(mode):
    found, made, _ = read(page_file(b"BT %d Tr /F1 10 Tf 100 200 Td (AB) Tj ET" % mode))
    assert [g.render_mode for g in found] == [mode, mode]
    assert texts(made) == ["AB"]


def test_text_drawn_again_at_its_place_is_given_once():
    """PDFium leaves the second drawing out of its text page (measured, issue T3)."""
    once = b"BT /F1 10 Tf 100 200 Td (AB) Tj ET"
    found, made, _ = read(page_file(once + b" " + once))
    assert (texts(found), texts(made)) == (["A", "B"], ["AB"])


def test_the_first_drawing_keeps_its_render_mode():
    """Invisible, then visible at the same place: the text comes out invisible."""
    data = page_file(
        b"BT 3 Tr /F1 10 Tf 100 200 Td (AB) Tj ET BT 0 Tr /F1 10 Tf 100 200 Td (AB) Tj ET"
    )
    found, _, _ = read(data)
    assert [g.render_mode for g in found] == [3, 3]


def test_upright_as_the_page_is_shown():
    """/Rotate 90: text up the page in user space reads left to right; text
    along x reads down the page and makes no word."""
    data = page_file(
        b"BT /F1 10 Tf 0 1 -1 0 100 100 Tm (AB) Tj ET BT /F1 10 Tf 200 50 Td (CD) Tj ET",
        page=b"/Rotate 90",
    )
    found, made, joined = read(data)
    assert [g.upright for g in found] == [True, True, False, False]
    assert (texts(made), texts(joined)) == (["AB"], ["AB"])


def test_upright_on_a_page_turned_by_270():
    """/Rotate 270: text down the page in user space reads left to right."""
    data = page_file(b"BT /F1 10 Tf 0 -1 1 0 100 200 Tm (AB) Tj ET", page=b"/Rotate 270")
    found, made, _ = read(data)
    assert [g.upright for g in found] == [True, True]
    assert texts(made) == ["AB"]


def test_the_second_page():
    """Page 1 is turned by 90; its text runs up the page and reads left to right."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents %d 0 R"
    page += b" /Resources << /Font << /F1 5 0 R >> >> %s>>"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R 8 0 R] /Count 2 >>",
            3: stream(b"BT /F1 10 Tf 100 200 Td (A) Tj ET"),
            4: page % (3, b""),
            5: FONT,
            6: DESCRIPTOR,
            7: stream(b"BT /F1 10 Tf 0 1 -1 0 100 100 Tm (BC) Tj ET"),
            8: page % (7, b"/Rotate 90 "),
        },
        1,
    )
    with pdfacts.open(data) as document:
        found, made, joined = glyphs(document, 1), words(document, 1), lines(document, 1)
    assert texts(found) == ["B", "C"]
    assert [g.ref.page_index for g in found] == [1, 1]
    assert [(w.page_index, w.text) for w in made] == [(1, "BC")]
    assert [(line.page_index, line.text) for line in joined] == [(1, "BC")]


def test_text_in_an_annotation_appearance_alone_is_none():
    """An appearance is not on PDFium's text page (#69)."""
    data = page_file(
        b"",
        page=b"/Annots [7 0 R]",
        objects={
            7: b"<< /Type /Annot /Subtype /Square /Rect [10 10 110 30] /AP << /N 8 0 R >> >>",
            8: stream(
                b"BT /F1 10 Tf 0 0 Td (AB) Tj ET",
                b"/Type /XObject /Subtype /Form /BBox [0 0 100 20]"
                b" /Resources << /Font << /F1 5 0 R >> >>",
            ),
        },
    )
    assert read(data) == ((), (), ())


def test_text_in_a_form_comes_where_the_form_is_drawn():
    data = page_file(
        b"BT /F1 10 Tf 100 250 Td (A) Tj ET /X Do BT /F1 10 Tf 100 50 Td (C) Tj ET",
        resources=b"/XObject << /X 7 0 R >>",
        objects={
            7: stream(
                b"BT /F1 10 Tf 100 150 Td (B) Tj ET",
                FORM + b" /Resources << /Font << /F1 5 0 R >> >>",
            )
        },
    )
    found, _, joined = read(data)
    assert (texts(found), texts(joined)) == (["A", "B", "C"], ["A", "B", "C"])


def chain(depth: int) -> dict[int, bytes]:
    """Forms 100 to 100 + depth - 1, each drawing the next; the last draws text."""
    objects = {}
    for level in range(depth):
        last = level == depth - 1
        content = b"BT /F1 10 Tf 100 200 Td (A) Tj ET" if last else b"/X Do"
        held = b"" if last else b" /XObject << /X %d 0 R >>" % (101 + level)
        objects[100 + level] = stream(
            content, FORM + b" /Resources << /Font << /F1 5 0 R >>" + held + b" >>"
        )
    return objects


def test_a_form_deeper_than_pdfium_reads_leaves_the_text_unknown():
    data = page_file(
        b"BT /F1 10 Tf 10 10 Td (Z) Tj ET /X Do",
        resources=b"/XObject << /X 100 0 R >>",
        objects=chain(41),
    )
    for value in read(data):
        assert isinstance(value, Unknown) and value.reason is UnknownReason.UNREADABLE


def test_text_forty_forms_deep_is_read():
    data = page_file(b"/X Do", resources=b"/XObject << /X 100 0 R >>", objects=chain(40))
    assert texts(read(data)[1]) == ["A"]


def test_the_characters_count_against_the_item_limit():
    data = page_file(b"BT /F1 10 Tf 100 200 Td (AB CD) Tj ET")
    assert len(read(data, Limits(max_items_per_page=5))[0]) == 5
    with pytest.raises(LimitExceededError) as raised:
        read(data, Limits(max_items_per_page=4))
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_items_per_page",
        4,
        5,
    )


def test_the_forms_are_counted_before_pdfium_loads_the_page():
    """The page draws its form twice, the second time 50 lower: two streams for
    PDFium to parse."""
    data = page_file(
        b"/X Do 1 0 0 1 0 -50 cm /X Do",
        resources=b"/XObject << /X 7 0 R >>",
        objects={7: stream(b"BT /F1 10 Tf 100 200 Td (A) Tj ET", FORM)},
    )
    with pytest.raises(LimitExceededError) as raised:
        read(data, Limits(max_forms_per_page=1))
    assert (raised.value.limit, raised.value.found) == ("max_forms_per_page", 2)
    with pdfacts.open(data, limits=Limits(max_forms_per_page=1)) as document:
        with document._access() as access:
            with pytest.raises(LimitExceededError):
                access.page_text(0, None)
    assert texts(read(data, Limits(max_forms_per_page=2))[0]) == ["A", "A"]


def test_the_text_needs_no_qpdf():
    """Junk before the header: PDFium opens the file, QPDF does not (M1)."""
    data = b"x" * 1024 + page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj ET")
    with pdfacts.open(data) as document:
        assert isinstance(document.pages[0].ref.object_number, Unknown)
        assert texts(words(document, 0)) == ["AB"]


def test_a_page_without_text():
    assert read(page_file(b"0 0 m 10 0 l S")) == ((), (), ())


def test_a_document_of_pdfacts_and_a_page_it_has():
    with pdfacts.open(page_file(b"")) as document:
        with pytest.raises(IndexError):
            glyphs(document, 1)
        with pytest.raises(IndexError):
            words(document, -1)
    with pytest.raises(ClosedError):
        lines(document, 0)
    with pytest.raises(TypeError):
        glyphs(b"%PDF-1.7", 0)


def test_the_entry_points_and_the_capability():
    assert {"glyphs", "words", "lines"} <= set(pdfacts.__all__)
    assert capabilities()[Capability.TEXT] is True
