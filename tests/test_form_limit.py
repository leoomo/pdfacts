"""max_forms_per_page: what PDFium would parse for a page, counted by QPDF first.

Each file is built with the byte-level builder; the counts expected are what
the construction draws (AGENTS.md, rule 2), under the counting rules of
docs/conventions.md, "Limits".
"""

from __future__ import annotations

import warnings
import zlib

import pytest

import pdfacts
from pdfacts import LimitExceededError, Limits, LimitUncheckedError, UnknownReason
from pdfacts.backends import pdfium
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, build_with_object_stream, stream

JUNK_ACCEPTED = 1024


def form(content: bytes, resources: bytes = b"", extra: bytes = b"") -> bytes:
    entries = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100]"
    if resources:
        entries += b" /Resources " + resources
    return stream(content, entries + extra)


def page_file(
    content: bytes,
    resources: bytes = b"<< >>",
    objects: dict[int, bytes] | None = None,
    *,
    annots: bytes = b"",
    pages_resources: bytes = b"",
) -> bytes:
    """A one-page file; `resources` on the page, or on /Pages when `pages_resources` is given."""
    pages = b"<< /Type /Pages /Kids [4 0 R] /Count 1 %s>>" % (
        b"/Resources " + pages_resources + b" " if pages_resources else b""
    )
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] %s/Contents 5 0 R %s>>" % (
        b"" if pages_resources else b"/Resources " + resources + b" ",
        b"/Annots " + annots + b" " if annots else b"",
    )
    whole = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: pages, 4: page, 5: stream(content)}
    whole.update(objects or {})
    return build(whole, 1)


def chain(first: int, count: int, twice: bool) -> dict[int, bytes]:
    """`count` forms from object `first` on, each drawing the next as /Fx (twice or once)."""
    out = {}
    for i in range(count):
        if i == count - 1:
            out[first + i] = form(b"0 0 m 10 0 l S")
        else:
            draws = b" /Fx Do /Fx Do" if twice else b" /Fx Do"
            out[first + i] = form(
                b"0 0 m 10 0 l S" + draws, b"<< /XObject << /Fx %d 0 R >> >>" % (first + i + 1)
            )
    return out


FX = b"<< /XObject << /Fx 10 0 R >> >>"
LINE = form(b"0 0 m 10 0 l S")
TYPE3 = (
    b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 100 100] /FontMatrix [0.01 0 0 0.01 0 0]"
    b" /CharProcs << /a 62 0 R /b 66 0 R >> /Encoding << /Differences [97 /a /b] >>"
    b" /FirstChar 97 /LastChar 98 /Widths [100 100] %s>>"
)
CELL = b"/BBox [0 0 100 100] /XStep 100 /YStep 100 /Resources << /XObject << /Fx 10 0 R >> >>"
PATTERN = b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 " + CELL
UNCOLOURED = b"/Type /Pattern /PatternType 1 /PaintType 2 /TilingType 1 " + CELL
GROUP = (
    b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Group << /S /Transparency"
    b" /CS /DeviceGray >> /Resources << /XObject << /Fx 10 0 R >> >>"
)
IMAGE = (
    b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8"
)
SQUARE = b"<< /Type /Annot /Subtype /Square %s/Rect [0 0 100 100] /AP << /N 10 0 R >> >>"
SOFT_MASK = b"<< /ExtGState << /G1 << /SMask << /S /Luminosity /G 64 0 R >> >> >> >>"
TYPE3_RESOURCES = b"/Resources << /XObject << /Fx 10 0 R >> >> "


# --- 1: each way PDFium parses more, refused before PDFium loads the page -------------


def bombs() -> dict[str, tuple[bytes, int]]:
    """Name: (file, count). 2^18 - 1 forms for the chain of 18; 2^41 - 1 for a form
    drawing itself twice, drawn at depth 1 and parsed down to depth 40."""
    chain18 = chain(10, 18, twice=True)
    return {
        "a chain of 18 forms, each drawing the next twice": (
            page_file(b"/Fx Do", FX, chain18),
            2**18 - 1,
        ),
        "a form drawing itself twice": (
            page_file(b"/Fx Do", FX, {10: form(b"/Fx Do /Fx Do", FX)}),
            2**41 - 1,
        ),
        "a form without /Resources drawing itself twice through the page's": (
            page_file(b"/Fx Do", FX, {10: form(b"/Fx Do /Fx Do")}),
            2**41 - 1,
        ),
        "the chain in an annotation's appearance": (
            page_file(b"", b"<< >>", {**chain18, 60: SQUARE % b""}, annots=b"[60 0 R]"),
            2**18 - 1,
        ),
        "the chain in a hidden annotation's appearance": (
            page_file(b"", b"<< >>", {**chain18, 60: SQUARE % b"/F 2 "}, annots=b"[60 0 R]"),
            2**18 - 1,
        ),
        "the chain in a tiling pattern that is used": (
            page_file(
                b"/Pattern cs /P1 scn 0 0 100 100 re f",
                b"<< /Pattern << /P1 61 0 R >> >>",
                {**chain18, 61: stream(b"/Fx Do", PATTERN)},
            ),
            1 + 2**18 - 1,
        ),
        "the chain in a soft mask that is used": (
            page_file(
                b"/G1 gs 0 0 100 100 re f",
                SOFT_MASK,
                {**chain18, 64: stream(b"/Fx Do", GROUP)},
            ),
            1 + 2**18 - 1,
        ),
        "the chain in a Type 3 glyph of a font the page selects": (
            page_file(
                b"BT /T3 12 Tf 10 10 Td (a) Tj ET",
                b"<< /Font << /T3 63 0 R >> >>",
                {
                    **chain18,
                    62: stream(b"100 0 0 0 100 100 d1 /Fx Do"),
                    66: stream(b"100 0 0 0 100 100 d1"),
                    63: TYPE3 % TYPE3_RESOURCES,
                },
            ),
            (1 + 2**18 - 1) + 1,
        ),
    }


@pytest.mark.parametrize("name", list(bombs()))
def test_a_page_that_makes_pdfium_parse_too_much_is_refused_before_pdfium_loads_it(
    monkeypatch, name
):
    data, count = bombs()[name]

    def must_not_run(*args, **kwargs):
        raise AssertionError("PDFium loaded the page")

    monkeypatch.setattr(pdfium.PdfiumDocument, "_read_page", must_not_run)
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10_000)) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_forms_per_page",
        10_000,
        count,
    )


# --- 2: the counting rules, pinned where the limit is the count and one less -------


def exact() -> dict[str, tuple[bytes, int]]:
    return {
        "one form drawn three times": (page_file(b"/Fx Do /Fx Do /Fx Do", FX, {10: LINE}), 3),
        "an image and an unknown name are not forms": (
            page_file(
                b"/Im Do /Nope Do /Fx Do /Fx Do",
                b"<< /XObject << /Fx 10 0 R /Im 11 0 R >> >>",
                {10: LINE, 11: stream(b"\x00", IMAGE)},
            ),
            2,
        ),
        "a chain of 45 forms, each drawing the next once: drawn to depth 41, parsed to 40": (
            page_file(b"/Fx Do", FX, chain(10, 45, twice=False)),
            41,
        ),
        "a form without /Resources takes those of the form that draws it": (
            # The page's /X draws two more forms (3); A's /X draws none (1).
            page_file(
                b"/A Do",
                b"<< /XObject << /A 10 0 R /X 20 0 R >> >>",
                {
                    10: form(b"/B Do", b"<< /XObject << /B 11 0 R /X 21 0 R >> >>"),
                    11: form(b"/X Do"),
                    20: form(b"/Y Do /Y Do", b"<< /XObject << /Y 22 0 R >> >>"),
                    21: LINE,
                    22: LINE,
                },
            ),
            3,
        ),
        "resources inherited from /Pages": (
            page_file(
                b"/Fx Do",
                pages_resources=FX,
                objects={10: form(b"/Gx Do", b"<< /XObject << /Gx 11 0 R >> >>"), 11: LINE},
            ),
            2,
        ),
        "a tiling pattern set twice and filled three times, one named and never used": (
            page_file(
                b"/Pattern cs /P1 scn 0 0 100 100 re f 0 100 100 100 re f /P1 scn 0 200 10 10 re f",
                b"<< /Pattern << /P1 61 0 R /P2 67 0 R >> >>",
                {10: LINE, 61: stream(b"/Fx Do", PATTERN), 67: stream(b"/Fx Do", PATTERN)},
            ),
            1 + 1,
        ),
        "an uncoloured pattern: its name is the last operand": (
            page_file(
                b"/CS1 cs 0.5 /P1 scn 0 0 100 100 re f",
                b"<< /ColorSpace << /CS1 [/Pattern /DeviceGray] >> /Pattern << /P1 61 0 R >> >>",
                {10: LINE, 61: stream(b"/Fx Do", UNCOLOURED)},
            ),
            1 + 1,
        ),
        "a soft mask set twice": (
            page_file(
                b"/G1 gs 0 0 100 100 re f /G1 gs",
                SOFT_MASK,
                {10: LINE, 64: stream(b"/Fx Do", GROUP)},
            ),
            1 + 1,
        ),
        "a Type 3 font selected twice: its two glyphs once": (
            page_file(
                b"BT /T3 12 Tf (a) Tj /T3 10 Tf (ab) Tj ET",
                b"<< /Font << /T3 63 0 R >> >>",
                {
                    10: LINE,
                    62: stream(b"100 0 0 0 100 100 d1 /Fx Do"),
                    66: stream(b"100 0 0 0 100 100 d1 /Fx Do"),
                    63: TYPE3 % TYPE3_RESOURCES,
                },
            ),
            2 * (1 + 1),
        ),
        "two annotations sharing one appearance, one hidden: each counts": (
            page_file(
                b"",
                b"<< >>",
                {
                    10: form(b"/Fx Do", b"<< /XObject << /Fx 11 0 R >> >>"),
                    11: LINE,
                    60: SQUARE % b"",
                    65: SQUARE % b"",
                    69: SQUARE % b"/F 2 ",
                },
                annots=b"[60 0 R 65 0 R 69 0 R]",
            ),
            3 * (1 + 1),
        ),
        "every state of /N counts, whichever PDFium selects": (
            # /AS names a state, names none, or is absent (PDFium then takes
            # the field's /V or /Off); each dictionary has two states.
            page_file(
                b"",
                b"<< >>",
                {
                    10: form(b"/Fx Do", b"<< /XObject << /Fx 11 0 R >> >>"),
                    11: LINE,
                    60: WIDGET % b"/AS /On ",
                    65: WIDGET % b"/AS /Yes ",
                    69: WIDGET % b"",
                },
                annots=b"[60 0 R 65 0 R 69 0 R]",
            ),
            3 * 2 * (1 + 1),
        ),
        "a stroking pattern": (
            page_file(
                b"/Pattern CS /P1 SCN 0 0 m 100 0 l S",
                b"<< /Pattern << /P1 61 0 R >> >>",
                {10: LINE, 61: stream(b"/Fx Do", PATTERN)},
            ),
            1 + 1,
        ),
        "a page without /Type, its /Contents two streams": (
            build(
                {
                    1: b"<< /Type /Catalog /Pages 2 0 R >>",
                    2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
                    4: b"<< /Parent 2 0 R /MediaBox [0 0 400 300] /Resources "
                    + FX
                    + b" /Contents [5 0 R 6 0 R] >>",
                    # The name ends one stream, the operator begins the next.
                    5: stream(b"/Fx Do /Fx"),
                    6: stream(b"Do"),
                    10: LINE,
                },
                1,
            ),
            2,
        ),
        "a form without /Resources, drawn by two forms with other resources: A then B": (
            page_file(b"/A Do /B Do", AB, TWO_CONTEXTS),
            TWO_CONTEXTS_COUNT,
        ),
        "the same, B then A": (page_file(b"/B Do /A Do", AB, TWO_CONTEXTS), TWO_CONTEXTS_COUNT),
        "an appearance starts at depth 0: a chain of 45, each drawing the next once": (
            page_file(
                b"", b"<< >>", {**chain(10, 45, twice=False), 60: SQUARE % b""}, annots=b"[60 0 R]"
            ),
            # The appearance, forms at depths 1 to 40, one at 41 not parsed.
            1 + 40 + 1,
        ),
        "a tiling pattern starts at depth 0, used at depth 39": (
            page_file(
                b"/Fx Do",
                FX,
                at_depth_39(
                    b"/Pattern cs /P1 scn 0 0 10 10 re f",
                    {b"/Pattern << /P1 61 0 R >>": {61: stream(b"/Z1 Do", PATTERN_Z)}},
                ),
            ),
            39 + CHAIN_45,
        ),
        "a soft mask starts at depth 0, used at depth 39": (
            page_file(
                b"/Fx Do",
                FX,
                at_depth_39(
                    b"/G1 gs",
                    {
                        b"/ExtGState << /G1 << /SMask << /S /Luminosity /G 64 0 R >> >> >>": {
                            64: stream(b"/Z1 Do", GROUP_Z)
                        }
                    },
                ),
            ),
            39 + CHAIN_45,
        ),
        "a Type 3 glyph starts at depth 0, its font selected at depth 39": (
            page_file(
                b"/Fx Do",
                FX,
                at_depth_39(
                    b"BT /T3 12 Tf (a) Tj ET",
                    {
                        b"/Font << /T3 63 0 R >>": {
                            62: stream(b"100 0 0 0 100 100 d1 /Z1 Do"),
                            66: stream(b"100 0 0 0 100 100 d1"),
                            63: TYPE3 % b"/Resources << /XObject << /Z1 80 0 R >> >> ",
                        }
                    },
                ),
            ),
            39 + CHAIN_45 + 1,
        ),
    }


WIDGET = (
    b"<< /Type /Annot /Subtype /Widget /FT /Btn /T (c) /Rect [0 0 10 10] %s"
    b"/AP << /N << /On 10 0 R /M#F6 10 0 R >> >> >>"
)
# A and B each draw C, which has no /Resources and draws /X: A's /X draws two
# lines (3), B's /X is a line (1). A = 1 + (1 + 3), B = 1 + (1 + 1).
AB = b"<< /XObject << /A 20 0 R /B 21 0 R >> >>"
TWO_CONTEXTS = {
    20: form(b"/C Do", b"<< /XObject << /C 22 0 R /X 23 0 R >> >>"),
    21: form(b"/C Do", b"<< /XObject << /C 22 0 R /X 24 0 R >> >>"),
    22: form(b"/X Do"),
    23: form(b"/Y Do /Y Do", b"<< /XObject << /Y 25 0 R >> >>"),
    24: LINE,
    25: LINE,
}
TWO_CONTEXTS_COUNT = (1 + (1 + 3)) + (1 + (1 + 1))
# Z1 (80) heads a chain of 45 forms, each drawing the next once. A stream at
# depth 0 that draws Z1: itself, forms at depths 1 to 40, one at 41 not parsed.
CHAIN_45 = 1 + 40 + 1
PATTERN_Z = (
    b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 100 100]"
    b" /XStep 100 /YStep 100 /Resources << /XObject << /Z1 80 0 R >> >>"
)
GROUP_Z = (
    b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Group << /S /Transparency"
    b" /CS /DeviceGray >> /Resources << /XObject << /Z1 80 0 R >> >>"
)


def at_depth_39(content: bytes, uses: dict[bytes, dict[int, bytes]]) -> dict[int, bytes]:
    """Forms 10 to 48, each drawing the next once as /Fx; 48, at depth 39, runs `content`
    with the resources in `uses`; Z1 (80) heads a chain of 45."""
    objects = chain(10, 39, twice=False)
    ((entry, extra),) = uses.items()
    objects[48] = form(content, b"<< " + entry + b" >>")
    objects.update(extra)
    objects.update(chain(80, 45, twice=False))
    return objects


@pytest.mark.parametrize("name", list(exact()))
def test_the_count_follows_what_pdfium_parses(name):
    data, count = exact()[name]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=count)) as document:
        document.pages[0]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=count - 1)) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert raised.value.found == count


# --- 3 to 5: when it is counted -----------------------------------------------------


def test_without_the_limit_nothing_is_counted(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("counted")

    monkeypatch.setattr(QpdfDocument, "parsed_streams", must_not_run)
    data, _ = exact()["one form drawn three times"]
    with pdfacts.open(data) as document:
        document.pages[0].render(0.25)
        pdfacts.widgets(document, 0)


def test_each_page_is_counted_once(monkeypatch):
    calls = []
    real = QpdfDocument.parsed_streams

    def counting(self, index):
        calls.append(index)
        return real(self, index)

    monkeypatch.setattr(QpdfDocument, "parsed_streams", counting)
    data, _ = exact()["one form drawn three times"]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=3)) as document:
        document.pages[0].render(0.25)
        document.pages[0].render(0.5)
        pdfacts.widgets(document, 0)
    assert calls == [0]


def test_rendering_and_annotation_reads_are_guarded_too():
    data, _ = bombs()["a chain of 18 forms, each drawing the next twice"]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10_000)) as document:
        with pytest.raises(LimitExceededError):
            document._render(0, scale=0.25, crop=(0, 0, 0, 0), rgb=False)
        with document._access() as access:
            with pytest.raises(LimitExceededError):
                access.pdfium_annotation_count(0)
            with pytest.raises(LimitExceededError):
                access.annotation_views(0)


# --- 6 to 10: when QPDF cannot count ---------------------------------------------------


def test_a_file_qpdf_cannot_open_is_refused_with_the_limit_and_opens_without():
    data, _ = exact()["one form drawn three times"]
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(b"x" * JUNK_ACCEPTED + data, limits=Limits(max_forms_per_page=10))
    assert raised.value.limit == "max_forms_per_page"
    assert raised.value.reason.reason is UnknownReason.UNREADABLE
    with pdfacts.open(b"x" * JUNK_ACCEPTED + data) as document:
        document.pages[0]


def test_object_streams_that_fail_the_check_are_refused_with_the_limit():
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /UserUnit 9 0 R >>"
    )
    data = build_with_object_stream(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: page,
        },
        [(9, b"2"), (9, b"3")],
        {9: 0},
        1,
    )
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(data, limits=Limits(max_forms_per_page=10))
    assert raised.value.reason.reason is UnknownReason.INVALID


def test_a_page_tree_qpdf_changed_leaves_the_page_unchecked():
    kid = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 9 0 R >>"
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R %s] /Count 2 >>" % kid,
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 9 0 R >>",
            9: stream(b""),
        },
        1,
    )
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10)) as document:
        with pytest.raises(LimitUncheckedError) as raised:
            document.pages[0]
    assert raised.value.reason.reason is UnknownReason.UNREADABLE


@pytest.mark.parametrize(
    "content",
    [b"[/Fx Do] /Fx Do", b"<< /A /Fx Do >> /Fx Do"],
    ids=["an operator in an array", "an operator in a dictionary"],
)
def test_content_qpdf_cannot_read_leaves_the_page_unchecked(content):
    data = page_file(content, FX, {10: LINE})
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10)) as document:
        with pytest.raises(LimitUncheckedError) as raised:
            document.pages[0]
    assert raised.value.reason.reason is UnknownReason.UNREADABLE
    with pdfacts.open(data) as document:
        document.pages[0]


@pytest.mark.parametrize(
    "content",
    [b") /Fx Do", b"/Other /Fx Do", b"] /Fx Do", b"(a\\) /Fx Do) /Fx Do"],
    ids=["a stray )", "two names", "a stray ]", "an escaped paren in a string"],
)
def test_the_name_is_taken_next_to_the_operator(content):
    # Fx draws a form; Other is an image. PDFium draws the form once in each case.
    data = page_file(
        content,
        b"<< /XObject << /Fx 10 0 R /Other 11 0 R >> >>",
        {10: LINE, 11: stream(b"\x00", IMAGE)},
    )
    with pdfacts.open(data, limits=Limits(max_forms_per_page=1)) as document:
        document.pages[0]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=1)) as document:
        assert document._qpdf.parsed_streams(0) == 1


def contents_file(contents: bytes) -> bytes:
    """A page whose /Contents is the stream object written as `contents`."""
    return build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources "
            + FX
            + b" /Contents 5 0 R >>",
            5: contents,
            10: LINE,
        },
        1,
    )


def test_a_filter_qpdf_does_not_know_leaves_the_page_unchecked():
    data = contents_file(b"<< /Length 6 /Filter /NoSuchFilter >>\nstream\n/Fx Do\nendstream")
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10)) as document:
        with pytest.raises(LimitUncheckedError):
            document.pages[0]


def test_content_cut_short_is_counted_as_far_as_it_decodes_and_qpdf_says_so():
    raw = b"/Fx Do " * 50 + b" " * 2000
    packed = zlib.compress(raw)
    cut = packed[: len(packed) // 2]
    data = contents_file(
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(cut) + cut + b"\nendstream"
    )
    with pdfacts.open(data, limits=Limits(max_forms_per_page=50)) as document:
        document.pages[0]
        assert any("input stream is complete" in warning for warning in document.warnings)
    with pdfacts.open(data, limits=Limits(max_forms_per_page=49)) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert raised.value.found == 50
    with pdfacts.open(data) as document:
        document.pages[0]
        assert not any("input stream is complete" in warning for warning in document.warnings)


def test_engines_that_count_different_pages_are_refused_with_the_limit(monkeypatch):
    original = QpdfDocument.page_count
    monkeypatch.setattr(QpdfDocument, "page_count", lambda self: original(self) + 1)
    data, _ = exact()["one form drawn three times"]
    with pytest.raises(LimitUncheckedError) as raised:
        pdfacts.open(data, limits=Limits(max_forms_per_page=10))
    assert raised.value.reason.reason is UnknownReason.ENGINES_DISAGREE


def test_content_that_ends_in_a_string_is_counted_as_pdfium_reads_it():
    """pikepdf warns of it; where warnings are errors, the page cannot be counted."""
    data = page_file(b"/Fx Do (abc /Fx Do", FX, {10: LINE})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pdfacts.open(data, limits=Limits(max_forms_per_page=1)) as document:
            document.pages[0]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pdfacts.open(data, limits=Limits(max_forms_per_page=1)) as document:
            with pytest.raises(LimitUncheckedError):
                document.pages[0]


def test_the_deadline_is_checked_after_the_count(monkeypatch):
    now = [0.0]
    real = QpdfDocument.parsed_streams

    def slow(self, index):
        now[0] = 6.0
        return real(self, index)

    monkeypatch.setattr(QpdfDocument, "parsed_streams", slow)
    data, _ = exact()["one form drawn three times"]
    with pdfacts.open(
        data, limits=Limits(max_forms_per_page=3, deadline_seconds=5), _clock=lambda: now[0]
    ) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert raised.value.limit == "deadline_seconds"


def lookups() -> dict[str, tuple[bytes, int]]:
    """Where PDFium finds a name. The page names /X as form 31, which draws two
    lines (3); each other /X is a line (1)."""
    x3 = {31: form(b"/Y Do /Y Do", b"<< /XObject << /Y 32 0 R >> >>"), 32: LINE, 33: LINE}
    return {
        "a form whose /Resources lack /XObject finds /X in the page's": (
            page_file(
                b"/A Do",
                b"<< /XObject << /A 20 0 R /X 31 0 R >> >>",
                {**x3, 20: form(b"/X Do", b"<< /Font << >> >>")},
            ),
            1 + 3,
        ),
        "an appearance whose /Resources lack /XObject finds /X in the page's": (
            page_file(
                b"",
                b"<< /XObject << /X 31 0 R >> >>",
                {**x3, 20: form(b"/X Do", b"<< /Font << >> >>"), 60: SQUARE_20},
                annots=b"[60 0 R]",
            ),
            1 + 3,
        ),
        "a soft mask group without /Resources uses the page's, not the form's": (
            page_file(
                b"/A Do",
                b"<< /XObject << /A 20 0 R /X 31 0 R >> >>",
                {
                    **x3,
                    20: form(
                        b"/G1 gs 0 0 10 10 re f",
                        b"<< /XObject << /X 33 0 R >> /ExtGState << /G1 << /SMask << /S"
                        b" /Luminosity /G 64 0 R >> >> >> >>",
                    ),
                    64: form(b"/X Do", b"", GROUP_ENTRIES),
                },
            ),
            1 + (1 + 3),
        ),
        "a tiling pattern without /Resources finds nothing": (
            page_file(
                b"/Pattern cs /P1 scn 0 0 10 10 re f",
                b"<< /XObject << /X 31 0 R >> /Pattern << /P1 61 0 R >> >>",
                {**x3, 61: stream(b"/X Do", PATTERN_BARE)},
            ),
            1,
        ),
        "a Type 3 font without /Resources, selected in two forms: counted for each": (
            # A's /X is a line, B's draws two lines; each glyph draws /X.
            page_file(
                b"/A Do /B Do",
                b"<< /XObject << /A 20 0 R /B 21 0 R >> /Font << /T3 63 0 R >> >>",
                {
                    **x3,
                    20: form(
                        b"BT /T3 12 Tf (a) Tj ET",
                        b"<< /XObject << /X 33 0 R >> /Font << /T3 63 0 R >> >>",
                    ),
                    21: form(
                        b"BT /T3 12 Tf (a) Tj ET",
                        b"<< /XObject << /X 31 0 R >> /Font << /T3 63 0 R >> >>",
                    ),
                    62: stream(b"100 0 0 0 100 100 d1 /X Do"),
                    66: stream(b"100 0 0 0 100 100 d1"),
                    63: TYPE3 % b"",
                },
            ),
            (1 + (1 + 1) + 1) + (1 + (1 + 3) + 1),
        ),
        "two Type 3 fonts written directly, sharing a glyph, each with its own /Resources": (
            page_file(
                b"BT /T1 12 Tf (a) Tj /T2 12 Tf (a) Tj ET",
                b"<< /Font << /T1 "
                + DIRECT_TYPE3 % b"33"
                + b" /T2 "
                + DIRECT_TYPE3 % b"31"
                + b" >> >>",
                {**x3, 62: stream(b"100 0 0 0 100 100 d1 /X Do")},
            ),
            (1 + 1) + (1 + 3),
        ),
    }


SQUARE_20 = b"<< /Type /Annot /Subtype /Square /Rect [0 0 100 100] /AP << /N 20 0 R >> >>"
GROUP_ENTRIES = b" /Group << /S /Transparency /CS /DeviceGray >>"
PATTERN_BARE = (
    b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 100 100]"
    b" /XStep 100 /YStep 100"
)
DIRECT_TYPE3 = (
    b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 100 100] /FontMatrix [0.01 0 0 0.01 0 0]"
    b" /CharProcs << /a 62 0 R >> /Encoding << /Differences [97 /a] >> /FirstChar 97"
    b" /LastChar 97 /Widths [100] /Resources << /XObject << /X %s 0 R >> >> >>"
)


@pytest.mark.parametrize("name", list(lookups()))
def test_names_are_looked_up_as_pdfium_looks_them_up(name):
    data, count = lookups()[name]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=count)) as document:
        document.pages[0]
    if count > 1:
        with pdfacts.open(data, limits=Limits(max_forms_per_page=count - 1)) as document:
            with pytest.raises(LimitExceededError) as raised:
                document.pages[0]
        assert raised.value.found == count


# --- 11: the count follows PDFium's lenient reading, and says what it cannot count ----
#
# Six constructions made the G1 counter read nothing where PDFium parsed an
# expanding chain (measured 2026-10-01): a string /Subtype, a stream where
# PDFium reads a dictionary, a name that breaks QPDF's parser, an integer
# beyond 64 bits, a wrong /Length, and object-stream entries whose bodies are
# stream objects. The counter now reads as PDFium reads, and a page whose
# objects the two engines read differently is refused with LimitUncheckedError.


def counted(data: bytes, count: int) -> None:
    """The count is `count`: it opens at the count and refuses one below it."""
    with pdfacts.open(data, limits=Limits(max_forms_per_page=count)) as document:
        document.pages[0]
    with pdfacts.open(data, limits=Limits(max_forms_per_page=count - 1)) as document:
        with pytest.raises(LimitExceededError) as raised:
            document.pages[0]
    assert raised.value.found == count


def unchecked(data: bytes) -> None:
    with pdfacts.open(data, limits=Limits(max_forms_per_page=10)) as document:
        with pytest.raises(LimitUncheckedError) as raised:
            document.pages[0]
    assert raised.value.reason.reason is UnknownReason.UNREADABLE


def test_a_form_whose_subtype_is_a_string_counts():
    data = page_file(
        b"/Fx Do /Fx Do",
        b"<< /XObject << /Fx 10 0 R /Fx2 11 0 R >> >>",
        {
            10: stream(b"/Fx2 Do /Fx2 Do", b"/Type /XObject /Subtype (Form) /BBox [0 0 9 9]"),
            **chain(11, 5, twice=True),
        },
    )
    counted(data, 2 * (1 + 2 * (2**5 - 1)))


def test_a_form_s_resources_as_a_stream_are_read_as_a_dictionary():
    data = page_file(
        b"/Fx Do",
        b"<< /XObject << /Fx 10 0 R >> >>",
        {
            10: stream(
                b"/Fx Do /Fx Do", b"/Type /XObject /Subtype /Form /BBox [0 0 9 9] /Resources 30 0 R"
            ),
            30: stream(b"", b"/XObject << /Fx 11 0 R >>"),
            **chain(11, 5, twice=True),
        },
    )
    counted(data, 1 + 2 * (2**5 - 1))


def test_an_xobject_group_as_a_stream_is_read_as_a_dictionary():
    data = page_file(
        b"/Fx Do",
        b"<< /XObject 31 0 R >>",
        {31: stream(b"", b"/Fx 10 0 R"), **chain(10, 6, twice=True)},
    )
    counted(data, 2**6 - 1)


def test_the_page_s_resources_as_a_stream_are_read_as_a_dictionary():
    data = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 5 0 R"
            b" /Resources 31 0 R >>",
            5: stream(b"/Fx Do"),
            31: stream(b"", b"/XObject << /Fx 10 0 R >>"),
            **chain(10, 6, twice=True),
        },
        1,
    )
    counted(data, 2**6 - 1)


def test_a_name_that_breaks_qpdf_s_parser_leaves_the_page_unchecked():
    unchecked(
        page_file(b"/Fx#00 Do", b"<< /XObject << /Fx#00 10 0 R >> >>", chain(10, 6, twice=True))
    )


def test_an_integer_beyond_64_bits_leaves_the_page_unchecked():
    unchecked(
        page_file(
            b"/Fx Do",
            b"<< /XObject << /Fx 10 0 R >> >>",
            {
                10: stream(
                    b"/Fx Do /Fx Do",
                    b"/Type /XObject /Subtype /Form /BBox [0 0 9 9]"
                    b" /Big 99999999999999999999999999999999999"
                    b" /Resources << /XObject << /Fx 11 0 R >> >>",
                ),
                **chain(11, 5, twice=True),
            },
        )
    )


def test_a_wrong_length_on_the_content_leaves_the_page_unchecked():
    unchecked(
        build(
            {
                1: b"<< /Type /Catalog /Pages 2 0 R >>",
                2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
                4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 5 0 R"
                b" /Resources << /XObject << /Fx 10 0 R >> >> >>",
                5: b"<< /Length 2 >>\nstream\n0 0\nstream\n/Fx Do /Fx Do\nendstream",
                **chain(10, 6, twice=True),
            },
            1,
        )
    )


def _object_stream_chain(
    page_content: bytes,
    page_resources: bytes,
    extra: dict[int, bytes],
    first_entries: bytes,
    n: int,
    *,
    annots: bytes = b"",
) -> bytes:
    """A file whose xref says objects 10..10+n-1 are compressed in an object
    stream whose entries hold what look like stream-object bodies; PDFium
    parses them as streams (measured, 2026-10-01), QPDF reads dictionaries."""
    bodies = b""
    heads = []
    for i in range(n):
        heads.append((10 + i, len(bodies)))
        if i == n - 1:
            line = b"0 0 m 1 1 l S\n"
            bodies += (
                b"<< /Length %d /Type /XObject /Subtype /Form /BBox [0 0 9 9] >>\nstream\n%s"
                b" endstream\n" % (len(line), line)
            )
        else:
            content = b"/Fx Do /Fx Do\n"
            entries = (
                first_entries
                if i == 0
                else b"/Type /XObject /Subtype /Form /BBox [0 0 9 9]"
                b" /Resources << /XObject << /Fx %d 0 R >> >>" % (11 + i)
            )
            bodies += b"<< /Length %d %s >>\nstream\n%s endstream\n" % (
                len(content),
                entries,
                content,
            )
    head = b" ".join(b"%d %d" % (number, offset) for number, offset in heads) + b" "
    data = head + bodies
    out = bytearray(b"%PDF-1.7\n")
    where = {}
    physical = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        3: stream(page_content),
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 3 0 R /Resources "
        + page_resources
        + (b" /Annots " + annots + b" " if annots else b"")
        + b" >>",
        **extra,
    }
    for number in sorted(physical):
        where[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, physical[number])
    where[8] = len(out)
    out += (
        b"8 0 obj\n<< /Type /ObjStm /N %d /First %d /Length %d >>\nstream\n%sendstream\nendobj\n"
        % (len(heads), len(head), len(data), data)
    )
    import struct as _struct

    size = max([*where, 9, *range(10, 10 + n)]) + 1
    at = len(out)
    rows = b""
    for number in range(size):
        if number in where:
            rows += _struct.pack(">BIH", 1, where[number], 0)
        elif number == 9:
            rows += _struct.pack(">BIH", 1, at, 0)
        elif 10 <= number < 10 + n:
            rows += _struct.pack(">BIH", 2, 8, number - 10)
        else:
            rows += _struct.pack(">BIH", 0, 0, 65535 if number == 0 else 0)
    out += b"9 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R /Length %d >>\nstream\n" % (
        size,
        len(rows),
    )
    out += rows + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % at
    return bytes(out)


OBJSTM_FORM = (
    b"/Type /XObject /Subtype /Form /BBox [0 0 9 9] /Resources << /XObject << /Fx 11 0 R >> >>"
)


def test_a_form_chain_in_an_object_stream_leaves_the_page_unchecked():
    unchecked(
        _object_stream_chain(
            b"/Fx Do /Fx Do\n", b"<< /XObject << /Fx 10 0 R >> >>", {}, OBJSTM_FORM, 6
        )
    )


def test_a_pattern_chain_in_an_object_stream_leaves_the_page_unchecked():
    unchecked(
        _object_stream_chain(
            b"/Pattern cs /P1 scn 0 0 9 9 re f\n",
            b"<< /Pattern << /P1 10 0 R >> >>",
            {},
            b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 9 9]"
            b" /XStep 9 /YStep 9 /Resources << /XObject << /Fx 11 0 R >> >>",
            6,
        )
    )


def test_an_appearance_written_into_an_object_stream_leaves_the_page_unchecked():
    unchecked(
        _object_stream_chain(
            b"",
            b"<< >>",
            {60: b"<< /Type /Annot /Subtype /Square /Rect [0 0 100 100] /AP << /N 10 0 R >> >>"},
            b"/Type /XObject /Subtype /Form /BBox [0 0 9 9]",
            1,
            annots=b"[60 0 R]",
        )
    )


def test_an_ap_written_as_a_stream_is_read_by_its_dictionary():
    # Measured: PDFium takes the stream's dictionary at /AP and parses the
    # appearance it names (2026-10-01; the review of #62 measured the same).
    data = page_file(
        b"",
        b"<< >>",
        {
            10: form(b"/Fx Do", b"<< /XObject << /Fx 11 0 R >> >>"),
            11: LINE,
            60: b"<< /Type /Annot /Subtype /Square /Rect [0 0 100 100] /AP 33 0 R >>",
            33: stream(b"", b"/N 10 0 R"),
        },
        annots=b"[60 0 R]",
    )
    counted(data, 2)
