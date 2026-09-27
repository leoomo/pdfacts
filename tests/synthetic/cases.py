"""Named test files that several test modules share.

Each function returns the bytes of a file together with the numbers it was
built from. A test compares what pdfacts reads with these numbers, never with
the output of an engine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from synthetic.builder import PageSpec, document, page_object, truncate
from synthetic.encryption import encrypt

Box = tuple[float, float, float, float]

MEDIA_BOX: Box = (0, 0, 400, 300)
# A filled black rectangle on every page of the geometry cases, in user space.
RECTANGLE: Box = (50, 40, 110, 60)
RECTANGLE_CONTENT = b"0 g 50 40 60 20 re f"


def number_text(value: float) -> bytes:
    return b"%d" % value if float(value).is_integer() else repr(float(value)).encode()


def box_text(box: Box) -> bytes:
    return b"[" + b" ".join(number_text(value) for value in box) + b"]"


@dataclass(frozen=True)
class Pages:
    """A file with one or more pages, and the entries they were built from."""

    data: bytes
    page_objects: tuple[int, ...]
    # Written on each page; None where a page has no box of its own.
    media_boxes: tuple[Box | None, ...]
    # Written on the page tree node, from which the pages inherit.
    pages_media_box: Box | None = None
    # The following are written on every page, as they are given here.
    crop_box: Box | None = None
    rotate: int | None = None
    user_unit: bytes | None = None
    pages_rotate: int | None = None
    # Drawn on every page, in user space.
    rectangle: Box = RECTANGLE


def _pages(
    media_boxes: Sequence[Box | None],
    *,
    pages_media_box: Box | None = None,
    crop_box: Box | None = None,
    rotate: int | None = None,
    user_unit: bytes | None = None,
    pages_rotate: int | None = None,
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
        specs.append(PageSpec(b" ".join(entries), RECTANGLE_CONTENT))
    tree_entries = []
    if pages_media_box is not None:
        tree_entries.append(b"/MediaBox " + box_text(pages_media_box))
    if pages_rotate is not None:
        tree_entries.append(b"/Rotate %d" % pages_rotate)
    return Pages(
        data=document(specs, pages_entries=b" ".join(tree_entries)),
        page_objects=tuple(page_object(index) for index in range(len(specs))),
        media_boxes=tuple(media_boxes),
        pages_media_box=pages_media_box,
        crop_box=crop_box,
        rotate=rotate,
        user_unit=user_unit,
        pages_rotate=pages_rotate,
    )


def plain_page() -> Pages:
    return _pages([MEDIA_BOX])


def one_rectangle() -> Pages:
    """One page with one filled rectangle at RECTANGLE; every geometry case has it."""
    return plain_page()


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
    return _pages([(0, 0, 400, 300), (0, 0, 300, 500), (0, 0, 612, 792)])


ROTATIONS = (0, 90, 180, 270)
# Written as they are; a reader has to bring them into 0, 90, 180 or 270.
ROTATIONS_AS_WRITTEN = (-90, 450, 45)
USER_UNITS = (b"2", b"2.5", b"0", b"-1", b"(text)")

GEOMETRY: dict[str, Callable[[], Pages]] = {
    "plain page": plain_page,
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
    """The start of three_pages(): the file was cut after `cut_at` bytes."""

    data: bytes
    cut_at: int
    pages_built: int


def _truncated(data: bytes) -> Truncated:
    return Truncated(data, len(data), len(three_pages().page_objects))


def cut_inside_xref_table() -> Truncated:
    full = three_pages().data
    return _truncated(truncate(full, keep=full.rindex(b"\nxref\n") + 1 + 40))


def cut_before_xref_table() -> Truncated:
    full = three_pages().data
    return _truncated(truncate(full, keep=full.rindex(b"\nxref\n") + 1))


def cut_inside_last_page() -> Truncated:
    """Cut in the middle of the content stream of the last page, the last object."""
    full = three_pages().data
    start = full.index(b"%d 0 obj" % (page_object(2) + 1))
    return _truncated(truncate(full, keep=(start + full.rindex(b"endobj")) // 2))


def cut_after_first_page() -> Truncated:
    return _truncated(truncate(three_pages().data, before=b"%d 0 obj" % page_object(1)))


def cut_inside_catalog() -> Truncated:
    full = three_pages().data
    return _truncated(truncate(full, keep=full.index(b"1 0 obj") + 12))


TRUNCATED: dict[str, Callable[[], Truncated]] = {
    "cut inside the xref table": cut_inside_xref_table,
    "cut before the xref table": cut_before_xref_table,
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
