"""The PDFium backend: the characters of a page's content, as plain data.

Each page is built here with the byte-level builder. The font has metrics of
its own, which PDFium takes from the file: every character 1000 thousandths
wide, ascent 800, descent -200. The values expected are those the content
writes, and what PDFium was measured to do where the test says so (issue T1).
"""

from __future__ import annotations

import dataclasses
import typing

import pytest

from pdfacts.backends.pdfium import PdfiumDocument
from pdfacts.backends.protocols import PageText, TextCharacter, TextReader
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
RESOURCES = b"/Font << /F1 5 0 R >>"


def page_file(content: bytes, *, resources: bytes = b"", objects=None) -> bytes:
    """A one-page file whose font /F1 is object 5; `resources` go beside it."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /Resources << " + RESOURCES + b" " + resources + b" >> >>",
            5: FONT,
            6: DESCRIPTOR,
            **(objects or {}),
        },
        1,
    )


def text_of(data: bytes, cap: int | None = None) -> PageText:
    document = PdfiumDocument.open(data, None)
    try:
        return document.page_text(0, cap)
    finally:
        document.close()


def codes(text: PageText) -> list[int]:
    return [character.code for character in text.characters]


def test_a_character_as_its_font_and_position_make_it():
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (A) Tj ET"))
    assert text == PageText(
        characters=(
            TextCharacter(
                code=ord("A"),
                map_error=False,
                hyphen=False,
                origin=(100.0, 200.0),
                matrix=(1.0, 0.0, 0.0, 1.0, 100.0, 200.0),
                font_size=10.0,
                loose_box=(100.0, 198.0, 110.0, 208.0),
                render_mode=0,
                ascent=0.8,
                descent=-0.2,
            ),
        ),
        stopped=False,
        too_deep=False,
    )


def test_the_matrices_are_in_the_matrix_and_not_the_font_size():
    """cm triples, Tm doubles: six times in all; Tf stays the font size."""
    (character,) = text_of(
        page_file(b"3 0 0 3 0 0 cm BT /F1 10 Tf 2 0 0 2 10 20 Tm (A) Tj ET")
    ).characters
    assert character.origin == (30.0, 60.0)
    assert character.matrix == (6.0, 0.0, 0.0, 6.0, 30.0, 60.0)
    assert character.font_size == 10.0
    assert character.loose_box == (30.0, 48.0, 90.0, 108.0)


def test_horizontal_scaling_is_in_the_matrix():
    (character,) = text_of(page_file(b"BT /F1 10 Tf 50 Tz 100 200 Td (A) Tj ET")).characters
    assert character.matrix == (0.5, 0.0, 0.0, 1.0, 100.0, 200.0)
    assert character.loose_box == (100.0, 198.0, 105.0, 208.0)


def test_turned_text_has_its_matrix():
    (character,) = text_of(page_file(b"BT /F1 10 Tf 0 1 -1 0 100 200 Tm (A) Tj ET")).characters
    assert (character.origin, character.matrix) == (
        (100.0, 200.0),
        (0.0, 1.0, -1.0, 0.0, 100.0, 200.0),
    )


def test_the_matrix_begins_where_the_text_object_does():
    """The translation of the matrix stays at the object's start; the origin moves."""
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj ET"))
    assert [c.matrix for c in text.characters] == [(1.0, 0.0, 0.0, 1.0, 100.0, 200.0)] * 2
    assert [c.origin for c in text.characters] == [(100.0, 200.0), (110.0, 200.0)]


def test_a_negative_font_size_is_given_as_tf_sets_it():
    (character,) = text_of(page_file(b"BT /F1 -10 Tf 100 200 Td (A) Tj ET")).characters
    assert character.font_size == -10.0


def test_the_characters_come_in_content_order():
    """PDFium's text page puts the text of one line in order of x (measured,
    issue T1); the characters come as the content draws them."""
    data = page_file(b"BT /F1 10 Tf 200 200 Td (CD) Tj ET BT /F1 10 Tf 100 200 Td (AB) Tj ET")
    text = text_of(data)
    assert codes(text) == [ord(c) for c in "CDAB"]
    assert [c.origin[0] for c in text.characters] == [200.0, 210.0, 100.0, 110.0]


def test_text_in_a_form_comes_where_the_form_is_drawn():
    """The form draws B, then D to its left on the same line."""
    data = page_file(
        b"BT /F1 10 Tf 100 100 Td (A) Tj ET /X Do BT /F1 10 Tf 100 300 Td (C) Tj ET",
        resources=b"/XObject << /X 7 0 R >>",
        objects={
            7: stream(
                b"BT /F1 10 Tf 300 200 Td (B) Tj ET BT /F1 10 Tf 100 200 Td (D) Tj ET",
                b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
                b" /Resources << /Font << /F1 5 0 R >> >>",
            )
        },
    )
    assert codes(text_of(data)) == [ord(c) for c in "ABDC"]


def test_each_character_is_where_the_advances_put_it():
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (A B) Tj ET"))
    assert codes(text) == [ord("A"), ord(" "), ord("B")]
    assert [c.origin for c in text.characters] == [(100.0, 200.0), (110.0, 200.0), (120.0, 200.0)]


def test_characters_pdfium_adds_are_left_out():
    """PDFium adds a line break, CR and LF, between the lines and another
    before the E far to the right (measured, issue T1): no character of the
    content."""
    data = page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj 0 -20 Td (CD) Tj 100 0 Td (E) Tj ET")
    assert codes(text_of(data)) == [ord(c) for c in "ABCDE"]


def test_a_space_pdfium_adds_across_a_gap_is_left_out():
    """AB ends at 120; CD begins at 125: PDFium adds a space between (measured,
    issue T1)."""
    data = page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj 25 0 Td (CD) Tj ET")
    assert codes(text_of(data)) == [ord(c) for c in "ABCD"]


def test_a_hyphen_at_the_end_of_a_line_is_u0002():
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (AB-) Tj 0 -20 Td (CD) Tj ET"))
    assert codes(text) == [ord("A"), ord("B"), 0x02, ord("C"), ord("D")]
    assert [c.hyphen for c in text.characters] == [False, False, True, False, False]


@pytest.mark.parametrize("mode", [0, 1, 2, 3, 4, 5, 6, 7])
def test_the_render_mode_as_tr_sets_it(mode):
    (character,) = text_of(page_file(b"BT %d Tr /F1 10 Tf 100 200 Td (A) Tj ET" % mode)).characters
    assert character.render_mode == mode


def test_each_text_object_its_render_mode():
    text = text_of(page_file(b"BT 3 Tr /F1 10 Tf 100 200 Td (A) Tj 0 Tr (B) Tj ET"))
    assert [c.render_mode for c in text.characters] == [3, 0]


def test_text_set_at_size_0_is_not_on_the_text_page():
    text = text_of(page_file(b"BT /F1 0 Tf 100 200 Td (A) Tj /F1 10 Tf (B) Tj ET"))
    assert codes(text) == [ord("B")]


def test_a_ligature_is_two_characters_at_one_origin():
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /L def"
        b" 1 begincodespacerange <00> <FF> endcodespacerange"
        b" 1 beginbfchar <41> <00660069> endbfchar endcmap"
        b" CMapName currentdict /CMap defineresource pop end end"
    )
    data = page_file(
        b"BT /F2 10 Tf 100 200 Td (BAB) Tj ET",
        resources=b"/Font << /F1 5 0 R /F2 7 0 R >>",
        objects={7: FONT[:-2] + b" /ToUnicode 8 0 R >>", 8: stream(cmap)},
    )
    text = text_of(data)
    assert codes(text) == [ord("B"), ord("f"), ord("i"), ord("B")]
    assert [c.origin for c in text.characters] == [
        (100.0, 200.0),
        (110.0, 200.0),
        (110.0, 200.0),
        (120.0, 200.0),
    ]


def test_a_code_pdfium_cannot_map_is_given_as_it_is():
    """A Type 0 font, Identity-H, without /ToUnicode."""
    data = page_file(
        b"BT /F2 10 Tf 100 200 Td <00410042> Tj ET",
        resources=b"/Font << /F1 5 0 R /F2 7 0 R >>",
        objects={
            7: b"<< /Type /Font /Subtype /Type0 /BaseFont /Gate /Encoding /Identity-H"
            b" /DescendantFonts [8 0 R] >>",
            8: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Gate /CIDSystemInfo"
            b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R"
            b" /DW 1000 >>",
        },
    )
    text = text_of(data)
    assert codes(text) == [0x41, 0x42]
    assert [c.map_error for c in text.characters] == [True, True]


def test_codes_mapped_and_not_mapped_on_one_page():
    data = page_file(
        b"BT /F1 10 Tf 100 200 Td (A) Tj /F2 10 Tf <0042> Tj ET",
        resources=b"/Font << /F1 5 0 R /F2 7 0 R >>",
        objects={
            7: b"<< /Type /Font /Subtype /Type0 /BaseFont /Gate /Encoding /Identity-H"
            b" /DescendantFonts [8 0 R] >>",
            8: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Gate /CIDSystemInfo"
            b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R"
            b" /DW 1000 >>",
        },
    )
    assert [c.map_error for c in text_of(data).characters] == [False, True]


def test_each_font_its_own_metrics():
    data = page_file(
        b"BT /F1 10 Tf 100 200 Td (A) Tj /F2 10 Tf (B) Tj ET",
        resources=b"/Font << /F1 5 0 R /F2 7 0 R >>",
        objects={
            7: FONT.replace(b"6 0 R", b"8 0 R"),
            8: DESCRIPTOR.replace(b"/Ascent 800 /Descent -200", b"/Ascent 700 /Descent -300"),
        },
    )
    text = text_of(data)
    assert [(c.ascent, c.descent) for c in text.characters] == [(0.8, -0.2), (0.7, -0.3)]


def test_text_in_a_form_is_placed_through_its_matrices():
    data = page_file(
        b"1 0 0 1 0 10 cm /X Do",
        resources=b"/XObject << /X 7 0 R >>",
        objects={
            7: stream(
                b"BT /F1 10 Tf 100 200 Td (A) Tj ET",
                b"/Type /XObject /Subtype /Form /BBox [0 0 400 300] /Matrix [1 0 0 1 50 0]"
                b" /Resources << /Font << /F1 5 0 R >> >>",
            )
        },
    )
    (character,) = text_of(data).characters
    assert (character.origin, character.matrix) == (
        (150.0, 210.0),
        (1.0, 0.0, 0.0, 1.0, 150.0, 210.0),
    )


def chain(depth: int) -> dict[int, bytes]:
    """Forms 100 to 100 + depth - 1, each drawing the next; the last draws text."""
    objects = {}
    for level in range(depth):
        last = level == depth - 1
        content = b"BT /F1 10 Tf 100 200 Td (A) Tj ET" if last else b"/X Do"
        held = b"" if last else b" /XObject << /X %d 0 R >>" % (101 + level)
        objects[100 + level] = stream(
            content,
            b"/Type /XObject /Subtype /Form /BBox [0 0 400 300] /Resources << /Font << /F1 5 0 R >>"
            + held
            + b" >>",
        )
    return objects


def forms(depth: int) -> bytes:
    """Text in the last of `depth` forms."""
    return page_file(b"/X Do", resources=b"/XObject << /X 100 0 R >>", objects=chain(depth))


def test_text_forty_forms_deep_is_read():
    text = text_of(forms(40))
    assert (codes(text), text.too_deep) == ([ord("A")], False)


def test_text_forty_one_forms_deep_is_lost_and_said_so():
    text = text_of(forms(41))
    assert (codes(text), text.too_deep) == ([], True)


def test_text_beside_a_form_too_deep_is_read():
    """A path, text, then a form whose first object is a path and whose second
    begins a chain of 41 forms: the 41st form is too deep."""
    data = page_file(
        b"0 0 m 1 0 l S BT /F1 10 Tf 10 10 Td (Z) Tj ET /W Do",
        resources=b"/XObject << /W 99 0 R >>",
        objects={
            **chain(41),
            99: stream(
                b"0 0 m 1 0 l S /X Do",
                b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
                b" /Resources << /XObject << /X 100 0 R >> >>",
            ),
        },
    )
    text = text_of(data)
    assert (codes(text), text.too_deep) == ([ord("Z")], True)


def test_text_after_a_form_too_deep_is_read():
    data = page_file(
        b"BT /F1 10 Tf 10 10 Td (Z) Tj ET /X Do BT /F1 10 Tf 10 30 Td (Y) Tj ET",
        resources=b"/XObject << /X 100 0 R >>",
        objects=chain(41),
    )
    text = text_of(data)
    assert (codes(text), text.too_deep) == ([ord("Z"), ord("Y")], True)


def test_the_cap_counts_in_content_order():
    data = page_file(b"BT /F1 10 Tf 200 200 Td (CD) Tj ET BT /F1 10 Tf 100 200 Td (AB) Tj ET")
    text = text_of(data, 2)
    assert (codes(text), text.stopped) == ([ord("C"), ord("D")], True)


@pytest.mark.parametrize(
    ("cap", "read", "stopped"), [(None, 3, False), (3, 3, False), (2, 2, True), (0, 0, True)]
)
def test_reading_stops_at_the_cap(cap, read, stopped):
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (ABC) Tj ET"), cap)
    assert (len(text.characters), text.stopped) == (read, stopped)


def test_the_cap_counts_no_character_pdfium_adds():
    """Two lines: PDFium adds a line break, CR and LF, between them."""
    text = text_of(page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj 0 -20 Td (CD) Tj ET"), 4)
    assert (codes(text), text.stopped) == ([ord(c) for c in "ABCD"], False)


def test_a_page_without_text():
    assert text_of(page_file(b"0 0 m 10 0 l S"), 0) == PageText((), stopped=False, too_deep=False)


def test_a_cap_below_0_is_refused():
    with pytest.raises(ValueError):
        text_of(page_file(b""), -1)


def test_it_implements_the_text_reader():
    members = typing.get_protocol_members(TextReader)
    assert members == {"page_text"}
    assert all(callable(getattr(PdfiumDocument, name)) for name in members)


def test_the_text_types_have_their_fields_and_are_frozen():
    assert [f.name for f in dataclasses.fields(TextCharacter)] == [
        "code",
        "map_error",
        "hyphen",
        "origin",
        "matrix",
        "font_size",
        "loose_box",
        "render_mode",
        "ascent",
        "descent",
    ]
    assert [f.name for f in dataclasses.fields(PageText)] == ["characters", "stopped", "too_deep"]
    text = PageText((), stopped=False, too_deep=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        text.stopped = True  # type: ignore[misc]
