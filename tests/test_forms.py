"""widgets() and fields(): the entry points of forms, with the run-time check.

Table B of the #39 handover. Files are built with the byte-level builder;
expected values come from what was written, never from an engine.
"""

from __future__ import annotations

import dataclasses

import pypdfium2.raw
import pytest

import pdfacts
from pdfacts import (
    ClosedError,
    LimitExceededError,
    Limits,
    Unknown,
    UnknownReason,
)
from pdfacts.backends import pdfium as pdfium_backend
from pdfacts.core.strings import decode_name
from pdfacts.forms import reader
from synthetic.builder import build

N = decode_name
STREAM = b"<< /Length 0 >>\nstream\n\nendstream"


def a1_file(extra_widget: bytes = b"", annots: bytes | None = None) -> bytes:
    """A1's file: one merged text field as the only widget of page 0."""
    listing = annots if annots is not None else b"[6 0 R]"
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots %s >>" % listing,
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (a) /FT /Tx /V (x) /TU (L) /F 4 %s>>" % extra_widget,
        },
        1,
    )


def widget(entries: bytes, rect: bytes = b"[10 10 110 30]") -> bytes:
    return b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect %s %s >>" % (rect, entries)


def forms_file(annots: dict[int, bytes], fields: bytes, objects=None, acroform=None) -> bytes:
    listing = b"[" + b" ".join(b"%d 0 R" % n for n in sorted(annots)) + b"]" if annots else b"[]"
    catalog = b"<< /Type /Catalog /Pages 2 0 R /AcroForm %s >>" % (
        acroform if acroform is not None else b"<< /Fields %s >>" % fields
    )
    whole = {
        1: catalog,
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
        % listing,
    }
    whole.update(annots)
    if objects:
        whole.update(objects)
    return build(whole, 1)


def listed_twice(first: bytes, second: bytes) -> bytes:
    """The B9 builder: one table lists object 12 in two subsections."""
    widget_ = b"/Type /Annot /Subtype /Widget /P 4 0 R /FT /Tx /T (name) /Rect [10 10 110 30]"
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    at = {}
    for number, body in [
        (1, b"<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>"),
        (2, b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>"),
        (
            4,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots [12 0 R] >>",
        ),
        (5, b"<< /Fields [12 0 R] >>"),
    ]:
        at[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    a = len(out)
    out += b"12 0 obj\n<< %s /V (%s) >>\nendobj\n" % (widget_, first)
    b = len(out)
    out += b"12 0 obj\n<< %s /V (%s) >>\nendobj\n" % (widget_, second)
    table = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for number in range(1, 6):
        out += b"%010d 00000 n \n" % at[number] if number in at else b"0000000000 00001 f \n"
    out += b"12 1\n%010d 00000 n \n12 1\n%010d 00000 n \n" % (a, b)
    out += b"trailer\n<< /Size 13 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % table
    return bytes(out)


def radio_file() -> bytes:
    """A2's radio group: widget 6 under field 8, only the widget in /Annots."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [8 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots [6 0 R] >>",
            6: widget(b"/Parent 8 0 R /AS /Off"),
            8: b"<< /T (p) /FT /Btn /Ff 32768 /Opt [(A) (B)] /Kids [6 0 R] >>",
        },
        1,
    )


def real_views(document, index):
    """The views PDFium really gives, for the tests that change one field."""
    with document._access() as access:
        return access.annotation_views(index)


def with_views(monkeypatch, document, index, change):
    """Run widgets() with every view of the page changed by `change`."""
    views = real_views(document, index)
    changed = tuple(None if view is None else change(view) for view in views)

    def replaced(self, at):
        return changed

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "annotation_views", replaced)


# --- B1 to B5: reading, arguments, refusals before the engines ---------------------------


def test_b1_a_file_read_end_to_end():
    with pdfacts.open(a1_file()) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    assert widget_.ref.object_number == 6
    assert widget_.field.name == "a"
    assert widget_.field.type is pdfacts.FieldType.TEXT
    assert widget_.field.value == "x"
    assert widget_.field.label == "L"
    assert widget_.annotation_flags == 4
    assert (widget_.rect.x0, widget_.rect.y0, widget_.rect.x1, widget_.rect.y1) == (10, 10, 110, 30)


def test_b2_arguments():
    with pdfacts.open(a1_file()) as document:
        with pytest.raises(TypeError):
            pdfacts.widgets(b"not a document", 0)
        with pytest.raises(TypeError):
            pdfacts.widgets(document, True)
        with pytest.raises(TypeError):
            pdfacts.widgets(document, 1.0)
        with pytest.raises(IndexError):
            pdfacts.widgets(document, 5)
    with pytest.raises(ClosedError):
        pdfacts.widgets(document, 0)
    with pdfacts.open(a1_file()) as document:
        pass
    with pytest.raises(TypeError):
        pdfacts.fields(b"not a document")
    with pytest.raises(ClosedError):
        pdfacts.fields(document)


def test_b3_qpdf_cannot_open_the_file():
    case = a1_file()
    data = b"x" * 1024 + case
    with pdfacts.open(data) as document:
        unknown = document._qpdf_unknown
        assert isinstance(unknown, Unknown)
        assert pdfacts.widgets(document, 0) is unknown
        assert pdfacts.fields(document) is unknown


def test_b4_the_pages_own_check_failed(monkeypatch):
    from pdfacts.backends import qpdf as qpdf_backend
    from pdfacts.backends.protocols import PageObject

    original = qpdf_backend.QpdfDocument.page_object

    def off_by_a_hundredth(self, index):
        page = original(self, index)
        return PageObject(
            page.object_number, page.generation, (0.0, 0.0, 400.01, 300.0), None, None, None, False
        )

    monkeypatch.setattr(qpdf_backend.QpdfDocument, "page_object", off_by_a_hundredth)
    with pdfacts.open(a1_file()) as document:
        page = document.pages[0]
        assert isinstance(page.ref.object_number, Unknown)
        widgets = pdfacts.widgets(document, 0)
        assert widgets is page.ref.object_number
    assert widgets.reason is UnknownReason.ENGINES_DISAGREE


def test_b5_annots_that_is_no_array():
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots 5 >>",
        },
        1,
    )
    with pdfacts.open(data) as document:
        widgets = pdfacts.widgets(document, 0)
    assert isinstance(widgets, Unknown)
    assert widgets.reason is UnknownReason.INVALID


def test_b6_the_item_limit_before_reading(monkeypatch):
    data = forms_file(
        {
            6: widget(b"/T (a) /FT /Tx"),
            7: widget(b"/T (b) /FT /Tx"),
            8: widget(b"/T (c) /FT /Tx"),
        },
        fields=b"[6 0 R 7 0 R 8 0 R]",
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("an annotations reader was called")

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "annotation_views", must_not_run)
    import pdfacts.core.document as document_module

    monkeypatch.setattr(document_module.FeatureAccess, "annotations", must_not_run)
    with pdfacts.open(data, limits=Limits(max_items_per_page=2)) as document:
        document.pages[0]
        with pytest.raises(LimitExceededError) as raised:
            pdfacts.widgets(document, 0)
    assert raised.value.limit == "max_items_per_page"
    assert raised.value.found == 3


def test_b7_counts_differ(monkeypatch):
    data = forms_file(
        {6: widget(b"/T (a)"), 7: widget(b"/T (b)"), 8: widget(b"/T (c)")},
        fields=b"[6 0 R 7 0 R 8 0 R]",
    )

    def count(self, index):
        return 2

    monkeypatch.setattr(pdfium_backend.PdfiumDocument, "annotation_count", count)
    with pdfacts.open(data) as document:
        widgets = pdfacts.widgets(document, 0)
    assert isinstance(widgets, Unknown)
    assert widgets.reason is UnknownReason.ENGINES_DISAGREE


def with_generation(data: bytes, number: int, generation: int) -> bytes:
    """`data` with object `number` in `generation`; every length stays the same."""
    data = data.replace(b"\n%d 0 obj" % number, b"\n%d %d obj" % (number, generation))
    import re as re_module

    data = re_module.sub(rb"(?<![0-9])%d 0 R" % number, b"%d %d R" % (number, generation), data)
    table = data.rindex(b"\nxref\n") + 1
    lines = data[table:].split(b"\n")
    entry = lines[2 + number]
    lines[2 + number] = entry[:11] + b"%05d" % generation + entry[16:]
    return data[:table] + b"\n".join(lines)


def test_b8_a_widget_on_one_side_only():
    base = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R 7 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots [6 0 R 7 0 R] >>",
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (a) /FT /Tx /V (x) /TU (L) >>",
            7: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (b) /FT /Tx /V (y) >>",
        },
        1,
    )
    offset = base.index(b"\n7 0 obj\n") + 1
    data = base.replace(b"\n7 0 obj\n", b"\n7 1 obj\n")
    data = data.replace(b"%010d 00000 n \n" % offset, b"%010d 00001 n \n" % offset)
    with pdfacts.open(data) as document:
        six, seven = pdfacts.widgets(document, 0)
    assert six.field.name == "a"
    assert isinstance(seven.field, Unknown)
    assert seven.field.reason is UnknownReason.ENGINES_DISAGREE
    assert "only PDFium sees a widget here" in seven.field.detail
    assert seven.ref.kind == "Widget"


def test_b9_the_widget_listed_twice_in_the_cross_reference():
    with pdfacts.open(listed_twice(b"Alice", b"Mallory")) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    assert isinstance(widget_.field, Unknown)
    assert widget_.field.reason is UnknownReason.ENGINES_DISAGREE
    assert "/V" in widget_.field.detail


@pytest.mark.parametrize(
    ("file", "change", "detail"),
    [
        (
            "moved rect",
            lambda view: dataclasses.replace(view, rect=(view.rect[0] + 0.01, *view.rect[1:])),
            "rectangle",
        ),
        ("flags plus one", lambda view: dataclasses.replace(view, flags=view.flags + 1), "flags"),
        ("another t", lambda view: dataclasses.replace(view, t=view.t + "x"), "/T"),
        (
            "another tu",
            lambda view: dataclasses.replace(
                view,
                values={**view.values, b"TU": (view.values[b"TU"][0], view.values[b"TU"][1] + "x")},
            ),
            "/TU",
        ),
        (
            "no widget",
            lambda view: dataclasses.replace(view, widget=False),
            "only QPDF sees a widget here",
        ),
    ],
)
def test_b10_a1_plus_f4_the_check_fails(file, change, detail, monkeypatch):
    with pdfacts.open(a1_file(b"/F 4")) as document:
        document.pages[0]
        with_views(monkeypatch, document, 0, change)
        widgets = pdfacts.widgets(document, 0)
    (widget_,) = widgets
    assert isinstance(widget_.field, Unknown)
    assert widget_.field.reason is UnknownReason.ENGINES_DISAGREE
    assert detail in widget_.field.detail


def test_b10_flags_one_without_f(monkeypatch):
    with pdfacts.open(a1_file()) as document:
        document.pages[0]
        with_views(monkeypatch, document, 0, lambda view: dataclasses.replace(view, flags=1))
        (widget_,) = pdfacts.widgets(document, 0)
    assert isinstance(widget_.field, Unknown)
    assert "flags" in widget_.field.detail


def test_b10_the_view_replaced_by_none(monkeypatch):
    with pdfacts.open(a1_file(b"/F 4")) as document:
        document.pages[0]
        with_views(monkeypatch, document, 0, lambda view: None)
        (widget_,) = pdfacts.widgets(document, 0)
    assert isinstance(widget_.field, Unknown)
    assert "only QPDF sees a widget here" in widget_.field.detail


def test_b10_as_and_parent_of_a2(monkeypatch):
    with pdfacts.open(radio_file()) as document:
        document.pages[0]
        views = real_views(document, 0)
        (view,) = views

        def as_other(self, index):
            return (dataclasses.replace(view, as_text=view.as_text + "x"),)

        monkeypatch.setattr(pdfium_backend.PdfiumDocument, "annotation_views", as_other)
        (widget_,) = pdfacts.widgets(document, 0)
        assert isinstance(widget_.field, Unknown)
        assert "/AS" in widget_.field.detail

        def no_parent(self, index):
            return (dataclasses.replace(view, parent_type=0),)

        monkeypatch.setattr(pdfium_backend.PdfiumDocument, "annotation_views", no_parent)
        (widget_,) = pdfacts.widgets(document, 0)
        assert isinstance(widget_.field, Unknown)
        assert "/Parent" in widget_.field.detail


def test_b10_unchanged_views_pass(monkeypatch):
    with pdfacts.open(a1_file(b"/F 4")) as document:
        document.pages[0]
        (widget_,) = pdfacts.widgets(document, 0)
    assert widget_.field.name == "a"
    with pdfacts.open(radio_file()) as document:
        document.pages[0]
        (widget_,) = pdfacts.widgets(document, 0)
    assert widget_.export_value == "A"


@pytest.mark.parametrize(
    ("entries", "objects"),
    [
        (b"/T (a) /FT /Tx /Rect [0 0 40000.123 20]", {}),
        (b"/T (a) /FT /Tx /F 2147483652", {}),
        (b"/T (a) /FT /Tx /TU (a\255b)", {}),
        (b"/T (a) /FT /Tx /DV 9 0 R", {9: b"(ref)"}),
        (b"/T (a) /FT /Tx /AS /" + b"L" * 300, {}),
    ],
)
def test_b11_pdfiums_ways_that_must_pass(entries, objects):
    body = entries.replace(b"/Rect [0 0 40000.123 20]", b"")
    rect = b"[0 0 40000.123 20]" if b"40000" in entries else b"[10 10 110 30]"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots [6 0 R] >>",
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect %s %s >>" % (rect, body),
            **objects,
        },
        1,
    )
    with pdfacts.open(data) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    assert not isinstance(widget_.field, Unknown), widget_.field


def test_b11_t_null_on_a_kid():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R /T null"),
            8: b"<< /T (f) /FT /Tx /Kids [6 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    with pdfacts.open(data) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    assert not isinstance(widget_.field, Unknown)


@pytest.mark.parametrize("entries", [b"", b"/T 5"], ids=["no t", "t a number"])
def test_b12_nothing_identifies_the_widget(entries):
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots [6 0 R] >>",
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [0 0 1] %s >>" % entries,
        },
        1,
    )
    with pdfacts.open(data) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    assert isinstance(widget_.field, Unknown)
    assert "nothing identifies" in widget_.field.detail


def test_b13_the_deadline():
    now = [0.0]
    with pdfacts.open(
        a1_file(), limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    ) as document:
        document.pages[0]
        now[0] = 6.0
        for call in (lambda: pdfacts.widgets(document, 0), lambda: pdfacts.fields(document)):
            with pytest.raises(LimitExceededError) as raised:
                call()
            assert (
                raised.value.limit,
                raised.value.allowed,
            ) == ("deadline_seconds", 5)
            assert raised.value.found == 6.0


def test_b14_object_streams_that_fail_the_check():
    sys_path = __import__("sys")
    sys_path.path.insert(0, "tests")
    from synthetic.builder import build_with_object_stream

    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /UserUnit 9 0 R >>"
    )
    data = build_with_object_stream(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: page,
        },
        [(9, b"2"), (9, b"3")],
        {9: 0},
        1,
    )
    with pdfacts.open(data) as document:
        unknown = document._qpdf_unknown
        assert isinstance(unknown, Unknown)
        assert pdfacts.widgets(document, 0) is unknown
        assert pdfacts.fields(document) is unknown


def test_b15_exports_and_capability():
    for name in ("widgets", "fields"):
        assert name in pdfacts.__all__
    assert pdfacts.capabilities()[pdfacts.Capability.FORMS] is True


def test_b16_stable():
    with pdfacts.open(radio_file()) as document:
        assert pdfacts.widgets(document, 0) == pdfacts.widgets(document, 0)
        assert pdfacts.fields(document) == pdfacts.fields(document)


def test_b17_both_views_agree():
    page_of = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [8 0 R] >> >>",
            2: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
            4: page_of % b"[6 0 R]",
            5: page_of % b"[7 0 R]",
            6: widget(b"/Parent 8 0 R /AS /Off"),
            7: widget(b"/Parent 8 0 R /AS /B"),
            8: b"<< /T (p) /FT /Btn /Ff 32768 /Opt [(A) (B)] /Kids [6 0 R 7 0 R] >>",
        },
        1,
    )
    with pdfacts.open(data) as document:
        fields = pdfacts.fields(document)
        widgets = pdfacts.widgets(document, 0) + pdfacts.widgets(document, 1)
    by_ref = {(field.ref.object_number, field.ref.generation): field for field in fields}
    for widget_ in widgets:
        assert (
            by_ref[(widget_.field.ref.object_number, widget_.field.ref.generation)] == widget_.field
        )


def test_b18_feature_access_ends_with_its_block(monkeypatch):
    kept = []

    from contextlib import contextmanager

    real_access = pdfacts.core.document.Document._access

    @contextmanager
    def keeping(self):
        with real_access(self) as access:
            kept.append(access)
            yield access

    monkeypatch.setattr(pdfacts.core.document.Document, "_access", keeping)
    with pdfacts.open(a1_file()) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    access = kept[0]
    with pytest.raises(ClosedError):
        access.annotation_count(0)


def test_b19_the_node_table_is_built_once(monkeypatch):
    objects = {}
    for i in range(50):
        objects[100 + i] = (
            b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (f%d) /FT /Tx >>" % i
        )
    listing = b"[" + b" ".join(b"%d 0 R" % (100 + i) for i in range(50)) + b"]"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields %s >> >>" % listing,
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >>"
            b" /Annots %s >>" % listing,
            **objects,
        },
        1,
    )
    calls = []
    real_nodes = reader._nodes

    def counting(tree):
        calls.append(1)
        return real_nodes(tree)

    monkeypatch.setattr(reader, "_nodes", counting)
    with pdfacts.open(data) as document:
        pdfacts.widgets(document, 0)
    assert len(calls) == 1
    del calls[:]
    with pdfacts.open(data) as document:
        pdfacts.fields(document)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("entries", "name_invalid", "state_invalid"),
    [
        (b"/T 5", True, False),
        (b"/T (a) /FT /Btn /AS (Off)", False, True),
    ],
)
def test_b20_present_of_any_kind(entries, name_invalid, state_invalid):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    with pdfacts.open(data) as document:
        (widget_,) = pdfacts.widgets(document, 0)
    field = widget_.field
    assert not isinstance(field, Unknown)
    if name_invalid:
        assert isinstance(field.name, Unknown)
        assert isinstance(field.partial_names, Unknown)
    else:
        assert field.name == "a"
    if state_invalid:
        assert isinstance(widget_.state, Unknown)
    else:
        assert widget_.state is None


def test_b21_the_readers_pdfium_codes():
    assert reader._OBJECT_UNKNOWN == pypdfium2.raw.FPDF_OBJECT_UNKNOWN
    assert reader._OBJECT_STRING == pypdfium2.raw.FPDF_OBJECT_STRING
    assert reader._OBJECT_NULLOBJ == pypdfium2.raw.FPDF_OBJECT_NULLOBJ
    assert reader._OBJECT_REFERENCE == pypdfium2.raw.FPDF_OBJECT_REFERENCE
