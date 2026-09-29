"""What the core asks the engines, and the plain data they answer with.

One protocol per capability (docs/architecture.md, rule 5). This module
imports no engine and nothing from pdfacts.core: the core depends on the
backends through it, and the backends may not import the public errors.

A backend reports what the engine says. It does not normalize values and
does not decide what is unknown; the core does.
"""

from __future__ import annotations

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
class Written:
    """A value as the file writes it."""

    kind: str
    # "string" and "name": bytes (a name without its slash, #xx resolved);
    # "integer": int; "real": a finite float; "boolean": bool; "null": None;
    # "array": tuple[Written, ...]; "dictionary", "stream", "other": None.
    data: bytes | int | float | bool | tuple[Written, ...] | None


@dataclass(frozen=True, slots=True)
class AnnotationEntry:
    """One entry of a page's /Annots, as the file writes it."""

    index: int  # position in the page's /Annots
    # None when the entry is written directly into /Annots, or is no dictionary.
    object_number: int | None
    generation: int | None
    dictionary: bool
    # /Subtype as name bytes; None when absent, not a name, or no dictionary.
    subtype: bytes | None


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
    # Keys of /AP /N in the dictionary's order, when /N is a dictionary; None
    # when /AP or /N is absent or /N is a stream.
    states: tuple[bytes, ...] | None
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


class ObjectReader(Protocol):
    """The objects of the file as they are written."""

    def page_count(self) -> int: ...

    def object_count(self) -> int | None: ...

    def objects_as_written(self) -> bool:
        """Whether each compressed object is named once in its stream, at its index."""
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

    def warnings(self) -> tuple[str, ...]: ...

    def close(self) -> None: ...
