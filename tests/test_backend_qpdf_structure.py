"""The file's structure for the inspection: QPDF's objects by kind, the
encryption and each page's /Contents, as plain data.

Files are built with the byte-level builder, and the values expected are
those written (AGENTS.md, rule 2). Encrypted files are encrypted by pikepdf
with the parameters chosen here (tests/synthetic/encryption.py); a value of
the encryption dictionary that is pikepdf's choice is checked in the bytes it
wrote, and what QPDF makes of the dictionary follows from ISO 32000-2.
"""

from __future__ import annotations

import dataclasses
import re
import typing

import pikepdf
import pytest

from pdfacts.backends.protocols import (
    ContentsForm,
    ContentStreamLength,
    EngineFailure,
    FailureKind,
    ObjectCounts,
    PageContents,
    StructureReader,
)
from pdfacts.backends.qpdf import QpdfDocument
from synthetic.builder import build, build_with_object_stream, stream
from synthetic.encryption import encrypt

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
BODY = b"0 0 m 10 10 l S"


def page_file(contents: bytes | None, objects: dict[int, bytes] | None = None) -> bytes:
    """One page, object 3; `contents` written as the value of /Contents, or no key."""
    entry = b"" if contents is None else b" /Contents " + contents
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>" + entry + b" >>"
    return build({1: CATALOG, 2: PAGES, 3: page, **(objects or {})}, 1)


def raw_stream(dictionary: bytes, data: bytes = BODY) -> bytes:
    """A stream whose dictionary is written as given, /Length included."""
    return dictionary + b"\nstream\n" + data + b"\nendstream"


def structure(data: bytes, password: str | None = None):
    document = QpdfDocument.open(data, password)
    try:
        return document.structure()
    finally:
        document.close()


def length(
    number: int, written: int, *, indirect: bool = False, generation: int = 0
) -> ContentStreamLength:
    return ContentStreamLength(
        object_number=number, generation=generation, length=written, length_indirect=indirect
    )


def with_generation(data: bytes, number: int, generation: int) -> bytes:
    """`data` with object `number` of `generation`, from 1 to 9: its header,
    the references to it and its entry in the table, each as long as before."""
    data = re.sub(rb"(?m)^%d 0 obj" % number, b"%d %d obj" % (number, generation), data)
    data = re.sub(rb"(?<!\d)%d 0 R" % number, b"%d %d R" % (number, generation), data)
    table = data.rindex(b"\nxref\n0 ") + 1
    lines = data[table:].split(b"\n")
    entry = lines[2 + number]
    assert entry.endswith(b" 00000 n ")
    lines[2 + number] = entry[: -len(b"00000 n ")] + b"%05d n " % generation
    return data[:table] + b"\n".join(lines)


def test_the_objects_by_kind():
    """A catalog, a page tree, a page, a content stream, an array and a number."""
    data = page_file(b"4 0 R", {4: stream(BODY), 5: b"[1 2]", 6: b"7"})
    found = structure(data)
    assert found.misread is False
    assert found.objects == ObjectCounts(
        objects=6, streams=1, object_streams=0, cross_reference_streams=0, dictionaries=3, arrays=1
    )


def test_an_object_stream_and_a_cross_reference_stream():
    """Objects 1 to 3 uncompressed; an array and a dictionary in object
    stream 7; the cross-reference stream 8 (synthetic.builder)."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>"
    data = build_with_object_stream(
        {1: CATALOG, 2: PAGES, 3: page}, [(4, b"[1 2]"), (5, b"<< /A 1 >>")], {4: 0, 5: 1}, 1
    )
    found = structure(data)
    assert found.objects == ObjectCounts(
        objects=7, streams=0, object_streams=1, cross_reference_streams=1, dictionaries=4, arrays=1
    )


@pytest.mark.parametrize(
    ("contents", "objects", "expected"),
    [
        (b"4 0 R", {4: stream(BODY)}, PageContents(ContentsForm.STREAM, None, (length(4, 15),), 0)),
        (
            b"[4 0 R 5 0 R]",
            {4: stream(BODY), 5: stream(b"0 0")},
            PageContents(ContentsForm.ARRAY, None, (length(4, 15), length(5, 3)), 0),
        ),
        (
            b"6 0 R",
            {4: stream(BODY), 5: stream(b"0 0"), 6: b"[4 0 R 5 0 R]"},
            PageContents(ContentsForm.INDIRECT_ARRAY, (6, 0), (length(4, 15), length(5, 3)), 0),
        ),
        (
            b"[4 0 R 7 null 9 0 R]",
            {4: stream(BODY)},
            PageContents(ContentsForm.ARRAY, None, (length(4, 15),), 3),
        ),
        (
            b"6 0 R",
            {4: stream(BODY), 6: b"[null 4 0 R]"},
            PageContents(ContentsForm.INDIRECT_ARRAY, (6, 0), (length(4, 15),), 1),
        ),
        (b"9 0 R", {}, PageContents(ContentsForm.ABSENT, None, (), 0)),
        (None, {}, PageContents(ContentsForm.ABSENT, None, (), 0)),
        (b"null", {}, PageContents(ContentsForm.ABSENT, None, (), 0)),
        (b"7", {}, PageContents(ContentsForm.OTHER, None, (), 1)),
        (b"<< >>", {}, PageContents(ContentsForm.OTHER, None, (), 1)),
    ],
    ids=[
        "a stream",
        "an array",
        "an indirect array",
        "an array with others",
        "an indirect array with others",
        "an object the file lacks",
        "no key",
        "null",
        "a number",
        "a dictionary",
    ],
)
def test_the_contents_of_a_page(contents, objects, expected):
    """Object 9 is not in the file: a reference to it is a null (ISO
    32000-2, 7.3.10)."""
    found = structure(page_file(contents, objects))
    assert (found.misread, found.pages, found.warnings) == (False, (expected,), ())


def test_the_forms_by_name():
    assert [form.value for form in ContentsForm] == [
        "stream",
        "array",
        "indirect array",
        "absent",
        "other",
    ]


def test_objects_of_another_generation():
    """The indirect array is object 6 of generation 1, its stream object 4 of
    generation 2."""
    data = page_file(b"6 0 R", {4: stream(BODY), 6: b"[4 0 R]"})
    data = with_generation(with_generation(data, 6, 1), 4, 2)
    found = structure(data)
    expected = PageContents(ContentsForm.INDIRECT_ARRAY, (6, 1), (length(4, 15, generation=2),), 0)
    assert (found.pages, found.warnings) == ((expected,), ())


@pytest.mark.parametrize(
    ("dictionary", "objects", "indirect"),
    [(b"<< /Length 15 >>", {}, False), (b"<< /Length 5 0 R >>", {5: b"15"}, True)],
    ids=["an integer", "a reference to an integer"],
)
def test_the_length_a_content_stream_writes(dictionary, objects, indirect):
    found = structure(page_file(b"4 0 R", {4: raw_stream(dictionary), **objects}))
    assert found.pages[0].streams == (length(4, len(BODY), indirect=indirect),)


@pytest.mark.parametrize(
    ("dictionary", "objects", "warned"),
    [
        (b"<< /Length 10 >>", {}, 4),
        (b"<< /Length 20 >>", {}, 4),
        (b"<< >>", {}, 4),
        (b"<< /Length (15) >>", {}, 4),
        (b"<< /Length 15.0 >>", {}, 4),
        (b"<< /Length 5 0 R >>", {5: b"(15)"}, 5),
    ],
    ids=["5 short", "5 long", "no /Length", "a string", "a real", "a reference to a string"],
)
@pytest.mark.parametrize("named", [True, False], ids=["the page's", "no page's"])
def test_a_stream_qpdf_cannot_read(dictionary, objects, warned, named):
    """QPDF, its recovery off, reads such a stream as null and warns that it
    mis-read an object, at the stream's offset; where /Length is a reference,
    the warning names the object of the reference (measured). No count and
    no page's /Contents is known, whether a page names the stream or not."""
    contents = b"4 0 R" if named else b"8 0 R"
    data = page_file(contents, {4: raw_stream(dictionary), 8: stream(BODY), **objects})
    found = structure(data)
    assert (found.misread, found.objects, found.pages) == (True, None, None)
    assert any(f"(object {warned} 0, offset" in message for message in found.warnings)


@pytest.mark.parametrize("pages", [1, 2], ids=["named by no page", "another page's /Contents"])
def test_the_structure_changes_no_other_answer(pages):
    """Object 5 is a stream QPDF cannot read: named by no page, or the
    /Contents of a second page. QPDF warns of it only when it reads it
    (measured), so reading every object and every page for the structure
    must leave what the document answers otherwise as it was: page 0 draws
    no form and has no annotation, so PDFium parses no stream for them."""
    kids = b" ".join(b"%d 0 R" % number for number in (3, 6)[:pages])
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents %s >>"
    objects = {
        1: CATALOG,
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages),
        3: page % b"4 0 R",
        4: stream(BODY),
        5: raw_stream(b"<< /Length 10 >>"),
        6: page % b"5 0 R",
    }
    data = build(objects, 1)
    document = QpdfDocument.open(data, None)
    try:
        before = (document.parsed_streams(0), document.warnings())
        found = document.structure()
        after = (document.parsed_streams(0), document.warnings())
    finally:
        document.close()
    assert before == after == (0, ())
    assert found.misread is True


def test_no_page_has_contents_where_the_pages_are_not_the_files():
    """The page tree lists page 3 twice: QPDF copies the second (as
    test_backend_qpdf finds), so its pages are not the file's objects."""
    pages = b"<< /Type /Pages /Kids [3 0 R 3 0 R] /Count 2 >>"
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>"
    )
    data = build({1: CATALOG, 2: pages, 3: page, 4: stream(BODY)}, 1)
    found = structure(data)
    assert (found.misread, found.pages) == (False, None)
    assert found.objects == ObjectCounts(
        objects=4, streams=1, object_streams=0, cross_reference_streams=0, dictionaries=3, arrays=0
    )


def test_no_encryption():
    found = structure(page_file(b"4 0 R", {4: stream(BODY)}))
    assert found.encryption is None


# ISO 32000-2, Table 22: the bit of each permission, numbered from 1; bits 1
# and 2 are 0, bits 7, 8 and 13 to 32 are 1.
PERMISSION_BITS = {
    "print_lowres": 3,
    "modify_other": 4,
    "extract": 5,
    "modify_annotation": 6,
    "modify_form": 9,
    "accessibility": 10,
    "modify_assembly": 11,
    "print_highres": 12,
}


def p_of(allow: pikepdf.Permissions) -> int:
    """/P for the permissions, a signed 32-bit integer."""
    value = 0xFFFFF0C0
    for name, bit in PERMISSION_BITS.items():
        if getattr(allow, name):
            value |= 1 << (bit - 1)
    return value - (1 << 32)


ALLOW = pikepdf.Permissions(
    accessibility=True,
    extract=False,
    modify_annotation=True,
    modify_assembly=True,
    modify_form=False,
    modify_other=False,
    print_lowres=True,
    print_highres=False,
)


def encryption_dictionary(data: bytes) -> bytes:
    """The bytes of the object that holds the encryption dictionary."""
    at = data.index(b"/Filter /Standard")
    return data[data.rindex(b" obj", 0, at) : data.index(b"endobj", at)]


def entry(written: bytes, key: bytes) -> bytes | None:
    """The value of `key` in the dictionary's bytes: a number or a name."""
    found = re.findall(rb"%s (-?\d+|/\w+|true|false)(?![\w])" % re.escape(key), written)
    assert len(found) <= 1, (key, found)
    return found[0] if found else None


@pytest.mark.parametrize(
    ("options", "values"),
    [
        # (/V, /R, /Length and /EncryptMetadata as written; the key's bits)
        ({"R": 2, "aes": False, "metadata": False}, (b"1", b"2", b"40", None, 40)),
        ({"R": 3, "aes": False, "metadata": False}, (b"2", b"3", b"128", None, 128)),
        ({"R": 4}, (b"4", b"4", b"128", None, 128)),
        ({"R": 4, "aes": False, "metadata": False}, (b"4", b"4", b"128", b"false", 128)),
        ({"R": 4, "metadata": False}, (b"4", b"4", b"128", b"false", 128)),
        ({"R": 6}, (b"5", b"6", b"256", None, 256)),
    ],
    ids=["R2", "R3", "R4 AES", "R4 RC4", "R4 metadata in the clear", "R6"],
)
def test_the_encryption_as_written_and_as_qpdf_makes_it(options, values):
    """The standard security handler (ISO 32000-2, 7.6.4): the key has the
    bits /Length gives, /V 1 always 40, /V 5 256. /P is as written: from the
    permissions chosen (Table 22) where the revision is 3 or more; revision 2
    ignores bits 9 to 12, so what pikepdf writes there is not of the
    construction."""
    data = encrypt(
        page_file(b"4 0 R", {4: stream(BODY)}), user="", owner="o", allow=ALLOW, **options
    )
    v, r, length_written, metadata, bits = values
    written = encryption_dictionary(data)
    assert [entry(written, key) for key in (b"/V", b"/R", b"/EncryptMetadata")] == [
        v,
        r,
        metadata,
    ]
    assert re.search(rb"/Length %s(?!\d)" % length_written, written)
    p = int(entry(written, b"/P"))
    if options["R"] >= 3:
        assert p == p_of(ALLOW)
    found = structure(data)
    entries = found.encryption
    assert (entries.filter, entries.sub_filter) == (b"Standard", None)
    assert (entries.v, entries.r, entries.length) == (int(v), int(r), int(length_written))
    assert entries.encrypt_metadata is (None if metadata is None else False)
    assert (entries.length_invalid, entries.encrypt_metadata_invalid) == (False, False)
    assert entries.p == p
    assert entries.key_bits == bits


@pytest.mark.parametrize(
    ("user", "owner", "password", "matched"),
    [
        ("", "o", None, (True, False)),
        ("", "o", "o", (False, True)),
        ("u", "o", "u", (True, False)),
        ("u", "o", "o", (False, True)),
        ("x", "x", "x", (True, True)),
    ],
    ids=[
        "owner only, no password",
        "owner only, the owner's",
        "the user's",
        "the owner's",
        "one for both",
    ],
)
def test_which_password_opened_the_file(user, owner, password, matched):
    data = encrypt(page_file(b"4 0 R", {4: stream(BODY)}), user=user, owner=owner)
    entries = structure(data, password).encryption
    assert (entries.user_password_matched, entries.owner_password_matched) == matched


def test_the_length_an_encrypted_stream_writes():
    """AES-128 writes 16 bytes of initialization vector and the 15 bytes
    padded to 16 (ISO 32000-2, 7.6.3): /Length is 32 as written, where QPDF
    reads 15 bytes. The stream is left uncompressed."""
    data = encrypt(page_file(b"4 0 R", {4: stream(BODY)}), user="", owner="o", compress=False)
    found = structure(data)
    assert found.pages[0].streams == (length(4, 32),)


def test_a_key_length_the_dictionary_does_not_write():
    """Revision 2 with no /Length: the key is 40 bits long (ISO 32000-2,
    Table 20, /Length's default). The key is renamed, the same number of
    bytes."""
    data = encrypt(
        page_file(b"4 0 R", {4: stream(BODY)}),
        user="",
        owner="o",
        R=2,
        aes=False,
        metadata=False,
        compress=False,
    )
    assert data.count(b"/Length 40") == 1
    found = structure(data.replace(b"/Length 40", b"/Lengtx 40"))
    entries = found.encryption
    assert (entries.length, entries.length_invalid, entries.key_bits) == (None, False, 40)
    assert found.warnings == ()


@pytest.mark.parametrize(
    ("old", "new", "expected", "misread"),
    [
        (b"/EncryptMetadata false", b"/EncryptMetadata true ", {"encrypt_metadata": True}, False),
        (
            b"/EncryptMetadata false",
            b"/EncryptMetadata 12345",
            {"encrypt_metadata": None, "encrypt_metadata_invalid": True},
            False,
        ),
        (b"/Length 256", b"/Length /25", {"length": None, "length_invalid": True}, False),
        (
            b"/EncryptMetadata false",
            b"/SubFilter /Abcdefghij",
            {"encrypt_metadata": None, "sub_filter": b"Abcdefghij"},
            False,
        ),
        (
            b"/EncryptMetadata false",
            b"/SubFilter /Abcde#zzij",
            {
                "encrypt_metadata": None,
                "sub_filter": "/SubFilter is a name with a # not followed by two "
                "hexadecimal digits",
            },
            True,
        ),
    ],
    ids=[
        "/EncryptMetadata true",
        "/EncryptMetadata an integer",
        "/Length a name",
        "a /SubFilter",
        "a /SubFilter with a stray #",
    ],
)
def test_entries_of_the_dictionary_as_written(old, new, expected, misread):
    """Revision 6 with /EncryptMetadata false; an entry replaced by as many
    bytes. QPDF opens each: it warns that /Perms, which holds the flag too,
    no longer matches, and of the /SubFilter, naming the encryption
    dictionary and no object; of the stray #, naming the dictionary's object,
    which is a mis-read object (measured). The key stays 256 bits long."""
    data = encrypt(page_file(b"4 0 R", {4: stream(BODY)}), user="", owner="o", R=6, metadata=False)
    assert data.count(old) == 1
    found = structure(data.replace(old, new))
    unchanged = {
        "filter": b"Standard",
        "sub_filter": None,
        "length": 256,
        "length_invalid": False,
        "encrypt_metadata": False,
        "encrypt_metadata_invalid": False,
        "key_bits": 256,
    }
    entries = found.encryption
    assert {name: getattr(entries, name) for name in unchanged} == unchanged | expected
    assert found.misread is misread


def test_the_encryption_where_qpdf_cannot_read_a_stream():
    """The encryption is read where the objects are not: /Length 15 of the
    RC4 stream becomes 20, the same number of bytes. Too long, not too short:
    QPDF then reads past the data into the file's own text and finds no
    endstream after it. Shortened, the encrypted bytes, which follow from
    QPDF's file identifier and so from the clock, stood before endstream, and
    where they began with a %, after white space or not, QPDF read the rest of
    the line as a comment and found endstream: about one encryption in 250
    (measured, PR #89 and PR #90)."""
    data = encrypt(
        page_file(b"4 0 R", {4: stream(BODY)}),
        user="",
        owner="o",
        aes=False,
        metadata=False,
        compress=False,
    )
    assert data.count(b"/Length 15") == 1
    found = structure(data.replace(b"/Length 15", b"/Length 20"))
    assert (found.misread, found.objects, found.pages) == (True, None, None)
    assert found.encryption.r == 4


def test_closed():
    document = QpdfDocument.open(page_file(b"4 0 R", {4: stream(BODY)}), None)
    document.close()
    with pytest.raises(EngineFailure) as raised:
        document.structure()
    assert (raised.value.kind, raised.value.detail) == (FailureKind.OTHER, "closed")


def test_the_values_are_frozen():
    data = encrypt(page_file(b"4 0 R", {4: stream(BODY)}), user="", owner="o")
    found = structure(data)
    values = [found, found.objects, found.encryption, found.pages[0], found.pages[0].streams[0]]
    for value in values:
        field = dataclasses.fields(value)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, None)


def test_it_implements_the_structure_reader():
    members = typing.get_protocol_members(StructureReader)
    assert members == {"structure"}
    assert all(callable(getattr(QpdfDocument, name)) for name in members)
