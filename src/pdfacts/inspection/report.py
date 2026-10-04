"""The inspection report: the file's objects, each page's /Contents, its
encryption and its active content, read through QPDF only
(docs/conventions.md, "Inspection").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pdfacts.backends import protocols
from pdfacts.core.document import Document
from pdfacts.core.errors import EngineError
from pdfacts.core.refs import ObjectRef, _is_int
from pdfacts.core.strings import Name, decode_name
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.inspection.active_content import ActiveContent, active_content

# Why the counts, the pages and the active content are unknown where QPDF
# warned that it mis-read an object (issue #62's rule).
_MISREAD = (
    "QPDF warned that it mis-read an object of the file, so what it reads of the "
    "objects is not known; its warnings are in the inspection's warnings"
)
# Why an entry of the encryption dictionary that QPDF finds absent is unknown
# there: QPDF drops a key whose value it reads as null.
_MISREAD_ENTRY = (
    "QPDF warned that it mis-read an object of the file, and drops a key whose value "
    "it reads as null, so whether the entry is written is not known"
)
_PAGES = (
    "the pages QPDF found are not the objects the file's page tree lists, so the "
    "object QPDF has at a page index may not be the file's"
)


class ContentsForm(StrEnum):
    """How a page writes /Contents."""

    STREAM = "stream"
    ARRAY = "array"
    # An array that is an object of its own.
    INDIRECT_ARRAY = "indirect-array"
    # No key, a null, or a reference to an object the file lacks.
    ABSENT = "absent"
    # Anything else: a number, a name, a dictionary.
    OTHER = "other"


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentsStream:
    """A stream of a page's /Contents and the /Length its dictionary writes."""

    ref: ObjectRef
    # As written: in an encrypted file, the length of the encrypted data;
    # unknown, invalid, where it is below 0, which QPDF reads as 0.
    length: int | Unknown
    # /Length is a reference to the integer.
    length_indirect: bool

    def __post_init__(self) -> None:
        _check_type("ref", self.ref, ObjectRef)
        if not isinstance(self.length, Unknown):
            _check_count("length", self.length)
        _check_type("length_indirect", self.length_indirect, bool)


@dataclass(frozen=True, slots=True, kw_only=True)
class Contents:
    """What a /Contents names: its streams, and its entries that are none."""

    # In order: one for a stream, none for ABSENT and OTHER.
    streams: tuple[ContentsStream, ...]
    # The entries of the array that are no streams, a null included; 1 for
    # OTHER, 0 for a stream and for ABSENT.
    others: int

    def __post_init__(self) -> None:
        _check_tuple("streams", self.streams, ContentsStream)
        _check_count("others", self.others)


# The streams and the others each form names (Contents); an array's
# are as written.
_SHAPES = {
    ContentsForm.STREAM: (1, 0),
    ContentsForm.ABSENT: (0, 0),
    ContentsForm.OTHER: (0, 1),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PageContents:
    """What a page writes in /Contents."""

    page_index: int
    # The page object as QPDF reads the file's page tree, not checked against
    # PDFium as a PageRef's is ("Object identity").
    ref: ObjectRef
    form: ContentsForm
    # The object of an indirect array; None otherwise.
    array: ObjectRef | None
    # What /Contents names, as an index into Inspection.contents. Pages whose
    # /Contents is the same stream or the same indirect array give the same
    # index.
    contents: int

    def __post_init__(self) -> None:
        _check_count("page_index", self.page_index)
        _check_type("ref", self.ref, ObjectRef)
        object.__setattr__(self, "form", ContentsForm(self.form))
        if (self.array is not None) != (self.form is ContentsForm.INDIRECT_ARRAY):
            raise ValueError("array is an ObjectRef exactly where the form is an indirect array")
        if self.array is not None:
            _check_type("array", self.array, ObjectRef)
        _check_count("contents", self.contents)


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectCounts:
    """The objects QPDF reads, and of them each kind."""

    total: int
    # Streams other than object streams and cross-reference streams.
    streams: int
    object_streams: int
    cross_reference_streams: int
    dictionaries: int
    arrays: int

    def __post_init__(self) -> None:
        for name in (
            "total",
            "streams",
            "object_streams",
            "cross_reference_streams",
            "dictionaries",
            "arrays",
        ):
            _check_count(name, getattr(self, name))
        kinds = (
            self.streams,
            self.object_streams,
            self.cross_reference_streams,
            self.dictionaries,
            self.arrays,
        )
        if sum(kinds) > self.total:
            raise ValueError("the objects of each kind are among the total")


class CryptMethod(StrEnum):
    """How strings, streams or embedded files are encrypted (ISO 32000-2, 7.6)."""

    # The crypt filter /Identity, or none named: not encrypted.
    IDENTITY = "identity"
    # RC4: /V 1 and 2, or /CFM /V2.
    RC4 = "rc4"
    # AES: /CFM /AESV2 or /AESV3, with a key Encryption.key_bits long. Both
    # engines decrypt /AESV3 with the key of 128 bits of /V 4, and /AESV2 with
    # the key of 256 bits of /V 5 (measured).
    AES = "aes"


@dataclass(frozen=True, slots=True, kw_only=True)
class CryptFilter:
    """A crypt filter of the encryption dictionary's /CF, as written."""

    name: Name | Unknown
    # /CFM; None where absent; unknown where it is no name, where the filter
    # is no dictionary, or where QPDF may have dropped it, as
    # Encryption.filter.
    cfm: Name | None | Unknown

    def __post_init__(self) -> None:
        _check_type("name", self.name, (Name, Unknown))
        if self.cfm is not None:
            _check_type("cfm", self.cfm, (Name, Unknown))


@dataclass(frozen=True, slots=True, kw_only=True)
class Permissions:
    """The bits of /P (ISO 32000-2, Table 22): True where the bit is 1.

    Reported, not enforced. Revision 2 defines bits 3 to 6 only; there the
    others are None.
    """

    print: bool  # bit 3
    modify: bool  # bit 4
    copy: bool  # bit 5
    annotate: bool  # bit 6
    fill_forms: bool | None  # bit 9
    # Bit 10, extraction for accessibility in ISO 32000-1; ISO 32000-2 uses
    # it no more, and has writers set it.
    accessibility: bool | None
    assemble: bool | None  # bit 11
    print_high_quality: bool | None  # bit 12

    def __post_init__(self) -> None:
        for name in ("print", "modify", "copy", "annotate"):
            _check_type(name, getattr(self, name), bool)
        later = ("fill_forms", "accessibility", "assemble", "print_high_quality")
        values = [getattr(self, name) for name in later]
        for name, value in zip(later, values, strict=True):
            if value is not None:
                _check_type(name, value, bool)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("bits 9 to 12 are all None, for revision 2, or none")


def _permissions(p: int, r: int) -> Permissions:
    """The bits of `p` for a security handler of revision `r`."""

    def bit(number: int) -> bool:
        return bool((p >> (number - 1)) & 1)

    later = r >= 3
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


@dataclass(frozen=True, slots=True, kw_only=True)
class Encryption:
    """A file's encryption: its dictionary as written, and what follows from it."""

    # The object the encryption dictionary is written in; None where the
    # trailer holds it directly.
    ref: ObjectRef | None
    # Names, a number or a flag as written; None where absent; unknown where
    # of another type, or where QPDF mis-read an object of the file and finds
    # it absent, for QPDF drops a key whose value it reads as null.
    filter: Name | None | Unknown
    sub_filter: Name | None | Unknown
    v: int
    r: int
    # /Length in bits.
    length: int | None | Unknown
    p: int
    # The bits of `p` for revision `r`.
    permissions: Permissions
    encrypt_metadata: bool | None | Unknown
    # /StmF, /StrF and /EFF, and the crypt filters /CF defines, in the order of
    # their keys' bytes; these unknown where QPDF mis-read an object of the
    # file, for it may have dropped one.
    stream_filter: Name | None | Unknown
    string_filter: Name | None | Unknown
    embedded_file_filter: Name | None | Unknown
    crypt_filters: tuple[CryptFilter, ...] | Unknown
    # QPDF's: the key's length in bits.
    key_bits: int
    # The methods for streams, strings and embedded files, as ISO 32000-2
    # gives them for the values above; unknown where they do not settle it:
    # invalid for another /CFM, a filter /CF lacks or an entry of another
    # type; unreadable where QPDF may have dropped what it rests on; the
    # engines disagreeing for /CFM /None or none, and for a filter named
    # /Identity that /CF defines.
    stream_method: CryptMethod | Unknown
    string_method: CryptMethod | Unknown
    embedded_file_method: CryptMethod | Unknown
    # Whether the password given to open() matched the user's, and whether
    # it matched the owner's; no password is the empty one.
    user_password_matched: bool
    owner_password_matched: bool

    def __post_init__(self) -> None:
        if self.ref is not None:
            _check_type("ref", self.ref, ObjectRef)
        for name in (
            "filter",
            "sub_filter",
            "stream_filter",
            "string_filter",
            "embedded_file_filter",
        ):
            value = getattr(self, name)
            if value is not None:
                _check_type(name, value, (Name, Unknown))
        for name in ("v", "r", "p"):
            if not _is_int(getattr(self, name)):
                raise TypeError(f"{name} must be an int, not {type(getattr(self, name)).__name__}")
        if not (self.length is None or isinstance(self.length, Unknown) or _is_int(self.length)):
            raise TypeError(
                f"length must be an int, None or Unknown, not {type(self.length).__name__}"
            )
        _check_type("permissions", self.permissions, Permissions)
        if self.permissions != _permissions(self.p, self.r):
            raise ValueError("permissions are the bits of p for revision r")
        if self.encrypt_metadata is not None:
            _check_type("encrypt_metadata", self.encrypt_metadata, (bool, Unknown))
        if not isinstance(self.crypt_filters, Unknown):
            _check_tuple("crypt_filters", self.crypt_filters, CryptFilter)
        _check_count("key_bits", self.key_bits)
        if self.key_bits == 0:
            raise ValueError("a key has at least 1 bit")
        for name in ("stream_method", "string_method", "embedded_file_method"):
            value = getattr(self, name)
            if not isinstance(value, Unknown):
                object.__setattr__(self, name, CryptMethod(value))
        if self.v not in (1, 2, 4, 5):
            raise ValueError(f"v is 1, 2, 4 or 5, not {self.v}")
        methods = (self.stream_method, self.string_method, self.embedded_file_method)
        if self.v in (1, 2) and any(method is not CryptMethod.RC4 for method in methods):
            raise ValueError("a handler of /V 1 or 2 uses RC4")
        for name in ("user_password_matched", "owner_password_matched"):
            _check_type(name, getattr(self, name), bool)


@dataclass(frozen=True, slots=True, kw_only=True)
class Inspection:
    """What pdfacts reads of a file's structure and its active content, through QPDF."""

    # The trailer's /Size, as Document.object_count gives it.
    size: int | Unknown
    objects: ObjectCounts | Unknown
    # Each page's /Contents, in page order.
    pages: tuple[PageContents, ...] | Unknown
    # What each page's /Contents names, in the order the pages first name it:
    # a stream or an indirect array once, however many pages name it. Unknown
    # where `pages` is, with its reason.
    contents: tuple[Contents, ...] | Unknown
    # None where the file is not encrypted.
    encryption: Encryption | None | Unknown
    active_content: ActiveContent | Unknown
    # What QPDF warned of while it read every object for the inspection.
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.size, Unknown):
            _check_count("size", self.size)
            if self.size == 0:
                raise ValueError("/Size is a whole number above 0")
        if not isinstance(self.objects, Unknown):
            _check_type("objects", self.objects, ObjectCounts)
        if isinstance(self.pages, Unknown) or isinstance(self.contents, Unknown):
            if self.contents != self.pages:
                raise ValueError("contents are unknown exactly where the pages are, as they are")
        else:
            _check_tuple("pages", self.pages, PageContents)
            _check_tuple("contents", self.contents, Contents)
            listed: dict[object, int] = {}
            for index, page in enumerate(self.pages):
                if page.page_index != index:
                    raise ValueError(f"the page at {index} gives the index {page.page_index}")
                if page.contents >= len(self.contents):
                    raise ValueError(f"the page at {index} names contents {page.contents} of none")
                named = self.contents[page.contents]
                shape = _SHAPES.get(page.form)
                if shape is not None and (len(named.streams), named.others) != shape:
                    raise ValueError(
                        f"a /Contents of the form {page.form.value} names {shape[0]} streams "
                        f"and {shape[1]} others"
                    )
                # What the page names: a stream or an indirect array by its
                # object, anything else for the page alone.
                key: object = index
                if page.form is ContentsForm.INDIRECT_ARRAY:
                    key = page.array
                elif page.form is ContentsForm.STREAM:
                    key = named.streams[0].ref
                if key in listed:
                    if listed[key] != page.contents:
                        raise ValueError("pages that name one stream or array give one index")
                elif page.contents != len(listed):
                    raise ValueError(
                        "contents are in the order the pages first name them, each once"
                    )
                else:
                    listed[key] = page.contents
            if len(listed) != len(self.contents):
                raise ValueError("every contents is named by a page")
        if self.encryption is not None:
            _check_type("encryption", self.encryption, (Encryption, Unknown))
        _check_type("active_content", self.active_content, (ActiveContent, Unknown))
        _check_tuple("warnings", self.warnings, str)


def inspect(document: Document) -> Inspection:
    """The file's objects, each page's /Contents, its encryption and its active
    content, read through QPDF, every object read (docs/conventions.md,
    "Inspection")."""
    if not isinstance(document, Document):
        raise TypeError(f"a document of pdfacts, not {type(document).__name__}")
    size = document.object_count
    with document._access() as access:
        if access.qpdf_unknown is not None:
            unknown = access.qpdf_unknown
            return Inspection(
                size=size,
                objects=unknown,
                pages=unknown,
                contents=unknown,
                encryption=unknown,
                active_content=unknown,
                warnings=(),
            )
        structure = access.structure()
        # Where QPDF mis-read an object, the view of the active content would
        # too, reading every object again for values that are unknown.
        found = None
        if not structure.misread:
            # Reading every object again takes as long as the first read.
            document._deadline()
            found = access.active_content()
    misread = Unknown(UnknownReason.UNREADABLE, _MISREAD)
    # Whether the file is encrypted open() checked against PDFium: where QPDF
    # dropped /Encrypt, reading the dictionary as null, the engines disagree,
    # and every value from QPDF is unknown (docs/conventions.md, "Object
    # identity").
    encryption: Encryption | None = None
    if structure.encryption is not None:
        encryption = _encryption(structure.encryption, misread=structure.misread)
    if structure.misread:
        objects: ObjectCounts | Unknown = misread
        pages: tuple[PageContents, ...] | Unknown = misread
        contents: tuple[Contents, ...] | Unknown = misread
    else:
        counts = structure.objects  # given where QPDF mis-read nothing
        objects = ObjectCounts(
            total=counts.objects,
            streams=counts.streams,
            object_streams=counts.object_streams,
            cross_reference_streams=counts.cross_reference_streams,
            dictionaries=counts.dictionaries,
            arrays=counts.arrays,
        )
        if structure.pages is None or structure.page_objects is None:
            pages = contents = Unknown(UnknownReason.UNREADABLE, _PAGES)
        else:
            pages, contents = _pages(structure.page_objects, structure.pages)
    return Inspection(
        size=size,
        objects=objects,
        pages=pages,
        contents=contents,
        encryption=encryption,
        active_content=misread if found is None or found.misread else active_content(found),
        warnings=tuple(
            dict.fromkeys((*structure.warnings, *(() if found is None else found.warnings)))
        ),
    )


_FORMS = {
    protocols.ContentsForm.STREAM: ContentsForm.STREAM,
    protocols.ContentsForm.ARRAY: ContentsForm.ARRAY,
    protocols.ContentsForm.INDIRECT_ARRAY: ContentsForm.INDIRECT_ARRAY,
    protocols.ContentsForm.ABSENT: ContentsForm.ABSENT,
    protocols.ContentsForm.OTHER: ContentsForm.OTHER,
}

# The methods /CFM names (ISO 32000-2, Table 25), but /None.
_METHODS = {
    b"V2": CryptMethod.RC4,
    b"AESV2": CryptMethod.AES,
    b"AESV3": CryptMethod.AES,
}


def _pages(
    objects: tuple[tuple[int, int], ...], written: tuple[protocols.PageContents, ...]
) -> tuple[tuple[PageContents, ...], tuple[Contents, ...]]:
    """Each page's /Contents, and what they name: a stream or an indirect array
    once, by its object, anything else for its page."""
    listed: dict[object, int] = {}
    contents: list[Contents] = []
    pages = []
    # Each stream once, however often the arrays name it.
    streams: dict[tuple[int, int], ContentsStream] = {}

    def stream_of(written: protocols.ContentStreamLength) -> ContentsStream:
        objgen = (written.object_number, written.generation)
        if objgen not in streams:
            streams[objgen] = ContentsStream(
                ref=_ref(objgen),
                length=written.length
                if written.length >= 0
                else Unknown(
                    UnknownReason.INVALID, "/Length of the stream is below 0, which QPDF reads as 0"
                ),
                length_indirect=written.length_indirect,
            )
        return streams[objgen]

    for index, (objgen, one) in enumerate(zip(objects, written, strict=True)):
        key: object = index
        if one.form is protocols.ContentsForm.INDIRECT_ARRAY:
            key = ("array", one.array_object)
        elif one.form is protocols.ContentsForm.STREAM:
            key = ("stream", one.streams[0].object_number, one.streams[0].generation)
        if key not in listed:
            listed[key] = len(contents)
            contents.append(
                Contents(
                    streams=tuple(stream_of(s) for s in one.streams),
                    others=one.others,
                )
            )
        pages.append(
            PageContents(
                page_index=index,
                ref=_ref(objgen),
                form=_FORMS[one.form],
                array=None if one.array_object is None else _ref(one.array_object),
                contents=listed[key],
            )
        )
    return tuple(pages), tuple(contents)


def _encryption(written: protocols.EncryptionEntries, *, misread: bool) -> Encryption:
    """The public values; where QPDF mis-read an object, an entry it finds
    absent is unknown."""
    v, r, p = written.v, written.r, written.p
    if v not in (1, 2, 4, 5) or r is None or p is None:
        # QPDF opens no file whose /V, /R or /P is absent or no integer, nor
        # one whose /V is 0, 3 or 6 (measured); should it ever, no value is
        # made up.
        raise EngineError(
            engine="qpdf",
            detail="QPDF read an encryption dictionary whose /V is none of 1, 2, 4 and 5, "
            "or whose /R or /P is no integer",
        )

    def entry(value: object) -> object:
        if value is None and misread:
            return Unknown(UnknownReason.UNREADABLE, _MISREAD_ENTRY)
        return value

    stream_filter = entry(_name(written.stream_filter))
    string_filter = entry(_name(written.string_filter))
    embedded_file_filter = entry(_name(written.file_filter))
    defined: tuple[CryptFilter, ...] | Unknown
    if isinstance(written.crypt_filters, str):
        defined = Unknown(UnknownReason.INVALID, written.crypt_filters)
    else:
        # Each /CFM decoded once: the backend gives one value for a /CFM that
        # several filters share (qpdf._read_once).
        cfms: dict[bytes | str | None, object] = {}

        def cfm_of(method: bytes | str | None) -> object:
            if method not in cfms:
                cfms[method] = entry(_name(method))
            return cfms[method]

        defined = tuple(
            CryptFilter(name=_name(one.name), cfm=cfm_of(one.method))
            for one in written.crypt_filters
        )
    return Encryption(
        ref=None if written.dictionary is None else _ref(written.dictionary),
        filter=entry(_name(written.filter)),
        sub_filter=entry(_name(written.sub_filter)),
        v=v,
        r=r,
        length=entry(
            Unknown(UnknownReason.INVALID, "/Length of the encryption dictionary is no integer")
            if written.length_invalid
            else written.length
        ),
        p=p,
        permissions=_permissions(p, r),
        encrypt_metadata=entry(
            Unknown(UnknownReason.INVALID, "/EncryptMetadata is no boolean")
            if written.encrypt_metadata_invalid
            else written.encrypt_metadata
        ),
        stream_filter=stream_filter,
        string_filter=string_filter,
        embedded_file_filter=embedded_file_filter,
        crypt_filters=Unknown(UnknownReason.UNREADABLE, _MISREAD_ENTRY)
        if misread and not isinstance(defined, Unknown)
        else defined,
        key_bits=written.key_bits,
        stream_method=_method(v, "/StmF", stream_filter, defined, misread),
        string_method=_method(v, "/StrF", string_filter, defined, misread),
        embedded_file_method=_method(v, "/EFF", embedded_file_filter, defined, misread)
        if embedded_file_filter is not None
        else _method(v, "/StmF", stream_filter, defined, misread),
        user_password_matched=written.user_password_matched,
        owner_password_matched=written.owner_password_matched,
    )


def _method(
    v: int,
    key: str,
    name: Name | None | Unknown,
    defined: tuple[CryptFilter, ...] | Unknown,
    misread: bool,
) -> CryptMethod | Unknown:
    """The method ISO 32000-2 gives: RC4 for /V 1 and 2, which have no crypt
    filters (Table 20); for /V 4 and 5 that of the crypt filter `name`, which
    is /Identity where absent (Table 20, /StmF and /StrF; /EFF is /StmF's
    where absent), and otherwise one /CF defines, by its /CFM (Table 25).
    /CFM /None, its default, leaves the decryption to the security handler,
    and PDFium and QPDF decrypt such a filter differently (measured)."""
    if v in (1, 2):
        return CryptMethod.RC4
    if isinstance(name, Unknown):
        return name
    if name is None or name.raw == b"Identity":
        # PDFium takes /Identity for the identity filter, QPDF for a filter
        # /CF defines by that name (measured).
        if (
            name is not None
            and not isinstance(defined, Unknown)
            and any(isinstance(one.name, Name) and one.name.raw == b"Identity" for one in defined)
        ):
            return Unknown(
                UnknownReason.ENGINES_DISAGREE,
                "/CF defines a crypt filter named /Identity, which PDFium takes for the "
                "identity filter and QPDF for that filter",
            )
        if misread:
            return Unknown(UnknownReason.UNREADABLE, _MISREAD_ENTRY)
        return CryptMethod.IDENTITY
    if isinstance(defined, Unknown):
        return defined
    for one in defined:
        if isinstance(one.name, Name) and one.name.raw == name.raw:
            if isinstance(one.cfm, Unknown):
                return one.cfm
            if one.cfm is None or one.cfm.raw == b"None":
                return Unknown(
                    UnknownReason.ENGINES_DISAGREE,
                    f"the crypt filter {key} names has /CFM /None or none, which leaves the "
                    "decryption to the security handler, and PDFium takes it for RC4 where "
                    "QPDF names its method none or unknown",
                )
            method = _METHODS.get(one.cfm.raw)
            if method is None:
                return Unknown(
                    UnknownReason.INVALID,
                    f"the /CFM of the crypt filter {key} names is no method ISO 32000-2 defines",
                )
            return method
    if misread:
        return Unknown(UnknownReason.UNREADABLE, _MISREAD_ENTRY)
    return Unknown(UnknownReason.INVALID, f"{key} names a crypt filter /CF does not define")


def _name(written: bytes | str | None) -> Name | None | Unknown:
    if isinstance(written, str):
        return Unknown(UnknownReason.INVALID, written)
    return None if written is None else decode_name(written)


def _ref(objgen: tuple[int, int]) -> ObjectRef:
    return ObjectRef(object_number=objgen[0], generation=objgen[1])


def _check_type(name: str, value: object, kind: type | tuple[type, ...]) -> None:
    if kind is bool:
        if type(value) is not bool:
            raise TypeError(f"{name} must be a bool, not {type(value).__name__}")
        return
    if not isinstance(value, kind):
        names = (
            kind.__name__ if isinstance(kind, type) else " or ".join(one.__name__ for one in kind)
        )
        raise TypeError(f"{name} must be a {names}, not {type(value).__name__}")


def _check_count(name: str, value: object) -> None:
    if not _is_int(value):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} is at least 0, not {value}")


def _check_tuple(name: str, value: object, kind: type) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, not {type(value).__name__}")
    for item in value:
        if not isinstance(item, kind):
            raise TypeError(f"{name} holds {kind.__name__}s, not {type(item).__name__}")
