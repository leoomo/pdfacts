"""decrypt(): an encrypted file written anew without encryption, and checked.

Inputs are built with the byte-level builder and encrypted by pikepdf
(tests/synthetic/encryption.py); the expected values are what was built: the
page's box, the widget's name, value and rectangle, the content, and the
passwords chosen (AGENTS.md, rule 2). The checks are shown to catch results
made wrong on purpose: the backend is replaced by one that returns a file
built otherwise.
"""

from __future__ import annotations

import dataclasses
import io
import re
import time

import pikepdf
import pytest

import pdfacts
from pdfacts import (
    Decryption,
    DecryptionFailedError,
    EngineError,
    LimitExceededError,
    Limits,
    NotEncryptedError,
    PasswordRequiredError,
)
from pdfacts.backends import qpdf as qpdf_backend
from pdfacts.backends.protocols import (
    CryptFilterEntry,
    DecryptedBytes,
    EncryptionEntries,
    EngineFailure,
    FailureKind,
)
from pdfacts.core import decryption
from synthetic.builder import append_update, build, stream
from synthetic.encryption import encrypt

BODY = b"0 0 m 100 100 l S"
BOX = [0, 0, 200, 200]
RECT = [10, 10, 100, 30]


def plain(
    *,
    box: bytes = b"[0 0 200 200]",
    rect: bytes = b"[10 10 100 30]",
    body: bytes = BODY,
    pages: int = 1,
    annotation: bool = True,
    extra: dict[int, bytes] | None = None,
    catalog: bytes = b"",
    page_entries: bytes = b"",
    resources: bytes = b"<< >>",
    last_box: bytes | None = None,
) -> bytes:
    """A form of `pages` pages that share the content `body`, the first with
    one text field's widget, object 5, named Name, its value Value."""
    kids = b" ".join(b"%d 0 R" % (10 + index) for index in range(pages))
    fields = b" /AcroForm << /Fields [5 0 R] >>" if annotation else b""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R" + fields + catalog + b" >>",
        2: b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % pages,
        4: stream(body),
    }
    if annotation:
        objects[5] = (
            b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (Name) /V (Value) /Rect "
            + rect
            + b" /P 10 0 R >>"
        )
    for index in range(pages):
        annots = b" /Annots [5 0 R]" if annotation and index == 0 else b""
        objects[10 + index] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox "
            + (last_box if last_box is not None and index == pages - 1 else box)
            + b" /Resources "
            + resources
            + b" /Contents 4 0 R"
            + annots
            + page_entries
            + b" >>"
        )
    objects.update(extra or {})
    return build(objects, 1)


ENCRYPTED = encrypt(plain(), user="", owner="o")


def reopened(data: bytes) -> pikepdf.Pdf:
    return pikepdf.open(io.BytesIO(data), attempt_recovery=False)


def assert_built(data: bytes) -> None:
    """The result as QPDF reads it: not encrypted, and what was built."""
    with reopened(data) as pdf:
        assert not pdf.is_encrypted
        assert "/Encrypt" not in pdf.trailer
        page = pdf.pages[0]
        assert [int(value) for value in page.MediaBox] == BOX
        assert page.Contents.read_bytes() == BODY
        widget = page.Annots[0]
        assert (str(widget.T), str(widget.V)) == ("Name", "Value")
        assert [int(value) for value in widget.Rect] == RECT


def returning(monkeypatch, data: bytes, warnings: tuple[str, ...] = ()) -> None:
    """The backend replaced by one that writes `data`, reporting `warnings`."""

    def rewrite(source: bytes, password: str | None) -> DecryptedBytes:
        return DecryptedBytes(data=data, warnings=warnings)

    monkeypatch.setattr(qpdf_backend, "rewrite_decrypted", rewrite)


def same_length(data: bytes, old: bytes, new: bytes) -> bytes:
    """`data` with `old` replaced by `new`, the difference taken from the
    encryption dictionary's /AuthEvent /DocOpen, its default, so that every
    offset stays."""
    assert data.count(old) == 1
    edited = data.replace(old, new)
    gap = b"/AuthEvent /DocOpen "
    assert edited.count(gap) == 1
    edited = edited.replace(gap, b" " * (len(gap) - (len(new) - len(old))))
    assert len(edited) == len(data)
    return edited


def updated(data: bytes, objects: dict[int, bytes], trailer_entries: bytes = b"") -> bytes:
    """The encrypted file `data` and an update that writes `objects` anew, its
    trailer naming the file's encryption dictionary and /ID, and holding
    `trailer_entries`. Nothing in them is encrypted."""
    trailer = data[data.rindex(b"trailer") :]
    named = re.findall(rb"/Encrypt \d+ 0 R", trailer) + re.findall(rb"/ID ?\[.*?\]", trailer, re.S)
    assert len(named) == 2 and b"/Root 1 0 R" in trailer
    result = append_update(data, objects, 1)
    at = result.rindex(b"/Root 1 0 R")
    return result[:at] + b" ".join(named) + b" " + trailer_entries + result[at:]


def written_object(data: bytes, pattern: bytes) -> tuple[int, bytes]:
    """The number and the dictionary of the one object of `data` whose
    dictionary matches `pattern`, as pikepdf writes it: on one line."""
    found = [
        (int(number), dictionary)
        for number, dictionary in re.findall(rb"\n(\d+) 0 obj\n(<<[^\n]*>>)\n", data)
        if re.search(pattern, dictionary)
    ]
    assert len(found) == 1
    return found[0]


# --- what it writes --------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        {"R": 2, "aes": False, "metadata": False},
        {"R": 3, "aes": False, "metadata": False},
        {"R": 4, "aes": False, "metadata": False},
        {"R": 4},
        {"R": 6},
    ],
    ids=["revision 2", "revision 3", "revision 4, RC4", "revision 4, AES", "revision 6"],
)
def test_each_revision(options):
    result = pdfacts.decrypt(encrypt(plain(), user="", owner="o", **options))
    assert result.page_count == 1
    assert_built(result.data)


def test_the_page_count():
    assert pdfacts.decrypt(encrypt(plain(pages=3), user="", owner="o")).page_count == 3


@pytest.mark.parametrize("object_streams", [False, True], ids=["none", "object streams"])
def test_object_streams_as_the_input_has_them(object_streams):
    data = encrypt(plain(), user="", owner="o", object_streams=object_streams)
    result = pdfacts.decrypt(data)
    assert_built(result.data)
    with reopened(result.data) as pdf:
        assert not pdf.is_linearized
        found = any(
            isinstance(obj, pikepdf.Stream) and obj.get("/Type") == "/ObjStm" for obj in pdf.objects
        )
        assert found is object_streams


def test_the_result_opens_without_a_password_and_without_warnings():
    with pdfacts.open(pdfacts.decrypt(ENCRYPTED).data) as document:
        assert document.warnings == ()
        assert document.page_count == 1


def test_the_metadata_is_left_as_it_is():
    """/EncryptMetadata false, and an update that writes the metadata anew,
    unencrypted, its packet naming another PDF version than the header: the
    result holds the packet as built. pikepdf, which encrypts the input,
    writes a packet of its own; the update comes after it."""
    packet = (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        b'rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" pdf:PDFVersion="1.3"/>'
        b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
    entries = b"/Type /Metadata /Subtype /XML"
    built = plain(catalog=b" /Metadata 6 0 R", extra={6: stream(b"<?xpacket?>", entries)})
    data = encrypt(built, user="", owner="o", metadata=False, compress=False)
    assert data.startswith(b"%PDF-1.7")
    number, _ = written_object(data, rb"/Subtype /XML")
    result = pdfacts.decrypt(updated(data, {number: stream(packet, entries)}))
    with reopened(result.data) as pdf:
        assert pdf.Root.Metadata.read_bytes() == packet


def test_an_inherited_box_stays_where_it_is_written():
    """An update that moves the media box from the page to the page tree
    node, which the page inherits it from: the result writes it there too.
    pikepdf, which encrypts the input, writes inherited attributes on each
    page."""
    data = encrypt(plain(), user="", owner="o")
    tree, kids = written_object(data, rb"/Type /Pages")
    page, entries = written_object(data, rb"/Type /Page\b(?!s)")
    box = b" /MediaBox [ 0 0 200 200 ]"
    assert entries.count(box) == 1
    moved = {tree: kids.replace(b" >>", box + b" >>"), page: entries.replace(box, b"")}
    result = pdfacts.decrypt(updated(data, moved))
    with pikepdf.open(io.BytesIO(result.data), inherit_page_attributes=False) as pdf:
        assert "/MediaBox" not in pdf.pages[0].obj
        assert [int(value) for value in pdf.Root.Pages.MediaBox] == BOX


def test_the_input_is_not_changed():
    data = bytes(ENCRYPTED)
    pdfacts.decrypt(data)
    assert data == ENCRYPTED


def test_the_same_input_gives_the_same_bytes():
    # QPDF would make /ID from the current second: the two runs straddle one.
    first = pdfacts.decrypt(ENCRYPTED).data
    time.sleep(1.05 - time.time() % 1)
    assert pdfacts.decrypt(ENCRYPTED).data == first


def test_the_id_keeps_its_first_part():
    """QPDF keeps the first part of /ID and makes the second from the content
    (measured); the first is read from the input's bytes."""
    trailer = ENCRYPTED[ENCRYPTED.rindex(b"trailer") :]
    written = re.findall(rb"/ID ?\[ ?<([0-9a-fA-F]+)> ?<([0-9a-fA-F]+)>", trailer)
    assert len(written) == 1
    with reopened(pdfacts.decrypt(ENCRYPTED).data) as pdf:
        first, second = (bytes(part).hex() for part in pdf.trailer.ID)
    assert first == written[0][0].decode().lower()
    assert second != written[0][1].decode().lower()


def test_decryption_is_frozen_and_exported():
    result = pdfacts.decrypt(ENCRYPTED)
    assert [field.name for field in dataclasses.fields(Decryption)] == ["data", "page_count"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.page_count = 2  # type: ignore[misc]
    assert "data" not in repr(result)
    assert {"decrypt", "Decryption", "DecryptionFailedError", "NotEncryptedError"} <= set(
        pdfacts.__all__
    )


def test_decryption_is_a_capability():
    assert pdfacts.capabilities()[pdfacts.Capability.DECRYPTION] is True


# --- passwords --------------------------------------------------------------------------


USER = encrypt(plain(), user="user", owner="owner")


def test_a_user_password_set_and_none_given_is_refused():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.decrypt(USER)
    assert raised.value.password_was_given is False


def test_a_wrong_password_is_refused():
    with pytest.raises(PasswordRequiredError) as raised:
        pdfacts.decrypt(USER, password="wrong")
    assert raised.value.password_was_given is True


def test_either_password_decrypts_the_file():
    by_user = pdfacts.decrypt(USER, password="user").data
    by_owner = pdfacts.decrypt(USER, password="owner").data
    assert_built(by_user)
    assert_built(by_owner)
    assert by_owner == by_user


# --- what it refuses -----------------------------------------------------------------------


def test_a_file_that_is_not_encrypted():
    with pytest.raises(NotEncryptedError):
        pdfacts.decrypt(plain())


def test_a_damaged_file_is_refused_as_open_refuses_it():
    with pytest.raises(pdfacts.DamagedFileError):
        pdfacts.decrypt(ENCRYPTED[:-20])


def hidden_by_an_update(trailer: bytes = b"") -> bytes:
    """Revision 4 with RC4, and an update that writes object `/Size` anew,
    `trailer` added to its trailer, which writes no /Encrypt."""
    data = encrypt(plain(), user="", owner="o", R=4, aes=False, metadata=False)
    sizes = {int(size) for size in re.findall(rb"/Size (\d+)", data)}
    assert len(sizes) == 1
    updated = append_update(data, {sizes.pop(): b"42"}, 1)
    at = updated.rindex(b"/Root 1 0 R")
    return updated[:at] + trailer + updated[at:]


def test_an_encryption_hidden_from_qpdf():
    """PDFium decrypts the file, QPDF reads it as not encrypted
    (docs/conventions.md, "Object identity")."""
    with pytest.raises(DecryptionFailedError, match="every value from QPDF is unknown"):
        pdfacts.decrypt(hidden_by_an_update())


@pytest.mark.parametrize("written", [b"null", b"99 0 R"], ids=["null", "a reference to no object"])
def test_an_encryption_hidden_from_both_engines(written):
    """Neither engine decrypts the file, and it holds the encryption dictionary."""
    with pytest.raises(DecryptionFailedError, match="no trailer names"):
        pdfacts.decrypt(hidden_by_an_update(b"/Encrypt " + written + b" "))


RC4 = encrypt(plain(), user="", owner="o", R=4, aes=False, metadata=False, compress=False)


def without_cfm(data: bytes) -> bytes:
    return same_length(data, b"/CFM /V2 ", b"")


def named_identity(data: bytes) -> bytes:
    """/CF's one filter renamed /Identity, and /StmF and /StrF naming it."""
    renamed = data.replace(b"/CF << /StdCF", b"/CF << /Identity")
    edited = same_length(renamed, b"/StmF /StdCF /StrF /StdCF", b"/StmF /Identity /StrF /Identity")
    # Three more bytes for the longer name in /CF.
    gap = b" " * (len(b"/AuthEvent /DocOpen ") - 6)
    assert edited.count(gap) == 1
    edited = edited.replace(gap, gap[:-3])
    assert len(edited) == len(data)
    return edited


def comment_and_link() -> bytes:
    """RC4 at revision 4 without /CFM: a page that draws nothing, with a
    comment and a link, whose strings no check compares."""
    built = build(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> "
            b"/Annots [4 0 R 5 0 R] >>",
            4: b"<< /Type /Annot /Subtype /Text /Rect [10 10 30 30] /Contents (Comment) >>",
            5: b"<< /Type /Annot /Subtype /Link /Rect [40 10 90 30] "
            b"/A << /S /URI /URI (http://example.org/) >> >>",
        },
        1,
    )
    data = encrypt(built, user="", owner="o", R=4, aes=False, metadata=False, compress=False)
    return without_cfm(data)


NOT_ALIKE = "its crypt filters are not all ones both engines decrypt alike: "


@pytest.mark.parametrize(
    "data, found",
    [
        (without_cfm(RC4), "the crypt filter /StmF names has no /CFM"),
        (comment_and_link(), "the crypt filter /StmF names has no /CFM"),
        (
            same_length(RC4, b"/CFM /V2 ", b"/CFM /None "),
            "the crypt filter /StmF names has a /CFM other than /V2, /AESV2 and /AESV3",
        ),
        (
            same_length(RC4, b"/CFM /V2 ", b"/CFM /Foo "),
            "the crypt filter /StmF names has a /CFM other than /V2, /AESV2 and /AESV3",
        ),
        (same_length(RC4, b"/CFM /V2 ", b"/CFM (V2) "), "/CFM is no name"),
        (same_length(RC4, b"/StmF /StdCF", b"/StmF (StdCF)"), "/StmF is no name"),
        (named_identity(RC4), "/StmF names /Identity, and /CF defines a crypt filter of that name"),
        (
            same_length(RC4, b"/StrF /StdCF", b"/StrF /StdCF /EFF /StdCG"),
            "/EFF names a crypt filter /CF does not define",
        ),
    ],
    ids=[
        "no /CFM",
        "no /CFM, a comment and a link",
        "/CFM /None",
        "/CFM /Foo",
        "/CFM a string",
        "/StmF a string",
        "a filter named /Identity",
        "/EFF names no filter",
    ],
)
def test_crypt_filters_the_engines_may_decrypt_otherwise(data, found):
    """Refused before QPDF writes. Without /CFM, PDFium decrypts with RC4
    where QPDF leaves the data as it is; with a filter named /Identity, QPDF
    decrypts and PDFium reads the data as written; /CFM /None QPDF warns of
    (measured). The checks would see these only where an annotation's
    compared entries or the rendering show them: not a comment's /Contents
    or a link's /URI on a page that draws nothing (measured)."""
    with pytest.raises(DecryptionFailedError, match=re.escape(NOT_ALIKE + found)):
        pdfacts.decrypt(data)


def entries(**changed) -> EncryptionEntries:
    """The entries of revision 4 with RC4, one crypt filter /StdCF, changed."""
    values = {
        "filter": b"Standard",
        "sub_filter": None,
        "v": 4,
        "r": 4,
        "length": 128,
        "length_invalid": False,
        "p": -4,
        "encrypt_metadata": False,
        "encrypt_metadata_invalid": False,
        "key_bits": 128,
        "user_password_matched": True,
        "owner_password_matched": False,
        "dictionary": (6, 0),
        "stream_filter": b"StdCF",
        "string_filter": b"StdCF",
        "file_filter": None,
        "crypt_filters": (CryptFilterEntry(name=b"StdCF", method=b"V2"),),
    }
    return EncryptionEntries(**(values | changed))


@pytest.mark.parametrize(
    "changed, refused",
    [
        ({}, None),
        ({"crypt_filters": (CryptFilterEntry(name=b"StdCF", method=b"AESV2"),)}, None),
        ({"crypt_filters": (CryptFilterEntry(name=b"StdCF", method=b"AESV3"),)}, None),
        ({"stream_filter": None, "string_filter": None}, None),
        ({"stream_filter": b"Identity", "file_filter": b"Identity"}, None),
        ({"crypt_filters": "/CF is no dictionary"}, "/CF is no dictionary"),
        (
            {
                "crypt_filters": (
                    CryptFilterEntry(name=b"StdCF", method="the filter is no dictionary"),
                )
            },
            "the filter is no dictionary",
        ),
        (
            {
                "string_filter": b"Identity",
                "crypt_filters": (
                    CryptFilterEntry(name=b"Identity", method=b"V2"),
                    CryptFilterEntry(name=b"StdCF", method=b"V2"),
                ),
            },
            "/StrF names /Identity, and /CF defines a crypt filter of that name",
        ),
    ],
    ids=[
        "/V2",
        "/AESV2",
        "/AESV3",
        "both absent",
        "/Identity, not defined",
        "/CF no dictionary",
        "a filter no dictionary",
        "/Identity, defined",
    ],
)
def test_the_crypt_filters_as_the_backend_reads_them(changed, refused):
    """The cases PDFium opens no file of, or that the encryption dictionary
    cannot be edited to at the same length, as the backend's plain data."""
    assert decryption._refused_filter(entries(**changed)) == refused


@pytest.mark.parametrize(
    "options, old, new",
    [({"R": 4}, b"/CFM /AESV2", b"/CFM /AESV3"), ({"R": 6}, b"/CFM /AESV3", b"/CFM /AESV2")],
    ids=["revision 4, /AESV3", "revision 6, /AESV2"],
)
def test_an_aes_method_of_the_other_revision(options, old, new):
    """Both engines decrypt either AES method whatever /R says (measured)."""
    data = encrypt(plain(), user="", owner="o", **options)
    assert data.count(old) == 1
    assert_built(pdfacts.decrypt(data.replace(old, new)).data)


def test_an_embedded_file_filter_named_identity():
    data = same_length(RC4, b"/StrF /StdCF", b"/StrF /StdCF /EFF /Identity")
    assert_built(pdfacts.decrypt(data).data)


TEXT = b"BT 3 Tr /F1 24 Tf 20 100 Td (Hello) Tj ET"
FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def invisible_text() -> bytes:
    """RC4 at revision 4, /EncryptMetadata false: a page whose content writes
    a word in invisible text, which the rendering does not show."""
    built = plain(
        body=TEXT, annotation=False, extra={7: FONT}, resources=b"<< /Font << /F1 7 0 R >> >>"
    )
    return encrypt(built, user="", owner="o", R=4, aes=False, metadata=False, compress=False)


def written_anew(data: bytes, entries: bytes, catalog_metadata: bool = False) -> bytes:
    """The page's content written anew in an update, as it is, with `entries`;
    also as the catalog's /Metadata where `catalog_metadata`."""
    content = int(re.findall(rb"/Contents (\d+) 0 R", data)[0])
    objects = {content: stream(TEXT, entries)}
    if catalog_metadata:
        catalog, dictionary = written_object(data, rb"/Type /Catalog")
        objects[catalog] = dictionary.replace(b" >>", b" /Metadata %d 0 R >>" % content)
    return updated(data, objects)


def with_dictionary(dictionary: bytes) -> bytes:
    """AES at revision 4: the catalog names `dictionary`, whose strings pikepdf encrypts."""
    built = plain(catalog=b" /Extra 6 0 R", extra={6: dictionary})
    return encrypt(built, user="", owner="o")


def metadata_dictionary() -> bytes:
    """/EncryptMetadata false: the catalog's /Metadata a comment's dictionary,
    written in the clear in an update."""
    data = invisible_text()
    catalog, dictionary = written_object(data, rb"/Type /Catalog")
    comment = b"<< /Subtype /Text /Rect [10 10 30 30] /Contents (Comment) >>"
    objects = {catalog: dictionary.replace(b" >>", b" /Metadata 20 0 R >>"), 20: comment}
    return updated(data, objects)


def with_info(info: bytes, objects: dict[int, bytes] | None = None) -> bytes:
    """An update whose trailer has /Info `info`, written in the clear, and
    that writes the catalog anew as it is, and `objects`."""
    data = invisible_text()
    catalog, dictionary = written_object(data, rb"/Type /Catalog")
    return updated(data, {catalog: dictionary, **(objects or {})}, b"/Info " + info + b" ")


SIGNATURE = (
    b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
    b"/ByteRange [0 10 20 30] /Contents <3082ABCD> >>"
)


@pytest.mark.parametrize(
    "data, found",
    [
        (
            written_anew(
                invisible_text(), b"/Filter [/Crypt] /DecodeParms [<< /Name /Identity >>]"
            ),
            "a stream whose /DecodeParms name a crypt filter",
        ),
        (
            written_anew(invisible_text(), b"/Filter /Crypt /DecodeParms << /Name /StdCF >>"),
            "a stream whose /DecodeParms name a crypt filter",
        ),
        (written_anew(invisible_text(), b"/Type /XRef"), "a stream /Type /XRef"),
        (with_dictionary(b"<< /Type /Sig /Contents (Comment) >>"), "a dictionary PDFium takes"),
        (with_dictionary(b"<< /FT /Sig /Contents (Comment) >>"), "a dictionary PDFium takes"),
        (
            with_dictionary(b"<< /FT /Sig /ByteRange [0 1 2 3] /Contents (Comment) >>"),
            "a dictionary PDFium takes",
        ),
        (with_dictionary(b"<< /Type (Sig) /Contents (Comment) >>"), "a dictionary PDFium takes"),
        (with_dictionary(b"<< /FT (Sig) /Contents (Comment) >>"), "a dictionary PDFium takes"),
        (with_dictionary(b"<< /Type /Sig /Contents [(Comment)] >>"), "a dictionary PDFium takes"),
        (
            written_anew(invisible_text(), b"/Type /Metadata", catalog_metadata=True),
            "the catalog's /Metadata under /EncryptMetadata false",
        ),
        (
            written_anew(invisible_text(), b"/Subtype /XML", catalog_metadata=True),
            "the catalog's /Metadata under /EncryptMetadata false",
        ),
        (metadata_dictionary(), "the catalog's /Metadata under /EncryptMetadata false"),
        (
            with_info(b"<< /Title (Comment) >>"),
            "an object the trailer holds otherwise: a string in it",
        ),
        (
            with_info(b"20 0 R", {20: b"<< /Type /Sig /Contents (Comment) >>"}),
            "object 20 0 otherwise: a dictionary PDFium takes",
        ),
    ],
    ids=[
        "/Crypt naming /Identity",
        "/Crypt naming /StdCF",
        "a stream /Type /XRef",
        "/Type /Sig without /ByteRange",
        "/FT /Sig without /Type",
        "/FT /Sig with /ByteRange, without /Type",
        "/Type a string Sig",
        "/FT a string Sig",
        "/Contents an array",
        "metadata /Type /Metadata only",
        "metadata /Subtype /XML only",
        "metadata a dictionary",
        "a direct /Info",
        "/Info of /Type /Sig",
    ],
)
def test_objects_the_engines_decrypt_otherwise(data, found):
    """Refused before QPDF writes (docs/conventions.md, "Decryption"): QPDF
    takes a crypt filter /DecodeParms name, PDFium does not; QPDF leaves a
    stream /Type /XRef as written, PDFium decrypts it; PDFium leaves the
    /Contents of a dictionary of /Type /Sig, or /FT /Sig without /Type, as
    written, QPDF unless it has /ByteRange; under /EncryptMetadata false,
    PDFium leaves the catalog's /Metadata as written, QPDF only one of /Type
    /Metadata with /Subtype /XML (measured)."""
    with pytest.raises(DecryptionFailedError, match=re.escape(found)):
        pdfacts.decrypt(data)


@pytest.mark.parametrize("revision", [2, 3])
def test_metadata_left_unencrypted_below_version_4(revision):
    """/EncryptMetadata false added to the encryption dictionary of revision 2
    or 3 in an update, the catalog's XMP in the clear: below /V 4 QPDF
    ignores the flag and decrypts the XMP, PDFium leaves it as written
    (measured)."""
    data = encrypt(
        plain(), user="", owner="o", R=revision, aes=False, metadata=False, compress=False
    )
    catalog, dictionary = written_object(data, rb"/Type /Catalog")
    trailer = data[data.rindex(b"trailer") :]
    number = int(re.findall(rb"/Encrypt (\d+) 0 R", trailer)[0])
    encryption = re.findall(rb"\n%d 0 obj\n(<<.*?>>)\nendobj" % number, data, re.S)
    assert len(encryption) == 1 and b"/EncryptMetadata" not in encryption[0]
    packet = (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
    )
    objects = {
        catalog: dictionary.replace(b" >>", b" /Metadata 30 0 R >>"),
        number: encryption[0][:-2] + b" /EncryptMetadata false >>",
        30: stream(packet, b"/Type /Metadata /Subtype /XML"),
    }
    with pytest.raises(DecryptionFailedError, match=re.escape("at /V 4 or more")):
        pdfacts.decrypt(updated(data, objects))


def test_metadata_that_holds_nothing_to_decrypt():
    """/EncryptMetadata false, the catalog's /Metadata a number: nothing in it
    is encrypted."""
    data = invisible_text()
    catalog, dictionary = written_object(data, rb"/Type /Catalog")
    pdfacts.decrypt(updated(data, {catalog: dictionary.replace(b" >>", b" /Metadata 5 >>")}))


def test_a_direct_metadata_named_by_its_catalog():
    """/EncryptMetadata false: the catalog's /Metadata a direct dictionary,
    written in the clear in an update; the catalog's number names it."""
    data = invisible_text()
    catalog, dictionary = written_object(data, rb"/Type /Catalog")
    direct = b" /Metadata << /Subtype /Text /Contents (Comment) >> >>"
    found = f"the engines decrypt object {catalog} 0 otherwise: the catalog's /Metadata"
    with pytest.raises(DecryptionFailedError, match=re.escape(found)):
        pdfacts.decrypt(updated(data, {catalog: dictionary.replace(b" >>", direct)}))


def test_an_info_both_engines_read_alike():
    """An /Info in an object of its own: both decrypt its strings."""
    pdfacts.decrypt(with_info(b"20 0 R", {20: b"<< /Producer 5 >>"}))


def test_an_object_reached_through_a_stream():
    """A form that only another form's /Resources names, written anew in the
    clear with /Filter /Crypt and /DecodeParms << /Name /Identity >>: the walk
    goes into a stream's dictionary."""
    outer = (
        b"/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources << /XObject << /I 6 0 R >> >>"
    )
    inner = b"/Type /XObject /Subtype /Form /BBox [0 0 11 11]"
    built = plain(
        annotation=False,
        resources=b"<< /XObject << /O 5 0 R >> >>",
        extra={5: stream(b"/I Do", outer), 6: stream(BODY, inner)},
    )
    data = encrypt(built, user="", owner="o", compress=False)
    number, _ = written_object(data, rb"/BBox \[ 0 0 11 11 \]")
    crypt = stream(BODY, inner + b" /Filter /Crypt /DecodeParms << /Name /Identity >>")
    with pytest.raises(
        DecryptionFailedError, match="a stream whose /DecodeParms name a crypt filter"
    ):
        pdfacts.decrypt(updated(data, {number: crypt}))


def test_a_signature_in_a_page():
    """A direct /Type /Sig without /ByteRange in a page's dictionary, whose
    strings pikepdf encrypts: the walk goes into direct dictionaries."""
    data = encrypt(
        plain(page_entries=b" /Extra << /Type /Sig /Contents (Comment) >>"), user="", owner="o"
    )
    with pytest.raises(DecryptionFailedError, match="a dictionary PDFium takes for a signature"):
        pdfacts.decrypt(data)


def test_a_byte_range_of_any_value():
    """/Type /Sig with /ByteRange 0: QPDF takes it for a signature as PDFium does."""
    result = pdfacts.decrypt(with_dictionary(b"<< /Type /Sig /ByteRange 0 /Contents <3082ABCD> >>"))
    with reopened(result.data) as pdf:
        assert bytes(pdf.Root.Extra.Contents) == bytes.fromhex("3082ABCD")


def test_a_string_in_an_array_of_a_direct_info():
    with pytest.raises(DecryptionFailedError, match="an object the trailer holds otherwise"):
        pdfacts.decrypt(with_info(b"<< /Keywords [(a) (b)] >>"))


def test_a_password_outside_ascii():
    """A user password outside ASCII at revision 4: both engines take it
    (docs/conventions.md, "Errors"), and decrypt() decrypts the file."""
    data = encrypt(plain(), user="pässwörd", owner="owner")
    assert_built(pdfacts.decrypt(data, password="pässwörd").data)


def test_a_signature_both_engines_read_alike():
    """/Type /Sig with /ByteRange: both leave /Contents as written."""
    result = pdfacts.decrypt(with_dictionary(SIGNATURE))
    with reopened(result.data) as pdf:
        assert bytes(pdf.Root.Extra.Contents) == bytes.fromhex("3082ABCD")


def test_a_signature_without_contents_as_a_string():
    """/Type /Sig without /ByteRange, and no string /Contents: nothing that
    either engine leaves as written."""
    result = pdfacts.decrypt(with_dictionary(b"<< /Type /Sig /Contents 5 /Name (Signer) >>"))
    with reopened(result.data) as pdf:
        assert str(pdf.Root.Extra.Name) == "Signer"


def test_metadata_of_another_type_where_metadata_is_encrypted():
    """The catalog's /Metadata of /Type /Metadata alone, with /EncryptMetadata
    true: both engines decrypt it (measured), so it is not refused."""
    base = encrypt(
        plain(
            body=TEXT, annotation=False, extra={7: FONT}, resources=b"<< /Font << /F1 7 0 R >> >>"
        ),
        user="",
        owner="o",
        compress=False,
    )
    pdfacts.decrypt(written_anew(base, b"/Type /Metadata", catalog_metadata=True))


def test_a_byte_range_without_a_signature():
    """/ByteRange, but no /Type /Sig: both engines decrypt /Contents."""
    result = pdfacts.decrypt(with_dictionary(b"<< /ByteRange [0 1 2 3] /Contents (Comment) >>"))
    with reopened(result.data) as pdf:
        assert str(pdf.Root.Extra.Contents) == "Comment"


def test_metadata_with_a_crypt_filter_it_does_not_name():
    """The catalog's /Metadata, /Type /Metadata /Subtype /XML, /Filter [/Crypt]
    without /DecodeParms, under /EncryptMetadata false, as a corpus file
    writes its XMP: both engines leave it as written (measured)."""
    data = written_anew(
        invisible_text(), b"/Type /Metadata /Subtype /XML /Filter [/Crypt]", catalog_metadata=True
    )
    result = pdfacts.decrypt(data)
    with reopened(result.data) as pdf:
        assert pdf.Root.Metadata.read_bytes() == TEXT


def test_aes_padding_the_engines_strip_otherwise():
    """The plain content's last 16 bytes end in 0x0f, and the encrypted
    stream's /Length leaves out the block of padding pikepdf added, at the
    same length: PDFium strips 15 bytes from the end by the last byte alone,
    QPDF strips nothing, the padding being inconsistent (measured). The text
    is invisible, so only the characters show it."""
    head = b"BT 3 Tr /F1 24 Tf 20 100 Td (Hi) Tj"
    head += b" " * (-len(head) % 16)
    tail = b" (NOT) Tj ET   \x0f"
    assert len(tail) == 16
    built = plain(
        body=head + tail,
        annotation=False,
        extra={7: FONT},
        resources=b"<< /Font << /F1 7 0 R >> >>",
    )
    data = encrypt(built, user="", owner="o", compress=False)
    number = int(re.findall(rb"/Contents (\d+) 0 R", data)[0])
    found = re.search(rb"\n%d 0 obj\n<< /Length (\d+) >>\nstream\n" % number, data)
    assert found is not None
    length, start = int(found.group(1)), found.end()
    shorter = str(length - 16).encode()
    assert len(shorter) == len(found.group(1))
    edited = (
        data[: found.start(1)]
        + shorter
        + data[found.end(1) : start + length - 16]
        + b" " * 16
        + data[start + length :]
    )
    assert len(edited) == len(data)
    with pytest.raises(DecryptionFailedError, match="PDFium reads the text of page 0 otherwise"):
        pdfacts.decrypt(edited)


def test_max_items_per_page_holds_the_characters():
    """Hello, five characters, and no annotation."""
    data = encrypt(
        plain(
            body=TEXT, annotation=False, extra={7: FONT}, resources=b"<< /Font << /F1 7 0 R >> >>"
        ),
        user="",
        owner="o",
    )
    pdfacts.decrypt(data, limits=Limits(max_items_per_page=5))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(data, limits=Limits(max_items_per_page=4))
    assert (raised.value.limit, raised.value.found) == ("max_items_per_page", 5)


def test_max_items_per_page_holds_the_annotations():
    square = b"<< /Type /Annot /Subtype /Square /Rect [10 10 20 20] >>"
    built = plain(
        annotation=False, page_entries=b" /Annots [6 0 R 7 0 R]", extra={6: square, 7: square}
    )
    data = encrypt(built, user="", owner="o")
    pdfacts.decrypt(data, limits=Limits(max_items_per_page=2))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(data, limits=Limits(max_items_per_page=1))
    assert (raised.value.limit, raised.value.found) == ("max_items_per_page", 2)


def test_an_unreadable_encryption_dictionary():
    """The object header of the encryption dictionary broken: neither engine
    reads the file as encrypted, and QPDF warns that it mis-read the object
    (measured)."""
    trailer = RC4[RC4.rindex(b"trailer") :]
    number = int(re.findall(rb"/Encrypt (\d+) 0 R", trailer)[0])
    header = b"\n%d 0 obj" % number
    assert RC4.count(header) == 1
    with pytest.raises(DecryptionFailedError, match="QPDF warns that it mis-read an object"):
        pdfacts.decrypt(RC4.replace(header, b"\n%d 0 ob|" % number))


def test_a_damaged_page_is_refused_as_open_refuses_it():
    """A flat crop box (docs/conventions.md, "Damaged files")."""
    data = encrypt(plain(page_entries=b" /CropBox [0 0 0 200]"), user="", owner="o")
    with pytest.raises(pdfacts.DamagedFileError):
        pdfacts.decrypt(data)


def test_identity_filters_both_engines_read_alike():
    """/StmF and /StrF /Identity, which /CF does not define: both engines read
    the data as written, and so does the result."""
    data = same_length(RC4, b"/StmF /StdCF /StrF /StdCF", b"/StmF /Identity /StrF /Identity")
    written = re.search(rb"\nstream\n(.*?)\nendstream", data, re.S)
    assert written is not None
    result = pdfacts.decrypt(data)
    with reopened(result.data) as pdf:
        assert pdf.pages[0].Contents.read_bytes() == written.group(1)


def test_a_warning_while_qpdf_writes():
    """A stream the catalog names and nothing draws, /ASCIIHexDecode with data
    that is not hex: QPDF decodes it only when it writes it, and warns; open()
    gives no warning (measured)."""
    hex_data = stream(b"zz not hex >", b"/Filter /ASCIIHexDecode")
    built = plain(catalog=b" /Extra 6 0 R", extra={6: hex_data})
    found = "QPDF reported while it decrypted the file: .*error decoding stream data"
    with pytest.raises(DecryptionFailedError, match=found):
        pdfacts.decrypt(encrypt(built, user="", owner="o"))


def test_an_input_that_gives_warnings():
    """PDFium reads 2^32 as 0, so its media box is flat and it puts in US
    letter, where QPDF reads the box as written (docs/conventions.md,
    "Damaged files"): the page's values from QPDF are unknown, with a
    warning."""
    data = encrypt(plain(box=b"[0 0 4294967296 200]"), user="", owner="o")
    with pytest.raises(DecryptionFailedError, match=": the file gives warnings: page 0"):
        pdfacts.decrypt(data)


# --- the checks catch a result made wrong on purpose ------------------------------------------


def test_the_checks_pass_the_file_as_it_was_built(monkeypatch):
    returning(monkeypatch, plain())
    assert pdfacts.decrypt(ENCRYPTED).data == plain()


@pytest.mark.parametrize(
    "data, found",
    [
        (b"%PDF-1.7\nnot a file\n%%EOF\n", "the decrypted file does not open"),
        (ENCRYPTED, "PDFium reads the decrypted file as encrypted, revision 4"),
        (USER, "the decrypted file does not open: a password is needed"),
        (plain(pages=2), "the decrypted file has 2 pages, the file 1"),
        (plain(box=b"[0 0 200 300]"), "page 0 of the decrypted file has the geometry"),
        (plain(rect=b"[10 10 100 40]"), "PDFium reads the annotations of page 0 otherwise"),
        (plain(body=b"0 0 m 100 50 l S"), "PDFium renders page 0 otherwise"),
        (
            plain().replace(b"/Size 11", b"/Size 12"),
            "the decrypted file gives warnings: .*reported number of objects",
        ),
    ],
    ids=[
        "does not open",
        "encrypted",
        "encrypted with a user password",
        "another page count",
        "another box",
        "another rectangle",
        "other content",
        "a warning",
    ],
)
def test_the_checks_catch_a_wrong_result(monkeypatch, data, found):
    returning(monkeypatch, data)
    with pytest.raises(DecryptionFailedError, match=found):
        pdfacts.decrypt(ENCRYPTED)


def test_the_rendering_is_compared_in_colour(monkeypatch):
    """A square filled red, and in its place one filled with the grey that
    PDFium renders red as when it renders in grey, 76 of 255 (measured): only
    a rendering in colour tells them apart."""
    square = b" 50 50 100 100 re f"
    data = encrypt(plain(body=b"1 0 0 rg" + square, annotation=False), user="", owner="o")
    returning(monkeypatch, plain(body=b"%.4f g" % (76 / 255) + square, annotation=False))
    with pytest.raises(DecryptionFailedError, match="renders page 0 otherwise"):
        pdfacts.decrypt(data)


def test_the_checks_catch_other_invisible_text(monkeypatch):
    """Text in rendering mode 3 draws nothing: only the characters tell."""
    returning(
        monkeypatch,
        plain(
            body=TEXT.replace(b"(Hello)", b"(Hallo)"),
            annotation=False,
            extra={7: FONT},
            resources=b"<< /Font << /F1 7 0 R >> >>",
        ),
    )
    with pytest.raises(DecryptionFailedError, match="PDFium reads the text of page 0 otherwise"):
        pdfacts.decrypt(invisible_text())


def test_the_checks_compare_every_page(monkeypatch):
    returning(monkeypatch, plain(pages=2, last_box=b"[0 0 200 300]"))
    with pytest.raises(
        DecryptionFailedError, match="page 1 of the decrypted file has the geometry"
    ):
        pdfacts.decrypt(encrypt(plain(pages=2), user="", owner="o"))


def test_the_checks_catch_a_damaged_page_of_the_result(monkeypatch):
    returning(monkeypatch, plain(page_entries=b" /CropBox [0 0 0 200]"))
    with pytest.raises(DecryptionFailedError, match="page 0 of the decrypted file is damaged"):
        pdfacts.decrypt(ENCRYPTED)


def test_the_checks_catch_a_warning_while_writing(monkeypatch):
    returning(monkeypatch, plain(), warnings=("a warning",))
    with pytest.raises(DecryptionFailedError, match="QPDF reported .*: a warning"):
        pdfacts.decrypt(ENCRYPTED)


def test_the_checks_catch_a_result_without_a_usable_size(monkeypatch):
    """The input's /Size as written, the result's taken away: the first QPDF
    document opened is the input's."""
    opened = []
    count = qpdf_backend.QpdfDocument.object_count

    def object_count(self):
        if self not in opened:
            opened.append(self)
        return count(self) if self is opened[0] else None

    monkeypatch.setattr(qpdf_backend.QpdfDocument, "object_count", object_count)
    with pytest.raises(DecryptionFailedError, match="the decrypted file has no usable /Size"):
        pdfacts.decrypt(ENCRYPTED)


@pytest.mark.parametrize(
    "kind, error",
    [(FailureKind.FORMAT, DecryptionFailedError), (FailureKind.OTHER, EngineError)],
    ids=["format", "other"],
)
def test_a_failure_while_writing(monkeypatch, kind, error):
    def rewrite(source: bytes, password: str | None) -> DecryptedBytes:
        raise EngineFailure(kind, "it failed")

    monkeypatch.setattr(qpdf_backend, "rewrite_decrypted", rewrite)
    with pytest.raises(error, match="it failed"):
        pdfacts.decrypt(ENCRYPTED)


# --- limits ---------------------------------------------------------------------------------


def test_max_bytes_applies_to_the_input_only(monkeypatch):
    # White space after %%EOF makes the result one byte larger than the input;
    # QPDF finds startxref within 1054 bytes of the end.
    padding = len(ENCRYPTED) - len(plain()) + 1
    assert 0 < padding < 1000
    returning(monkeypatch, plain() + b" " * padding)
    pdfacts.decrypt(ENCRYPTED, limits=Limits(max_bytes=len(ENCRYPTED)))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(ENCRYPTED, limits=Limits(max_bytes=len(ENCRYPTED) - 1))
    assert raised.value.found == len(ENCRYPTED)


def test_max_pages_applies_to_the_input(monkeypatch):
    # The input has two pages; the result one, so only the input is over the limit.
    returning(monkeypatch, plain())
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(encrypt(plain(pages=2), user="", owner="o"), limits=Limits(max_pages=1))
    assert (raised.value.limit, raised.value.found) == ("max_pages", 2)


def test_max_pages_applies_to_the_result(monkeypatch):
    returning(monkeypatch, plain(pages=2))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(ENCRYPTED, limits=Limits(max_pages=1))
    assert (raised.value.limit, raised.value.found) == ("max_pages", 2)


def test_max_objects_applies_to_the_result(monkeypatch):
    # Objects 20 to 39 are used by nothing; the input has 11 at most.
    unused = {number: b"<< >>" for number in range(20, 40)}
    returning(monkeypatch, plain(extra=unused))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(ENCRYPTED, limits=Limits(max_objects=20))
    assert (raised.value.limit, raised.value.found) == ("max_objects", 40)


def test_max_render_pixels_applies_to_the_renderings():
    # 200 by 200 units at scale 1.
    pdfacts.decrypt(ENCRYPTED, limits=Limits(max_render_pixels=40000))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(ENCRYPTED, limits=Limits(max_render_pixels=39999))
    assert raised.value.found == 40000


@pytest.mark.parametrize(
    "box, side",
    [
        (b"[0 0 8192 8192]", 8192),
        (b"[0 0 3000000000 0.001]", 3000000000),
        (b"[0 0 3000000000 0.00000000000001]", 3000000000),
    ],
    ids=["8192 by 8192 units", "3,000,000,000 by 0.001 units", "3,000,000,000 by 10^-14 units"],
)
def test_a_large_page_is_rendered_at_a_lower_scale(box, side):
    """At most 2^22 pixels: (w s + 1)(h s + 1) = 2^22, so the page is fewer by
    less than w s + h s + 1, its sides at that scale and one."""
    assert decryption.CHECK_PIXELS == 2**22
    data = encrypt(plain(box=box, annotation=False), user="", owner="o")
    pdfacts.decrypt(data, limits=Limits(max_render_pixels=2**22))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(data, limits=Limits(max_render_pixels=2**20))
    assert 2**20 < raised.value.found <= 2**22
    if side == 8192:
        # 2047 by 2047: fewer by 2 * 2047 + 1.
        assert raised.value.found > 2**22 - 2 * 2048


def test_the_deadline_counts_the_writing(monkeypatch):
    now = [0.0]
    compared = []
    write = qpdf_backend.rewrite_decrypted

    def slow(source: bytes, password: str | None) -> DecryptedBytes:
        now[0] += 10
        return write(source, password)

    monkeypatch.setattr(qpdf_backend, "rewrite_decrypted", slow)
    monkeypatch.setattr(decryption, "_compare_page", lambda *page: compared.append(page))
    with pytest.raises(LimitExceededError) as raised:
        pdfacts.decrypt(ENCRYPTED, limits=Limits(deadline_seconds=5), _clock=lambda: now[0])
    assert raised.value.limit == "deadline_seconds"
    assert compared == []


def test_the_deadline_is_checked_after_each_page(monkeypatch):
    now = [0.0]
    compared = []
    compare = decryption._compare_page

    def slow(source, result, index):
        compared.append(index)
        compare(source, result, index)
        now[0] += 10

    monkeypatch.setattr(decryption, "_compare_page", slow)
    data = encrypt(plain(pages=3), user="", owner="o")
    with pytest.raises(LimitExceededError):
        pdfacts.decrypt(data, limits=Limits(deadline_seconds=5), _clock=lambda: now[0])
    assert compared == [0]
