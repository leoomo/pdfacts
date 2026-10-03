"""A name that writes a # not followed by two hexadecimal digits has no bytes
(docs/conventions.md, "Text strings and names"): a value that is such a name
is unknown, invalid in the file; where one of an annotation's appearance
states is such a name, its graphics are unknown; where its /Subtype is one
and PDFium draws it, no graphics of the page are known.

Files are built with the byte-level builder; the values expected are those
written, by ISO 32000-2 (7.3.5): `#` and two hexadecimal digits is the byte
they give, anything else after a `#` is no name.
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts.backends.protocols import Written
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, stream

CATALOG = b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>"
PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
PAGE = (
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>"
    b" /Annots [5 0 R] /Contents 4 0 R >>"
)
# A form that strokes a line, so that PDFium draws it.
FORM = stream(b"0 0 m 10 10 l S", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]")
INVALID = "a name with a # not followed by two hexadecimal digits"

# Names, each after a T, whose # is not followed by two hexadecimal digits;
# in the last two an escape follows the #.
STRAY = [b"T#zzx", b"T#4G", b"T#", b"T#4", b"T#+4x", b"T#4#31", b"T#A#42"]
STRAY_IDS = [
    "two letters",
    "a digit and a letter",
    "at the end",
    "one digit at the end",
    "a sign",
    "a digit and an escape",
    "a letter and an escape",
]
# Valid names and their bytes.
VALID = [
    (b"T#41", b"TA"),
    (b"T#23x", b"T#x"),
    (b"T#e9", b"T\xe9"),
    (b"T#2B4x", b"T+4x"),
    (b"T#20x", b"T x"),
    (b"T#ff#fe", b"T\xff\xfe"),
]
VALID_IDS = ["A", "#", "a byte above 127", "+", "a space", "two bytes above 127"]


def one_widget(entries: bytes) -> bytes:
    """One page; annotation 5 a widget-field with `entries`; form 6."""
    widget = (
        b"<< /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /T (a) /P 3 0 R " + entries + b" >>"
    )
    return build({1: CATALOG, 2: PAGES, 3: PAGE, 4: stream(b"q Q"), 5: widget, 6: FORM}, 1)


def one_annotation(annotation: bytes) -> bytes:
    """One page; annotation 5 as given; form 6."""
    return build({1: CATALOG, 2: PAGES, 3: PAGE, 4: stream(b"q Q"), 5: annotation, 6: FORM}, 1)


def page_annotations(data: bytes):
    document = QpdfDocument.open(data, None)
    try:
        return document.annotations(0), document.appearances(0)
    finally:
        document.close()


# --- the backend ------------------------------------------------------------------


@pytest.mark.parametrize("written", STRAY, ids=STRAY_IDS)
def test_a_value_without_bytes(written):
    annotations, _ = page_annotations(one_widget(b"/FT /" + written))
    (widget,) = annotations.widgets
    assert widget.chain[0].entries[b"FT"] == Written("invalid name", None)


@pytest.mark.parametrize(("written", "data"), VALID, ids=VALID_IDS)
def test_a_value_with_its_bytes(written, data):
    annotations, _ = page_annotations(one_widget(b"/FT /" + written))
    (widget,) = annotations.widgets
    assert widget.chain[0].entries[b"FT"] == Written("name", data)


def test_a_subtype_without_bytes():
    """Neither engine reads /Widget there (third comment): the entry is no
    widget."""
    annotations, appearances = page_annotations(
        one_annotation(b"<< /Type /Annot /Subtype /Wid#zzget /Rect [0 0 10 10] /P 3 0 R >>")
    )
    (entry,) = annotations.entries
    assert (entry.subtype, annotations.widgets) == (f"/Subtype is {INVALID}", ())
    assert appearances[0].annotation.subtype == f"/Subtype is {INVALID}"


@pytest.mark.parametrize("written", STRAY, ids=STRAY_IDS)
def test_a_state_without_bytes(written):
    """/AP /N names the form 6 under a key without bytes, and under Off.
    QPDF gives the keys in its own order."""
    entries = b"/FT /Btn /AS /Off /AP << /N << /" + written + b" 6 0 R /Off 6 0 R >> >>"
    annotations, appearances = page_annotations(one_widget(entries))
    (widget,) = annotations.widgets
    assert len(widget.states) == 2 and set(widget.states) == {None, b"Off"}
    (appearance,) = appearances
    assert (appearance.appearance, appearance.undecided) == (None, f"a key of /AP /N is {INVALID}")


@pytest.mark.parametrize(("written", "data"), VALID, ids=VALID_IDS)
def test_a_state_with_its_bytes(written, data):
    entries = b"/FT /Btn /AS /Off /AP << /N << /" + written + b" 6 0 R /Off 6 0 R >> >>"
    annotations, appearances = page_annotations(one_widget(entries))
    (widget,) = annotations.widgets
    assert len(widget.states) == 2 and set(widget.states) == {data, b"Off"}
    assert appearances[0].undecided is None


@pytest.mark.parametrize("written", STRAY, ids=STRAY_IDS)
def test_the_state_chosen_without_bytes(written):
    entries = b"/FT /Btn /AS /" + written + b" /AP << /N << /Off 6 0 R >> >>"
    _, (appearance,) = page_annotations(one_widget(entries))
    assert (appearance.appearance, appearance.undecided) == (None, f"/AS is {INVALID}")


# --- fields(), widgets(), paths() and images() -------------------------------------


def only(data: bytes):
    with pdfacts.open(data) as document:
        (field,) = pdfacts.fields(document)
        (widget,) = pdfacts.widgets(document, 0)
    return field, widget


def unknown(value, reason: pdfacts.UnknownReason, detail: str) -> bool:
    return isinstance(value, pdfacts.Unknown) and (value.reason, value.detail) == (reason, detail)


def invalid(value, detail: str) -> bool:
    return unknown(value, pdfacts.UnknownReason.INVALID, detail)


def test_a_field_type_without_bytes():
    field, widget = only(one_widget(b"/FT /T#zzx"))
    assert invalid(field.type, f"the /FT of the field is {INVALID}")
    assert widget.field == field


@pytest.mark.parametrize("written", [b"Y#zzs", b"Y#4#31"], ids=["a stray #", "an escape after it"])
def test_a_checkbox_value_without_bytes(written):
    field, widget = only(one_widget(b"/FT /Btn /V /%s /AS /%s" % (written, written)))
    assert field.type is pdfacts.FieldType.CHECKBOX
    assert invalid(field.value, f"the value of the field is {INVALID}")
    assert invalid(widget.state, f"the /AS of the widget is {INVALID}")


def test_the_states_without_bytes():
    field, widget = only(
        one_widget(b"/FT /Btn /AS /Off /AP << /N << /Y#zzs 6 0 R /Off 6 0 R >> >>")
    )
    assert invalid(widget.states, f"a key of the widget's /AP /N is {INVALID}")
    assert widget.state == pdfacts.Name(b"Off", "Off", pdfacts.NameEncoding.UTF8)


@pytest.mark.parametrize("written", [b"D#zz", b"#4#34"], ids=["a stray #", "an escape after it"])
def test_a_border_style_without_bytes(written):
    field, widget = only(one_widget(b"/FT /Tx /BS << /W 1 /S /%s >>" % written))
    assert invalid(widget.border, f"the style of the border is {INVALID}")


def test_the_graphics_of_an_annotation_with_a_state_without_bytes():
    """PDFium draws the form 6, which strokes one line: the annotation's path
    is there, its values unknown, as for any appearance QPDF cannot tell."""
    data = one_widget(b"/FT /Btn /AS /Off /AP << /N << /Y#zzs 6 0 R /Off 6 0 R >> >>")
    with pdfacts.open(data) as document:
        (path,) = pdfacts.paths(document, 0)
    assert path.ref.annotation is not None
    assert unknown(
        path.stroke_colour, pdfacts.UnknownReason.UNREADABLE, f"a key of /AP /N is {INVALID}"
    )


def test_a_page_with_an_annotation_subtype_without_bytes():
    """Not drawn: the page's graphics are known; it has none."""
    annotation = b"<< /Type /Annot /Subtype /Wid#zzget /Rect [0 0 10 10] /P 3 0 R >>"
    with pdfacts.open(one_annotation(annotation)) as document:
        assert pdfacts.widgets(document, 0) == ()
        assert pdfacts.paths(document, 0) == ()
        assert pdfacts.images(document, 0) == ()


def test_a_page_with_a_drawn_annotation_subtype_without_bytes():
    """PDFium draws the form 6: no graphics of the page are known."""
    annotation = b"<< /Type /Annot /Subtype /Squ#zzare /Rect [0 0 10 10] /AP << /N 6 0 R >> >>"
    detail = f"PDFium draws annotation 0 of the page, whose /Subtype is {INVALID}"
    with pdfacts.open(one_annotation(annotation)) as document:
        assert invalid(pdfacts.paths(document, 0), detail)
        assert invalid(pdfacts.images(document, 0), detail)
