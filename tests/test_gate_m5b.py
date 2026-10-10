"""The M5b gate: what it counts, what it compares, and when it fails.

The gate (bench/gates/m5b.py) reads the annotations other than widgets and
the words and lines of TextRules.V3 of every page, digests them for comparing
platforms, and applies an EditSession with Decrypt alone and one with Rewrite
alone, comparing each with the function it replaces. Files are built with the
byte-level builder; the numbers expected are what was written.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pdfacts
from pdfacts import Rect, Unknown, UnknownReason

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import compare  # noqa: E402
import m5b  # noqa: E402

from synthetic.builder import build, stream  # noqa: E402
from synthetic.cases import TEXT_DESCRIPTOR, TEXT_FONT  # noqa: E402
from synthetic.encryption import encrypt  # noqa: E402

# One page of 400 x 300 with the text "hello world" at one text object: two
# words of one line, the numbers taken from how the file is written.
LINK = (
    b"<< /Type /Annot /Subtype /Link /Rect [10 20 110 40] /F 4 /Contents (a note)"
    b" /C [0.5 0.5 0.5] /AP << /N 8 0 R >> >>"
)


def page_file(annots: bytes, objects=None) -> bytes:
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(b"BT /F1 10 Tf 100 200 Td (hello world) Tj ET"),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >>" + annots,
            5: TEXT_FONT + b" >>",
            6: TEXT_DESCRIPTOR,
            **(objects or {}),
        },
        1,
    )


ANNOTATED = page_file(b" /Annots [7 0 R] >>", {7: LINK, 8: stream(b"q Q")})
# The same page without the annotation: its text is the same.
PLAIN = page_file(b" >>")
# /Annots a number: the page's annotations are unknown, its text is not.
BAD_ANNOTS = page_file(b" /Annots 5 >>")
ENCRYPTED = encrypt(PLAIN, user="", owner="o")


def test_1_a_page_of_each_fact():
    entry = m5b.read_file(ANNOTATED)
    assert entry["page_count"] == 1
    assert (entry["annotation_count"], entry["word_count"], entry["line_count"]) == (1, 2, 1)
    assert entry["pages_unknown"] == []
    assert entry["session"] == {"rewrite equal": True}
    for kind in ("annotations", "words", "lines"):
        assert re.fullmatch(r"[0-9a-f]{64}", entry[kind])
    plain = m5b.read_file(PLAIN)
    assert (plain["annotation_count"], plain["word_count"], plain["line_count"]) == (0, 2, 1)
    assert plain["words"] == entry["words"]  # the annotation changes no text
    assert plain["annotations"] != entry["annotations"]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("a\nb", "a b"),
        ("ab", "a b"),
        ("ab", 2),
        (1, 1.0),
        (True, 1),
        (None, "none"),
        ((1, 2), (1, (2,))),
        (Rect(0, 0, 1, 1), Rect(0, 0, 1, 2)),
        (Unknown(UnknownReason.INVALID, "a"), Unknown(UnknownReason.INVALID, "b")),
    ],
    ids=[
        "the separator inside a string",
        "a space and another text",
        "a text and an int",
        "an int and a float",
        "a bool and an int",
        "none and a text",
        "nesting",
        "a rectangle and one a point taller",
        "two reasons that differ in their detail",
    ],
)
def test_2_the_digest_tells_values_apart(one, other):
    first, second = m5b.Digest(), m5b.Digest()
    m5b._put(first, one)
    m5b._put(second, other)
    assert first.hexdigest() != second.hexdigest()


def test_3_a_page_whose_annotations_are_unknown():
    entry = m5b.read_file(BAD_ANNOTS)
    assert entry["pages_unknown"] == ["0"]  # the page once, not once per kind
    assert entry["annotation_count"] == 0
    assert entry["word_count"] == 2  # the text of that page is known
    assert entry["annotations"] != m5b.read_file(PLAIN)["annotations"]
    assert m5b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1
    assert m5b.exit_status({"errors": {}, "files": {"a.pdf": m5b.read_file(PLAIN)}}) == 0


def test_4_an_encrypted_file():
    """The session as decrypt() equals it; the rewrite is not compared, for an
    encrypted write is never the same twice."""
    entry = m5b.read_file(ENCRYPTED)
    assert entry["session"] == {"decrypt equal": True}


def test_5_a_session_not_equal_fails(monkeypatch):
    """recover() made to return other bytes: the session's rewrite is not the
    function's result, and the run fails."""
    recover = pdfacts.recover

    def other(data):
        return dataclasses.replace(recover(data), data=b"%PDF-other")

    monkeypatch.setattr(pdfacts, "recover", other)
    entry = m5b.read_file(PLAIN)
    assert entry["session"] == {"rewrite equal": False}
    assert m5b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


def test_6_a_session_with_nothing_to_compare_fails(monkeypatch):
    """Both functions made to refuse the file: the session is empty, which is
    a failure, not a vacuous pass."""

    def refused(data):
        raise pdfacts.DamagedFileError()

    monkeypatch.setattr(pdfacts, "decrypt", refused)
    monkeypatch.setattr(pdfacts, "recover", refused)
    entry = m5b.read_file(PLAIN)
    assert entry["session"] == {}
    assert m5b.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1


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
    monkeypatch.setattr(m5b, "MANIFEST", manifest)
    return folder


def run(folder: Path, out: Path, *more: str) -> int:
    return m5b.main(["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder), *more])


def test_7_the_receipt(tmp_path, monkeypatch, capsys):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": ANNOTATED, "b.pdf": ENCRYPTED})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("ok a.pdf 1 pages, 0 unknown, session {'rewrite equal': True}\n")
    assert "ok b.pdf 1 pages, 0 unknown, session {'decrypt equal': True}\n" in printed
    assert (
        # Both files hold the same text of one page: 2 words and 1 line each.
        "2 files, 2 pages: 1 annotation, 4 word, 2 line, 0 pages unknown;"
        " the session as decrypt() in 1 files, as recover() in 1\n" in printed
    )
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["gate"] == "m5b"
    assert receipt["partial"] is False
    assert receipt["errors"] == {}
    assert sorted(receipt["files"]) == ["a.pdf", "b.pdf"]
    assert receipt["environment"]["commit"] == "0" * 40
    assert receipt["environment"]["engine_info"]["font_policy"] == "embedded-only"


def test_8_an_error_fails_the_run(tmp_path, monkeypatch):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": b"%PDF-1.7\nnothing"})
    out = tmp_path / "receipt.json"
    assert run(folder, out) == 1
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["errors"] == {"b.pdf": "DamagedFileError"}


def test_9_two_receipts_compared(tmp_path, monkeypatch, capsys):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": ANNOTATED, "b.pdf": ENCRYPTED})
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert run(folder, first) == run(folder, second) == 0
    assert compare.main([str(first), str(second)]) == 0
    receipt = json.loads(second.read_text(encoding="utf-8"))
    receipt["files"]["a.pdf"]["word_count"] = 3
    second.write_text(json.dumps(receipt), encoding="utf-8")
    capsys.readouterr()
    assert compare.main([str(first), str(second)]) == 1
    assert "| a.pdf | identical | differs | identical | identical |" in capsys.readouterr().out


def test_10_only_the_files_named(tmp_path, monkeypatch):
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN, "b.pdf": ENCRYPTED})
    out = tmp_path / "receipt.json"
    assert run(folder, out, "--only", "02") == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(receipt["files"]) == ["b.pdf"]
    assert receipt["partial"] is True


@pytest.mark.parametrize("refusal", ["another hash", "an id not listed", "fonts of the host"])
def test_11_a_run_refused(tmp_path, monkeypatch, refusal):
    listed = {"a.pdf": b"other"} if refusal == "another hash" else None
    folder = corpus(tmp_path, monkeypatch, {"a.pdf": PLAIN}, listed)
    if refusal == "fonts of the host":
        monkeypatch.setattr(m5b, "engine_info", lambda: SimpleNamespace(font_policy="host"))
    more = ["--only", "99"] if refusal == "an id not listed" else []
    out = tmp_path / "receipt.json"
    assert run(folder, out, *more) == 2
    assert not out.exists()
