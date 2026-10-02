"""The M4a gate: what it counts, what it compares, and when it fails.

The gate (bench/gates/m4a.py) reads glyphs(), words() and lines(), digests
them, and compares the characters the glyphs hold with PDFium's own. Files
are built with the byte-level builder; the counts expected are what was
written.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pdfacts
from pdfacts import GlyphRef, Point, Quad, Rect, Unknown, UnknownReason

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m4a  # noqa: E402

from synthetic import cases  # noqa: E402
from synthetic.builder import build, stream  # noqa: E402
from synthetic.cases import TEXT_DESCRIPTOR, TEXT_FONT  # noqa: E402

APPEARANCE = (
    b"/Type /XObject /Subtype /Form /BBox [0 0 20 10] /Resources << /Font << /F1 5 0 R >> >>"
)
# Code 0x45 to U+2003, an em space.
EM_SPACE_MAP = stream(
    b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Gate def"
    b" 1 begincodespacerange <00> <FF> endcodespacerange"
    b" 1 beginbfchar <45> <2003> endbfchar"
    b" endcmap CMapName currentdict /CMap defineresource pop end end"
)


def page_file(
    content: bytes,
    *,
    resources: bytes = b"/Font << /F1 5 0 R >>",
    annots: bytes = b"",
    objects=None,
) -> bytes:
    """A one-page file of 400 x 300. Its font object 5 is 1000 thousandths wide
    for every character: at size 10, a glyph is 10 wide."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
    page += b" /Resources << " + resources + b" >>"
    if annots:
        page += b" /Annots " + annots
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: page + b" >>",
            5: TEXT_FONT + b" >>",
            6: TEXT_DESCRIPTOR,
            **(objects or {}),
        },
        1,
    )


def written() -> bytes:
    """Each text object at size 10, Tw, Tc and Tr kept within q and Q:
    - "AB", a line;
    - "X" running up the page: not upright;
    - "EF" in render mode 3, then visible at one place: PDFium leaves the
      second out, over invisible text;
    - "GH" twice at one place: PDFium leaves the second out;
    - "A B" at 9 Tw: a new line after white space, 0.9 forward;
    - [(J ) 1000 (K)]: the space at 110, its box to 120, K at 110: a new line
      after white space, 1 back;
    - "LM" at 8 Tc: a new line after another glyph, 0.8 forward;
    - [(NP) 1800 (Q)]: Q at 102 after P ends at 120: a new line after another
      glyph, 1.8 back;
    - "UV" in render mode 7, then visible at one place: as "EF";
    - a text object of one space: PDFium leaves it out, and it has no area;
    - [(RE) -900 (S)] in /F2, whose E is an em space: a new line after white
      space, 0.9 forward;
    - "YZ" from x 200, then "WX" from x 100 on the same baseline: PDFium's text
      page puts W and X first;
    - "T" in a form.
    Annotation 0's appearance draws text; annotation 1's a rectangle."""
    return page_file(
        b"BT /F1 10 Tf 100 270 Td (AB) Tj ET"
        b" BT /F1 10 Tf 0 1 -1 0 300 100 Tm (X) Tj ET"
        b" q BT /F1 10 Tf 3 Tr 100 240 Td (EF) Tj ET Q BT /F1 10 Tf 100 240 Td (EF) Tj ET"
        b" BT /F1 10 Tf 200 240 Td (GH) Tj ET BT /F1 10 Tf 200 240 Td (GH) Tj ET"
        b" q BT /F1 10 Tf 9 Tw 100 210 Td (A B) Tj ET Q"
        b" BT /F1 10 Tf 100 180 Td [(J ) 1000 (K)] TJ ET"
        b" q BT /F1 10 Tf 8 Tc 100 150 Td (LM) Tj ET Q"
        b" BT /F1 10 Tf 100 120 Td [(NP) 1800 (Q)] TJ ET"
        b" q BT /F1 10 Tf 7 Tr 200 270 Td (UV) Tj ET Q BT /F1 10 Tf 200 270 Td (UV) Tj ET"
        b" BT /F1 10 Tf 300 150 Td ( ) Tj ET"
        b" BT /F2 10 Tf 100 60 Td [(RE) -900 (S)] TJ ET"
        b" BT /F1 10 Tf 200 30 Td (YZ) Tj ET BT /F1 10 Tf 100 30 Td (WX) Tj ET"
        b" /Fm Do",
        resources=b"/Font << /F1 5 0 R /F2 11 0 R >> /XObject << /Fm 13 0 R >>",
        annots=b"[7 0 R 9 0 R]",
        objects={
            7: b"<< /Type /Annot /Subtype /Square /Rect [300 250 320 260] /AP << /N 8 0 R >> >>",
            8: stream(b"BT /F1 10 Tf 0 0 Td (Z) Tj ET", APPEARANCE),
            9: b"<< /Type /Annot /Subtype /Square /Rect [300 200 320 210] /AP << /N 10 0 R >> >>",
            10: stream(b"0 0 10 10 re f", APPEARANCE),
            11: TEXT_FONT + b" /ToUnicode 12 0 R >>",
            12: EM_SPACE_MAP,
            13: stream(
                b"BT /F1 10 Tf 300 30 Td (T) Tj ET",
                b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
                b" /Resources << /Font << /F1 5 0 R >> >>",
            ),
        },
    )


# AB, X, EF, GH, "A B", "J K", LM, NPQ, UV, "R S", YZ, WX, T: 28 glyphs. X
# is in no word; the others make 17 words, each a line of its own.
WRITTEN_KNOWN = {
    "annotations with text in their appearance": 1,
    "glyphs not upright": 1,
    "left out": 3,
    "left out over invisible text": 2,
}
WRITTEN_BREAKS = {
    "after another glyph back": 1,
    "after another glyph forward": 1,
    "after white space back": 1,
    "after white space forward": 2,
}
NO_BREAKS = dict.fromkeys(WRITTEN_BREAKS, 0)
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


def corpus(tmp_path, monkeypatch, files: dict[str, bytes], listed=None) -> Path:
    """`files` in a folder, and a manifest that lists them, ids 01, 02, ...;
    `listed` gives other bytes to hash for a file."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "file", "sha256"])
        for number, (name, data) in enumerate(files.items(), 1):
            (folder / name).write_bytes(data)
            hashed = (listed or {}).get(name, data)
            writer.writerow([f"{number:02d}", name, hashlib.sha256(hashed).hexdigest()])
    monkeypatch.setattr(m4a, "MANIFEST", manifest)
    return folder


def run(folder: Path, out: Path, *more: str) -> int:
    return m4a.main(["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder), *more])


def test_1_a_file_is_counted_as_written():
    entry = m4a.read_file(written())
    assert (entry["page_count"], entry["glyph_count"]) == (1, 28)
    assert (entry["word_count"], entry["line_count"]) == (17, 17)
    assert entry["unknown"] == {}
    assert entry["known"] == WRITTEN_KNOWN
    assert entry["breaks"] == WRITTEN_BREAKS
    assert (entry["pdfium_differences"], entry["pages_not_compared"]) == ([], 0)
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 0


def test_2_a_new_line_between_two_text_objects_is_not_counted():
    """ "CD" 0.8 after "AB" ends, in a text object of its own."""
    data = page_file(b"BT /F1 10 Tf 100 200 Td (AB) Tj ET BT /F1 10 Tf 128 200 Td (CD) Tj ET")
    entry = m4a.read_file(data)
    assert entry["line_count"] == 2
    assert entry["breaks"] == NO_BREAKS


@pytest.mark.parametrize(
    ("content", "breaks"),
    [
        (b"[(AB) -800 (CD)] TJ", {**NO_BREAKS, "after another glyph forward": 1}),
        (b"[(AB) -787.5 (CD)] TJ", NO_BREAKS),
        (b"[(AB) 800 (CD)] TJ", {**NO_BREAKS, "after another glyph back": 1}),
        (b"[(AB) 787.5 (CD)] TJ", NO_BREAKS),
    ],
)
def test_3_a_new_line_is_a_step_of_0_8_or_more(content, breaks):
    entry = m4a.read_file(page_file(b"BT /F1 10 Tf 100 200 Td " + content + b" ET"))
    assert entry["breaks"] == breaks


@pytest.mark.parametrize(
    ("name", "kind"), [("glyphs", pdfacts.Glyph), ("words", pdfacts.Word), ("lines", pdfacts.Line)]
)
def test_4_each_digest_holds_every_value(monkeypatch, name, kind):
    """Each field of the page's first fact changed in turn: its digest changes,
    and no other."""
    assert set(OTHER[name]) == {field.name for field in dataclasses.fields(kind)}
    before = m4a.read_file(written())
    real = getattr(pdfacts, name)
    for field, other in OTHER[name].items():

        def changed(document, page_index, field=field, other=other):
            found = real(document, page_index)
            return (dataclasses.replace(found[0], **{field: other}), *found[1:])

        monkeypatch.setattr(pdfacts, name, changed)
        after = m4a.read_file(written())
        assert [key for key in ("glyphs", "words", "lines") if before[key] != after[key]] == [
            name
        ], field


def test_5_an_unknown_value_by_its_reason_and_detail():
    def digest(value) -> str:
        made = m4a.Digest()
        m4a._put(made, value)
        return made.hexdigest()

    first = Unknown(UnknownReason.UNREADABLE, "one")
    assert digest(first) != digest(Unknown(UnknownReason.UNREADABLE, "two"))
    assert digest(first) != digest(Unknown(UnknownReason.INVALID, "one"))


def test_6_a_page_unknown_as_a_whole_is_counted_not_compared_and_fails():
    """Forms nested 41 deep: PDFium reads 40 levels, so the page is unknown."""
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300] /Resources << /Font << /F1 5 0 R >>"
    chain = {}
    for level in range(41):
        last = level == 40
        content = b"BT /F1 10 Tf 100 200 Td (A) Tj ET" if last else b"/X Do"
        held = b">>" if last else b" /XObject << /X %d 0 R >> >>" % (101 + level)
        chain[100 + level] = stream(content, form + held)
    data = page_file(
        b"BT /F1 10 Tf 100 250 Td (B) Tj ET /X Do",
        resources=b"/Font << /F1 5 0 R >> /XObject << /X 100 0 R >>",
        objects=chain,
    )
    entry = m4a.read_file(data)
    assert entry["unknown"] == {"page as a whole unreadable": 1}
    assert (entry["glyph_count"], entry["pages_not_compared"]) == (0, 1)
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_7_a_page_with_a_glyph_of_unknown_text_is_not_compared_and_fails():
    """A Type 0 font, Identity-H, without /ToUnicode: the codes have no text."""
    data = page_file(
        b"BT /F2 10 Tf 100 200 Td <00410042> Tj ET",
        resources=b"/Font << /F2 7 0 R >>",
        objects={
            7: b"<< /Type /Font /Subtype /Type0 /BaseFont /Gate /Encoding /Identity-H"
            b" /DescendantFonts [8 0 R] >>",
            8: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Gate /CIDSystemInfo"
            b" << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R"
            b" /DW 1000 >>",
        },
    )
    entry = m4a.read_file(data)
    assert entry["unknown"] == {
        "glyph text unreadable": 2,
        "line text unreadable": 1,
        "word text unreadable": 1,
    }
    assert (entry["glyph_count"], entry["pages_not_compared"]) == (2, 1)
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_8_a_glyph_pdfacts_drops_is_a_difference(monkeypatch):
    real = pdfacts.glyphs

    def without_the_last(document, page_index):
        return real(document, page_index)[:-1]

    monkeypatch.setattr(pdfacts, "glyphs", without_the_last)
    entry = m4a.read_file(written())
    assert entry["pdfium_differences"] == ["0"]
    assert entry["breaks"] == NO_BREAKS
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def _first_changed(found, **changes):
    return (dataclasses.replace(found[0], **changes), *found[1:])


@pytest.mark.parametrize(
    ("change", "data"),
    [
        # Another code: Z for A.
        (lambda found: _first_changed(found, text="Z"), written),
        # Another origin.
        (lambda found: _first_changed(found, origin=Point(0.0, 0.0)), written),
        # The second code unit of a ligature: "fj" for "fi".
        (
            lambda found: _first_changed(found, text="fj"),
            cases.TEXT["a ligature PDFium expands is one glyph"],
        ),
        # A and B as one glyph at A's origin: B's character stands elsewhere.
        (lambda found: _first_changed(found, text="AB")[:1] + found[2:], written),
    ],
    ids=["another code", "another origin", "a ligature's second unit", "two glyphs as one"],
)
def test_9_a_glyph_unlike_pdfiums_characters_is_a_difference(monkeypatch, change, data):
    real = pdfacts.glyphs

    def changed(document, page_index):
        return change(real(document, page_index))

    monkeypatch.setattr(pdfacts, "glyphs", changed)
    made = data()
    assert m4a.read_file(made if isinstance(made, bytes) else made.data)["pdfium_differences"] == [
        "0"
    ]


def test_10_a_run_writes_the_receipt_and_the_summary(monkeypatch, tmp_path, capsys):
    data = written()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["gate"] == "m4a"
    assert receipt["files"] == {"a.pdf": m4a.read_file(data)}
    assert (receipt["errors"], receipt["partial"]) == ({}, False)
    assert receipt["environment"]["commit"] == "0" * 40
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "ok a.pdf 1 pages 28 glyphs 17 words 17 lines",
        "1 files, 1 pages: 28 glyphs, 17 words, 17 lines",
        "unknown: none",
        "not given: glyphs not upright 1 in 1 files,"
        " annotations with text in their appearance 1 in 1 files",
        "left out by PDFium as drawn again: text objects 3 in 1 files,"
        " over invisible text 2 in 1 files",
        "new lines inside one text object: after white space, forward 2 in 1 files,"
        " back 1 in 1 files; after another glyph, forward 1 in 1 files, back 1 in 1 files",
        "PDFium's own count of characters: equal in 1 of 1 files; not compared: 0 pages",
        f"0 errors, {receipt['seconds']} s; receipt {out}",
    ]


def test_11_the_summary_counts_over_the_files(capsys):
    good = m4a.read_file(written())
    other = {
        **good,
        "unknown": {"glyph box unreadable": 3},
        "known": {"left out": 4},
        "breaks": {},
        "pdfium_differences": ["0"],
    }
    not_compared = {**good, "pages_not_compared": 1}
    m4a.print_summary(4, {"a.pdf": good, "b.pdf": other, "c.pdf": not_compared})
    assert capsys.readouterr().out.splitlines() == [
        "4 files, 3 pages: 84 glyphs, 51 words, 51 lines",
        "unknown: glyph box unreadable 3",
        "not given: glyphs not upright 2 in 2 files,"
        " annotations with text in their appearance 2 in 2 files",
        "left out by PDFium as drawn again: text objects 10 in 3 files,"
        " over invisible text 4 in 2 files",
        "new lines inside one text object: after white space, forward 4 in 2 files,"
        " back 2 in 2 files; after another glyph, forward 2 in 2 files, back 2 in 2 files",
        "PDFium's own count of characters: equal in 1 of 4 files; not compared: 1 pages",
    ]


def test_12_only_the_files_named_and_the_receipt_partial(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": written(), "b.pdf": written()})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (list(receipt["files"]), receipt["partial"]) == (["b.pdf"], True)


def test_13_an_error_is_recorded_and_the_run_fails(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": written(), "b.pdf": written()})
    real = m4a.read_file

    def fails_for_the_first(data: bytes) -> dict:
        calls.append(data)
        if len(calls) == 1:
            raise RuntimeError("no")
        return real(data)

    calls: list[bytes] = []
    monkeypatch.setattr(m4a, "read_file", fails_for_the_first)
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (receipt["errors"], list(receipt["files"])) == ({"a.pdf": "RuntimeError"}, ["b.pdf"])


@pytest.mark.parametrize(
    "refusal", ["a file missing", "another SHA-256", "an id not listed", "fonts of the host"]
)
def test_14_a_refused_run_writes_no_receipt(monkeypatch, tmp_path, refusal):
    data = written()
    listed = {"a.pdf": data + b"\n"} if refusal == "another SHA-256" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data}, listed)
    if refusal == "a file missing":
        (folder / "a.pdf").unlink()
    if refusal == "fonts of the host":
        monkeypatch.setattr(m4a, "engine_info", lambda: SimpleNamespace(font_policy="host"))
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()


def test_15_a_commit_is_forty_lowercase_hex_digits(tmp_path):
    with pytest.raises(SystemExit) as raised:
        m4a.main(["--commit", "A" * 40, "--out", str(tmp_path / "receipt.json")])
    assert raised.value.code == 2
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("failing", ["a difference", "not compared"])
def test_16_one_file_that_fails_fails_the_run(failing):
    good = m4a.read_file(written())
    bad = {
        "a difference": {**good, "pdfium_differences": ["0"]},
        "not compared": {**good, "pages_not_compared": 1},
    }[failing]
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": good, "b.pdf": good}}) == 0
    assert m4a.exit_status({"errors": {}, "files": {"a.pdf": good, "b.pdf": bad}}) == 1


def test_17_the_gate_takes_none_of_the_c_librarys_trigonometry(monkeypatch):
    """The gate measures the steps as words and lines are made, with the
    vector along the baseline that pdfacts computes."""
    before = m4a.read_file(written())

    def fails(*arguments):
        raise AssertionError("the C library's trigonometry was taken")

    for name in ("atan", "atan2", "sin", "cos", "tan"):
        monkeypatch.setattr(math, name, fails)
    assert m4a.read_file(written()) == before
