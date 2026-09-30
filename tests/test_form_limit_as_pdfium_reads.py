"""max_forms_per_page where a file writes what PDFium reads leniently (issue G1b).

Each file hides a chain of forms behind one spelling: SIX forms, each drawing
the next twice, which makes PDFium parse 1 + 2 + 4 + 8 + 16 + 32 = 63 of them.
The counts expected follow from the construction under the rules of
docs/conventions.md, "Limits" (AGENTS.md, rule 2).
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import LimitExceededError, Limits, LimitUncheckedError
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, stream

SIX = 63
FORM = b"/Type /XObject /Subtype /Form /BBox [0 0 9 9]"
AT_100 = FORM + b" /Resources << /XObject << /X 100 0 R >> >>"
PLAIN = b"<< /XObject << /X 100 0 R >> >>"


def chain(subtype: bytes = b"/Form", name: bytes = b"/X", resources=None) -> dict[int, bytes]:
    """Six forms from object 100 on, each drawing the next twice by `name`."""
    objects = {}
    for level in range(6):
        last = level == 5
        content = b"0 0 m 1 1 l S" if last else b"%s Do %s Do" % (name, name)
        if last:
            held = b""
        elif resources is not None:
            held = resources(100 + level)
        else:
            held = b" /Resources << /XObject << %s %d 0 R >> >>" % (name, 101 + level)
        entries = b"/Type /XObject /Subtype %s /BBox [0 0 9 9]%s" % (subtype, held)
        objects[100 + level] = stream(content, entries)
    return objects


def page(content: bytes, resources: bytes, objects: dict[int, bytes], annots: bytes = b"") -> bytes:
    """A one-page file; object 3 is its content unless `objects` gives one."""
    body = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R"
    body += b" /Resources " + resources
    if annots:
        body += b" /Annots " + annots
    whole = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        3: stream(content),
        4: body + b" >>",
    }
    whole.update(objects)
    return build(whole, 1)


def counted(data: bytes) -> int | None:
    document = QpdfDocument.open(data, None)
    try:
        return document.parsed_streams(0)
    finally:
        document.close()


def lenient() -> dict[str, tuple[bytes, int]]:
    """Each file, and what PDFium parses for its page, by the construction."""
    per_form = {
        n + 500: stream(b"", b"/XObject << /X %d 0 R >>" % (n + 1)) for n in range(100, 105)
    }
    per_group = {n + 500: stream(b"", b"/X %d 0 R" % (n + 1)) for n in range(100, 105)}
    return {
        "/Subtype (Form), a string": (page(b"/X Do", PLAIN, chain(b"(Form)")), SIX),
        "the page's /Resources a stream": (
            page(b"/X Do", b"9 0 R", {9: stream(b"", b"/XObject << /X 100 0 R >>"), **chain()}),
            SIX,
        ),
        "each form's /Resources a stream": (
            page(
                b"/X Do",
                PLAIN,
                {**chain(resources=lambda n: b" /Resources %d 0 R" % (n + 500)), **per_form},
            ),
            SIX,
        ),
        "each /XObject group a stream": (
            page(
                b"/X Do",
                b"<< /XObject 9 0 R >>",
                {
                    9: stream(b"", b"/X 100 0 R"),
                    **chain(resources=lambda n: b" /Resources << /XObject %d 0 R >>" % (n + 500)),
                    **per_group,
                },
            ),
            SIX,
        ),
        # The appearance, and what it draws.
        "/AP a stream holding /N": (
            page(
                b"",
                b"<< >>",
                {
                    6: b"<< /Type /Annot /Subtype /Square /Rect [0 0 9 9] /AP 7 0 R >>",
                    7: stream(b"", b"/N 8 0 R"),
                    8: stream(b"/X Do", AT_100),
                    **chain(),
                },
                b"[6 0 R]",
            ),
            1 + SIX,
        ),
        # A font of type 3 whose /Subtype is a string, with its glyph.
        "/Subtype (Type3), a string": (
            page(
                b"BT /F 12 Tf (a) Tj ET",
                b"<< /Font << /F 10 0 R >> >>",
                {
                    10: b"<< /Type /Font /Subtype (Type3) /FontMatrix [0.001 0 0 0.001 0 0]"
                    b" /FontBBox [0 0 1000 1000] /FirstChar 97 /LastChar 97 /Widths [1000]"
                    b" /Encoding << /Differences [97 /a] >> /CharProcs << /a 12 0 R >>"
                    b" /Resources << /XObject << /X 100 0 R >> >> >>",
                    12: stream(b"1000 0 d0 /X Do"),
                    **chain(),
                },
            ),
            1 + SIX,
        ),
        # Upper bounds: PDFium takes no stream as an /ExtGState or a font.
        "an /ExtGState and its /SMask streams": (
            page(
                b"/G gs 0 0 m 1 1 l S",
                b"<< /ExtGState << /G 10 0 R >> >>",
                {
                    10: stream(b"", b"/SMask 11 0 R"),
                    11: stream(b"", b"/S /Luminosity /G 12 0 R"),
                    12: stream(b"/X Do", AT_100 + b" /Group << /S /Transparency >>"),
                    **chain(),
                },
            ),
            1 + SIX,
        ),
        "a font, its /Resources and /CharProcs streams": (
            page(
                b"BT /F 12 Tf (a) Tj ET",
                b"<< /Font << /F 10 0 R >> >>",
                {
                    10: stream(
                        b"",
                        b"/Type /Font /Subtype /Type3 /FontMatrix [0.001 0 0 0.001 0 0]"
                        b" /FontBBox [0 0 1000 1000] /FirstChar 97 /LastChar 97 /Widths [1000]"
                        b" /Encoding << /Differences [97 /a] >> /CharProcs 11 0 R"
                        b" /Resources 13 0 R",
                    ),
                    11: stream(b"", b"/a 12 0 R"),
                    12: stream(b"1000 0 d0 /X Do"),
                    13: stream(b"", b"/XObject << /X 100 0 R >>"),
                    **chain(),
                },
            ),
            1 + SIX,
        ),
    }


@pytest.mark.parametrize("name", list(lenient()))
def test_what_pdfium_reads_leniently_is_counted(name):
    data, expected = lenient()[name]
    assert counted(data) == expected
    with pdfacts.open(data, limits=Limits(max_forms_per_page=expected - 1)) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert raised.value.found == expected
    with pdfacts.open(data, limits=Limits(max_forms_per_page=expected)) as document:
        document.pages[0]


def misread() -> dict[str, bytes]:
    """Files whose objects QPDF's parser warns about, naming the object and its offset."""
    return {
        "names with #00": page(
            b"/X#00 Do", b"<< /XObject << /X#00 100 0 R >> >>", chain(name=b"/X#00")
        ),
        "an integer beyond 64 bits in each form": page(
            b"/X Do",
            PLAIN,
            {
                k: v.replace(b"/BBox", b"/Big 99999999999999999999999 /BBox")
                for k, v in chain().items()
            },
        ),
        "the page's content with a /Length too long": page(
            b"", PLAIN, {3: b"<< /Length 999 >>\nstream\n/X Do\nendstream", **chain()}
        ),
        "the page's content without /Length": page(
            b"", PLAIN, {3: b"<< >>\nstream\n/X Do\nendstream", **chain()}
        ),
        "a hex string with a character that is no hex digit": page(
            b"/X Do", PLAIN, {**chain(), 105: stream(b"0 0 m 1 1 l S", FORM + b" /A <zz>")}
        ),
    }


@pytest.mark.parametrize("name", list(misread()))
def test_a_page_whose_objects_qpdf_misreads_is_unchecked(name):
    data = misread()[name]
    assert counted(data) is None
    with pdfacts.open(data, limits=Limits(max_forms_per_page=1000)) as document:
        with pytest.raises(LimitUncheckedError) as raised:
            document.pages[0]
    assert "misread" in raised.value.reason.detail
    with pdfacts.open(data) as document:
        document.pages[0]


def test_a_warning_that_names_no_offset_leaves_the_count():
    """QPDF repairs the page tree for a stream as /Resources, and says so without an offset."""
    data, expected = lenient()["the page's /Resources a stream"]
    document = QpdfDocument.open(data, None)
    try:
        assert document.parsed_streams(0) == expected
        assert any("Resources is missing or invalid" in w for w in document.warnings())
    finally:
        document.close()


def test_a_misread_object_found_before_the_count_leaves_the_page_unchecked():
    """QPDF warns once, when it first reads the object; the count still sees the warning."""
    data = misread()["an integer beyond 64 bits in each form"]
    document = QpdfDocument.open(data, None)
    try:
        document._unwalked.get_object((100, 0))  # read, and warned about, before the count
        assert document.parsed_streams(0) is None
    finally:
        document.close()


def test_an_annots_entry_that_is_a_stream_is_no_annotation():
    # Measured: PDFium parses no appearance for it (issue G1b, "Facts").
    data = page(
        b"",
        b"<< >>",
        {
            6: stream(b"", b"/Type /Annot /Subtype /Square /Rect [0 0 9 9] /AP << /N 8 0 R >>"),
            8: stream(b"/X Do", AT_100),
            **chain(),
        },
        b"[6 0 R]",
    )
    assert counted(data) == 0


def test_a_form_whose_resources_are_no_dictionary_uses_those_of_what_draws_it():
    """Form B, /Resources 5, is drawn by form A, which holds /X; the page holds none."""
    data = page(
        b"/A Do",
        b"<< /XObject << /A 98 0 R >> >>",
        {
            98: stream(b"/B Do", FORM + b" /Resources << /XObject << /B 99 0 R /X 100 0 R >> >>"),
            99: stream(b"/X Do /X Do", FORM + b" /Resources 5"),
            **chain(),
        },
    )
    # A once, B once, and B draws the six forms twice.
    assert counted(data) == 1 + 1 + 2 * SIX


def test_resources_that_hold_no_usable_group_send_the_name_to_the_page():
    """Form B's /XObject is 5: /X is looked up in the page's resources, not in A's."""
    data = page(
        b"/A Do",
        b"<< /XObject << /A 98 0 R /X 100 0 R >> >>",
        {
            98: stream(b"/B Do", FORM + b" /Resources << /XObject << /B 99 0 R >> >>"),
            99: stream(b"/X Do /X Do", FORM + b" /Resources << /XObject 5 >>"),
            **chain(),
        },
    )
    assert counted(data) == 1 + 1 + 2 * SIX
