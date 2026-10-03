"""A name without bytes in a content stream: the page's form count and the
pattern check refuse it, as issue #62's rule refuses an object QPDF mis-reads.

A name whose # is not followed by two hexadecimal digits has no bytes
(docs/conventions.md, "Text strings and names"): QPDF holds a NUL in place of
the #, PDFium reads the name otherwise, and QPDF's warning about it names the
content stream and an offset, not an object in the form issue #62's rule looks
for, so the warning does not refuse the page by itself. The count and the
check take such names from the streams they parse, and what the engines make
of the stream may differ.

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

# Names, each after a T, whose # is not followed by two hexadecimal digits;
# in the last two an escape follows the # (the seven forms of #87).
STRAY = [b"T#zzx", b"T#4G", b"T#", b"T#4", b"T#+4x", b"T#4#31", b"T#A#42"]
STRAY_IDS = [
    "two letters",
    "a digit and a letter",
    "at the end",
    "one digit at the end",
    "a sign",
    "a digit and an escape",
    "a letter and an escape",
]


def page_file(content: bytes, objects: dict[int, bytes] | None = None) -> bytes:
    """One page whose content stream holds `content`."""
    return build({1: CATALOG, 2: PAGES, 3: PAGE, 4: stream(content), **(objects or {})}, 1)


def drawn(content: bytes):
    """The page's strokes through pdfacts.open."""
    with pdfacts.open(page_file(content)) as document:
        return pdfacts.paths(document, 0)


def form_file(form_content: bytes) -> bytes:
    """One page drawing form 6, whose content holds `form_content`."""
    page = PAGE.replace(b"/Resources << >>", b"/Resources << /XObject << /Fm 6 0 R >> >>")
    return build(
        {
            1: CATALOG,
            2: PAGES,
            3: page,
            4: stream(b"/Fm Do"),
            6: stream(form_content, b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]"),
        },
        1,
    )


@pytest.mark.parametrize("written", STRAY, ids=STRAY_IDS)
@pytest.mark.parametrize("operator", [b"Do", b"gs", b"scn", b"SCN"], ids=["Do", "gs", "scn", "SCN"])
def test_the_form_count_refuses_a_name_without_bytes(written, operator):
    """The count takes the name operand of each of these operators."""
    document = QpdfDocument.open(page_file(b"q /" + written + b" " + operator + b" Q"), None)
    try:
        assert document.parsed_streams(0) is None
    finally:
        document.close()


def test_the_form_count_refuses_the_font_name_without_bytes():
    """Tf takes its name from the operand before the size."""
    document = QpdfDocument.open(page_file(b"q /F#zz 12 Tf Q"), None)
    try:
        assert document.parsed_streams(0) is None
    finally:
        document.close()


@pytest.mark.parametrize("written", STRAY, ids=STRAY_IDS)
@pytest.mark.parametrize(
    "operator", [b"Do", b"scn", b"SCN", b"cs", b"CS"], ids=["Do", "scn", "SCN", "cs", "CS"]
)
def test_the_pattern_check_refuses_a_name_without_bytes(written, operator):
    """The check takes the name operand of these operators; gs and Tf, which
    cannot set a pattern colour, it does not read."""
    document = QpdfDocument.open(page_file(b"q /" + written + b" " + operator + b" Q"), None)
    try:
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()


@pytest.mark.parametrize("written", STRAY[:3], ids=STRAY_IDS[:3])
def test_a_form_s_content_refuses_the_count_and_the_pattern_check(written):
    """The page draws form 6; its content holds the name, for gs and for scn:
    the count refuses both, the pattern check, which reads no gs name, scn."""
    document = QpdfDocument.open(form_file(b"q /" + written + b" gs Q"), None)
    try:
        assert document.parsed_streams(0) is None
    finally:
        document.close()
    document = QpdfDocument.open(form_file(b"q /" + written + b" scn Q"), None)
    try:
        assert document.parsed_streams(0) is None
        assert document.page_uses_pattern(0) is None
    finally:
        document.close()


def test_a_name_without_bytes_leaves_the_page_unchecked():
    """With max_forms_per_page set, reading the page is refused; without the
    limit, the page reads and its strokes are given."""
    content = b"q /GS#zz gs 0 0 m 10 10 l S Q"
    with pytest.raises(LimitUncheckedError) as raised:
        with pdfacts.open(page_file(content), limits=Limits(max_forms_per_page=10)) as document:
            document.pages[0]
    assert raised.value.reason.reason is UnknownReason.UNREADABLE
    assert "name without bytes" in raised.value.reason.detail
    (path,) = drawn(content)
    assert path.ref.annotation is None


@pytest.mark.parametrize("operator", [b"cs", b"CS", b"Do"], ids=["cs", "CS", "Do"])
def test_the_colours_of_the_page_are_unknown(operator):
    """The pattern check cannot tell whether a pattern colour is set, so no
    colour of the page's strokes is known ("Paths and images"). scn and SCN
    already make them unknown with a name, pattern or not."""
    (path,) = drawn(b"q /N#zz " + operator + b" 0 0 m 10 10 l S Q")
    assert isinstance(path.stroke_colour, Unknown)
    assert path.stroke_colour.reason is UnknownReason.UNREADABLE
    assert "cannot read the content" in path.stroke_colour.detail


# --- names with bytes: no refusal -----------------------------------------------------


def test_a_page_whose_content_names_nothing_counts():
    document = QpdfDocument.open(page_file(b"q 0 0 m 10 10 l S Q"), None)
    try:
        assert document.parsed_streams(0) == 0
    finally:
        document.close()


def test_a_name_with_no_hash_still_counts():
    document = QpdfDocument.open(page_file(b"q /GS0 gs Q"), None)
    try:
        assert document.parsed_streams(0) == 0
        assert document.page_uses_pattern(0) is False
    finally:
        document.close()


@pytest.mark.parametrize(
    "written", [b"T#23x", b"T#e9"], ids=["a # left by #23", "a byte above 127"]
)
@pytest.mark.parametrize("operator", [b"gs", b"scn"], ids=["gs", "scn"])
def test_a_name_with_bytes_still_counts(written, operator):
    """/GST#23x holds a literal # in its bytes, /GST#e9 a byte above 127: both
    have bytes, and the count and the pattern check take them."""
    document = QpdfDocument.open(page_file(b"q /GS" + written + b" " + operator + b" Q"), None)
    try:
        assert document.parsed_streams(0) == 0
        assert document.page_uses_pattern(0) is (True if operator == b"scn" else False)
    finally:
        document.close()


def test_a_name_written_with_an_escape_draws_and_counts_its_form():
    """/X#41 is the name XA written with an escape: the form it names is
    counted, and the pattern check walks it."""
    page = PAGE.replace(b"/Resources << >>", b"/Resources << /XObject << /XA 5 0 R >> >>")
    data = build(
        {
            1: CATALOG,
            2: PAGES,
            3: page,
            4: stream(b"q /X#41 Do Q"),
            5: stream(b"q Q", b"/Type /XObject /Subtype /Form /BBox [0 0 10 10]"),
        },
        1,
    )
    document = QpdfDocument.open(data, None)
    try:
        assert document.parsed_streams(0) == 1
        assert document.page_uses_pattern(0) is False
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
