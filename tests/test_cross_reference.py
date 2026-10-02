"""`max_objects` counts the objects QPDF's cross-reference holds, which QPDF
reads whatever the trailer's /Size says; and a page's count for
`max_forms_per_page` depends on what QPDF has read before it
(docs/conventions.md, "Limits").

Files are built with the byte-level builder; the values expected follow from
what is written (AGENTS.md, rule 2), and from what QPDF was measured to do
where a test says so.
"""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts.backends.protocols import EngineFailure, FailureKind
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, build_with_object_stream, stream

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> >>"


def many_objects(size: bytes) -> bytes:
    """One page and 36 arrays, objects 1 to 39; the trailer's /Size, 40 as
    built, written as `size`, as many bytes."""
    objects = {1: CATALOG, 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", 3: PAGE}
    objects.update({number: b"[%d]" % number for number in range(4, 40)})
    data = build(objects, 1)
    assert data.count(b"/Size 40") == 1 and len(size) == 2
    return data.replace(b"/Size 40", b"/Size " + size)


@pytest.mark.parametrize(
    ("size", "limit", "found"),
    [
        (b"40", 39, 40),
        (b"40", 40, None),
        (b"4 ", 38, 39),
        (b"4 ", 39, None),
    ],
    ids=[
        "/Size 40, 39 allowed",
        "/Size 40, 40 allowed",
        "/Size 4, 38 allowed",
        "/Size 4, 39 allowed",
    ],
)
def test_max_objects_counts_the_cross_reference(size, limit, found):
    """39 objects: the larger of /Size and 39 is checked."""
    limits = pdfacts.Limits(max_objects=limit)
    if found is None:
        with pdfacts.open(many_objects(size), limits=limits) as document:
            assert document.object_count == int(size)
        return
    with pytest.raises(pdfacts.LimitExceededError) as raised:
        pdfacts.open(many_objects(size), limits=limits)
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "max_objects",
        limit,
        found,
    )


@pytest.mark.parametrize(
    ("data", "count"),
    [
        (lambda: many_objects(b"40"), 39),
        (lambda: many_objects(b"4 "), 39),
        (
            lambda: build_with_object_stream(
                {1: CATALOG, 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", 3: PAGE},
                [(4, b"[1]"), (5, b"[2]")],
                {4: 0, 5: 1},
                1,
            ),
            7,
        ),
    ],
    ids=["/Size 40", "/Size 4", "objects 1 to 3, 4 and 5 compressed in 7, the table 8"],
)
def test_the_cross_reference_count(data, count):
    document = QpdfDocument.open(data(), None)
    try:
        assert document.cross_reference_count() == count
    finally:
        document.close()


def test_the_cross_reference_count_after_close():
    document = QpdfDocument.open(many_objects(b"40"), None)
    document.close()
    with pytest.raises(EngineFailure) as raised:
        document.cross_reference_count()
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")


def two_pages() -> bytes:
    """Page 0, object 4, strokes a line; page 1, object 5, has object 6 as its
    content, whose /Length 999 is too long for its 14 bytes: QPDF reads it as
    null and warns that it mis-read object 6, when it reads it (measured,
    #83)."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Contents %s >>"
    body = b"0 0 m 10 0 l S"
    return build(
        {
            1: CATALOG,
            2: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
            3: stream(body),
            4: page % b"3 0 R",
            5: page % b"6 0 R",
            6: b"<< /Length 999 >>\nstream\n" + body + b"\nendstream",
        },
        1,
    )


@pytest.mark.parametrize(
    ("order", "counted"),
    [((0, 1), [True, False]), ((1, 0), [False, False])],
    ids=["page 0 first", "page 1 first"],
)
def test_a_page_s_form_count_depends_on_what_was_read_before(order, counted):
    """Page 0 can be counted while QPDF has not read object 6; once page 1
    was read, no page can ("Limits")."""
    limits = pdfacts.Limits(max_forms_per_page=10)
    found = []
    with pdfacts.open(two_pages(), limits=limits) as document:
        for index in order:
            try:
                pdfacts.paths(document, index)
                found.append(True)
            except pdfacts.LimitUncheckedError:
                found.append(False)
    assert found == counted
