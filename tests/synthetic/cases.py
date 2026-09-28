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


def cut_inside_the_last_offset(*, naming_the_update: bool = False) -> Updated:
    """The update of incremental_update(), padded, cut inside its last offset.

    A string that nothing uses pads the update until its table lies at ten
    times the offset that the cut leaves after the last startxref: the offset
    of the source's table, or with `naming_the_update` that of the update's
    first object. The objects of the update are whole.
    """
    source = plain_page()
    _, earlier = _startxref(source.data)
    named = len(source.data) if naming_the_update else earlier

    def padded(padding: int) -> bytes:
        objects = {FIRST_PAGE: _updated_page(), size_of_document(1): b"(%s)" % (b"x" * padding)}
        return append_update(source.data, objects, 1)

    _, table = _startxref(padded(0))
    data = padded(10 * named - table)
    start, _ = _startxref(data)
    return Updated(data[: start + len(b"%d" % named)], source, UPDATED_MEDIA_BOX)


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
