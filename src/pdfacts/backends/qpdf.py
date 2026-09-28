"""QPDF backend: object access, rewrite and decryption.

The only module allowed to import pikepdf.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

import pikepdf

from pdfacts.backends.protocols import Box, EngineFailure, FailureKind, PageObject


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
    """A document opened by QPDF with recovery switched off: an ObjectReader.

    It holds the native handles; no method returns a pikepdf object. Values are
    reported as the file writes them: nothing is normalized, and whether a
    value is unknown is for the core to decide.

    Recovery being off does not keep QPDF from changing the document in
    memory. When it walks the page tree it gives a page without a usable media
    box US letter, turns a page written directly into /Kids into a new object,
    and copies a page that is listed twice into a new object; it says so only
    in a warning. So the document is opened twice: the page list comes from
    the view QPDF walked, the values from a view nothing walks, and a page
    whose object is not in the file's cross-reference table has no values.
    """

    def __init__(self, walked: pikepdf.Pdf, unwalked: pikepdf.Pdf, sources: list[_Input]) -> None:
        # Use open(); this takes documents that are already open.
        self._walked = walked
        self._unwalked = unwalked
        self._sources = sources
        self._warnings: list[str] = []
        # The objects of the cross-reference table, read on first use.
        self._file_objects: frozenset[tuple[int, int]] | None = None
        self._closed = False

    @classmethod
    def open(cls, data: bytes, password: str | None) -> QpdfDocument:
        """Open `data`. A damaged file is a FORMAT failure: recovery is off."""
        views: list[pikepdf.Pdf] = []
        sources = [_Input(data), _Input(data)]
        try:
            with _translated():
                for source in sources:
                    views.append(
                        pikepdf.open(
                            source,
                            password="" if password is None else password,
                            attempt_recovery=False,
                            inherit_page_attributes=False,
                        )
                    )
                # Walk the page tree now, so that what QPDF changed while
                # walking it is among the warnings from the start.
                len(views[0].pages)
        except BaseException:
            for view in views:
                view.close()
            for source in sources:
                source.close()
            raise
        return cls(views[0], views[1], sources)

    def page_count(self) -> int:
        self._check_open()
        with _translated():
            return len(self._walked.pages)

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

    def page_object(self, index: int) -> PageObject:
        """The page's object and what the file writes on it and its ancestors.

        A page QPDF made while walking the page tree is a FORMAT failure: its
        object number is not one of the file's, and a wrong object number is
        worse than none (docs/conventions.md, "Object identity").
        """
        count = self.page_count()
        _check_index(index, count)
        with _translated():
            objgen = self._walked.pages[index].obj.objgen
            if self._file_objects is None:
                self._file_objects = frozenset(self._unwalked.get_xref_table())
            if objgen not in self._file_objects:
                raise EngineFailure(
                    FailureKind.FORMAT,
                    f"page {index} is not an object of the file; QPDF made it while "
                    "walking the page tree",
                )
            page = self._unwalked.get_object(objgen)
            if not isinstance(page, pikepdf.Dictionary):
                raise EngineFailure(FailureKind.FORMAT, f"page {index} is not a dictionary")
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

    def warnings(self) -> tuple[str, ...]:
        """Everything QPDF reported so far, for example a media box it added.

        QPDF hands out each warning once and then forgets it, so they are
        collected here. The unwalked view reads objects the walked one has
        read already; a warning of its own is added only once.
        """
        self._check_open()
        with _translated():
            self._warnings.extend(str(message) for message in self._walked.get_warnings())
            for message in self._unwalked.get_warnings():
                if str(message) not in self._warnings:
                    self._warnings.append(str(message))
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


def _inherited(page: pikepdf.Dictionary, key: str) -> object:
    """The value of an inheritable key on the page or its nearest ancestor.

    The chain of /Parent is followed as far as it goes, and never twice
    through the same object.
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
