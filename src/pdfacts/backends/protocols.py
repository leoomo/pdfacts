"""What the core asks the engines, and the plain data they answer with.

One protocol per capability (docs/architecture.md, rule 5). This module
imports no engine and nothing from pdfacts.core: the core depends on the
backends through it, and the backends may not import the public errors.

A backend reports what the engine says. It does not normalize values and
does not decide what is unknown; the core does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

# x0, y0, x1, y1 in user space.
Box = tuple[float, float, float, float]


class FailureKind(StrEnum):
    # The engine could not read the file.
    FORMAT = "format"
    # A password is needed, or the one given is wrong.
    PASSWORD = "password"
    # Anything else, including an engine used after close().
    OTHER = "other"


class EngineFailure(Exception):
    """An engine could not do what it was asked; the core translates it."""

    def __init__(self, kind: FailureKind | str, detail: str) -> None:
        super().__init__(FailureKind(kind), detail)
        self.kind = FailureKind(kind)
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass(frozen=True, slots=True)
class PageObject:
    """What the file says about one page object, read through QPDF."""

    object_number: int
    generation: int
    # As written, corners not put in order; None when absent or not four
    # numbers. An inherited value is looked up through /Parent.
    media_box: Box | None
    crop_box: Box | None
    # As written, for example -90 or 450; None when absent or not an integer.
    rotate: int | None
    # A number greater than 0, as a float; None otherwise.
    user_unit: float | None
    # True when the key is there and holds anything but such a number.
    user_unit_invalid: bool


@dataclass(frozen=True, slots=True)
class RawBitmap:
    """The pixels an engine rendered, copied out of its memory."""

    width: int
    height: int
    # Bytes per row; may be more than width * channels.
    row_length: int
    # 1 for grey, 3 for red, green, blue in that order.
    channels: int
    # row_length * height bytes; row 0 is the top.
    data: bytes


@dataclass(frozen=True, slots=True)
class RecoveredBytes:
    """A damaged file as QPDF rebuilt it, written anew."""

    data: bytes = field(repr=False)
    # Pages in the rebuilt document.
    page_count: int
    # What QPDF reported while it read the damaged file.
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecryptedBytes:
    """An encrypted file as QPDF reads it, written anew without encryption."""

    data: bytes = field(repr=False)
    # What QPDF reported while it read the file and wrote it.
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecryptedOtherwise:
    """An object the document refers to that QPDF and PDFium decrypt otherwise."""

    # The object it is written in; for a direct one, the indirect object that
    # holds it, None where the trailer holds it.
    objgen: tuple[int, int] | None
    # "crypt filter": a stream whose /Filter names /Crypt and whose
    # /DecodeParms give the filter's /Name; "cross-reference":
    # a stream /Type /XRef; "signature": a dictionary with a /Contents that
    # PDFium takes for a signature and QPDF does not; "metadata": the catalog's
    # /Metadata, under /EncryptMetadata false, not a stream /Type /Metadata
    # with /Subtype /XML at /V 4 or more; "trailer": an object the trailer
    # holds directly that holds a string.
    kind: str


class StateKeys(tuple[bytes | None, ...]):
    """The keys of an /AP /N dictionary, as a tuple, each as name bytes, or
    None for a name without bytes (Written, "invalid name").

    digest(): a SHA-256 of the keys, made once for each object, so that the
    keys the backend gives once for many widgets cost it once.
    """

    def digest(self) -> bytes:
        made = self.__dict__.get("_digest")
        if made is None:
            data = b"".join(
                b"\0" if key is None else b"\1" + len(key).to_bytes(8, "big") + key for key in self
            )
            made = hashlib.sha256(data).digest()
            self.__dict__["_digest"] = made
        return made


@dataclass(frozen=True, slots=True)
class Written:
    """A value as the file writes it."""

    kind: str
    # "string" and "name": bytes (a name without its slash, #xx resolved);
    # "integer": int; "real": a finite float; "boolean": bool; "null": None;
    # "array": tuple[Written, ...]; "dictionary", "stream", "other": None;
    # "invalid name": None, a name with a # not followed by two hexadecimal
    # digits (ISO 32000-2, 7.3.5), which has no bytes: QPDF holds a NUL in
    # place of the #, and PDFium reads it otherwise (measured).
    data: bytes | int | float | bool | tuple[Written, ...] | None
    # digest(), once it is asked for.
    _digest: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def digest(self) -> bytes:
        """A SHA-256 of the kind and what the value holds, an array by the
        digests of its items. Made once for each object: a value the backend
        gives once for many dictionaries costs it once."""
        if self._digest is None:
            if self.kind == "array":
                payload = b"".join(item.digest() for item in self.data)
            elif isinstance(self.data, bytes):
                payload = self.data
            else:
                payload = repr(self.data).encode()
            digest = hashlib.sha256(self.kind.encode() + b"\0" + payload).digest()
            object.__setattr__(self, "_digest", digest)
        return self._digest


@dataclass(frozen=True, slots=True)
class AnnotationEntry:
    """One entry of a page's /Annots, as the file writes it."""

    index: int  # position in the page's /Annots
    # None when the entry is written directly into /Annots, or is no dictionary.
    object_number: int | None
    generation: int | None
    dictionary: bool
    # /Subtype as name bytes; None when absent, not a name, or no dictionary;
    # a str saying why where it is a name without bytes (Written, "invalid
    # name").
    subtype: bytes | str | None


@dataclass(frozen=True, slots=True)
class FieldEntry:
    """A field dictionary's own entries, as the file writes them."""

    object_number: int | None  # None for a dictionary written directly
    generation: int | None
    # Of b"T", b"TU", b"FT", b"Ff", b"V", b"DV", b"Opt": those present.
    entries: Mapping[bytes, Written]
    kids: bool  # /Kids is present and an array
    subtype_widget: bool  # /Subtype is the name Widget


@dataclass(frozen=True, slots=True)
class KidEntry:
    """One entry of a /Kids array, as the file writes it."""

    # None for an entry written directly, null, or not a dictionary.
    object_number: int | None
    generation: int | None
    widget: bool  # a dictionary whose /Subtype is the name Widget


@dataclass(frozen=True, slots=True)
class Kids:
    """The entries of one /Kids array, with its widget positions."""

    entries: tuple[KidEntry, ...]
    # For each indirect widget entry, its positions among the widget entries,
    # direct ones counted too.
    widget_positions: Mapping[tuple[int, int], tuple[int, ...]]
    all_widgets: bool  # every entry is a widget dictionary


@dataclass(frozen=True, slots=True)
class WidgetEntry:
    """One widget of a page, its own entries and the dictionaries above it."""

    annotation: AnnotationEntry
    # Of b"Rect", b"F", b"AS", b"MK/BC", b"MK/BG", b"BS/W", b"BS/S": those present.
    entries: Mapping[bytes, Written]
    # Keys of /AP /N in the dictionary's order, when /N is a dictionary, None
    # for a key that is a name without bytes (Written, "invalid name"); None
    # when /AP or /N is absent or /N is a stream.
    states: StateKeys | None
    states_invalid: bool  # /AP is not a dictionary, or /N is neither a dictionary nor a stream
    mk_invalid: bool  # /MK is present and not a dictionary
    bs_invalid: bool  # /BS is present and not a dictionary
    chain: tuple[FieldEntry, ...]  # the widget's own dictionary, then each /Parent; at most 33
    chain_loops: bool  # a /Parent named a dictionary already in the chain
    chain_cut: bool  # the 33rd dictionary has a /Parent, which was not followed
    parent_invalid: bool  # a /Parent in the chain is present and not a dictionary
    # The /Kids of the widget's own /Parent; None without an indirect /Parent
    # or when it has no /Kids array.
    parent_kids: Kids | None


@dataclass(frozen=True, slots=True)
class PageAnnotations:
    """The /Annots of one page and its widgets, as the file writes them."""

    # () when /Annots is absent or not an array.
    entries: tuple[AnnotationEntry, ...]
    not_an_array: bool
    # One per entry that is a dictionary with /Subtype /Widget, in order.
    widgets: tuple[WidgetEntry, ...]


@dataclass(frozen=True, slots=True)
class FieldNode:
    """One node of the field tree, reached once through /Fields or /Kids."""

    entry: FieldEntry
    # Per entry of /Kids, the index of its node, None for an entry that is no
    # dictionary; None instead of a tuple when /Kids is an array that was not
    # followed (32 levels below a root).
    kids: tuple[int | None, ...] | None
    parent: int | None  # the node through which it was first reached; None for a root
    # Its /Parent is that node's dictionary (both indirect, same object); for
    # a root: it has no /Parent.
    parent_matches: bool
    # Named by two entries of /Fields or /Kids, or held by an indirect /Kids
    # array met twice.
    reached_twice: bool


@dataclass(frozen=True, slots=True)
class FieldTree:
    """The document's field tree from /AcroForm /Fields, each node once."""

    # Depth first from /AcroForm /Fields in array order, each node once.
    nodes: tuple[FieldNode, ...]
    roots: tuple[int | None, ...]  # per entry of /Fields, like FieldNode.kids
    # /AcroForm not a dictionary, /Fields absent or not an array, or an entry
    # of /Fields that is no dictionary.
    invalid: bool
    cut: bool  # a node 32 levels below a root has /Kids, which were not followed


@dataclass(frozen=True, slots=True)
class AnnotationView:
    """What PDFium reads in the annotation dictionary at an index of a page's /Annots."""

    index: int
    widget: bool  # FPDFAnnot_GetSubtype is FPDF_ANNOT_WIDGET
    # FPDFAnnot_GetRect, corners as PDFium gives them. PDFium gives (0, 0, 0, 0)
    # for an absent /Rect, one of fewer than four numbers or one it cannot hold,
    # and returns true (measured); None where it returns false.
    rect: Box | None
    flags: int  # FPDFAnnot_GetFlags, as PDFium gives it (bit 32 makes it negative)
    t_type: int  # FPDFAnnot_GetValueType for "T"
    t: str  # FPDFAnnot_GetStringValue for "T"
    as_type: int  # FPDFAnnot_GetValueType for "AS"
    as_text: str  # FPDFAnnot_GetStringValue for "AS"
    parent_type: int  # FPDFAnnot_GetValueType for "Parent"
    # For each of b"V", b"DV", b"TU", b"FT": its value type and
    # FPDFAnnot_GetStringValue.
    values: Mapping[bytes, tuple[int, str]]


# The components of a colour as the content writes them for `g` or `rg` (and
# `G` or `RG`): one for a grey, three for red, green and blue.
Components = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Painted:
    """A path as QPDF reads it where a pattern colour is set
    (QpdfDocument.page_painted, C1)."""

    # The components of the colour QPDF reads for the fill and for the stroke,
    # (0.0,) for the black where the content sets none; None for a side whose
    # colour QPDF does not read, or that the path does not paint.
    fill: Components | None
    stroke: Components | None
    # As PDFium gives them for the path's painting operator: FPDF_FILLMODE_*
    # (0 none, 1 even-odd, 2 nonzero), and whether it strokes.
    fill_mode: int
    stroked: bool
    # The path's first point in its own space, as the content writes it.
    first: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SelectedAppearance:
    """The normal appearance QPDF selects for an annotation, as the file writes it."""

    data: bytes | None  # the stream's data decoded; None where QPDF cannot decode it
    bbox: Written | None  # /BBox; None when absent
    matrix: Written | None  # /Matrix; None when absent
    # Whether its content, or a form it draws, sets a pattern colour (scn or
    # SCN with a name); None where QPDF cannot read that content.
    uses_pattern: bool | None


@dataclass(frozen=True, slots=True)
class AnnotationAppearance:
    """One entry of a page's /Annots, with the appearance PDFium would draw for it."""

    annotation: AnnotationEntry
    rect: Written | None  # /Rect; None when absent or no dictionary
    flags: Written | None  # /F; None when absent or no dictionary
    # Selected as PDFium selects it (issue G2): /N when it is a stream; from a
    # dictionary of states, /AS, else /V, else the /V of /Parent, else /Off.
    # None when PDFium draws none, or where QPDF cannot tell.
    appearance: SelectedAppearance | None
    # Why QPDF cannot tell which appearance PDFium draws, where it cannot;
    # None otherwise.
    undecided: str | None


class AppearanceReader(Protocol):
    """Annotation appearances and pattern colours as the file writes them."""

    def appearances(self, index: int) -> tuple[AnnotationAppearance, ...]: ...

    def page_uses_pattern(self, index: int) -> bool | None: ...

    def page_painted(self, index: int, cap: int) -> tuple[Painted, ...] | None: ...

    def appearance_painted(
        self, index: int, position: int, cap: int
    ) -> tuple[Painted, ...] | None: ...


# PDFium's matrix of an object, composed with those of the forms that hold it:
# (a, b, c, d, e, f), x' = a*x + c*y + e, y' = b*x + d*y + f.
Transform = tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class DrawnPath:
    """A path object as PDFium reads it, forms that hold it resolved.

    Each value is None where the PDFium call that gives it returns false.
    """

    # Into page space for the page's content, into the appearance stream's own
    # space for an appearance; None where PDFium gives no matrix for the path
    # or a form that holds it.
    matrix: Transform | None
    # Per point: FPDF_SEGMENT_* kind, x, y in the path's own space, and whether
    # it closes its subpath; a curve is three points in a row. None where
    # PDFium gives no segment or point at an index it counts.
    segments: tuple[tuple[int, float, float, bool], ...] | None
    fill_mode: int | None  # FPDF_FILLMODE_*: 0 none, 1 even-odd, 2 nonzero
    stroke: bool | None
    fill_colour: tuple[int, int, int, int] | None
    stroke_colour: tuple[int, int, int, int] | None
    width: float | None  # in the path's own space
    dash: tuple[float, ...] | None  # in the path's own space; () for a solid line
    dash_phase: float | None
    cap: int | None  # FPDF_LINECAP_*
    join: int | None  # FPDF_LINEJOIN_*


@dataclass(frozen=True, slots=True)
class DrawnImage:
    """An image object as PDFium reads it: where it maps the unit square, and its metadata.

    The metadata are None where FPDFImageObj_GetImageMetadata returns false.
    Where PDFium cannot decode the image, it returns true all the same, with
    colour space 0 and 0 bits per pixel (measured, issue #58).
    """

    matrix: Transform | None  # as for DrawnPath
    width: int | None  # pixels, as the image's dictionary gives them
    height: int | None
    colour_space: int | None  # FPDF_COLORSPACE_*, 0 where PDFium reports none
    bits_per_pixel: int | None  # as PDFium decodes the image


@dataclass(frozen=True, slots=True)
class Drawing:
    """The paths and images of a page's content, or of one appearance, in drawing order."""

    objects: tuple[DrawnPath | DrawnImage, ...]
    # Set when reading stopped at `cap`, before the last objects were read.
    stopped: bool
    # Set when a form lies deeper than PDFium reads forms, 40 levels: it is an
    # object without children, and what it draws is not among the objects.
    too_deep: bool


@dataclass(frozen=True, slots=True)
class AppearanceDrawing:
    """What PDFium draws for one annotation: its normal appearance's objects and text."""

    index: int  # the annotation's position in the page's /Annots
    # FPDFAnnot_GetAP, normal mode: the text of the stream the objects come
    # from, as PDFium decodes it: each byte through PDFDocEncoding, undefined
    # bytes as U+0000, and data that begins with FE FF as UTF-16BE (measured,
    # issue G3). "" where there is none, and for an empty stream.
    appearance_text: str
    flags: int  # FPDFAnnot_GetFlags, signed as PDFium gives it
    drawing: Drawing


class DrawingReader(Protocol):
    """The paths and images an engine that interprets the page finds."""

    def page_drawing(self, index: int, cap: int | None) -> Drawing: ...

    def appearance_drawings(
        self, index: int, cap: int | None
    ) -> tuple[AppearanceDrawing | None, ...]: ...


@dataclass(frozen=True, slots=True)
class TextCharacter:
    """A character of a page's content as PDFium's text page reads it.

    Each value is None where the PDFium call that gives it returns false, or
    answers -1 to a question.
    """

    code: int  # FPDFText_GetUnicode: the character PDFium maps the code to
    # FPDFText_HasUnicodeMapError: PDFium found no character for the code and
    # gives the code itself (measured, issue T1).
    map_error: bool | None
    # FPDFText_IsHyphen: a hyphen at the end of a line, which PDFium gives as
    # U+0002 (measured, issue T1).
    hyphen: bool | None
    origin: tuple[float, float] | None  # FPDFText_GetCharOrigin, in page space
    # FPDFText_GetMatrix: the text matrix with the horizontal scaling and the
    # rise, and the matrices of the content and of the forms that hold the
    # text; the font size is not in it. Its translation is where the text
    # object begins, not the character: that is the origin (measured, issue T1).
    matrix: Transform | None
    # FPDFText_GetFontSize: the size Tf sets, below 0 where Tf sets it so.
    # None where the character has no text object: PDFium then gives 1. Text
    # at size 0 is not on the text page at all (measured, issue T1).
    font_size: float | None
    # FPDFText_GetLooseCharBox, in page space: left, bottom, right, top. Along
    # the baseline, from the nearer of the origin and the ink to the farther of
    # the advance and the ink; across, the ascent and descent held within the
    # font's /FontBBox, then reaching as far as the ink. For some characters,
    # mostly hyphens in fonts the file embeds, the ink box alone (measured,
    # issue T1). It does not follow from the other values.
    loose_box: Box | None
    # FPDFTextObj_GetTextRenderMode of the character's text object: 0 to 7 as
    # Tr sets it; 3 and 7 paint nothing.
    render_mode: int | None
    # FPDFFont_GetAscent and FPDFFont_GetDescent of the text object's font, per
    # unit of font size; the descent is below 0 where it lies below the
    # baseline.
    ascent: float | None
    descent: float | None


@dataclass(frozen=True, slots=True)
class PageText:
    """The characters of a page's content, in content order.

    Content order: by text object, as a walk of the page's objects that goes
    into each form where it is drawn meets them; within one text object, as
    PDFium's text page gives them. The text page itself puts the text of one
    line in order of x (measured, issue T1). The characters PDFium adds of
    its own, spaces and line breaks, are left out (FPDFText_IsGenerated).
    """

    characters: tuple[TextCharacter, ...]
    # Set when characters beyond `cap` were left out.
    stopped: bool
    # Set when a form lies deeper than PDFium reads forms, 40 levels: whatever
    # text it would draw is not among the characters (measured, issue T1).
    too_deep: bool


class TextReader(Protocol):
    """The text an engine that interprets the page reads."""

    def page_text(self, index: int, cap: int | None) -> PageText: ...


@dataclass(frozen=True, slots=True)
class ContentGlyph:
    """A code a text-showing operator of a page's content shows, as pdfacts
    reads the content on QPDF's tokens (TextRules.V2)."""

    # The text the font maps the code to; None where it maps it to none.
    text: str | None
    # Set where the font's ToUnicode maps the code to bytes that are not
    # UTF-16BE; text is then None.
    text_invalid: bool
    # The text rendering matrix: text space at the font size, the horizontal
    # scaling and the rise, to user space (ISO 32000-1, 9.4.4). The origin is
    # its translation.
    matrix: Transform
    # The advance of the code, in text space units at a font size of 1: from
    # the font's widths, without the character and word spacing.
    width: float
    # The font's ascent and descent, in the same units; None where the font
    # gives none.
    ascent: float | None
    descent: float | None
    # The detail where the font's heights are invalid; ascent and descent are
    # then not to be used.
    heights_invalid: str | None
    # As Tr sets it; ISO 32000-1 allows 0 to 7.
    render_mode: int


@dataclass(frozen=True, slots=True)
class ContentText:
    """The codes a page's content shows, in content order: the page's content
    first, a form's where it is drawn.

    `unread` is set where the content cannot be read so: the reason, one of
    "not-read", "invalid-in-file" and "unreadable" (pdfacts.UnknownReason),
    and its detail; `glyphs` are then empty.
    """

    glyphs: tuple[ContentGlyph, ...]
    # Set when codes beyond `cap` were left out.
    stopped: bool
    unread: tuple[str, str] | None


class ContentTextReader(Protocol):
    """The text pdfacts reads from a page's content itself (TextRules.V2)."""

    def page_content_text(self, index: int, cap: int | None) -> ContentText: ...


class AnnotationViewer(Protocol):
    """Annotation dictionaries as an engine that interprets the page reads them."""

    def annotation_count(self, index: int) -> int: ...

    def annotation_views(self, index: int) -> tuple[AnnotationView | None, ...]: ...


class GeometryReader(Protocol):
    """Page geometry as an engine that interprets the page sees it."""

    def page_count(self) -> int: ...

    def visible_box(self, index: int) -> Box: ...

    def rotation(self, index: int) -> int: ...

    def close(self) -> None: ...


class Renderer(Protocol):
    """A page drawn by an engine that interprets it.

    `crop` is (left, bottom, right, top): how much is cut off each side of the
    displayed page, in display units before scaling. The backend knows
    nothing of user space; the core computes `crop`.
    """

    def render(
        self, index: int, *, scale: float, crop: tuple[float, float, float, float], rgb: bool
    ) -> RawBitmap: ...


class ContentsForm(StrEnum):
    """How a page writes /Contents."""

    STREAM = "stream"
    ARRAY = "array"
    # An array reached through a reference.
    INDIRECT_ARRAY = "indirect array"
    # No key, a null, or a reference to an object the file lacks, which is a
    # null (ISO 32000-2, 7.3.10).
    ABSENT = "absent"
    # Anything else: a number, a name, a dictionary.
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ContentStreamLength:
    """A stream of a page's /Contents and the /Length its dictionary writes.

    QPDF, its recovery off as open() reads, reads a stream only where /Length
    is an integer that leads to endstream; any other it reads as null and
    warns that it mis-read the object (FileStructure.misread). A /Length
    below 0 it reads as 0, warning of no mis-read object (measured): the
    length is then that integer, as written.
    """

    object_number: int
    generation: int
    length: int
    # /Length is a reference to the integer.
    length_indirect: bool


@dataclass(frozen=True, slots=True)
class PageContents:
    """What a page writes in /Contents."""

    form: ContentsForm
    # The object of an indirect array; None otherwise.
    array_object: tuple[int, int] | None
    # The streams /Contents names, in order.
    streams: tuple[ContentStreamLength, ...]
    # The entries of the array that are not streams, a null included; 1 for
    # a /Contents of the form OTHER.
    others: int


@dataclass(frozen=True, slots=True)
class ObjectCounts:
    """The objects QPDF reads, and of them each kind."""

    objects: int
    # Streams other than object and cross-reference streams.
    streams: int
    object_streams: int
    cross_reference_streams: int
    dictionaries: int
    arrays: int


@dataclass(frozen=True, slots=True)
class CryptFilterEntry:
    """A crypt filter of the encryption dictionary's /CF, as written."""

    # The filter's key in /CF, as EncryptionEntries.filter gives a name.
    name: bytes | str
    # /CFM, as EncryptionEntries.filter; a str saying why also where the
    # filter is no dictionary.
    method: bytes | str | None


@dataclass(frozen=True, slots=True)
class EncryptionEntries:
    """A file's encryption as QPDF reads it.

    The dictionary's entries as written, None where absent; QPDF opens no
    file whose /V, /R or /P is absent or of another type (measured). And the
    length of the key QPDF's security handler derives, and the passwords it
    matched. QPDF's names for the methods are not given: it names /Identity
    and no /CFM alike, and RC4 of /V 1 and 2 "none" (measured); the methods
    follow from the entries (inspection.report).
    """

    # The bytes of the names; None where absent; a str saying why where the
    # entry is no name, or a name QPDF cannot tell the bytes of, one with a
    # stray #.
    filter: bytes | str | None
    sub_filter: bytes | str | None
    v: int | None
    r: int | None
    # /Length, in bits.
    length: int | None
    # True when the key is there and holds anything but an integer.
    length_invalid: bool
    p: int | None
    encrypt_metadata: bool | None
    # True when the key is there and holds anything but a boolean.
    encrypt_metadata_invalid: bool
    # QPDF's: the key's length in bits.
    key_bits: int
    # Whether the password given matched the user's, and whether it matched
    # the owner's; both where the two passwords are the same.
    user_password_matched: bool
    owner_password_matched: bool
    # The object the encryption dictionary is written in; None where the
    # trailer holds it directly.
    dictionary: tuple[int, int] | None
    # /StmF, /StrF and /EFF, as `filter`.
    stream_filter: bytes | str | None
    string_filter: bytes | str | None
    file_filter: bytes | str | None
    # The crypt filters /CF defines, in the order of their keys' bytes; a str
    # saying why where /CF is no dictionary.
    crypt_filters: tuple[CryptFilterEntry, ...] | str


@dataclass(frozen=True, slots=True)
class FileStructure:
    """What the inspection reads of a file's structure, through QPDF."""

    # QPDF warned, reading every object, that it mis-read one (issue #62's
    # rule). It reads such an object as null, or as something else, and drops
    # a dictionary's key whose value it reads as null (measured), so a page's
    # /Contents can look absent; the warning names the object, not what
    # refers to it.
    misread: bool
    # None where `misread`.
    objects: ObjectCounts | None
    # None where the file is not encrypted.
    encryption: EncryptionEntries | None
    # Each page's /Contents, in page order; None where `misread`, or where
    # the pages QPDF found are not the objects the file's page tree lists.
    pages: tuple[PageContents, ...] | None
    # What QPDF warned of while it read these, in the order it warned.
    warnings: tuple[str, ...]
    # The page objects the file's page tree lists, in page order, read
    # without reading the pages; None where `pages` is.
    page_objects: tuple[tuple[int, int], ...] | None


class ActionPlace(StrEnum):
    """Where an entry names an action to run (ISO 32000-2, 12.6.3)."""

    # The catalog's /OpenAction, and the events WC, WS, DS, WP and DP of its
    # /AA.
    DOCUMENT = "document"
    # An entry of the catalog's JavaScript name tree, run when the document
    # opens.
    DOCUMENT_JAVASCRIPT = "document javascript"
    # The events O and C of a page's /AA.
    PAGE = "page"
    # An annotation's /A, but a Movie annotation's, which is no action, and the
    # events E, X, D, U, Fo, Bl, PO, PC, PV and PI of its /AA; also those
    # events in the /AA of a field no page lists, which MuPDF reads for the
    # field's widgets, if it has any, through /Parent.
    ANNOTATION = "annotation"
    # The events K, F, V and C of a field's /AA. The fields: the dictionaries
    # of /AcroForm's /Fields and of their /Kids; those of its /CO, whose C
    # MuPDF runs; the widget annotations the pages list, which PDFium takes
    # for fields once a page is loaded; and the /Parent of each, and of that.
    # PDFium reads a field's events from the nearest /AA up its /Parent,
    # MuPDF each event from the nearest /AA that holds it (measured): an
    # entry is listed where it is written.
    FIELD = "field"
    # An outline item's /A.
    OUTLINE_ITEM = "outline item"


@dataclass(frozen=True, slots=True)
class Action:
    """An action dictionary (ISO 32000-2, 12.6.2), or a stream read as its
    dictionary."""

    # /S: the bytes of the name; None where /S is absent; a str saying why
    # where it is no name. QPDF reads a name without bytes only with a warning
    # that it mis-read the object (measured), which leaves ActiveContent
    # unknown.
    kind: bytes | str | None
    # The object the dictionary is written in: its own, or the object that
    # holds it.
    written_in: tuple[int, int]
    # The actions its /Next names, as an index into ActiveContent.sequences;
    # None where /Next names none. Actions whose /Next is the same give the
    # same index.
    next: int | None


@dataclass(frozen=True, slots=True)
class ActionTrigger:
    """An entry that names an action to run."""

    place: ActionPlace
    # The object the dictionary that holds the entry is written in, as
    # Action.written_in: that of the catalog, a page, an annotation, a field,
    # an outline item or a node of the name tree, or of the object that holds
    # it.
    owner: tuple[int, int]
    # For PAGE, the page's index. For ANNOTATION, and for FIELD where the field
    # is an annotation a page lists, the index of the first page that lists
    # it; None otherwise.
    page: int | None
    # Where `page` is an annotation's, its index in that page's /Annots, which
    # with the page identifies it ("Object identity"); None otherwise.
    annotation: int | None
    # The entry's key: OpenAction, A, or an event of its place's /AA, as
    # Action.kind; None for an entry of the name tree, whose key is a string.
    event: bytes | None
    # The action the entry names, as an index into ActiveContent.actions.
    action: int


@dataclass(frozen=True, slots=True)
class EmbeddedFile:
    """A file specification whose /EF names its embedded file streams."""

    # The object the file specification is written in.
    specification: tuple[int, int]
    # The streams its /EF names, as an index into
    # ActiveContent.embedded_streams. File specifications whose /EF is the same
    # give the same index.
    streams: int


@dataclass(frozen=True, slots=True)
class ActiveContent:
    """A file's actions, its XFA and its embedded files, through QPDF.

    A stream is read as its dictionary wherever a dictionary is read.
    """

    # As FileStructure.misread.
    misread: bool
    # Every action, each once: those the triggers name, each followed by those
    # its /Next names, depth first; then those no trigger reaches, in object
    # order. None where `misread`, or where the pages QPDF found are not the
    # objects the file's page tree lists.
    actions: tuple[Action, ...] | None
    # The actions each /Next names, in order, as indices into `actions`: one
    # entry for each /Next as it is written, so that what several actions
    # share is listed once, in the order the actions are listed. None where
    # `actions` is.
    sequences: tuple[tuple[int, ...], ...] | None
    # Every entry that names an action, each once: the catalog's, the name
    # tree's, each page's followed by its annotations', the fields' (each
    # field's events, then, for a field no page lists, an annotation's events
    # in its /AA), the outline's. None where `actions` is.
    triggers: tuple[ActionTrigger, ...] | None
    # The streams of /AcroForm's /XFA, in order; None where `misread`, or where
    # /AcroForm has no /XFA; a str saying why where /XFA is neither a stream
    # nor an array.
    xfa: tuple[tuple[int, int], ...] | str | None
    # In object order; None where `misread`.
    embedded_files: tuple[EmbeddedFile, ...] | None
    # The streams each /EF names, in the order of their keys, each once: one
    # entry for each /EF as it is written, in the order the file
    # specifications are listed. None where `misread`.
    embedded_streams: tuple[tuple[tuple[int, int], ...], ...] | None
    # What QPDF warned of while it read these, in the order it warned.
    warnings: tuple[str, ...]


class StructureReader(Protocol):
    """What the inspection asks QPDF of a file's structure."""

    def structure(self) -> FileStructure: ...


class ActiveContentReader(Protocol):
    """What the inspection asks QPDF of a file's active content."""

    def active_content(self) -> ActiveContent: ...


class ObjectReader(Protocol):
    """The objects of the file as they are written."""

    def page_count(self) -> int: ...

    def object_count(self) -> int | None: ...

    def objects_as_written(self) -> bool:
        """Whether each compressed object is named once in its stream, at its index."""
        ...

    def security_handler_revision(self) -> int | None:
        """/R of the security handler; None where the file is read as not encrypted."""
        ...

    def cross_reference_count(self) -> int:
        """The objects the cross-reference holds, which QPDF reads whatever /Size says."""
        ...

    def annotation_count(self, index: int) -> int | None:
        """The number of entries of the page's /Annots; None when it is no array."""
        ...

    def annotations(self, index: int) -> PageAnnotations:
        """The page's /Annots entries and its widgets, as the file writes them."""
        ...

    def field_tree(self) -> FieldTree | None:
        """The document's field tree from /AcroForm /Fields; None without /AcroForm."""
        ...

    def page_object(self, index: int) -> PageObject: ...

    def parsed_streams(self, index: int) -> int | None:
        """The content streams PDFium parses for the page; None when one cannot be read."""
        ...

    def warnings(self) -> tuple[str, ...]: ...

    def close(self) -> None: ...
