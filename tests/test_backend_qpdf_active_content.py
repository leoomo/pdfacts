"""The file's active content for the inspection: its actions and the entries
that name them, its XFA and its embedded files, as plain data.

Files are built with the byte-level builder, and the values expected are
those written (AGENTS.md, rule 2): each action by the object it is written in,
each entry by the dictionary that holds it, in the order ActiveContent states.
"""

from __future__ import annotations

import dataclasses
import time
import tracemalloc
import typing

import pytest

from pdfacts.backends.protocols import (
    Action,
    ActionPlace,
    ActionTrigger,
    ActiveContent,
    ActiveContentReader,
    EmbeddedFile,
    EngineFailure,
    FailureKind,
)
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, build_with_object_stream, stream
from synthetic.encryption import encrypt

PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
JS = b"<< /S /JavaScript /JS (1) >>"
URI = b"<< /S /URI /URI (u) >>"
DOCUMENT = ActionPlace.DOCUMENT
PAGE = ActionPlace.PAGE
ANNOTATION = ActionPlace.ANNOTATION
FIELD = ActionPlace.FIELD


def catalog(entries: bytes = b"") -> bytes:
    return b"<< /Type /Catalog /Pages 2 0 R" + entries + b" >>"


def page(entries: bytes = b"") -> bytes:
    return (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>" + entries + b" >>"
    )


def annotation(entries: bytes, subtype: bytes = b"/Link") -> bytes:
    return b"<< /Type /Annot /Subtype " + subtype + b" /Rect [0 0 10 10]" + entries + b" >>"


def one_page(
    catalog_entries: bytes = b"", page_entries: bytes = b"", objects: dict[int, bytes] | None = None
) -> bytes:
    """The catalog, object 1; the page tree, 2; its one page, 3."""
    objects = {1: catalog(catalog_entries), 2: PAGES, 3: page(page_entries), **(objects or {})}
    return build(objects, 1)


def active(data: bytes, password: str | None = None) -> ActiveContent:
    document = QpdfDocument.open(data, password)
    try:
        return document.active_content()
    finally:
        document.close()


def action(kind: bytes | str | None, written_in: int, following: int | None = None) -> Action:
    """An action; `following` is its /Next as an index into sequences."""
    return Action(kind=kind, written_in=(written_in, 0), next=following)


def entry(
    place: ActionPlace,
    owner: int | None,
    event: bytes | None,
    index: int,
    page: int | None = None,
    annotation: int | None = None,
) -> ActionTrigger:
    owner_object = None if owner is None else (owner, 0)
    return ActionTrigger(
        place=place, owner=owner_object, page=page, annotation=annotation, event=event, action=index
    )


def test_no_active_content():
    assert active(one_page()) == ActiveContent(
        misread=False,
        actions=(),
        sequences=(),
        triggers=(),
        xfa=None,
        embedded_files=(),
        embedded_streams=(),
        warnings=(),
    )


@pytest.mark.parametrize(
    "value, objects, written_in",
    [(b"4 0 R", {4: JS}, 4), (JS, {}, 1)],
    ids=["its own object", "written in the catalog"],
)
def test_the_open_action(value, objects, written_in):
    found = active(one_page(b" /OpenAction " + value, objects=objects))
    assert found.actions == (action(b"JavaScript", written_in),)
    assert found.triggers == (entry(DOCUMENT, 1, b"OpenAction", 0),)


def test_an_open_action_that_is_a_destination():
    """/OpenAction may be a destination, which is no action."""
    found = active(one_page(b" /OpenAction [3 0 R /Fit]"))
    assert (found.actions, found.triggers) == ((), ())


def test_the_documents_events():
    """The events of the catalog's /AA come after /OpenAction, in the order
    of their keys, not as written."""
    entries = b" /OpenAction 5 0 R /AA << /WC 4 0 R /DP << /S /Named /N /Print >> >>"
    found = active(one_page(entries, objects={4: JS, 5: URI}))
    assert found.actions == (action(b"URI", 5), action(b"Named", 1), action(b"JavaScript", 4))
    assert found.triggers == (
        entry(DOCUMENT, 1, b"OpenAction", 0),
        entry(DOCUMENT, 1, b"DP", 1),
        entry(DOCUMENT, 1, b"WC", 2),
    )


def test_document_javascript():
    """Each value of the JavaScript name tree that is a dictionary, by the
    node that holds it: a node's /Names, then its /Kids. 12 is no
    dictionary, (z) has no value, and node 6 names node 4 again."""
    names = b"<< /Names [(b) 5 0 R (a) << /S /JavaScript /JS (2) >> (x) 12 (z)] /Kids [6 0 R] >>"
    objects = {4: names, 5: JS, 6: b"<< /Names [(c) 7 0 R] /Kids [4 0 R] >>", 7: JS}
    found = active(one_page(b" /Names << /JavaScript 4 0 R >>", objects=objects))
    assert found.actions == (
        action(b"JavaScript", 5),
        action(b"JavaScript", 4),
        action(b"JavaScript", 7),
    )
    place = ActionPlace.DOCUMENT_JAVASCRIPT
    assert found.triggers == (
        entry(place, 4, None, 0),
        entry(place, 4, None, 1),
        entry(place, 6, None, 2),
    )


def test_a_name_tree_written_in_the_catalog():
    found = active(one_page(b" /Names << /JavaScript << /Names [(a) 4 0 R] >> >>", objects={4: JS}))
    assert found.triggers == (entry(ActionPlace.DOCUMENT_JAVASCRIPT, 1, None, 0),)


def test_a_pages_events():
    found = active(one_page(page_entries=b" /AA << /O 4 0 R /C " + JS + b" >>", objects={4: URI}))
    assert found.actions == (action(b"JavaScript", 3), action(b"URI", 4))
    assert found.triggers == (entry(PAGE, 3, b"C", 0, page=0), entry(PAGE, 3, b"O", 1, page=0))


def test_the_events_in_the_order_of_their_keys():
    """The ten events of an annotation's /AA, written in another order, come
    in the order of their keys."""
    events = (b"E", b"X", b"D", b"U", b"Fo", b"Bl", b"PO", b"PC", b"PV", b"PI")
    written = b" ".join(b"/%s << /S /Named /N /%s >>" % (event, event) for event in events)
    objects = {5: annotation(b" /AA << " + written + b" >>")}
    found = active(one_page(page_entries=b" /Annots [5 0 R]", objects=objects))
    ordered = sorted(events)
    assert [trigger.event for trigger in found.triggers] == ordered
    assert [trigger.action for trigger in found.triggers] == list(range(len(events)))


def test_an_annotations_action_and_events():
    """/A first, then the events of /AA, which is an object of its own here:
    an action written in it is written in that object."""
    objects = {
        5: annotation(b" /A " + URI + b" /AA 7 0 R"),
        6: JS,
        7: b"<< /X 6 0 R /E << /S /Named /N /NextPage >> >>",
    }
    found = active(one_page(page_entries=b" /Annots [5 0 R]", objects=objects))
    assert found.actions == (action(b"URI", 5), action(b"Named", 7), action(b"JavaScript", 6))
    assert found.triggers == (
        entry(ANNOTATION, 5, b"A", 0, page=0, annotation=0),
        entry(ANNOTATION, 5, b"E", 1, page=0, annotation=0),
        entry(ANNOTATION, 5, b"X", 2, page=0, annotation=0),
    )


def test_an_annotation_written_in_the_page():
    found = active(one_page(page_entries=b" /Annots [" + annotation(b" /A " + URI) + b"]"))
    assert found.actions == (action(b"URI", 3),)
    assert found.triggers == (entry(ANNOTATION, 3, b"A", 0, page=0, annotation=0),)


def test_an_annotation_two_pages_list():
    """Annotation 5 is listed by both pages, and twice by the second, which
    ISO 32000-2, 12.5.2, forbids: it is listed once, with the first page."""
    objects = {
        1: catalog(),
        2: b"<< /Type /Pages /Kids [3 0 R 8 0 R] /Count 2 >>",
        3: page(b" /Annots [5 0 R]"),
        5: annotation(b" /A 6 0 R"),
        6: URI,
        8: page(b" /Annots [5 0 R 9 0 R 5 0 R]"),
        9: annotation(b" /A 10 0 R"),
        10: JS,
    }
    found = active(build(objects, 1))
    assert found.actions == (action(b"URI", 6), action(b"JavaScript", 10))
    assert found.triggers == (
        entry(ANNOTATION, 5, b"A", 0, page=0, annotation=0),
        entry(ANNOTATION, 9, b"A", 1, page=1, annotation=1),
    )


def test_a_widget_that_is_its_fields():
    """One dictionary is the field and its widget: of its /AA, K and F are
    the field's, Fo and Bl the annotation's; the field's come after the
    pages."""
    widget = annotation(
        b" /FT /Tx /T (t) /AA << /K 6 0 R /F 7 0 R /Fo 8 0 R /Bl 9 0 R >>", b"/Widget"
    )
    objects = {5: widget, 6: JS, 7: JS, 8: JS, 9: JS}
    entries = b" /AcroForm << /Fields [5 0 R] >>"
    found = active(one_page(entries, b" /Annots [5 0 R]", objects=objects))
    assert found.actions == tuple(action(b"JavaScript", number) for number in (9, 8, 7, 6))
    assert found.triggers == (
        entry(ANNOTATION, 5, b"Bl", 0, page=0, annotation=0),
        entry(ANNOTATION, 5, b"Fo", 1, page=0, annotation=0),
        entry(FIELD, 5, b"F", 2, page=0, annotation=0),
        entry(FIELD, 5, b"K", 3, page=0, annotation=0),
    )


def test_a_field_and_its_widget():
    """Field 5 is no annotation: of its /AA, K is a field's event and Fo an
    annotation's, which MuPDF reads for its widgets through /Parent; no page
    lists field 5. Its widget 6 is an annotation a page lists and a field:
    Fo is the annotation's, K the field's."""
    objects = {
        5: b"<< /FT /Tx /T (t) /Kids [6 0 R] /AA << /K 10 0 R /Fo 11 0 R >> >>",
        6: annotation(b" /Parent 5 0 R /AA << /Fo 12 0 R /K 13 0 R >>", b"/Widget"),
        10: JS,
        11: JS,
        12: JS,
        13: JS,
    }
    entries = b" /AcroForm << /Fields [5 0 R] >>"
    found = active(one_page(entries, b" /Annots [6 0 R]", objects=objects))
    assert found.actions == tuple(action(b"JavaScript", number) for number in (12, 10, 11, 13))
    assert found.triggers == (
        entry(ANNOTATION, 6, b"Fo", 0, page=0, annotation=0),
        entry(FIELD, 5, b"K", 1),
        entry(ANNOTATION, 5, b"Fo", 2),
        entry(FIELD, 6, b"K", 3, page=0, annotation=0),
    )


def test_fields_beyond_the_field_tree():
    """Widget 5 is listed by the page and by no /Fields: PDFium takes it for a
    field once the page is loaded (measured). Widget 6 is in /Fields, its
    /Parent 7 is not; PDFium reads 7's K for it (measured). Field 7's /Parent
    is widget 6 again. The fields: those of /Fields, then the widgets, then
    the /Parent of each."""
    objects = {
        5: annotation(b" /FT /Tx /T (a) /AA << /K 10 0 R >>", b"/Widget"),
        6: annotation(b" /T (b) /Parent 7 0 R", b"/Widget"),
        7: b"<< /FT /Tx /T (p) /Kids [6 0 R] /Parent 6 0 R /AA << /K 11 0 R >> >>",
        10: JS,
        11: JS,
    }
    entries = b" /AcroForm << /Fields [6 0 R] >>"
    found = active(one_page(entries, b" /Annots [5 0 R 6 0 R]", objects=objects))
    assert found.actions == (action(b"JavaScript", 10), action(b"JavaScript", 11))
    assert found.triggers == (
        entry(FIELD, 5, b"K", 0, page=0, annotation=0),
        entry(FIELD, 7, b"K", 1),
    )


def test_the_parents_of_a_field():
    """Widget 5, on the page and in /Fields, has /Parent 6, which has /Parent
    7, whose /AA has K; field 8, in /Fields and on no page, has /Parent 9,
    whose /AA has K. Neither 6, 7 nor 9 is in /Fields: each /Parent is a
    field, and so is its own, taken in turn: 6 and 9, then 7."""
    objects = {
        5: annotation(b" /T (w) /Parent 6 0 R", b"/Widget"),
        6: b"<< /T (a) /Parent 7 0 R >>",
        7: b"<< /FT /Tx /T (b) /AA << /K 10 0 R >> >>",
        8: b"<< /T (c) /Parent 9 0 R >>",
        9: b"<< /FT /Tx /T (d) /AA << /K 11 0 R >> >>",
        10: JS,
        11: JS,
    }
    entries = b" /AcroForm << /Fields [5 0 R 8 0 R] >>"
    found = active(one_page(entries, b" /Annots [5 0 R]", objects=objects))
    assert found.triggers == (entry(FIELD, 9, b"K", 0), entry(FIELD, 7, b"K", 1))
    assert found.actions == (action(b"JavaScript", 11), action(b"JavaScript", 10))


def test_the_a_of_a_field_no_page_lists():
    """Field 5 is on no page: its /A, which only clicking an annotation runs,
    is no entry, and its action is among those no entry names."""
    objects = {5: b"<< /FT /Btn /T (b) /A 6 0 R >>", 6: JS}
    found = active(one_page(b" /AcroForm << /Fields [5 0 R] >>", objects=objects))
    assert (found.actions, found.triggers) == ((action(b"JavaScript", 6),), ())


def test_a_movie_annotation():
    """A Movie annotation's /A is a flag or a movie activation dictionary
    (ISO 32000-1, Table 186), no action; its /AA is an annotation's."""
    objects = {5: annotation(b" /A << /Duration 5 >> /AA << /PO 6 0 R >>", b"/Movie"), 6: JS}
    found = active(one_page(page_entries=b" /Annots [5 0 R]", objects=objects))
    assert found.actions == (action(b"JavaScript", 6),)
    assert found.triggers == (entry(ANNOTATION, 5, b"PO", 0, page=0, annotation=0),)


@pytest.mark.parametrize("names", [b"/Kids", b"/Names"])
def test_an_array_of_two_kinds_of_list(names):
    """Array 8 is the page's /Annots and the JavaScript name tree's /Kids, or
    its /Names: it is read as each. As /Names, its entry 5 is a value of the
    tree, listed as what the tree names, the link annotation itself."""
    objects = {5: annotation(b" /A " + URI), 8: b"[(a) 5 0 R]"}
    entries = b" /Names << /JavaScript << " + names + b" 8 0 R >> >>"
    found = active(one_page(entries, b" /Annots 8 0 R", objects=objects))
    link = entry(ANNOTATION, 5, b"A", 0, page=0, annotation=1)
    if names == b"/Kids":
        assert (found.actions, found.triggers) == ((action(b"URI", 5),), (link,))
    else:
        assert found.actions == (action(None, 5), action(b"URI", 5))
        place = ActionPlace.DOCUMENT_JAVASCRIPT
        assert found.triggers == (
            entry(place, 1, None, 0),
            entry(ANNOTATION, 5, b"A", 1, page=0, annotation=1),
        )


def test_more_streams_read_as_dictionaries():
    """/Names, its node 13 in /Kids, /Outlines, /AcroForm and the field 12 in
    /Kids written as streams, and a stream in an array of /Next: each read as
    its dictionary."""
    objects = {
        4: stream(b"", b" /JavaScript << /Kids [13 0 R] >>"),
        5: stream(b"", b" /First 6 0 R"),
        6: b"<< /Title (a) /A " + URI + b" >>",
        7: stream(b"", b" /Fields [8 0 R]"),
        8: b"<< /FT /Tx /T (t) /Kids [12 0 R] >>",
        9: JS,
        10: b"<< /S /Named /N /Print /Next [11 0 R] >>",
        11: stream(b"", b" /S /URI /URI (v)"),
        12: stream(b"", b" /T (k) /AA << /K " + JS + b" >>"),
        13: stream(b"", b" /Names [(a) 9 0 R]"),
    }
    entries = b" /OpenAction 10 0 R /Names 4 0 R /Outlines 5 0 R /AcroForm 7 0 R"
    found = active(one_page(entries, objects=objects))
    assert found.actions == (
        action(b"Named", 10, 0),
        action(b"URI", 11),
        action(b"JavaScript", 9),
        action(b"JavaScript", 12),
        action(b"URI", 6),
    )
    assert found.triggers == (
        entry(DOCUMENT, 1, b"OpenAction", 0),
        entry(ActionPlace.DOCUMENT_JAVASCRIPT, 13, None, 2),
        entry(FIELD, 12, b"K", 3),
        entry(ActionPlace.OUTLINE_ITEM, 6, b"A", 4),
    )
    assert found.sequences == ((1,),)


def test_two_widgets_written_in_the_page():
    """Two widgets written directly in the page's /Annots, in no /Fields: each
    is a field, named by the page that holds it, its page and its index."""
    first = annotation(b" /FT /Tx /T (a) /AA << /K " + JS + b" >>", b"/Widget")
    second = annotation(b" /FT /Tx /T (b) /AA << /K " + URI + b" >>", b"/Widget")
    found = active(one_page(page_entries=b" /Annots [" + first + b" " + second + b"]"))
    assert found.actions == (action(b"JavaScript", 3), action(b"URI", 3))
    assert found.triggers == (
        entry(FIELD, 3, b"K", 0, page=0, annotation=0),
        entry(FIELD, 3, b"K", 1, page=0, annotation=1),
    )


def test_a_field_only_co_lists():
    """Field 5 is in /AcroForm's /CO, not in /Fields: MuPDF runs its C."""
    objects = {5: b"<< /FT /Tx /T (c) /AA << /C 6 0 R >> >>", 6: JS}
    found = active(one_page(b" /AcroForm << /Fields [] /CO [5 0 R] >>", objects=objects))
    assert found.triggers == (entry(FIELD, 5, b"C", 0),)


def test_the_trees_depth_first():
    """/Fields [5 6], 5 with the kid 7: fields 5, 7, 6. The name tree's root
    has the kids 10 and 11, 10 the kid 12: 12's entry, then 11's."""
    objects = {
        5: b"<< /FT /Tx /T (a) /Kids [7 0 R] /AA << /K 8 0 R >> >>",
        6: b"<< /FT /Tx /T (b) /AA << /K 9 0 R >> >>",
        7: b"<< /T (c) /AA << /K 13 0 R >> >>",
        8: JS,
        9: URI,
        10: b"<< /Kids [12 0 R] >>",
        11: b"<< /Names [(b) 14 0 R] >>",
        12: b"<< /Names [(a) 15 0 R] >>",
        13: b"<< /S /Named /N /Print >>",
        14: b"<< /S /Hide /T (h) >>",
        15: b"<< /S /ResetForm >>",
    }
    entries = (
        b" /AcroForm << /Fields [5 0 R 6 0 R] >>"
        b" /Names << /JavaScript << /Kids [10 0 R 11 0 R] >> >>"
    )
    found = active(one_page(entries, objects=objects))
    assert [(t.place, t.owner[0]) for t in found.triggers] == [
        (ActionPlace.DOCUMENT_JAVASCRIPT, 12),
        (ActionPlace.DOCUMENT_JAVASCRIPT, 11),
        (FIELD, 5),
        (FIELD, 7),
        (FIELD, 6),
    ]
    assert [a.kind for a in found.actions] == [
        b"ResetForm",
        b"Hide",
        b"JavaScript",
        b"Named",
        b"URI",
    ]


def test_annots_that_are_no_dictionaries():
    """/Annots [null 5 0 R 7]: annotation 5 is at index 1."""
    objects = {5: annotation(b" /A " + URI)}
    found = active(one_page(page_entries=b" /Annots [null 5 0 R 7]", objects=objects))
    assert found.triggers == (entry(ANNOTATION, 5, b"A", 0, page=0, annotation=1),)


def test_a_field_in_fields_and_co():
    """Field 5 is in /Fields and in /CO, as a field with a C usually is: it is
    listed once."""
    objects = {5: b"<< /FT /Tx /T (c) /AA << /C 6 0 R >> >>", 6: JS}
    found = active(one_page(b" /AcroForm << /Fields [5 0 R] /CO [5 0 R] >>", objects=objects))
    assert found.triggers == (entry(FIELD, 5, b"C", 0),)


def test_the_fields_of_co_before_the_widgets():
    """Field 5 is in /CO only, widget 6 on the page only: 5 comes first."""
    objects = {
        5: b"<< /FT /Tx /T (c) /AA << /C 7 0 R >> >>",
        6: annotation(b" /FT /Tx /T (w) /AA << /K 8 0 R >>", b"/Widget"),
        7: JS,
        8: URI,
    }
    entries = b" /AcroForm << /Fields [] /CO [5 0 R] >>"
    found = active(one_page(entries, b" /Annots [6 0 R]", objects=objects))
    assert found.triggers == (
        entry(FIELD, 5, b"C", 0),
        entry(FIELD, 6, b"K", 1, page=0, annotation=0),
    )


def test_each_page_then_its_annotations():
    """Pages 3 and 4, each with /AA /O and an annotation: page 3's event, its
    annotation's, then page 4's, its annotation's."""
    objects = {
        1: catalog(),
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        3: page(b" /AA << /O 7 0 R >> /Annots [5 0 R]"),
        4: page(b" /AA << /O 8 0 R >> /Annots [6 0 R]"),
        5: annotation(b" /A 9 0 R"),
        6: annotation(b" /A 10 0 R"),
        7: JS,
        8: JS,
        9: URI,
        10: URI,
    }
    found = active(build(objects, 1))
    assert found.triggers == (
        entry(PAGE, 3, b"O", 0, page=0),
        entry(ANNOTATION, 5, b"A", 1, page=0, annotation=0),
        entry(PAGE, 4, b"O", 2, page=1),
        entry(ANNOTATION, 6, b"A", 3, page=1, annotation=0),
    )


def test_a_warning_that_is_no_misreading():
    """Object 5, which no object names, is listed at offset 0: QPDF warns,
    naming the object and no offset (measured), which is no mis-read object;
    the values are given, with the warning."""
    data = one_page(objects={4: URI, 5: b"null"})
    table = data.rindex(b"\nxref\n0 ") + 1
    lines = data[table:].split(b"\n")
    assert lines[2 + 5].endswith(b" 00000 n ")
    lines[2 + 5] = b"0000000000 00000 n "
    found = active(data[:table] + b"\n".join(lines))
    assert (found.misread, found.actions) == (False, (action(b"URI", 4),))
    assert [message[message.index("(object") :] for message in found.warnings] == [
        "(object 5 0): object has offset 0 - a common error handled correctly by qpdf and"
        " most other applications"
    ]


def test_the_places_named():
    assert [place.value for place in ActionPlace] == [
        "document",
        "document javascript",
        "page",
        "annotation",
        "field",
        "outline item",
    ]


def test_a_field_two_parents_name():
    """Field 7 is a kid of fields 5 and 6, each with /Kids of its own: it is
    listed once."""
    objects = {
        5: b"<< /FT /Tx /T (a) /Kids [7 0 R] >>",
        6: b"<< /FT /Tx /T (b) /Kids [7 0 R] >>",
        7: b"<< /T (c) /AA << /K 8 0 R >> >>",
        8: JS,
    }
    found = active(one_page(b" /AcroForm << /Fields [5 0 R 6 0 R] >>", objects=objects))
    assert found.triggers == (entry(FIELD, 7, b"K", 0),)


def test_the_events_of_each_place():
    """Each place's events of ISO 32000-2, Tables 197 to 200, in the order of
    their keys: the catalog's five, a page's two (of the second page here), a
    field's four; an annotation's ten are in
    test_the_events_in_the_order_of_their_keys."""

    def additional(events: tuple[bytes, ...]) -> bytes:
        return (
            b" /AA << " + b" ".join(b"/%s << /S /Named /N /%s >>" % (e, e) for e in events) + b" >>"
        )

    document_events = (b"WC", b"WS", b"DS", b"WP", b"DP")
    field_events = (b"K", b"F", b"V", b"C")
    objects = {
        1: catalog(additional(document_events) + b" /AcroForm << /Fields [5 0 R] >>"),
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        3: page(),
        4: page(additional((b"O", b"C"))),
        5: b"<< /FT /Tx /T (t)" + additional(field_events) + b" >>",
    }
    found = active(build(objects, 1))
    expected = (
        [(DOCUMENT, 1, event, None) for event in sorted(document_events)]
        + [(PAGE, 4, event, 1) for event in (b"C", b"O")]
        + [(FIELD, 5, event, None) for event in sorted(field_events)]
    )
    assert [(t.place, t.owner[0], t.event, t.page) for t in found.triggers] == expected
    assert [a.kind for a in found.actions] == [b"Named"] * len(expected)


def test_keys_that_are_no_events_of_their_place():
    """An /AA entry whose key is no event of its place runs nothing: O on the
    catalog, WC on a page, K on an annotation that is no field, O on a field,
    /Foo anywhere. Each place's own event is listed; the other actions are
    among those no entry names, in object order."""
    objects = {
        5: annotation(b" /AA << /K 10 0 R /E 11 0 R /Foo 12 0 R >>"),
        6: b"<< /FT /Tx /T (t) /AA << /O 13 0 R /V 14 0 R >> >>",
        7: JS,
        8: JS,
        9: JS,
        10: JS,
        11: JS,
        12: JS,
        13: JS,
        14: JS,
        15: JS,
    }
    entries = b" /AA << /O 7 0 R /WC 8 0 R /Foo 9 0 R >> /AcroForm << /Fields [6 0 R] >>"
    page_entries = b" /Annots [5 0 R] /AA << /WC 15 0 R >>"
    found = active(one_page(entries, page_entries, objects))
    assert found.triggers == (
        entry(DOCUMENT, 1, b"WC", 0),
        entry(ANNOTATION, 5, b"E", 1, page=0, annotation=0),
        entry(FIELD, 6, b"V", 2),
    )
    assert found.actions == tuple(
        action(b"JavaScript", number) for number in (8, 11, 14, 7, 9, 10, 12, 13, 15)
    )


def test_a_field_tree_that_loops():
    objects = {
        5: b"<< /FT /Tx /T (a) /Kids [6 0 R] >>",
        6: b"<< /T (b) /Kids [5 0 R] /AA << /K 7 0 R >> >>",
        7: JS,
    }
    found = active(one_page(b" /AcroForm << /Fields [5 0 R] >>", objects=objects))
    assert found.triggers == (entry(FIELD, 6, b"K", 0),)


def test_the_outline():
    """Item 5, its first child 6, whose /Next names item 5 again, then item
    5's /Next, 7; item 8 has a destination and no action. The outline's own
    dictionary, 4, is no item: its /A is no entry."""
    objects = {
        4: b"<< /First 5 0 R /A 11 0 R >>",
        5: b"<< /Title (a) /A 9 0 R /First 6 0 R /Next 7 0 R >>",
        6: b"<< /Title (a1) /A << /S /GoTo /D [3 0 R /Fit] >> /Next 5 0 R >>",
        7: b"<< /Title (b) /A 10 0 R /Next 8 0 R >>",
        8: b"<< /Title (c) /Dest [3 0 R /Fit] >>",
        9: URI,
        10: JS,
        11: b"<< /S /Named /N /Print >>",
    }
    found = active(one_page(b" /Outlines 4 0 R", objects=objects))
    assert found.actions == (
        action(b"URI", 9),
        action(b"GoTo", 6),
        action(b"JavaScript", 10),
        action(b"Named", 11),
    )
    item = ActionPlace.OUTLINE_ITEM
    assert found.triggers == (
        entry(item, 5, b"A", 0),
        entry(item, 6, b"A", 1),
        entry(item, 7, b"A", 2),
    )


def test_next():
    """Action 4's /Next names action 5, whose /Next names 4 again, a direct
    action whose /Next is a dictionary, and 6, which is no dictionary: each
    action is listed once, depth first, and a /Next that names one again
    names its index."""
    following = b"<< /S /Named /N /Print /Next << /S /Hide /T (x) >> >>"
    objects = {
        4: b"<< /S /JavaScript /JS (a) /Next [5 0 R " + following + b" 6] >>",
        5: b"<< /S /URI /URI (u) /Next 4 0 R >>",
    }
    found = active(one_page(b" /OpenAction 4 0 R", objects=objects))
    assert found.actions == (
        action(b"JavaScript", 4, 0),
        action(b"URI", 5, 1),
        action(b"Named", 4, 2),
        action(b"Hide", 4),
    )
    assert found.sequences == ((1, 2), (0,), (3,))
    assert found.triggers == (entry(DOCUMENT, 1, b"OpenAction", 0),)


def test_an_action_two_entries_name():
    found = active(one_page(b" /OpenAction 4 0 R /AA << /WC 4 0 R >>", objects={4: JS}))
    assert found.actions == (action(b"JavaScript", 4),)
    assert found.triggers == (entry(DOCUMENT, 1, b"OpenAction", 0), entry(DOCUMENT, 1, b"WC", 0))


def test_the_kind_as_written():
    """A dictionary an entry names is listed whatever its /S: absent, a
    string, which is no name, a name no action type of ISO 32000-2 has."""
    entries = b" /OpenAction << /JS (a) >> /AA << /WC << /S (JavaScript) >> /WS << /S /Foo >> >>"
    found = active(one_page(entries))
    no_name = "/S of the action is no name"
    assert found.actions == (action(None, 1), action(no_name, 1), action(b"Foo", 1))


@pytest.mark.parametrize(
    "objects, expected",
    [
        ({4: JS}, (action(b"JavaScript", 4),)),
        ({4: b"<< /Type /Action /S /Foo >>"}, (action(b"Foo", 4),)),
        ({4: b"<< /Type /Action >>"}, (action(None, 4),)),
        ({4: b"<< /S /Foo >>"}, ()),
        ({4: annotation(b" /A " + URI)}, (action(b"URI", 4),)),
        ({6: JS, 4: URI}, (action(b"URI", 4), action(b"JavaScript", 6))),
        (
            {4: b"<< /B " + JS + b" /A " + URI + b" >>"},
            (action(b"URI", 4), action(b"JavaScript", 4)),
        ),
        ({4: stream(b"x", b" /S /JavaScript")}, (action(b"JavaScript", 4),)),
        ({4: stream(b"x", b" /A " + URI)}, (action(b"URI", 4),)),
        (
            {4: b"<< /S /URI /URI (u) /Next 5 0 R >>", 5: b"<< /S /Foo >>"},
            (action(b"URI", 4, 0), action(b"Foo", 5)),
        ),
    ],
    ids=[
        "an action of its own",
        "/Type /Action",
        "/Type /Action, no /S",
        "no action type, no /Type",
        "in an annotation no page lists",
        "in object order",
        "in one object, in the order of the keys",
        "a stream, read as its dictionary",
        "in a stream's dictionary",
        "its /Next",
    ],
)
def test_actions_no_entry_names(objects, expected):
    """Found among the objects: a dictionary whose /S names an action type of
    ISO 32000-2, Table 201, or whose /Type is /Action, and the dictionaries
    its /Next names."""
    found = active(one_page(objects=objects))
    assert (found.actions, found.triggers) == (expected, ())


@pytest.mark.parametrize(
    "kind",
    [
        b"GoTo",
        b"GoToR",
        b"GoToE",
        b"GoToDp",
        b"Launch",
        b"Thread",
        b"URI",
        b"Sound",
        b"Movie",
        b"Hide",
        b"Named",
        b"SubmitForm",
        b"ResetForm",
        b"ImportData",
        b"SetOCGState",
        b"Rendition",
        b"Trans",
        b"GoTo3DView",
        b"JavaScript",
        b"RichMediaExecute",
    ],
)
def test_each_action_type(kind):
    """Each action type of ISO 32000-2, Table 201, found where no entry names
    it."""
    found = active(one_page(objects={4: b"<< /S /" + kind + b" >>"}))
    assert found.actions == (action(kind, 4),)


def test_streams_read_as_dictionaries():
    """PDFium reads a stream as the action of /OpenAction, of a link's or an
    outline item's /A, and as a page's /AA; it does not for a /Next, a value
    of the name tree, or an annotation (measured). Every one is read as its
    dictionary, as a reader such as MuPDF reads every stream: the outline
    items 12 and 14, the /Next of 12, and the annotation 13 are streams
    too."""
    objects = {
        4: stream(b"", b" /S /Named /N /NextPage /Next 5 0 R"),
        5: stream(b"", b" /S /URI /URI (u)"),
        6: annotation(b" /A 7 0 R"),
        7: stream(b"", b" /S /URI /URI (v)"),
        8: stream(b"", b" /O 9 0 R"),
        9: JS,
        10: stream(b"", b" /S /JavaScript /JS (1)"),
        11: b"<< /First 12 0 R >>",
        12: stream(b"", b" /Title (a) /A " + URI + b" /Next 14 0 R"),
        14: stream(b"", b" /Title (b) /A << /S /Named /N /Print >>"),
        13: stream(b"", b" /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A " + URI),
    }
    entries = (
        b" /OpenAction 4 0 R /Outlines 11 0 R /Names << /JavaScript << /Names [(a) 10 0 R] >> >>"
    )
    found = active(one_page(entries, b" /Annots [6 0 R 13 0 R] /AA 8 0 R", objects))
    assert found.actions == (
        action(b"Named", 4, 0),
        action(b"URI", 5),
        action(b"JavaScript", 10),
        action(b"JavaScript", 9),
        action(b"URI", 7),
        action(b"URI", 13),
        action(b"URI", 12),
        action(b"Named", 14),
    )
    assert found.triggers == (
        entry(DOCUMENT, 1, b"OpenAction", 0),
        entry(ActionPlace.DOCUMENT_JAVASCRIPT, 1, None, 2),
        entry(PAGE, 3, b"O", 3, page=0),
        entry(ANNOTATION, 6, b"A", 4, page=0, annotation=0),
        entry(ANNOTATION, 13, b"A", 5, page=0, annotation=1),
        entry(ActionPlace.OUTLINE_ITEM, 12, b"A", 6),
        entry(ActionPlace.OUTLINE_ITEM, 14, b"A", 7),
    )
    assert found.sequences == ((1,),)


def test_what_is_shared_is_listed_once():
    """Actions 10 to 59 each name one /Next array, 60, of all fifty: it is
    listed once, and each names its index. Annotations 61 to 110 share one
    /AA, 111, of fifty keys that are no events and E: each is listed with
    its E. Name tree nodes 112 to 161 share one /Names, 162, of fifty
    entries: each entry is listed once, with the first node."""
    objects = {}
    for i in range(50):
        objects[10 + i] = b"<< /S /Named /N /Print /Next 60 0 R >>"
        objects[61 + i] = annotation(b" /AA 111 0 R")
        objects[112 + i] = b"<< /Names 162 0 R >>"
    objects[60] = b"[" + b" ".join(b"%d 0 R" % (10 + i) for i in range(50)) + b"]"
    keys = b" ".join(b"/k%d 9 0 R" % i for i in range(50))
    objects[111] = b"<< " + keys + b" /E 8 0 R >>"
    objects[162] = b"[" + b" ".join(b"(n%02d) 7 0 R" % i for i in range(50)) + b"]"
    objects.update({7: JS, 8: URI, 9: JS})
    kids = b" ".join(b"%d 0 R" % (112 + i) for i in range(50))
    annots = b" ".join(b"%d 0 R" % (61 + i) for i in range(50))
    entries = b" /OpenAction 10 0 R /Names << /JavaScript << /Kids [" + kids + b"] >> >>"
    found = active(one_page(entries, b" /Annots [" + annots + b"]", objects))
    chain = found.actions[:50]
    assert chain == tuple(action(b"Named", 10 + i, 0) for i in range(50))
    assert found.sequences == (tuple(range(50)),)
    javascript = [t for t in found.triggers if t.place is ActionPlace.DOCUMENT_JAVASCRIPT]
    assert javascript == [entry(ActionPlace.DOCUMENT_JAVASCRIPT, 112, None, 50)] * 50
    annotations = [t for t in found.triggers if t.place is ANNOTATION]
    assert annotations == [
        entry(ANNOTATION, 61 + i, b"E", 51, page=0, annotation=i) for i in range(50)
    ]
    assert found.actions[50:] == (
        action(b"JavaScript", 7),
        action(b"URI", 8),
        action(b"JavaScript", 9),
    )


def test_names_of_other_bytes():
    """pikepdf gives a name as a str by decoding it as UTF-8, and raises for
    /Caf#E9 (measured): names are compared as bytes. Object 4 is a structure
    element whose /S is such a name, no action; object 5 an action by its
    /Type, its kind those bytes; object 6 has such a /Type; annotation 7,
    such a /Subtype, and an /A."""
    objects = {
        4: b"<< /Type /StructElem /S /Caf#E9 >>",
        5: b"<< /Type /Action /S /Caf#E9 >>",
        6: b"<< /Type /Caf#E9 >>",
        7: annotation(b" /A 8 0 R", b"/Caf#E9"),
        8: URI,
    }
    found = active(one_page(page_entries=b" /Annots [7 0 R]", objects=objects))
    assert found.actions == (action(b"URI", 8), action(b"Caf\xe9", 5))
    assert found.triggers == (entry(ANNOTATION, 7, b"A", 0, page=0, annotation=0),)


def test_an_ef_many_file_specifications_share():
    """File specifications 10 to 59 name one /EF, 4, of fifty streams under
    a hundred keys, each stream twice: each is listed, the /EF once, its
    fifty streams each once."""
    keys = b" ".join(b"/k%02d %d 0 R /m%02d %d 0 R" % (i, 60 + i, i, 60 + i) for i in range(50))
    objects = {4: b"<< " + keys + b" >>"}
    for i in range(50):
        objects[10 + i] = b"<< /EF 4 0 R >>"
        objects[60 + i] = stream(b"%d" % i)
    found = active(one_page(objects=objects))
    assert found.embedded_files == tuple(
        EmbeddedFile(specification=(10 + i, 0), streams=0) for i in range(50)
    )
    assert found.embedded_streams == (tuple((60 + i, 0) for i in range(50)),)


COUNT = 1500


def shared_annots(shared: bool) -> bytes:
    kids = b" ".join(b"%d 0 R" % (10 + i) for i in range(COUNT))
    objects = {1: catalog(), 2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, COUNT)}
    listed = 10 + 2 * COUNT
    objects[listed] = b"[" + b" ".join(b"%d 0 R" % (10 + COUNT + i) for i in range(COUNT)) + b"]"
    for i in range(COUNT):
        own = b"[%d 0 R]" % (10 + COUNT + i)
        objects[10 + i] = page(b" /Annots " + (b"%d 0 R" % listed if shared else own))
        objects[10 + COUNT + i] = annotation(b" /A " + URI)
    return build(objects, 1)


def shared_next(shared: bool) -> bytes:
    listed = 10 + COUNT
    objects = {listed: b"[" + b" ".join(b"%d 0 R" % (10 + i) for i in range(COUNT)) + b"]"}
    for i in range(COUNT):
        following = b"%d 0 R" % listed if shared else b"[%d 0 R]" % (10 + (i + 1) % COUNT)
        objects[10 + i] = b"<< /S /Named /N /Print /Next " + following + b" >>"
    return one_page(b" /OpenAction 10 0 R", objects=objects)


def shared_field_kids(shared: bool) -> bytes:
    listed = 10 + 2 * COUNT
    objects = {listed: b"[" + b" ".join(b"%d 0 R" % (10 + COUNT + i) for i in range(COUNT)) + b"]"}
    for i in range(COUNT):
        kids = b"%d 0 R" % listed if shared else b"[%d 0 R]" % (10 + COUNT + i)
        objects[10 + i] = b"<< /FT /Tx /T (f) /Kids " + kids + b" >>"
        objects[10 + COUNT + i] = b"<< /T (k) /AA << /K 9 0 R >> >>"
    objects[9] = JS
    fields = b" ".join(b"%d 0 R" % (10 + i) for i in range(COUNT))
    return one_page(b" /AcroForm << /Fields [" + fields + b"] >>", objects=objects)


def shared_name_kids(shared: bool) -> bytes:
    listed = 10 + 2 * COUNT
    objects = {listed: b"[" + b" ".join(b"%d 0 R" % (10 + COUNT + i) for i in range(COUNT)) + b"]"}
    for i in range(COUNT):
        kids = b"%d 0 R" % listed if shared else b"[%d 0 R]" % (10 + COUNT + i)
        objects[10 + i] = b"<< /Kids " + kids + b" >>"
        objects[10 + COUNT + i] = b"<< /Names [(a) 9 0 R] >>"
    objects[9] = JS
    nodes = b" ".join(b"%d 0 R" % (10 + i) for i in range(COUNT))
    return one_page(b" /Names << /JavaScript << /Kids [" + nodes + b"] >> >>", objects=objects)


def shared_additional(shared: bool) -> bytes:
    keys = b" ".join(b"/k%d 9 0 R" % i for i in range(COUNT))
    objects = {8: b"<< " + keys + b" /E 9 0 R >>", 9: JS}
    for i in range(COUNT):
        own = b"<< /k0 9 0 R /E 9 0 R >>"
        objects[10 + i] = annotation(b" /AA " + (b"8 0 R" if shared else own))
    annots = b" ".join(b"%d 0 R" % (10 + i) for i in range(COUNT))
    return one_page(page_entries=b" /Annots [" + annots + b"]", objects=objects)


def shared_embedded(shared: bool) -> bytes:
    keys = b" ".join(b"/k%d %d 0 R" % (i, 10 + COUNT + i) for i in range(COUNT))
    objects = {8: b"<< " + keys + b" >>"}
    for i in range(COUNT):
        own = b"<< /F %d 0 R >>" % (10 + COUNT + i)
        objects[10 + i] = b"<< /EF " + (b"8 0 R" if shared else own) + b" >>"
        objects[10 + COUNT + i] = stream(b"a")
    return one_page(objects=objects)


@pytest.mark.parametrize(
    "build_file",
    [
        shared_annots,
        shared_next,
        shared_field_kids,
        shared_name_kids,
        shared_additional,
        shared_embedded,
    ],
    ids=[
        "an /Annots of all, each page's",
        "a /Next of all, each action's",
        "a /Kids of all, each field's",
        "a /Kids of all, each node's",
        "an /AA of all and as many other keys, each annotation's",
        "an /EF of all, each file specification's",
    ],
)
def test_what_is_shared_takes_no_longer(build_file):
    """1500 owners each name one array or dictionary of 1500 entries: read
    once, it takes about as long as 1500 owners each with one entry of its
    own; read for each owner, it took from 9 to 95 times as long (measured).
    The best of three runs of each, within three times."""

    def seconds(data: bytes) -> float:
        document = QpdfDocument.open(data, None)
        try:
            best = []
            for _ in range(3):
                start = time.perf_counter()
                document.active_content()
                best.append(time.perf_counter() - start)
            return min(best)
        finally:
            document.close()

    assert seconds(build_file(True)) < 3 * seconds(build_file(False))


def test_a_key_that_runs_nothing():
    """/PA of a link annotation keeps a former action (ISO 32000-2, Table
    176) and runs nothing: its action is found among the objects."""
    objects = {5: annotation(b" /A 6 0 R /PA " + URI), 6: JS}
    found = active(one_page(page_entries=b" /Annots [5 0 R]", objects=objects))
    assert found.actions == (action(b"JavaScript", 6), action(b"URI", 5))
    assert found.triggers == (entry(ANNOTATION, 5, b"A", 0, page=0, annotation=0),)


def test_actions_written_deep_among_many():
    """Object 4 nests arrays 300 deep, 200 empty arrays beside each, and 2000
    actions in the deepest. What the reading of every object keeps grows with
    how deep a value is written, not with how many are written beside it, and
    a place is one pair per step, its path shared: Python's peak stays under
    3 MiB (measured: 1.0 MiB; 14 MiB where every value beside waits, 5.5 MiB
    where each place holds its whole path)."""
    siblings = b" ".join([b"[]"] * 200)
    value = b"[" + b" ".join([URI] * 2000) + b"]"
    for _ in range(300):
        value = b"[" + value + b" " + siblings + b"]"
    document = QpdfDocument.open(one_page(objects={4: value}), None)
    tracemalloc.start()
    try:
        found = document.active_content()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
        document.close()
    assert found.actions == (action(b"URI", 4),) * 2000
    assert peak < 3 * 2**20


def test_actions_in_an_object_stream():
    """Object 4 and annotation 5 are compressed: each is written in its own
    object, not in the object stream."""
    objects = {1: catalog(b" /OpenAction 4 0 R"), 2: PAGES, 3: page(b" /Annots [5 0 R]")}
    compressed = [(4, JS), (5, annotation(b" /A " + URI))]
    found = active(build_with_object_stream(objects, compressed, {4: 0, 5: 1}, 1))
    assert found.actions == (action(b"JavaScript", 4), action(b"URI", 5))
    assert found.triggers == (
        entry(DOCUMENT, 1, b"OpenAction", 0),
        entry(ANNOTATION, 5, b"A", 1, page=0, annotation=0),
    )


def test_the_order_of_the_entries():
    objects = {
        4: b"<< /Names [(a) 5 0 R] >>",
        5: JS,
        6: annotation(b" /A " + URI),
        7: b"<< /FT /Tx /T (t) /AA << /K " + JS + b" >> >>",
        8: b"<< /First 9 0 R >>",
        9: b"<< /Title (a) /A " + URI + b" >>",
    }
    entries = (
        b" /Outlines 8 0 R /AcroForm << /Fields [7 0 R] >> /Names << /JavaScript 4 0 R >>"
        b" /OpenAction " + JS
    )
    page_entries = b" /Annots [6 0 R] /AA << /O " + URI + b" >>"
    found = active(one_page(entries, page_entries, objects))
    assert [trigger.place for trigger in found.triggers] == [
        DOCUMENT,
        ActionPlace.DOCUMENT_JAVASCRIPT,
        PAGE,
        ANNOTATION,
        FIELD,
        ActionPlace.OUTLINE_ITEM,
    ]
    assert [trigger.action for trigger in found.triggers] == [0, 1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "entries, objects, expected",
    [
        (b"", {}, None),
        (b" /AcroForm << /Fields [] >>", {}, None),
        (b" /AcroForm << /XFA 5 0 R >>", {5: stream(b"<xdp/>")}, ((5, 0),)),
        (
            b" /AcroForm << /XFA [(template) 5 0 R (datasets) 6 0 R] >>",
            {5: stream(b"<a/>"), 6: stream(b"<b/>")},
            ((5, 0), (6, 0)),
        ),
        (b" /AcroForm << /XFA /Foo >>", {}, "/XFA is neither a stream nor an array"),
        (
            b" /AcroForm << /XFA [(a) 5 0 R (b) 6 0 R] >>",
            {5: stream(b"<a/>"), 6: b"(x)"},
            "/XFA is an array, but not of pairs of a packet's name and its stream",
        ),
        (b" /AcroForm 7 0 R", {7: stream(b"", b" /XFA 5 0 R"), 5: stream(b"<xdp/>")}, ((5, 0),)),
    ],
    ids=[
        "no /AcroForm",
        "no /XFA",
        "a stream",
        "an array",
        "neither",
        "an object of the array no stream",
        "/AcroForm a stream, read as its dictionary",
    ],
)
def test_xfa(entries, objects, expected):
    assert active(one_page(entries, objects=objects)).xfa == expected


def test_an_xfa_stream_typed_as_an_embedded_file():
    """The corpus's XFA streams have /Type /EmbeddedFile; only a file
    specification's /EF names an embedded file."""
    objects = {5: stream(b"<xdp/>", b" /Type /EmbeddedFile")}
    found = active(one_page(b" /AcroForm << /XFA 5 0 R >>", objects=objects))
    assert (found.xfa, found.embedded_files) == (((5, 0),), ())


def file(specification: int, streams: int) -> EmbeddedFile:
    return EmbeddedFile(specification=(specification, 0), streams=streams)


@pytest.mark.parametrize(
    "objects, files, streams",
    [
        (
            {4: b"<< /Type /Filespec /F (a) /EF << /F 5 0 R /UF 5 0 R >> >>", 5: stream(b"a")},
            (file(4, 0),),
            (((5, 0),),),
        ),
        (
            {4: b"<< /EF << /UF 6 0 R /F 5 0 R >> >>", 5: stream(b"a"), 6: stream(b"b")},
            (file(4, 0),),
            (((5, 0), (6, 0)),),
        ),
        (
            {
                4: annotation(b" /FS << /Type /Filespec /EF << /F 5 0 R >> >>", b"/FileAttachment"),
                5: stream(b"a"),
            },
            (file(4, 0),),
            (((5, 0),),),
        ),
        (
            {6: b"<< /EF << /F 5 0 R >> >>", 4: b"<< /EF << /F 7 >> >>", 5: stream(b"a")},
            (file(4, 0), file(6, 1)),
            ((), ((5, 0),)),
        ),
        ({4: b"<< /EF 5 0 R >>", 5: stream(b"a")}, (file(4, 0),), ((),)),
        ({4: b"<< /EF 7 >>"}, (), ()),
        ({4: stream(b"", b" /EF << /F 5 0 R >>"), 5: stream(b"a")}, (file(4, 0),), (((5, 0),),)),
    ],
    ids=[
        "two keys, one stream",
        "two streams, in the order of their keys",
        "written in an annotation",
        "in object order, a stream or not",
        "/EF a stream, read as its dictionary",
        "/EF neither",
        "a file specification that is a stream",
    ],
)
def test_embedded_files(objects, files, streams):
    found = active(one_page(objects=objects))
    assert (found.embedded_files, found.embedded_streams) == (files, streams)


def test_a_stream_qpdf_cannot_read():
    """QPDF, its recovery off, reads object 5 as null and warns that it
    mis-read it (FileStructure.misread): nothing here is known."""
    objects = {
        4: JS,
        5: b"<< /Length 10 >>\nstream\n0 0 m 10 10 l S\nendstream",
        6: stream(b"<xdp/>"),
    }
    found = active(one_page(b" /OpenAction 4 0 R /AcroForm << /XFA 6 0 R >>", objects=objects))
    assert found.misread is True
    values = (found.actions, found.sequences, found.triggers, found.xfa)
    assert values + (found.embedded_files, found.embedded_streams) == (None,) * 6
    assert any("(object 5 0, offset" in message for message in found.warnings)


def test_the_warnings_in_the_order_qpdf_gave_them():
    """Objects 5 and 6 are streams QPDF cannot read; reading every object, in
    object order, it warns of 5, then of 6 (measured)."""
    unreadable = b"<< /Length 10 >>\nstream\n0 0 m 10 10 l S\nendstream"
    found = active(one_page(objects={5: unreadable, 6: unreadable}))
    named = [message[message.index("(object") :] for message in found.warnings]
    assert [message.split(",")[0] for message in named] == ["(object 5 0", "(object 6 0"]


def test_the_trailer_of_a_table_is_not_read():
    """The trailer of a cross-reference table is no object: an action written
    in it is not looked for."""
    data = one_page()
    written = data.replace(b"/Root 1 0 R", b"/Root 1 0 R /X << /S /JavaScript /JS (1) >>")
    assert written != data
    assert active(written).actions == ()


def test_no_action_where_the_pages_are_not_the_files():
    """The page tree lists page 3 twice: QPDF copies the second, so its
    pages are not the file's objects (test_backend_qpdf_structure)."""
    objects = {
        1: catalog(b" /OpenAction 4 0 R /AcroForm << /XFA 5 0 R >>"),
        2: b"<< /Type /Pages /Kids [3 0 R 3 0 R] /Count 2 >>",
        3: page(),
        4: JS,
        5: stream(b"<xdp/>"),
        6: b"<< /EF << /F 5 0 R >> >>",
    }
    found = active(build(objects, 1))
    assert (found.misread, found.actions, found.sequences, found.triggers) == (
        False,
        None,
        None,
        None,
    )
    assert found.xfa == ((5, 0),)
    assert found.embedded_files == (file(6, 0),)
    assert found.embedded_streams == (((5, 0),),)


def test_an_encrypted_file():
    data = encrypt(one_page(b" /OpenAction << /S /JavaScript /JS (1) >>"), user="u", owner="o")
    found = active(data, "u")
    assert [(a.kind, a.next) for a in found.actions] == [(b"JavaScript", None)]
    assert [(t.place, t.event, t.action) for t in found.triggers] == [(DOCUMENT, b"OpenAction", 0)]
    assert found.actions[0].written_in == found.triggers[0].owner


@pytest.mark.parametrize("pages", [1, 2], ids=["named by no page", "another page's /Contents"])
def test_the_active_content_changes_no_other_answer(pages):
    """Object 5 is a stream QPDF cannot read: named by no page, or the
    /Contents of a second page. QPDF warns of it only when it reads it
    (measured), so reading every object for the active content must leave
    what the document answers otherwise as it was (as structure())."""
    kids = b" ".join(b"%d 0 R" % number for number in (3, 6)[:pages])
    objects = {
        1: catalog(),
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages),
        3: page(b" /Contents 4 0 R"),
        4: stream(b"0 0 m 10 10 l S"),
        5: b"<< /Length 10 >>\nstream\n0 0 m 10 10 l S\nendstream",
        6: page(b" /Contents 5 0 R"),
    }
    data = build(objects, 1)

    def answers(document: QpdfDocument) -> tuple:
        return (*(document.parsed_streams(index) for index in range(pages)), document.warnings())

    alone = QpdfDocument.open(data, None)
    second = QpdfDocument.open(data, None)
    try:
        expected = answers(alone)
        found = second.active_content()
        after = answers(second)
    finally:
        alone.close()
        second.close()
    assert after == expected
    assert found.misread is True


def test_a_catalog_written_in_the_trailer():
    """PDFium opens no such file, so open() never reads one; QPDF does, and
    no object holds what the catalog holds."""
    data = one_page(b" /OpenAction 4 0 R", objects={4: JS})
    written = catalog(b" /OpenAction 4 0 R")
    data = data.replace(b"/Root 1 0 R", b"/Root " + written)
    document = QpdfDocument.open(data, None)
    try:
        with pytest.raises(EngineFailure) as raised:
            document.active_content()
    finally:
        document.close()
    assert (raised.value.kind, raised.value.detail) == (
        FailureKind.OTHER,
        "the trailer's /Root is not a reference",
    )


def test_closed():
    document = QpdfDocument.open(one_page(), None)
    document.close()
    with pytest.raises(EngineFailure) as raised:
        document.active_content()
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")


def test_the_values_are_frozen():
    objects = {4: JS, 5: b"<< /EF << /F 6 0 R >> >>", 6: stream(b"a")}
    found = active(one_page(b" /OpenAction 4 0 R", objects=objects))
    for value in (found, found.actions[0], found.triggers[0], found.embedded_files[0]):
        field = dataclasses.fields(value)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, None)


def test_it_implements_the_active_content_reader():
    members = typing.get_protocol_members(ActiveContentReader)
    assert members == {"active_content"}
    assert all(callable(getattr(QpdfDocument, name)) for name in members)
