"""The colours of a path where a pattern colour is set: known for a side only
where QPDF reads the colour PDFium reports for it, unknown otherwise.

The rules are those of docs/conventions.md, "Paths and images": the colour
that g and rg set, or black, as QPDF reads the content, each path QPDF reads
taken with PDFium's in drawing order (measured, issue C1). Each file is
built here with the byte-level builder; the colours expected follow from the
construction (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import DisplayColour, Limits, LimitUncheckedError, Unknown, UnknownReason, paths
from pdfacts.backends import protocols
from pdfacts.backends import qpdf as qpdf_backend
from synthetic.builder import build, build_with_object_stream, stream

BLACK = DisplayColour(0, 0, 0, 255)
RED = DisplayColour(255, 0, 0, 255)
BLUE = DisplayColour(0, 0, 255, 255)
GREY = DisplayColour(128, 128, 128, 255)
CELL = stream(
    b"1 0 0 rg 0 0 5 5 re f",
    b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
    b" /BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
)
UNCOLOURED = stream(
    b"0 0 5 5 re f",
    b"/Type /Pattern /PatternType 1 /PaintType 2 /TilingType 1"
    b" /BBox [0 0 10 10] /XStep 10 /YStep 10 /Resources << >>",
)
SHADING = (
    b"<< /PatternType 2 /Shading << /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0]"
    b" /Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >> >> >>"
)
RESOURCES = (
    b"<< /Pattern << /P0 5 0 R /U 6 0 R /S 11 0 R >> /ColorSpace << /PU [/Pattern /DeviceRGB] >>"
    b" /XObject << /X 7 0 R /Im 10 0 R >> >>"
)
IMAGE_DICTIONARY = (
    b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8"
)
IMAGE = stream(b"\x00\xff\x80\x40", IMAGE_DICTIONARY)
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
RECT = b" 10 10 50 50 re "
UNPAIRED = (
    "a pattern colour or colour space is set here, PDFium reports a colour of its own,"
    " and QPDF cannot read the content's colours path by path"
)
# A pattern colour set and restored: the content sets one, its paths are
# drawn from the default state.
SET = b"q /Pattern cs /P0 scn Q "
# Which colours the fill and the stroke of a path have: a DisplayColour, or
# FILL or STROKE where QPDF does not confirm the colour PDFium reports, or WHOLE
# where QPDF cannot read the content's colours path by path.
FILL = "fill"
STROKE = "stroke"
WHOLE = "whole"


def page_file(
    content: bytes | list[bytes],
    form: bytes = b"0 0 10 10 re f",
    annots=None,
    extra=None,
    resources: bytes = RESOURCES,
) -> bytes:
    """A page of 400 x 300 that draws `content`, a list for a /Contents
    array; its form /X draws `form`."""
    parts = content if isinstance(content, list) else [content]
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        5: CELL,
        6: UNCOLOURED,
        7: stream(form, FORM + b" /Resources " + RESOURCES),
        10: IMAGE,
        11: SHADING,
        **(extra or {}),
    }
    numbers = []
    for i, part in enumerate(parts):
        objects[20 + i] = stream(part)
        numbers.append(b"%d 0 R" % (20 + i))
    contents = numbers[0] if len(numbers) == 1 else b"[" + b" ".join(numbers) + b"]"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents " + contents
    page += b" /Resources " + resources
    if annots:
        page += b" /Annots " + annots
    objects[4] = page + b" >>"
    return build(objects, 1)


def colours(data: bytes) -> list[tuple[object, object]]:
    with pdfacts.open(data) as document:
        found = paths(document, 0)
    assert not isinstance(found, Unknown), found
    return [(_side(path.fill_colour, FILL), _side(path.stroke_colour, STROKE)) for path in found]


def _side(value, side: str):
    if value == Unknown(UnknownReason.UNREADABLE, UNPAIRED):
        return WHOLE
    if isinstance(value, Unknown):
        assert value.reason is UnknownReason.UNREADABLE
        assert value.detail == (
            "a pattern colour is set here, and QPDF does not confirm the colour PDFium"
            f" reports for the {side}"
        )
        return side
    return value


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"1 0 0 rg" + RECT + b"f", [(RED, None)]),
        (b"1 0 0 rg" + RECT + b"f /Pattern cs /P0 scn" + RECT + b"f", [(RED, None), (FILL, None)]),
        (b"/Pattern cs /P0 scn" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn 0 0 1 rg" + RECT + b"f", [(BLUE, None)]),
        (b"/Pattern cs /P0 scn 0.5 g" + RECT + b"f", [(GREY, None)]),
        (b"/Pattern cs /P0 scn 2 -1 0 rg" + RECT + b"f", [(RED, None)]),
        (RECT + b"f /Pattern cs /P0 scn" + RECT + b"f", [(BLACK, None), (FILL, None)]),
        (b"/Pattern cs /P0 scn /DeviceRGB cs" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn /DeviceRGB cs 0 0 1 sc" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn /DeviceRGB cs 0 0 sc" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn /Missing cs 1 0 0 sc" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn 0 0 1 sc" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn 0 0 0 1 k" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn 0 0 1 k" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn 1 /x 0 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn 0 0 1 0 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"q /Pattern cs /P0 scn" + RECT + b"f Q" + RECT + b"f", [(FILL, None), (BLACK, None)]),
        (b"/Pattern cs /P0 scn Q" + RECT + b"f", [(FILL, None)]),
        (b"0 1 0 rg /Pattern cs" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /Nothing scn" + RECT + b"f", [(WHOLE, None)]),
        (b"/DeviceRGB cs /P0 scn" + RECT + b"f", [(FILL, None)]),
        (b"/PU cs 0.2 0.4 0.6 /U scn" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern CS /P0 SCN" + RECT + b"B", [(BLACK, STROKE)]),
        (b"/Pattern CS /P0 SCN" + RECT + b"f", [(BLACK, None)]),
        (b"/Pattern cs /P0 scn" + RECT + b"B 0 0 m 10 10 l S", [(FILL, BLACK), (None, BLACK)]),
        (b"/Pattern cs /P0 scn 1 0 0 RG" + RECT + b"B", [(FILL, RED)]),
        (b"/Pattern CS /P0 SCN 0 0 1 rg" + RECT + b"B", [(BLUE, STROKE)]),
        (b"/Pattern CS /P0 SCN 0 0 1 rg 1 0 0 RG" + RECT + b"B", [(BLUE, RED)]),
        (b"/Pattern cs /P0 scn 0 0 g" + RECT + b"f", [(WHOLE, None)]),
        (SET + b"0 0 1 rg Q" + RECT + b"f", [(BLUE, None)]),
        (SET + b"0.1373 0.1216 0.1255 rg 0 0 0 1 k" + RECT + b"f", [(FILL, None)]),
        (SET + b"1 0 0 rg 1 0 0 sc" + RECT + b"f", [(FILL, None)]),
        (SET + b"1 0 0 rg 1 0 0 scn" + RECT + b"f", [(FILL, None)]),
        (SET + b"1 0 0 RG /DeviceRGB CS" + RECT + b"S", [(None, STROKE)]),
        (SET + b"1 0 0 RG 1 0 0 SCN" + RECT + b"S", [(None, STROKE)]),
        (SET + b"0 0 1 rg 1 0 0 0 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn 0.749 g" + RECT + b"f", [(FILL, None)]),
        (b"/Pattern cs /P0 scn 1 g" + RECT + b"f", [(FILL, None)]),
        (b"/PU cs 0.2 0.4 0.6 /U scn 0.2 0.4 0.6 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/PU cs 0 0 0 1 /U scn 0 0 1 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn 1..5 0 0 1 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn /a 0 m 10 10 l f", [(WHOLE, None)]),
        (SET + b"1 0 0 rg 0 0 m /a 5 l 10 10 l f", [(WHOLE, None)]),
        (SET + b"1 0 0 RG 0 0 m 10 10 l 20 20 m 30 30 l S", [(None, RED)]),
        (b"/Pattern cs /P0 scn 0 0 1 rg n 1 0 0 rg" + RECT + b"f", [(RED, None)]),
        (b"/Pattern cs /P0 scn 0 0 1 rg 5 n" + RECT + b"f", [(WHOLE, None)]),
        (b"/DeviceRGB cs /P0 scn 1 g" + RECT + b"f", [(FILL, None)]),
        (b"/DeviceRGB cs /S scn 1 g" + RECT + b"f", [(FILL, None)]),
        (b"/DeviceRGB cs /P0 scn 0.749 g" + RECT + b"f", [(FILL, None)]),
        (b"/PU cs /P0 scn 0 0 1 rg" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /P0 scn <4 x h 0 0 1 rg> 1 w" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs /U scn 0.749 g" + RECT + b"f", [(WHOLE, None)]),
        (b"/Pattern cs 0.5 /P0 scn 0.749 g" + RECT + b"f", [(WHOLE, None)]),
    ],
    ids=[
        "plain, no pattern",
        "a pattern after a plain colour",
        "a pattern colour",
        "rg",
        "g",
        "rg with numbers beyond 0 and 1",
        "the default black",
        "cs to a plain space",
        "sc with numbers in a plain space",
        "sc with too few numbers",
        "a colour space PDFium cannot resolve",
        "sc with numbers in a Pattern space",
        "k",
        "k with too few numbers",
        "rg with a name among its operands",
        "rg with four numbers",
        "Q restores what q saved",
        "a Q without q",
        "a Pattern colour space alone",
        "a name that names no pattern",
        "a pattern's name in a plain space",
        "an uncoloured pattern with components",
        "a stroke pattern on a filled and stroked path",
        "a stroke pattern on a filled path",
        "the fill side alone",
        "RG for the stroke, the fill's pattern kept",
        "rg for the fill, the stroke's pattern kept",
        "both sides set anew",
        "g with two numbers",
        "a Q without q keeps the colour read",
        "k, where PDFium reports the colour read before",
        "sc, where PDFium reports the colour read before",
        "scn, where PDFium reports the colour read before",
        "CS, where PDFium reports the colour read before",
        "SCN, where PDFium reports the colour read before",
        "rg with four numbers after a colour read",
        "the grey PDFium reports for a coloured tiling pattern",
        "the white PDFium reports for a shading pattern",
        "the components of an uncoloured pattern",
        "an uncoloured pattern's components QPDF does not convert",
        "a token QPDF reads as an operator",
        "a move with a name",
        "a line with a name",
        "two subpaths",
        "n without its path",
        "n with an operand",
        "white after a tiling pattern alone",
        "white after a shading pattern",
        "the grey of a tiling pattern",
        "a coloured pattern in a Pattern colour space with a base",
        "a hex string QPDF warns of",
        "an uncoloured pattern without components",
        "a coloured pattern with components",
    ],
)
def test_the_sides_qpdf_reads(content, expected):
    assert colours(page_file(content)) == expected


@pytest.mark.parametrize(
    "content",
    [
        b"/Pattern cs /P0 scn 0.749\x0bg" + RECT + b"f",
        b"/Pattern cs /P0 scn <4 g n 0.749 g>" + RECT + b"f",
        b"/Pattern cs /P0 scn /a 0 m 300 0 l 300 200 l f 0.749 g 1..5 0 0 m 10 10 l f",
        b"1 J 20 w /Pattern CS /P0 SCN 100 100 m h S 0 0 1 RG 0 0 0 m 300 200 l S",
    ],
    ids=[
        "a byte QPDF takes for white space and PDFium does not",
        "a hex string QPDF reads otherwise",
        "two paths the engines make otherwise, one each",
        "a path PDFium makes and QPDF does not",
    ],
)
def test_where_the_engines_may_read_otherwise_every_colour_is_unknown(content):
    """QPDF and PDFium read these contents otherwise (measured in review,
    C1): QPDF does not read their colours path by path."""
    with pdfacts.open(page_file(content)) as document:
        found = paths(document, 0)
    assert found
    for path in found:
        for value in (path.fill_colour, path.stroke_colour):
            assert value is None or value == Unknown(UnknownReason.UNREADABLE, UNPAIRED)


@pytest.mark.parametrize(
    ("content", "form", "expected"),
    [
        (b"/Pattern cs /P0 scn /X Do", b"0 0 10 10 re f", [(FILL, None)]),
        (b"/X Do" + RECT + b"f", b"/Pattern cs /P0 scn", [(BLACK, None)]),
        (
            b"/X Do" + RECT + b"f",
            b"/Pattern cs /P0 scn 0 0 10 10 re f",
            [(FILL, None), (BLACK, None)],
        ),
        (
            b"/Pattern cs /P0 scn /X Do 0 0 1 rg /X Do",
            b"0 0 10 10 re f",
            [(FILL, None), (BLUE, None)],
        ),
        (b"1 0 0 rg /Pattern CS /P0 SCN /X Do", b"0 0 10 10 re B", [(RED, STROKE)]),
        (b"/Pattern cs /P0 scn 0 0 1 RG /X Do", b"0 0 10 10 re B", [(FILL, BLUE)]),
        (b"/Pattern cs /P0 scn 1 0 0 rg /X Do", b"-1 g 0 0 10 10 re f", [(BLACK, None)]),
        (b"/Pattern CS /P0 SCN 1 0 0 RG /X Do", b"-2 G 0 0 10 10 re S", [(None, BLACK)]),
        (b"/X Do 0.749 g" + RECT + b"f", b"/Pattern cs /P0 scn", [(FILL, None)]),
    ],
    ids=[
        "a form starts from where it is drawn",
        "what a form sets ends with it",
        "a form's own path",
        "one form drawn from two states",
        "a form takes both sides",
        "a form keeps the stroke where it is drawn",
        "a grey below 0 that a form sets for the fill",
        "a grey below 0 that a form sets for the stroke",
        "the colour a pattern set in a form makes up",
    ],
)
def test_a_form(content, form, expected):
    assert colours(page_file(content, form)) == expected


def test_a_form_in_a_form_keeps_the_colours_where_each_is_drawn():
    resources = RESOURCES.replace(b"/X 7 0 R", b"/X 7 0 R /X2 29 0 R")
    extra = {
        29: stream(b"/Y Do 1 0 0 rg /Y Do", FORM + b" /Resources << /XObject << /Y 30 0 R >> >>"),
        30: stream(b"0 0 10 10 re f", FORM),
    }
    content = b"/Pattern cs /P0 scn /X2 Do 0 0 1 rg /X2 Do"
    assert colours(page_file(content, resources=resources, extra=extra)) == [
        (FILL, None),
        (RED, None),
        (BLUE, None),
        (RED, None),
    ]


BOTH = b"/Pattern cs /P0 scn /Pattern CS /P0 SCN"


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (b"S", (None, STROKE)),
        (b"s", (None, STROKE)),
        (b"f", (FILL, None)),
        (b"F", (FILL, None)),
        (b"f*", (FILL, None)),
        (b"B", (FILL, STROKE)),
        (b"B*", (FILL, STROKE)),
        (b"b", (FILL, STROKE)),
        (b"b*", (FILL, STROKE)),
    ],
)
def test_each_painting_operator_paints_its_sides(operator, expected):
    """A pattern colour set for both sides; each operator makes one path, the
    sides it does not paint without a colour, then a path in plain colours."""
    content = BOTH + RECT + operator + b" 0 0 1 rg 1 0 0 RG" + RECT + b"B"
    assert colours(page_file(content)) == [expected, (BLUE, RED)]


@pytest.mark.parametrize(
    "segment", [b"10 10 l", b"5 5 8 8 10 0 c", b"5 5 10 0 v", b"5 5 10 0 y", b"0 0 10 10 re"]
)
def test_each_segment_makes_a_path(segment):
    content = b"/Pattern cs /P0 scn 0 0 m " + segment + b" f 0 0 1 rg" + RECT + b"f"
    assert colours(page_file(content)) == [(FILL, None), (BLUE, None)]


def test_an_image_between_paths_takes_no_path_s_sides():
    content = (
        b"/Pattern cs /P0 scn" + RECT + b"f q 20 0 0 20 0 0 cm /Im Do Q 0 0 1 rg" + RECT + b"f"
    )
    data = page_file(content)
    with pdfacts.open(data) as document:
        assert len(pdfacts.images(document, 0)) == 1
    assert colours(data) == [(FILL, None), (BLUE, None)]


def test_paths_pdfium_does_not_make_keep_the_order():
    """A painting operator after `n`, and a second painting operator, make
    no path in either engine; the next path keeps its colour."""
    content = b"/Pattern cs /P0 scn 0 0 m 5 5 l n f" + RECT + b"f f 1 0 0 rg" + RECT + b"f"
    assert colours(page_file(content)) == [(FILL, None), (RED, None)]


@pytest.mark.parametrize(
    "alone", [b"0 0 m f", b"1 J 20 w 100 100 m h S", b"100 100 m b*", b"0 0 m 5 5 m S"]
)
def test_a_move_alone_leaves_every_colour_unknown(alone):
    """PDFium makes a path of a move alone by the caps and the operator:
    QPDF does not tell which."""
    content = b"/Pattern cs /P0 scn" + RECT + b"f 1 0 0 rg " + alone + RECT + b"f"
    assert colours(page_file(content))[0] == (WHOLE, None)


def test_a_line_without_a_current_point_makes_no_path():
    content = b"/Pattern cs /P0 scn 10 10 l f 0 0 10 10 re 20 20 l f 1 0 0 rg" + RECT + b"f"
    assert colours(page_file(content)) == [(FILL, None), (RED, None)]


def test_a_comment_at_the_end_of_a_part_runs_on_into_the_next():
    """PDFium joins the parts of /Contents with a space: the comment takes
    `1 0 0 rg`, and the pattern colour paints the path."""
    content = [b"/Pattern cs /P0 scn %", b"1 0 0 rg\n" + RECT + b"f"]
    assert colours(page_file(content)) == [(FILL, None)]


def test_a_comment_hides_a_colour_from_both_engines():
    """The comment at the end of the first part takes `0 0 1 rg`: PDFium
    paints in red, and QPDF reads red too."""
    content = [SET + b"1 0 0 rg %", b"0 0 1 rg\n" + RECT + b"f"]
    assert colours(page_file(content)) == [(RED, None)]


def test_a_warning_about_the_parts_of_contents_leaves_every_colour_unknown():
    content = [b"/Pattern cs /P0 scn", b"<4 x h 0 0 1 rg> 1 w" + RECT + b"f"]
    assert colours(page_file(content)) == [(WHOLE, None)]


def test_the_state_carries_across_the_parts_of_contents():
    content = [b"/Pattern cs /P0", b" scn" + RECT + b"f", RECT + b"f 0 0 1 rg" + RECT + b"f"]
    assert colours(page_file(content)) == [(FILL, None), (FILL, None), (BLUE, None)]


def test_an_inline_image_leaves_every_colour_unknown():
    """QPDF ends an inline image at the first EI, PDFium after the data its
    dictionary declares: the paths are not taken one by one."""
    content = (
        b"/Pattern cs /P0 scn"
        + RECT
        + b"f BI /W 2 /H 1 /CS /G /BPC 8 ID ab EI 1 0 0 rg"
        + RECT
        + b"f"
    )
    with pdfacts.open(page_file(content)) as document:
        found = paths(document, 0)
    assert [path.fill_colour for path in found] == [Unknown(UnknownReason.UNREADABLE, UNPAIRED)] * 2


def test_an_inline_image_s_operators_alone_leave_every_colour_unknown():
    """QPDF takes what follows a bare ID for an image's data, up to EI, and
    does not read the pattern set there; PDFium does (measured in review)."""
    content = SET + b"0.749 g ID /Pattern cs /P0 scn EI" + RECT + b"f"
    assert colours(page_file(content)) == [(WHOLE, None)]


@pytest.mark.parametrize(
    ("size", "expected"), [(84, [(BLUE, None), (BLUE, None)]), (85, [(WHOLE, None)] * 2)]
)
def test_a_name_longer_than_pdfium_may_read_leaves_every_colour_unknown(size, expected):
    """A name of 85 bytes may be written with more than the 254 bytes PDFium
    reads after its slash."""
    name = b"/" + b"N" * size
    resources = RESOURCES.replace(b"/X 7 0 R", b"/X 7 0 R " + name + b" 7 0 R")
    content = b"/Pattern cs /P0 scn 0 0 1 rg" + RECT + b"f " + name + b" Do"
    assert colours(page_file(content, resources=resources)) == expected


def test_a_colour_space_with_a_long_name_leaves_every_colour_unknown():
    name = b"/" + b"N" * 85
    resources = RESOURCES.replace(b"/PU [", name + b" /DeviceRGB /PU [")
    content = b"/Pattern cs /P0 scn " + name + b" cs 0 0 1 sc 0 0 1 rg" + RECT + b"f"
    assert colours(page_file(content, resources=resources)) == [(WHOLE, None)]


def test_a_form_qpdf_draws_and_pdfium_does_not_leaves_every_colour_unknown():
    """The page's /Resources is null: QPDF takes its parent's, which draw
    the form; PDFium takes none."""
    extra = {2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 /Resources " + RESOURCES + b" >>"}
    content = b"/X Do /Pattern cs /P0 scn" + RECT + b"f"
    assert colours(page_file(content, resources=b"null", extra=extra)) == [(WHOLE, None)]


UNSCANNED = (
    "QPDF cannot read the content, or warns that it misread an object of the file,"
    " or reads a name without bytes in it, to see whether a pattern colour is set"
)


# The resources of a file with an object stream, which takes objects 7 and 8;
# /F names object 21.
HIDDEN_RESOURCES = RESOURCES.replace(b"/X 7 0 R", b"/F 21 0 R")


def hidden_file(page: bytes, contents: bytes, hidden: bytes, extra=None) -> bytes:
    """A page whose object 21, in an object stream, holds `hidden` as a
    stream object: QPDF reads its dictionary, PDFium the stream."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents " + contents + page,
        5: CELL,
        6: UNCOLOURED,
        10: IMAGE,
        11: SHADING,
        **(extra or {}),
    }
    return build_with_object_stream(objects, [(21, hidden)], {21: 0}, 1)


HIDDEN_PART = hidden_file(
    b" /Resources " + HIDDEN_RESOURCES + b" >>",
    b"[20 0 R 21 0 R 22 0 R]",
    stream(b"/Pattern cs /P0 scn"),
    {20: stream(SET + b"0.749 g"), 22: stream(RECT + b"f")},
)


HIDDEN_CONTENTS = hidden_file(
    b" /Resources " + HIDDEN_RESOURCES + b" >>",
    b"21 0 R",
    stream(b"/Pattern cs /P0 scn" + RECT + b"f"),
)


@pytest.mark.parametrize("data", [HIDDEN_PART, HIDDEN_CONTENTS], ids=["a part", "the whole"])
def test_contents_pdfium_reads_otherwise_leave_every_colour_unknown(data):
    """PDFium runs the hidden scn; QPDF reads a dictionary there (measured in
    review)."""
    with pdfacts.open(data) as document:
        found = paths(document, 0)
    assert [path.fill_colour for path in found] == [Unknown(UnknownReason.UNREADABLE, UNSCANNED)]


def test_a_part_of_contents_pdfium_reads_otherwise_leaves_the_page_unchecked():
    with pdfacts.open(HIDDEN_PART, limits=Limits(max_forms_per_page=10)) as document:
        with pytest.raises(LimitUncheckedError):
            document.pages[0]


HIDDEN_FORM = stream(
    b"/Pattern cs /P0 scn 0 0 10 10 re f", FORM + b" /Resources " + HIDDEN_RESOURCES
)


def test_a_form_pdfium_reads_otherwise_leaves_every_colour_unknown():
    """The hidden form sets a pattern for PDFium; QPDF does not see it."""
    data = hidden_file(
        b" /Resources " + HIDDEN_RESOURCES + b" >>",
        b"20 0 R",
        HIDDEN_FORM,
        {20: stream(b"/F Do 1 0 0 rg" + RECT + b"f")},
    )
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()
    with pdfacts.open(data) as document:
        found = paths(document, 0)
    assert [path.fill_colour for path in found] == [
        Unknown(UnknownReason.UNREADABLE, UNSCANNED)
    ] * 2


def test_an_image_pdfium_reads_otherwise_does_not_stop_the_pattern_check():
    """Only a form can set a pattern colour; an image held so is drawn by
    PDFium alone and changes no colour."""
    image = stream(b"\x00\xff\x80\x40", IMAGE_DICTIONARY)
    data = hidden_file(
        b" /Resources " + HIDDEN_RESOURCES + b" >>",
        b"20 0 R",
        image,
        {20: stream(b"1 0 0 rg" + RECT + b"f q 20 0 0 20 0 0 cm /F Do Q")},
    )
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is False
    finally:
        document.close()


def test_an_appearance_s_form_pdfium_reads_otherwise_leaves_its_colours_unknown():
    extra = {
        30: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 31 0 R >> >>",
        31: stream(b"/F Do", FORM + b" /Resources " + HIDDEN_RESOURCES),
        20: stream(b"1 0 0 rg" + RECT + b"f"),
    }
    data = hidden_file(
        b" /Resources " + HIDDEN_RESOURCES + b" /Annots [30 0 R] >>", b"20 0 R", HIDDEN_FORM, extra
    )
    with pdfacts.open(data) as document:
        found = paths(document, 0)
    assert [path.fill_colour for path in found] == [
        RED,
        Unknown(UnknownReason.UNREADABLE, UNSCANNED),
    ]


@pytest.mark.parametrize(
    ("space", "expected"),
    [
        (b"[(Pattern) /DeviceRGB]", True),
        (b"[(Pattern)]", False),
        (b"[(/Pattern) /DeviceRGB]", False),
    ],
)
def test_a_pattern_colour_space_whose_family_is_a_string(space, expected):
    """PDFium takes the string (Pattern) for the family where a base follows
    (measured in review)."""
    resources = RESOURCES.replace(b"/PU [", b"/CS0 " + space + b" /PU [")
    data = page_file(b"/CS0 cs 0 0 1 sc" + RECT + b"f", resources=resources)
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is expected
    finally:
        document.close()
    if expected:
        assert colours(data) == [(WHOLE, None)]


@pytest.mark.parametrize(
    ("shown", "hidden"),
    [
        (b"/DeviceRGB cs /P0 scn 1 g", b"/Pattern cs /S scn"),
        (SET + b"0.749 g", b"/Pattern cs /P0 scn"),
    ],
    ids=["a shading's white", "a tiling pattern's grey"],
)
def test_a_colour_pdfium_makes_up_is_unknown_where_qpdf_misses_the_pattern(shown, hidden):
    """The part that sets the pattern is named with another generation:
    PDFium reads it, QPDF reads null and does not warn (measured in review).
    The colours PDFium makes up are not read where a pattern is set."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300]"
        b" /Contents [20 0 R 21 1 R 22 0 R] /Resources " + RESOURCES + b" >>",
        5: CELL,
        6: UNCOLOURED,
        7: stream(b"0 0 10 10 re f", FORM + b" /Resources " + RESOURCES),
        10: IMAGE,
        11: SHADING,
        20: stream(shown),
        21: stream(hidden),
        22: stream(RECT + b"f"),
    }
    assert colours(build(objects, 1)) == [(FILL, None)]


def test_a_colour_space_named_as_a_device_colour_space_is_not_looked_up():
    """PDFium does not look up /DeviceRGB among the resources (measured in
    review): such a resource does not make a Pattern colour space."""
    resources = RESOURCES.replace(b"/ColorSpace << ", b"/ColorSpace << /DeviceRGB /Pattern ")
    data = page_file(b"/DeviceRGB cs 0 0 1 sc" + RECT + b"f", resources=resources)
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is False
    finally:
        document.close()


def test_an_appearance_starts_from_the_default_state():
    """The page sets a pattern; the appearance's first path is drawn from the
    default state, its second after a pattern of its own."""
    appearance = stream(
        b"0 0 10 10 re f /Pattern cs /P0 scn 0 0 10 10 re f",
        FORM + b" /Resources " + RESOURCES,
    )
    extra = {
        8: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 9 0 R >> >>",
        9: appearance,
    }
    data = page_file(b"/Pattern cs /P0 scn" + RECT + b"f", annots=b"[8 0 R]", extra=extra)
    assert colours(data) == [(FILL, None), (BLACK, None), (FILL, None)]


def test_a_misread_in_an_appearance_s_resources_leaves_its_colours_unknown():
    """QPDF warns that it misread the resources of the appearance (a key with
    #00, issue G1's measurement) only once it reads them; its warnings
    gather, so the page's colour is unknown too ("Paths and images")."""
    extra = {
        8: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 9 0 R >> >>",
        9: stream(b"1 0 0 rg 0 0 10 10 re f", FORM + b" /Resources 11 0 R"),
        11: b"<< /XObject << /Fx#00 7 0 R >> >>",
    }
    with pdfacts.open(
        page_file(b"0 0 1 rg" + RECT + b"f", annots=b"[8 0 R]", extra=extra)
    ) as document:
        found = paths(document, 0)
    assert len(found) == 2
    for path in found:
        assert isinstance(path.fill_colour, Unknown)
        assert path.fill_colour.detail.startswith("QPDF cannot read the content, or warns")


@pytest.mark.parametrize(
    ("default", "content", "expected"),
    [
        (b"/DefaultRGB /Pattern", b"/DeviceRGB cs 0 0 1 sc" + RECT + b"f", [(FILL, None)]),
        (
            b"/DefaultRGB [/Pattern /DeviceRGB]",
            b"/DeviceRGB cs 0 0 1 sc" + RECT + b"f",
            [(WHOLE, None)],
        ),
        (b"/DefaultGray 12 0 R", b"/DeviceGray cs 0.5 sc" + RECT + b"f", [(FILL, None)]),
        (b"/DefaultCMYK /Pattern", b"/DeviceCMYK CS 0 1 0 0 SC" + RECT + b"S", [(None, STROKE)]),
        (
            b"/DefaultRGB /Pattern",
            b"0 0 1 rg" + RECT + b"f",
            [(DisplayColour(0, 0, 255, 255), None)],
        ),
    ],
    ids=[
        "DefaultRGB",
        "a default with a base",
        "DefaultGray given by reference",
        "DefaultCMYK for the stroke",
        "rg, which PDFium takes as it is",
    ],
)
def test_a_default_colour_space_that_is_a_pattern_space(default, content, expected):
    resources = RESOURCES.replace(b"/ColorSpace << ", b"/ColorSpace << " + default + b" ")
    data = page_file(content, resources=resources, extra={12: b"[/Pattern]"})
    assert colours(data) == expected


def test_a_default_colour_space_counts_where_cs_selects_its_device_space():
    resources = RESOURCES.replace(b"/ColorSpace << ", b"/ColorSpace << /DefaultRGB /Pattern ")
    for content, expected in (
        (b"0 0 1 rg" + RECT + b"f", False),
        (b"/DeviceRGB cs" + RECT + b"f", True),
    ):
        document = qpdf_backend.QpdfDocument.open(page_file(content, resources=resources), None)
        try:
            assert document.page_uses_pattern(0) is expected
        finally:
            document.close()


def test_a_warning_about_an_appearance_leaves_its_colours_unknown():
    appearance = stream(
        b"/Pattern cs /P0 scn <4 x h 0 0 1 rg> 1 w 0 0 10 10 re f",
        FORM + b" /Resources " + RESOURCES,
    )
    extra = {
        8: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 9 0 R >> >>",
        9: appearance,
    }
    data = page_file(b"/Pattern cs /P0 scn" + RECT + b"f", annots=b"[8 0 R]", extra=extra)
    assert colours(data) == [(FILL, None), (WHOLE, None)]


def test_qpdf_reads_each_path_with_its_colours():
    data = page_file(
        b"1 0 0 rg 0 0 1 RG" + RECT + b"B /Pattern cs /P0 scn" + RECT + b"B /X Do 0.5 G"
        b" /Pattern CS /P0 SCN" + RECT + b"S"
    )
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is True
        assert document.page_painted(0, 4) == painted(
            ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 2, True, (10.0, 10.0)),
            (None, (0.0, 0.0, 1.0), 2, True, (10.0, 10.0)),
            (None, None, 2, False, (0.0, 0.0)),
            (None, None, 0, True, (10.0, 10.0)),
        )
        assert document.page_painted(0, 3) is None
    finally:
        document.close()
    document = qpdf_backend.QpdfDocument.open(page_file(b"/Pattern cs /P0 scn /X Do /X Do"), None)
    try:
        assert document.page_painted(0, 2) == painted((None, None, 2, False, (0.0, 0.0))) * 2
        assert document.page_painted(0, 1) is None
    finally:
        document.close()


def test_qpdf_reads_the_paths_of_an_appearance():
    appearance = stream(
        b"0 0 1 rg 0 0 10 10 re f /Pattern cs /P0 scn 0 0 10 10 re f",
        FORM + b" /Resources " + RESOURCES,
    )
    extra = {
        8: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] >>",
        9: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 11 0 R >> >>",
        11: appearance,
    }
    data = page_file(b"", annots=b"[8 0 R 9 0 R]", extra=extra)
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.appearance_painted(0, 1, 2) == painted(
            ((0.0, 0.0, 1.0), None, 2, False, (0.0, 0.0)),
            (None, None, 2, False, (0.0, 0.0)),
        )
        assert document.appearance_painted(0, 1, 1) is None
        assert document.appearance_painted(0, 0, 2) is None
        assert document.appearance_painted(0, 2, 2) is None
    finally:
        document.close()


@pytest.mark.parametrize(
    ("subtype", "expected"),
    [(b"(/Form)", [(FILL, None)]), (b"(Form)", [(FILL, None), (BLUE, None)])],
    ids=["the string (/Form), which PDFium does not draw", "the string (Form)"],
)
def test_a_form_qpdf_reads_as_pdfium_draws_it(subtype, expected):
    form = stream(
        b"0 0 1 rg 0 0 10 10 re f", b"/Type /XObject /Subtype " + subtype + b" /BBox [0 0 400 300]"
    )
    resources = RESOURCES.replace(b"/X 7 0 R", b"/X 7 0 R /T 29 0 R")
    data = page_file(
        b"/Pattern cs /P0 scn" + RECT + b"f /T Do", extra={29: form}, resources=resources
    )
    assert colours(data) == expected


def test_where_qpdf_misreads_an_object_it_reads_no_paths():
    """A name QPDF repairs (issue G1's measurement) leaves the paths unread,
    as it leaves the pattern."""
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /Resources << /XObject << /Fx#00 7 0 R >> /Pattern << /P0 5 0 R >> >> >>",
            3: stream(b"/Pattern cs /P0 scn" + RECT + b"f"),
            5: CELL,
            7: stream(b"0 0 10 10 re f", FORM),
        },
        1,
    )
    document = qpdf_backend.QpdfDocument.open(data, None)
    try:
        assert document.page_painted(0, 1) is None
    finally:
        document.close()


def painted(*records: tuple) -> tuple:
    """The paths QPDF reads, each given by the values of a Painted: the
    components of its colours as the content writes them."""
    return tuple(protocols.Painted(*record) for record in records)


# The two paths of PAIRED, as QPDF reads them.
PAIRED = b"1 0 0 rg" + RECT + b"f /Pattern cs /P0 scn" + RECT + b"f"
RED_PATH = ((1.0, 0.0, 0.0), None, 2, False, (10.0, 10.0))
PATTERN_PATH = (None, None, 2, False, (10.0, 10.0))


@pytest.mark.parametrize(
    "records",
    [
        (RED_PATH,),
        (RED_PATH, PATTERN_PATH, RED_PATH),
        None,
        (((1.0, 0.0, 0.0), None, 1, False, (10.0, 10.0)), PATTERN_PATH),
        (((1.0, 0.0, 0.0), None, 2, True, (10.0, 10.0)), PATTERN_PATH),
        (((1.0, 0.0, 0.0), None, 2, False, (10.0, 10.01)), PATTERN_PATH),
        (((1.0, 0.0, 0.0), None, 2, False, (10.01, 10.0)), PATTERN_PATH),
    ],
    ids=[
        "fewer",
        "more",
        "none read",
        "another fill mode",
        "stroked",
        "another first point, y",
        "another first point, x",
    ],
)
def test_other_paths_than_pdfium_makes_leave_every_colour_unknown(monkeypatch, records):
    """Where QPDF's paths do not match PDFium's, one for one, painted alike
    and from the same first point, or QPDF cannot read them, the content's
    colours are unknown as a whole."""
    found = None if records is None else painted(*records)
    monkeypatch.setattr(qpdf_backend.QpdfDocument, "page_painted", lambda self, index, cap: found)
    with pdfacts.open(page_file(PAIRED)) as document:
        found_paths = paths(document, 0)
    assert [(p.fill_colour, p.stroke_colour) for p in found_paths] == [
        (Unknown(UnknownReason.UNREADABLE, UNPAIRED), None)
    ] * 2


def test_paths_that_match_pdfium_s_give_their_colours(monkeypatch):
    found = painted(((1.0, 0.0, 0.0), None, 2, False, (10.00001, 9.99999)), PATTERN_PATH)
    monkeypatch.setattr(qpdf_backend.QpdfDocument, "page_painted", lambda self, index, cap: found)
    assert colours(page_file(PAIRED)) == [(RED, None), (FILL, None)]


def test_without_a_pattern_qpdf_is_not_asked_for_the_colours(monkeypatch):
    def refused(self, *arguments):
        raise AssertionError("QPDF asked for the colours of a content without a pattern")

    monkeypatch.setattr(qpdf_backend.QpdfDocument, "page_painted", refused)
    monkeypatch.setattr(qpdf_backend.QpdfDocument, "appearance_painted", refused)
    extra = {
        8: b"<< /Type /Annot /Subtype /Square /Rect [100 100 140 130] /AP << /N 9 0 R >> >>",
        9: stream(b"0 0 1 rg 0 0 10 10 re f", FORM),
    }
    assert colours(page_file(b"1 0 0 rg" + RECT + b"f", annots=b"[8 0 R]", extra=extra)) == [
        (RED, None),
        (BLUE, None),
    ]
