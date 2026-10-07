"""Fonts as the text of TextRules.V2 reads them (pdfacts.backends.fonts).

The entries are constructed; the expected widths and texts are those the
entries, ISO 32000-1 (9.6 to 9.10) and Adobe's files give: the widths of the
standard 14 fonts are the AFM files' WX, the glyph names' texts the Adobe
Glyph List's.
"""

from __future__ import annotations

import pytest

from pdfacts.backends.fonts import (
    ABSENT,
    INVALID_TEXT,
    FontEntries,
    FontInvalid,
    FontNotRead,
    font_of,
    glyph_text,
    parse_to_unicode,
    standard_name,
    type1_encoding,
)


def simple(**entries) -> FontEntries:
    return FontEntries(**{"subtype": b"TrueType", "base_font": b"ABCDEF+Some", **entries})


# --- widths -------------------------------------------------------------------------


def test_widths_from_first_char_and_the_missing_width():
    font = font_of(simple(first_char=65, widths=(600.0, 450.0), missing_width=250.0, embedded=True))
    assert font.width(65) == 0.6
    assert font.width(66) == 0.45
    assert font.width(64) == 0.25
    assert font.width(67) == 0.25


def test_without_a_missing_width_a_code_the_widths_do_not_hold_is_zero_wide():
    font = font_of(simple(first_char=65, widths=(600.0,), embedded=True))
    assert font.width(66) == 0.0


def test_a_standard_font_without_widths_takes_the_afm_widths_by_glyph_name():
    font = font_of(
        FontEntries(subtype=b"Type1", base_font=b"Helvetica", encoding=b"WinAnsiEncoding")
    )
    assert font.width(ord("A")) == 0.667
    assert font.width(0xE4) == 0.556  # adieresis
    assert font.width(0x80) == 0.556  # Euro
    # A code whose glyph the metrics lack has no width.
    assert font.width(0x01) is None


def test_a_font_without_widths_that_is_not_standard_is_invalid():
    with pytest.raises(FontInvalid, match="/Widths"):
        font_of(simple(embedded=True))
    # An embedded font of a standard name is not the standard font.
    with pytest.raises(FontInvalid, match="/Widths"):
        font_of(FontEntries(subtype=b"Type1", base_font=b"Helvetica", embedded=True))


def test_widths_without_first_char_are_invalid():
    with pytest.raises(FontInvalid, match="/FirstChar"):
        font_of(simple(widths=(500.0, 600.0), embedded=True))


def test_widths_that_last_char_does_not_end_are_invalid():
    with pytest.raises(FontInvalid, match="/LastChar"):
        font_of(simple(first_char=65, last_char=67, widths=(500.0, 600.0), embedded=True))
    font = font_of(simple(first_char=65, last_char=66, widths=(500.0, 600.0), embedded=True))
    assert font.width(66) == 0.6


def test_the_standard_name_without_its_subset_prefix():
    assert standard_name(b"ABCDEF+Helvetica-Bold") == "Helvetica-Bold"
    assert standard_name(b"Helvetica") == "Helvetica"
    assert standard_name(b"Arial") is None
    assert standard_name(b"abcdef+Helvetica") is None  # a tag is six capital letters
    assert standard_name(None) is None


# --- ascent and descent -------------------------------------------------------------


def test_the_ascent_and_descent_of_the_descriptor_per_unit_of_size():
    font = font_of(simple(first_char=32, widths=(250.0,), ascent=905.0, descent=-212.0))
    assert (font.ascent, font.descent) == (0.905, -0.212)


def test_a_standard_font_without_a_descriptor_takes_the_afm_ascender_and_descender():
    # Times-Roman.afm: Ascender 683, Descender -217.
    font = font_of(FontEntries(subtype=b"Type1", base_font=b"Times-Roman"))
    assert (font.ascent, font.descent) == (0.683, -0.217)


def test_a_standard_font_takes_from_its_afm_file_only_what_the_descriptor_lacks():
    font = font_of(FontEntries(subtype=b"Type1", base_font=b"Times-Roman", ascent=700.0))
    assert (font.ascent, font.descent) == (0.7, -0.217)


def test_a_symbolic_standard_font_takes_its_font_box():
    # ZapfDingbats.afm: no Ascender or Descender; FontBBox -1 -143 981 820.
    font = font_of(FontEntries(subtype=b"Type1", base_font=b"ZapfDingbats"))
    assert (font.ascent, font.descent) == (0.82, -0.143)


def test_without_them_the_ascent_and_descent_are_none():
    font = font_of(simple(first_char=32, widths=(250.0,)))
    assert (font.ascent, font.descent) == (None, None)


# --- encodings and glyph names -------------------------------------------------------


@pytest.mark.parametrize(
    ("encoding", "code", "text"),
    [
        (b"WinAnsiEncoding", 0x41, "A"),
        (b"WinAnsiEncoding", 0x80, "€"),
        (b"WinAnsiEncoding", 0xA0, " "),  # Annex D: space, also at 0240
        (b"WinAnsiEncoding", 0xAD, "-"),  # Annex D: hyphen, also at 0255
        (b"WinAnsiEncoding", 0x81, "•"),  # Annex D: an unused code is bullet
        (b"StandardEncoding", 0x27, "’"),  # quoteright
        (b"StandardEncoding", 0x60, "‘"),  # quoteleft
        (b"MacRomanEncoding", 0xDB, "¤"),  # Annex D: currency
        (b"MacRomanEncoding", 0xCA, " "),
        (b"MacRomanEncoding", 0x8A, "ä"),
    ],
)
def test_a_predefined_encoding_gives_the_text_of_its_glyph_name(encoding, code, text):
    font = font_of(simple(encoding=encoding, first_char=0, widths=(500.0,) * 256))
    assert font.text(code) == text


def test_an_encoding_pdfacts_does_not_know_is_not_read():
    with pytest.raises(FontNotRead, match="MacExpertEncoding"):
        font_of(simple(encoding=b"MacExpertEncoding", first_char=0, widths=(500.0,)))


def test_differences_over_the_base_encoding():
    font = font_of(
        simple(
            encoding_dictionary=True,
            base_encoding=b"WinAnsiEncoding",
            differences=((65, b"Euro"), (66, b"germandbls")),
            first_char=0,
            widths=(500.0,) * 256,
        )
    )
    assert (font.text(65), font.text(66), font.text(67)) == ("€", "ß", "C")


def test_differences_of_a_standard_font_without_base_are_over_its_own_encoding():
    font = font_of(
        FontEntries(
            subtype=b"Type1",
            base_font=b"Helvetica",
            encoding_dictionary=True,
            differences=((65, b"Adieresis"),),
        )
    )
    assert font.text(65) == "Ä"
    assert font.text(0x27) == "’"  # StandardEncoding, Helvetica's own


def test_an_embedded_type1_font_declares_its_own_encoding():
    program = (
        b"%!PS-AdobeFont-1.0: X\n/FontName /X def\n/Encoding StandardEncoding def\ncurrentdict end"
    )
    font = font_of(
        FontEntries(
            subtype=b"Type1",
            base_font=b"ABCDEF+X",
            encoding_dictionary=True,
            differences=((32, b"space"),),
            first_char=32,
            widths=(250.0,) * 95,
            embedded=True,
            type1_program=program,
        )
    )
    assert (font.text(0x41), font.text(0x27), font.text(32)) == ("A", "’", " ")


def test_a_font_program_encoding_of_dup_and_put():
    program = (
        b"/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
        b"dup 65 /B put\ndup 66 /udieresis put\nreadonly def"
    )
    names = type1_encoding(program)
    assert (names[65], names[66], names[67]) == (b"B", b"udieresis", None)
    assert type1_encoding(b"/FontName /X def") is None


def test_an_embedded_font_whose_own_encoding_pdfacts_does_not_read_has_texts_only_where_named():
    font = font_of(
        simple(
            encoding_dictionary=True,
            differences=((65, b"A"),),
            first_char=65,
            widths=(600.0, 600.0),
            embedded=True,
        )
    )
    assert font.text(65) == "A"
    assert font.text(66) is None


@pytest.mark.parametrize(
    ("name", "dingbats", "text"),
    [
        (b"adieresis", False, "ä"),
        (b"uni20AC", False, "€"),
        (b"uni00410042", False, "AB"),
        (b"u1F600", False, "\U0001f600"),
        (b"uniD800", False, None),  # a surrogate is no character
        (b"uni20ac", False, None),  # lower-case digits are no uniXXXX name
        (b"g123", False, None),
        (b"f_i", False, "fi"),
        (b"A.sc", False, "A"),
        (b"uni20AC_u1F600.alt", False, "€😀"),
        (b"f_xyz", False, "f"),
        (b".notdef", False, None),
        (b"a1", True, "✁"),
        (b"a1", False, None),
    ],
)
def test_glyph_names(name, dingbats, text):
    assert glyph_text(name, dingbats) == text


def test_the_zapfdingbats_font_reads_its_names_with_its_own_list():
    # ZapfDingbats.afm: C 33 ; N a1; C 32 ; N space, which only the Adobe
    # Glyph List names.
    font = font_of(FontEntries(subtype=b"Type1", base_font=b"ZapfDingbats"))
    assert (font.text(33), font.text(32)) == ("✁", " ")


def test_a_symbolic_standard_font_keeps_its_own_encoding_over_a_named_one():
    # Symbol.afm: C 97 ; N alpha.
    font = font_of(FontEntries(subtype=b"Type1", base_font=b"Symbol", encoding=b"WinAnsiEncoding"))
    assert font.text(97) == "α"
    named = font_of(
        FontEntries(
            subtype=b"Type1",
            base_font=b"Symbol",
            encoding_dictionary=True,
            base_encoding=b"WinAnsiEncoding",
            differences=((65, b"beta"),),
        )
    )
    assert (named.text(97), named.text(65)) == ("α", "β")


@pytest.mark.parametrize("code", [9, 10, 12, 13])
def test_a_white_space_code_below_32_without_a_glyph_is_that_character(code):
    font = font_of(simple(encoding=b"WinAnsiEncoding", first_char=0, widths=(500.0,) * 256))
    assert font.text(code) == chr(code)


@pytest.mark.parametrize(
    "entries",
    [
        {
            "encoding_dictionary": True,
            "base_encoding": b"WinAnsiEncoding",
            "differences": ((10, b"g3"),),
        },
        {"encoding": b"WinAnsiEncoding", "to_unicode": b"1 beginbfchar <0A> <> endbfchar"},
        {},
    ],
    ids=[
        "a glyph named for it",
        "a ToUnicode entry to no text",
        "an encoding pdfacts does not know",
    ],
)
def test_a_white_space_code_with_a_glyph_an_entry_or_no_known_encoding_is_not(entries):
    font = font_of(simple(first_char=0, widths=(500.0,) * 256, embedded=True, **entries))
    assert font.text(10) is None


def test_a_font_neither_embedded_nor_symbolic_builds_on_standard_encoding():
    nonsymbolic = font_of(simple(first_char=0, widths=(500.0,) * 256, flags=32))
    assert nonsymbolic.text(0x27) == "’"
    symbolic = font_of(simple(first_char=0, widths=(500.0,) * 256, flags=4))
    assert symbolic.text(0x27) is None


def test_another_code_below_32_without_a_glyph_has_no_text():
    font = font_of(simple(encoding=b"WinAnsiEncoding", first_char=0, widths=(500.0,) * 256))
    assert font.text(0x07) is None


# --- ToUnicode ----------------------------------------------------------------------


CMAP = b"""/CIDInit /ProcSet findresource begin 12 dict begin begincmap
1 begincodespacerange <0000> <FFFF> endcodespacerange
3 beginbfchar
<0003> <0020>
<0004> <00660069>
<0005> <>
endbfchar
2 beginbfrange
<0010> <0012> <0041>
<0020> <0022> [<0061> <D800> <0062>]
endbfrange
1 beginbfchar
<0011> <0078>
endbfchar
1 beginbfrange
<0030> <0031> <00FF>
endbfrange
endcmap CMapName currentdict /CMap defineresource pop end end"""


@pytest.mark.parametrize(
    ("code", "text"),
    [
        (0x03, " "),
        (0x04, "fi"),
        (0x05, None),  # mapped to no text
        (0x10, "A"),
        (0x11, "x"),  # the bfchar written after the range holds
        (0x12, "C"),
        (0x13, ABSENT),
        (0x20, "a"),
        (0x21, INVALID_TEXT),  # a lone surrogate is not UTF-16BE
        (0x22, "b"),
        (0x30, "ÿ"),
        (0x31, INVALID_TEXT),  # past the last byte: 9.10.3 leaves it undefined
    ],
)
def test_to_unicode(code, text):
    assert parse_to_unicode(CMAP).get(code) == text


def test_a_range_written_later_holds_over_an_earlier_char():
    cmap = b"1 beginbfchar <0001> <0078> endbfchar 1 beginbfrange <0001> <0002> <0041> endbfrange"
    assert parse_to_unicode(cmap).get(1) == "A"


def test_to_unicode_comes_before_the_encoding():
    cmap = b"1 beginbfchar <41> <0042> endbfchar"
    font = font_of(
        simple(encoding=b"WinAnsiEncoding", to_unicode=cmap, first_char=65, widths=(1.0,))
    )
    assert font.text(0x41) == "B"
    assert font.text(0x43) == "C"


# --- composite fonts ----------------------------------------------------------------


def composite(**entries) -> FontEntries:
    return FontEntries(
        **{
            "subtype": b"Type0",
            "base_font": b"ABCDEF+Some",
            "encoding": b"Identity-H",
            "descendant": b"CIDFontType2",
            **entries,
        }
    )


def test_identity_h_codes_are_two_bytes_and_an_odd_last_byte_is_none():
    font = font_of(composite())
    assert font.codes(b"\x00\x41\x01\x02\x03") == [0x41, 0x102]


def test_a_range_of_w_spanning_every_cid_is_looked_up_when_asked_for():
    font = font_of(composite(cid_widths=((0, 4294967295, 500.0),)))
    assert font.width(5) == 0.5


def test_where_w_gives_a_cid_twice_the_first_holds():
    font = font_of(composite(cid_widths=((10, 20, 300.0), (12, 12, 900.0), (15, 15, 700.0))))
    assert (font.width(12), font.width(15), font.width(21)) == (0.3, 0.3, 1.0)
    font = font_of(composite(cid_widths=((12, 12, 900.0), (10, 20, 300.0))))
    assert (font.width(12), font.width(13)) == (0.9, 0.3)
    font = font_of(composite(cid_widths=((12, 12, 900.0), (12, 12, 500.0))))
    assert font.width(12) == 0.9


def test_cid_widths_and_the_default_width():
    font = font_of(composite(cid_widths=((3, 3, 500.0), (10, 12, 700.0)), default_width=800.0))
    assert (font.width(3), font.width(11), font.width(4)) == (0.5, 0.7, 0.8)
    assert font_of(composite()).width(1) == 1.0


def test_a_composite_font_reads_its_text_only_from_to_unicode():
    font = font_of(composite(to_unicode=b"1 beginbfchar <0041> <00C4> endbfchar"))
    assert font.text(0x41) == "Ä"
    assert font.text(0x09) is None


@pytest.mark.parametrize(
    "entries",
    [
        {"encoding": b"Identity-V"},
        {"encoding": b"UniGB-UCS2-H"},
        {"encoding": None, "encoding_dictionary": True},
    ],
    ids=["vertical", "a predefined CMap", "a CMap stream"],
)
def test_another_cmap_is_not_read(entries):
    with pytest.raises(FontNotRead, match="CMap"):
        font_of(composite(**entries))


# --- Type3 fonts --------------------------------------------------------------------


def type3(**entries) -> FontEntries:
    return FontEntries(
        **{
            "subtype": b"Type3",
            "base_font": None,
            "font_matrix": (0.001, 0.0, 0.0, -0.001, 0.0, 0.0),
            "font_box": (20.0, 23.0, 765.0, -729.0),
            "encoding_dictionary": True,
            "differences": ((65, b"A"),),
            "first_char": 65,
            "widths": (600.0,),
            **entries,
        }
    )


def test_a_type3_font_maps_its_widths_and_box_through_its_matrix():
    font = font_of(type3())
    assert font.width(65) == pytest.approx(0.6)
    # The box's y values through d = -0.001: 23 and -729 give -0.023 and 0.729.
    assert font.ascent == pytest.approx(0.729)
    assert font.descent == pytest.approx(-0.023)
    assert font.text(65) == "A"


def test_a_type3_font_whose_matrix_turns_is_not_read():
    with pytest.raises(FontNotRead, match="Type3"):
        font_of(type3(font_matrix=(0.001, 0.001, 0.0, 0.001, 0.0, 0.0)))


def test_a_type3_font_without_widths_is_invalid():
    with pytest.raises(FontInvalid, match="/Widths"):
        font_of(type3(widths=None))


def test_a_type3_font_without_a_matrix_is_invalid():
    with pytest.raises(FontInvalid):
        font_of(type3(font_matrix=None))


# --- what is not read, and what is invalid ------------------------------------------


def test_an_entry_written_against_the_specification_makes_the_font_invalid():
    with pytest.raises(FontInvalid, match="Widths"):
        font_of(simple(invalid="a font whose /Widths ISO 32000-1 does not allow"))


@pytest.mark.parametrize("subtype", [b"CIDFontType0", b"OpenType", None])
def test_a_subtype_pdfacts_does_not_read(subtype):
    with pytest.raises(FontNotRead, match="subtype"):
        font_of(FontEntries(subtype=subtype, base_font=None))


def test_one_byte_codes():
    font = font_of(simple(first_char=0, widths=(1.0,)))
    assert font.codes(b"\x00\x41\xff") == [0, 0x41, 0xFF]
