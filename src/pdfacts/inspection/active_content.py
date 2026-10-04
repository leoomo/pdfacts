"""The active content of a file: its actions and the entries that name them,
its XFA and its embedded files (docs/conventions.md, "Inspection").

Each action, each /Next and each /EF is listed once and named by its index,
so that what the file shares is not multiplied, however a caller takes the
values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pdfacts.backends import protocols
from pdfacts.core.refs import ObjectRef, _is_int
from pdfacts.core.strings import Name, decode_name
from pdfacts.core.values import Unknown, UnknownReason


class ActionPlace(StrEnum):
    """Where an entry names an action to run (ISO 32000-2, 12.6.3)."""

    # The catalog's /OpenAction, and the events WC, WS, DS, WP and DP of its /AA.
    DOCUMENT = "document"
    # An entry of the catalog's JavaScript name tree, run when the document opens.
    DOCUMENT_JAVASCRIPT = "document-javascript"
    # The events O and C of a page's /AA.
    PAGE = "page"
    # An annotation's /A, but a Movie annotation's, which is no action, and the
    # events E, X, D, U, Fo, Bl, PO, PC, PV and PI of its /AA, also where a
    # field no page lists writes them.
    ANNOTATION = "annotation"
    # The events K, F, V and C of a field's /AA.
    FIELD = "field"
    # An outline item's /A.
    OUTLINE_ITEM = "outline-item"


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """An action dictionary, or a stream read as its dictionary."""

    # /S; None where absent; unknown, invalid, where it is no name.
    kind: Name | None | Unknown
    # The object the dictionary is written in: its own, or the one that holds it.
    ref: ObjectRef
    # The actions its /Next names, as an index into ActiveContent.sequences;
    # None where /Next names none.
    next: int | None

    def __post_init__(self) -> None:
        if not (self.kind is None or isinstance(self.kind, (Name, Unknown))):
            raise TypeError(f"kind must be a Name, None or Unknown, not {type(self.kind).__name__}")
        _check_ref("ref", self.ref)
        _check_index("next", self.next, optional=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionTrigger:
    """An entry that names an action to run."""

    place: ActionPlace
    # The object the dictionary that holds the entry is written in: the
    # catalog, a page, an annotation, a field or an outline item, also where
    # its /AA is an object of its own; a node of the name tree; or the object
    # that holds it, where it is written in another.
    owner: ObjectRef
    # The first page that lists the annotation, and its index in that page's
    # /Annots, where the entry is an annotation's, or a field's that is an
    # annotation a page lists; the page alone for PAGE; None otherwise.
    page_index: int | None
    annotation_index: int | None
    # The entry's key as text: "OpenAction", "A", or an event of /AA; None
    # for an entry of the name tree, whose key is a string.
    event: str | None
    # The action the entry names, as an index into ActiveContent.actions.
    action: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "place", ActionPlace(self.place))
        _check_ref("owner", self.owner)
        _check_index("page_index", self.page_index, optional=True)
        _check_index("annotation_index", self.annotation_index, optional=True)
        placed = (self.page_index is not None, self.annotation_index is not None)
        allowed = _PLACED[self.place]
        if placed not in allowed:
            raise ValueError(f"an entry of the place {self.place.value} has no such page and index")
        if self.event is None:
            if self.place is not ActionPlace.DOCUMENT_JAVASCRIPT:
                raise ValueError("only an entry of the name tree has no event")
        elif type(self.event) is not str or not self.event:
            raise TypeError(f"event must be a str, not empty, or None, not {self.event!r}")
        elif self.place is ActionPlace.DOCUMENT_JAVASCRIPT:
            raise ValueError("an entry of the name tree has no event")
        elif self.event not in _EVENTS[self.place]:
            raise ValueError(f"{self.event} is no event of the place {self.place.value}")
        _check_index("action", self.action, optional=False)


# Which of page_index and annotation_index each place gives: a page's entry
# its page; an annotation's both, or neither for a field no page lists; a
# field's both where the field is an annotation a page lists; the others
# neither.
# The keys each place names an action by (ISO 32000-2, 12.6.3, Tables 197 to
# 200; the catalog's /OpenAction, an annotation's and an outline item's /A).
_EVENTS = {
    ActionPlace.DOCUMENT: {"OpenAction", "WC", "WS", "DS", "WP", "DP"},
    ActionPlace.PAGE: {"O", "C"},
    ActionPlace.ANNOTATION: {"A", "E", "X", "D", "U", "Fo", "Bl", "PO", "PC", "PV", "PI"},
    ActionPlace.FIELD: {"K", "F", "V", "C"},
    ActionPlace.OUTLINE_ITEM: {"A"},
}

_PLACED = {
    ActionPlace.DOCUMENT: {(False, False)},
    ActionPlace.DOCUMENT_JAVASCRIPT: {(False, False)},
    ActionPlace.PAGE: {(True, False)},
    ActionPlace.ANNOTATION: {(True, True), (False, False)},
    ActionPlace.FIELD: {(True, True), (False, False)},
    ActionPlace.OUTLINE_ITEM: {(False, False)},
}


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddedFile:
    """A file specification whose /EF names its embedded file streams."""

    # The object the file specification is written in.
    specification: ObjectRef
    # The streams its /EF names, as an index into ActiveContent.embedded_streams.
    streams: int

    def __post_init__(self) -> None:
        _check_ref("specification", self.specification)
        _check_index("streams", self.streams, optional=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ActiveContent:
    """A file's actions, the entries that name them, its XFA and its embedded files.

    Every index points into its list: Action.next and ActionTrigger.action
    into `sequences` and `actions`, the entries of `sequences` into
    `actions`, EmbeddedFile.streams into `embedded_streams`.
    """

    # Every action, each once, in the order docs/conventions.md states.
    actions: tuple[Action, ...] | Unknown
    # The actions each /Next names, in order, as indices into `actions`.
    sequences: tuple[tuple[int, ...], ...] | Unknown
    # Every entry that names an action, each once.
    triggers: tuple[ActionTrigger, ...] | Unknown
    # The streams of /AcroForm's /XFA, in order; None where it has no /XFA;
    # unknown, invalid, where /XFA is neither a stream nor an array of pairs of
    # a packet's name and its stream.
    xfa: tuple[ObjectRef, ...] | None | Unknown
    # The file specifications whose /EF is a dictionary, in object order,
    # also where it names no stream.
    embedded_files: tuple[EmbeddedFile, ...]
    # The streams each /EF names, in the order of their keys' bytes, each once.
    embedded_streams: tuple[tuple[ObjectRef, ...], ...]

    def __post_init__(self) -> None:
        listed = (self.actions, self.sequences, self.triggers)
        unknown = [isinstance(value, Unknown) for value in listed]
        if any(unknown) and not all(unknown):
            raise ValueError("actions, sequences and triggers are all Unknown or none")
        if not all(unknown):
            _check_tuple("actions", self.actions, Action)
            _check_tuple("sequences", self.sequences, tuple)
            _check_tuple("triggers", self.triggers, ActionTrigger)
            for action in self.actions:
                _within("an action's next", action.next, self.sequences)
            for sequence in self.sequences:
                for index in sequence:
                    _check_index("an index of a sequence", index, optional=False)
                    _within("an index of a sequence", index, self.actions)
            for trigger in self.triggers:
                _within("a trigger's action", trigger.action, self.actions)
        if self.xfa is not None and not isinstance(self.xfa, Unknown):
            _check_tuple("xfa", self.xfa, ObjectRef)
        _check_tuple("embedded_files", self.embedded_files, EmbeddedFile)
        _check_tuple("embedded_streams", self.embedded_streams, tuple)
        for streams in self.embedded_streams:
            _check_tuple("a tuple of embedded_streams", streams, ObjectRef)
        for embedded in self.embedded_files:
            _within("an embedded file's streams", embedded.streams, self.embedded_streams)


def active_content(found: protocols.ActiveContent) -> ActiveContent:
    """The public values of QPDF's plain data, where QPDF mis-read no object."""
    # One ObjectRef for each object, however many values name it.
    refs: dict[tuple[int, int], ObjectRef] = {}

    def ref_of(objgen: tuple[int, int]) -> ObjectRef:
        if objgen not in refs:
            refs[objgen] = _ref(objgen)
        return refs[objgen]

    if found.actions is None or found.sequences is None or found.triggers is None:
        unknown = Unknown(
            UnknownReason.UNREADABLE,
            "the pages QPDF found are not the objects the file's page tree lists, so "
            "where an action is named to run is not known",
        )
        actions: tuple[Action, ...] | Unknown = unknown
        sequences: tuple[tuple[int, ...], ...] | Unknown = unknown
        triggers: tuple[ActionTrigger, ...] | Unknown = unknown
    else:
        # Each kind decoded once: the backend gives one value for a /S that
        # many actions share (qpdf._read_once).
        kinds: dict[bytes | str | None, Name | None | Unknown] = {}

        def kind_of(written: bytes | str | None) -> Name | None | Unknown:
            if written not in kinds:
                kinds[written] = _kind(written)
            return kinds[written]

        actions = tuple(
            Action(
                kind=kind_of(one.kind),
                ref=ref_of(one.written_in),
                next=one.next,
            )
            for one in found.actions
        )
        sequences = found.sequences
        triggers = tuple(
            ActionTrigger(
                place=_PLACES[one.place],
                owner=ref_of(one.owner),
                page_index=one.page,
                annotation_index=one.annotation,
                event=None if one.event is None else one.event.decode("ascii"),
                action=one.action,
            )
            for one in found.triggers
        )
    return ActiveContent(
        actions=actions,
        sequences=sequences,
        triggers=triggers,
        xfa=_xfa(found.xfa, ref_of),
        embedded_files=tuple(
            EmbeddedFile(specification=ref_of(one.specification), streams=one.streams)
            for one in found.embedded_files
        ),
        embedded_streams=tuple(
            tuple(ref_of(one) for one in streams) for streams in found.embedded_streams
        ),
    )


_PLACES = {
    protocols.ActionPlace.DOCUMENT: ActionPlace.DOCUMENT,
    protocols.ActionPlace.DOCUMENT_JAVASCRIPT: ActionPlace.DOCUMENT_JAVASCRIPT,
    protocols.ActionPlace.PAGE: ActionPlace.PAGE,
    protocols.ActionPlace.ANNOTATION: ActionPlace.ANNOTATION,
    protocols.ActionPlace.FIELD: ActionPlace.FIELD,
    protocols.ActionPlace.OUTLINE_ITEM: ActionPlace.OUTLINE_ITEM,
}


def _ref(objgen: tuple[int, int]) -> ObjectRef:
    return ObjectRef(object_number=objgen[0], generation=objgen[1])


def _check_ref(name: str, value: object) -> None:
    if not isinstance(value, ObjectRef):
        raise TypeError(f"{name} must be an ObjectRef, not {type(value).__name__}")


def _check_index(name: str, value: object, *, optional: bool) -> None:
    if value is None and optional:
        return
    if not _is_int(value):
        raise TypeError(
            f"{name} must be an int{' or None' if optional else ''}, not {type(value).__name__}"
        )
    if value < 0:
        raise ValueError(f"{name} starts at 0, not {value}")


def _kind(written: bytes | str | None) -> Name | None | Unknown:
    if isinstance(written, str):
        return Unknown(UnknownReason.INVALID, written)
    return None if written is None else decode_name(written)


def _xfa(
    written: tuple[tuple[int, int], ...] | str | None,
    ref_of: Callable[[tuple[int, int]], ObjectRef],
) -> tuple[ObjectRef, ...] | None | Unknown:
    if isinstance(written, str):
        return Unknown(UnknownReason.INVALID, written)
    return None if written is None else tuple(ref_of(one) for one in written)


def _check_tuple(name: str, value: object, kind: type) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, not {type(value).__name__}")
    for item in value:
        if not isinstance(item, kind):
            raise TypeError(f"{name} holds {kind.__name__}s, not {type(item).__name__}")


def _within(name: str, index: int | None, listed: tuple) -> None:
    if index is not None and index >= len(listed):
        raise ValueError(f"{name}, {index}, is not an index of a list of {len(listed)}")
