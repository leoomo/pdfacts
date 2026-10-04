"""inspect(): what QPDF reads of a file's structure and its active content, as
public facts (docs/conventions.md, "Inspection").

Files are built with the byte-level builder, some then encrypted by pikepdf
with the parameters the test chooses; the values expected are those written,
and those ISO 32000-2 gives for them (AGENTS.md, rule 2). What is pikepdf's
choice, /P, the encryption dictionary's object and its crypt filters, is read
from the bytes it wrote.
"""

from __future__ import annotations

import dataclasses
import re

import pikepdf
import pytest

import pdfacts
from pdfacts import (
    Action,
    ActionPlace,
    ActionTrigger,
    ActiveContent,
    Contents,
    ContentsForm,
    ContentsStream,
    CryptFilter,
    CryptMethod,
    EmbeddedFile,
    Encryption,
    EngineError,
    Inspection,
    LimitExceededError,
    Limits,
    Name,
    NameEncoding,
    ObjectCounts,
    ObjectRef,
    PageContents,
    Permissions,
    Unknown,
    UnknownReason,
    inspect,
)
from synthetic.builder import build, build_with_object_stream, stream
from synthetic.encryption import encrypt

PAGES = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
BODY = b"0 0 m 10 10 l S"
JS = b"<< /S /JavaScript /JS (1) >>"
URI = b"<< /S /URI /URI (u) >>"
UNREADABLE_LENGTH = b"<< /Length 10 >>\nstream\n" + BODY + b"\nendstream"
MISREAD = (
    "QPDF warned that it mis-read an object of the file, so what it reads of the "
    "objects is not known; its warnings are in the inspection's warnings"
)
MISREAD_ENTRY = (
    "QPDF warned that it mis-read an object of the file, and drops a key whose value "
    "it reads as null, so whether the entry is written is not known"
)
NOT_AS_WRITTEN = (
    "the pages QPDF found are not the objects the file's page tree lists, so the "
    "object QPDF has at a page index may not be the file's"
)


def catalog(entries: bytes = b"") -> bytes:
    return b"<< /Type /Catalog /Pages 2 0 R" + entries + b" >>"


def page(entries: bytes = b"") -> bytes:
    return (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >>" + entries + b" >>"
    )


def one_page(
    catalog_entries: bytes = b"", page_entries: bytes = b"", objects: dict[int, bytes] | None = None
) -> bytes:
    """The catalog, object 1; the page tree, 2; its one page, 3."""
    return build(
        {1: catalog(catalog_entries), 2: PAGES, 3: page(page_entries), **(objects or {})}, 1
    )


def inspected(data: bytes, password: str | None = None) -> Inspection:
    with pdfacts.open(data, password=password) as document:
        return inspect(document)


def ref(number: int, generation: int = 0) -> ObjectRef:
    return ObjectRef(object_number=number, generation=generation)


def name(text: str) -> Name:
    return Name(raw=text.encode(), text=text, encoding=NameEncoding.UTF8)


def counts(objects: int, streams: int, dictionaries: int, arrays: int = 0) -> ObjectCounts:
    return ObjectCounts(
        total=objects,
        streams=streams,
        object_streams=0,
        cross_reference_streams=0,
        dictionaries=dictionaries,
        arrays=arrays,
    )


def contents(
    index: int, number: int, form: ContentsForm, named: int = 0, array=None
) -> PageContents:
    """The page at `index`, object `number`, its /Contents at `named` of Inspection.contents."""
    return PageContents(page_index=index, ref=ref(number), form=form, array=array, contents=named)


def named(*streams: ContentsStream, others: int = 0) -> Contents:
    return Contents(streams=streams, others=others)


NOTHING = ActiveContent(
    actions=(), sequences=(), triggers=(), xfa=None, embedded_files=(), embedded_streams=()
)


def test_a_page_and_its_content_stream():
    """Object 5, the integer 42, is an object of no kind the counts name."""
    found = inspected(
        one_page(page_entries=b" /Contents 4 0 R", objects={4: stream(BODY), 5: b"42"})
    )
    stream_4 = ContentsStream(ref=ref(4), length=len(BODY), length_indirect=False)
    assert found == Inspection(
        size=6,
        objects=counts(5, 1, 3),
        pages=(contents(0, 3, ContentsForm.STREAM),),
        contents=(named(stream_4),),
        encryption=None,
        active_content=NOTHING,
        warnings=(),
    )


def test_a_length_below_0():
    """Stream 4 writes /Length -1 and holds nothing: QPDF reads it as 0 bytes,
    with a warning that names no mis-read object, and PDFium opens the file
    (measured)."""
    written = b"<< /Length -1 >>\nstream\n\nendstream"
    found = inspected(one_page(page_entries=b" /Contents 4 0 R", objects={4: written}))
    below = Unknown(
        UnknownReason.INVALID, "/Length of the stream is below 0, which QPDF reads as 0"
    )
    assert found.contents == (
        named(ContentsStream(ref=ref(4), length=below, length_indirect=False)),
    )
    assert isinstance(found.objects, ObjectCounts)
    assert any("unsigned value request for negative number" in one for one in found.warnings)


@pytest.mark.parametrize(
    "written, objects, form, array, streams, others",
    [
        (b"[4 0 R 5 0 R]", {}, ContentsForm.ARRAY, None, ((4, 15, False), (5, 3, True)), 0),
        (
            b"6 0 R",
            {6: b"[4 0 R 5 0 R null]"},
            ContentsForm.INDIRECT_ARRAY,
            ref(6),
            ((4, 15, False), (5, 3, True)),
            1,
        ),
        (b"9 0 R", {}, ContentsForm.ABSENT, None, (), 0),
        (b"7", {}, ContentsForm.OTHER, None, (), 1),
    ],
    ids=["an array", "an indirect array", "a reference to no object", "a number"],
)
def test_the_contents_of_a_page(written, objects, form, array, streams, others):
    """Stream 4 writes /Length 15, stream 5 /Length 8 0 R, object 8 the integer 3."""
    files = {4: stream(BODY), 5: b"<< /Length 8 0 R >>\nstream\nabc\nendstream", 8: b"3", **objects}
    found = inspected(one_page(page_entries=b" /Contents " + written, objects=files))
    expected = tuple(
        ContentsStream(ref=ref(number), length=length, length_indirect=indirect)
        for number, length, indirect in streams
    )
    assert found.pages == (contents(0, 3, form, array=array),)
    assert found.contents == (named(*expected, others=others),)


def test_two_pages_and_their_objects():
    objects = {
        1: catalog(),
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        3: page(b" /Contents 4 0 R"),
        4: stream(BODY),
        5: page(),
    }
    found = inspected(build(objects, 1))
    assert found.pages == (
        contents(0, 3, ContentsForm.STREAM, 0),
        contents(1, 5, ContentsForm.ABSENT, 1),
    )
    stream_4 = ContentsStream(ref=ref(4), length=len(BODY), length_indirect=False)
    assert found.contents == (named(stream_4), named())
    assert (found.size, found.objects) == (6, counts(5, 1, 4, 0))


def test_what_pages_share_is_listed_once():
    """Pages 3 and 5 name the indirect array 6, which names stream 4 twice;
    pages 7 and 8 the stream 4; pages 9 and 10 each a direct array of its own.
    Each indirect array or stream is listed once, each direct array for its
    page, and the backend reads each once: one value for the pages that name
    it, and one for the stream however often an array names it."""
    from pdfacts.backends.qpdf import QpdfDocument

    kids = b"[3 0 R 5 0 R 7 0 R 8 0 R 9 0 R 10 0 R]"
    objects = {
        1: catalog(),
        2: b"<< /Type /Pages /Kids " + kids + b" /Count 6 >>",
        3: page(b" /Contents 6 0 R"),
        4: stream(BODY),
        5: page(b" /Contents 6 0 R"),
        6: b"[4 0 R 4 0 R]",
        7: page(b" /Contents 4 0 R"),
        8: page(b" /Contents 4 0 R"),
        9: page(b" /Contents [4 0 R]"),
        10: page(b" /Contents [4 0 R 4 0 R null]"),
    }
    data = build(objects, 1)
    found = inspected(data)
    array = ContentsForm.INDIRECT_ARRAY
    assert found.pages == (
        contents(0, 3, array, 0, ref(6)),
        contents(1, 5, array, 0, ref(6)),
        contents(2, 7, ContentsForm.STREAM, 1),
        contents(3, 8, ContentsForm.STREAM, 1),
        contents(4, 9, ContentsForm.ARRAY, 2),
        contents(5, 10, ContentsForm.ARRAY, 3),
    )
    stream_4 = ContentsStream(ref=ref(4), length=len(BODY), length_indirect=False)
    assert found.contents == (
        named(stream_4, stream_4),
        named(stream_4),
        named(stream_4),
        named(stream_4, stream_4, others=1),
    )
    named_4 = [found.contents[0].streams[0], found.contents[0].streams[1]]
    named_4 += [found.contents[1].streams[0], found.contents[2].streams[0]]
    assert all(one is named_4[0] for one in named_4)
    document = QpdfDocument.open(data, None)
    try:
        pages = document.structure().pages
    finally:
        document.close()
    assert (pages[0] is pages[1], pages[2] is pages[3], pages[3] is pages[4]) == (True, True, False)
    assert pages[4] is not pages[5]
    assert pages[0].streams[0] is pages[0].streams[1] is pages[2].streams[0]
    assert pages[4].streams[0] is pages[2].streams[0]


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


def test_objects_of_another_generation():
    """The catalog 1 of generation 4, the page 3 of generation 2, its content
    stream 4 of generation 1, the action 5 of generation 3."""
    data = one_page(b" /OpenAction 5 0 R", b" /Contents 4 0 R", {4: stream(BODY), 5: JS})
    for number, generation in ((1, 4), (3, 2), (4, 1), (5, 3)):
        data = with_generation(data, number, generation)
    found = inspected(data)
    assert (found.pages[0].ref, found.contents[0].streams[0].ref) == (ref(3, 2), ref(4, 1))
    assert found.active_content.actions[0].ref == ref(5, 3)
    assert found.active_content.triggers[0].owner == ref(1, 4)


def test_more_objects_of_another_generation():
    """The XFA stream 5 of generation 2, the file specification 6 of
    generation 3 and its embedded file stream 7 of generation 4, the page's
    indirect /Contents array 8 of generation 5."""
    entries = b" /AcroForm << /XFA 5 0 R >> /Names << /EmbeddedFiles << /Names [(a) 6 0 R] >> >>"
    objects = {
        4: stream(BODY),
        5: stream(b"<xdp/>"),
        6: b"<< /Type /Filespec /F (a) /EF << /F 7 0 R >> >>",
        7: stream(b"a"),
        8: b"[4 0 R]",
    }
    data = one_page(entries, b" /Contents 8 0 R", objects)
    for number, generation in ((5, 2), (6, 3), (7, 4), (8, 5)):
        data = with_generation(data, number, generation)
    found = inspected(data)
    content = found.active_content
    assert content.xfa == (ref(5, 2),)
    assert content.embedded_files == (EmbeddedFile(specification=ref(6, 3), streams=0),)
    assert content.embedded_streams == ((ref(7, 4),),)
    assert found.pages[0].array == ref(8, 5)


def test_the_warnings_of_both_views(monkeypatch):
    """The view of the active content reads the same objects as that of the
    structure, and QPDF gives the same warnings there (on the corpus too); a
    warning given there alone is given once, after the structure's."""
    import dataclasses as fields

    from pdfacts.core.document import FeatureAccess

    read = FeatureAccess.active_content

    def active_content(self):
        return fields.replace(read(self), warnings=("of the active content",))

    monkeypatch.setattr(FeatureAccess, "active_content", active_content)
    data = at_offset_0(one_page(objects={4: URI, 5: b"null"}), 5)
    warnings = inspected(data).warnings
    assert len(warnings) == 2
    assert "(object 5 0): object has offset 0" in warnings[0]
    assert warnings[1] == "of the active content"


def test_an_object_stream():
    """Objects 4 and 5 compressed in object stream 7; cross-reference stream 8;
    stream 9, typed /ObjStm, holding nothing, which no entry names: a stream
    is counted by its /Type (I1)."""
    objects = {
        1: catalog(),
        2: PAGES,
        3: page(b" /Contents 6 0 R"),
        6: stream(BODY),
        9: stream(b"", b" /Type /ObjStm /N 0 /First 0"),
    }
    data = build_with_object_stream(objects, [(4, b"[1 2]"), (5, b"<< /A 1 >>")], {4: 0, 5: 1}, 1)
    assert inspected(data).objects == ObjectCounts(
        total=9,
        streams=1,
        object_streams=2,
        cross_reference_streams=1,
        dictionaries=4,
        arrays=1,
    )


def written_p(data: bytes) -> int:
    """/P as pikepdf wrote it in the encryption dictionary."""
    return written(data, b"P")


def written(data: bytes, key: bytes) -> int:
    """The integer `key` names in the encryption dictionary pikepdf wrote,
    outside /CF, whose crypt filters write a /Length of their own."""
    number = written_encrypt(data).object_number
    found = re.findall(rb"\n%d 0 obj\n(.*?)\nendobj" % number, data, re.S)
    assert len(found) == 1
    outside = re.sub(rb"/CF << /\w+ << [^>]*>> >>", b"", found[0])
    values = re.findall(rb"/%s (-?\d+)" % key, outside)
    assert len(values) == 1
    return int(values[0])


def bits(p: int, revision: int) -> Permissions:
    """The bits of /P as ISO 32000-2, Table 22, names them."""

    def bit(number: int) -> bool:
        return bool((p >> (number - 1)) & 1)

    later = revision >= 3
    return Permissions(
        print=bit(3),
        modify=bit(4),
        copy=bit(5),
        annotate=bit(6),
        fill_forms=bit(9) if later else None,
        accessibility=bit(10) if later else None,
        assemble=bit(11) if later else None,
        print_high_quality=bit(12) if later else None,
    )


def written_filters(data: bytes) -> tuple[CryptFilter, ...]:
    """The crypt filters pikepdf wrote in /CF: each with its /CFM."""
    return tuple(
        CryptFilter(name=name(key.decode()), cfm=name(method.decode()))
        for key, method in re.findall(rb"/(\w+) << /AuthEvent /DocOpen /CFM /(\w+)", data)
    )


def written_encrypt(data: bytes) -> ObjectRef:
    found = re.findall(rb"/Encrypt (\d+) 0 R", data)
    assert len(set(found)) == 1
    return ref(int(found[0]))


ENCRYPTED = one_page(page_entries=b" /Contents 4 0 R", objects={4: stream(BODY)})


@pytest.mark.parametrize(
    "options, method",
    [
        ({"R": 2, "aes": False, "metadata": False}, CryptMethod.RC4),
        ({"R": 3, "aes": False, "metadata": False}, CryptMethod.RC4),
        ({"R": 4, "aes": False, "metadata": False}, CryptMethod.RC4),
        ({"R": 4}, CryptMethod.AES),
        ({"R": 6}, CryptMethod.AES),
    ],
    ids=["revision 2", "revision 3", "revision 4, RC4", "revision 4, AES", "revision 6"],
)
def test_the_encryption(options, method):
    """pikepdf encrypts with the revision chosen, and its choices, /V,
    /Length and /P, are read from the bytes it wrote; the rest follows from
    ISO 32000-2, Tables 20, 21 and 25: /V 1 and 2 have no crypt filters, so
    their method is RC4; the key is 40 bits long for /V 1, 256 for /V 5, and
    /Length long otherwise."""
    data = encrypt(ENCRYPTED, user="", owner="o", **options)
    found = inspected(data)
    revision = options["R"]
    v = written(data, b"V")
    filters = written_filters(data)
    assert bool(filters) == (revision >= 4)
    std = name("StdCF") if filters else None
    assert found.encryption == Encryption(
        ref=written_encrypt(data),
        filter=name("Standard"),
        sub_filter=None,
        v=v,
        r=revision,
        length=written(data, b"Length"),
        p=written_p(data),
        permissions=bits(written_p(data), revision),
        encrypt_metadata=options.get("metadata", True) if b"/EncryptMetadata" in data else None,
        stream_filter=std,
        string_filter=std,
        embedded_file_filter=None,
        crypt_filters=filters,
        key_bits={1: 40, 5: 256}.get(v, written(data, b"Length")),
        stream_method=method,
        string_method=method,
        embedded_file_method=method,
        user_password_matched=True,
        owner_password_matched=False,
    )


FLAGS = (
    "print_lowres",
    "modify_other",
    "extract",
    "modify_annotation",
    "modify_form",
    "accessibility",
    "modify_assembly",
    "print_highres",
)


@pytest.mark.parametrize("off", FLAGS)
def test_each_permission(off):
    """Revision 3, every pikepdf flag allowed but one: each of the eight bits
    is told apart."""
    allow = pikepdf.Permissions(**{flag: flag != off for flag in FLAGS})
    data = encrypt(ENCRYPTED, user="", owner="o", R=3, aes=False, metadata=False, allow=allow)
    assert inspected(data).encryption.permissions == bits(written_p(data), 3)


def test_the_permissions_of_revision_2():
    """Revision 2 defines bits 3 to 6: printing and copying allowed, nothing else."""
    allow = pikepdf.Permissions(**{flag: flag in ("print_lowres", "extract") for flag in FLAGS})
    data = encrypt(ENCRYPTED, user="", owner="o", R=2, aes=False, metadata=False, allow=allow)
    permissions = inspected(data).encryption.permissions
    assert permissions == bits(written_p(data), 2)
    assert (permissions.print, permissions.modify, permissions.copy, permissions.annotate) == (
        True,
        False,
        True,
        False,
    )
    assert permissions.fill_forms is None


@pytest.mark.parametrize(
    "password, user, owner", [(None, True, False), ("o", False, True)], ids=["none", "the owner's"]
)
def test_which_password_matched(password, user, owner):
    found = inspected(encrypt(ENCRYPTED, user="", owner="o"), password)
    assert (found.encryption.user_password_matched, found.encryption.owner_password_matched) == (
        user,
        owner,
    )


def same_length(data: bytes, old: bytes, new: bytes) -> bytes:
    """`data` with `old` replaced by `new`, the difference taken from the
    encryption dictionary's /AuthEvent /DocOpen, its default, so that every
    offset stays."""
    edited = data.replace(old, new)
    gap = b"/AuthEvent /DocOpen "
    assert edited.count(gap) == 1
    edited = edited.replace(gap, b" " * (len(gap) - (len(new) - len(old))))
    assert len(edited) == len(data)
    return edited


RC4 = encrypt(ENCRYPTED, user="", owner="o", R=4, aes=False, metadata=False, compress=False)
NO_METHOD = "the /CFM of the crypt filter {} names is no method ISO 32000-2 defines"
HANDLER = (
    "the crypt filter {} names has /CFM /None or none, which leaves the decryption to "
    "the security handler, and PDFium takes it for RC4 where QPDF names its method none "
    "or unknown"
)
STD = name("StdCF")


def each(reason: UnknownReason, detail: str) -> tuple[Unknown, ...]:
    """The methods for streams, strings and embedded files, each unknown for
    the crypt filter its key names: /EFF, absent, names /StmF's."""
    return tuple(Unknown(reason, detail.format(key)) for key in ("/StmF", "/StrF", "/StmF"))


NO_NAME = Unknown(UnknownReason.INVALID, "/CFM is no name")


@pytest.mark.parametrize(
    "old, new, filters, defined, methods",
    [
        (
            b"/StmF /StdCF /StrF /StdCF",
            b"/StmF /Identity /StrF /Identity",
            (name("Identity"),) * 2,
            name("V2"),
            (CryptMethod.IDENTITY,) * 3,
        ),
        (
            b"/CFM /V2 ",
            b"",
            (STD,) * 2,
            None,
            each(UnknownReason.ENGINES_DISAGREE, HANDLER),
        ),
        (
            b"/CFM /V2 ",
            b"/CFM /None ",
            (STD,) * 2,
            name("None"),
            each(UnknownReason.ENGINES_DISAGREE, HANDLER),
        ),
        (
            b"/CFM /V2 ",
            b"/CFM /Foo ",
            (STD,) * 2,
            name("Foo"),
            each(UnknownReason.INVALID, NO_METHOD),
        ),
        (b"/CFM /V2 ", b"/CFM (V2) ", (STD,) * 2, NO_NAME, (NO_NAME,) * 3),
        (
            b"/StmF /StdCF /StrF /StdCF",
            b"/StmF (StdCF) /StrF (StdCF)",
            (
                Unknown(UnknownReason.INVALID, "/StmF is no name"),
                Unknown(UnknownReason.INVALID, "/StrF is no name"),
            ),
            name("V2"),
            (
                Unknown(UnknownReason.INVALID, "/StmF is no name"),
                Unknown(UnknownReason.INVALID, "/StrF is no name"),
                Unknown(UnknownReason.INVALID, "/StmF is no name"),
            ),
        ),
    ],
    ids=["/Identity", "no /CFM", "/CFM /None", "/CFM /Foo", "/CFM a string", "filters strings"],
)
def test_the_crypt_filters(old, new, filters, defined, methods):
    """Revision 4 with RC4, not compressed, edited at the same length: PDFium
    opens each (measured). ISO 32000-2: /Identity is no encryption (7.6.6);
    /CFM /None, its default, leaves the decryption to the security handler
    (Table 25), and the engines decrypt such a filter differently (measured);
    /Foo is no method; a string is no name, though PDFium reads it as one
    (measured)."""
    assert RC4.count(old) == 1
    found = inspected(same_length(RC4, old, new)).encryption
    assert (found.stream_filter, found.string_filter, found.embedded_file_filter) == (
        *filters,
        None,
    )
    assert found.crypt_filters == (CryptFilter(name=STD, cfm=defined),)
    assert (found.stream_method, found.string_method, found.embedded_file_method) == methods


@pytest.mark.parametrize(
    "options, old, new, key_bits",
    [
        ({"R": 4}, b"/CFM /AESV2 ", b"/CFM /AESV3 ", 128),
        ({"R": 6}, b"/CFM /AESV3 ", b"/CFM /AESV2 ", 256),
    ],
    ids=["/V 4 and /AESV3", "/V 5 and /AESV2"],
)
def test_aes_with_the_key_of_v(options, old, new, key_bits):
    """Each engine decrypts either /CFM with the key /V gives (measured): the
    method is AES, the key as long as ISO 32000-2, Table 20, makes it for /V."""
    data = encrypt(ENCRYPTED, user="", owner="o", **options)
    assert data.count(old) == 1
    found = inspected(data.replace(old, new)).encryption
    assert (found.stream_method, found.string_method, found.embedded_file_method) == (
        CryptMethod.AES,
    ) * 3
    assert found.key_bits == key_bits


def entries(**changed) -> object:
    """The backend's values of an encryption dictionary of revision 4, `changed` aside."""
    from pdfacts.backends.protocols import CryptFilterEntry, EncryptionEntries

    values = {
        "filter": b"Standard",
        "sub_filter": None,
        "v": 4,
        "r": 4,
        "length": 128,
        "length_invalid": False,
        "p": -4,
        "encrypt_metadata": None,
        "encrypt_metadata_invalid": False,
        "key_bits": 128,
        "user_password_matched": True,
        "owner_password_matched": False,
        "dictionary": (9, 0),
        "stream_filter": b"StdCF",
        "string_filter": b"StdCF",
        "file_filter": None,
        "crypt_filters": (CryptFilterEntry(name=b"StdCF", method=b"V2"),),
    }
    return EncryptionEntries(**(values | changed))


def test_the_filters_as_the_backend_reads_them():
    """/StmF /StdCF, /StrF /Identity and /EFF /StdCF, which PDFium opens no
    file with (measured), so read through the backend, as I1's tests read:
    each filter as written."""
    from pdfacts.backends.qpdf import QpdfDocument

    data = same_length(RC4, b"/StrF /StdCF", b"/StrF /Identity /EFF /StdCF")
    document = QpdfDocument.open(data, None)
    try:
        written = document.structure().encryption
    finally:
        document.close()
    filters = (written.stream_filter, written.string_filter, written.file_filter)
    assert filters == (b"StdCF", b"Identity", b"StdCF")


def test_crypt_filters_open_refuses():
    """PDFium opens no file whose /StmF and /StrF differ, nor one whose crypt
    filter /CF does not define (measured), so open() refuses them; the values
    come from the backend's here. /EFF names what /StmF names where absent."""
    from pdfacts.inspection.report import _encryption

    found = _encryption(
        entries(stream_filter=b"StdCG", string_filter=b"Identity", file_filter=None), misread=False
    )
    assert (found.stream_method, found.string_method, found.embedded_file_method) == (
        Unknown(UnknownReason.INVALID, "/StmF names a crypt filter /CF does not define"),
        CryptMethod.IDENTITY,
        Unknown(UnknownReason.INVALID, "/StmF names a crypt filter /CF does not define"),
    )
    found = _encryption(entries(file_filter=b"StdCG"), misread=False)
    assert found.embedded_file_method == Unknown(
        UnknownReason.INVALID, "/EFF names a crypt filter /CF does not define"
    )
    found = _encryption(
        entries(stream_filter=None, string_filter=None, file_filter=b"StdCF"), misread=False
    )
    assert (found.stream_method, found.string_method, found.embedded_file_method) == (
        CryptMethod.IDENTITY,
        CryptMethod.IDENTITY,
        CryptMethod.RC4,
    )


def test_entries_qpdf_may_have_dropped():
    """Where QPDF mis-read an object, an entry it finds absent is unknown, and
    so is a method that rests on one, on a crypt filter it does not find or on
    one it finds without /CFM; the crypt filters are unknown, for QPDF may have
    dropped one."""
    from pdfacts.backends.protocols import CryptFilterEntry
    from pdfacts.inspection.report import _encryption

    found = _encryption(
        entries(sub_filter=None, file_filter=None, stream_filter=b"StdCG"), misread=True
    )
    dropped = Unknown(UnknownReason.UNREADABLE, MISREAD_ENTRY)
    assert (found.sub_filter, found.embedded_file_filter, found.encrypt_metadata) == (dropped,) * 3
    assert (found.stream_method, found.string_method, found.embedded_file_method) == (
        dropped,
        CryptMethod.RC4,
        dropped,
    )
    assert (found.filter, found.length, found.ref) == (name("Standard"), 128, ref(9))
    assert _encryption(entries(filter=None), misread=True).filter == dropped
    assert found.crypt_filters == dropped
    found = _encryption(entries(length=None, stream_filter=None, string_filter=None), misread=True)
    assert (found.length, found.stream_filter, found.string_filter) == (dropped,) * 3
    assert (found.stream_method, found.string_method) == (dropped, dropped)
    stray = "a key of /CF is a name with a # not followed by two hexadecimal digits"
    two = (
        CryptFilterEntry(name=stray, method=b"V2"),
        CryptFilterEntry(name=b"StdCF", method=b"V2"),
    )
    found = _encryption(entries(crypt_filters=two), misread=True)
    assert (found.stream_method, found.string_method) == (CryptMethod.RC4,) * 2
    without = (CryptFilterEntry(name=b"StdCF", method=None),)
    found = _encryption(entries(crypt_filters=without), misread=True)
    assert (found.stream_method, found.string_method) == (dropped, dropped)
    found = _encryption(entries(crypt_filters=without), misread=False)
    assert found.stream_method.reason is UnknownReason.ENGINES_DISAGREE


def test_crypt_filters_found_by_their_bytes():
    """/StmF names Caf\xe9, /CF defines Caf\xc3\xa9: one text, two names."""
    from pdfacts.backends.protocols import CryptFilterEntry
    from pdfacts.inspection.report import _encryption

    filters = (CryptFilterEntry(name=b"Caf\xc3\xa9", method=b"V2"),)
    found = _encryption(
        entries(stream_filter=b"Caf\xe9", string_filter=b"Caf\xe9", crypt_filters=filters),
        misread=False,
    )
    assert found.stream_method == Unknown(
        UnknownReason.INVALID, "/StmF names a crypt filter /CF does not define"
    )


def test_an_eff_that_is_no_name():
    """/EFF (StdCF), a string, added at the same length: PDFium opens the
    file (measured); the filter and its method are unknown, invalid."""
    found = inspected(same_length(RC4, b"/StrF /StdCF", b"/StrF /StdCF /EFF (StdCF)")).encryption
    no_name = Unknown(UnknownReason.INVALID, "/EFF is no name")
    assert (found.embedded_file_filter, found.embedded_file_method) == (no_name, no_name)
    assert (found.stream_method, found.string_method) == (CryptMethod.RC4,) * 2


@pytest.mark.parametrize("v", [None, 3])
def test_a_v_qpdf_opens_no_file_with(v):
    """QPDF opens no file whose /V is absent, or 0, 3 or 6 (measured): should
    it ever give one, no method is made up."""
    from pdfacts.inspection.report import _encryption

    with pytest.raises(EngineError, match="none of 1, 2, 4 and 5"):
        _encryption(entries(v=v), misread=False)


def test_an_encryption_dictionary_qpdf_reads_as_null():
    """/Length 128 replaced by an integer beyond 64 bits, at the same length:
    QPDF warns that it reads the dictionary as null and drops the trailer's
    /Encrypt; PDFium opens the file and decrypts it (measured). open() finds
    the engines disagree on the encryption, and every value from QPDF is
    unknown, the inspection's too ("Object identity")."""
    data = same_length(RC4, b"/Length 128", b"/Length 99999999999999999999")
    reason = Unknown(
        UnknownReason.ENGINES_DISAGREE,
        "PDFium reads the file as encrypted, revision 4, QPDF as not encrypted",
    )
    assert inspected(data) == Inspection(
        size=reason,
        objects=reason,
        pages=reason,
        contents=reason,
        encryption=reason,
        active_content=reason,
        warnings=(),
    )


def test_the_crypt_filters_in_the_order_of_their_keys():
    """/Zz written before /StdCF: QPDF gives the keys of a dictionary in the
    order of their bytes."""
    found = inspected(same_length(RC4, b"/CF << /StdCF", b"/CF << /Zz << >> /StdCF")).encryption
    assert found.crypt_filters == (
        CryptFilter(name=STD, cfm=name("V2")),
        CryptFilter(name=name("Zz"), cfm=None),
    )


def test_an_encryption_dictionary_of_another_generation():
    data = with_generation(RC4, written_encrypt(RC4).object_number, 1)
    found = inspected(data).encryption
    assert found.ref == ref(written_encrypt(RC4).object_number, 1)
    assert found.stream_method == CryptMethod.RC4


def test_a_crypt_filter_named_identity():
    """/StmF and /StrF /Identity, and /CF's one filter renamed /Identity, at
    the same length: QPDF decrypts with that filter, PDFium takes /Identity
    for the identity filter and reads the data as written (measured)."""
    assert RC4.count(b"/CF << /StdCF") == 1
    renamed = RC4.replace(b"/CF << /StdCF", b"/CF << /Identity")
    data = same_length(renamed, b"/StmF /StdCF /StrF /StdCF", b"/StmF /Identity /StrF /Identity")
    gap = b" " * len(b"/AuthEvent /DocOpen ")
    data = data.replace(gap[: len(gap) - 6], gap[: len(gap) - 9], 1)
    assert len(data) == len(RC4)
    found = inspected(data).encryption
    disagree = Unknown(
        UnknownReason.ENGINES_DISAGREE,
        "/CF defines a crypt filter named /Identity, which PDFium takes for the identity "
        "filter and QPDF for that filter",
    )
    assert (found.stream_method, found.string_method, found.embedded_file_method) == (disagree,) * 3


def test_a_crypt_filter_named_identity_where_qpdf_misread():
    """Where QPDF mis-read an object, /CF may have lost a filter named
    /Identity: the method of /Identity is unknown, unless one is found."""
    from pdfacts.backends.protocols import CryptFilterEntry
    from pdfacts.inspection.report import _encryption

    identity = {"stream_filter": b"Identity", "string_filter": b"Identity"}
    found = _encryption(entries(**identity), misread=True)
    assert found.stream_method == Unknown(UnknownReason.UNREADABLE, MISREAD_ENTRY)
    named = (CryptFilterEntry(name=b"Identity", method=b"V2"),)
    found = _encryption(entries(crypt_filters=named, **identity), misread=True)
    assert found.stream_method.reason is UnknownReason.ENGINES_DISAGREE


def test_absent_filters_beside_a_crypt_filter_named_identity():
    """An absent /StmF is the identity filter whatever /CF defines; PDFium
    opens no such file at /V 4 (Facts), so the values are the backend's."""
    from pdfacts.backends.protocols import CryptFilterEntry
    from pdfacts.inspection.report import _encryption

    named = (CryptFilterEntry(name=b"Identity", method=b"V2"),)
    found = _encryption(
        entries(stream_filter=None, string_filter=None, crypt_filters=named), misread=False
    )
    assert (found.stream_method, found.string_method) == (CryptMethod.IDENTITY,) * 2


def test_a_crypt_filter_of_another_type():
    """/CF names an integer for /StdCF, which PDFium opens no file with
    (measured), so read through the backend, then made public."""
    from pdfacts.backends.qpdf import QpdfDocument
    from pdfacts.inspection.report import _encryption

    cf = b"/CF << /StdCF << /AuthEvent /DocOpen /CFM /V2 /Length 16 >> >>"
    assert RC4.count(cf) == 1
    document = QpdfDocument.open(RC4.replace(cf, b"/CF << /StdCF 5 >>".ljust(len(cf))), None)
    try:
        written = document.structure().encryption
    finally:
        document.close()
    found = _encryption(written, misread=False)
    no_dictionary = Unknown(
        UnknownReason.INVALID, "the crypt filter is no dictionary, so it has no /CFM"
    )
    assert found.crypt_filters == (CryptFilter(name=STD, cfm=no_dictionary),)
    assert found.stream_method == no_dictionary
    document = QpdfDocument.open(RC4.replace(cf, b"/CF 5".ljust(len(cf))), None)
    try:
        written = document.structure().encryption
    finally:
        document.close()
    found = _encryption(written, misread=True)
    no_cf = Unknown(UnknownReason.INVALID, "/CF is no dictionary")
    assert (found.crypt_filters, found.stream_method, found.string_method) == (no_cf,) * 3


@pytest.mark.parametrize(
    "written, reason",
    [
        (b"/CF << /StdCF 4 0 R >>", "the crypt filter is no dictionary, so it has no /CFM"),
        (b"/CF 4 0 R", "/CF is no dictionary"),
    ],
    ids=["a filter", "/CF"],
)
def test_a_crypt_filter_written_as_a_stream(written, reason):
    """The content stream 4 named for a crypt filter, or for /CF: QPDF takes
    no stream for either; PDFium reads a stream's dictionary there, and
    opens no file whose filter is this one, which names no method (measured),
    so read through the backend. The method is unknown, invalid."""
    from pdfacts.backends.qpdf import QpdfDocument
    from pdfacts.inspection.report import _encryption

    cf = b"/CF << /StdCF << /AuthEvent /DocOpen /CFM /V2 /Length 16 >> >>"
    assert written_encrypt(RC4).object_number != 4 and b"4 0 obj\n<< /Length 15 >>" in RC4
    document = QpdfDocument.open(RC4.replace(cf, written.ljust(len(cf))), None)
    try:
        found = _encryption(document.structure().encryption, misread=False)
    finally:
        document.close()
    assert found.stream_method == Unknown(UnknownReason.INVALID, reason)


def test_crypt_filters_that_share_a_value():
    """/CF's keys /A, /B and /C each with /CFM 5 0 R, one name; /D, /E and /F
    each naming one filter dictionary 6 0 R: each value is read once, and
    the keys share it, as they share what the file writes once; decoded once
    too."""
    import pikepdf

    from pdfacts.backends.qpdf import _crypt_filters
    from pdfacts.inspection.report import _encryption

    pdf = pikepdf.new()
    cfm = pdf.make_indirect(pikepdf.Name("/V2"))
    shared = pdf.make_indirect(pikepdf.Dictionary(CFM=pikepdf.Name("/AESV2")))
    defined = pikepdf.Dictionary(
        A=pikepdf.Dictionary(CFM=cfm),
        B=pikepdf.Dictionary(CFM=cfm),
        C=pikepdf.Dictionary(CFM=cfm),
        D=shared,
        E=shared,
        F=shared,
    )
    written = _crypt_filters(defined)
    assert [(one.name, one.method) for one in written] == [
        (b"A", b"V2"),
        (b"B", b"V2"),
        (b"C", b"V2"),
        (b"D", b"AESV2"),
        (b"E", b"AESV2"),
        (b"F", b"AESV2"),
    ]
    assert written[0].method is written[1].method is written[2].method
    assert written[3].method is written[4].method is written[5].method
    found = _encryption(entries(crypt_filters=written), misread=False).crypt_filters
    assert found[0].cfm is found[1].cfm is found[2].cfm
    assert found[3].cfm is found[4].cfm is found[5].cfm


def test_what_the_scan_reads_once(monkeypatch):
    """Actions 4, 5 and 6 write /S 9 0 R, dictionaries 7 and 8 /Type 10 0 R
    (/Action), the annotations 11 and 12 /Subtype 13 0 R: each name is read
    once, by the scan and the walk together. Dictionary 14 writes /S 10 0 R,
    the name /Action, which is no action type: a name read for /S and for
    /Type is read for each, apart."""
    from pdfacts.backends import qpdf

    calls: dict[str, int] = {}

    def counted(name, reader):
        def read(value):
            if hasattr(value, "is_indirect") and value.is_indirect:
                calls[name] = calls.get(name, 0) + 1
            return reader(value)

        return read

    for name in ("_action_kind", "_names_action", "_name_as_written"):
        monkeypatch.setattr(qpdf, name, counted(name, getattr(qpdf, name)))
    annotation = b"<< /Type /Annot /Subtype 13 0 R /Rect [0 0 1 1] /A 4 0 R >>"
    objects = {
        4: b"<< /S 9 0 R /JS (a) >>",
        5: b"<< /S 9 0 R /JS (a) >>",
        6: b"<< /S 9 0 R /JS (a) >>",
        7: b"<< /Type 10 0 R >>",
        8: b"<< /Type 10 0 R >>",
        9: b"/JavaScript",
        10: b"/Action",
        11: annotation,
        12: annotation,
        13: b"/Link",
        14: b"<< /S 10 0 R >>",
    }
    found = inspected(one_page(page_entries=b" /Annots [11 0 R 12 0 R]", objects=objects))
    assert [one.ref for one in found.active_content.actions] == [
        ref(4),
        ref(5),
        ref(6),
        ref(7),
        ref(8),
    ]
    assert calls == {"_action_kind": 2, "_names_action": 1, "_name_as_written": 1}


def test_a_key_of_cf_without_bytes():
    """/CF's key /S#zzCF, a stray #: QPDF cannot tell its bytes; read through
    the backend, as I1's tests read a /SubFilter so written."""
    from pdfacts.backends.qpdf import QpdfDocument

    document = QpdfDocument.open(same_length(RC4, b"/CF << /StdCF", b"/CF << /S#zzCF"), None)
    try:
        written = document.structure().encryption
    finally:
        document.close()
    stray = "a key of /CF is a name with a # not followed by two hexadecimal digits"
    assert [one.name for one in written.crypt_filters] == [stray]


def test_an_encryption_dictionary_in_the_trailer():
    """The dictionary of revision 4 written in the trailer itself, its object
    left where it was; PDFium and QPDF read it so (measured): it has no
    object."""
    number = written_encrypt(RC4).object_number
    body = re.findall(rb"\n%d 0 obj\n(.*?)\nendobj" % number, RC4, re.S)
    trailer = RC4.rindex(b"trailer")
    old = b"/Encrypt %d 0 R" % number
    assert len(body) == 1 and RC4[trailer:].count(old) == 1
    found = inspected(RC4[:trailer] + RC4[trailer:].replace(old, b"/Encrypt " + body[0]))
    assert found.encryption.ref is None
    assert found.encryption.stream_method == CryptMethod.RC4


@pytest.mark.parametrize(
    "options, old, new, values",
    [
        (
            {"R": 4, "aes": False, "metadata": False, "compress": False},
            b"/Length 128",
            b"/Length /12",
            {
                "length": Unknown(
                    UnknownReason.INVALID, "/Length of the encryption dictionary is no integer"
                )
            },
        ),
        (
            {"R": 6, "metadata": False},
            b"/EncryptMetadata false",
            b"/EncryptMetadata 12345",
            {"encrypt_metadata": Unknown(UnknownReason.INVALID, "/EncryptMetadata is no boolean")},
        ),
        (
            {"R": 6, "metadata": False},
            b"/EncryptMetadata false",
            b"/SubFilter /Abcdefghij",
            {"sub_filter": name("Abcdefghij"), "encrypt_metadata": None},
        ),
        (
            {"R": 6, "metadata": False},
            b"/EncryptMetadata false",
            b"/SubFilter (Abcdefghi)",
            {"sub_filter": Unknown(UnknownReason.INVALID, "/SubFilter is no name")},
        ),
    ],
    ids=[
        "/Length no integer",
        "/EncryptMetadata no boolean",
        "a /SubFilter",
        "a /SubFilter string",
    ],
)
def test_entries_of_the_dictionary(options, old, new, values):
    """An entry replaced by as many bytes; PDFium and QPDF open each (measured)."""
    data = encrypt(ENCRYPTED, user="", owner="o", **options)
    assert data.count(old) == 1 and len(old) == len(new)
    found = inspected(data.replace(old, new))
    for key, value in values.items():
        assert getattr(found.encryption, key) == value
    assert isinstance(found.objects, ObjectCounts)


def test_a_sub_filter_without_bytes():
    """Revision 6, /EncryptMetadata false replaced by /SubFilter /Abcde#zzij, as
    many bytes: QPDF warns of the encryption dictionary's object, so it
    mis-read one (measured, I1); the encryption is given, its /SubFilter
    unknown, invalid, and /EncryptMetadata, absent, unknown."""
    data = encrypt(ENCRYPTED, user="", owner="o", R=6, metadata=False)
    old, new = b"/EncryptMetadata false", b"/SubFilter /Abcde#zzij"
    assert data.count(old) == 1 and len(old) == len(new)
    found = inspected(data.replace(old, new))
    assert found.encryption.sub_filter.reason is UnknownReason.INVALID
    assert found.encryption.encrypt_metadata == Unknown(UnknownReason.UNREADABLE, MISREAD_ENTRY)
    assert found.encryption.stream_method == CryptMethod.AES
    misread = Unknown(UnknownReason.UNREADABLE, MISREAD)
    assert (found.objects, found.pages, found.active_content) == (misread, misread, misread)


def test_a_misread_object():
    """Object 5, which no page names, writes /Length 10 for 15 bytes: QPDF
    warns that it mis-read it. The file is not encrypted, as open() found both
    engines to read it."""
    objects = {4: stream(BODY), 5: UNREADABLE_LENGTH, 6: JS}
    found = inspected(one_page(b" /OpenAction 6 0 R", b" /Contents 4 0 R", objects))
    misread = Unknown(UnknownReason.UNREADABLE, MISREAD)
    assert (found.objects, found.pages, found.active_content) == (misread, misread, misread)
    assert (found.size, found.encryption) == (7, None)
    assert [message[message.index("(object") :].split(",")[0] for message in found.warnings] == [
        "(object 5 0"
    ]


def test_pages_qpdf_found_otherwise():
    """The page tree lists page 3 twice: QPDF copies the second, so its pages
    are not the file's objects (test_backend_qpdf_structure)."""
    objects = {
        1: catalog(b" /OpenAction 4 0 R /AcroForm << /XFA 5 0 R >>"),
        2: b"<< /Type /Pages /Kids [3 0 R 3 0 R] /Count 2 >>",
        3: page(),
        4: JS,
        5: stream(b"<xdp/>"),
        6: b"<< /Type /Filespec /F (a) /EF << /F 7 0 R >> >>",
        7: stream(b"a"),
    }
    found = inspected(build(objects, 1))
    assert found.pages == Unknown(UnknownReason.UNREADABLE, NOT_AS_WRITTEN)
    content = found.active_content
    unknown = Unknown(
        UnknownReason.UNREADABLE,
        "the pages QPDF found are not the objects the file's page tree lists, so "
        "where an action is named to run is not known",
    )
    assert (content.actions, content.sequences, content.triggers) == (unknown,) * 3
    assert content.xfa == (ref(5),)
    assert content.embedded_files == (EmbeddedFile(specification=ref(6), streams=0),)
    assert content.embedded_streams == ((ref(7),),)
    assert isinstance(found.objects, ObjectCounts)


def test_where_every_value_of_qpdf_is_unknown():
    """Object 4's entry names index 1 of object stream 7, whose header names
    it at index 0: the check of object streams fails, and every value of QPDF
    is unknown ("Object identity")."""
    objects = {1: catalog(), 2: PAGES, 3: page()}
    data = build_with_object_stream(objects, [(4, b"[1 2]"), (5, b"[3]")], {4: 1, 5: 1}, 1)
    reason = Unknown(
        UnknownReason.INVALID,
        "an object stream cannot be read, names object 0 or an object twice, or holds an "
        "object at another index than the cross-reference gives, so QPDF and PDFium may "
        "read different objects",
    )
    with pdfacts.open(data) as document:
        found = inspect(document)
    assert found == Inspection(
        size=reason,
        objects=reason,
        pages=reason,
        contents=reason,
        encryption=reason,
        active_content=reason,
        warnings=(),
    )


def test_the_active_content():
    """/OpenAction 4, whose /Next names 5; the link 6 on the page with /A 7, at
    index 1 of /Annots; the action 8 no entry names, its /S a Latin-1 name;
    the file specification 9 whose /EF names stream 10; /XFA [(a) 11 0 R]."""
    objects = {
        4: b"<< /S /JavaScript /JS (a) /Next 5 0 R >>",
        5: URI,
        6: b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A 7 0 R >>",
        7: b"<< /S /GoTo /D [3 0 R /Fit] >>",
        8: b"<< /Type /Action /S /Caf#E9 >>",
        9: b"<< /Type /Filespec /F (a) /EF << /F 10 0 R >> >>",
        10: stream(b"a"),
        11: stream(b"<xdp/>"),
    }
    written = b" /OpenAction 4 0 R /AcroForm << /XFA [(a) 11 0 R] >>"
    found = inspected(one_page(written, b" /Annots [null 6 0 R]", objects)).active_content
    latin = Name(raw=b"Caf\xe9", text="Caf\xe9", encoding=NameEncoding.LATIN1)
    assert found == ActiveContent(
        actions=(
            Action(kind=name("JavaScript"), ref=ref(4), next=0),
            Action(kind=name("URI"), ref=ref(5), next=None),
            Action(kind=name("GoTo"), ref=ref(7), next=None),
            Action(kind=latin, ref=ref(8), next=None),
        ),
        sequences=((1,),),
        triggers=(
            ActionTrigger(
                place=ActionPlace.DOCUMENT,
                owner=ref(1),
                page_index=None,
                annotation_index=None,
                event="OpenAction",
                action=0,
            ),
            ActionTrigger(
                place=ActionPlace.ANNOTATION,
                owner=ref(6),
                page_index=0,
                annotation_index=1,
                event="A",
                action=2,
            ),
        ),
        xfa=(ref(11),),
        embedded_files=(EmbeddedFile(specification=ref(9), streams=0),),
        embedded_streams=((ref(10),),),
    )


NOT_PAIRS = Unknown(
    UnknownReason.INVALID, "/XFA is an array, but not of pairs of a packet's name and its stream"
)


@pytest.mark.parametrize(
    "written, objects, expected",
    [
        (b"", {}, None),
        (b" /AcroForm << /XFA [] >>", {}, ()),
        (b" /AcroForm << /XFA [(a) 5 0 R (b)] >>", {5: stream(b"<a/>")}, NOT_PAIRS),
        (b" /AcroForm << /XFA [(a) 5 0 R] >>", {5: b"<< /A 1 >>"}, NOT_PAIRS),
        (b" /AcroForm << /XFA [/a 5 0 R] >>", {5: stream(b"<a/>")}, NOT_PAIRS),
        (
            b" /AcroForm << /XFA /Foo >>",
            {},
            Unknown(UnknownReason.INVALID, "/XFA is neither a stream nor an array"),
        ),
    ],
    ids=[
        "no /XFA",
        "an /XFA without streams",
        "a name without its stream",
        "a dictionary where a stream is due",
        "a name where a string is due",
        "an /XFA of another type",
    ],
)
def test_the_xfa(written, objects, expected):
    assert inspected(one_page(written, objects=objects)).active_content.xfa == expected


@pytest.mark.parametrize(
    "written, objects, place, event",
    [
        (
            b" /Names << /JavaScript << /Names [(a) 4 0 R] >> >>",
            {4: JS},
            "document-javascript",
            None,
        ),
        (
            b" /Outlines 5 0 R",
            {5: b"<< /First 6 0 R >>", 6: b"<< /Title (a) /A " + JS + b" >>"},
            "outline-item",
            "A",
        ),
        (
            b" /AcroForm << /Fields [7 0 R] >>",
            {7: b"<< /FT /Tx /T (t) /AA << /K " + JS + b" >> >>"},
            "field",
            "K",
        ),
    ],
    ids=["the name tree", "an outline item", "a field"],
)
def test_the_places(written, objects, place, event):
    """The place as a value of ActionPlace, the event as text; no page."""
    trigger = inspected(one_page(written, objects=objects)).active_content.triggers[0]
    assert (trigger.place.value, trigger.event) == (place, event)
    assert (trigger.page_index, trigger.annotation_index) == (None, None)


def test_the_entries_of_fields():
    """The widget 5, on the page at index 0 of /Annots and in /Fields, and the
    field 6, in /Fields only, each with /AA << /K /E >>: K is a field's event,
    E an annotation's; a field no page lists has no page."""
    aa = b" /AA << /K " + JS + b" /E " + JS + b" >>"
    objects = {
        5: b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (a) /Rect [0 0 10 10] /P 3 0 R"
        + aa
        + b" >>",
        6: b"<< /FT /Tx /T (b)" + aa + b" >>",
    }
    found = inspected(
        one_page(b" /AcroForm << /Fields [5 0 R 6 0 R] >>", b" /Annots [5 0 R]", objects)
    ).active_content
    assert [
        (one.place, one.owner, one.page_index, one.annotation_index, one.event)
        for one in found.triggers
    ] == [
        (ActionPlace.ANNOTATION, ref(5), 0, 0, "E"),
        (ActionPlace.FIELD, ref(5), 0, 0, "K"),
        (ActionPlace.FIELD, ref(6), None, None, "K"),
        (ActionPlace.ANNOTATION, ref(6), None, None, "E"),
    ]


def test_the_deadline_between_the_two_reads(monkeypatch):
    """Checked again before the active content is read, every object read a
    second time: here the clock passes it while the structure is read."""
    from pdfacts.core.document import FeatureAccess

    now = [0.0]
    read = FeatureAccess.structure

    def structure(self):
        found = read(self)
        now[0] = 6.0
        return found

    def active_content(self):
        raise AssertionError("the active content read")

    monkeypatch.setattr(FeatureAccess, "structure", structure)
    monkeypatch.setattr(FeatureAccess, "active_content", active_content)
    with pdfacts.open(
        one_page(), limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    ) as document:
        with pytest.raises(LimitExceededError):
            inspect(document)


def test_no_second_read_where_qpdf_misread(monkeypatch):
    """Where QPDF mis-read an object, the active content is unknown, and its
    view, which would read every object again, is not opened."""
    from pdfacts.core.document import FeatureAccess

    def active_content(self):
        raise AssertionError("the active content read")

    monkeypatch.setattr(FeatureAccess, "active_content", active_content)
    objects = {4: stream(BODY), 5: UNREADABLE_LENGTH}
    found = inspected(one_page(page_entries=b" /Contents 4 0 R", objects=objects))
    assert found.active_content == Unknown(UnknownReason.UNREADABLE, MISREAD)


def test_the_deadline(monkeypatch):
    """Checked when inspect() begins, as for the other features: before the
    structure is read."""
    from pdfacts.core.document import FeatureAccess

    def structure(self):
        raise AssertionError("the structure read")

    now = [0.0]
    with pdfacts.open(
        one_page(), limits=Limits(deadline_seconds=5), _clock=lambda: now[0]
    ) as document:
        monkeypatch.setattr(FeatureAccess, "structure", structure)
        now[0] = 6.0
        with pytest.raises(LimitExceededError) as raised:
            inspect(document)
    assert (raised.value.limit, raised.value.allowed, raised.value.found) == (
        "deadline_seconds",
        5,
        6.0,
    )


@pytest.mark.parametrize(
    "written, kind",
    [
        (b"/S (URI) /URI (u)", Unknown(UnknownReason.INVALID, "/S of the action is no name")),
        (b"/JS (a)", None),
    ],
    ids=["a string", "absent"],
)
def test_a_kind_that_is_no_name(written, kind):
    """/OpenAction's action, its /S a string, or absent: listed, its kind
    unknown, invalid, or none."""
    found = inspected(one_page(b" /OpenAction << " + written + b" >>")).active_content
    assert found.actions == (Action(kind=kind, ref=ref(1), next=None),)


def test_the_indices_of_sequences_and_streams():
    """Actions 4 and 6 with /Next 5 and 7, each its own; file specifications 8
    and 10 with an /EF each: each /Next and each /EF has an index of its own."""
    objects = {
        4: b"<< /S /JavaScript /JS (a) /Next 5 0 R >>",
        5: URI,
        6: b"<< /S /JavaScript /JS (b) /Next 7 0 R >>",
        7: URI,
        8: b"<< /Type /Filespec /F (a) /EF << /F 9 0 R >> >>",
        9: stream(b"a"),
        10: b"<< /Type /Filespec /F (b) /EF << /F 11 0 R >> >>",
        11: stream(b"b"),
    }
    entries = b" /OpenAction 4 0 R /Names << /JavaScript << /Names [(b) 6 0 R] >> >>"
    found = inspected(one_page(entries, objects=objects)).active_content
    assert [(one.ref, one.next) for one in found.actions] == [
        (ref(4), 0),
        (ref(5), None),
        (ref(6), 1),
        (ref(7), None),
    ]
    assert found.sequences == ((1,), (3,))
    assert found.embedded_files == (
        EmbeddedFile(specification=ref(8), streams=0),
        EmbeddedFile(specification=ref(10), streams=1),
    )
    assert found.embedded_streams == ((ref(9),), (ref(11),))


def test_what_entries_share():
    """/XFA [(a) 5 0 R (b) 5 0 R] names stream 5 twice; the catalog holds
    /OpenAction and the event WC of its /AA: each object is one value, the
    backend's and the public, however many entries name it."""
    from pdfacts.backends.qpdf import QpdfDocument

    written = b" /OpenAction " + JS + b" /AA << /WC " + JS + b" >>"
    written += b" /AcroForm << /XFA [(a) 5 0 R (b) 5 0 R] >>"
    data = one_page(written, objects={5: stream(b"<xdp/>")})
    found = inspected(data).active_content
    assert found.xfa == (ref(5), ref(5))
    assert found.xfa[0] is found.xfa[1]
    assert [one.owner for one in found.triggers] == [ref(1), ref(1)]
    assert found.triggers[0].owner is found.triggers[1].owner is found.actions[0].ref
    document = QpdfDocument.open(data, None)
    try:
        xfa = document.active_content().xfa
    finally:
        document.close()
    assert xfa[0] is xfa[1]


def test_a_kind_that_actions_share():
    """Actions 4, 5 and 6 write /S 9 0 R, the name /JavaScript an object of
    its own; 5 and 6 no entry names. The name is read once, and the actions
    share its value, as they share what the file writes once."""
    from pdfacts.backends.qpdf import QpdfDocument

    action = b"<< /S 9 0 R /JS (a) >>"
    objects = {4: action, 5: action, 6: action, 9: b"/JavaScript"}
    data = one_page(b" /OpenAction 4 0 R", objects=objects)
    found = inspected(data).active_content
    assert [one.kind for one in found.actions] == [name("JavaScript")] * 3
    assert found.actions[0].kind is found.actions[1].kind is found.actions[2].kind
    document = QpdfDocument.open(data, None)
    try:
        written = document.active_content().actions
    finally:
        document.close()
    assert written[0].kind is written[1].kind is written[2].kind


def test_a_page_event():
    found = inspected(one_page(page_entries=b" /AA << /O " + JS + b" >>")).active_content
    assert found.triggers == (
        ActionTrigger(
            place=ActionPlace.PAGE,
            owner=ref(3),
            page_index=0,
            annotation_index=None,
            event="O",
            action=0,
        ),
    )


def at_offset_0(data: bytes, number: int) -> bytes:
    """`data` with object `number` listed at offset 0 in the table."""
    table = data.rindex(b"\nxref\n0 ") + 1
    lines = data[table:].split(b"\n")
    lines[2 + number] = b"0000000000 00000 n "
    return data[:table] + b"\n".join(lines)


def test_a_warning_that_is_no_misreading():
    """Object 5, which no object names, is listed at offset 0: QPDF warns,
    naming no offset, which is no mis-read object; the inspection gives its
    values and the warning, once."""
    found = inspected(at_offset_0(one_page(objects={4: URI, 5: b"null"}), 5))
    assert isinstance(found.objects, ObjectCounts)
    assert len(found.warnings) == 1
    assert "(object 5 0): object has offset 0" in found.warnings[0]


def test_the_warnings_in_the_order_qpdf_gave_them():
    """Objects 9 and 10, which no object names, at offset 0: QPDF warns of 9
    first, whose warning sorts after 10's."""
    objects = {number: b"null" for number in range(4, 11)}
    data = at_offset_0(at_offset_0(one_page(objects=objects), 9), 10)
    assert [
        message[message.index("(object") :].split(")")[0] for message in inspected(data).warnings
    ] == [
        "(object 9 0",
        "(object 10 0",
    ]


@pytest.mark.parametrize(
    "data",
    [
        one_page(page_entries=b" /Contents 4 0 R", objects={4: stream(BODY), 5: UNREADABLE_LENGTH}),
        at_offset_0(one_page(page_entries=b" /CropBox 5 0 R", objects={5: b"[0 0 100 100]"}), 5),
    ],
    ids=["a stream QPDF cannot read, named by no page", "a page's /CropBox at offset 0"],
)
def test_the_inspection_changes_no_other_answer(data):
    """Reading every object, and nothing of the pages but their objects, in
    views of its own: the document's warnings are as they were before."""
    with pdfacts.open(data) as document:
        before = document.warnings
        inspect(document)
        after = document.warnings
    assert before == after


def test_an_encrypted_file_opened_with_the_users_password():
    data = encrypt(
        one_page(b" /OpenAction " + JS, b" /Contents 4 0 R", {4: stream(BODY)}), user="u", owner="o"
    )
    found = inspected(data, "u")
    matched = (found.encryption.user_password_matched, found.encryption.owner_password_matched)
    assert matched == (True, False)
    assert [action.kind.text for action in found.active_content.actions] == ["JavaScript"]


def test_no_document():
    with pytest.raises(TypeError, match="a document of pdfacts, not bytes"):
        inspect(one_page())


def test_the_names():
    """The function and its package do not share a name (decision 61)."""
    import pdfacts.inspection.report as report

    assert pdfacts.inspect is report.inspect
    assert pdfacts.capabilities()[pdfacts.Capability.INSPECTION] is True
    for exported in (
        "inspect",
        "Inspection",
        "ObjectCounts",
        "PageContents",
        "ContentsStream",
        "Contents",
        "ContentsForm",
        "Encryption",
        "CryptFilter",
        "Permissions",
        "CryptMethod",
        "ActiveContent",
        "Action",
        "ActionTrigger",
        "ActionPlace",
        "EmbeddedFile",
        "ObjectRef",
    ):
        assert exported in pdfacts.__all__


def test_the_values_of_the_enumerations():
    """The values are part of the interface; this test fixes them."""
    assert [one.value for one in ContentsForm] == [
        "stream",
        "array",
        "indirect-array",
        "absent",
        "other",
    ]
    assert [one.value for one in CryptMethod] == ["identity", "rc4", "aes"]
    assert [one.value for one in ActionPlace] == [
        "document",
        "document-javascript",
        "page",
        "annotation",
        "field",
        "outline-item",
    ]


def test_the_values_are_frozen():
    data = encrypt(ENCRYPTED, user="", owner="o")
    found = inspected(data)
    values = [found, found.objects, found.pages[0], found.contents[0], found.encryption]
    values += [found.contents[0].streams[0]]
    values += [found.encryption.permissions, found.encryption.crypt_filters[0], ref(1)]
    values += [
        NOTHING,
        Action(kind=None, ref=ref(1), next=None),
        ActionTrigger(
            place=ActionPlace.DOCUMENT,
            owner=ref(1),
            page_index=None,
            annotation_index=None,
            event="OpenAction",
            action=0,
        ),
        EmbeddedFile(specification=ref(1), streams=0),
    ]
    for value in values:
        field = dataclasses.fields(value)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, None)
