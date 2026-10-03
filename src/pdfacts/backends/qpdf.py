"""QPDF backend: object access, rewrite and decryption.

The only module allowed to import pikepdf.
"""

from __future__ import annotations

import io
import json
import math
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType

import pikepdf

from pdfacts.backends.protocols import (
    AnnotationAppearance,
    AnnotationEntry,
    Box,
    ContentsForm,
    ContentStreamLength,
    EncryptionEntries,
    EngineFailure,
    FailureKind,
    FieldEntry,
    FieldNode,
    FieldTree,
    FileStructure,
    KidEntry,
    Kids,
    ObjectCounts,
    PageAnnotations,
    PageContents,
    PageObject,
    RecoveredBytes,
    SelectedAppearance,
    WidgetEntry,
    Written,
)

# How far from the end QPDF looks for startxref (measured: it finds one that
# starts 1054 bytes before the end, not one that starts 1055 bytes before).
_STARTXREF_SEARCH = 1054
# White space as QPDF reads it around startxref, its offset and the sections
# and objects it looks for: PDF's six characters and the vertical tab
# (measured, review, round 8).
_SPACE = rb"[\x00\t\n\x0b\x0c\r ]"
# The header: %PDF- and a version, as QPDF takes it. Offsets in the file count
# from it, and it may follow other bytes (measured: QPDF and PDFium read such a
# file as written). It is looked for as far as classified() accepts it, one
# byte further than QPDF (review, round 8): where QPDF misses it, QPDF cannot
# read the last revision, and the file is refused or rebuilt.
_HEADER = re.compile(rb"%PDF-[0-9]+\.[0-9]+")
# startxref and the white space and comments before its offset, matched once
# and never inside the run of another, so that the file is read once however
# its startxrefs are laid out; possessive, so that a long run costs no memory.
_STARTXREF = re.compile(rb"startxref(?:" + _SPACE + rb"|%[^\r\n]*)++")
# The word startxref wherever it stands, also in the comment that follows
# another one. PDFium looks for it in the last 4096 bytes, QPDF in the last
# 1054 (_STARTXREF_SEARCH); one further from the end neither engine reads
# (measured).
_STARTXREF_WORD = re.compile(rb"startxref")
_STARTXREF_WINDOW = 4096 + len(b"startxref")
# White space and comments between the numbers and the keyword of an object
# header, and a sign on a number, which QPDF reads there (measured, reviews,
# rounds 9 and 11). An object header is looked for from every number, so what
# one gap may hold is bounded, and a crafted run of numbers and comments is
# not read to its end from each: at most eight runs of white space and
# comments, a comment ending at its end of line within 64 bytes. A header
# with more, or a longer comment, is not seen; no writer is known to write
# one.
_HEADER_GAP = rb"(?:" + _SPACE + rb"++|%[^\r\n]{0,64}[\r\n]){1,8}+"
_NUMBER = rb"[+-]?[0-9]+"
# The offset after startxref, in the forms QPDF reads through (measured): white
# space or comments before it, as above, a plus sign on it, a delimiter or the
# end of the file after it. It passes over a startxref without an offset, one
# glued to its offset and one whose offset other characters follow. An offset
# of more digits, leading zeros aside, names nothing in any file.
_OFFSET = re.compile(rb"\+?([0-9]+)(?![^\x00\t\n\x0b\x0c\r ()<>\[\]{}/%])")
_OFFSET_DIGITS = 20
# What the offset after startxref can name: a cross-reference table, or an
# object, which may be a cross-reference stream; QPDF, recovery off, tells
# whether it reads it. White space before it is allowed, more than QPDF reads
# (measured: one blank, not ten), and only the first bytes are looked at, so
# that a crafted file whose offsets all name a long run of white space is not
# read over and over.
_SECTION = re.compile(
    _SPACE + rb"*(?:xref|" + _NUMBER + _HEADER_GAP + _NUMBER + _HEADER_GAP + rb"obj)"
)
_REACH = 64
# Where the data of a stream begins, after its dictionary, and where it ends;
# endstream may follow the data directly (6 corpus files write it after a
# letter). The text of a startxref between the two ends no revision.
_STREAM_BOUNDARY = re.compile(rb">>" + _SPACE + rb"*stream(?=[\r\n])|endstream")
# "N G obj", where an object begins, in the forms QPDF reads.
_OBJECT_HEADER = re.compile(
    rb"(?<![0-9+\-])" + _NUMBER + _HEADER_GAP + _NUMBER + _HEADER_GAP + rb"obj(?![A-Za-z])"
)
# What a revision is written of: objects, a cross-reference table whose first
# numbers follow xref, a trailer whose dictionary follows it, either cut short
# at the end. Words alone, such as obj, stream or trailer in a web page
# appended to a file, are none (reviews, rounds 10 and 11).
_REVISION_PART = re.compile(
    _OBJECT_HEADER.pattern
    + rb"|(?<![A-Za-z])xref(?="
    + _SPACE
    + rb"*+(?:[0-9]|\Z))|(?<![A-Za-z])trailer(?="
    + _SPACE
    + rb"*+(?:<<|\Z))"
)


@dataclass(frozen=True)
class QpdfVersions:
    binding: str
    engine: str


def versions() -> QpdfVersions:
    return QpdfVersions(
        binding=str(pikepdf.__version__),
        engine=str(pikepdf.__libqpdf_version__),
    )


@contextmanager
def _translated() -> Iterator[None]:
    """Every exception from the engine as EngineFailure.

    MemoryError passes unchanged, and so does everything that is not an
    Exception, such as KeyboardInterrupt. Inside, numbers come back as int and
    Decimal whatever conversion mode the caller chose for pikepdf; a caller's
    "explicit" mode would otherwise turn every number into a pikepdf object.
    """
    try:
        with pikepdf.implicit_conversion():
            yield
    except MemoryError:
        raise
    except EngineFailure:
        raise
    # Not a PdfError in pikepdf 10; checked first in case it becomes one.
    except pikepdf.PasswordError as error:
        raise EngineFailure(FailureKind.PASSWORD, str(error)) from error
    except pikepdf.PdfError as error:
        raise EngineFailure(FailureKind.FORMAT, str(error)) from error
    except Exception as error:
        raise EngineFailure(FailureKind.OTHER, str(error)) from error


def _opened(
    data: bytes, password: str | None, sources: list[_Input], **options: bool
) -> pikepdf.Pdf:
    """pikepdf.open with the password given only where the file needs one.

    pikepdf warns when a password is given for a file that is not encrypted,
    and quieting it with warnings.catch_warnings would change the warning
    state of the whole process. So the file is opened without a password
    first, and again with it when that fails or the file is encrypted, so
    that a wrong password is refused. The input the returned document reads
    from is added to `sources`, for the caller to close.
    """
    source = _Input(data)
    try:
        pdf = pikepdf.open(source, password="", **options)
    except pikepdf.PasswordError:
        source.close()
        if not password:
            raise
    except BaseException:
        source.close()
        raise
    else:
        if not password or not pdf.is_encrypted:
            sources.append(source)
            return pdf
        pdf.close()
        source.close()
    source = _Input(data)
    sources.append(source)
    return pikepdf.open(source, password=password, **options)


def _check_index(index: int, count: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError(f"a page index is an int, not {type(index).__name__}")
    if not 0 <= index < count:
        raise IndexError(f"page index {index} is outside the {count} pages")


class _Input(io.BytesIO):
    """The bytes of a document.

    QPDF names its input in every warning, and by default that name holds a
    memory address, so the warnings of one file would differ between runs.
    """

    def __repr__(self) -> str:
        return "<input>"


class QpdfDocument:
    """A document opened by QPDF with recovery switched off: an ObjectReader, an
    AppearanceReader and a StructureReader.

    It holds the native handles; no method returns a pikepdf object. Values are
    reported as the file writes them: nothing is normalized, and whether a
    value is unknown is for the core to decide.

    Recovery being off does not keep QPDF from changing the document in
    memory. When it walks the page tree it gives a page without a usable media
    box US letter, turns a page written directly into /Kids into a new object,
    copies a page that is listed twice, drops a kid that is not a dictionary
    and flattens what is left, and may walk from another root; it says so in a
    warning at most. So the document is opened twice. The page list comes
    from the view QPDF walked, the values from a view nothing walks. When the
    pages QPDF found are not, index by index, the objects the file's page tree
    lists, no page has values: its object at an index may not be the file's.
    The structure is read from a third view (structure()).
    """

    def __init__(
        self,
        walked: pikepdf.Pdf,
        unwalked: pikepdf.Pdf,
        sources: list[_Input],
        pages: tuple[tuple[int, int], ...],
        pages_as_written: bool,
        objects_as_written: bool,
        cross_reference: int,
        data: bytes,
        password: str | None,
    ) -> None:
        # Use open(); this takes documents that are already open.
        self._walked = walked
        self._unwalked = unwalked
        self._sources = sources
        # The page objects as QPDF found them, read once: pikepdf takes time
        # in proportion to the page count for each access to its page list.
        self._pages = pages
        self._pages_as_written = pages_as_written
        self._objects_as_written = objects_as_written
        self._cross_reference = cross_reference
        # Plain data kept so that shared objects convert once (issue #36).
        self._kept = _Kept()
        self._warnings: list[str] = []
        # The same warnings as a set: a damaged file can give tens of thousands.
        self._known_warnings: set[str] = set()
        # The numbers of the compressed objects, read once (parsed_streams).
        self._compressed_objects: frozenset[int] | None = None
        # The input, for the view of structure().
        self._data = data
        self._password = password
        self._closed = False

    @classmethod
    def open(cls, data: bytes, password: str | None) -> QpdfDocument:
        """Open `data`. A damaged file is a FORMAT failure: recovery is off."""
        views: list[pikepdf.Pdf] = []
        sources: list[_Input] = []
        try:
            with _translated():
                for _ in range(2):
                    views.append(
                        _opened(
                            data,
                            password,
                            sources,
                            attempt_recovery=False,
                            inherit_page_attributes=False,
                        )
                    )
                walked, unwalked = views
                # Walked now, so that what QPDF changed is among the warnings
                # from the start.
                pages = tuple(page.obj.objgen for page in walked.pages)
                as_written = _page_objects_as_written(unwalked)
                table = unwalked.get_xref_table()
                objects_as_written = _objects_as_written(unwalked, table)
        except BaseException:
            for view in views:
                # The first exception is the one to report. MemoryError is
                # never swallowed.
                with suppress(EngineFailure), _translated():
                    view.close()
            for source in sources:
                source.close()
            raise
        return cls(
            walked,
            unwalked,
            sources,
            pages,
            pages == as_written,
            objects_as_written,
            len(table),
            data,
            password,
        )

    def page_count(self) -> int:
        """The number of pages QPDF found."""
        self._check_open()
        return len(self._pages)

    def object_count(self) -> int | None:
        """The /Size of the trailer; None unless it is an integer greater than 0.

        Objects are not counted instead: that is a different number.
        """
        self._check_open()
        with _translated():
            size = self._unwalked.trailer.get("/Size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return None
        return int(size)

    def objects_as_written(self) -> bool:
        """Whether each compressed object is named once in its object stream, at its index."""
        self._check_open()
        return self._objects_as_written

    def cross_reference_count(self) -> int:
        """The objects QPDF's cross-reference holds, in use or in an object stream.

        QPDF reads any of them that is asked for, whatever the trailer's /Size
        says: a /Size below their number gives a warning only (measured).
        """
        self._check_open()
        return self._cross_reference

    def annotation_count(self, index: int) -> int | None:
        """The number of entries of the page's /Annots; None when it is no array."""
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            annots = self._unwalked.get_object(objgen).get("/Annots")
        if annots is None:
            return 0
        if isinstance(annots, pikepdf.Array):
            return len(annots)
        return None

    def annotations(self, index: int) -> PageAnnotations:
        """The entries of the page's /Annots and its widgets, as the file writes them."""
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            annots = self._unwalked.get_object(objgen).get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                return PageAnnotations((), annots is not None, ())
            entries: list[AnnotationEntry] = []
            widgets: list[WidgetEntry] = []
            for position, item in enumerate(annots):
                entry = _annotation_entry(position, item)
                entries.append(entry)
                if entry.subtype != b"Widget":
                    continue
                item_objgen = (
                    None if entry.object_number is None else (entry.object_number, entry.generation)
                )
                kept = self._kept.widgets.get(item_objgen) if item_objgen else None
                if kept is not None:
                    # Listed again, on this page or another: converted once per
                    # document, with its own annotation.
                    widgets.append(replace(kept, annotation=entry))
                    continue
                one = _widget_entry(item, entry, self._kept)
                if item_objgen is not None:
                    self._kept.widgets[item_objgen] = one
                widgets.append(one)
            return PageAnnotations(tuple(entries), False, tuple(widgets))

    def field_tree(self) -> FieldTree | None:
        """The document's field tree from /AcroForm /Fields; None without /AcroForm."""
        self._check_open()
        if self._kept.tree_walked:
            return self._kept.tree
        with _translated():
            tree = _field_tree(self._unwalked, self._kept)
        self._kept.tree = tree
        self._kept.tree_walked = True
        return tree

    def _annotated_page(self, index: int) -> tuple[int, int]:
        """The objgen of page `index`, after the checks page_object() makes."""
        count = self.page_count()
        _check_index(index, count)
        if not self._pages_as_written:
            raise EngineFailure(
                FailureKind.FORMAT,
                "the pages QPDF found are not the objects the file's page tree lists, so "
                "the object QPDF has at a page index may not be the file's",
            )
        return self._pages[index]

    def page_object(self, index: int) -> PageObject:
        """The page's object and what the file writes on it and its ancestors.

        A FORMAT failure when the pages QPDF found are not the objects the
        file's page tree lists: a wrong object number is worse than none
        (docs/conventions.md, "Object identity").
        """
        count = self.page_count()
        _check_index(index, count)
        if not self._pages_as_written:
            raise EngineFailure(
                FailureKind.FORMAT,
                "the pages QPDF found are not the objects the file's page tree lists, so "
                "the object QPDF has at a page index may not be the file's",
            )
        objgen = self._pages[index]
        with _translated():
            page = self._unwalked.get_object(objgen)
            user_unit, user_unit_invalid = _user_unit(page.get("/UserUnit"))
            return PageObject(
                object_number=int(objgen[0]),
                generation=int(objgen[1]),
                media_box=_box(_inherited(page, "/MediaBox")),
                crop_box=_box(_inherited(page, "/CropBox")),
                rotate=_integer(_inherited(page, "/Rotate")),
                user_unit=user_unit,
                user_unit_invalid=user_unit_invalid,
            )

    def structure(self) -> FileStructure:
        """The file's objects by kind, its encryption and each page's /Contents.

        Read from a view of its own, opened for the call and closed after it,
        QPDF's recovery off as open() reads. QPDF reads an object it cannot
        read as written as null and warns, when it reads the object; reading
        every object in the views the other values come from would change
        those values (issue #62's rule, _misread). So this view's warnings go
        into the structure, not into warnings(). Where QPDF warned that it
        mis-read an object, the counts and the contents are not known. A
        page's /Contents is read from the page object the file's page tree
        lists; where the pages QPDF found are not those objects, no page's is
        known (as page_object()).
        """
        self._check_open()
        sources: list[_Input] = []
        try:
            with _translated():
                pdf = _opened(
                    self._data,
                    self._password,
                    sources,
                    attempt_recovery=False,
                    inherit_page_attributes=False,
                )
                try:
                    kinds = Counter(_object_kind(obj) for obj in pdf.objects)
                    encryption = _encryption_entries(pdf) if pdf.is_encrypted else None
                    pages = None
                    if self._pages_as_written:
                        pages = tuple(
                            _page_contents(pdf.get_object(objgen)) for objgen in self._pages
                        )
                    # Taken after the reading, so that they hold what it raised.
                    warnings = tuple(str(message) for message in pdf.get_warnings())
                finally:
                    pdf.close()
        finally:
            for source in sources:
                source.close()
        if any(not _countable(message) for message in warnings):
            return FileStructure(
                misread=True, objects=None, encryption=encryption, pages=None, warnings=warnings
            )
        objects = ObjectCounts(
            objects=sum(kinds.values()),
            streams=kinds["stream"],
            object_streams=kinds["object stream"],
            cross_reference_streams=kinds["cross-reference stream"],
            dictionaries=kinds["dictionary"],
            arrays=kinds["array"],
        )
        return FileStructure(
            misread=False, objects=objects, encryption=encryption, pages=pages, warnings=warnings
        )

    def appearances(self, index: int) -> tuple[AnnotationAppearance, ...]:
        """Each entry of the page's /Annots with the normal appearance PDFium would draw.

        () when /Annots is absent or not an array; annotation_count() tells
        the two apart. The appearance is selected as PDFium selects it
        (_selected_appearance). Where QPDF's parser has warned that it
        mis-read an object of the file (_misread), the selection is undecided
        and uses_pattern unknown: QPDF's view of the annotation is not the
        file's (measured, the review of the G2 draft, 2026-10-01).
        """
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            page = self._unwalked.get_object(objgen)
            annots = page.get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                return ()
            resources = _inherited(page, "/Resources")
            top = _Context(resources, ("page",), resources, ("page",))
            self.warnings()
            out = []
            for position, item in enumerate(annots):
                entry = _annotation_entry(position, item)
                if not entry.dictionary:
                    # QPDF can drop a whole annotation it cannot read; PDFium
                    # still draws it (measured, 2026-10-01), so say why.
                    out.append(
                        AnnotationAppearance(
                            entry,
                            None,
                            None,
                            None,
                            "QPDF warns that it misread an object of the file"
                            if self._misread()
                            else None,
                        )
                    )
                    continue
                kept = _Kept()
                rect = item.get("/Rect")
                flags = item.get("/F")
                selected = _selected_appearance(item)
                misread = self._misread()
                if misread and not isinstance(selected, str):
                    selected = "QPDF warns that it misread an object of the file"
                appearance = None
                if isinstance(selected, pikepdf.Stream):
                    appearance = _appearance(selected, top, kept)
                if misread and appearance is not None:
                    appearance = SelectedAppearance(
                        data=appearance.data,
                        bbox=appearance.bbox,
                        matrix=appearance.matrix,
                        uses_pattern=None,
                    )
                out.append(
                    AnnotationAppearance(
                        entry,
                        None if rect is None else _written(rect, 0, kept),
                        None if flags is None else _written(flags, 0, kept),
                        appearance,
                        selected if isinstance(selected, str) else None,
                    )
                )
            return tuple(out)

    def page_uses_pattern(self, index: int) -> bool | None:
        """Whether the page's content, or a form it draws, sets a pattern colour.

        A pattern colour is set by scn or SCN with a name; PDFium gives no
        colour for it that pdfacts can use (issue G2). None where QPDF cannot
        read the content, or a name an operator of it names has no bytes
        (_content_name).
        """
        self._check_open()
        objgen = self._annotated_page(index)
        with _translated():
            page = self._unwalked.get_object(objgen)
            resources = _inherited(page, "/Resources")
            top = _Context(resources, ("page",), resources, ("page",))
            # The joined content lives in the counter's scratch document, which
            # must outlive the parsing.
            counter = _ParsedStreams(self._compressed())
            self.warnings()
            # Where QPDF warns that it mis-read an object, what the content
            # sets is as unreadable (issue #62's rule).
            found = _uses_pattern(lambda: counter._page_content(page), top)
            return None if self._misread() else found

    def _misread(self) -> bool:
        """Whether QPDF's parser has warned that it mis-read an object of the file.

        The unwalked view's queue is drained into the collected warnings, so
        the answer covers warnings raised before this call too (issue #62).
        """
        fresh = [str(message) for message in self._unwalked.get_warnings()]
        for message in fresh:
            if message not in self._known_warnings:
                self._warnings.append(message)
                self._known_warnings.add(message)
        return any(not _countable(message) for message in (*fresh, *self._warnings))

    def parsed_streams(self, index: int) -> int | None:
        """The content streams PDFium parses for page `index` and its annotations.

        Counted as PDFium parses them (_ParsedStreams), without parsing
        anything for PDFium: each form each time it is drawn, down to
        _FORM_DEPTH; a tiling pattern, a soft mask group and the glyph
        procedures of a Type 3 font once, when first used; every stream of
        each annotation's normal appearance. None when the count cannot be
        had: a content stream QPDF cannot read, a warning of QPDF's parser
        about an object it mis-read, or an object the two engines read
        differently (measured, 2026-10-01: a stream-object body inside an
        object stream, which QPDF reads as a dictionary and PDFium parses as
        a stream); a name without bytes that an operator of a content stream
        names (_content_name). A FORMAT failure as page_object() gives one.
        """
        objgen = self._annotated_page(index)
        with _translated():
            # Both views' warnings so far, collected and drained, so that the
            # queue read after the count holds only what it raised.
            self.warnings()
            page = self._unwalked.get_object(objgen)
            found = _ParsedStreams(self._compressed()).page(page)
            fresh = [str(message) for message in self._unwalked.get_warnings()]
            for message in fresh:
                if message not in self._known_warnings:
                    self._warnings.append(message)
                    self._known_warnings.add(message)
            # QPDF warns of an object it mis-read when it reads it (measured,
            # #83): such a warning can sit in the collected warnings, from what
            # was read before, before this count begins, and one such object
            # read anywhere in the document leaves no page countable — what
            # PDFium makes of the same bytes may be more (measured,
            # 2026-10-01: a name with a #00). So a page counted at first can be
            # refused once another page was read (docs/conventions.md,
            # "Limits"). The corpus gives no warning.
            if any(not _countable(message) for message in (*fresh, *self._warnings)):
                return None
            return found

    def _compressed(self) -> frozenset[int]:
        """The numbers of the objects the cross-reference holds in object streams.

        QPDF reads such an object as what its body parses to, PDFium the body
        itself when it looks like a stream object; where the two differ, the
        page cannot be counted (measured, 2026-10-01).
        """
        if self._compressed_objects is None:
            with _translated():
                table = self._unwalked.get_xref_table()
            self._compressed_objects = frozenset(
                number for (number, _generation), entry in table.items() if entry.type == 2
            )
        return self._compressed_objects

    def warnings(self) -> tuple[str, ...]:
        """The warnings QPDF gave so far, for example about a media box it added.

        QPDF hands out each warning once and then forgets it, so they are
        collected here. A warning both views give is kept once. Some messages
        QPDF sends to pikepdf's logger instead, for example about a kid it
        drops; they are not among these.
        """
        self._check_open()
        with _translated():
            walked = [str(message) for message in self._walked.get_warnings()]
            unwalked = [str(message) for message in self._unwalked.get_warnings()]
        self._warnings.extend(walked)
        self._known_warnings.update(walked)
        for message in unwalked:
            if message not in self._known_warnings:
                self._warnings.append(message)
                self._known_warnings.add(message)
        return tuple(self._warnings)

    def close(self) -> None:
        """Close the document. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            with _translated():
                self._walked.close()
        finally:
            try:
                with _translated():
                    self._unwalked.close()
            finally:
                for source in self._sources:
                    source.close()

    def _check_open(self) -> None:
        # The backend's own flag: what the engine does with a closed handle
        # is not something to rely on.
        if self._closed:
            raise EngineFailure(FailureKind.OTHER, "closed")


def revision_part_follows(data: bytes) -> bool:
    """Whether part of a revision follows the end of the last whole revision.

    The end is where QPDF reads the file to (_revisions). Part of a revision
    is what _REVISION_PART finds after it, or a startxref that sends the
    engines to another section (_startxref_elsewhere). False when the file
    has no whole revision.
    """
    revisions = _revisions(data)
    ends = [end for end, _ in revisions]
    return _part_follows(data, ends) or _startxref_elsewhere(data, revisions)


def _part_follows(data: bytes, ends: list[int]) -> bool:
    return bool(ends) and _REVISION_PART.search(data, ends[-1]) is not None


def _startxref_elsewhere(data: bytes, revisions: list[tuple[int, int]]) -> bool:
    """Whether the last readable startxref after the last whole revision names another section.

    Both engines read the last startxref whose offset they can read, one in a
    comment too. One glued to its offset is none to them, and one without an
    offset makes PDFium rebuild the file, unless it begins in the last 16
    bytes, where PDFium does not see it (measured, issue #44). Naming the
    section of the last whole revision again, that startxref is harmless;
    naming another, it sends the engines there: to an earlier revision,
    without a word. One that names no section is left to the engines, which
    rebuild the file.
    """
    if not revisions:
        return False
    end, section = revisions[-1]
    heading = _HEADER.search(data, 0, 1024 + len(b"%PDF-9.9"))
    header = heading.start() if heading else 0
    named = None
    for word in _STARTXREF_WORD.finditer(data, max(end, len(data) - _STARTXREF_WINDOW)):
        found = _STARTXREF.match(data, word.start())
        offset = None if found is None else _readable_offset(data, found.end())
        if offset is not None:
            named = header + offset
    if named is None or named >= len(data):
        return False
    theirs = _SECTION.match(data, named, named + _REACH)
    ours = _SECTION.match(data, section, section + _REACH)
    return theirs is not None and theirs.end() != ours.end()


def _readable_offset(data: bytes, at: int) -> int | None:
    """The offset QPDF reads after a startxref that ends at `at`, or None."""
    match = _OFFSET.match(data, at)
    if match is None:
        return None
    digits = match.group(1).lstrip(b"0") or b"0"
    return None if len(digits) > _OFFSET_DIGITS else int(digits)


def rewrite_recovered(data: bytes, password: str | None) -> RecoveredBytes:
    """The document QPDF rebuilds from `data`, with recovery on, written anew.

    A FORMAT failure when QPDF cannot rebuild it. /Size is set in the trailer
    before saving, so that QPDF writes the right one: when it could not read
    the damaged file's trailer, it would write one without /Size, and the
    result would be damaged again. Encryption is kept; pikepdf would drop it.
    Object streams are kept as they are and none are generated (issue #13,
    decision 3; without /Size set, QPDF could not open what it generated even
    with recovery on); nothing is linearized. An unencrypted result gets an
    /ID made from its content, not from the time, so that the same input
    gives the same bytes.

    A FORMAT failure also when the rebuilt document is not encrypted and yet
    holds an encryption dictionary: QPDF did not find the trailer that names
    it, and read the encrypted strings and streams as they are.

    QPDF reads the file up to the end of its last whole revision
    (_revision_ends), and what follows is left out. Where part of a revision
    follows, an object, a cross-reference table or a trailer, the last
    revision is unfinished; where QPDF, recovery off, cannot read the last
    revision after another, its startxref leads nowhere. Either is a FORMAT
    failure, since QPDF would read the revision before without a word, or,
    rebuilding the file from its objects, mix the two; it places objects in
    object streams only through a cross-reference stream, which an
    unfinished update lacks. A file without a whole revision is rebuilt from
    its objects.

    The warnings are those QPDF gave while it rebuilt the file, walked its
    pages and wrote it: it reads streams only when it writes them.
    """
    ends = _revision_ends(data)
    last = ends[-1] if ends else None
    if _part_follows(data, ends):
        raise _unfinished()
    if last is not None and _reads_as_written(data[:last], password):
        data = data[:last]
    elif len(ends) > 1:
        raise _unfinished()
    else:
        # No startxref leads QPDF to a whole revision: it has to rebuild the
        # file from its objects, the last of each number counting. A comment
        # longer than its search for startxref keeps it from reading one
        # first, which fails outside its recovery where the offset is too
        # large for it (measured), and from dropping an object that only white
        # space follows to the end ("EOF after endobj"). Comments mean nothing,
        # and QPDF does not write them.
        data += b"%" + b" " * (2 * _STARTXREF_SEARCH)
    sources: list[_Input] = []
    try:
        with _translated():
            pdf = _opened(
                data, password, sources, attempt_recovery=True, inherit_page_attributes=False
            )
            try:
                page_count = len(pdf.pages)
                if _lost_encryption(pdf):
                    raise EngineFailure(
                        FailureKind.FORMAT,
                        "the file is encrypted, and QPDF did not find the trailer that names "
                        "its encryption dictionary",
                    )
                # Any value: QPDF writes the number of objects it writes.
                pdf.trailer["/Size"] = 1
                written = io.BytesIO()
                encrypted = pdf.is_encrypted
                # pikepdf's save() adds a warning about widgets the form does
                # not reach, a form cut off before its /AcroForm; _save is what
                # it calls to write. pikepdf would also write the XMP metadata
                # anew to set the PDF version in it, an empty packet in place
                # of metadata it cannot parse.
                pdf._save(
                    written,
                    encryption=encrypted,
                    deterministic_id=not encrypted,
                    fix_metadata_version=False,
                )
                # Everything QPDF reported since the file was opened.
                reported = tuple(str(message) for message in pdf.get_warnings())
            finally:
                pdf.close()
    finally:
        for source in sources:
            source.close()
    return RecoveredBytes(data=written.getvalue(), page_count=page_count, warnings=reported)


def _unfinished() -> EngineFailure:
    return EngineFailure(
        FailureKind.FORMAT,
        "its last revision is unfinished, or its startxref leads nowhere: QPDF would read "
        "the revision before without a word or, rebuilding the file from its objects, mix "
        "the two",
    )


def _revision_ends(data: bytes) -> list[int]:
    """Where the offsets after the startxrefs of whole revisions end, in order (_revisions)."""
    return [end for end, _ in _revisions(data)]


def _revisions(data: bytes) -> list[tuple[int, int]]:
    """Each whole revision, in order: where its startxref's offset ends, where its section begins.

    A startxref ends a revision where QPDF reads through it, it lies outside
    the data of a stream (between the dictionary's stream and an endstream
    that follows), and its offset, counted from the header, names a
    cross-reference table or an object after the startxref of the revision
    before: a later revision's section lies nowhere else, and an offset cut
    short, or cut short and followed by other bytes, can name an earlier one.
    The first-page trailer of a linearized file ends no revision
    (_first_page_trailer).
    """
    heading = _HEADER.search(data, 0, 1024 + len(b"%PDF-9.9"))
    header = heading.start() if heading else 0
    first_page = _first_page_trailer(data, header)
    # The stream boundaries around each startxref, read once in file order.
    boundaries = _STREAM_BOUNDARY.finditer(data)
    before = None
    after = next(boundaries, None)
    revisions: list[tuple[int, int]] = []
    previous = None
    for found in _STARTXREF.finditer(data):
        while after is not None and after.start() < found.start():
            before, after = after, next(boundaries, None)
        if found.start() == first_page or (
            before is not None
            and before.group().startswith(b">>")
            and after is not None
            and after.group() == b"endstream"
        ):
            continue
        match = _OFFSET.match(data, found.end())
        if match is None:
            continue
        digits = match.group(1).lstrip(b"0") or b"0"
        if len(digits) > _OFFSET_DIGITS:
            continue
        section = header + int(digits)
        if section >= len(data) or not _SECTION.match(data, section, section + _REACH):
            continue
        if previous is not None and section <= previous:
            continue
        revisions.append((match.end(), section))
        previous = found.start()
    return revisions


def _first_page_trailer(data: bytes, header: int) -> int | None:
    """Where the startxref of a linearized file's first-page trailer begins.

    None where the file is not linearized or has no such trailer. The
    linearization dictionary is the first object, within the first 1024
    bytes (ISO 32000-1, F.2), and the first-page cross-reference section
    follows it (F.3): its startxref is the first, with at most the object of
    a cross-reference stream between. The last startxref of the revision
    names the first-page section, which lies before this one; some files
    have no first-page trailer.
    """
    first = _OBJECT_HEADER.search(data, header, header + 1024)
    if first is None:
        return None
    closing = data.find(b"endobj", first.end(), header + 1024)
    if closing < 0 or b"/Linearized" not in data[first.end() : closing]:
        return None
    found = _STARTXREF.search(data, closing)
    if found is None:
        return None
    between = _OBJECT_HEADER.finditer(data, closing, found.start())
    if next(between, None) is not None and next(between, None) is not None:
        return None
    return found.start()


def _reads_as_written(data: bytes, password: str | None) -> bool:
    """Whether QPDF, recovery off, reads the cross-reference sections of `data`.

    QPDF reads them when it opens a file: from the last startxref through
    each /Prev.
    """
    sources: list[_Input] = []
    try:
        with _translated():
            _opened(
                data, password, sources, attempt_recovery=False, inherit_page_attributes=False
            ).close()
    except EngineFailure as failure:
        if failure.kind is not FailureKind.FORMAT:
            raise
        return False
    finally:
        for source in sources:
            source.close()
    return True


def _lost_encryption(pdf: pikepdf.Pdf) -> bool:
    """Whether a document QPDF reads as unencrypted holds an encryption dictionary.

    One that the catalog does not reach: only a trailer names an encryption
    dictionary, and one the document uses, such as the seed values of a
    signature field, is none.
    """
    if pdf.is_encrypted:
        return False
    found = {obj.objgen for obj in pdf.objects if _encryption_dictionary(obj)}
    return bool(found - _reachable(pdf.Root))


def _reachable(root: pikepdf.Object) -> set[tuple[int, int]]:
    """The indirect objects reached from `root` through dictionaries and arrays."""
    seen: set[tuple[int, int]] = set()
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, pikepdf.Dictionary | pikepdf.Array | pikepdf.Stream):
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        stack.extend(node if isinstance(node, pikepdf.Array) else node.values())
    return seen


def _encryption_dictionary(obj: object) -> bool:
    """A security handler's name in /Filter, and what only encryption holds.

    /O and /U of the standard handler, /Recipients of the public-key one,
    crypt filters (/CF, /StmF, /StrF) from version 4 on, or else /V for the
    algorithm. A signature dictionary also names a handler in /Filter and may
    have /V; it has /ByteRange and /Contents, which encryption never has.
    """
    if not isinstance(obj, pikepdf.Dictionary) or not isinstance(obj.get("/Filter"), pikepdf.Name):
        return False
    if "/ByteRange" in obj or "/Contents" in obj:
        return False
    return ("/O" in obj and "/U" in obj) or any(
        key in obj for key in ("/Recipients", "/CF", "/StmF", "/StrF", "/V")
    )


def _page_objects_as_written(pdf: pikepdf.Pdf) -> tuple[tuple[int, int] | None, ...] | None:
    """The page objects in the order the file's page tree lists them.

    Read without QPDF's walk. A node with a /Kids array is a node of the tree,
    any other dictionary a page. A kid that is not a dictionary is None; a
    page written directly into /Kids has the objgen (0, 0), which QPDF never
    lists, since it makes such a page an object of its own. None for the whole
    tree when an object is reached twice, by a loop or by a page listed twice:
    QPDF refuses a loop in the tree it walks, but it may walk another tree
    than the one the catalog names.
    """
    pages: list[tuple[int, int] | None] = []
    seen: set[tuple[int, int]] = set()
    stack: list[object] = [pdf.Root.get("/Pages")]
    while stack:
        node = stack.pop()
        if not isinstance(node, pikepdf.Dictionary):
            pages.append(None)
            continue
        if node.is_indirect:
            if node.objgen in seen:
                return None
            seen.add(node.objgen)
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend(reversed(list(kids)))
        else:
            pages.append(node.objgen)
    return tuple(pages)


def _objects_as_written(
    pdf: pikepdf.Pdf, table: Mapping[tuple[int, int], pikepdf.XrefEntry]
) -> bool:
    """Whether each compressed object is named once in its object stream, at its index.

    `table` is the view's cross-reference, as pikepdf gives it.

    The cross-reference gives a compressed object as an object stream and an
    index. QPDF finds the object by the number in the stream's header and
    keeps the last of two with one number; PDFium takes what sits at the
    index. Both read the same only when the header names the object at that
    index and names no number twice.
    """
    headers: dict[int, list[int]] = {}
    for (number, _), entry in table.items():
        if entry.type != 2:
            continue
        header = headers.get(entry.obj_stream_number)
        if header is None:
            header = _object_stream_header(pdf, entry.obj_stream_number)
            if header is None or 0 in header or len(set(header)) != len(header):
                return False
            headers[entry.obj_stream_number] = header
        index = entry.obj_stream_index
        if not 0 <= index < len(header) or header[index] != number:
            return False
    return True


def _object_stream_header(pdf: pikepdf.Pdf, number: int) -> list[int] | None:
    """The object numbers the header of object stream `number` names, in order.

    None when the object `(number, 0)` is not a stream, its /N or /First is
    not an integer from 0, reading its data raises, or the bytes before
    /First are not exactly 2 * N tokens of ASCII digits separated by PDF
    white space. The even tokens are the numbers.
    """
    try:
        stream = pdf.get_object(number, 0)
        if not isinstance(stream, pikepdf.Stream):
            return None
        count = stream.get("/N")
        first = stream.get("/First")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or isinstance(first, bool)
            or not isinstance(first, int)
            or first < 0
        ):
            return None
        head = stream.read_bytes()[:first]
    except (pikepdf.PdfError, ValueError):
        return None
    tokens = re.split(rb"[\x00\t\n\x0c\r ]+", head.strip(b"\x00\t\n\x0c\r "))
    if len(tokens) != 2 * count:
        return None
    numbers = []
    for token in tokens:
        if not token.isdigit():
            return None
        numbers.append(int(token))
    return numbers[::2]


# --- widgets and their fields, as written ---------------------------------------

# The field keys a FieldEntry holds, as PDF names.
_FIELD_KEYS = ("/T", "/TU", "/FT", "/Ff", "/V", "/DV", "/Opt")
# The keys a WidgetEntry reads from the widget itself, as PDF names.
_WIDGET_KEYS = ("/Rect", "/F", "/AS")
# The keys it takes from /MK and /BS, as pairs of the PDF name and the bytes key.
_MK_KEYS = (("/BC", b"MK/BC"), ("/BG", b"MK/BG"))
_BS_KEYS = (("/W", b"BS/W"), ("/S", b"BS/S"))


class _Kept:
    """Plain data kept per document so that a shared object converts once.

    A value, a parent or a /Kids array named by thousands of dictionaries is
    converted once per document, not once per dictionary that names it. The
    stores are keyed by object number and generation, the states and the
    entries of /MK and /BS one store per role, so that one object can play two
    (issue #36, decision 7). Nothing is dropped before close().
    """

    def __init__(self) -> None:
        self.written: dict[tuple[int, int, int], Written] = {}
        self.fields: dict[tuple[int, int], FieldEntry] = {}
        self.kids: dict[tuple[int, int], Kids] = {}
        self.direct_kids: dict[tuple[int, int], Kids] = {}
        self.n_states: dict[tuple[int, int], tuple[bytes | None, ...]] = {}
        self.ap_n_states: dict[tuple[int, int], tuple[bytes | None, ...]] = {}
        self.mk: dict[tuple[int, int], dict[bytes, Written]] = {}
        self.bs: dict[tuple[int, int], dict[bytes, Written]] = {}
        self.widgets: dict[tuple[int, int], WidgetEntry] = {}
        self.tree: FieldTree | None = None
        self.tree_walked = False


def _written(value: object, depth: int, kept: _Kept) -> Written:
    """A value as the file writes it; an indirect one is converted once per depth."""
    if isinstance(value, pikepdf.Object) and value.is_indirect:
        key = (*value.objgen, depth)
        if key in kept.written:
            return kept.written[key]
        written = _converted(value, depth, kept)
        kept.written[key] = written
        return written
    return _converted(value, depth, kept)


def _converted(value: object, depth: int, kept: _Kept) -> Written:
    if value is None:
        return Written("null", None)
    if isinstance(value, bool):
        return Written("boolean", value)
    if isinstance(value, int):
        return Written("integer", value)
    if isinstance(value, Decimal):
        number = float(value)
        return Written("real", number) if math.isfinite(number) else Written("other", None)
    if isinstance(value, pikepdf.String):
        return Written("string", bytes(value))
    if isinstance(value, pikepdf.Name):
        data = _name_bytes(value)
        return Written("name", data) if data is not None else Written("invalid name", None)
    if isinstance(value, pikepdf.Array):
        if depth >= 2:
            return Written("other", None)
        return Written("array", tuple(_written(item, depth + 1, kept) for item in value))
    if isinstance(value, pikepdf.Stream):
        return Written("stream", None)
    if isinstance(value, pikepdf.Dictionary):
        return Written("dictionary", None)
    return Written("other", None)


# A name without bytes (_name_bytes), in the reasons given for it.
_STRAY_NAME = "a name with a # not followed by two hexadecimal digits"


def _content_name(name: pikepdf.Name) -> pikepdf.Name:
    """A name an operator of a content stream takes, checked to have bytes.

    A name without bytes (_name_bytes: QPDF holds a NUL in place of the #,
    PDFium reads it otherwise) is not taken: _CannotCount, since what the
    engines make of the stream may then differ, as of an object QPDF mis-reads
    (issue #62's rule). QPDF's warning about it names no object and no offset
    (measured), so that rule does not see it by itself.
    """
    if _name_bytes(name) is None:
        raise _CannotCount(f"{_STRAY_NAME} in a content stream")
    return name


def _name_bytes(name: pikepdf.Name) -> bytes | None:
    """The bytes of a name, without its slash, #xx resolved.

    None where the name writes a # not followed by two hexadecimal digits,
    which ISO 32000-2 (7.3.5) does not allow: QPDF holds such a # as a NUL,
    resolves what follows it, and warns; PDFium reads the two characters
    after it as hexadecimal digits, one that is none as 0, and keeps a #
    among the last two characters (measured), so the name has no bytes. A
    name holds no NUL otherwise: QPDF refuses #00. unparse() writes the NUL
    back as a #, which can then read as an escape, as in #4#31.
    """
    data = bytes(name)
    return None if b"\x00" in data else data[1:]


def _key_bytes(key: str) -> bytes | None:
    """The bytes of a key of /AP /N, without its slash.

    None where the key writes a # not followed by two hexadecimal digits
    (_name_bytes), which pikepdf gives as a NUL (measured): a name holds no
    NUL otherwise, since QPDF refuses one.
    """
    if "\x00" in key:
        return None
    return key.encode("utf-8", "surrogateescape")[1:]


def _field_entry(dictionary: pikepdf.Dictionary, kept: _Kept) -> FieldEntry:
    """A field dictionary's own entries; an indirect one is converted once."""
    if dictionary.is_indirect and dictionary.objgen in kept.fields:
        return kept.fields[dictionary.objgen]
    entries: dict[bytes, Written] = {}
    for key in _FIELD_KEYS:
        value = dictionary.get(key)
        if value is not None:
            entries[key.encode("ascii")[1:]] = _written(value, 0, kept)
    objgen = dictionary.objgen if dictionary.is_indirect else None
    entry = FieldEntry(
        object_number=objgen[0] if objgen else None,
        generation=objgen[1] if objgen else None,
        entries=MappingProxyType(entries),
        kids=isinstance(dictionary.get("/Kids"), pikepdf.Array),
        subtype_widget=dictionary.get("/Subtype") == pikepdf.Name("/Widget"),
    )
    if dictionary.is_indirect:
        kept.fields[dictionary.objgen] = entry
    return entry


def _kids(array: pikepdf.Array) -> Kids:
    """The entries of one /Kids array, with the positions of its widgets."""
    entries: list[KidEntry] = []
    positions: dict[tuple[int, int], list[int]] = {}
    widgets = 0
    all_widgets = True
    for item in array:
        dictionary = isinstance(item, pikepdf.Dictionary)
        objgen = item.objgen if dictionary and item.is_indirect else None
        widget = dictionary and item.get("/Subtype") == pikepdf.Name("/Widget")
        entries.append(
            KidEntry(objgen[0] if objgen else None, objgen[1] if objgen else None, widget)
        )
        if widget:
            if objgen is not None:
                positions.setdefault(objgen, []).append(widgets)
            widgets += 1
        else:
            all_widgets = False
    return Kids(
        tuple(entries),
        MappingProxyType({key: tuple(value) for key, value in positions.items()}),
        all_widgets,
    )


def _parent_kids(dictionary: pikepdf.Dictionary, kept: _Kept) -> Kids | None:
    """The /Kids of the widget's own /Parent; the array is read once per document."""
    parent = dictionary.get("/Parent")
    if not (isinstance(parent, pikepdf.Dictionary) and parent.is_indirect):
        return None
    kids_value = parent.get("/Kids")
    if not isinstance(kids_value, pikepdf.Array):
        return None
    if kids_value.is_indirect:
        store, key = kept.kids, kids_value.objgen
    else:
        # A direct array is kept by the indirect parent that holds it, so that
        # parents sharing one array share one Kids.
        store, key = kept.direct_kids, parent.objgen
    if key not in store:
        store[key] = _kids(kids_value)
    return store[key]


def _taken(source: pikepdf.Dictionary, names, kept: _Kept) -> dict[bytes, Written]:
    taken = {}
    for pdf_key, bytes_key in names:
        value = source.get(pdf_key)
        if value is not None:
            taken[bytes_key] = _written(value, 0, kept)
    return taken


def _sub_entries(
    source: pikepdf.Dictionary, names, store: dict, kept: _Kept
) -> dict[bytes, Written]:
    """Entries taken from /MK or /BS; an indirect one is converted once per role."""
    if source.is_indirect:
        if source.objgen in store:
            return dict(store[source.objgen])
        taken = _taken(source, names, kept)
        store[source.objgen] = taken
        return dict(taken)
    return _taken(source, names, kept)


def _states(n: pikepdf.Dictionary, ap: pikepdf.Dictionary, kept: _Kept) -> tuple[bytes | None, ...]:
    """The keys of an /N dictionary in QPDF's order; converted once per role."""
    if n.is_indirect:
        store, key = kept.n_states, n.objgen
    elif ap.is_indirect:
        store, key = kept.ap_n_states, ap.objgen
    else:
        return tuple(_key_bytes(key) for key in n)
    if key not in store:
        store[key] = tuple(_key_bytes(key) for key in n)
    return store[key]


def _widget_entry(
    dictionary: pikepdf.Dictionary, annotation: AnnotationEntry, kept: _Kept
) -> WidgetEntry:
    """One widget of a page, its own entries and the chain of dictionaries above it."""
    entries: dict[bytes, Written] = {}
    for key in _WIDGET_KEYS:
        value = dictionary.get(key)
        if value is not None:
            entries[key.encode("ascii")[1:]] = _written(value, 0, kept)
    mk = dictionary.get("/MK")
    mk_invalid = mk is not None and not isinstance(mk, pikepdf.Dictionary)
    if not mk_invalid and mk is not None:
        entries.update(_sub_entries(mk, _MK_KEYS, kept.mk, kept))
    bs = dictionary.get("/BS")
    bs_invalid = bs is not None and not isinstance(bs, pikepdf.Dictionary)
    if not bs_invalid and bs is not None:
        entries.update(_sub_entries(bs, _BS_KEYS, kept.bs, kept))
    ap = dictionary.get("/AP")
    states: tuple[bytes | None, ...] | None = None
    states_invalid = False
    if ap is not None:
        if not isinstance(ap, pikepdf.Dictionary):
            states_invalid = True
        else:
            n = ap.get("/N")
            if isinstance(n, pikepdf.Dictionary):
                states = _states(n, ap, kept)
            elif n is not None and not isinstance(n, pikepdf.Stream):
                states_invalid = True
    chain: list[FieldEntry] = [_field_entry(dictionary, kept)]
    seen = {dictionary.objgen} if dictionary.is_indirect else set()
    current = dictionary
    chain_loops = chain_cut = parent_invalid = False
    while True:
        parent = current.get("/Parent")
        if parent is None:
            break
        if not isinstance(parent, pikepdf.Dictionary):
            parent_invalid = True
            break
        if len(chain) >= 33:
            chain_cut = True
            break
        if parent.is_indirect and parent.objgen in seen:
            chain_loops = True
            break
        chain.append(_field_entry(parent, kept))
        if parent.is_indirect:
            seen.add(parent.objgen)
        current = parent
    return WidgetEntry(
        annotation=annotation,
        entries=MappingProxyType(entries),
        states=states,
        states_invalid=states_invalid,
        mk_invalid=mk_invalid,
        bs_invalid=bs_invalid,
        chain=tuple(chain),
        chain_loops=chain_loops,
        chain_cut=chain_cut,
        parent_invalid=parent_invalid,
        parent_kids=_parent_kids(dictionary, kept),
    )


class _TreeWalk:
    """The state of one walk of the field tree, and its identity rules."""

    def __init__(self, kept: _Kept) -> None:
        self.kept = kept
        self.entries: list[FieldEntry] = []
        self.dictionaries: list[object] = []
        self.kids: list[tuple[int | None, ...] | None] = []
        self.parents: list[int | None] = []
        self.matches: list[bool] = []
        self.reached: list[bool] = []
        # A node's identity: an objgen for an indirect one, otherwise what
        # holds it and its position there.
        self.index_of: dict[object, int] = {}
        self.arrays_done: dict[tuple[int, int], tuple[int | None, ...]] = {}
        self.arrays_active: set[tuple[int, int]] = set()
        self.array_nodes: dict[tuple[int, int], list[int]] = {}
        self.array_marked: set[tuple[int, int]] = set()
        # Nodes whose /Kids is an array still being walked: their kids are that
        # array's, filled in when its walk ends.
        self.array_waiting: dict[tuple[int, int], list[int]] = {}
        self.cut = False


def _tree_entry(
    entry: object, key: object, parent: int | None, level: int, walk: _TreeWalk
) -> int | None:
    """One entry of /Fields or of a /Kids array: its node's index, or None.

    Called once per entry, so that a test can count the steps of a walk.
    """
    if not isinstance(entry, pikepdf.Dictionary):
        return None
    if entry.is_indirect:
        key = entry.objgen
    if key in walk.index_of:
        index = walk.index_of[key]
        walk.reached[index] = True
        return index
    index = len(walk.entries)
    walk.index_of[key] = index
    walk.entries.append(_field_entry(entry, walk.kept))
    walk.dictionaries.append(entry)
    walk.kids.append(None)
    walk.parents.append(parent)
    walk.matches.append(_parent_matches(entry, parent, walk))
    walk.reached.append(False)
    kids_value = entry.get("/Kids")
    if isinstance(kids_value, pikepdf.Array):
        if level >= 32:
            walk.cut = True
        else:
            walk.kids[index] = _walk_kids(kids_value, key, index, level, walk)
    else:
        walk.kids[index] = ()
    return index


def _parent_matches(entry: pikepdf.Dictionary, parent: int | None, walk: _TreeWalk) -> bool:
    """Whether the node's /Parent is the dictionary of the node it was reached through."""
    named = entry.get("/Parent")
    if parent is None:
        return named is None
    if not (isinstance(named, pikepdf.Dictionary) and named.is_indirect):
        return False
    above = walk.dictionaries[parent]
    return above.is_indirect and above.objgen == named.objgen


def _walk_kids(
    array: pikepdf.Array, node_key: object, node_index: int, level: int, walk: _TreeWalk
) -> tuple[int | None, ...]:
    """The node indices of a /Kids array's entries; an indirect one is walked once."""
    if not array.is_indirect:
        return _kids_entries(array, (node_key, "direct kids"), node_index, level, walk)
    key = array.objgen
    if key in walk.arrays_done:
        if key not in walk.array_marked:
            walk.array_marked.add(key)
            for held in walk.array_nodes[key]:
                walk.reached[held] = True
        return walk.arrays_done[key]
    if key in walk.arrays_active:
        # A loop through the array: it is not walked again. The node gets its
        # kids when the walk of the array ends, and they count as met again.
        walk.array_waiting.setdefault(key, []).append(node_index)
        return ()
    walk.arrays_active.add(key)
    try:
        kids = _kids_entries(array, key, node_index, level, walk)
    finally:
        walk.arrays_active.discard(key)
    walk.arrays_done[key] = kids
    walk.array_nodes[key] = [index for index in kids if index is not None]
    waiting = walk.array_waiting.pop(key, [])
    for node in waiting:
        walk.kids[node] = kids
    if waiting:
        walk.array_marked.add(key)
        for held in walk.array_nodes[key]:
            walk.reached[held] = True
    return kids


def _kids_entries(
    array: pikepdf.Array, holder_key: object, parent_index: int, level: int, walk: _TreeWalk
) -> tuple[int | None, ...]:
    kids = []
    for position, item in enumerate(array):
        kids.append(_tree_entry(item, (holder_key, position), parent_index, level + 1, walk))
    return tuple(kids)


def _field_tree(pdf: pikepdf.Pdf, kept: _Kept) -> FieldTree | None:
    """The field tree from /AcroForm /Fields, each node once, depth first."""
    acroform = pdf.Root.get("/AcroForm")
    if acroform is None:
        return None
    if not isinstance(acroform, pikepdf.Dictionary):
        return FieldTree((), (), True, False)
    fields = acroform.get("/Fields")
    if not isinstance(fields, pikepdf.Array):
        return FieldTree((), (), True, False)
    walk = _TreeWalk(kept)
    holder = fields.objgen if fields.is_indirect else "fields"
    roots = []
    invalid = False
    for position, item in enumerate(fields):
        if not isinstance(item, pikepdf.Dictionary):
            invalid = True
        roots.append(_tree_entry(item, (holder, position), None, 0, walk))
    nodes = tuple(
        FieldNode(entry, kids, parent, matches, reached)
        for entry, kids, parent, matches, reached in zip(
            walk.entries, walk.kids, walk.parents, walk.matches, walk.reached, strict=True
        )
    )
    return FieldTree(nodes, tuple(roots), invalid, walk.cut)


def _inherited(page: pikepdf.Dictionary, key: str) -> object:
    """The value of an inheritable key on the page or its nearest ancestor.

    The chain of /Parent is followed as far as it goes, and never twice
    through the same object. The first value found counts, also when it is
    not usable, as in PDFium. pikepdf does not show a key whose value is
    null, so an explicit null counts as absent, as ISO 32000 says; PDFium
    stops at it instead, so #11's cross-check sees such a page as a
    disagreement.
    """
    node: object = page
    seen: set[tuple[int, int]] = set()
    while isinstance(node, pikepdf.Dictionary):
        value = node.get(key)
        if value is not None:
            return value
        if node.is_indirect:
            if node.objgen in seen:
                return None
            seen.add(node.objgen)
        node = node.get("/Parent")
    return None


# PDFium parses a form XObject drawn at a nesting depth of 1 to 40 below the
# content that draws it, and gives one drawn deeper as a form without objects.
# An appearance starts its own 40 levels (measured, issue G1). A tiling
# pattern, a soft mask group and a Type 3 glyph do not continue the levels of
# where they are used (measured); they are counted from depth 0 as well, which
# can only count more.
_FORM_DEPTH = 40
# The operators after which PDFium parses another content stream.
_PARSING_OPERATORS = "Do gs scn SCN Tf"


# A warning of QPDF's parser about one object: "(object 12 0, offset 345)".
# Any such warning leaves the page uncountable: what PDFium makes of the same
# bytes may be more (measured, 2026-10-01: a name with a #00, an integer
# beyond 64 bits, a wrong or missing /Length, a broken hex string). The
# page-tree repairs of the walked view name no offset, the recoveries of a
# content stream name no object, and a stream cut short gives no such warning,
# so those stay countable; the corpus gives none of them.
_MISREAD = re.compile(r"\(object \d+ \d+, offset \d+\)")


def _countable(message: str) -> bool:
    return _MISREAD.search(message) is None


class _CannotCount(Exception):
    """Raised where the two engines read an object differently, or a content
    stream names an object without bytes (_content_name), so counting ends."""


def _dict_of(value: object):
    """The value read as a dictionary: a dictionary, or a stream, as PDFium reads one.

    PDFium reads a stream's dictionary wherever a dictionary is expected
    (measured, 2026-10-01: a form's /Resources, an /XObject group); None for
    anything else.
    """
    if isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
        return value
    return None


def _subtype_is(value: object, name: bytes) -> bool:
    """Whether /Subtype names `name`, as PDFium reads it: a name or a string.

    A name's bytes carry their slash; a string's do not. PDFium was measured
    with `(Form)` only; the string with the slash is accepted too, which can
    only count more, never less.
    """
    if isinstance(value, pikepdf.Name):
        return bytes(value) == b"/" + name
    return isinstance(value, pikepdf.String) and bytes(value) in (name, b"/" + name)


def _read_otherwise(value: object, compressed: frozenset[int]) -> None:
    """Refuse to count a compressed dictionary that PDFium reads as a stream.

    An object stream entry holds what parses as an object; where its body is a
    dictionary followed by the keyword `stream`, QPDF reads the dictionary and
    PDFium the stream (measured, 2026-10-01). Such a dictionary sits where a
    stream is required — a form, a pattern, a soft mask group, a glyph
    procedure, an appearance — and what PDFium would parse from its bytes
    cannot be counted.
    """
    if isinstance(value, pikepdf.Dictionary) and value.is_indirect:
        if value.objgen[0] in compressed:
            raise _CannotCount("an object stream holds what PDFium reads as a stream")


class _Context:
    """Where a stream's names are looked up, as PDFium looks them up.

    `resources` are the stream's own /Resources, or those it takes when it has
    none; `fallback` is where PDFium looks when `resources` lack the kind of
    name asked for (/XObject, /Font, ...): the page's, or none. Each carries a
    key that names it, for the counts kept per context.
    """

    __slots__ = ("resources", "key", "fallback", "fallback_key")

    def __init__(self, resources, key, fallback, fallback_key) -> None:
        self.resources = resources
        self.key = key
        self.fallback = fallback
        self.fallback_key = fallback_key

    def drawing(self, stream: pikepdf.Stream) -> _Context:
        """The context of a form drawn here: its own /Resources, else these."""
        own = _dict_of(stream.get("/Resources"))
        if own is not None:
            return _Context(own, ("own", stream.objgen), self.fallback, self.fallback_key)
        return self

    def lookup(self, kind: str, name: pikepdf.Name) -> tuple[object, tuple | None]:
        """The object `name` stands for, and the key of the resources it is in."""
        resources = _dict_of(self.resources)
        group = resources.get(kind) if resources is not None else None
        dictionary = _dict_of(group)
        if dictionary is not None:
            return dictionary.get(name), self.key
        if self.fallback is not None and self.fallback_key != self.key:
            fallback = _dict_of(self.fallback)
            group = fallback.get(kind) if fallback is not None else None
            dictionary = _dict_of(group)
            if dictionary is not None:
                return dictionary.get(name), self.fallback_key
        return None, None


class _ParsedStreams:
    """What parsing a page makes PDFium parse, counted without parsing it.

    Measured with PDFium 153.0.7999.0 (issue G1): a form is parsed each time it
    is drawn; a tiling pattern and a soft mask group once, when first used,
    however often they are used; a Type 3 font's glyph procedures once, and
    only those shown (all of them are counted here: an upper bound); an
    annotation's normal appearance once per annotation, also when two
    annotations share it (every stream of /N is counted here, whichever one
    PDFium selects: an upper bound). Names are looked up as PDFium looks them
    up (_Context), and a stream is counted once per depth and per context.
    """

    def __init__(self, compressed: frozenset[int]) -> None:
        # Where the parts of a page's /Contents are joined, apart from the
        # document, which a count must not change.
        self._scratch = pikepdf.new()
        self._compressed = compressed
        self._instructions: dict[tuple[int, int], list[tuple[str, pikepdf.Name]]] = {}
        self._counted: dict[tuple, int] = {}
        # Patterns, soft mask groups and Type 3 fonts already counted, and
        # where each font was counted.
        self._once: set = set()

    def page(self, page: pikepdf.Dictionary) -> int | None:
        """The count; None when QPDF cannot read one of the streams.

        A stream QPDF decodes only in part, with a warning, is counted as far
        as it decodes: PDFium draws the same part (measured, issue G1).
        """
        resources = _dict_of(_inherited(page, "/Resources"))
        top = _Context(resources, ("page",), resources, ("page",))
        try:
            total = self._content(self._page_content(page), None, top, 0)
            annots = page.get("/Annots")
            for annot in annots if isinstance(annots, pikepdf.Array) else ():
                for appearance in _normal_appearances(annot, self._compressed):
                    total += self._stream(appearance, top.drawing(appearance), 0)
        except MemoryError:
            raise
        except Exception:
            # pikepdf raises PdfError for a filter it does not know, TypeError
            # for an operator inside an array or a dictionary (measured, issue
            # G1), _CannotCount where the engines read an object differently,
            # and any exception where a warning filter makes one of its
            # warnings an error; none of them leaves a count.
            return None
        return total

    def _page_content(self, page: pikepdf.Dictionary) -> pikepdf.Stream:
        """The page's /Contents as one stream, parts joined as PDFium joins them."""
        contents = page.get("/Contents")
        if isinstance(contents, pikepdf.Stream):
            return contents
        parts = (
            [part for part in contents if isinstance(part, pikepdf.Stream)]
            if isinstance(contents, pikepdf.Array)
            else []
        )
        return pikepdf.Stream(self._scratch, b"\n".join(part.read_bytes() for part in parts))

    def _content(self, owner, key, context: _Context, depth: int) -> int:
        """What parsing `owner` at `depth` makes PDFium parse besides it."""
        total = 0
        for operator, name in self._read(owner, key):
            if operator == "Do":
                drawn, _ = context.lookup("/XObject", name)
                if isinstance(drawn, pikepdf.Stream) and _subtype_is(
                    drawn.get("/Subtype"), b"Form"
                ):
                    total += self._stream(drawn, context.drawing(drawn), depth + 1)
                elif isinstance(drawn, pikepdf.Dictionary) and _subtype_is(
                    drawn.get("/Subtype"), b"Form"
                ):
                    _read_otherwise(drawn, self._compressed)
            elif operator == "gs":
                state, _ = context.lookup("/ExtGState", name)
                state = _dict_of(state)
                mask = state.get("/SMask") if state is not None else None
                mask = _dict_of(mask)
                group = mask.get("/G") if mask is not None else None
                if isinstance(group, pikepdf.Stream) and self._first(("mask", group.objgen)):
                    # Its own /Resources, else the page's (measured, issue G1).
                    page = _Context(
                        context.fallback,
                        context.fallback_key,
                        context.fallback,
                        context.fallback_key,
                    )
                    total += self._stream(group, page.drawing(group), 0)
                else:
                    _read_otherwise(group, self._compressed)
            elif operator in ("scn", "SCN"):
                pattern, _ = context.lookup("/Pattern", name)
                if (
                    isinstance(pattern, pikepdf.Stream)
                    and pattern.get("/PatternType") == 1
                    and self._first(("pattern", pattern.objgen))
                ):
                    # Its own /Resources or none, and no fallback (measured).
                    alone = _Context(None, ("none",), None, None)
                    total += self._stream(pattern, alone.drawing(pattern), 0)
                elif isinstance(pattern, pikepdf.Dictionary) and pattern.get("/PatternType") == 1:
                    _read_otherwise(pattern, self._compressed)
            else:  # Tf
                font, holder = context.lookup("/Font", name)
                font = _dict_of(font)
                if font is not None and _subtype_is(font.get("/Subtype"), b"Type3"):
                    total += self._type3(
                        font, (holder, name) if not font.is_indirect else font.objgen, context
                    )
        return total

    def _first(self, key) -> bool:
        """Whether `key` is met for the first time."""
        if key in self._once:
            return False
        self._once.add(key)
        return True

    def _stream(self, stream: pikepdf.Stream, context: _Context, depth: int) -> int:
        """1 for the stream, and what parsing it at `depth` in `context` makes PDFium parse."""
        if depth > _FORM_DEPTH:
            return 1
        key = (stream.objgen, depth, context.key, context.fallback_key)
        counted = self._counted.get(key)
        if counted is None:
            # A stream that draws itself does so one level deeper, under
            # another key, so the recursion ends at _FORM_DEPTH.
            counted = 1 + self._content(stream, stream.objgen, context, depth)
            self._counted[key] = counted
        return counted

    def _type3(self, font: pikepdf.Dictionary, font_key, context: _Context) -> int:
        """A Type 3 font's glyph procedures, each counted once.

        With its own /Resources a glyph uses those, and the font is counted
        once. Without them a glyph uses the resources of where the font was
        selected before it is shown (measured, issue G1): the font is counted
        once for each place it is selected, an upper bound.
        """
        own = _dict_of(font.get("/Resources"))
        if own is not None:
            where = ("font", font_key)
            glyphs = _Context(own, where, own, where)
        else:
            where = ("font", font_key, context.key)
            glyphs = _Context(context.resources, context.key, context.resources, context.key)
        if not self._first(where):
            return 0
        procs = _dict_of(font.get("/CharProcs"))
        total = 0
        for proc in procs.values() if procs is not None else ():
            if isinstance(proc, pikepdf.Stream):
                total += self._stream(proc, glyphs.drawing(proc), 0)
            else:
                _read_otherwise(proc, self._compressed)
        return total

    def _read(self, owner, key) -> list[tuple[str, pikepdf.Name]]:
        """The instructions of `owner` that make PDFium parse more, each stream read once."""
        if key is not None and key in self._instructions:
            return self._instructions[key]
        found: list[tuple[str, pikepdf.Name]] = []
        for operands, operator in pikepdf.parse_content_stream(owner, _PARSING_OPERATORS):
            op = str(operator)
            # PDFium takes operands counted from the operator: the name is the
            # last operand of Do, gs and scn, the one before the size of Tf.
            at = 2 if op == "Tf" else 1
            if len(operands) < at:
                continue
            name = operands[-at]
            if isinstance(name, pikepdf.Name):
                found.append((op, _content_name(name)))
        if key is not None:
            self._instructions[key] = found
        return found


def _normal_appearances(annot: object, compressed: frozenset[int]) -> list[pikepdf.Stream]:
    """Every stream PDFium may take as the annotation's normal appearance.

    /AP /N, or each stream of it when it is a dictionary: PDFium selects by
    /AS, else by the field's /V, else /Off (measured, issue G1), and counting
    them all spares copying that choice. /AP written as a stream is read by
    its dictionary, as PDFium reads it (measured, 2026-10-01). A compressed
    dictionary among the states is what PDFium reads as a stream (measured,
    2026-10-01) and makes the page uncountable.
    """
    if not isinstance(annot, pikepdf.Dictionary):
        return []
    ap = _dict_of(annot.get("/AP"))
    normal = ap.get("/N") if ap is not None else None
    if isinstance(normal, pikepdf.Stream):
        return [normal]
    if isinstance(normal, pikepdf.Dictionary):
        _read_otherwise(normal, compressed)
        # values(), not get() with each key: a key that is no UTF-8 comes with
        # surrogate escapes, and get() refuses it (measured, issue G1).
        states = [state for state in normal.values() if isinstance(state, pikepdf.Stream)]
        for state in normal.values():
            _read_otherwise(state, compressed)
        return states
    return []


def _annotation_entry(position: int, item: object) -> AnnotationEntry:
    """The entry at `position` of a page's /Annots, as the file writes it."""
    dictionary = isinstance(item, pikepdf.Dictionary)
    objgen = item.objgen if dictionary and item.is_indirect else None
    subtype = item.get("/Subtype") if dictionary else None
    return AnnotationEntry(
        position,
        objgen[0] if objgen else None,
        objgen[1] if objgen else None,
        dictionary,
        _name_entry(subtype, "/Subtype"),
    )


def _selected_appearance(annot: pikepdf.Dictionary) -> pikepdf.Stream | str | None:
    """The normal appearance PDFium draws for an annotation (measured, issue G2).

    /AP /N when it is a stream. When it is a dictionary of states: the state
    /AS names, and none when /N has no stream by that name; where /AS says
    nothing, the state the annotation's /V names, else its /Parent's /V, and
    /Off when that names no state of /N. None when PDFium draws none; a str
    where QPDF cannot tell which one PDFium draws, saying why.
    """
    ap = _dict_of(annot.get("/AP"))
    normal = ap.get("/N") if ap is not None else None
    if isinstance(normal, pikepdf.Stream):
        return normal
    if not isinstance(normal, pikepdf.Dictionary):
        return None
    # items(), not get() with a key: a key that is no UTF-8 comes with surrogate
    # escapes, and get() refuses it.
    states = {_key_bytes(key): value for key, value in normal.items()}
    if None in states:
        # PDFium reads such a key otherwise, and may select it (_name_bytes).
        return f"a key of /AP /N is {_STRAY_NAME}"
    wanted = _state_text(annot.get("/AS"), "/AS")
    if not wanted:
        wanted = _state_text(annot.get("/V"), "/V")
        if not wanted:
            parent = _dict_of(annot.get("/Parent"))
            if parent is not None:
                wanted = _state_text(parent.get("/V"), "the /V of its /Parent")
        if isinstance(wanted, bytes):
            # An empty name is no choice: PDFium falls back past it (measured,
            # issue G2), so a state named / is not selected by an empty /V.
            wanted = wanted if wanted and wanted in states else b"Off"
    if isinstance(wanted, str):
        return wanted
    chosen = states.get(wanted)
    return chosen if isinstance(chosen, pikepdf.Stream) else None


def _state_text(value: object, where: str) -> bytes | str:
    """What PDFium reads as the name of a state: b"" where it reads nothing.

    The bytes of a name or a string, true or false, the digits of an integer
    wrapped to PDFium's 32 bits — 2147483648 names the state -2147483648
    (measured, issue G2) — and 5.0 names the state 5, but a real number's own
    spelling QPDF cannot tell. A str where QPDF cannot tell at all: a name
    without bytes (_name_bytes), or a real number.
    """
    if isinstance(value, pikepdf.Name):
        data = _name_bytes(value)
        return data if data is not None else f"{where} is {_STRAY_NAME}"
    if isinstance(value, pikepdf.String):
        return bytes(value)
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return b"%d" % (((value + 2**31) % 2**32) - 2**31)
    if isinstance(value, Decimal):
        return f"{where} is a number PDFium writes out in its own way to name a state"
    return b""


def _appearance(stream: pikepdf.Stream, page: _Context, kept: _Kept) -> SelectedAppearance:
    try:
        data: bytes | None = stream.read_bytes()
    except pikepdf.PdfError:
        data = None
    bbox = stream.get("/BBox")
    matrix = stream.get("/Matrix")
    return SelectedAppearance(
        data=data,
        bbox=None if bbox is None else _written(bbox, 0, kept),
        matrix=None if matrix is None else _written(matrix, 0, kept),
        uses_pattern=_uses_pattern(lambda: stream, page.drawing(stream)),
    )


def _uses_pattern(owner, context: _Context) -> bool | None:
    """Whether the content `owner()` gives, or a form it draws, sets a pattern colour.

    None where QPDF cannot read one of them, or a name an operator of one
    names has no bytes (_content_name).
    """
    try:
        return _pattern_in(owner(), context, 0, {})
    except MemoryError:
        raise
    except Exception:
        # As in _ParsedStreams.page: PdfError for a filter pikepdf does not
        # know, TypeError for an operator inside an array or a dictionary, and
        # any exception a warning filter makes of pikepdf's warnings.
        return None


def _pattern_space(name: pikepdf.Name, context: _Context) -> bool:
    """Whether cs/CS with `name` selects a Pattern colour space (measured, G2).

    The name /Pattern, or a name whose /ColorSpace resource is /Pattern or an
    array that starts with /Pattern: PDFium makes up a colour for a fill in
    such a space even where no pattern is ever set (the review of the G2
    draft, 2026-10-01).
    """
    if bytes(name) == b"/Pattern":
        return True
    space, _ = context.lookup("/ColorSpace", name)
    if isinstance(space, pikepdf.Name):
        return bytes(space) == b"/Pattern"
    return (
        isinstance(space, pikepdf.Array)
        and len(space) > 0
        and isinstance(space[0], pikepdf.Name)
        and bytes(space[0]) == b"/Pattern"
    )


def _pattern_in(owner, context: _Context, depth: int, seen: dict[tuple, bool]) -> bool:
    """Whether `owner` sets a pattern colour, or a form it draws does, down to 40 levels.

    Names are looked up as PDFium looks them up (_Context); a form is read once
    for each level and context it is drawn in, as _ParsedStreams counts it.
    """
    for operands, operator in pikepdf.parse_content_stream(owner, "Do scn SCN cs CS"):
        name = operands[-1] if operands else None
        if not isinstance(name, pikepdf.Name):
            continue
        name = _content_name(name)
        op = str(operator)
        if op in ("scn", "SCN"):
            return True
        if op in ("cs", "CS") and _pattern_space(name, context):
            return True
        drawn, _ = context.lookup("/XObject", name)
        if (
            isinstance(drawn, pikepdf.Stream)
            and _subtype_is(drawn.get("/Subtype"), b"Form")
            and depth < _FORM_DEPTH
        ):
            inner = context.drawing(drawn)
            key = (drawn.objgen, depth + 1, inner.key, inner.fallback_key)
            if key not in seen:
                seen[key] = _pattern_in(drawn, inner, depth + 1, seen)
            if seen[key]:
                return True
    return False


def _number(value: object) -> float | None:
    """A PDF number as a finite float; None for anything else, a flag included.

    QPDF reads integers into 64 bits, so an int always fits a float; a real
    can still be too large and become infinite.
    """
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _box(value: object) -> Box | None:
    """Four numbers as written, corners not put in order; None otherwise."""
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        return None
    x0, y0, x1, y1 = (_number(item) for item in value)
    if x0 is None or y0 is None or x1 is None or y1 is None:
        return None
    return (x0, y0, x1, y1)


def _object_kind(obj: object) -> str:
    if isinstance(obj, pikepdf.Stream):
        kind = obj.stream_dict.get("/Type")
        if kind == pikepdf.Name.ObjStm:
            return "object stream"
        if kind == pikepdf.Name.XRef:
            return "cross-reference stream"
        return "stream"
    if isinstance(obj, pikepdf.Dictionary):
        return "dictionary"
    if isinstance(obj, pikepdf.Array):
        return "array"
    return "other"


# An indirect reference as QPDF writes one in JSON.
_REFERENCE = re.compile(r"\d+ \d+ R")


def _page_contents(page: pikepdf.Object) -> PageContents:
    """What the page writes in /Contents; pikepdf hands out a null as None."""
    value = page.get("/Contents")
    if value is None:
        return PageContents(form=ContentsForm.ABSENT, array_object=None, streams=(), others=0)
    if isinstance(value, pikepdf.Stream):
        return PageContents(
            form=ContentsForm.STREAM, array_object=None, streams=(_stream_length(value),), others=0
        )
    if isinstance(value, pikepdf.Array):
        streams = tuple(_stream_length(item) for item in value if isinstance(item, pikepdf.Stream))
        if value.is_indirect:
            form = ContentsForm.INDIRECT_ARRAY
            array_object = (int(value.objgen[0]), int(value.objgen[1]))
        else:
            form, array_object = ContentsForm.ARRAY, None
        others = len(value) - len(streams)
        return PageContents(form=form, array_object=array_object, streams=streams, others=others)
    return PageContents(form=ContentsForm.OTHER, array_object=None, streams=(), others=1)


def _stream_length(stream: pikepdf.Stream) -> ContentStreamLength:
    """The /Length the stream's dictionary writes. pikepdf resolves a
    reference before it hands out the value; QPDF's JSON of the dictionary
    still names it."""
    length = _integer(stream.stream_dict.get("/Length"))
    if length is None:
        # QPDF reads no such stream (ContentStreamLength, measured); should it
        # ever, no length is made up.
        raise EngineFailure(FailureKind.OTHER, "QPDF read a stream whose /Length is no integer")
    written = json.loads(stream.stream_dict.to_json()).get("/Length")
    return ContentStreamLength(
        object_number=int(stream.objgen[0]),
        generation=int(stream.objgen[1]),
        length=length,
        length_indirect=isinstance(written, str) and _REFERENCE.fullmatch(written) is not None,
    )


def _encryption_entries(pdf: pikepdf.Pdf) -> EncryptionEntries:
    """The encryption dictionary as written, and QPDF's security handler."""
    info = pdf.encryption
    found = pdf.trailer.get("/Encrypt")
    dictionary = found if isinstance(found, pikepdf.Dictionary) else pikepdf.Dictionary()
    length = dictionary.get("/Length")
    metadata = dictionary.get("/EncryptMetadata")
    return EncryptionEntries(
        filter=_name_entry(dictionary.get("/Filter"), "/Filter"),
        sub_filter=_name_entry(dictionary.get("/SubFilter"), "/SubFilter"),
        v=_integer(dictionary.get("/V")),
        r=_integer(dictionary.get("/R")),
        length=_integer(length),
        length_invalid=length is not None and _integer(length) is None,
        p=_integer(dictionary.get("/P")),
        encrypt_metadata=metadata if isinstance(metadata, bool) else None,
        encrypt_metadata_invalid=metadata is not None and not isinstance(metadata, bool),
        key_bits=int(info.bits),
        stream_method=info.stream_method.name,
        string_method=info.string_method.name,
        file_method=info.file_method.name,
        user_password_matched=bool(pdf.user_password_matched),
        owner_password_matched=bool(pdf.owner_password_matched),
    )


def _name_entry(value: object, key: str) -> bytes | str | None:
    """The bytes of a name; None for anything else; a str where the name has
    no bytes (_name_bytes)."""
    if not isinstance(value, pikepdf.Name):
        return None
    data = _name_bytes(value)
    return data if data is not None else f"{key} is {_STRAY_NAME}"


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _user_unit(value: object) -> tuple[float | None, bool]:
    """The value and whether it is invalid: absent is not invalid."""
    if value is None:
        return None, False
    number = _number(value)
    if number is None or number <= 0:
        return None, True
    return number, False
