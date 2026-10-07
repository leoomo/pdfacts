"""The text state and the codes it shows (pdfacts.backends.content_text).

The machine is fed plain values, as backends/qpdf.py feeds it; the expected
matrices follow from ISO 32000-1, 9.4.2 and 9.4.4, with the constructed
widths and sizes.
"""

from __future__ import annotations

import pytest

from pdfacts.backends.content_text import IDENTITY, TextMachine, TextUnread, TooMany, then
from pdfacts.backends.fonts import FontEntries, font_of

ONE_BYTE = font_of(
    FontEntries(
        subtype=b"TrueType",
        base_font=b"ABCDEF+One",
        encoding=b"WinAnsiEncoding",
        first_char=32,
        widths=(250.0,) + (500.0,) * 94,
        ascent=800.0,
        descent=-200.0,
        embedded=True,
    )
)
TWO_BYTES = font_of(
    FontEntries(
        subtype=b"Type0",
        base_font=b"ABCDEF+Two",
        encoding=b"Identity-H",
        descendant=b"CIDFontType2",
        cid_widths=((32, 32, 250.0),),
        default_width=1000.0,
    )
)


def origins(machine: TextMachine) -> list[tuple[float, float]]:
    return [(g.matrix[4], g.matrix[5]) for g in machine.glyphs]


def started(font=ONE_BYTE, size: float = 10.0) -> TextMachine:
    machine = TextMachine(None)
    machine.begin()
    machine.font(font, size)
    return machine


def test_each_code_advances_by_its_width_at_the_size():
    machine = started()
    machine.show(b"A B")
    # A: 500/1000 * 10 = 5; the space: 250/1000 * 10 = 2.5.
    assert origins(machine) == [(0.0, 0.0), (5.0, 0.0), (7.5, 0.0)]
    assert [g.text for g in machine.glyphs] == ["A", " ", "B"]
    assert [g.width for g in machine.glyphs] == [0.5, 0.25, 0.5]


def test_character_spacing_word_spacing_and_horizontal_scaling():
    machine = started()
    machine.char_spacing(1.0)
    machine.word_spacing(3.0)
    machine.horizontal_scaling(50.0)
    machine.show(b"A A")
    # (5 + 1) * 0.5 = 3; the space: (2.5 + 1 + 3) * 0.5 = 3.25.
    assert origins(machine) == [(0.0, 0.0), (3.0, 0.0), (6.25, 0.0)]
    # The text rendering matrix scales x by the size and the scaling.
    assert machine.glyphs[0].matrix[:4] == (5.0, 0.0, 0.0, 10.0)


def test_word_spacing_is_not_for_a_two_byte_code_32():
    machine = started(TWO_BYTES)
    machine.word_spacing(3.0)
    machine.show(b"\x00\x20\x00\x41")
    # 250/1000 * 10 = 2.5, no word spacing.
    assert origins(machine) == [(0.0, 0.0), (2.5, 0.0)]


def test_tj_numbers_move_against_the_writing_direction():
    machine = started()
    machine.show(b"A")
    machine.adjust(-500.0)
    machine.show(b"A")
    machine.adjust(200.0)
    machine.show(b"A")
    # 5 + 500/1000 * 10 = 10; then 10 + 5 - 2 = 13.
    assert origins(machine) == [(0.0, 0.0), (10.0, 0.0), (13.0, 0.0)]


def test_td_td_capital_t_star_and_tm():
    machine = started()
    machine.move(10.0, 100.0)
    machine.show(b"A")
    machine.leading(12.0)
    machine.next_line()
    machine.show(b"A")
    # TD 5 -20 as backends/qpdf.py makes it: the leading set to 20, then Td.
    machine.leading(20.0)
    machine.move(5.0, -20.0)
    machine.show(b"A")
    machine.set_matrix((2.0, 0.0, 0.0, 2.0, 50.0, 60.0))
    machine.show(b"A")
    machine.next_line()
    machine.show(b"A")
    assert origins(machine) == [
        (10.0, 100.0),
        (10.0, 88.0),
        (15.0, 68.0),
        (50.0, 60.0),
        # T* moves by the leading in text space: 20 down, scaled by 2.
        (50.0, 20.0),
    ]


def test_the_rise_moves_the_glyph_across_its_baseline():
    machine = started()
    machine.rise(3.0)
    machine.show(b"A")
    assert machine.glyphs[0].matrix[4:] == (0.0, 3.0)


def test_q_and_capital_q_keep_the_text_state_and_the_matrix():
    machine = started()
    machine.save()
    machine.char_spacing(5.0)
    machine.concat((1.0, 0.0, 0.0, 1.0, 100.0, 0.0))
    machine.font(TWO_BYTES, 20.0)
    machine.restore()
    machine.show(b"AA")
    assert origins(machine) == [(0.0, 0.0), (5.0, 0.0)]
    # A Q without its q is passed over.
    machine.restore()
    machine.show(b"A")
    assert origins(machine)[-1] == (10.0, 0.0)


def test_a_form_begins_from_the_state_and_its_end_restores_it():
    machine = started()
    machine.begin_form((2.0, 0.0, 0.0, 2.0, 7.0, 0.0))
    machine.show(b"A")
    machine.save()
    machine.save()
    machine.end_form()
    machine.show(b"A")
    # In the form: at (7, 0), A 2 * 5 wide; after it, the text matrix moved
    # on by that advance in text space (5), in the page's matrix.
    assert origins(machine) == [(7.0, 0.0), (5.0, 0.0)]


def test_a_forms_extra_q_does_not_restore_what_was_saved_before_it():
    machine = started()
    machine.save()
    machine.concat((1.0, 0.0, 0.0, 1.0, 100.0, 0.0))
    machine.begin_form(IDENTITY)
    machine.restore()
    machine.restore()
    machine.end_form()
    machine.begin()
    machine.show(b"A")
    assert origins(machine) == [(100.0, 0.0)]


def test_the_matrix_takes_the_ctm_after_the_text_matrix():
    machine = TextMachine(None)
    machine.concat((0.0, 1.0, -1.0, 0.0, 300.0, 0.0))
    machine.begin()
    machine.font(ONE_BYTE, 10.0)
    machine.move(10.0, 20.0)
    machine.show(b"AA")
    # (10, 20) turned by 90 degrees and moved by (300, 0) is (280, 10); the
    # second glyph 5 units along the turned baseline.
    assert origins(machine) == [(280.0, 10.0), (280.0, 15.0)]
    assert then(IDENTITY, IDENTITY) == IDENTITY


def test_the_render_mode_and_the_font_metrics_go_with_each_glyph():
    machine = started()
    machine.render_mode(3)
    machine.show(b"A")
    glyph = machine.glyphs[0]
    assert (glyph.render_mode, glyph.ascent, glyph.descent) == (3, 0.8, -0.2)


def test_text_before_a_font_is_invalid():
    machine = TextMachine(None)
    machine.begin()
    with pytest.raises(TextUnread) as raised:
        machine.show(b"A")
    assert raised.value.reason == "invalid-in-file"


def test_a_code_without_a_width_is_not_read():
    helvetica = font_of(FontEntries(subtype=b"Type1", base_font=b"Helvetica"))
    machine = started(helvetica)
    with pytest.raises(TextUnread) as raised:
        machine.show(b"\x01")
    assert raised.value.reason == "not-read"


def test_the_cap_stops_at_the_code_after_it():
    machine = TextMachine(2)
    machine.begin()
    machine.font(ONE_BYTE, 10.0)
    machine.show(b"AB")
    with pytest.raises(TooMany):
        machine.show(b"C")
    assert len(machine.glyphs) == 3


def test_a_code_whose_to_unicode_is_invalid():
    font = font_of(
        FontEntries(
            subtype=b"TrueType",
            base_font=b"ABCDEF+X",
            first_char=65,
            widths=(500.0,),
            to_unicode=b"1 beginbfchar <41> <D800> endbfchar",
            embedded=True,
        )
    )
    machine = started(font)
    machine.show(b"A")
    assert (machine.glyphs[0].text, machine.glyphs[0].text_invalid) == (None, True)
