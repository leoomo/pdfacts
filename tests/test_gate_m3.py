"""The M3 gate: what it counts, what it compares, and when it fails.

The gate (bench/gates/m3.py) reads paths() and images() twice and compares
their numbers with PDFium's own. Files are built with the byte-level builder;
the counts expected are what was written.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

import pdfacts

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m3  # noqa: E402

from synthetic.builder import build, stream  # noqa: E402

IMAGE = stream(
    bytes(6),
    b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
    b" /BitsPerComponent 8",
)
# PDFium knows no such filter: the pixels cannot be decoded.
BROKEN = stream(
    bytes(6),
    b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
    b" /BitsPerComponent 8 /Filter /NoSuchDecode",
)
CELL = stream(
    b"1 0 0 rg 0 0 5 5 re f",
    b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
    b" /BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
)
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100]"


def page_file(content: bytes, *, resources: bytes = b"", annots: bytes = b"", objects=None):
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
    if resources:
        page += b" /Resources " + resources
    if annots:
        page += b" /Annots " + annots
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: page + b" >>",
            **(objects or {}),
        },
        1,
    )


def drawn() -> bytes:
    """On the page: one path of two rectangles; a line; an image and one PDFium
    cannot decode; a form that draws a line; text; a rectangle in a pattern
    colour. Annotation 0 has no appearance; that of annotation 1 draws a curve
    and a rectangle, placed on /Rect twice as wide and as high, that of
    annotation 2 an image."""
    return page_file(
        b"10 20 50 30 re 60 20 50 30 re f 0 0 m 10 0 l S"
        b" q 30 0 0 20 5 5 cm /Im Do Q q 30 0 0 20 50 5 cm /Bad Do Q /Fm Do"
        b" BT /F1 12 Tf 100 100 Td (x) Tj ET"
        b" /Pattern cs /P0 scn 0 0 100 100 re f",
        resources=b"<< /XObject << /Im 5 0 R /Bad 13 0 R /Fm 10 0 R >> /Pattern << /P0 6 0 R >>"
        b" /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>",
        annots=b"[7 0 R 8 0 R 11 0 R]",
        objects={
            5: IMAGE,
            6: CELL,
            7: b"<< /Type /Annot /Subtype /Square /Rect [0 0 10 10] >>",
            8: b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 30] /AP << /N 9 0 R >> >>",
            9: stream(
                b"0 0 m 10 20 30 20 40 0 c S 0 0 5 5 re f",
                b"/Type /XObject /Subtype /Form /BBox [0 0 100 20]",
            ),
            10: stream(b"0 0 m 5 5 l S", FORM),
            11: b"<< /Type /Annot /Subtype /Square /Rect [300 200 350 225] /AP << /N 12 0 R >> >>",
            12: stream(
                b"50 0 0 25 0 0 cm /Im Do",
                b"/Type /XObject /Subtype /Form /BBox [0 0 50 25]"
                b" /Resources << /XObject << /Im 5 0 R >> >>",
            ),
            13: BROKEN,
        },
    )


DRAWN_COUNTS = {
    "annotations with facts": 2,
    "appearance curves": 1,
    "appearance images": 1,
    "appearance paths": 2,
    "appearance rectangle": 1,
    "page images": 2,
    "page line": 2,
    "page paths": 4,
    "page rectangle": 3,
}
# A pattern set in the page's content makes unknown the colour of the side of
# each path it may paint: the fill of the last rectangle alone
# (docs/conventions.md, "Paths and images"). The image PDFium cannot decode
# has no colour space and no bits per pixel. Annotation 1's /BBox of 100 x 20
# is placed on a /Rect of 200 x 20: the width of its stroked curve has no
# single value.
DRAWN_UNKNOWN = {
    "appearance line_width unreadable": 1,
    "page bits_per_pixel unreadable": 1,
    "page colour_space unreadable": 1,
    "page fill_colour unreadable": 1,
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
    monkeypatch.setattr(m3, "MANIFEST", manifest)
    return folder


def run(folder: Path, out: Path, *more: str) -> int:
    return m3.main(["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder), *more])


def test_1_a_file_is_counted_by_source_shape_and_unknown_value():
    entry = m3.read_file(drawn())
    assert entry["pages"] == 1
    assert entry["counts"] == DRAWN_COUNTS
    assert entry["unknown"] == DRAWN_UNKNOWN
    assert entry["stable"] is True
    assert entry["pdfium_differences"] == []
    assert (entry["pages_not_compared"], entry["annotations_not_compared"]) == (0, 0)


def test_2_a_page_unknown_as_a_whole_is_counted_not_compared_and_fails():
    """Forms nested 41 deep: PDFium reads 40 levels, so the page is unknown;
    its annotation, whose appearance PDFium does read, is not compared either."""
    chain = {}
    for level in range(41):
        last = level == 40
        content = b"0 0 m 1 0 l S" + (b"" if last else b" /X Do")
        held = b"" if last else b" /Resources << /XObject << /X %d 0 R >> >>" % (101 + level)
        chain[100 + level] = stream(content, FORM + held)
    data = page_file(
        b"/X Do",
        resources=b"<< /XObject << /X 100 0 R >> >>",
        annots=b"[5 0 R]",
        objects={
            **chain,
            5: b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 30] /AP << /N 6 0 R >> >>",
            6: stream(b"0 0 10 10 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 100 10]"),
        },
    )
    entry = m3.read_file(data)
    assert entry["counts"] == {}
    assert entry["unknown"] == {"page as a whole unreadable": 1}
    assert entry["pdfium_differences"] == []
    assert (entry["pages_not_compared"], entry["annotations_not_compared"]) == (1, 0)
    assert set(m3.pdfium_counts(data)) == {"0", "0 0"}
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_3_an_annotation_whose_facts_are_unknown_is_not_compared_and_fails():
    """/BBox without area: the annotation stands as one path and one image."""
    data = page_file(
        b"",
        annots=b"[5 0 R]",
        objects={
            5: b"<< /Type /Annot /Subtype /Square /Rect [10 10 210 30] /AP << /N 6 0 R >> >>",
            6: stream(b"0 0 10 10 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 0 10]"),
        },
    )
    entry = m3.read_file(data)
    assert entry["counts"]["appearance paths"] == entry["counts"]["appearance images"] == 1
    assert entry["pdfium_differences"] == []
    assert (entry["pages_not_compared"], entry["annotations_not_compared"]) == (0, 1)
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_4_a_path_pdfacts_drops_is_a_difference(monkeypatch):
    real = pdfacts.paths

    def without_the_last(document, page_index):
        return real(document, page_index)[:-1]

    monkeypatch.setattr(pdfacts, "paths", without_the_last)
    entry = m3.read_file(drawn())
    assert entry["pdfium_differences"] == ["0 1"]
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_5_images_pdfacts_adds_are_differences(monkeypatch):
    real = pdfacts.images

    def twice(document, page_index):
        return real(document, page_index) * 2

    monkeypatch.setattr(pdfacts, "images", twice)
    entry = m3.read_file(drawn())
    assert entry["pdfium_differences"] == ["0", "0 2"]


@pytest.mark.parametrize("name", ["paths", "images"])
def test_6_the_two_reads_are_compared(monkeypatch, name):
    real = getattr(pdfacts, name)
    calls = 0

    def other_the_second_time(document, page_index):
        nonlocal calls
        calls += 1
        found = real(document, page_index)
        if calls > document.page_count:
            moved = dataclasses.replace(found[0].ref, drawing_index=99)
            return (dataclasses.replace(found[0], ref=moved), *found[1:])
        return found

    monkeypatch.setattr(pdfacts, name, other_the_second_time)
    entry = m3.read_file(drawn())
    assert entry["stable"] is False
    assert entry["pdfium_differences"] == []
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_7_each_read_opens_the_file_anew():
    """Page 1's content has a /Length too long: once QPDF has read it, it has
    warned of a misread object, and page 0's colours are unknown too."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
            3: stream(b"0 0 m 10 0 l S"),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R >>",
            5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 6 0 R >>",
            6: b"<< /Length 999 >>\nstream\n0 0 m 10 0 l S\nendstream",
        },
        1,
    )
    with pdfacts.open(data) as document:
        before = pdfacts.paths(document, 0)
        pdfacts.paths(document, 1)
        assert pdfacts.paths(document, 0) != before
    entry = m3.read_file(data)
    assert entry["stable"] is True
    assert entry["unknown"] == {"page stroke_colour unreadable": 1}


def test_8_a_run_writes_the_receipt_and_the_summary(monkeypatch, tmp_path, capsys):
    data = drawn()
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["files"] == {"a.pdf": m3.read_file(data)}
    assert (receipt["errors"], receipt["partial"]) == ({}, False)
    assert receipt["environment"]["commit"] == "0" * 40
    lines = capsys.readouterr().out.splitlines()
    assert lines[:8] == [
        "ok a.pdf 1 pages 6 paths 3 images",
        "1 files, 1 pages, 2 annotations with facts",
        "page: paths 4, images 2, line 2, rectangle 3, quad 0, polyline 0, curves 0",
        "appearance: paths 2, images 1, line 0, rectangle 1, quad 0, polyline 0, curves 1",
        "unknown: appearance line_width unreadable 1, page bits_per_pixel unreadable 1,"
        " page colour_space unreadable 1, page fill_colour unreadable 1",
        "stable: 1 of 1 files",
        "PDFium's own count: equal in 1 of 1 files; not compared: 0 pages, 0 annotations",
        f"0 errors, {receipt['seconds']} s; receipt {out}",
    ]


def test_9_the_summary_counts_what_failed(capsys):
    good = m3.read_file(drawn())
    bad = {**good, "stable": False, "pdfium_differences": ["0"], "annotations_not_compared": 1}
    m3.print_summary(3, {"a.pdf": good, "b.pdf": bad})
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "3 files, 2 pages, 4 annotations with facts"
    assert lines[4:] == [
        "stable: 1 of 3 files",
        "PDFium's own count: equal in 1 of 3 files; not compared: 0 pages, 1 annotations",
    ]


def test_10_only_the_files_named_and_the_receipt_partial(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": drawn(), "b.pdf": drawn()})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (list(receipt["files"]), receipt["partial"]) == (["b.pdf"], True)


def test_11_an_error_is_recorded_and_the_run_fails(monkeypatch, tmp_path):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": drawn(), "b.pdf": drawn()})
    real = m3.read_file

    def fails_for_the_first(data: bytes) -> dict:
        calls.append(data)
        if len(calls) == 1:
            raise RuntimeError("no")
        return real(data)

    calls: list[bytes] = []
    monkeypatch.setattr(m3, "read_file", fails_for_the_first)
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert (receipt["errors"], list(receipt["files"])) == ({"a.pdf": "RuntimeError"}, ["b.pdf"])


@pytest.mark.parametrize("refusal", ["a file missing", "another SHA-256", "an id not listed"])
def test_12_a_refused_run_writes_no_receipt(monkeypatch, tmp_path, refusal):
    data = drawn()
    listed = {"a.pdf": data + b"\n"} if refusal == "another SHA-256" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": data}, listed)
    if refusal == "a file missing":
        (folder / "a.pdf").unlink()
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()


def test_13_a_commit_is_forty_lowercase_hex_digits(tmp_path):
    with pytest.raises(SystemExit) as raised:
        m3.main(["--commit", "A" * 40, "--out", str(tmp_path / "receipt.json")])
    assert raised.value.code == 2
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("failing", ["unstable", "a difference", "not compared"])
def test_14_one_file_that_fails_fails_the_run(failing):
    good = m3.read_file(drawn())
    bad = {
        "unstable": {**good, "stable": False},
        "a difference": {**good, "pdfium_differences": ["0"]},
        "not compared": {**good, "pages_not_compared": 1},
    }[failing]
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": good, "b.pdf": good}}) == 0
    assert m3.exit_status({"errors": {}, "files": {"a.pdf": good, "b.pdf": bad}}) == 1
