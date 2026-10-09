"""The QPDF backend: the appearance PDFium would draw for each annotation, and pattern colours.

Each file is built here with the byte-level builder. Which appearance is
selected follows the rule PDFium was measured to use (issue G2, "Facts"); the
states are told apart by their data.
"""

from __future__ import annotations

import dataclasses
import typing
import zlib

import pytest

from pdfacts.backends.protocols import (
    AnnotationAppearance,
    AnnotationEntry,
    AppearanceReader,
    EngineFailure,
    FailureKind,
    SelectedAppearance,
    Written,
)
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, stream

ON, OFF, YES = b"0 1 m 10 1 l S", b"0 2 m 10 2 l S", b"0 3 m 10 3 l S"
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100]"
STATES = {11: stream(ON, FORM), 12: stream(OFF, FORM), 13: stream(YES, FORM)}
N = b"/AP << /N << /On 11 0 R /Off 12 0 R /Yes 13 0 R >> >>"


def page_file(
    content: bytes = b"",
    *,
    annots: bytes | None = None,
    resources: bytes = b"",
    objects: dict[int, bytes] | None = None,
) -> bytes:
    """A one-page file with the content, /Annots and resources given, `objects` beside."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
    if resources:
        page += b" /Resources " + resources
    if annots is not None:
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


def appearances_of(data: bytes) -> tuple[AnnotationAppearance, ...]:
    document = QpdfDocument.open(data, None)
    try:
        return document.appearances(0)
    finally:
        document.close()


def pattern_of(data: bytes) -> bool | None:
    document = QpdfDocument.open(data, None)
    try:
        return document.page_uses_pattern(0)
    finally:
        document.close()


def one(entries: bytes, objects: dict[int, bytes] | None = None) -> AnnotationAppearance:
    """The appearance of a widget with `entries`, the three states beside it."""
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 100 100] %s >>" % entries,
            14: stream(b"", b"/N << /On 11 0 R /Off 12 0 R /Yes 13 0 R >>"),
            **STATES,
            **(objects or {}),
        },
    )
    (found,) = appearances_of(data)
    return found


def data_of(found: AnnotationAppearance) -> bytes | None:
    return None if found.appearance is None else found.appearance.data


def plain(value: object) -> bool:
    """Built from int, float, str, bytes, bool, None, tuple and the dataclasses of the protocol."""
    if value is None or type(value) in (int, float, str, bytes, bool):
        return True
    if type(value) is tuple:
        return all(plain(item) for item in value)
    if type(value) in (AnnotationAppearance, AnnotationEntry, SelectedAppearance, Written):
        return all(plain(getattr(value, f.name)) for f in dataclasses.fields(value))
    return False


# --- which appearance ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (b"/AS /Yes " + N, YES),
        (b"/AS (Yes) " + N, YES),
        (b"/AS /Nope /V /On " + N, None),
        (b"/AS / /V /On " + N, ON),
        (b"/AS [/Yes] " + N, OFF),
        (b"/V /On " + N, ON),
        (b"/V (On) " + N, ON),
        (b"/V /Nope " + N, OFF),
        (N, OFF),
        (b"/AP << /N << /On 11 0 R /Yes 13 0 R >> >>", None),
        (b"/AS /Off /AP << /N 13 0 R >>", YES),
        (b"/AS /Yes /AP << /N << /Yes << /A 1 >> /Off 12 0 R >> >>", None),
        (b"/AS /#C3#28 /AP << /N << /#C3#28 13 0 R /Off 12 0 R >> >>", YES),
        (b"", None),
        (b"/AP << /D << /On 11 0 R /Off 12 0 R >> >>", None),
        (b"/AP [11 0 R]", None),
        (b"/AS /On /AP 14 0 R", ON),
        (b"/V /On /AP 14 0 R", ON),
    ],
    ids=[
        "/AS a name",
        "/AS a string",
        "/AS names no state",
        "/AS empty",
        "/AS an array",
        "/V a name",
        "/V a string",
        "/V names no state",
        "neither: /Off",
        "neither, and no /Off",
        "/N one stream",
        "the state is no stream",
        "a state whose name is no UTF-8",
        "no /AP",
        "no /N",
        "/AP no dictionary",
        "/AP a stream",
        "/V through /AP a stream",
    ],
)
def test_the_appearance_pdfium_draws(entries, expected):
    found = one(entries)
    assert data_of(found) == expected
    assert found.undecided is None


@pytest.mark.parametrize(
    ("own", "parent", "expected"),
    [
        (b"", b"/V /Yes", YES),
        (b"/V ()", b"/V /Yes", YES),
        (b"/V /Off", b"/V /Yes", OFF),
    ],
    ids=["from /Parent", "an empty /V of its own", "its own /V first"],
)
def test_v_is_the_annotation_s_else_its_parent_s(own, parent, expected):
    found = one(
        b"/Parent 7 0 R %s %s" % (own, N),
        {7: b"<< /FT /Btn /T (p) %s /Kids [6 0 R] >>" % parent},
    )
    assert data_of(found) == expected


def test_a_parent_written_as_a_stream_is_read_by_its_dictionary():
    # Measured with PDFium (the review of the G2 draft, 2026-09-30): it takes
    # the stream's dictionary at /Parent and reads the /V there.
    found = one(
        b"/Parent 7 0 R " + N,
        {7: stream(b"", b"/FT /Btn /T (p) /V /On /Kids [6 0 R]")},
    )
    assert data_of(found) == ON


def test_v_two_parents_up_is_not_looked_at():
    found = one(
        b"/Parent 7 0 R " + N,
        {
            7: b"<< /T (n) /Parent 8 0 R /Kids [6 0 R] >>",
            8: b"<< /FT /Btn /T (p) /V /Yes /Kids [7 0 R] >>",
        },
    )
    assert data_of(found) == OFF


@pytest.mark.parametrize(
    ("entries", "state"),
    [
        (b"/AS 5", b"/5"),
        (b"/AS 007", b"/7"),
        (b"/AS -0", b"/0"),
        (b"/AS -2147483648", b"/-2147483648"),
        (b"/AS 2147483648", b"/-2147483648"),
        (b"/AS 4294967296", b"/0"),
        (b"/AS true", b"/true"),
        (b"/V 5", b"/5"),
    ],
)
def test_a_number_or_a_boolean_names_a_state_as_its_text(entries, state):
    found = one(b"%s /AP << /N << %s 13 0 R /Off 12 0 R >> >>" % (entries, state))
    assert data_of(found) == YES


@pytest.mark.parametrize(
    ("entries", "where"),
    [
        (b"/AS 5.5", "/AS"),
        (b"/V 5.0", "/V"),
        (b"/Parent 7 0 R", "/Parent"),
    ],
)
def test_a_real_number_leaves_it_undecided(entries, where):
    found = one(entries + b" " + N, {7: b"<< /FT /Btn /T (p) /V 2.5 /Kids [6 0 R] >>"})
    assert found.appearance is None
    assert found.undecided is not None and where in found.undecided


def test_a_square_selects_as_a_widget_does():
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Square /Rect [0 0 9 9] /V /Yes %s >>" % N,
            **STATES,
        },
    )
    (found,) = appearances_of(data)
    assert data_of(found) == YES


# --- what the file writes -------------------------------------------------------------------


def test_the_entry_its_rect_flags_box_and_matrix_as_written():
    appearance = stream(YES, FORM + b" /Matrix [2 0 0 2 0 0]")
    found = one(b"/F 4 /AS /Yes /AP << /N << /Yes 14 0 R >> >>", {14: appearance})
    assert found.annotation == AnnotationEntry(0, 6, 0, True, b"Widget")
    assert found.rect == Written("array", tuple(Written("integer", v) for v in (0, 0, 100, 100)))
    assert found.flags == Written("integer", 4)
    assert found.appearance == SelectedAppearance(
        object_number=14,
        generation=0,
        data=YES,
        bbox=Written("array", tuple(Written("integer", v) for v in (0, 0, 100, 100))),
        matrix=Written("array", tuple(Written("integer", v) for v in (2, 0, 0, 2, 0, 0))),
        uses_pattern=False,
    )


def test_what_is_absent_is_none():
    data = page_file(
        annots=b"[6 0 R]",
        objects={6: b"<< /Type /Annot /Subtype /Square /AP << /N 7 0 R >> >>", 7: stream(YES)},
    )
    (found,) = appearances_of(data)
    assert (found.rect, found.flags) == (None, None)
    assert (found.appearance.bbox, found.appearance.matrix) == (None, None)


def test_the_data_decoded():
    packed = zlib.compress(YES)
    appearance = b"<< /Length %d /Filter /FlateDecode %s >>\nstream\n%s\nendstream" % (
        len(packed),
        FORM,
        packed,
    )
    found = one(b"/AS /Yes /AP << /N << /Yes 14 0 R >> >>", {14: appearance})
    assert data_of(found) == YES


def test_data_qpdf_cannot_decode_is_none():
    appearance = stream(YES, FORM + b" /Filter /NoSuchDecode")
    found = one(b"/AS /Yes /AP << /N << /Yes 14 0 R >> >>", {14: appearance})
    assert found.appearance is not None and found.appearance.data is None


def test_every_entry_in_order_the_ones_that_are_no_dictionary_too():
    data = page_file(
        annots=b"[6 0 R null 42 << /Type /Annot /Subtype /Square >>]",
        objects={6: b"<< /Type /Annot /Subtype /Widget %s >>" % N, **STATES},
    )
    first, second, third, fourth = appearances_of(data)
    assert data_of(first) == OFF
    assert second == AnnotationAppearance(
        AnnotationEntry(1, None, None, False, None), None, None, None, None
    )
    assert third.annotation == AnnotationEntry(2, None, None, False, None)
    assert fourth.annotation == AnnotationEntry(3, None, None, True, b"Square")
    assert fourth.appearance is None


@pytest.mark.parametrize("annots", [None, b"42"], ids=["absent", "no array"])
def test_annots_absent_or_no_array_is_nothing(annots):
    assert appearances_of(page_file(annots=annots)) == ()


# --- pattern colours ------------------------------------------------------------------------


def appearance_setting(content: bytes, resources: bytes = b"", objects=None, page_resources=b""):
    """uses_pattern of a Square whose appearance draws `content`."""
    entries = FORM + (b" /Resources " + resources if resources else b"")
    data = page_file(
        annots=b"[6 0 R]",
        resources=page_resources,
        objects={
            6: b"<< /Type /Annot /Subtype /Square /Rect [0 0 9 9] /AP << /N 7 0 R >> >>",
            7: stream(content, entries),
            **(objects or {}),
        },
    )
    (found,) = appearances_of(data)
    return found.appearance.uses_pattern


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"/Pattern cs /P0 scn 0 0 9 9 re f", True),
        (b"/Pattern CS /P0 SCN 0 0 9 9 re S", True),
        (b"/CS0 cs 0.5 scn 0 0 9 9 re f", False),
        (b"0 0 9 9 re f", False),
        (b"/Pattern cs 0.5 0.5 0.5 /P0 scn 0 0 9 9 re f", True),
        (b"/CS1 cs 0.5 /P1 scn 0 0 9 9 re f", True),
        (b"(/P0 scn) Tj", False),
        (b"[/P0 scn] pop", False),
    ],
    ids=[
        "scn",
        "SCN",
        "scn with numbers",
        "an uncoloured pattern through a name space",
        "no colour",
        "an uncoloured pattern, its components first",
        "inside a string",
        "inside an array",
    ],
)
def test_a_pattern_colour_is_scn_or_scn_with_a_name(content, expected):
    assert appearance_setting(content) is expected


def test_a_form_the_appearance_draws_counts():
    setting = stream(b"/P0 scn", FORM)
    resources = b"<< /XObject << /X 8 0 R >> >>"
    assert appearance_setting(b"/X Do", resources, {8: setting}) is True


def test_a_form_whose_subtype_is_a_string_counts():
    # PDFium reads a string /Subtype as the name (measured, issue G1b).
    setting = stream(b"/P0 scn", b"/Type /XObject /Subtype (Form) /BBox [0 0 100 100]")
    resources = b"<< /XObject << /X 8 0 R >> >>"
    assert appearance_setting(b"/X Do", resources, {8: setting}) is True


def test_a_form_found_in_the_page_s_resources_counts():
    # An appearance without /Resources looks its names up in the page's (issue G1).
    setting = stream(b"/P0 scn", FORM)
    page_resources = b"<< /XObject << /X 8 0 R >> >>"
    assert appearance_setting(b"/X Do", objects={8: setting}, page_resources=page_resources)


def test_a_form_without_resources_uses_those_of_what_draws_it():
    resources = b"<< /XObject << /X 8 0 R /Y 9 0 R >> >>"
    objects = {8: stream(b"/Y Do", FORM), 9: stream(b"/P0 scn", FORM)}
    assert appearance_setting(b"/X Do", resources, objects) is True


def test_a_form_drawn_from_two_places_is_read_in_each():
    """/C has no /Resources: drawn by /A its /Y is a line, drawn by /B a pattern colour."""
    resources = b"<< /XObject << /A 8 0 R /B 9 0 R >> >>"
    objects = {
        8: stream(b"/C Do", FORM + b" /Resources << /XObject << /C 10 0 R /Y 11 0 R >> >>"),
        9: stream(b"/C Do", FORM + b" /Resources << /XObject << /C 10 0 R /Y 12 0 R >> >>"),
        10: stream(b"/Y Do", FORM),
        11: stream(b"0 0 m 1 1 l S", FORM),
        12: stream(b"/P0 scn", FORM),
    }
    assert appearance_setting(b"/A Do /B Do", resources, objects) is True


def test_a_form_that_draws_itself_is_read_to_the_end():
    resources = b"<< /XObject << /X 8 0 R >> >>"
    objects = {8: stream(b"/X Do /X Do", FORM + b" /Resources " + resources)}
    assert appearance_setting(b"/X Do", resources, objects) is False


@pytest.mark.parametrize(("depth", "expected"), [(40, True), (41, False)])
def test_forms_are_read_40_levels_deep(depth, expected):
    """A chain of forms, the last setting a pattern colour at `depth`; PDFium parses 40 (G1)."""
    objects = {}
    for level in range(1, depth + 1):
        content = b"/P0 scn" if level == depth else b"/X Do"
        nested = b" /Resources << /XObject << /X %d 0 R >> >>" % (100 + level + 1)
        objects[100 + level] = stream(content, FORM + (b"" if level == depth else nested))
    assert appearance_setting(b"/X Do", b"<< /XObject << /X 101 0 R >> >>", objects) is expected


def test_an_image_is_not_read_as_a_form():
    image = stream(b"/P0 scn", b"/Type /XObject /Subtype /Image /Width 1 /Height 1")
    assert appearance_setting(b"/X Do", b"<< /XObject << /X 8 0 R >> >>", {8: image}) is False


@pytest.mark.parametrize(
    "content",
    [b"[/P0 scn] /P0 scn", b"/P0 scn"],
    ids=["an operator in an array", "no filter known"],
)
def test_content_qpdf_cannot_read_is_none(content):
    entries = b" /Filter /NoSuchDecode" if content == b"/P0 scn" else b""
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Square /AP << /N 7 0 R >> >>",
            7: stream(content, FORM + entries),
        },
    )
    (found,) = appearances_of(data)
    assert found.appearance.uses_pattern is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"/Pattern cs /P0 scn 0 0 9 9 re f", True), (b"0 0 9 9 re f", False)],
    ids=["scn", "none"],
)
def test_the_page_s_content(content, expected):
    assert pattern_of(page_file(content)) is expected


def test_the_parts_of_the_page_s_contents_are_read_as_one():
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents [5 0 R 6 0 R] >>",
            5: stream(b"/P0"),
            6: stream(b"scn"),
        },
        1,
    )
    assert pattern_of(data) is True


def test_a_form_the_page_draws_counts():
    data = page_file(
        b"/X Do",
        resources=b"<< /XObject << /X 8 0 R >> >>",
        objects={8: stream(b"/P0 SCN", FORM)},
    )
    assert pattern_of(data) is True


def test_page_content_qpdf_cannot_read_is_none():
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(b"/P0 scn", b"/Filter /NoSuchDecode"),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R >>",
        },
        1,
    )
    assert pattern_of(data) is None


# --- the review of the first draft (2026-10-01): pattern spaces, misreads ----


@pytest.mark.parametrize(
    "content",
    [
        b"/Pattern cs 0 0 100 100 re f",
        b"/Pattern cs 0.5 scn 0 0 100 100 re f",
        b"/Pattern cs /P0 sc 0 0 100 100 re f",
        b"/CSX cs 0 0 100 100 re f",
        b"/CSA cs 0 0 100 100 re f",
        b"/Pattern CS /P0 SCN 0 0 m 10 0 l S",
    ],
    ids=[
        "a fill in /Pattern",
        "a number in scn",
        "a name in sc",
        "a name that is /Pattern in the resources",
        "an array that starts with /Pattern",
        "the stroking space",
    ],
)
def test_a_pattern_colour_space_without_a_pattern_set_counts(content):
    # Measured (the review of the first draft): PDFium makes up a colour for a
    # fill in a Pattern colour space even where no pattern is ever set.
    resources = b"<< /ColorSpace << /CSX /Pattern /CSA [/Pattern /DeviceRGB] >> >>"
    assert appearance_setting(content, resources) is True


def test_a_name_that_names_no_pattern_in_a_plain_space_is_the_known_gap():
    """Measured with the review of this implementation (2026-10-01): PDFium
    ignores a scn whose name names no pattern while the space is no pattern
    space, and paints the colour set before. The count says True — the answer
    stays unknown, never a wrong value. A known gap, stated in the issue."""
    content = b"1 0 0 rg /DeviceRGB cs /Nope scn 0 0 9 9 re f"
    resources = b"<< /Pattern << /P0 30 0 R >> >>"
    assert appearance_setting(content, resources) is True


def test_a_name_that_names_a_pattern_in_a_plain_space_counts():
    """PDFium sets the pattern colour even there (measured, 2026-10-01)."""
    content = b"1 0 0 rg /DeviceRGB cs /P0 scn 0 0 9 9 re f"
    resources = b"<< /Pattern << /P0 30 0 R >> >>"
    assert appearance_setting(content, resources) is True


@pytest.mark.parametrize(
    "content",
    [b"/DeviceGray cs 0 0 100 100 re f", b"/CSG cs 0 0 100 100 re f"],
    ids=["a plain space", "a name for a plain space in the resources"],
)
def test_a_plain_colour_space_does_not_count(content):
    resources = b"<< /ColorSpace << /CSG /DeviceGray >> >>"
    assert appearance_setting(content, resources) is False


def test_an_integer_beyond_64_bits_in_the_annotation_leaves_it_undecided():
    # QPDF gives up on the whole annotation; PDFium draws it (measured,
    # 2026-10-01), so the entry says why rather than looking like a page
    # without an annotation.
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 9 9] /V /On"
            b" /AS 99999999999999999999999999999999 %s >>" % N,
            **STATES,
        },
    )
    (found,) = appearances_of(data)
    assert found.annotation.dictionary is False
    assert found.appearance is None
    assert found.undecided is not None


def test_a_state_name_with_a_null_byte_leaves_it_undecided():
    # QPDF repairs the dictionary (a fake key); PDFium draws that state.
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 9 9] /V /On /AP 15 0 R >>",
            15: b"<< /N << /On#00 11 0 R /Off 12 0 R >> >>",
            **STATES,
        },
    )
    (found,) = appearances_of(data)
    assert found.undecided is not None


def test_a_name_qpdf_cannot_read_leaves_the_page_s_pattern_unknown():
    # The page's own resources hold a key with a null byte (issue G1's
    # measurement): QPDF repairs it, so what the content sets is unreadable.
    data = page_file(
        content=b"/Fx#00 Do",
        resources=b"<< /XObject << /Fx#00 8 0 R >> >>",
        objects={8: stream(b"/P0 scn", FORM)},
    )
    document = QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()


def test_an_empty_state_name_is_not_selected():
    # Measured: PDFium falls back past an empty /V; a state named / is not it.
    found = one(
        b"/V () " + b"/AP << /N << / 11 0 R /Off 12 0 R >> >>",
    )
    assert data_of(found) == OFF


def test_a_name_qpdf_cannot_read_leaves_it_undecided():
    # A stray # in a name: QPDF mis-reads the whole object; the selection says
    # so rather than choosing (the review's S3).
    data = page_file(
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 9 9] /AS /#ZZ %s >>" % N,
            **STATES,
        },
    )
    (found,) = appearances_of(data)
    assert found.undecided is not None


def test_a_hex_string_names_a_state():
    # <C328> decodes to the bytes C3 28; the state is named with #xx escapes.
    found = one(b"/AS <C328> /AP << /N << /#C3#28 13 0 R /Off 12 0 R >> >>")
    assert data_of(found) == YES


def test_a_form_first_met_deep_then_shallow_is_read():
    """The memo key holds the depth: a form reached at the last level is not
    walked, and one drawn at depth 1 still is."""
    setting = stream(b"/P0 scn", FORM)
    deep = {}
    for i in range(39):
        deep[30 + i] = stream(
            b"/X Do", FORM + b" /Resources << /XObject << /X %d 0 R >> >>" % (31 + i)
        )
    deep[69] = stream(b"0 0 m 1 1 l S", FORM)
    deep[30] = stream(b"/Y Do /Y Do", FORM + b" /Resources << /XObject << /Y 70 0 R >> >>")
    deep[70] = setting
    resources = b"<< /XObject << /X 30 0 R >> >>"
    assert appearance_setting(b"/X Do", resources, deep) is True


def test_warnings_as_errors_leave_the_pattern_unknown():
    import warnings as warnings_module

    data = page_file(content=b"/P0 scn (abc")
    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error")
        document = QpdfDocument.open(data, None)
        try:
            assert document.page_uses_pattern(0) is None
        finally:
            document.close()


def test_an_appearance_s_resources_without_xobject_use_the_page_s():
    setting = stream(b"/P0 scn", FORM + b" /Resources << >>")
    page_resources = b"<< /XObject << /X 8 0 R >> >>"
    assert appearance_setting(b"/X Do", objects={8: setting}, page_resources=page_resources)


def test_the_page_s_pattern_space_on_pages_only_is_inherited():
    # The page content names CSX through the resources of /Pages.
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1"
            b" /Resources << /ColorSpace << /CSX /Pattern >> >> >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 5 0 R >>",
            5: stream(b"/CSX cs 0 0 100 100 re f"),
        },
        1,
    )
    document = QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is True
    finally:
        document.close()


# --- the reader ----------------------------------------------------------------------------


def test_after_close_and_outside_the_pages():
    document = QpdfDocument.open(page_file(annots=b"[]"), None)
    try:
        with pytest.raises(IndexError):
            document.appearances(1)
        with pytest.raises(IndexError):
            document.page_uses_pattern(1)
    finally:
        document.close()
    for read in (document.appearances, document.page_uses_pattern):
        with pytest.raises(EngineFailure) as raised:
            read(0)
        assert raised.value.kind is FailureKind.OTHER


def test_no_engine_type_leaves_the_backend():
    data = page_file(
        b"/X Do",
        resources=b"<< /XObject << /X 8 0 R >> >>",
        annots=b"[6 0 R null]",
        objects={
            6: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 9 9] /F 4 %s >>" % N,
            8: stream(b"", FORM),
            **STATES,
        },
    )
    assert plain(appearances_of(data))
    assert plain(pattern_of(data))


def test_the_types_have_their_fields_and_are_frozen():
    assert [f.name for f in dataclasses.fields(SelectedAppearance)] == [
        "object_number",
        "generation",
        "data",
        "bbox",
        "matrix",
        "uses_pattern",
    ]
    assert [f.name for f in dataclasses.fields(AnnotationAppearance)] == [
        "annotation",
        "rect",
        "flags",
        "appearance",
        "undecided",
    ]
    selected = SelectedAppearance(
        object_number=4, generation=0, data=b"", bbox=None, matrix=None, uses_pattern=False
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        selected.data = None  # type: ignore[misc]


def test_it_implements_the_appearance_reader():
    members = typing.get_protocol_members(AppearanceReader)
    assert members == {"appearances", "page_uses_pattern", "page_painted", "appearance_painted"}
    assert all(callable(getattr(QpdfDocument, name)) for name in members)
