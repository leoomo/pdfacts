"""A name without bytes in a content stream: the page's form count and the
pattern check refuse it, as issue #62's rule refuses an object QPDF mis-reads.

A name whose # is not followed by two hexadecimal digits has no bytes
(docs/conventions.md, "Text strings and names"): QPDF holds a NUL in place of
the #, PDFium reads the name otherwise, and QPDF's warning about it names no
object and no offset, so the warning does not refuse the page by itself. The
count and the check take such names from the streams they parse, and what the
engines make of the stream may differ.

Files are built with the byte-level builder; the values expected follow from
what is written (AGENTS.md, rule 2), and from what the engines were measured
to do where a test says so.
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import Limits, LimitUncheckedError, Unknown, UnknownReason
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, stream

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>"
FORM = stream(b"q Q", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]")


def page_file(content: bytes, objects: dict[int, bytes] | None = None) -> bytes:
    """One page whose content stream holds `content`."""
    return build({1: CATALOG, 2: PAGES, 3: PAGE, 4: stream(content), **(objects or {})}, 1)


def drawn(content: bytes, objects: dict[int, bytes] | None = None):
    """The page's strokes through pdfacts.open."""
    with pdfacts.open(page_file(content, objects)) as document:
        return pdfacts.paths(document, 0)


@pytest.mark.parametrize(
    "content",
    [
        b"q /X#zz Do Q",
        b"q /GS#zz gs Q",
        b"q /P#zz scn Q",
        b"q /P#zz SCN Q",
        b"q /F#zz 12 Tf Q",
    ],
    ids=["Do", "gs", "scn", "SCN", "Tf"],
)
def test_the_form_count_refuses_a_name_without_bytes(content):
    """The count takes the name of each of these operators from the stream."""
    document = QpdfDocument.open(page_file(content), None)
    try:
        assert document.parsed_streams(0) is None
    finally:
        document.close()


@pytest.mark.parametrize(
    "content",
    [
        b"q /X#zz Do Q",
        b"q /P#zz scn Q",
        b"q /P#zz SCN Q",
        b"q /C#zz cs Q",
        b"q /C#zz CS Q",
    ],
    ids=["Do", "scn", "SCN", "cs", "CS"],
)
def test_the_pattern_check_refuses_a_name_without_bytes(content):
    """The check takes the names of these operators from the stream; gs and
    Tf, which cannot set a pattern colour, it does not read."""
    document = QpdfDocument.open(page_file(content), None)
    try:
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()


def test_the_form_count_refuses_a_name_without_bytes_in_a_form():
    """The page draws form 6; the form's content holds the name."""
    page = PAGE.replace(b"/Resources << >>", b"/Resources << /XObject << /Fm 6 0 R >> >>")
    content = b"/Fm Do"
    data = build(
        {
            1: CATALOG,
            2: PAGES,
            3: page,
            4: stream(content),
            6: stream(b"q /GS#zz gs Q", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]"),
        },
        1,
    )
    document = QpdfDocument.open(data, None)
    try:
        assert document.parsed_streams(0) is None
    finally:
        document.close()
    data = build(
        {
            1: CATALOG,
            2: PAGES,
            3: page,
            4: stream(content),
            6: stream(b"q /P#zz scn Q", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]"),
        },
        1,
    )
    document = QpdfDocument.open(data, None)
    try:
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()


def test_a_name_without_bytes_leaves_the_page_unchecked():
    """With max_forms_per_page set, reading the page is refused; without the
    limit, the page reads and its strokes are given."""
    content = b"q /P#zz scn 0 0 m 10 10 l S Q"
    with pytest.raises(LimitUncheckedError) as raised:
        with pdfacts.open(page_file(content), limits=Limits(max_forms_per_page=10)) as document:
            document.pages[0]
    assert raised.value.reason.reason is UnknownReason.UNREADABLE
    (path,) = drawn(content)
    assert path.ref.annotation is None


def test_the_colours_of_the_page_are_unknown():
    """The pattern check cannot tell whether a pattern colour is set, so no
    colour of the page's strokes is known ("Paths and images")."""
    (path,) = drawn(b"q /P#zz scn 0 0 m 10 10 l S Q")
    assert isinstance(path.stroke_colour, Unknown)
    assert path.stroke_colour.reason is UnknownReason.UNREADABLE
    assert "cannot read the content" in path.stroke_colour.detail


@pytest.mark.parametrize(
    ("content", "objects", "count"),
    [
        (b"q /GS0 gs Q", None, 0),
        (b"q Q", None, 0),
        # /X#41 is the name XA written with an escape, which has its bytes:
        # the form it names is counted.
        (
            b"q /X#41 Do Q",
            {5: stream(b"q Q", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]")},
            1,
        ),
    ],
    ids=["a name with no #", "no name taken", "a name with a valid escape"],
)
def test_names_with_bytes_still_count(content, objects, count):
    if content.endswith(b"Do Q"):
        page = PAGE.replace(b"/Resources << >>", b"/Resources << /XObject << /XA 5 0 R >> >>")
        data = build({1: CATALOG, 2: PAGES, 3: page, 4: stream(content), **(objects or {})}, 1)
    else:
        data = page_file(content, objects)
    document = QpdfDocument.open(data, None)
    try:
        assert document.parsed_streams(0) == count
    finally:
        document.close()


def test_a_name_with_a_valid_escape_still_sets_a_pattern_colour():
    """/C#31 is the name C1 written with an escape: the check sees scn take a
    name with bytes and answers True (measured, issue G2: PDFium gives no
    colour for it)."""
    document = QpdfDocument.open(page_file(b"q /C#31 scn Q"), None)
    try:
        assert document.page_uses_pattern(0) is True
    finally:
        document.close()
