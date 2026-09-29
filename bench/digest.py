"""The digest: SHA-256 over a sequence of values, for comparing platforms.

Each value is encoded as a letter for its type (`i` for an int, `f` for a
float, `s` for a string), then the text, then the separator `\\n` (one byte
that cannot occur inside a number). The text is the decimal notation for an
int, `repr` for a float — the shortest text that reads back to the same
number, with negative zero written as zero — and the UTF-8 bytes of a string.

A value is accepted only when its type is exactly int, float or str; a bool,
an enum member or any other type raises TypeError, and a value that is not a
finite number raises ValueError. Standard library only.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable

SEPARATOR = b"\n"
LETTERS = {int: b"i", float: b"f", str: b"s"}


def encode(value: int | float | str) -> bytes:
    """The bytes of one value, its separator included."""
    letter = LETTERS.get(type(value))
    if letter is None:
        raise TypeError(f"a digest takes int, float or str, not {type(value).__name__}")
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"a digest takes finite numbers, not {value}")
        text = repr(value + 0.0)
    elif type(value) is str:
        if "\n" in value:
            raise ValueError("a string in a digest cannot contain the separator")
        text = value
    else:
        text = str(value)
    return letter + text.encode("utf-8") + SEPARATOR


class Digest:
    """SHA-256 over values, hashed as they arrive."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()

    def update(self, *values: int | float | str) -> None:
        for value in values:
            self._hash.update(encode(value))

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


def digest(values: Iterable[int | float | str]) -> str:
    whole = Digest()
    whole.update(*values)
    return whole.hexdigest()
