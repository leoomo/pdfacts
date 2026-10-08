"""The M4b gate: what it counts, what it compares, and when it fails.

The gate (bench/gates/m4b.py) reads glyphs(), words() and lines() with
TextRules.V2 and with the default rules, looks for PDFium's characters among
the glyphs of V2, and measures the word boxes against a rendering of the text
alone. This file tries the agreement on glyph facts made here, the facts, variant
D, and the receipt and the run; test_gate_m4b_render.py the rendering and the
word boxes. Files are built with the byte-level builder; the numbers expected are
what was written.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
from collections import Counter
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import scipy

import gate_corpus
import pdfacts
from m4b_cases import (
    DEEP_RESOURCES,
    HELVETICA,
    UNKNOWN,
    deep_forms,
    file_of,
    glyph,
    written,
)
from pdfacts import (
    Glyph,
    GlyphRef,
    Line,
    Point,
    Quad,
    Rect,
    TextRules,
    Unknown,
    UnknownReason,
    Word,
)
from pdfacts.text import glyphs as glyphs_module

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m4a  # noqa: E402
import m4b  # noqa: E402

from synthetic.builder import build, stream  # noqa: E402
from synthetic.cases import TEXT_DESCRIPTOR, TEXT_FONT  # noqa: E402

corpus = partial(gate_corpus.corpus, m4b)
run = partial(gate_corpus.run, m4b)


# A value of each field of each fact, other than written()'s first fact holds.
OTHER = {
    "glyphs": {
        "ref": GlyphRef(page_index=0, glyph_index=99),
        "text": "Z",
        "origin": Point(1.0, 2.0),
        "direction": 1.0,
        "size": 3.0,
        "box": Quad(Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0)),
        "upright": False,
        "render_mode": 2,
        "hyphen": True,
    },
    "words": {"page_index": 1, "glyphs": (99,), "text": "Z", "box": Rect(0.0, 0.0, 1.0, 1.0)},
    "lines": {"page_index": 1, "words": (99,), "text": "Z", "box": Rect(0.0, 0.0, 1.0, 1.0)},
}


@pytest.mark.parametrize(
    ("pdfium", "v2", "wanted"),
    [
        ("A", ["A"], "same text"),
        ("fi", ["fi"], "same text"),
        # White space is looked for like any other character.
        (" ", [" "], "same text"),
        ("\u00a0", ["\u00a0"], "same text"),
        (" ", ["A"], "other text at the place"),
        (" ", [], "no glyph at the place"),
        ("\x02", ["-"], "line-end hyphen marker"),
        ("\x02", ["\u00ad"], "line-end hyphen marker"),
        ("-", ["\u00ad"], "soft hyphen read as a hyphen"),
        ("\ufb01", ["fi"], "same text under NFKC"),
        ("fi", ["\ufb01"], "same text under NFKC"),
        ("A", [UNKNOWN], "V2 text unknown"),
        ("ab", ["ac"], "same first character"),
        ("A", ["B"], "other text at the place"),
        ("\x02", ["A"], "other text at the place"),
        ("-", ["A"], "other text at the place"),
        ("A", [], "no glyph at the place"),
    ],
)
def test_1_each_class_of_a_pdfium_character(pdfium, v2, wanted):
    counts = m4b.agreement(
        [glyph(pdfium)], [glyph(text, index=number) for number, text in enumerate(v2)]
    )
    assert counts["PDFium: " + wanted] == 1
    assert sum(n for key, n in counts.items() if key.startswith("PDFium: ")) == 1


@pytest.mark.parametrize(
    ("pdfium", "v2", "wanted"),
    [
        # The classes in the order they are tried: the first that holds.
        ("-", ["\u00ad", "-"], "same text"),
        ("\x02", ["\x02", "-"], "same text"),
        ("\x02", ["A", "-"], "line-end hyphen marker"),
        ("\x02", [UNKNOWN, "\u00ad"], "line-end hyphen marker"),
        ("-", [UNKNOWN, "\u00ad"], "soft hyphen read as a hyphen"),
        ("\ufb01", [UNKNOWN, "fi"], "same text under NFKC"),
        ("ab", [UNKNOWN, "ac"], "V2 text unknown"),
        ("ab", ["xy", "ac"], "same first character"),
        ("ab", ["xy", UNKNOWN], "V2 text unknown"),
        # A hyphen marker is not a soft hyphen read as a hyphen, nor the reverse.
        ("\u00ad", ["-"], "other text at the place"),
        ("-", ["\x02"], "other text at the place"),
        # The first character of a text and a first character that is no code unit alone.
        ("A", ["Ab"], "same first character"),
    ],
)
def test_2_the_first_class_that_holds_wins(pdfium, v2, wanted):
    counts = m4b.agreement(
        [glyph(pdfium)], [glyph(text, index=number) for number, text in enumerate(v2)]
    )
    assert counts["PDFium: " + wanted] == 1
    assert sum(n for key, n in counts.items() if key.startswith("PDFium: ")) == 1


def test_3_the_characters_of_pdfium_with_no_text_or_no_origin_fall_in_no_class():
    pdfium = [
        glyph(UNKNOWN, 30.0, 10.0),
        glyph("A", None, index=1),
        glyph("B", 40.0, 10.0, index=2),
    ]
    counts = m4b.agreement(pdfium, [glyph("B", 40.0, 10.0)])
    assert counts == Counter(
        {
            "PDFium: text unknown": 1,
            "PDFium: origin unknown": 1,
            "PDFium: same text": 1,
            "V2: a PDFium character at the place": 1,
        }
    )


def test_4_white_space_of_pdfium_is_looked_for_like_any_other_character():
    """A space with a space of V2 at its place, a no-break space with none, a tab with an "A"."""
    pdfium = [
        glyph(" ", 10.0, 10.0),
        glyph("\u00a0", 20.0, 10.0, index=1),
        glyph("\t", 30.0, 10.0, index=2),
    ]
    v2 = [glyph(" ", 10.0, 10.0), glyph("A", 30.0, 10.0, index=1)]
    assert m4b.agreement(pdfium, v2) == Counter(
        {
            "PDFium: same text": 1,
            "PDFium: no glyph at the place": 1,
            "PDFium: other text at the place": 1,
            "V2: white space": 1,
            "V2: a PDFium character at the place": 1,
        }
    )


@pytest.mark.parametrize(
    ("dx", "dy", "at_the_place"),
    [
        (0.0, 0.0, True),
        (0.01, 0.0, True),
        (-0.01, 0.0, True),
        (0.0, 0.01, True),
        (0.0, -0.01, True),
        (0.01, -0.01, True),
        (0.0101, 0.0, False),
        (-0.0101, 0.0, False),
        (0.0, 0.0101, False),
        (0.0, -0.0101, False),
        (0.0101, 0.0101, False),
        (0.5, 0.5, False),
    ],
)
def test_5_a_place_is_within_a_hundredth_in_x_and_in_y(dx, dy, at_the_place):
    """Differences of floats as they are, no rounding: from 0.0, 0.01 is the float 0.01."""
    counts = m4b.agreement([glyph("A", 0.0, 0.0)], [glyph("A", dx, dy)])
    assert counts["PDFium: same text"] == at_the_place
    assert counts["PDFium: no glyph at the place"] == (not at_the_place)
    assert counts["V2: a PDFium character at the place"] == at_the_place
    assert counts["V2: other"] == (not at_the_place)


def test_6_a_place_across_cells_and_below_zero():
    """Places on both sides of a cell's edge, and of zero, are found."""
    for x in (0.0199, 0.02, 0.0201, -0.0001, -0.0199, -0.02, 123.45):
        for step in (0.0, 0.0099, -0.0099):
            counts = m4b.agreement([glyph("A", x, 7.0)], [glyph("A", x + step, 7.0)])
            assert counts["PDFium: same text"] == 1, (x, step)


@pytest.mark.parametrize(
    ("v2", "pdfium", "wanted"),
    [
        # The PDFium character may be of any text, white space or unknown.
        (glyph("A"), glyph("A"), "a PDFium character at the place"),
        (glyph("A"), glyph("B"), "a PDFium character at the place"),
        (glyph("A"), glyph(" "), "a PDFium character at the place"),
        (glyph("A"), glyph(UNKNOWN), "a PDFium character at the place"),
        (glyph("A"), None, "other"),
        (glyph("A", mode=3), None, "invisible"),
        (glyph("A", mode=7), None, "invisible"),
        (glyph("A", mode=1), None, "other"),
        (glyph("A", mode=2), None, "other"),
        (glyph("A", mode=4), None, "other"),
        (glyph("A", mode=3), glyph("B"), "a PDFium character at the place"),
    ],
)
def test_7_each_class_of_a_glyph_of_v2(v2, pdfium, wanted):
    counts = m4b.agreement([] if pdfium is None else [pdfium], [v2])
    assert counts["V2: " + wanted] == 1
    assert sum(n for key, n in counts.items() if key.startswith("V2: ")) == 1


def test_8_drawn_again_is_an_earlier_glyph_of_the_text_at_the_place():
    first, again = glyph("A", index=0), glyph("A", index=1)
    counts = m4b.agreement([], [first, again])
    assert counts == Counter({"V2: other": 1, "V2: drawn again": 1})
    # Another text at the place is not drawing again; nor is a later glyph, nor another place.
    assert m4b.agreement([], [glyph("A"), glyph("B", index=1)]) == Counter({"V2: other": 2})
    assert m4b.agreement([], [glyph("A"), glyph("A", 100.5, index=1)]) == Counter({"V2: other": 2})
    # The earlier glyph may be invisible; the later one, drawn again, comes before "invisible".
    counts = m4b.agreement([], [glyph("A", index=0), glyph("A", mode=3, index=1)])
    assert counts == Counter({"V2: other": 1, "V2: drawn again": 1})
    counts = m4b.agreement([], [glyph("A", mode=3, index=0), glyph("A", index=1)])
    assert counts == Counter({"V2: invisible": 1, "V2: drawn again": 1})
    # A PDFium character at the place comes before drawn again.
    counts = m4b.agreement([glyph("A")], [first, again])
    assert counts == Counter({"PDFium: same text": 1, "V2: a PDFium character at the place": 2})


def test_9_the_glyphs_of_v2_that_fall_in_no_class():
    v2 = [
        glyph(" ", index=0),
        glyph("\t", 20.0, index=1),
        glyph(UNKNOWN, 30.0, index=2),
        glyph(Unknown(UnknownReason.INVALID, "made here"), 40.0, index=3),
        glyph("A", None, index=4),
        glyph("B", 50.0, index=5),
    ]
    assert m4b.agreement([], v2) == Counter(
        {
            "V2: white space": 2,
            "V2: text unknown unreadable": 1,
            "V2: text unknown invalid-in-file": 1,
            "V2: origin unknown": 1,
            "V2: other": 1,
        }
    )


def test_10_a_glyph_with_an_unknown_origin_is_at_no_place():
    counts = m4b.agreement([glyph("A", None)], [glyph("A", None)])
    assert counts == Counter({"PDFium: origin unknown": 1, "V2: origin unknown": 1})


def test_11_a_file_is_counted_as_written():
    entry = m4b.read_file(written())
    assert (entry["page_count"], entry["glyph_count"]) == (1, 5)
    assert (entry["word_count"], entry["line_count"]) == (2, 2)
    assert entry["unknown"] == {}
    assert entry["pages_unknown"] == []
    assert entry["agreement"] == {
        **dict.fromkeys(m4b.AGREEMENT, 0),
        "PDFium: same text": 4,
        "V2: a PDFium character at the place": 4,
        "V2: white space": 1,
    }
    # AB has ink and is correct; EF paints nothing, and every glyph of it is invisible.
    expected = {
        "words": 2,
        "measured": 2,
        "no ink, all invisible": 1,
        "judged": 1,
        "correct": 1,
    }
    assert entry["render"] == {
        **dict.fromkeys(m4b.RENDER, 0),
        "pages measured": 1,
        **{f"{variant} {key}": n for variant in m4b.VARIANTS for key, n in expected.items()},
    }
    assert m4b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 0


@pytest.mark.parametrize(("name", "kind"), [("glyphs", Glyph), ("words", Word), ("lines", Line)])
def test_12_each_digest_holds_every_value(monkeypatch, name, kind):
    """Each field of the page's first fact changed in turn: its digest changes, and no
    other. Nothing is rendered, as the facts changed do not describe the page."""
    assert set(OTHER[name]) == {field.name for field in dataclasses.fields(kind)}
    monkeypatch.setattr(m4b, "measure_file", lambda *arguments: Counter())
    before = m4b.read_file(written())
    real = getattr(pdfacts, name)
    for field, other in OTHER[name].items():

        def changed(document, page_index, rules=TextRules.V1, field=field, other=other):
            found = real(document, page_index, rules=rules)
            return (dataclasses.replace(found[0], **{field: other}), *found[1:])

        monkeypatch.setattr(pdfacts, name, changed)
        after = m4b.read_file(written())
        assert [key for key in ("glyphs", "words", "lines") if before[key] != after[key]] == [
            name
        ], field


def test_13_the_digests_are_those_of_the_m4a_gate_of_the_same_facts():
    """digest_page of the M4a gate, over the glyphs, words and lines of V2."""
    digests = {key: m4a.Digest() for key in ("glyphs", "words", "lines")}
    with pdfacts.open(written()) as document:
        facts = m4b.read_page(document, 0)
    assert not isinstance(facts, Unknown)
    m4a.digest_page(digests, *facts)
    entry = m4b.read_file(written())
    assert {key: entry[key] for key in digests} == {k: d.hexdigest() for k, d in digests.items()}


def test_14_a_page_unknown_as_a_whole_is_listed_not_measured_and_fails():
    data = file_of(
        b"BT /F1 10 Tf 100 250 Td (B) Tj ET /X Do", resources=DEEP_RESOURCES, objects=deep_forms()
    )
    entry = m4b.read_file(data)
    assert entry["unknown"] == {"page as a whole unreadable": 1}
    assert entry["pages_unknown"] == ["0"]
    assert (entry["page_count"], entry["glyph_count"], entry["word_count"]) == (1, 0, 0)
    assert entry["agreement"] == dict.fromkeys(m4b.AGREEMENT, 0)
    assert entry["render"] == dict.fromkeys(m4b.RENDER, 0)
    assert m4b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1
    # The page is in the digests as what it is, unknown with its reason and detail.
    with pdfacts.open(data) as document:
        what = pdfacts.glyphs(document, 0, rules=TextRules.V2)
    assert isinstance(what, Unknown)
    digests = {key: m4a.Digest() for key in ("glyphs", "words", "lines")}
    m4a.digest_page(digests, what, what, what)
    assert {key: entry[key] for key in digests} == {k: d.hexdigest() for k, d in digests.items()}
    empty = m4b.read_file(file_of(b""))
    assert all(entry[key] != empty[key] for key in digests)


def test_15_the_pages_read_beside_a_page_unknown_are_counted_and_measured():
    """Page 0 shows "AB"; page 1 draws the forms nested too deep."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents %d 0 R /Resources << "
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: page % 6 + b"/Font << /F1 5 0 R >> >> >>",
            4: page % 7 + DEEP_RESOURCES + b" >> >>",
            5: HELVETICA,
            6: stream(b"BT /F1 10 Tf 100 250 Td (AB) Tj ET"),
            7: stream(b"/X Do"),
            **deep_forms(),
        },
        1,
    )
    entry = m4b.read_file(data)
    assert (entry["page_count"], entry["glyph_count"], entry["word_count"]) == (2, 2, 1)
    assert entry["pages_unknown"] == ["1"]
    assert entry["unknown"] == {"page as a whole unreadable": 1}
    assert entry["agreement"]["PDFium: same text"] == 2
    assert entry["render"]["pages measured"] == 1
    assert entry["render"]["V2 judged"] == 1


def test_16_a_glyph_of_unknown_text_is_counted_by_its_reason():
    """A Type 0 font, Identity-H, without /ToUnicode: the codes have no text."""
    data = file_of(
        b"BT /F2 10 Tf 100 200 Td <00410042> Tj ET",
        resources=b"/Font << /F2 7 0 R >>",
        objects={
            7: b"<< /Type /Font /Subtype /Type0 /BaseFont /Gate /Encoding /Identity-H"
            b" /DescendantFonts [8 0 R] >>",
            8: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Gate /CIDSystemInfo"
            b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R"
            b" /DW 1000 >>",
            6: TEXT_DESCRIPTOR,
        },
    )
    entry = m4b.read_file(data)
    assert entry["unknown"] == {
        "glyph text unreadable": 2,
        "line text unreadable": 1,
        "word text unreadable": 1,
    }
    assert entry["agreement"]["V2: text unknown unreadable"] == 2
    assert entry["agreement"]["PDFium: text unknown"] == 2
    assert entry["pages_unknown"] == []


def test_17_a_page_whose_text_pdfium_does_not_give_is_counted_not_compared(monkeypatch):
    real = pdfacts.glyphs

    def without_pdfium(document, page_index, rules=TextRules.V1):
        if rules is TextRules.V1:
            return UNKNOWN
        return real(document, page_index, rules=rules)

    monkeypatch.setattr(pdfacts, "glyphs", without_pdfium)
    entry = m4b.read_file(written())
    assert entry["agreement"] == {
        **dict.fromkeys(m4b.AGREEMENT, 0),
        "pages without PDFium's characters": 1,
    }
    assert entry["glyph_count"] == 5
    assert entry["pages_unknown"] == []


def test_18_the_gate_takes_none_of_the_c_librarys_trigonometry(monkeypatch):
    """The gate places the words with the vector along the baseline that pdfacts computes."""
    before = m4b.read_file(written())

    def fails(*arguments):
        raise AssertionError("the C library's trigonometry was taken")

    for name in ("atan", "atan2", "sin", "cos", "tan"):
        monkeypatch.setattr(math, name, fails)
    assert m4b.read_file(written()) == before


def half_em_font(ascent=300, descent=-200, bbox=b"[0 -200 1000 800]") -> bytes:
    """A page of 400 x 300 with "A" at size 10 at (100, 200), in a font 1000 thousandths wide whose
    descriptor gives `ascent` and `descent`, and `bbox`."""
    descriptor = (
        b"<< /Type /FontDescriptor /FontName /Gate /Flags 32 /FontBBox " + bbox + b" /ItalicAngle 0"
        b" /Ascent %d /Descent %d /CapHeight 700 /StemV 80 >>" % (ascent, descent)
    )
    return file_of(
        b"BT /F1 10 Tf 100 200 Td (A) Tj ET", font=TEXT_FONT + b" >>", objects={6: descriptor}
    )


def box_of(data: bytes) -> tuple[float, float, float, float]:
    with pdfacts.open(data) as document:
        found = pdfacts.glyphs(document, 0, rules=TextRules.V2)
    assert not isinstance(found, Unknown)
    box = found[0].box
    assert not isinstance(box, Unknown)
    bounds = box.bounds
    return bounds.x0, bounds.y0, bounds.x1, bounds.y1


def test_19_the_descriptors_height_is_the_box_from_descent_to_ascent_at_the_size():
    """Ascent 300 and descent -200 span half an em: V2 scales them to span one, -4 to 6 at size
    10; variant D keeps them, -2 to 3. The advance is 10 either way."""
    data = half_em_font()
    assert box_of(data) == pytest.approx((100.0, 196.0, 110.0, 206.0))
    with m4b.descriptor_heights():
        assert box_of(data) == pytest.approx((100.0, 198.0, 110.0, 203.0))
    assert box_of(data) == pytest.approx((100.0, 196.0, 110.0, 206.0))


@pytest.mark.parametrize(
    ("ascent", "descent", "low", "high"),
    [
        # Heights that span one em or more are as V2 has them, scaled by nothing.
        (800, -200, 198.0, 208.0),
        (900, -300, 197.0, 209.0),
        # A positive descent is read as its negation, and a span below one is kept as it is.
        (300, 200, 198.0, 203.0),
        (500, 0, 200.0, 205.0),
        (999, 0, 200.0, 209.99),
    ],
)
def test_20_the_heights_of_other_spans(ascent, descent, low, high):
    data = half_em_font(ascent, descent)
    with m4b.descriptor_heights():
        _, y0, _, y1 = box_of(data)
    assert (y0, y1) == pytest.approx((low, high))


def test_21_an_invalid_height_is_the_unknown_box_it_is_without_the_wrapper():
    """An ascent of 0 and a /FontBBox with no top above 0: the box is unknown, with the wrapper
    too."""
    data = half_em_font(0, 0, bbox=b"[0 -200 1000 0]")
    with pdfacts.open(data) as document:
        plain = pdfacts.glyphs(document, 0, rules=TextRules.V2)
    with m4b.descriptor_heights(), pdfacts.open(data) as document:
        wrapped = pdfacts.glyphs(document, 0, rules=TextRules.V2)
    assert isinstance(plain[0].box, Unknown)
    assert wrapped[0].box == plain[0].box


def test_22_the_wrapper_is_removed_after_the_block_also_when_it_raises():
    original = glyphs_module._content_box
    with m4b.descriptor_heights():
        assert glyphs_module._content_box is not original
    assert glyphs_module._content_box is original
    with pytest.raises(KeyError), m4b.descriptor_heights():
        assert glyphs_module._content_box is not original
        raise KeyError("raised inside the block")
    assert glyphs_module._content_box is original


def test_23_the_wrapper_scales_the_size_by_the_span_where_it_is_below_one_and_above_0(monkeypatch):
    calls = []

    def recorded(origin, along, size, advance, code):
        calls.append((origin, along, size, advance, code))
        return "the original's result"

    monkeypatch.setattr(glyphs_module, "_content_box", recorded)

    def code(ascent, descent, invalid=None):
        return SimpleNamespace(heights_invalid=invalid, ascent=ascent, descent=descent)

    cases = [
        (code(0.3, -0.2), 5.0),
        (code(0.9, 0.0), 9.0),
        (code(1.0, 0.0), 10.0),
        (code(0.8, -0.4), 10.0),
        (code(0.0, 0.0), 10.0),
        (code(0.1, 0.3), 10.0),
        (code(None, -0.2), 10.0),
        (code(0.3, None), 10.0),
        (code(0.3, -0.2, invalid="invalid"), 10.0),
    ]
    with m4b.descriptor_heights():
        for each, _ in cases:
            assert glyphs_module._content_box("origin", "along", 10.0, 7.0, each) == (
                "the original's result"
            )
    assert [call[2] for call in calls] == pytest.approx([size for _, size in cases])
    assert all(call[:2] == ("origin", "along") and call[3] == 7.0 for call in calls)
    assert [call[4] for call in calls] == [each for each, _ in cases]


def blank(**changes) -> dict:
    """The entry of a file read, every count 0; `changes` replace keys of the entry and, for
    `agreement` and `render`, of those."""
    entry = {
        "page_count": 1,
        "glyph_count": 0,
        "word_count": 0,
        "line_count": 0,
        "glyphs": "0" * 64,
        "words": "0" * 64,
        "lines": "0" * 64,
        "unknown": {},
        "agreement": dict.fromkeys(m4b.AGREEMENT, 0),
        "render": dict.fromkeys(m4b.RENDER, 0),
        "pages_unknown": [],
    }
    for key, value in changes.items():
        entry[key] = {**entry[key], **value} if key in ("agreement", "render") else value
    return entry


def with_words(judged: int, correct: int, **more) -> dict:
    return blank(render={"V2 judged": judged, "V2 correct": correct, **more})


@pytest.mark.parametrize(
    ("judged", "correct", "status"),
    [
        (100, 100, 0),
        (100, 99, 0),
        (100, 98, 1),
        (1000, 990, 0),
        (1000, 989, 1),
        (200, 198, 0),
        (200, 197, 1),
        (1, 1, 0),
        (1, 0, 1),
        (0, 0, 1),
    ],
)
def test_24_at_least_99_percent_of_the_words_judged_have_to_be_correct(judged, correct, status):
    receipt = {"errors": {}, "files": {"a.pdf": with_words(judged, correct)}}
    assert m4b.exit_status(receipt) == status
    assert m4b.enough(correct, judged) == (status == 0)


def test_25_the_share_is_over_all_the_files():
    files = {"a.pdf": with_words(50, 50), "b.pdf": with_words(50, 48)}
    assert m4b.correct_share(files) == (98, 100)
    assert m4b.exit_status({"errors": {}, "files": files}) == 1
    files["b.pdf"] = with_words(50, 49)
    assert m4b.exit_status({"errors": {}, "files": files}) == 0


@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("all good", 0),
        ("an error", 1),
        ("a page unknown", 1),
        ("a page not mapped", 1),
        # A page whose characters PDFium does not give is counted, and fails nothing.
        ("a page without PDFium's characters", 0),
    ],
)
def test_26_one_file_that_fails_fails_the_run(case, status):
    good = with_words(100, 100)
    other = {
        "a page unknown": with_words(100, 100) | {"pages_unknown": ["3"]},
        "a page not mapped": with_words(100, 100, **{"pages not mapped": 1}),
        "a page without PDFium's characters": blank(
            render={"V2 judged": 100, "V2 correct": 100},
            agreement={"pages without PDFium's characters": 1},
        ),
    }.get(case, good)
    receipt = {"errors": {}, "files": {"a.pdf": good, "b.pdf": other}}
    if case == "an error":
        receipt["errors"] = {"c.pdf": "RuntimeError"}
    assert m4b.exit_status(receipt) == status


def test_27_the_descriptors_height_doing_better_fails_the_run():
    """Where it does, the question goes back to the maintainer (ROADMAP.md, M4b): the run exits
    1 though every word judged is correct."""

    def receipt(only_v2: int, only_d: int) -> dict:
        render = {
            "V2 judged": 100,
            "V2 correct": 100,
            "good in V2 only": only_v2,
            "good in D only": only_d,
        }
        return {"errors": {}, "files": {"a.pdf": blank(render=render)}}

    assert m4b.exit_status(receipt(35, 3)) == 0
    assert m4b.exit_status(receipt(4, 4)) == 0
    assert m4b.exit_status(receipt(3, 35)) == 1
    assert m4b.exit_status(receipt(0, 1)) == 1


def test_28_a_run_writes_the_receipt_and_the_summary(monkeypatch, tmp_path, capsys):
    data = written()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["gate"] == "m4b"
    assert receipt["files"] == {"a.pdf": m4b.read_file(data)}
    assert (receipt["errors"], receipt["partial"]) == ({}, False)
    environment = receipt["environment"]
    assert environment["commit"] == "0" * 40
    assert set(environment) == {"commit", "date", "engine_info", "machine", "platform", "python"}
    assert environment["engine_info"]["numpy"] == np.__version__
    assert environment["engine_info"]["scipy"] == scipy.__version__
    assert environment["engine_info"]["font_policy"] == "embedded-only"
    lines = capsys.readouterr().out.splitlines()
    word_boxes = (
        "{words}: 2 words, 0 not horizontal, 0 outside the page, 2 measured;"
        " 1 all invisible, not judged; judged 1: correct 1, without ink of their own 0,"
        " failing vertically 0, failing horizontally 0, into another line 0;"
        " unattributed ink: 0 components, 0 pixels"
    )
    assert lines == [
        "ok a.pdf 1 pages 5 glyphs 2 words 2 lines",
        "1 files, 1 pages: 5 glyphs, 2 words, 2 lines",
        "unknown: none",
        "pages unknown as a whole: 0 in 0 files",
        "PDFium's characters, among the glyphs of V2: same text 4,"
        " line-end hyphen marker 0, soft hyphen read as a hyphen 0, same text under NFKC 0,"
        " V2 text unknown 0, same first character 0, other text at the place 0,"
        " no glyph at the place 0",
        "glyphs of V2 that are no white space: a PDFium character at the place 4,"
        " drawn again 0, invisible 0, other 0",
        "counted apart: PDFium: text unknown 0, PDFium: origin unknown 0,"
        " V2: white space 1, V2: origin unknown 0, pages without PDFium's characters 0",
        "pages measured 1, pages not mapped 0, pages where D groups otherwise 0",
        "word boxes of " + word_boxes.format(words="V2, one em at the least"),
        "word boxes of " + word_boxes.format(words="D, the descriptor's height"),
        "good in V2 and not in D: 0, good in D and not in V2: 0",
        "word boxes correct: 1 of 1 (100.00%), at least 99%: yes",
        "the descriptor's height does better: no",
        f"0 errors, {receipt['seconds']} s; receipt {out}",
    ]


def test_29_the_summary_counts_over_the_files(capsys):
    first = blank(
        page_count=3,
        glyph_count=10,
        word_count=4,
        line_count=2,
        unknown={"glyph box unreadable": 3},
        agreement={
            "PDFium: same text": 8,
            "PDFium: no glyph at the place": 1,
            "V2: other": 2,
            "V2: white space": 5,
            "V2: text unknown invalid-in-file": 1,
        },
        render={
            "pages measured": 3,
            "V2 words": 4,
            "V2 measured": 3,
            "V2 not horizontal": 1,
            "V2 judged": 3,
            "V2 correct": 2,
            "V2 judged without ink of their own": 1,
            "V2 unattributed components": 2,
            "V2 unattributed pixels": 9,
            "D words": 4,
            "D measured": 3,
            "D not horizontal": 1,
            "D judged": 3,
            "D correct": 2,
            "D failing vertically": 1,
            "good in V2 only": 1,
            "good in D only": 3,
        },
    )
    second = blank(
        page_count=2,
        pages_unknown=["1"],
        unknown={"page as a whole unreadable": 1},
        agreement={"V2: text unknown invalid-in-file": 2, "pages without PDFium's characters": 1},
        render={"pages not mapped": 1, "V2 judged": 1, "V2 correct": 0, "V2 into another line": 1},
    )
    m4b.print_summary(3, {"a.pdf": first, "b.pdf": second})
    assert capsys.readouterr().out.splitlines() == [
        "3 files, 5 pages: 10 glyphs, 4 words, 2 lines",
        "unknown: glyph box unreadable 3, page as a whole unreadable 1",
        "pages unknown as a whole: 1 in 1 files",
        "PDFium's characters, among the glyphs of V2: same text 8,"
        " line-end hyphen marker 0, soft hyphen read as a hyphen 0, same text under NFKC 0,"
        " V2 text unknown 0, same first character 0, other text at the place 0,"
        " no glyph at the place 1",
        "glyphs of V2 that are no white space: a PDFium character at the place 0,"
        " drawn again 0, invisible 0, other 2",
        "counted apart: PDFium: text unknown 0, PDFium: origin unknown 0,"
        " V2: white space 5, V2: origin unknown 0, pages without PDFium's characters 1,"
        " V2: text unknown invalid-in-file 3",
        "pages measured 3, pages not mapped 1, pages where D groups otherwise 0",
        "word boxes of V2, one em at the least: 4 words, 1 not horizontal, 0 outside the page,"
        " 3 measured; 0 all invisible, not judged; judged 4: correct 2,"
        " without ink of their own 1, failing vertically 0, failing horizontally 0,"
        " into another line 1; unattributed ink: 2 components, 9 pixels",
        "word boxes of D, the descriptor's height: 4 words, 1 not horizontal, 0 outside the page,"
        " 3 measured; 0 all invisible, not judged; judged 3: correct 2,"
        " without ink of their own 0, failing vertically 1, failing horizontally 0,"
        " into another line 0; unattributed ink: 0 components, 0 pixels",
        "good in V2 and not in D: 1, good in D and not in V2: 3",
        "word boxes correct: 2 of 4 (50.00%), at least 99%: no",
        "the descriptor's height does better: yes",
    ]


def test_30_the_descriptors_height_does_better_where_more_words_are_good_in_it():
    def files(only_v2: int, only_d: int) -> dict:
        return {"a.pdf": blank(render={"good in V2 only": only_v2, "good in D only": only_d})}

    assert m4b.descriptor_better(files(35, 3)) is False
    assert m4b.descriptor_better(files(3, 35)) is True
    assert m4b.descriptor_better(files(4, 4)) is False
    assert m4b.descriptor_better(files(0, 1)) is True
    assert m4b.descriptor_better({"a.pdf": blank(), "b.pdf": blank()}) is False
    both = {"a.pdf": files(5, 0)["a.pdf"], "b.pdf": files(0, 6)["a.pdf"]}
    assert m4b.descriptor_better(both) is True


def test_31_no_word_judged_is_no_share(capsys):
    m4b.print_summary(1, {"a.pdf": blank()})
    output = capsys.readouterr().out.splitlines()
    assert "word boxes correct: 0 of 0 (n/a), at least 99%: no" in output


def test_32_only_the_files_named_and_the_receipt_partial(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": written(), "b.pdf": written()})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (list(receipt["files"]), receipt["partial"]) == (["b.pdf"], True)


def test_33_an_error_is_recorded_and_the_run_fails(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": written(), "b.pdf": written()})
    real = m4b.read_file
    calls: list[bytes] = []

    def fails_for_the_first(data: bytes) -> dict:
        calls.append(data)
        if len(calls) == 1:
            raise RuntimeError("no")
        return real(data)

    monkeypatch.setattr(m4b, "read_file", fails_for_the_first)
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (receipt["errors"], list(receipt["files"])) == ({"a.pdf": "RuntimeError"}, ["b.pdf"])


@pytest.mark.parametrize(
    "refusal", ["a file missing", "another SHA-256", "an id not listed", "fonts of the host"]
)
def test_34_a_refused_run_writes_no_receipt(monkeypatch, tmp_path, refusal):
    data = written()
    listed = {"a.pdf": data + b"\n"} if refusal == "another SHA-256" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data}, listed)
    if refusal == "a file missing":
        (folder / "a.pdf").unlink()
    if refusal == "fonts of the host":
        monkeypatch.setattr(m4b, "engine_info", lambda: SimpleNamespace(font_policy="host"))
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()


def test_35_a_commit_is_forty_lowercase_hex_digits(tmp_path):
    with pytest.raises(SystemExit) as raised:
        m4b.main(["--commit", "A" * 40, "--out", str(tmp_path / "receipt.json")])
    assert raised.value.code == 2
    assert not (tmp_path / "receipt.json").exists()


def test_36_one_unknown_page_fails_the_run_and_is_listed_in_the_receipt(monkeypatch, tmp_path):
    deep = file_of(b"/X Do", resources=DEEP_RESOURCES, objects=deep_forms())
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": written(), "b.pdf": deep})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["files"]["a.pdf"]["pages_unknown"] == []
    assert receipt["files"]["b.pdf"]["pages_unknown"] == ["0"]
    assert receipt["errors"] == {}
