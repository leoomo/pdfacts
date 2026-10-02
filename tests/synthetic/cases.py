"""Named test files that several test modules share.

Each function returns the bytes of a file together with the numbers it was
built from. A test compares what pdfacts reads with these numbers, never with
the output of an engine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from synthetic.builder import (
    FIRST_PAGE,
    FONT_OBJECT,
    PAGE_TREE,
    PageSpec,
    append_update,
    build,
    document,
    page_object,
    size_of_document,
    stream,
    truncate,
)
from synthetic.encryption import encrypt

Box = tuple[float, float, float, float]
Colour = tuple[float, float, float]

MEDIA_BOX: Box = (0, 0, 400, 300)
# A filled rectangle on every page of the geometry cases, in user space.
RECTANGLE: Box = (50, 40, 110, 60)
BLACK: Colour = (0, 0, 0)
RED: Colour = (1, 0, 0)


def number_text(value: float) -> bytes:
    return b"%d" % value if float(value).is_integer() else repr(float(value)).encode()


def box_text(box: Box) -> bytes:
    return b"[" + b" ".join(number_text(value) for value in box) + b"]"


def rectangle_content(box: Box, colour: Colour) -> bytes:
    """A content stream that fills `box` with `colour`."""
    x0, y0, x1, y1 = box
    fill = b" ".join(number_text(value) for value in colour)
    size = b" ".join(number_text(value) for value in (x0, y0, x1 - x0, y1 - y0))
    return fill + b" rg " + size + b" re f"


@dataclass(frozen=True)
class Pages:
    """A file with one or more pages, and the entries they were built from."""

    data: bytes
    page_objects: tuple[int, ...]
    # The /Size of the trailer: the highest object number plus 1.
    size: int
    # Written on each page; None where a page has no box of its own.
    media_boxes: tuple[Box | None, ...]
    # Written on the page tree node, from which the pages inherit.
    pages_media_box: Box | None = None
    # The following are written on every page, as they are given here.
    crop_box: Box | None = None
    rotate: int | None = None
    user_unit: bytes | None = None
    pages_rotate: int | None = None
    # Filled on every page, in user space.
    rectangle: Box = RECTANGLE
    colour: Colour = BLACK


def _pages(
    media_boxes: Sequence[Box | None],
    *,
    pages_media_box: Box | None = None,
    crop_box: Box | None = None,
    rotate: int | None = None,
    user_unit: bytes | None = None,
    pages_rotate: int | None = None,
    colour: Colour = BLACK,
) -> Pages:
    specs = []
    for media_box in media_boxes:
        entries = []
        if media_box is not None:
            entries.append(b"/MediaBox " + box_text(media_box))
        if crop_box is not None:
            entries.append(b"/CropBox " + box_text(crop_box))
        if rotate is not None:
            entries.append(b"/Rotate %d" % rotate)
        if user_unit is not None:
            entries.append(b"/UserUnit " + user_unit)
        specs.append(PageSpec(b" ".join(entries), rectangle_content(RECTANGLE, colour)))
    tree_entries = []
    if pages_media_box is not None:
        tree_entries.append(b"/MediaBox " + box_text(pages_media_box))
    if pages_rotate is not None:
        tree_entries.append(b"/Rotate %d" % pages_rotate)
    return Pages(
        data=document(specs, pages_entries=b" ".join(tree_entries)),
        page_objects=tuple(page_object(index) for index in range(len(specs))),
        size=size_of_document(len(specs)),
        media_boxes=tuple(media_boxes),
        pages_media_box=pages_media_box,
        crop_box=crop_box,
        rotate=rotate,
        user_unit=user_unit,
        pages_rotate=pages_rotate,
        colour=colour,
    )


def plain_page() -> Pages:
    return _pages([MEDIA_BOX])


def one_rectangle() -> Pages:
    """One page with one filled rectangle at RECTANGLE; every geometry case has it."""
    return plain_page()


def red_rectangle() -> Pages:
    """As plain_page(), with the rectangle filled in pure red."""
    return _pages([MEDIA_BOX], colour=RED)


def rotated(rotation: int) -> Pages:
    return _pages([MEDIA_BOX], rotate=rotation)


def crop_box_inside() -> Pages:
    return _pages([MEDIA_BOX], crop_box=(20, 10, 380, 290))


def crop_box_inside_rotated() -> Pages:
    return _pages([MEDIA_BOX], crop_box=(20, 10, 380, 290), rotate=90)


def crop_box_larger() -> Pages:
    return _pages([MEDIA_BOX], crop_box=(-50, -50, 500, 400))


def shifted_origin() -> Pages:
    return _pages([(30, 20, 430, 320)])


def reversed_corners() -> Pages:
    return _pages([(400, 300, 0, 0)])


def media_box_on_pages() -> Pages:
    return _pages([None], pages_media_box=MEDIA_BOX)


def media_box_and_rotation_on_pages() -> Pages:
    return _pages([None], pages_media_box=MEDIA_BOX, pages_rotate=90)


def no_media_box() -> Pages:
    return _pages([None])


def user_unit(text: bytes) -> Pages:
    return _pages([MEDIA_BOX], user_unit=text)


def three_pages() -> Pages:
    # None of the sizes is 612 x 792, the box both engines fall back to.
    return _pages([(0, 0, 400, 300), (0, 0, 300, 500), (0, 0, 595, 842)])


ROTATIONS = (0, 90, 180, 270)
# Written as they are; a reader has to bring them into 0, 90, 180 or 270.
ROTATIONS_AS_WRITTEN = (-90, 450, 45)
USER_UNITS = (b"2", b"2.5", b"0", b"-1", b"(text)")

GEOMETRY: dict[str, Callable[[], Pages]] = {
    "plain page": plain_page,
    "red rectangle": red_rectangle,
    **{f"rotation {r}": partial(rotated, r) for r in ROTATIONS + ROTATIONS_AS_WRITTEN},
    "crop box inside": crop_box_inside,
    "crop box inside, rotation 90": crop_box_inside_rotated,
    "crop box larger than the media box": crop_box_larger,
    "media box with shifted origin": shifted_origin,
    "media box with reversed corners": reversed_corners,
    "media box only on the page tree": media_box_on_pages,
    "media box and rotation only on the page tree": media_box_and_rotation_on_pages,
    "no media box": no_media_box,
    **{f"UserUnit {text.decode()}": partial(user_unit, text) for text in USER_UNITS},
    "three pages": three_pages,
}


@dataclass(frozen=True)
class TextPage:
    """One page with one line of text in a font that is not embedded."""

    data: bytes
    font: bytes
    text: bytes
    font_size: float
    origin: tuple[float, float]


def verdana_text() -> TextPage:
    """Text in Verdana, not embedded and without /Widths.

    Without widths in the file, the glyph positions depend on which font
    program the engine picks, which is what the font policy controls.
    """
    font = b"<< /Type /Font /Subtype /TrueType /BaseFont /Verdana /Encoding /WinAnsiEncoding >>"
    text, size, origin = b"Hamburgefonstiv 0123", 24.0, (40.0, 200.0)
    content = b"BT /F1 %s Tf %s %s Td (%s) Tj ET" % (
        number_text(size),
        number_text(origin[0]),
        number_text(origin[1]),
        text,
    )
    data = document([PageSpec(b"/MediaBox " + box_text(MEDIA_BOX), content)], font=font)
    return TextPage(data, font, text, size, origin)


@dataclass(frozen=True)
class Unusable:
    data: bytes


def empty() -> Unusable:
    return Unusable(b"")


def header_only() -> Unusable:
    return Unusable(b"%PDF-1.7\n")


def garbage() -> Unusable:
    return Unusable(b"this is not a PDF file\n" * 40)


def garbage_after_header() -> Unusable:
    return Unusable(b"%PDF-1.7\n" + bytes(range(256)) * 4)


UNUSABLE: dict[str, Callable[[], Unusable]] = {
    "empty": empty,
    "header only": header_only,
    "garbage": garbage,
    "garbage after a header": garbage_after_header,
}


@dataclass(frozen=True)
class Truncated:
    """The start of `source`: the file was cut after `cut_at` bytes."""

    data: bytes
    cut_at: int
    source: Pages

    @property
    def pages_built(self) -> int:
        return len(self.source.page_objects)


def _truncated(source: Pages, data: bytes) -> Truncated:
    return Truncated(data, len(data), source)


def _table(data: bytes) -> int:
    return data.rindex(b"\nxref\n") + 1


def cut_inside_xref_table() -> Truncated:
    source = three_pages()
    return _truncated(source, truncate(source.data, keep=_table(source.data) + 40))


def cut_before_xref_table() -> Truncated:
    source = three_pages()
    return _truncated(source, truncate(source.data, keep=_table(source.data)))


def cut_before_xref_table_rotated_cropped() -> Truncated:
    """As cut_before_xref_table(), of a page with rotation 90 and a crop box."""
    source = crop_box_inside_rotated()
    return _truncated(source, truncate(source.data, keep=_table(source.data)))


def cut_inside_last_page() -> Truncated:
    """Cut in the middle of the data of the last content stream, the last object."""
    source = three_pages()
    last = source.data.index(b"%d 0 obj" % (source.size - 1))
    start = source.data.index(b"stream\n", last) + len(b"stream\n")
    end = source.data.index(b"\nendstream", start)
    return _truncated(source, truncate(source.data, keep=(start + end) // 2))


def cut_after_first_page() -> Truncated:
    source = three_pages()
    return _truncated(source, truncate(source.data, before=b"%d 0 obj" % page_object(1)))


def cut_inside_catalog() -> Truncated:
    source = three_pages()
    return _truncated(source, truncate(source.data, keep=source.data.index(b"1 0 obj") + 12))


TRUNCATED: dict[str, Callable[[], Truncated]] = {
    "cut inside the xref table": cut_inside_xref_table,
    "cut before the xref table": cut_before_xref_table,
    "cut before the xref table, rotation 90 and crop box": cut_before_xref_table_rotated_cropped,
    "cut inside the last page": cut_inside_last_page,
    "cut after the first page": cut_after_first_page,
    "cut inside the catalog": cut_inside_catalog,
}


@dataclass(frozen=True)
class Encrypted:
    data: bytes
    user_password: str
    owner_password: str
    pages_built: int


def user_password() -> Encrypted:
    """A user password is set: the file cannot be opened without a password."""
    return Encrypted(encrypt(plain_page().data, user="user", owner="owner"), "user", "owner", 1)


def owner_password_only() -> Encrypted:
    """Only an owner password is set: the file opens without a password."""
    return Encrypted(encrypt(plain_page().data, user="", owner="owner"), "", "owner", 1)


ENCRYPTED: dict[str, Callable[[], Encrypted]] = {
    "user password": user_password,
    "owner password only": owner_password_only,
}


@dataclass(frozen=True)
class Changed:
    """A file made from `source` by changing or adding bytes."""

    data: bytes
    source: Pages


def _startxref(data: bytes) -> tuple[int, int]:
    """Where the number after the last startxref begins, and the number."""
    start = data.rindex(b"startxref\n") + len(b"startxref\n")
    return start, int(data[start : data.index(b"\n", start)])


def startxref_off_by_three() -> Changed:
    """The last startxref points three bytes behind the cross-reference table."""
    source = plain_page()
    start, table = _startxref(source.data)
    end = source.data.index(b"\n", start)
    return Changed(source.data[:start] + b"%d" % (table + 3) + source.data[end:], source)


def line_ends_crlf() -> Changed:
    """Every LF written as CR LF, as a text transfer does: every offset is wrong."""
    source = plain_page()
    return Changed(source.data.replace(b"\n", b"\r\n"), source)


# PDFium opens these only by rebuilding the cross-reference table.
REBUILT: dict[str, Callable[[], Changed]] = {
    "startxref three bytes off": startxref_off_by_three,
    "line ends CR LF": line_ends_crlf,
}


def junk_after_the_end() -> Changed:
    """Bytes that are not white space after the last %%EOF."""
    source = plain_page()
    return Changed(source.data + b"<html>appended on the way</html>\n", source)


def white_space_after_the_end() -> Changed:
    """Only white space after the last %%EOF, NUL included (ISO 32000, 7.2.2)."""
    source = plain_page()
    return Changed(source.data + b"\r\n \t\x0c\x00\n", source)


@dataclass(frozen=True)
class Updated:
    """`source` with an incremental update that gives its page another media box."""

    data: bytes
    source: Pages
    media_box: Box


UPDATED_MEDIA_BOX: Box = (0, 0, 300, 200)


def _updated_page() -> bytes:
    """The page of plain_page() with UPDATED_MEDIA_BOX."""
    resources = b"/Resources << /Font << /F1 %d 0 R >> >>" % FONT_OBJECT
    return b"<< /Type /Page /Parent %d 0 R /MediaBox %s %s /Contents %d 0 R >>" % (
        PAGE_TREE,
        box_text(UPDATED_MEDIA_BOX),
        resources,
        FIRST_PAGE + 1,
    )


def incremental_update() -> Updated:
    source = plain_page()
    data = append_update(source.data, {FIRST_PAGE: _updated_page()}, 1)
    return Updated(data, source, UPDATED_MEDIA_BOX)


def cut_inside_the_last_offset(*, naming_the_update: bool = False, closed: bool = True) -> Updated:
    """The update of incremental_update(), padded, cut inside its last offset.

    A string that nothing uses pads the update until its table lies at ten
    times the offset that the cut leaves after the last startxref: the offset
    of the source's table, or with `naming_the_update` that of the update's
    first object. Without `closed`, the source's %%EOF is left out before the
    update. The objects of the update are whole.
    """
    source = plain_page()
    written = source.data if closed else source.data[: source.data.rindex(b"%%EOF")]
    _, earlier = _startxref(source.data)
    named = len(written) if naming_the_update else earlier

    def padded(padding: int) -> bytes:
        objects = {FIRST_PAGE: _updated_page(), size_of_document(1): b"(%s)" % (b"x" * padding)}
        return append_update(written, objects, 1)

    _, table = _startxref(padded(0))
    data = padded(10 * named - table)
    start, _ = _startxref(data)
    return Updated(data[: start + len(b"%d" % named)], source, UPDATED_MEDIA_BOX)


def update_in_a_cross_reference_stream(
    *, gap: bytes = b" ", sign: bytes = b"", generation: bytes = b"0"
) -> Updated:
    """The update of incremental_update() written with a cross-reference stream.

    Its entries are not compressed. `gap` separates the numbers and the
    keyword of the update's two object headers, `sign` comes before each
    object number, and `generation` is written as the generation, as QPDF
    reads them.
    """
    source = plain_page()
    _, earlier = _startxref(source.data)

    def header(number: int) -> bytes:
        return sign + b"%d" % number + gap + generation + gap + b"obj\n"

    out = bytearray(source.data)
    page = len(out)
    out += header(FIRST_PAGE) + _updated_page() + b"\nendobj\n"
    table = len(out)
    number = size_of_document(1) + 1
    entries = b"".join(bytes([1]) + at.to_bytes(4, "big") + bytes([0]) for at in (page, table))
    out += header(number)
    out += b"<< /Type /XRef /Size %d /W [1 4 1] /Index [%d 1 %d 1] /Root 1 0 R /Prev %d " % (
        number + 1,
        FIRST_PAGE,
        number,
        earlier,
    )
    out += b"/Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(entries), entries)
    out += b"startxref\n%d\n%%%%EOF\n" % table
    return Updated(bytes(out), source, UPDATED_MEDIA_BOX)


@dataclass(frozen=True)
class Linearized:
    """A one-page file laid out as a linearized one, and where its main table begins."""

    data: bytes
    main_table: int


def linearized(
    *, first_page_offset: bool = False, first_page_trailer: bool = True, between: int = 0
) -> Linearized:
    """A one-page file with the layout of ISO 32000-1, Annex F.

    A linearization dictionary, its /E the end of the first page; a
    first-page section with its own table and a trailer whose /Prev names the
    main table; the objects; the main table, whose startxref names the
    first-page table. The first-page trailer's startxref is 0, or with
    `first_page_offset` the offset of the first-page table. Without
    `first_page_trailer`, as some files are written, the first-page section
    has no table and trailer of its own: the main table lists every object,
    and its startxref names it. `between` objects that nothing uses lie
    between the linearization dictionary and the first-page table, a layout
    the standard does not have. Hint streams are left out: no engine reads
    them without being asked to.
    """
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox %s /Resources << >> /Contents 4 0 R >>"
        % box_text(MEDIA_BOX),
        4: stream(rectangle_content(RECTANGLE, BLACK)),
    }
    # Numbers written with ten digits, so that they can be filled in once
    # known without moving what follows.
    out = bytearray(b"%PDF-1.7\n")
    dictionary = len(out)
    out += b"5 0 obj\n<< /Linearized 1 /E 0000000000 >>\nendobj\n"
    for number in range(between):
        out += b"%d 0 obj\n<< >>\nendobj\n" % (6 + number)
    first_table = len(out)
    if first_page_trailer:
        out += b"xref\n5 1\n%010d 00000 n \n" % dictionary
        out += b"trailer\n<< /Size 6 /Root 1 0 R /Prev 0000000000 >>\n"
        out += b"startxref\n%d\n%%%%EOF\n" % (first_table if first_page_offset else 0)
    offsets = {5: dictionary}
    for number, body in objects.items():
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    main_table = len(out)
    listed = [*objects] if first_page_trailer else [*objects, 5]
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(listed) + 1)
    out += b"".join(b"%010d 00000 n \n" % offsets[number] for number in listed)
    named = first_table if first_page_trailer else main_table
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % named
    data = bytes(out)
    data = data.replace(b"/E 0000000000", b"/E %010d" % main_table, 1)
    data = data.replace(b"/Prev 0000000000", b"/Prev %010d" % main_table, 1)
    return Linearized(data, main_table)


def cut_inside_the_update() -> Changed:
    """incremental_update() cut inside the object of its update.

    The last %%EOF left is the one of the source, which is complete.
    """
    updated = incremental_update()
    keep = len(updated.source.data) + 20
    return Changed(truncate(updated.data, keep=keep), updated.source)


def no_end_marker() -> Changed:
    """The %%EOF of plain_page() written as %%END; every offset stays right."""
    source = plain_page()
    return Changed(source.data.replace(b"%%EOF", b"%%END"), source)


def vertical_tab_after_the_end() -> Changed:
    """A vertical tab after the last %%EOF: white space in Python, not in PDF."""
    source = plain_page()
    return Changed(source.data + b"\x0b", source)


# Anything but white space after the last %%EOF, or no %%EOF at all.
BAD_ENDS: dict[str, Callable[[], Changed]] = {
    "junk after the end": junk_after_the_end,
    "cut inside an incremental update": cut_inside_the_update,
    "no end marker": no_end_marker,
    "vertical tab after the end": vertical_tab_after_the_end,
}


@dataclass(frozen=True)
class NoPages:
    data: bytes


def empty_page_tree() -> NoPages:
    return NoPages(
        build(
            {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [] /Count 0 >>"}, 1
        )
    )


def no_page_tree() -> NoPages:
    return NoPages(build({1: b"<< /Type /Catalog >>"}, 1))


def missing_page_tree() -> NoPages:
    """The catalog names object 7 as its page tree; there is no object 7."""
    return NoPages(build({1: b"<< /Type /Catalog /Pages 7 0 R >>"}, 1))


# PDFium finds no page in these, and cannot tell them apart.
NO_PAGES: dict[str, Callable[[], NoPages]] = {
    "empty page tree": empty_page_tree,
    "no page tree": no_page_tree,
    "page tree missing": missing_page_tree,
}


def count_without_kids() -> NoPages:
    """/Count promises one page; /Kids lists none."""
    return NoPages(
        build(
            {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [] /Count 1 >>"}, 1
        )
    )


def missing_kid() -> NoPages:
    """/Kids names object 7 as the one page; there is no object 7."""
    return NoPages(
        build(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [7 0 R] /Count 1 >>",
            },
            1,
        )
    )


def page_with_kids_null() -> NoPages:
    """Three pages, the first with /Kids null.

    QPDF ignores the null value; PDFium takes the page for a node of the tree
    without kids, so it counts three pages and finds two.
    """

    def page(box: Box, extra: bytes = b"") -> bytes:
        return b"<< /Type /Page /Parent 2 0 R /MediaBox %s %s/Contents 9 0 R >>" % (
            box_text(box),
            extra,
        )

    return NoPages(
        build(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [4 0 R 5 0 R 6 0 R] /Count 3 >>",
                4: page((0, 0, 400, 300), b"/Kids null "),
                5: page((0, 0, 300, 500)),
                6: page((0, 0, 595, 842)),
                9: stream(b""),
            },
            1,
        )
    )


def missing_kid_in_the_middle() -> NoPages:
    """Three kids, the second of which names object 7; there is no object 7."""

    def page(box: Box) -> bytes:
        return b"<< /Type /Page /Parent 2 0 R /MediaBox %s /Contents 9 0 R >>" % box_text(box)

    return NoPages(
        build(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [4 0 R 7 0 R 5 0 R] /Count 3 >>",
                4: page((0, 0, 400, 300)),
                5: page((0, 0, 300, 500)),
                9: stream(b""),
            },
            1,
        )
    )


# PDFium counts these pages but finds no page dictionary for one of them.
MISSING_PAGES: dict[str, Callable[[], NoPages]] = {
    "count without kids": count_without_kids,
    "missing kid": missing_kid,
    "page with /Kids null": page_with_kids_null,
    "missing kid in the middle": missing_kid_in_the_middle,
}


# --- What a page draws (the M3 gate) ---------------------------------------------
#
# Each case is a page of 400 x 300 and what it draws, in drawing order. The
# points are the construction's, put into user space by hand: each case says,
# in its docstring or in the comment above it, which matrices it writes and
# where they take a point. A case that writes none draws in user space as its
# content is written.

XY = tuple[float, float]

DRAWN_FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300]"
# Three by two pixels of black: placed, it fills its corners in black.
BLACK_IMAGE = stream(
    bytes(6),
    b"/Type /XObject /Subtype /Image /Width 3 /Height 2 /ColorSpace /DeviceGray"
    b" /BitsPerComponent 8",
)


@dataclass(frozen=True)
class DrawnSubpath:
    """A subpath as built: its shape under the rules of docs/conventions.md, and
    its points in user space, the move's and then each segment's in order."""

    shape: str
    points: tuple[XY, ...]
    closed: bool = False


@dataclass(frozen=True)
class DrawnPath:
    subpaths: tuple[DrawnSubpath, ...]
    # The index in /Annots of the annotation whose appearance draws it; None
    # where the page's content draws it.
    annotation: int | None = None


@dataclass(frozen=True)
class DrawnImage:
    """A placement of the image of 3 x 2 pixels, DeviceGray, 8 bits."""

    # Where the image's (0, 0), (1, 0), (1, 1) and (0, 1) go, in user space.
    corners: tuple[XY, XY, XY, XY]
    annotation: int | None = None


@dataclass(frozen=True)
class Drawing:
    """A one-page file and what its page draws, in drawing order."""

    data: bytes
    drawn: tuple[DrawnPath | DrawnImage, ...]
    # Everything is filled in black: the dark pixels of a rendering lie in
    # the box that holds every point drawn.
    inked: bool = True


def _drawing_file(
    content: bytes,
    *,
    resources: bytes = b"",
    annots: bytes = b"",
    objects: dict[int, bytes] | None = None,
) -> bytes:
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


def _one(shape: str, *points: XY, closed: bool = False) -> DrawnPath:
    return DrawnPath((DrawnSubpath(shape, points, closed),))


# The rectangle (10, 20) to (110, 70), drawn from (10, 20) anticlockwise.
_RECTANGLE = _one("rectangle", (10, 20), (110, 20), (110, 70), (10, 70), (10, 20), closed=True)


def _shape(content: bytes, *drawn: DrawnPath, inked: bool = True) -> Drawing:
    return Drawing(_drawing_file(content), drawn, inked)


def _images_on_page(content: bytes, *corners: tuple[XY, XY, XY, XY]) -> Drawing:
    data = _drawing_file(
        content, resources=b"<< /XObject << /Im 5 0 R >> >>", objects={5: BLACK_IMAGE}
    )
    return Drawing(data, tuple(DrawnImage(c) for c in corners))


def forms_nested_two_deep() -> Drawing:
    """The page doubles and moves by (100, 50); form X turns by 90 degrees,
    (x, y) to (-y, x), and moves its content by (10, 0); form Y moves by
    (0, 5). (0, 0) of Y: (0, 5), (10, 5), (-5, 10), (90, 70)."""
    data = _drawing_file(
        b"2 0 0 2 100 50 cm /X Do",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={
            5: stream(
                b"1 0 0 1 10 0 cm /Y Do",
                b"/Type /XObject /Subtype /Form /BBox [-100 -100 100 100]"
                b" /Matrix [0 1 -1 0 0 0] /Resources << /XObject << /Y 6 0 R >> >>",
            ),
            6: stream(
                b"0 0 20 10 re f",
                b"/Type /XObject /Subtype /Form /BBox [0 0 20 10] /Matrix [1 0 0 1 0 5]",
            ),
        },
    )
    drawn = _one("rectangle", (90, 70), (90, 110), (70, 110), (70, 70), (90, 70), closed=True)
    return Drawing(data, (drawn,))


def one_form_drawn_twice() -> Drawing:
    """Form X moves by (10, 10). Drawn once under a doubling: (0, 0) of the
    form at (20, 20); then, the state restored, without it: at (10, 10)."""
    data = _drawing_file(
        b"q 2 0 0 2 0 0 cm /X Do Q /X Do",
        resources=b"<< /XObject << /X 5 0 R >> >>",
        objects={5: stream(b"0 0 30 20 re f", DRAWN_FORM + b" /Matrix [1 0 0 1 10 10]")},
    )
    doubled = _one("rectangle", (20, 20), (80, 20), (80, 60), (20, 60), (20, 20), closed=True)
    plain = _one("rectangle", (10, 10), (40, 10), (40, 30), (10, 30), (10, 10), closed=True)
    return Drawing(data, (doubled, plain))


def _annotated(appearance: bytes, appearance_entries: bytes, objects=None) -> bytes:
    """A Square annotation on /Rect [100 100 200 150] whose normal appearance,
    object 7, has /BBox [0 0 50 25]: placed, it doubles and moves by (100, 100)
    unless its /Matrix turns it."""
    return _drawing_file(
        b"",
        annots=b"[6 0 R]",
        objects={
            6: b"<< /Type /Annot /Subtype /Square /Rect [100 100 200 150] /F 4"
            b" /AP << /N 7 0 R >> >>",
            7: stream(
                appearance,
                b"/Type /XObject /Subtype /Form /BBox [0 0 50 25] " + appearance_entries,
            ),
            **(objects or {}),
        },
    )


def appearance_with_a_form() -> Drawing:
    """The form moves by (10, 5), the placement doubles and moves by (100, 100):
    (0, 0) of the form at (120, 110)."""
    data = _annotated(
        b"/F2 Do",
        b"/Resources << /XObject << /F2 8 0 R >> >>",
        {
            8: stream(
                b"0 0 20 10 re f",
                b"/Type /XObject /Subtype /Form /BBox [0 0 50 25] /Matrix [1 0 0 1 10 5]",
            )
        },
    )
    drawn = DrawnPath(
        (
            DrawnSubpath(
                "rectangle", ((120, 110), (160, 110), (160, 130), (120, 130), (120, 110)), True
            ),
        ),
        annotation=0,
    )
    return Drawing(data, (drawn,))


def appearance_turned_by_its_matrix() -> Drawing:
    """/Matrix [0 1 -1 0 0 0] turns /BBox to (-25, 0, 0, 50); placed on /Rect,
    x is taken 4 times and y once: (x, y) to (-4y + 200, x + 100)."""
    data = _annotated(b"0 0 20 10 re f", b"/Matrix [0 1 -1 0 0 0]")
    drawn = DrawnPath(
        (
            DrawnSubpath(
                "rectangle", ((200, 100), (200, 120), (160, 120), (160, 100), (200, 100)), True
            ),
        ),
        annotation=0,
    )
    return Drawing(data, (drawn,))


def one_image_placed_three_times() -> Drawing:
    """Scaled, (x, y) to (30x + 10, 20y + 10); turned, (x, y) to
    (-30y + 100, 20x + 50); mirrored, (x, y) to (-30x + 250, 20y + 10)."""
    return _images_on_page(
        b"q 30 0 0 20 10 10 cm /Im Do Q q 0 20 -30 0 100 50 cm /Im Do Q"
        b" q -30 0 0 20 250 10 cm /Im Do Q",
        ((10, 10), (40, 10), (40, 30), (10, 30)),
        ((100, 50), (100, 70), (70, 70), (70, 50)),
        ((250, 10), (220, 10), (220, 30), (250, 30)),
    )


def one_image_in_a_form_drawn_twice() -> Drawing:
    """The form scales the image to 30 x 20; the page moves it by (10, 100),
    then doubles it and moves it by (100, 100)."""
    data = _drawing_file(
        b"q 1 0 0 1 10 100 cm /F Do Q q 2 0 0 2 100 100 cm /F Do Q",
        resources=b"<< /XObject << /F 5 0 R >> >>",
        objects={
            5: stream(
                b"30 0 0 20 0 0 cm /Im Do",
                DRAWN_FORM + b" /Resources << /XObject << /Im 6 0 R >> >>",
            ),
            6: BLACK_IMAGE,
        },
    )
    return Drawing(
        data,
        (
            DrawnImage(((10, 100), (40, 100), (40, 120), (10, 120))),
            DrawnImage(((100, 100), (160, 100), (160, 140), (100, 140))),
        ),
    )


def an_inline_image() -> Drawing:
    """Scaled, (x, y) to (30x + 10, 20y + 10)."""
    data = _drawing_file(
        b"q 30 0 0 20 10 10 cm BI /W 3 /H 2 /CS /G /BPC 8 ID " + bytes(6) + b" EI Q"
    )
    return Drawing(data, (DrawnImage(((10, 10), (40, 10), (40, 30), (10, 30))),))


def an_image_in_an_appearance() -> Drawing:
    """The appearance scales the image to its /BBox; placed, (0, 0) at (100, 100)."""
    data = _annotated(
        b"50 0 0 25 0 0 cm /Im Do", b"/Resources << /XObject << /Im 8 0 R >> >>", {8: BLACK_IMAGE}
    )
    corners = ((100, 100), (200, 100), (200, 150), (100, 150))
    return Drawing(data, (DrawnImage(corners, annotation=0),))


def paths_and_images_in_drawing_order() -> Drawing:
    """A rectangle, an image, a rectangle, then the annotation's rectangle. The
    image is scaled, (x, y) to (30x + 100, 20y + 100); the appearance's /BBox
    has the size of /Rect, so placing it only moves it, by (300, 200)."""
    data = _drawing_file(
        b"10 20 50 30 re f q 30 0 0 20 100 100 cm /Im Do Q 200 20 50 30 re f",
        resources=b"<< /XObject << /Im 5 0 R >> >>",
        annots=b"[6 0 R]",
        objects={
            5: BLACK_IMAGE,
            6: b"<< /Type /Annot /Subtype /Square /Rect [300 200 350 225] /AP << /N 7 0 R >> >>",
            7: stream(b"0 0 50 25 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 50 25]"),
        },
    )
    return Drawing(
        data,
        (
            _one("rectangle", (10, 20), (60, 20), (60, 50), (10, 50), (10, 20), closed=True),
            DrawnImage(((100, 100), (130, 100), (130, 120), (100, 120))),
            _one("rectangle", (200, 20), (250, 20), (250, 50), (200, 50), (200, 20), closed=True),
            DrawnPath(
                (
                    DrawnSubpath(
                        "rectangle",
                        ((300, 200), (350, 200), (350, 225), (300, 225), (300, 200)),
                        True,
                    ),
                ),
                annotation=0,
            ),
        ),
    )


GRAPHICS: dict[str, Callable[[], Drawing]] = {
    # The shape rules.
    "re": partial(_shape, b"10 20 100 50 re f", _RECTANGLE),
    "m l l l h": partial(_shape, b"10 20 m 110 20 l 110 70 l 10 70 l h f", _RECTANGLE),
    "m l l l l h": partial(_shape, b"10 20 m 110 20 l 110 70 l 10 70 l 10 20 l h f", _RECTANGLE),
    "a line": partial(
        _shape, b"10 20 m 110 70 l S", _one("line", (10, 20), (110, 70)), inked=False
    ),
    # (x, y) to (-y + 300, x): the edges still keep x or y.
    "a rectangle turned by 90 degrees": partial(
        _shape,
        b"0 1 -1 0 300 0 cm 10 20 100 50 re f",
        _one("rectangle", (280, 10), (280, 110), (230, 110), (230, 10), (280, 10), closed=True),
    ),
    # (x, y) to (x + y, y): a parallelogram.
    "a sheared rectangle": partial(
        _shape,
        b"1 0 1 1 0 0 cm 10 20 100 50 re f",
        _one("quad", (30, 20), (130, 20), (180, 70), (80, 70), (30, 20), closed=True),
    ),
    "a trapezoid": partial(
        _shape,
        b"10 20 m 110 20 l 90 70 l 30 70 l h f",
        _one("quad", (10, 20), (110, 20), (90, 70), (30, 70), (10, 20), closed=True),
    ),
    # 2^-11 off: within 0.001, one number.
    "a corner 2^-11 off": partial(
        _shape,
        b"10 20 m 110 20.00048828125 l 110 70 l 10 70 l h f",
        _one(
            "rectangle", (10, 20), (110, 20.00048828125), (110, 70), (10, 70), (10, 20), closed=True
        ),
    ),
    # 2^-6 off: beyond 0.001.
    "a corner 2^-6 off": partial(
        _shape,
        b"10 20 m 110 20.015625 l 110 70 l 10 70 l h f",
        _one("quad", (10, 20), (110, 20.015625), (110, 70), (10, 70), (10, 20), closed=True),
    ),
    "an L": partial(
        _shape,
        b"10 20 m 110 20 l 110 40 l 30 40 l 30 70 l 10 70 l h f",
        _one(
            "polyline",
            (10, 20),
            (110, 20),
            (110, 40),
            (30, 40),
            (30, 70),
            (10, 70),
            (10, 20),
            closed=True,
        ),
    ),
    "an open polyline": partial(
        _shape,
        b"10 20 m 60 70 l 110 20 l S",
        _one("polyline", (10, 20), (60, 70), (110, 20)),
        inked=False,
    ),
    "a curve, c": partial(
        _shape,
        b"10 20 m 20 60 100 60 110 20 c S",
        _one("curves", (10, 20), (20, 60), (100, 60), (110, 20)),
        inked=False,
    ),
    # v: the first control point is the current point.
    "a curve, v": partial(
        _shape,
        b"10 20 m 60 70 110 20 v S",
        _one("curves", (10, 20), (10, 20), (60, 70), (110, 20)),
        inked=False,
    ),
    # y: the second control point is the end.
    "a curve, y": partial(
        _shape,
        b"10 20 m 60 70 110 20 y S",
        _one("curves", (10, 20), (60, 70), (110, 20), (110, 20)),
        inked=False,
    ),
    "two rectangles in one path": partial(
        _shape,
        b"10 20 50 30 re 200 100 50 30 re f",
        DrawnPath(
            (
                DrawnSubpath("rectangle", ((10, 20), (60, 20), (60, 50), (10, 50), (10, 20)), True),
                DrawnSubpath(
                    "rectangle", ((200, 100), (250, 100), (250, 130), (200, 130), (200, 100)), True
                ),
            )
        ),
    ),
    # Nested transforms.
    "forms nested two deep": forms_nested_two_deep,
    "one form drawn twice": one_form_drawn_twice,
    "an appearance with a form": appearance_with_a_form,
    "an appearance turned by its /Matrix": appearance_turned_by_its_matrix,
    # Repeated images.
    "one image placed three times": one_image_placed_three_times,
    "one image in a form drawn twice": one_image_in_a_form_drawn_twice,
    "an inline image": an_inline_image,
    "an image in an appearance": an_image_in_an_appearance,
    "paths and images in drawing order": paths_and_images_in_drawing_order,
}


# --- The text a page holds (the M4a gate) ----------------------------------------
#
# Each case is a page of 400 x 300 and the text it holds, as written. Font /F1
# is 1000 thousandths wide for every character, ascent 800, descent -200,
# metrics PDFium takes from the file; /F2 is the same font with a /ToUnicode
# that maps code 0x41 to "fi". At size 10 a glyph is 10 wide and its box runs
# from 2 below its baseline to 8 above. Steps are in units of the size, and
# every position and step written is exact in binary, so that PDFium's floats
# hold it.
TEXT_FONT = (
    b"<< /Type /Font /Subtype /TrueType /BaseFont /Gate /FirstChar 32 /LastChar 126 /Widths ["
    + b" ".join([b"1000"] * 95)
    + b"] /Encoding /WinAnsiEncoding /FontDescriptor 6 0 R"
)
TEXT_DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /Gate /Flags 32 /FontBBox [0 -200 1000 800]"
    b" /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>"
)
LIGATURE_MAP = stream(
    b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Gate def"
    b" 1 begincodespacerange <00> <FF> endcodespacerange"
    b" 1 beginbfchar <41> <00660069> endbfchar"
    b" endcmap CMapName currentdict /CMap defineresource pop end end"
)


@dataclass(frozen=True)
class WrittenText:
    """A one-page file of text and what it holds as written: the text of each
    glyph in content order, and the words of each line, each with its box in
    user space where the case gives one."""

    data: bytes
    glyphs: tuple[str, ...]
    lines: tuple[tuple[tuple[str, Box | None], ...], ...]
    # Every glyph that paints lies in the box of a word: the dark pixels of a
    # rendering lie in the words' boxes, and each word's box holds some.
    inked: bool = True


def _text_file(content: bytes, page: bytes) -> bytes:
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
            b" /Resources << /Font << /F1 5 0 R /F2 7 0 R >> >> " + page + b">>",
            5: TEXT_FONT + b" >>",
            6: TEXT_DESCRIPTOR,
            7: TEXT_FONT + b" /ToUnicode 8 0 R >>",
            8: LIGATURE_MAP,
        },
        1,
    )


def _written(
    content: bytes,
    glyphs: str | tuple[str, ...],
    *lines: tuple[tuple[str, Box | None], ...],
    page: bytes = b"",
    inked: bool = True,
) -> WrittenText:
    return WrittenText(_text_file(content, page), tuple(glyphs), lines, inked)


def _word(text: str, x: float, y: float = 200, size: float = 10) -> tuple[str, Box]:
    """A word of glyphs 1000 wide from (x, y) at `size`, with its box."""
    return (text, (x, y - 0.2 * size, x + len(text) * size, y + 0.8 * size))


def _span(text: str, x0: float, x1: float, y0: float = 198, y1: float = 208) -> tuple[str, Box]:
    """A word with the box from (x0, y0) to (x1, y1)."""
    return (text, (x0, y0, x1, y1))


def _two_words(x: float, y: float = 200, size: float = 10) -> bytes:
    """ "AB" at size 10 from (100, 200), ending at 120; then "CD" at `size` from (x, y)."""
    return b"BT /F1 10 Tf 100 200 Td (AB) Tj ET BT /F1 %s Tf %s %s Td (CD) Tj ET" % (
        number_text(size),
        number_text(x),
        number_text(y),
    )


TEXT: dict[str, Callable[[], WrittenText]] = {
    # The glyphs.
    # "CD" begins 5 after "AB" ends: PDFium adds a space between, of its own.
    "PDFium's own space is no glyph": partial(
        _written, _two_words(125), "ABCD", (_word("AB", 100), _word("CD", 125))
    ),
    # PDFium's text page puts A and B first, in order of x. In content order C
    # and D come first, and the step back from 220 to 100 begins a line.
    "glyphs in content order": partial(
        _written,
        b"BT /F1 10 Tf 200 200 Td (CD) Tj ET BT /F1 10 Tf 100 200 Td (AB) Tj ET",
        "CDAB",
        (_word("CD", 200),),
        (_word("AB", 100),),
    ),
    # One code, two characters at one origin.
    "a ligature PDFium expands is one glyph": partial(
        _written, b"BT /F2 10 Tf 100 200 Td (A) Tj ET", ("fi",), (_span("fi", 100, 110),)
    ),
    "a line-end hyphen": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td (Hyphen-) Tj 0 -14 Td (ated) Tj ET",
        "Hyphen\x02ated",
        (_word("Hyphen-", 100),),
        (_word("ated", 100, 186),),
    ),
    # A step along the baseline, from the end of B at 120 to the origin of C.
    "a gap of 0.1375 is within a word": partial(
        _written, _two_words(121.375), "ABCD", (_span("ABCD", 100, 141.375),)
    ),
    "a gap of 0.15 ends a word": partial(
        _written, _two_words(121.5), "ABCD", (_word("AB", 100), _word("CD", 121.5))
    ),
    "a gap of 0.7875 ends a word": partial(
        _written, _two_words(127.875), "ABCD", (_word("AB", 100), _word("CD", 127.875))
    ),
    "a gap of 0.8 begins a line": partial(
        _written, _two_words(128), "ABCD", (_word("AB", 100),), (_word("CD", 128),)
    ),
    "a step back of 0.7875 is within a word": partial(
        _written, _two_words(112.125), "ABCD", (_span("ABCD", 100, 132.125),)
    ),
    "a step back of 0.8 begins a line": partial(
        _written, _two_words(112), "ABCD", (_word("AB", 100),), (_word("CD", 112),)
    ),
    # A step across the baseline, C at the end of B.
    "a baseline 0.7875 up is the line's": partial(
        _written, _two_words(120, 207.875), "ABCD", (_span("ABCD", 100, 140, 198, 215.875),)
    ),
    "a baseline 0.7875 down is the line's": partial(
        _written, _two_words(120, 192.125), "ABCD", (_span("ABCD", 100, 140, 190.125, 208),)
    ),
    "a baseline 0.8 up begins a line": partial(
        _written, _two_words(120, 208), "ABCD", (_word("AB", 100),), (_word("CD", 120, 208),)
    ),
    "a baseline 0.8 down begins a line": partial(
        _written, _two_words(120, 192), "ABCD", (_word("AB", 100),), (_word("CD", 120, 192),)
    ),
    # "CD" at size 20 begins 2.875 after "AB" ends: 0.14375 of its own size, a
    # step within the word; of the size of B, 10, it would be 0.2875.
    "a step along in units of the later glyph's size": partial(
        _written, _two_words(122.875, size=20), "ABCD", (_span("ABCD", 100, 162.875, 196, 216),)
    ),
    # "CD" at size 20 begins at the end of B, 15 above its baseline: 0.75 of
    # its own size, on the line; of the size of B, 10, it would be 1.5.
    "a step across in units of the later glyph's size": partial(
        _written, _two_words(120, 215, size=20), "ABCD", (_span("ABCD", 100, 160, 198, 231),)
    ),
    # The space's box runs from 120 to 130, where C begins.
    "a space ends a word and moves the pen": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td (AB CD) Tj ET",
        "AB CD",
        (_word("AB", 100), _word("CD", 130)),
    ),
    # At 50 Tz a glyph is 5 wide; "CD" begins 1.375 after "AB" ends: 0.1375 of
    # the size, 10, a step within the word; of the width as scaled, 0.275.
    "horizontal scaling leaves the size": partial(
        _written,
        b"BT /F1 10 Tf 50 Tz 100 200 Td (AB) Tj 11.375 0 Td (CD) Tj ET",
        "ABCD",
        (_span("ABCD", 100, 121.375),),
    ),
    # X runs up the page: no pen moves to its end, and C, 0.1375 after B,
    # stays in the word.
    "a glyph that is not upright is passed over": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td (AB) Tj ET BT /F1 10 Tf 0 1 -1 0 300 100 Tm (X) Tj ET"
        b" BT /F1 10 Tf 121.375 200 Td (CD) Tj ET",
        "ABXCD",
        (_span("ABCD", 100, 141.375),),
        inked=False,
    ),
    # "AB" turned by 0.9 degrees, upright; "CD" by 1.1 degrees, in no line.
    # The matrices hold the cosine and sine of each angle.
    "upright within one degree": partial(
        _written,
        b"BT /F1 10 Tf 0.9998766324816606 0.015707317311820675 -0.015707317311820675"
        b" 0.9998766324816606 100 200 Tm (AB) Tj ET"
        b" BT /F1 10 Tf 0.9998157121216442 0.01919744239968967 -0.01919744239968967"
        b" 0.9998157121216442 100 100 Tm (CD) Tj ET",
        "ABCD",
        (("AB", None),),
        inked=False,
    ),
    # Turned by 90 degrees for showing: "AB" runs up user space, (x, y) to
    # (100 - y, 100 + x), and reads left to right; "CD" runs up as shown.
    "upright as the page is shown": partial(
        _written,
        b"BT /F1 10 Tf 0 1 -1 0 100 100 Tm (AB) Tj ET BT /F1 10 Tf 100 250 Td (CD) Tj ET",
        "ABCD",
        (_span("AB", 92, 102, 100, 120),),
        page=b"/Rotate 90 ",
        inked=False,
    ),
    "invisible text is given": partial(
        _written,
        b"BT /F1 10 Tf 3 Tr 100 200 Td (AB) Tj ET",
        "AB",
        (_word("AB", 100),),
        inked=False,
    ),
    # PDFium gives the two spaces as one character, at the first one's origin;
    # a text object of one space it leaves out.
    "a line of white space alone is none": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td (AB) Tj ET BT /F1 10 Tf 100 150 Td (  ) Tj ET",
        "AB ",
        (_word("AB", 100),),
    ),
    "text off the page is given": partial(
        _written,
        b"BT /F1 10 Tf 500 200 Td (AB) Tj ET",
        "AB",
        (_word("AB", 500),),
        inked=False,
    ),
    # Known to go wrong (docs/conventions.md): the box of a space does not hold
    # Tw. At 9 Tw the step from the space's end at 120 to B is 0.9.
    "a space Tw widens by 0.9: two lines": partial(
        _written,
        b"BT /F1 10 Tf 9 Tw 100 200 Td (A B) Tj ET",
        "A B",
        (_word("A", 100),),
        (_word("B", 129),),
    ),
    "a space Tw widens by 0.7: one line": partial(
        _written,
        b"BT /F1 10 Tf 7 Tw 100 200 Td (A B) Tj ET",
        "A B",
        (_word("A", 100), _word("B", 127)),
    ),
    # The space is drawn at 110, its box ends at 120; the TJ moves B back to
    # 110: a step back of 1.
    "a wide space at the next origin: two lines": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td [(A ) 1000 (B)] TJ ET",
        "A B",
        (_word("A", 100),),
        (_word("B", 110),),
    ),
    "letters Tc spaces by 0.8: a line each": partial(
        _written,
        b"BT /F1 10 Tf 8 Tc 100 200 Td (ABC) Tj ET",
        "ABC",
        (_word("A", 100),),
        (_word("B", 118),),
        (_word("C", 136),),
    ),
    "letters Tc spaces by 0.7: one line": partial(
        _written,
        b"BT /F1 10 Tf 7 Tc 100 200 Td (ABC) Tj ET",
        "ABC",
        (_word("A", 100), _word("B", 117), _word("C", 134)),
    ),
    # A gap of 0.8 that TJ makes begins a line: right for columns, wrong for
    # the words of justified text.
    "words TJ spaces by 0.8: two lines": partial(
        _written,
        b"BT /F1 10 Tf 100 200 Td [(AB) -800 (CD)] TJ ET",
        "ABCD",
        (_word("AB", 100),),
        (_word("CD", 128),),
    ),
}
