"""The annotations of a page other than widgets (pdfacts.annotation).

Files are built with the byte-level builder; the values expected are those
written (AGENTS.md, rule 2).
"""

from __future__ import annotations

import dataclasses

import pytest

import pdfacts
from pdfacts import (
    AnnotationAppearance,
    DeviceColour,
    LimitExceededError,
    Limits,
    Unknown,
    UnknownReason,
)
from pdfacts.annotation.model import Annotation
from pdfacts.core.geometry import Rect
from pdfacts.core.refs import AnnotationRef
from synthetic.builder import build

PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"


def page(entries: bytes = b"", objects: dict[int, bytes] | None = None) -> bytes:
    body = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>"
    if entries:
        body += b" /Annots [" + entries + b"]"
    return build(
        {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: PAGES, 3: body + b" >>", **(objects or {})}, 1
    )


def annot(entries: bytes, subtype: bytes = b"/Link", number: int = 5) -> bytes:
    return b"%d 0 R" % number if entries is None else b"%d 0 R" % number


def listed(*items: bytes) -> bytes:
    return b" ".join(items)


def one(data: bytes, password: str | None = None):
    with pdfacts.open(data, password=password) as document:
        return pdfacts.annotations(document, 0)


def entry(subtype: str, number: int, **values) -> Annotation:
    return Annotation(
        ref=AnnotationRef(
            page_index=0, annotation_index=0, kind=subtype, object_number=number, generation=0
        ),
        **values,
    )


def test_no_annotation():
    assert one(page()) == ()


def test_a_link():
    data = page(
        listed(b"5 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Link /Rect [10 20 110 40] /F 4"
            b" /Contents (a note) /C [0 1 0] >>"
        },
    )
    found = one(data)
    assert found == (
        Annotation(
            ref=AnnotationRef(
                page_index=0, annotation_index=0, kind="Link", object_number=5, generation=0
            ),
            rect=Rect(10, 20, 110, 40),
            annotation_flags=4,
            contents="a note",
            colour=DeviceColour((0.0, 1.0, 0.0)),
            appearance=None,
        ),
    )


def test_widgets_are_not_listed():
    data = page(
        listed(b"5 0 R 6 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 10 10] >>",
            6: b"<< /Type /Annot /Subtype /Link /Rect [10 20 110 40] >>",
        },
    )
    found = one(data)
    assert [a.ref.kind for a in found] == ["Link"]
    assert [a.ref.annotation_index for a in found] == [1]


@pytest.mark.parametrize(
    "subtype, listed_kind",
    [
        (b"/Popup", "Popup"),
        (b"/Highlight", "Highlight"),
        (b"/FreeText", "FreeText"),
        (b"/Square", "Square"),
    ],
)
def test_each_subtype_is_listed(subtype, listed_kind):
    data = page(
        listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype " + subtype + b" /Rect [0 0 10 10] >>"}
    )
    assert [a.ref.kind for a in one(data)] == [listed_kind]


def test_no_subtype_is_not_listed():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Rect [0 0 10 10] >>"})
    assert one(data) == ()


def test_a_subtype_that_is_no_name_is_not_listed():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype (Link) /Rect [0 0 10 10] >>"})
    assert one(data) == ()


@pytest.mark.parametrize(
    "rect, expected",
    [
        (b"[0 0 10 10]", Rect(0, 0, 10, 10)),
        (b"[10 20 0 5]", Rect(0, 5, 10, 20)),
        (b"[-1 -2 3 4]", Rect(-1, -2, 3, 4)),
        (b"[1.5 2 3.25 4]", Rect(1.5, 2, 3.25, 4)),
    ],
)
def test_the_rectangle(rect, expected):
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect " + rect + b" >>"})
    assert one(data)[0].rect == expected


def test_the_rectangle_absent():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link >>"})
    assert one(data)[0].rect == Unknown(UnknownReason.INVALID, "/Rect is absent")


@pytest.mark.parametrize(
    "rect",
    [b"[0 0 10]", b"[0 0 10 10 10]", b"5", b"[(a)]"],
    ids=["three numbers", "five numbers", "a number", "a string among them"],
)
def test_the_rectangle_of_another_shape(rect):
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect " + rect + b" >>"})
    assert one(data)[0].rect == Unknown(
        UnknownReason.INVALID, "/Rect is not an array of four numbers"
    )


def test_the_rectangle_null_is_absent():
    """QPDF reads /Rect null as absent: the key is dropped from the Written."""
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect null >>"})
    assert one(data)[0].rect == Unknown(UnknownReason.INVALID, "/Rect is absent")


def test_flags_absent():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"})
    assert one(data)[0].annotation_flags == Unknown(UnknownReason.INVALID, "/F is absent")


def test_flags_as_written():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /F 32 >>"})
    assert one(data)[0].annotation_flags == 32


def test_flags_of_another_type():
    data = page(
        listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /F (4) >>"}
    )
    assert one(data)[0].annotation_flags == Unknown(UnknownReason.INVALID, "/F is not an integer")


@pytest.mark.parametrize(
    "written, text",
    [
        (b"(a note)", "a note"),
        (b"(\xfe\xff\x00a\x00b)", "ab"),
        (b"(\xef\xbb\xbfab)", "ab"),
        (b"()", ""),
        (b"(caf\xe9)", "caf\xe9"),
    ],
    ids=["PDFDocEncoding", "UTF-16 with a BOM", "UTF-8 with a BOM", "empty", "a byte above 127"],
)
def test_contents(written, text):
    data = page(
        listed(b"5 0 R"),
        {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /Contents " + written + b" >>"},
    )
    assert one(data)[0].contents == text


def test_contents_absent():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"})
    assert one(data)[0].contents is None


def test_contents_of_another_type():
    data = page(
        listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /Contents 5 >>"}
    )
    assert one(data)[0].contents == Unknown(UnknownReason.INVALID, "/Contents is not a string")


@pytest.mark.parametrize(
    "colour",
    [
        b"[0.5]",
        b"[0 1 0]",
        b"[0 0.1 0.2 1]",
        b"[]",
        b"[1.5 0 0]",
        b"null",
    ],
    ids=["grey", "rgb", "cmyk", "none", "beyond 1", "null"],
)
def test_colour(colour):
    data = page(
        listed(b"5 0 R"),
        {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /C " + colour + b" >>"},
    )
    assert one(data)[0].colour == _colour_of(colour)


def _colour_of(colour: bytes):
    if colour == b"[0.5]":
        return DeviceColour((0.5,))
    if colour == b"[0 1 0]":
        return DeviceColour((0.0, 1.0, 0.0))
    if colour == b"[0 0.1 0.2 1]":
        return DeviceColour((0.0, 0.1, 0.2, 1.0))
    if colour == b"[]":
        return DeviceColour(())
    if colour == b"[1.5 0 0]":
        return Unknown(UnknownReason.INVALID, "a component of /C is not between 0 and 1")
    return None


def test_colour_of_another_shape():
    data = page(
        listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /C (b) >>"}
    )
    assert one(data)[0].colour == Unknown(UnknownReason.INVALID, "/C is not an array")


def test_colour_two_components():
    data = page(
        listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /C [0 1] >>"}
    )
    assert one(data)[0].colour == Unknown(
        UnknownReason.INVALID, "/C has not 0, 1, 3 or 4 components"
    )


def test_the_appearance_of_a_stream():
    data = page(
        listed(b"5 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Highlight /Rect [0 0 10 10] /AP << /N 6 0 R >> >>",
            6: b"<< /BBox [0 0 10 10] /Matrix [2 0 0 2 0 0] /Length 0 >>\nstream\n\nendstream",
        },
    )
    assert one(data)[0].appearance == AnnotationAppearance(
        object_number=6,
        generation=0,
        bbox=(0.0, 0.0, 10.0, 10.0),
        matrix=(2.0, 0.0, 0.0, 2.0, 0.0, 0.0),
    )


def test_the_appearance_absent():
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"})
    assert one(data)[0].appearance is None


def test_the_appearance_selected_by_a_state():
    data = page(
        listed(b"5 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Highlight /Rect [0 0 10 10] /AS /Yes"
            b" /AP << /N << /Yes 6 0 R /No 7 0 R >> >> >>",
            6: b"<< /BBox [0 0 10 10] /Length 0 >>\nstream\n\nendstream",
            7: b"<< /BBox [0 0 5 5] /Length 0 >>\nstream\n\nendstream",
        },
    )
    assert one(data)[0].appearance == AnnotationAppearance(
        object_number=6, generation=0, bbox=(0.0, 0.0, 10.0, 10.0), matrix=None
    )


def test_two_annotations_in_order():
    data = page(
        listed(b"5 0 R 6 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Popup /Rect [0 0 10 10] >>",
            6: b"<< /Type /Annot /Subtype /Link /Rect [10 20 110 40] >>",
        },
    )
    assert [a.ref.kind for a in one(data)] == ["Popup", "Link"]
    assert [a.ref.annotation_index for a in one(data)] == [0, 1]


def test_an_entry_that_is_no_dictionary():
    data = page(listed(b"5 0 R 7"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"})
    assert [a.ref.annotation_index for a in one(data)] == [0]


def test_a_direct_annotation():
    data = page(listed(b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"))
    found = one(data)
    assert found[0].ref.object_number is None and found[0].ref.generation is None


def test_an_annots_that_is_no_array():
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: PAGES,
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>"
            b" /Annots 5 >>",
        },
        1,
    )
    assert one(data) == Unknown(UnknownReason.INVALID, "/Annots is not an array")


def test_the_item_limit_over_annots():
    data = page(
        listed(b"5 0 R 6 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>",
            6: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>",
        },
    )
    with pdfacts.open(data, limits=Limits(max_items_per_page=1)) as document:
        with pytest.raises(LimitExceededError) as raised:
            pdfacts.annotations(document, 0)
    assert raised.value.limit == "max_items_per_page"


def test_the_type_is_frozen():
    found = one(page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        found[0].contents = "x"


def test_it_takes_no_other_document():
    with pytest.raises(TypeError):
        pdfacts.annotations(object(), 0)


def test_an_appearance_qpdf_cannot_decide_is_unknown():
    """/AP /N a dictionary of states with a key whose name has no bytes: QPDF
    cannot tell which one PDFium draws."""
    data = page(
        listed(b"5 0 R"),
        {
            5: b"<< /Type /Annot /Subtype /Highlight /Rect [0 0 10 10]"
            b" /AP << /N << /W#zz 6 0 R >> >> >>",
            6: b"<< /BBox [0 0 10 10] /Length 0 >>\nstream\n\nendstream",
        },
    )
    found = one(data)[0].appearance
    assert found is not None and found.reason is UnknownReason.UNREADABLE


def test_engines_that_count_annotations_differently(monkeypatch):
    from pdfacts.backends.qpdf import QpdfDocument

    original = QpdfDocument.annotation_count

    def one_more(self, index):
        return original(self, index) + 1

    monkeypatch.setattr(QpdfDocument, "annotation_count", one_more)
    data = page(listed(b"5 0 R"), {5: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] >>"})
    found = one(data)
    assert found.reason is UnknownReason.ENGINES_DISAGREE
    assert "QPDF finds 2 annotations on the page, PDFium 1" in found.detail
