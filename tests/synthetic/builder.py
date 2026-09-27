"""Write PDF files byte by byte, for tests.

No engine takes part in building a file. A test takes its expected values from
the numbers it built the file with. The builder writes exactly what it is
given, valid or not: writing broken files on purpose is one of its jobs.

Files have a classic cross-reference table and a trailer, no object streams
and no cross-reference streams, so that every byte can be read by eye.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
# The default font of document(), which every page knows as /F1. It is one of
# the 14 standard fonts and is not embedded, so no font file is needed.
FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

# Object numbers in a file made by document().
CATALOG = 1
PAGE_TREE = 2
FONT_OBJECT = 3
FIRST_PAGE = 4


def build(objects: Mapping[int, bytes], root: int, *, header: bytes = HEADER) -> bytes:
    """A complete file: header, objects in ascending order, table, trailer."""
    out = bytearray(header)
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    size = max(objects, default=0) + 1
    free = [number for number in range(1, size) if number not in offsets]
    # Each free entry names the next free object; the last one names object 0.
    following = dict(zip([0, *free], [*free, 0], strict=True))
    table = len(out)
    out += b"xref\n0 %d\n" % size
    out += b"%010d 65535 f \n" % following[0]
    for number in range(1, size):
        if number in offsets:
            out += b"%010d 00000 n \n" % offsets[number]
        else:
            out += b"%010d 00001 f \n" % following[number]
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (size, root)
    out += b"startxref\n%d\n%%%%EOF\n" % table
    return bytes(out)


def stream(data: bytes, entries: bytes = b"") -> bytes:
    """The body of a stream object whose /Length is the length of data."""
    extra = b" " + entries if entries else b""
    return b"<< /Length %d%s >>\nstream\n%s\nendstream" % (len(data), extra, data)


@dataclass(frozen=True)
class PageSpec:
    # Written into the page dictionary as they are, for example
    # b"/MediaBox [0 0 400 300] /Rotate 90". Nothing is added.
    entries: bytes
    content: bytes = b""


def page_object(index: int) -> int:
    """The object number of page `index` in a file made by document()."""
    return FIRST_PAGE + 2 * index


def size_of_document(page_count: int) -> int:
    """The /Size of a file made by document(): its highest object number plus 1."""
    return page_object(page_count - 1) + 2 if page_count else FIRST_PAGE


def document(pages: Sequence[PageSpec], *, pages_entries: bytes = b"", font: bytes = FONT) -> bytes:
    """A file with the given pages; `pages_entries` go into the page tree node.

    `font` is the dictionary of the font that every page knows as /F1.
    """
    objects = {
        CATALOG: b"<< /Type /Catalog /Pages %d 0 R >>" % PAGE_TREE,
        FONT_OBJECT: font,
    }
    kids = []
    for index, page in enumerate(pages):
        number = page_object(index)
        kids.append(b"%d 0 R" % number)
        entries = b" " + page.entries if page.entries else b""
        objects[number] = b"<< /Type /Page /Parent %d 0 R%s " % (PAGE_TREE, entries) + (
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (FONT_OBJECT, number + 1)
        )
        objects[number + 1] = stream(page.content)
    extra = b" " + pages_entries if pages_entries else b""
    objects[PAGE_TREE] = b"<< /Type /Pages /Kids [%s] /Count %d%s >>" % (
        b" ".join(kids),
        len(pages),
        extra,
    )
    return build(objects, CATALOG)


def truncate(data: bytes, *, before: bytes | None = None, keep: int | None = None) -> bytes:
    """The start of data: up to the first occurrence of `before`, or `keep` bytes."""
    if (before is None) == (keep is None):
        raise TypeError("give exactly one of before and keep")
    if before is not None:
        position = data.find(before)
        if position < 0:
            raise ValueError(f"{before!r} does not occur in the data")
        return data[:position]
    assert keep is not None
    if not 0 <= keep <= len(data):
        raise ValueError(f"cannot keep {keep} of {len(data)} bytes")
    return data[:keep]
