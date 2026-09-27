"""The PDF builder writes exactly the structure it is told to write.

The builder produces the inputs of most tests, so its own correctness is
checked here against the bytes it writes. Two tests open the files with the
engines, to show that the files are valid; the engines are never the source
of an expected value.
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pikepdf
import pypdfium2
import pytest

from synthetic import builder, cases
from synthetic.builder import PAGE_TREE, build, document, page_object, stream, truncate


def table_entries(data: bytes) -> dict[int, tuple[int, int, bytes]]:
    """The classic cross-reference table: object number -> (offset, generation, kind).

    The table is found by its keyword, not through startxref, so that a wrong
    startxref does not hide a wrong table.
    """
    start = data.rindex(b"\nxref\n") + 1
    header = re.match(rb"xref\n0 (\d+)\n", data[start:])
    assert header is not None
    first = start + header.end()
    entries = {}
    for number in range(int(header.group(1))):
        entry = data[first + 20 * number : first + 20 * (number + 1)]
        assert re.fullmatch(rb"\d{10} \d{5} [nf] \n", entry), entry
        entries[number] = (int(entry[:10]), int(entry[11:16]), entry[17:18])
    return entries


def object_bytes(data: bytes, number: int) -> bytes:
    start = data.index(b"%d 0 obj\n" % number)
    return data[start : data.index(b"\nendobj\n", start)]


def trailer(data: bytes) -> bytes:
    return data[data.rindex(b"trailer\n") :]


# The tests that the issue asks for, one per row of its table.


@pytest.mark.parametrize(
    "data",
    [
        build({1: b"<< /Type /Catalog >>", 2: b"(two)", 3: b"(three)"}, 1),
        cases.three_pages().data,
    ],
    ids=["three objects", "three pages"],
)
def test_every_offset_in_the_table_points_at_its_object(data):
    entries = table_entries(data)
    in_use = [number for number, (_, _, kind) in entries.items() if kind == b"n"]
    assert in_use
    for number in in_use:
        offset = entries[number][0]
        assert data[offset:].startswith(b"%d 0 obj\n" % number), number


def test_size_is_the_highest_object_number_plus_one():
    # Objects 3 and 4 are missing, so the number of objects would give 4, not 6.
    data = build({1: b"<< /Type /Catalog >>", 2: b"(two)", 5: b"(five)"}, 1)
    assert re.search(rb"/Size (\d+)", trailer(data)).group(1) == b"6"
    kinds = {number: kind for number, (_, _, kind) in table_entries(data).items()}
    assert kinds == {0: b"f", 1: b"n", 2: b"n", 3: b"f", 4: b"f", 5: b"n"}


def test_the_free_entries_form_a_list():
    data = build({1: b"<< /Type /Catalog >>", 2: b"(two)", 5: b"(five)"}, 1)
    entries = table_entries(data)
    # 0 -> 3 -> 4 -> 0; object 0 has generation 65535.
    assert entries[0] == (3, 65535, b"f")
    assert entries[3][0] == 4 and entries[4][0] == 0


def test_startxref_points_at_the_table():
    data = cases.three_pages().data
    number = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", data).group(1))
    assert number == data.rindex(b"\nxref\n") + 1


@pytest.mark.parametrize(
    "content",
    [b"", b"0 g 50 40 60 20 re f", b"line one\nline two\n", bytes(range(256))],
    ids=["empty", "one operator", "two lines", "every byte value"],
)
def test_stream_length_is_the_length_of_the_data(content):
    body = stream(content)
    declared = int(re.search(rb"/Length (\d+)", body).group(1))
    start = body.index(b">>\nstream\n") + len(b">>\nstream\n")
    between = body[start : body.rindex(b"\nendstream")]
    assert declared == len(between) == len(content)
    assert between == content


def test_a_box_written_on_the_page_tree_is_not_on_the_page():
    case = cases.media_box_on_pages()
    assert b"/MediaBox" not in object_bytes(case.data, case.page_objects[0])
    assert b"/MediaBox [0 0 400 300]" in object_bytes(case.data, PAGE_TREE)


@pytest.mark.parametrize("name", sorted(cases.GEOMETRY))
def test_both_engines_read_every_geometry_case(name):
    case = cases.GEOMETRY[name]()
    loaded = pypdfium2.PdfDocument(case.data)
    try:
        assert len(loaded) == len(case.page_objects)
    finally:
        loaded.close()
    # Without recovery: the file has to be valid as written.
    with pikepdf.open(io.BytesIO(case.data), attempt_recovery=False) as pdf:
        assert [page.obj.objgen for page in pdf.pages] == [(n, 0) for n in case.page_objects]
        warnings = pdf.get_warnings()
    if name == "no media box":
        # QPDF supplies the default box and says so; the file is still read.
        assert len(warnings) == 1
    else:
        assert warnings == []


def test_truncation_before_a_marker():
    data = cases.three_pages().data
    cut = truncate(data, before=b"xref")
    assert data.startswith(cut)
    assert data[len(cut) :].startswith(b"xref")
    assert b"xref" not in cut


def test_truncation_after_a_number_of_bytes():
    data = cases.three_pages().data
    assert truncate(data, keep=100) == data[:100]
    assert len(truncate(data, keep=100)) == 100


def test_truncation_needs_exactly_one_rule():
    with pytest.raises(TypeError):
        truncate(b"%PDF-1.7\n")
    with pytest.raises(TypeError):
        truncate(b"%PDF-1.7\n", before=b"x", keep=1)
    with pytest.raises(ValueError):
        truncate(b"%PDF-1.7\n", before=b"not there")


# The builder itself.


def test_the_builder_imports_no_engine():
    tree = ast.parse(Path(builder.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"pikepdf", "pypdfium2", "pypdfium2_raw", "fitz", "pymupdf"}


def test_a_document_has_its_pages_in_the_order_given():
    data = document(
        [builder.PageSpec(b"/MediaBox [0 0 100 100]"), builder.PageSpec(b"/MediaBox [0 0 200 200]")]
    )
    tree = object_bytes(data, PAGE_TREE)
    assert b"/Kids [%d 0 R %d 0 R] /Count 2" % (page_object(0), page_object(1)) in tree
    assert b"/MediaBox [0 0 100 100]" in object_bytes(data, page_object(0))
    assert b"/MediaBox [0 0 200 200]" in object_bytes(data, page_object(1))


# The shared cases.


def test_every_case_of_the_issue_exists():
    assert set(cases.GEOMETRY) >= {
        "plain page",
        "rotation 0",
        "rotation 90",
        "rotation 180",
        "rotation 270",
        "rotation -90",
        "rotation 450",
        "rotation 45",
        "crop box inside",
        "crop box larger than the media box",
        "media box with shifted origin",
        "media box with reversed corners",
        "media box only on the page tree",
        "media box and rotation only on the page tree",
        "no media box",
        "UserUnit 2",
        "UserUnit 2.5",
        "UserUnit 0",
        "UserUnit -1",
        "UserUnit (text)",
        "three pages",
    }
    assert set(cases.UNUSABLE) == {"empty", "header only", "garbage", "garbage after a header"}
    assert set(cases.TRUNCATED) == {
        "cut inside the xref table",
        "cut before the xref table",
        "cut inside the last page",
        "cut after the first page",
        "cut inside the catalog",
    }
    assert set(cases.ENCRYPTED) == {"user password", "owner password only"}


@pytest.mark.parametrize("name", sorted(cases.GEOMETRY))
def test_each_geometry_case_contains_what_it_records(name):
    case = cases.GEOMETRY[name]()
    tree = object_bytes(case.data, PAGE_TREE)
    for number, media_box in zip(case.page_objects, case.media_boxes, strict=True):
        page = object_bytes(case.data, number)
        if media_box is None:
            assert b"/MediaBox" not in page
        else:
            assert b"/MediaBox " + cases.box_text(media_box) in page
        if case.crop_box is not None:
            assert b"/CropBox " + cases.box_text(case.crop_box) in page
        if case.rotate is not None:
            assert b"/Rotate %d" % case.rotate in page
        if case.user_unit is not None:
            assert b"/UserUnit " + case.user_unit in page
        assert b"0 g 50 40 60 20 re f" in case.data
    if case.pages_media_box is not None:
        assert b"/MediaBox " + cases.box_text(case.pages_media_box) in tree
    if case.pages_rotate is not None:
        assert b"/Rotate %d" % case.pages_rotate in tree


def test_the_unusable_cases_are_what_their_names_say():
    assert cases.empty().data == b""
    assert cases.header_only().data == b"%PDF-1.7\n"
    assert b"%PDF-" not in cases.garbage().data
    after_header = cases.garbage_after_header().data
    assert after_header.startswith(b"%PDF-1.7\n")
    assert b"xref" not in after_header and b"trailer" not in after_header


@pytest.mark.parametrize("name", sorted(cases.TRUNCATED))
def test_each_truncated_case_is_a_shorter_prefix_of_three_pages(name):
    case = cases.TRUNCATED[name]()
    full = cases.three_pages().data
    assert case.pages_built == 3
    assert full.startswith(case.data)
    assert case.cut_at == len(case.data) < len(full)


def test_the_truncated_cases_end_where_their_names_say():
    full = cases.three_pages().data
    assert cases.cut_before_xref_table().cut_at == full.rindex(b"\nxref\n") + 1
    inside_table = cases.cut_inside_xref_table().cut_at
    assert full.rindex(b"\nxref\n") + 1 < inside_table < full.rindex(b"trailer")
    assert cases.cut_after_first_page().cut_at == full.index(b"%d 0 obj" % page_object(1))
    last = full.index(b"%d 0 obj" % (page_object(2) + 1))
    assert last < cases.cut_inside_last_page().cut_at < full.rindex(b"endobj")
    catalog = full.index(b"1 0 obj")
    assert catalog < cases.cut_inside_catalog().cut_at < full.index(b"endobj", catalog)


def test_a_user_password_is_needed_for_one_encrypted_case_and_not_the_other():
    locked = cases.user_password()
    with pytest.raises(pypdfium2.PdfiumError):
        pypdfium2.PdfDocument(locked.data)
    for password in (locked.user_password, locked.owner_password):
        opened = pypdfium2.PdfDocument(locked.data, password=password)
        assert len(opened) == locked.pages_built
        opened.close()
    open_to_all = cases.owner_password_only()
    assert open_to_all.user_password == ""
    opened = pypdfium2.PdfDocument(open_to_all.data)
    assert len(opened) == open_to_all.pages_built
    opened.close()
    for case in (locked, open_to_all):
        with pikepdf.open(io.BytesIO(case.data), password=case.owner_password) as pdf:
            assert pdf.is_encrypted
