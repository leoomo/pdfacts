"""Files and facts the tests of the M4b gate are made of.

Files are built byte by byte; the numbers a test expects are those written into
them. Facts (glyphs, words, a page's geometry) are made with the constructors.
"""

from __future__ import annotations

from pdfacts import Glyph, GlyphRef, PageGeometry, Point, Quad, Rect, Unknown, UnknownReason, Word
from synthetic.builder import build, stream

HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
UNKNOWN = Unknown(UnknownReason.UNREADABLE, "made here")
# A page of 200 x 100, which a rendering at 4 to the point makes 800 x 400 pixels.
SMALL = b"[0 0 200 100]"


def file_of(
    content: bytes,
    *,
    font: bytes = HELVETICA,
    objects: dict[int, bytes] | None = None,
    entries: bytes = b"",
    media: bytes = b"[0 0 400 300]",
    resources: bytes = b"/Font << /F1 5 0 R >>",
) -> bytes:
    """A one-page file. Object 5 is the font /F1."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox " + media + b" /Contents 3 0 R"
    page += b" /Resources << " + resources + b" >> " + entries + b" >>"
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: stream(content),
            4: page,
            5: font,
            **(objects or {}),
        },
        1,
    )


def squares(content: bytes, height: int = 700, bbox: bytes = b"[0 0 1000 1000]", **more) -> bytes:
    """A file, on a page of 200 x 100 unless `media` says otherwise, whose font /F1 is a Type 3
    font 1000 thousandths wide with /FontBBox `bbox`: "A" is a rectangle 1000 wide and `height`
    tall in glyph space, "B" has no ink. At size 10 "A" is 10 wide and height / 100 tall, one
    em being 1000."""
    font = (
        b"<< /Type /Font /Subtype /Type3 /FontBBox " + bbox + b" /FontMatrix [0.001 0 0 0.001 0 0]"
        b" /CharProcs << /A 7 0 R /B 8 0 R >> /Encoding << /Type /Encoding"
        b" /Differences [65 /A /B] >> /FirstChar 65 /LastChar 66 /Widths [1000 1000]"
        b" /Resources << >> >>"
    )
    objects = {
        7: stream(b"1000 0 d0 0 0 1000 %d re f" % height),
        8: stream(b"1000 0 d0"),
        **more.pop("objects", {}),
    }
    more.setdefault("media", SMALL)
    return file_of(content, font=font, objects=objects, **more)


def written() -> bytes:
    """A page of 400 x 300 with, at size 10:
    - "AB", a word;
    - a text object of one space, which PDFium leaves out;
    - "EF" in render mode 3, which paints nothing and is a word of its own.
    AB, space, EF: 5 glyphs of V2, 2 words, 2 lines; PDFium's 4 characters A, B, E and F."""
    return file_of(
        b"BT /F1 10 Tf 100 270 Td (AB) Tj ET"
        b" BT /F1 10 Tf 300 150 Td ( ) Tj ET"
        b" q BT /F1 10 Tf 3 Tr 100 240 Td (EF) Tj ET Q"
    )


def deep_forms() -> dict[int, bytes]:
    """Forms nested 41 deep, objects 100 to 140, the last showing "A": PDFium reads 40 levels, so
    the text of a page that draws the first is unknown as a whole. The page's resources hold
    the first as /X."""
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 400 300] /Resources << /Font << /F1 5 0 R >>"
    chain = {}
    for level in range(41):
        last = level == 40
        content = b"BT /F1 10 Tf 100 200 Td (A) Tj ET" if last else b"/X Do"
        held = b">>" if last else b" /XObject << /X %d 0 R >> >>" % (101 + level)
        chain[100 + level] = stream(content, form + held)
    return chain


DEEP_RESOURCES = b"/Font << /F1 5 0 R >> /XObject << /X 100 0 R >>"


def glyph(text, x=100.0, y=200.0, *, mode=0, index=0) -> Glyph:
    """A glyph fact at (x, y): a text that may be unknown, an origin that may be
    unknown (x None)."""
    return Glyph(
        ref=GlyphRef(page_index=0, glyph_index=index),
        text=text,
        origin=UNKNOWN if x is None else Point(x, y),
        direction=0.0,
        size=10.0,
        box=UNKNOWN,
        upright=True,
        render_mode=mode,
        hyphen=False,
    )


def quad_of(x0, y0, x1, y1) -> Quad:
    return Quad(Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))


def upright(text, x0, y0, x1, y1, *, index=0, mode=0, size=10.0, direction=0.0) -> Glyph:
    """A glyph fact with the box (x0, y0, x1, y1), its origin at the box's lower left."""
    return Glyph(
        ref=GlyphRef(page_index=0, glyph_index=index),
        text=text,
        origin=Point(x0, y0),
        direction=direction,
        size=size,
        box=quad_of(x0, y0, x1, y1),
        upright=True,
        render_mode=mode,
        hyphen=False,
    )


def word_of(box, glyphs=(0,)) -> Word:
    return Word(page_index=0, glyphs=glyphs, text="A", box=Rect(*box))


def geometry_of(box=(0.0, 0.0, 200.0, 100.0), rotation=0) -> PageGeometry:
    return PageGeometry(index=0, visible_box=Rect(*box), rotation=rotation, user_unit=1.0)
