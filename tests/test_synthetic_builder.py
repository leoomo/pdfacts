"""The PDF builder writes exactly the structure it is told to write.

The builder produces the inputs of most tests, so its own correctness is
checked here against the bytes it writes. A few tests open the files with the
engines, to show that the files are valid and that the encrypted ones need
what they should; the engines are never the source of an expected value.
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
from synthetic.builder import (
    PAGE_TREE,
    build,
    build_with_object_stream,
    document,
    page_object,
    stream,
    truncate,
)


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
        "cut before the xref table, rotation 90 and crop box",
        "cut inside the last page",
        "cut after the first page",
        "cut inside the catalog",
    }
    assert set(cases.ENCRYPTED) == {"user password", "owner password only"}


NUMBER = rb"-?\d+(?:\.\d+)?"


def read_box(dictionary: bytes, key: bytes) -> tuple[float, ...] | None:
    """The numbers of an array entry, read back from the bytes; None when absent."""
    found = re.findall(rb"/" + key + rb"\s*\[([^\]]*)\]", dictionary)
    assert len(found) <= 1, (key, found)
    return tuple(float(value) for value in found[0].split()) if found else None


def read_number(dictionary: bytes, key: bytes) -> float | None:
    found = re.findall(rb"/" + key + rb"\s+(" + NUMBER + rb")(?=[\s/>])", dictionary)
    assert len(found) <= 1, (key, found)
    return float(found[0]) if found else None


def read_token(dictionary: bytes, key: bytes) -> bytes | None:
    found = re.findall(rb"/" + key + rb"\s+(\([^)]*\)|[^\s/>]+)", dictionary)
    assert len(found) <= 1, (key, found)
    return found[0] if found else None


def as_floats(box):
    return None if box is None else tuple(float(value) for value in box)


@pytest.mark.parametrize("name", sorted(cases.GEOMETRY))
def test_each_geometry_case_contains_what_it_records(name):
    # Every value is read back from the bytes up to its delimiter and compared
    # with the record; an entry the record says is absent must be absent.
    case = cases.GEOMETRY[name]()
    tree = object_bytes(case.data, PAGE_TREE)
    assert read_box(tree, b"MediaBox") == as_floats(case.pages_media_box)
    assert read_number(tree, b"Rotate") == case.pages_rotate
    assert read_box(tree, b"CropBox") is None and read_token(tree, b"UserUnit") is None
    for number, media_box in zip(case.page_objects, case.media_boxes, strict=True):
        page = object_bytes(case.data, number)
        assert read_box(page, b"MediaBox") == as_floats(media_box)
        assert read_box(page, b"CropBox") == as_floats(case.crop_box)
        assert read_number(page, b"Rotate") == case.rotate
        assert read_token(page, b"UserUnit") == case.user_unit


@pytest.mark.parametrize("name", sorted(cases.GEOMETRY))
def test_every_page_fills_the_recorded_rectangle_in_the_recorded_colour(name):
    case = cases.GEOMETRY[name]()
    for number in case.page_objects:
        content = object_bytes(case.data, number + 1)
        data = content[
            content.index(b"stream\n") + len(b"stream\n") : content.rindex(b"\nendstream")
        ]
        match = re.fullmatch(rb"(\S+) (\S+) (\S+) rg (\S+) (\S+) (\S+) (\S+) re f", data)
        assert match, data
        red, green, blue, x, y, width, height = (float(value) for value in match.groups())
        assert (red, green, blue) == case.colour
        assert (x, y, x + width, y + height) == case.rectangle


@pytest.mark.parametrize("name", sorted(cases.GEOMETRY))
def test_the_recorded_size_is_the_size_of_the_trailer(name):
    case = cases.GEOMETRY[name]()
    assert re.search(rb"/Size (\d+)", trailer(case.data)).group(1) == b"%d" % case.size


def test_the_unusable_cases_are_what_their_names_say():
    assert cases.empty().data == b""
    assert cases.header_only().data == b"%PDF-1.7\n"
    assert b"%PDF-" not in cases.garbage().data
    after_header = cases.garbage_after_header().data
    assert after_header.startswith(b"%PDF-1.7\n")
    assert b"xref" not in after_header and b"trailer" not in after_header


@pytest.mark.parametrize("name", sorted(cases.TRUNCATED))
def test_each_truncated_case_is_a_shorter_prefix_of_its_source(name):
    case = cases.TRUNCATED[name]()
    assert case.source.data.startswith(case.data)
    assert case.cut_at == len(case.data) < len(case.source.data)
    assert case.pages_built == len(case.source.page_objects)


def test_the_truncated_cases_end_where_their_names_say():
    full = cases.three_pages().data
    assert cases.cut_before_xref_table().cut_at == full.rindex(b"\nxref\n") + 1
    inside_table = cases.cut_inside_xref_table().cut_at
    assert full.rindex(b"\nxref\n") + 1 < inside_table < full.rindex(b"trailer")
    assert cases.cut_after_first_page().cut_at == full.index(b"%d 0 obj" % page_object(1))
    last = full.index(b"%d 0 obj" % (page_object(2) + 1))
    data_start = full.index(b"stream\n", last) + len(b"stream\n")
    data_end = full.index(b"\nendstream", data_start)
    assert data_start < cases.cut_inside_last_page().cut_at < data_end
    catalog = full.index(b"1 0 obj")
    assert catalog < cases.cut_inside_catalog().cut_at < full.index(b"endobj", catalog)


def test_the_rotated_cropped_cut_keeps_its_geometry_on_record():
    case = cases.cut_before_xref_table_rotated_cropped()
    assert case.source.rotate == 90
    assert case.source.crop_box == (20, 10, 380, 290)
    assert case.cut_at == case.source.data.rindex(b"\nxref\n") + 1


def test_the_verdana_page_uses_the_font_it_records_and_nothing_else():
    case = cases.verdana_text()
    assert case.font in case.data
    assert b"/Helvetica" not in case.data
    assert b"/Widths" not in case.data
    assert b"BT /F1 24 Tf 40 200 Td (Hamburgefonstiv 0123) Tj ET" in case.data
    loaded = pypdfium2.PdfDocument(case.data)
    try:
        assert len(loaded) == 1
    finally:
        loaded.close()


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


def test_an_update_keeps_the_file_before_it_and_names_its_table():
    source = cases.plain_page().data
    updated = builder.append_update(source, {6: b"<< /Updated true >>"}, 1)
    assert updated.startswith(source)
    tail = updated[len(source) :]
    previous = int(re.search(rb"startxref\n(\d+)", source).group(1))
    assert re.search(rb"/Prev (\d+)", tail).group(1) == b"%d" % previous
    table = int(re.findall(rb"startxref\n(\d+)", updated)[-1])
    assert updated[table:].startswith(b"xref\n6 1\n")
    offset = int(updated[table + len(b"xref\n6 1\n") :][:10])
    assert updated[offset:].startswith(b"6 0 obj\n<< /Updated true >>")
    assert re.search(rb"/Size (\d+)", tail).group(1) == b"7"
    assert updated.endswith(b"%%EOF\n")


def test_the_update_of_the_page_is_what_the_engines_read():
    case = cases.incremental_update()
    pdf = pikepdf.open(io.BytesIO(case.data))
    assert [float(value) for value in pdf.pages[0].obj.MediaBox] == list(case.media_box)
    assert pdf.get_warnings() == []
    document_ = pypdfium2.PdfDocument(case.data)
    assert document_[0].get_mediabox() == tuple(float(value) for value in case.media_box)


def test_the_startxref_case_points_three_bytes_behind_the_table():
    case = cases.startxref_off_by_three()
    table = case.data.rindex(b"\nxref\n") + 1
    assert int(re.findall(rb"startxref\n(\d+)", case.data)[-1]) == table + 3
    assert len(case.data) == len(case.source.data)


def test_the_crlf_case_has_no_line_feed_alone():
    case = cases.line_ends_crlf()
    assert b"\n" not in case.data.replace(b"\r\n", b"")
    assert case.data.replace(b"\r\n", b"\n") == case.source.data


def test_what_follows_the_last_end_of_file_marker():
    white = b"\x00\t\n\x0c\r "
    for make, only_white_space in (
        (cases.white_space_after_the_end, True),
        (cases.junk_after_the_end, False),
        (cases.cut_inside_the_update, False),
        (cases.vertical_tab_after_the_end, False),
    ):
        case = make()
        assert case.data.startswith(case.source.data)
        tail = case.data[case.data.rindex(b"%%EOF") + len(b"%%EOF") :]
        assert (tail.strip(white) == b"") is only_white_space, make.__name__
    assert cases.vertical_tab_after_the_end().data.endswith(b"%%EOF\n\x0b")


def test_the_case_without_an_end_marker_keeps_every_offset():
    case = cases.no_end_marker()
    assert b"%%EOF" not in case.data
    assert len(case.data) == len(case.source.data)
    assert case.data.replace(b"%%END", b"%%EOF") == case.source.data


def test_the_cases_with_a_page_that_is_not_there_are_what_their_names_say():
    count = cases.count_without_kids().data
    assert b"/Kids [] /Count 1" in count
    missing = cases.missing_kid().data
    assert b"/Kids [7 0 R] /Count 1" in missing
    assert 7 not in table_entries(missing)
    kids_null = cases.page_with_kids_null().data
    assert b"/Kids [4 0 R 5 0 R 6 0 R] /Count 3" in kids_null
    entries = table_entries(kids_null)
    first = entries[4][0]
    assert kids_null[first:].startswith(b"4 0 obj\n<< /Type /Page ")
    assert b"/Kids null" in kids_null[first : kids_null.index(b"endobj", first)]
    for number in (1, 2, 4, 5, 6, 9):
        offset = entries[number][0]
        assert kids_null[offset:].startswith(b"%d 0 obj" % number)
    middle = cases.missing_kid_in_the_middle().data
    assert b"/Kids [4 0 R 7 0 R 5 0 R] /Count 3" in middle
    entries = table_entries(middle)
    assert entries[7][2] == b"f"
    assert entries[4][2] == entries[5][2] == b"n"


def test_the_cut_update_ends_inside_the_object_of_its_update():
    case = cases.cut_inside_the_update()
    update = cases.incremental_update().data
    assert update.startswith(case.data)
    assert case.data[len(case.source.data) :].startswith(b"4 0 obj")
    assert b"endobj" not in case.data[len(case.source.data) :]


def test_the_offset_left_after_the_last_startxref_names_the_earlier_table():
    case = cases.cut_inside_the_last_offset()
    earlier = int(re.findall(rb"startxref\n(\d+)", case.source.data)[-1])
    assert case.data.startswith(case.source.data)
    assert case.data.endswith(b"startxref\n%d" % earlier)
    assert case.data[earlier:].startswith(b"xref\n")
    assert case.data[10 * earlier :].startswith(b"xref\n")
    update = case.data[len(case.source.data) : 10 * earlier]
    assert update.startswith(b"4 0 obj\n<< /Type /Page ")
    assert update.count(b" obj\n") == update.count(b"\nendobj\n") == 2


def test_the_offset_left_after_the_last_startxref_can_name_the_update():
    case = cases.cut_inside_the_last_offset(naming_the_update=True)
    named = len(case.source.data)
    assert case.data.endswith(b"startxref\n%d" % named)
    assert case.data[named:].startswith(b"4 0 obj\n<< /Type /Page ")
    assert case.data[10 * named :].startswith(b"xref\n")


@pytest.mark.parametrize("first_page_offset", [False, True])
def test_the_linearized_case_has_the_layout_of_annex_f(first_page_offset):
    case = cases.linearized(first_page_offset=first_page_offset)
    data = case.data
    first_table = data.index(b"xref\n5 1\n")
    found = list(re.finditer(rb"startxref\n(\d+)", data))
    assert [int(match.group(1)) for match in found] == [
        first_table if first_page_offset else 0,
        first_table,
    ]
    assert data[case.main_table :].startswith(b"xref\n0 5\n")
    first_page_end = int(re.search(rb"/E (\d+)", data[:1024]).group(1))
    assert found[0].start() < first_page_end < found[1].start()
    assert b"/Prev %010d" % case.main_table in data
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        assert len(pdf.pages) == 1
        assert pdf.get_warnings() == []


def test_the_linearized_case_without_a_first_page_trailer_has_one_table():
    case = cases.linearized(first_page_trailer=False)
    data = case.data
    assert [int(offset) for offset in re.findall(rb"startxref\n(\d+)", data)] == [case.main_table]
    assert data[case.main_table :].startswith(b"xref\n0 6\n")
    assert data.index(b"/Linearized") < 1024
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        assert len(pdf.pages) == 1
        assert pdf.get_warnings() == []


def test_the_update_in_a_cross_reference_stream_points_at_its_objects():
    case = cases.update_in_a_cross_reference_stream()
    data = case.data
    table = int(re.findall(rb"startxref\n(\d+)", data)[-1])
    assert data[table:].startswith(b"7 0 obj\n<< /Type /XRef ")
    earlier = int(re.findall(rb"startxref\n(\d+)", case.source.data)[-1])
    assert b"/Prev %d " % earlier in data[table:]
    start = data.index(b"stream\n", table) + len(b"stream\n")
    entries = data[start : start + 12]
    offsets = [int.from_bytes(entries[at + 1 : at + 5], "big") for at in (0, 6)]
    assert data[offsets[0] :].startswith(b"4 0 obj\n<< /Type /Page ")
    assert offsets[1] == table
    assert entries[0] == entries[6] == 1


def test_the_cases_without_pages_are_what_their_names_say():
    empty = cases.empty_page_tree().data
    assert b"/Kids [] /Count 0" in empty
    none = cases.no_page_tree().data
    assert b"/Pages" not in none
    missing = cases.missing_page_tree().data
    assert b"/Pages 7 0 R" in missing
    assert 7 not in table_entries(missing)
    for data in (empty, none, missing):
        assert b"/Type /Page " not in data


def test_the_object_stream_holds_what_it_was_given():
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /UserUnit 9 0 R >>"
    )
    data = build_with_object_stream(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            4: page,
        },
        [(9, b"2")],
        {9: 0},
        1,
    )
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        assert int(pdf.get_object(9, 0)) == 2
        entry = pdf.get_xref_table()[(9, 0)]
        assert entry.type == 2
        assert entry.obj_stream_number == 7
        assert entry.obj_stream_index == 0
