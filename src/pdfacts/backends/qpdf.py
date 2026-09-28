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

    It holds the native handle; no method returns a pikepdf object. Values are
    reported as the file writes them: nothing is normalized, and whether a
    value is unknown is for the core to decide.
    """

    def __init__(self, pdf: pikepdf.Pdf, source: _Input) -> None:
        # Use open(); this takes a document that is already open.
        self._pdf = pdf
        self._source = source
        self._warnings: list[str] = []
        self._closed = False

    @classmethod
    def open(cls, data: bytes, password: str | None) -> QpdfDocument:
        """Open `data`. A damaged file is a FORMAT failure: `open` repairs nothing."""
        source = _Input(data)
        with _translated():
            pdf = pikepdf.open(
                source,
                password="" if password is None else password,
                attempt_recovery=False,
            )
        return cls(pdf, source)

    def page_count(self) -> int:
        self._check_open()
        with _translated():
            return len(self._pdf.pages)

    def object_count(self) -> int | None:
        """The /Size of the trailer; None unless it is an integer greater than 0.

        Objects are not counted instead: that is a different number.
        """
        self._check_open()
        with _translated():
            size = self._pdf.trailer.get("/Size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return None
        return int(size)

    def page_object(self, index: int) -> PageObject:
        count = self.page_count()
        _check_index(index, count)
        with _translated():
            page = self._pdf.pages[index].obj
            number, generation = page.objgen
            user_unit, user_unit_invalid = _user_unit(page.get("/UserUnit"))
            return PageObject(
                object_number=int(number),
                generation=int(generation),
                media_box=_box(page.get("/MediaBox")),
                crop_box=_box(page.get("/CropBox")),
                rotate=_integer(page.get("/Rotate")),
                user_unit=user_unit,
                user_unit_invalid=user_unit_invalid,
            )

    def warnings(self) -> tuple[str, ...]:
        """Everything QPDF reported so far, for example a media box it added.

        QPDF hands out each warning once and then forgets it, so they are
        collected here.
        """
        self._check_open()
        with _translated():
            self._warnings.extend(str(message) for message in self._pdf.get_warnings())
        return tuple(self._warnings)

    def close(self) -> None:
        """Close the document. A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        with _translated():
            self._pdf.close()
        self._source.close()

    def _check_open(self) -> None:
        # The backend's own flag: what the engine does with a closed handle
        # is not something to rely on.
        if self._closed:
            raise EngineFailure(FailureKind.OTHER, "closed")


def _number(value: object) -> float | None:
    """A PDF number as a finite float; None for anything else, a flag included."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
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
