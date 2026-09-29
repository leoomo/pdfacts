"""Text strings and names: how the file's bytes become text.

A text string is decoded as ISO 32000-2 7.9.2.2 says; a name is returned as
its exact bytes and as text, UTF-8 where the bytes are valid UTF-8, Latin-1
otherwise (docs/conventions.md, "Forms"). Nothing here reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Name", "NameEncoding", "decode_name", "decode_text_string"]


class NameEncoding(StrEnum):
    """How a name's bytes became its text."""

    UTF8 = "utf-8"
    LATIN1 = "latin-1"


@dataclass(frozen=True, slots=True)
class Name:
    """A name as the file writes it: its bytes, and its text.

    Given by position or by name. The bytes are a name's bytes without its
    slash, `#xx` resolved.
    """

    raw: bytes
    text: str
    encoding: NameEncoding

    def __post_init__(self) -> None:
        object.__setattr__(self, "encoding", NameEncoding(self.encoding))
        if type(self.raw) is not bytes:
            raise TypeError(f"raw must be bytes, not {type(self.raw).__name__}")
        if type(self.text) is not str:
            raise TypeError(f"text must be str, not {type(self.text).__name__}")
        if (self.text, self.encoding) != _decoded(self.raw):
            raise ValueError(
                "text must be what raw decodes to in its encoding: "
                f"{_decoded(self.raw)[0]!r} in {_decoded(self.raw)[1]!r}"
            )


# ISO 32000-2, Annex D, Table D.2; the three undefined bytes as U+FFFD
# (measured against the engines).
_PDFDOC = [chr(code) for code in range(256)]
_PDFDOC[0x18:0x20] = "˘ˇˆ˙˝˛˚˜"
_PDFDOC[0x80:0x9F] = "•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž"
_PDFDOC[0xA0] = "€"
for _code in (0x7F, 0x9F, 0xAD):
    _PDFDOC[_code] = "�"


def decode_text_string(data: bytes) -> str:
    """The text of a text string of the file; it never fails.

    UTF-16BE after FE FF, UTF-16LE after FF FE (both engines read it),
    UTF-8 after EF BB BF, PDFDocEncoding otherwise. Language escape
    sequences are removed from the Unicode forms; what does not decode
    becomes U+FFFD.
    """
    if type(data) is not bytes:
        raise TypeError(f"a text string is bytes, not {type(data).__name__}")
    if data[:2] == b"\xfe\xff":
        return _without_language_escapes(data[2:].decode("utf-16-be", "replace"))
    if data[:2] == b"\xff\xfe":
        return _without_language_escapes(data[2:].decode("utf-16-le", "replace"))
    if data[:3] == b"\xef\xbb\xbf":
        return _without_language_escapes(data[3:].decode("utf-8", "replace"))
    return "".join(_PDFDOC[byte] for byte in data)


def decode_name(data: bytes) -> Name:
    """A name from its bytes, without the slash, `#xx` resolved."""
    if type(data) is not bytes:
        raise TypeError(f"a name is bytes, not {type(data).__name__}")
    return Name(data, *_decoded(data))


def _decoded(raw: bytes) -> tuple[str, NameEncoding]:
    try:
        return raw.decode("utf-8"), NameEncoding.UTF8
    except UnicodeDecodeError:
        return raw.decode("latin-1"), NameEncoding.LATIN1


def _without_language_escapes(text: str) -> str:
    """The text without language escape sequences (ISO 32000-2 7.9.2.2).

    A U+001B, a language code, an optional country code and another U+001B
    mark a language, not text. An escape that is not closed is removed with
    everything that follows it.
    """
    out: list[str] = []
    start = 0
    while (at := text.find("\x1b", start)) >= 0:
        out.append(text[start:at])
        closing = text.find("\x1b", at + 1)
        if closing < 0:
            return "".join(out)
        start = closing + 1
    out.append(text[start:])
    return "".join(out)
