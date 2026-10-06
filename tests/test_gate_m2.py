"""The M2 gate: the referee's reading and the comparison it makes.

The referee (bench/gates/m2_referee.py) reads the dictionaries with pikepdf
and nothing of pdfacts; the gate (bench/gates/m2.py) compares it with
pdfacts. Files are built with the byte-level builder; expected values are
what was written.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

import pdfacts

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "gates"))

import m2  # noqa: E402
import m2_referee  # noqa: E402

from synthetic.builder import build  # noqa: E402

STREAM = b"<< /Length 0 >>\nstream\n\nendstream"


def widget(entries: bytes, rect: bytes = b"[10 10 110 30]") -> bytes:
    return b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect %s %s >>" % (rect, entries)


def forms_file(annots: dict[int, bytes], fields: bytes, objects=None) -> bytes:
    """A one-page file: `annots` in the page's /Annots, `fields` in /AcroForm /Fields."""
    listing = b"[" + b" ".join(b"%d 0 R" % number for number in sorted(annots)) + b"]"
    whole = {
        1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields %s >> >>" % fields,
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
        % listing,
    }
    whole.update(annots)
    if objects:
        whole.update(objects)
    return build(whole, 1)


def a1() -> bytes:
    """P4's A1: one merged text field as the only widget."""
    return forms_file({6: widget(b"/T (a) /FT /Tx /V (x) /TU (L)")}, fields=b"[6 0 R]")


def a2() -> bytes:
    """P4's A2: a radio group, widget 6 and 7 under field 8."""
    return forms_file(
        {
            6: widget(b"/Parent 8 0 R /AS /Off /AP << /N << /A 9 0 R /Off 9 0 R >> >>"),
            7: widget(b"/Parent 8 0 R /AS /B /AP << /N << /B 9 0 R /Off 9 0 R >> >>"),
            8: b"<< /T (p) /FT /Btn /Ff 32768 /V /B /Opt [(A) (B)] /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
        objects={9: STREAM},
    )


def a4() -> bytes:
    """P4's A4: two widgets under a text field; the first one's own /V counts for nothing."""
    return forms_file(
        {
            6: widget(b"/Parent 8 0 R /V ()"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Tx /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )


def test_1_the_referee_reads_a_merged_text_field():
    widgets, fields = m2_referee.read(a1())
    assert fields == 1
    (one,) = widgets
    assert one["page"] == 0
    assert one["index"] == 0
    assert one["object"] == [6, 0]
    assert one["field_ref"] == [6, 0]
    assert one["name"] == "a"
    assert one["partial_names"] == ["a"]
    assert one["type"] == "text"
    assert one["field_flags"] == 0
    assert one["value"] == "x"
    assert one["label"] == "L"
    assert one["rect"] == [10.0, 10.0, 110.0, 30.0]
    assert one["annotation_flags"] == 0
    assert one["in_form"] is True
    assert one["states"] is None
    assert one["state"] is None
    assert one["export_value"] is None
    assert one["default_value"] is None
    assert one["options"] is None
    assert one["border_colour"] is None
    assert one["background_colour"] is None
    assert one["border"] is None


def test_2_the_referee_reads_a_radio_group():
    widgets, fields = m2_referee.read(a2())
    assert fields == 1
    six, seven = widgets
    assert six["export_value"] == "A"
    assert seven["export_value"] == "B"
    assert six["states"] == [{"name": "41"}, {"name": "4f6666"}]
    assert seven["states"] == [{"name": "42"}, {"name": "4f6666"}]
    assert six["value"] == {"name": "42"}
    assert seven["value"] == {"name": "42"}
    assert six["field_ref"] == [8, 0]
    assert seven["field_ref"] == [8, 0]


def test_3_the_referee_inherits_from_the_terminal_field():
    widgets, _ = m2_referee.read(a4())
    six, seven = widgets
    assert six["value"] is None
    assert seven["value"] is None


@pytest.mark.parametrize(
    ("entries", "fact", "expected"),
    [
        (b"/T (a) /FT /Tx /TU (a\255b)", "label", "a\ufffdb"),
        (b"/T (a) /FT /Tx /TU <FEFF001B00640065001B004C>", "label", "L"),
        (b"/T (a) /FT /Btn /AS /M#F6", "state", {"name": "4df6"}),
    ],
)
def test_4_the_referees_decoding(entries, fact, expected):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    widgets, _ = m2_referee.read(data)
    assert widgets[0][fact] == expected


@pytest.mark.parametrize(
    "entries",
    [b"/T (c) /FT /Btn /V ()", b"/T (p) /FT /Btn /Ff 65536 /V (x)"],
    ids=["a checkbox", "a pushbutton"],
)
def test_5_the_referee_finds_the_unknown_values(entries):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    widgets, _ = m2_referee.read(data)
    assert widgets[0]["value"] == {"unknown": "invalid-in-file"}


@pytest.mark.parametrize(
    "entries",
    [b"/T (s) /FT /Sig /V 9 0 R", b"/T (t) /FT /Tx /V 9 0 R"],
    ids=["a signature field", "a text field written as a stream"],
)
def test_5b_the_referee_does_not_read_these_values(entries):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]", objects={9: STREAM})
    widgets, _ = m2_referee.read(data)
    assert widgets[0]["value"] == {"unknown": "not-read"}
    assert m2.check_file(data)["differences"] == []


def test_6_the_referee_uses_nothing_of_pdfacts():
    program = (
        "import sys\n"
        f"sys.path[:0] = [{str(ROOT / 'bench' / 'gates')!r}, {str(ROOT / 'tests')!r}]\n"
        "from synthetic.builder import build\n"
        "import m2_referee\n"
        "data = build(\n"
        "    {\n"
        "        1: b'<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>',\n"
        "        2: b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>',\n"
        "        4: b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
        " /Annots [6 0 R] >>',\n"
        "        6: b'<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
        " /T (a) /FT /Tx /V (x) /TU (L) >>',\n"
        "    },\n"
        "    1,\n"
        ")\n"
        "m2_referee.read(data)\n"
        "print(any(key == 'pdfacts' or key.startswith('pdfacts.') for key in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_7_the_comparison_names_a_difference_and_no_text(monkeypatch):
    real = m2_referee.read(a1())

    def with_another_label(data: bytes):
        widgets, fields = real
        one = dict(widgets[0])
        one["label"] = "Secret"
        return [one], fields

    monkeypatch.setattr(m2_referee, "read", with_another_label)
    entry = m2.check_file(a1())
    assert entry["differences"] == [[0, 0, "label"]]
    assert "Secret" not in json.dumps(entry)


def test_8_the_comparison_finds_equal_facts_equal():
    entry = m2.check_file(a1())
    assert entry["differences"] == []


def test_9_a_refused_run(tmp_path):
    with (ROOT / "bench" / "corpus" / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        first = next(csv.DictReader(handle))["id"]
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "receipt.json"
    status = m2.main(
        ["--commit", "0" * 40, "--out", str(out), "--corpus", str(empty), "--only", first]
    )
    assert status == 2
    assert not out.exists()


def test_10_the_two_passes_are_compared(monkeypatch):
    real = pdfacts.widgets
    calls = 0

    def twice(document, page_index):
        nonlocal calls
        calls += 1
        widgets = real(document, page_index)
        if calls > document.page_count:
            return tuple(
                dataclasses.replace(w, ref=dataclasses.replace(w.ref, object_number=99))
                for w in widgets
            )
        return widgets

    monkeypatch.setattr(pdfacts, "widgets", twice)
    entry = m2.check_file(a1())
    assert entry["stable"] is False
    assert m2.exit_status({"errors": {}, "files": {"a.pdf": entry}}) == 1
