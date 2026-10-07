"""Fonts as the text of TextRules.V2 reads them: how a string splits into
codes, and each code's advance and text; the font's ascent and descent.

Built from plain values that backends/qpdf.py reads from a font dictionary
(FontEntries); ISO 32000-1, 9.6 to 9.10. The metrics of the standard 14
fonts and the glyph names come from Adobe's files (adobe_fonts.py).

What this module does not read raises FontNotRead, and what the file writes
against the specification FontInvalid; either leaves the text of the page
unknown. A code without text is a glyph whose text is unknown.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pdfacts.backends import adobe_fonts

# The text of a code whose ToUnicode entry is not UTF-16BE.
INVALID_TEXT = object()
# What ToUnicode.get gives for a code no entry maps.
ABSENT = object()


class FontNotRead(Exception):
    """A font, or a part of it, that pdfacts does not read."""


class FontInvalid(Exception):
    """A font the file writes against ISO 32000-1."""


@dataclass(frozen=True, slots=True)
class FontEntries:
    """A font dictionary's entries, as backends/qpdf.py reads them.

    Names are bytes without their slash; None where the entry is absent.
    `invalid` names an entry the file writes as ISO 32000-1 does not allow,
    which leaves the font unread.
    """

    subtype: bytes | None
    base_font: bytes | None
    # /Encoding: a name, or a dictionary with /BaseEncoding and /Differences
    # (code, glyph name).
    encoding: bytes | None = None
    encoding_dictionary: bool = False
    base_encoding: bytes | None = None
    differences: tuple[tuple[int, bytes], ...] = ()
    first_char: int | None = None
    last_char: int | None = None
    widths: tuple[float, ...] | None = None
    # Of the font descriptor (the descendant's, for a Type0 font).
    flags: int | None = None
    missing_width: float | None = None
    ascent: float | None = None
    descent: float | None = None
    embedded: bool = False
    # The clear-text part of an embedded Type 1 font program (/FontFile, the
    # first /Length1 bytes), where the font has one.
    type1_program: bytes | None = None
    # The decoded data of /ToUnicode.
    to_unicode: bytes | None = None
    # Type3: /FontMatrix and /FontBBox of the font dictionary.
    font_matrix: tuple[float, ...] | None = None
    font_box: tuple[float, ...] | None = None
    # Type0: the descendant's /Subtype, /DW, and /W as runs (first, last,
    # width) in the order /W writes them.
    descendant: bytes | None = None
    default_width: float | None = None
    cid_widths: tuple[tuple[int, int, float], ...] = ()
    invalid: str | None = None


@dataclass(frozen=True, slots=True)
class Font:
    """How the text of TextRules.V2 reads a font."""

    two_bytes: bool
    # The advance of a code, in text space units at a font size of 1; None
    # where the file gives none (a standard font's code whose glyph its
    # metrics lack).
    width_of: Callable[[int], float | None]
    # The text of a code: a str, None where the font maps it to none, or
    # INVALID_TEXT.
    text: Callable[[int], object]
    ascent: float | None
    descent: float | None

    def codes(self, data: bytes) -> list[int]:
        """The codes a string shows: one byte each, or two for a font of
        Identity-H; a last odd byte of a two-byte string is no code."""
        if not self.two_bytes:
            return list(data)
        return [(data[i] << 8) | data[i + 1] for i in range(0, len(data) - 1, 2)]

    def width(self, code: int) -> float | None:
        return self.width_of(code)


STANDARD_ENCODINGS = {
    b"StandardEncoding": adobe_fonts.STANDARD_ENCODING,
    b"WinAnsiEncoding": adobe_fonts.WIN_ANSI_ENCODING,
    b"MacRomanEncoding": adobe_fonts.MAC_ROMAN_ENCODING,
}
_SYMBOLIC_STANDARD = ("Symbol", "ZapfDingbats")


def standard_name(base_font: bytes | None) -> str | None:
    """The standard 14 font `base_font` names, without a subset prefix of six
    capital letters and a plus; None for any other font."""
    if base_font is None:
        return None
    tagged = len(base_font) > 7 and base_font[6:7] == b"+" and base_font[:6].isalpha()
    name = base_font[7:] if tagged and base_font[:6].isupper() else base_font
    try:
        text = name.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if text in adobe_fonts.STANDARD_FONTS else None


_HEXADECIMAL = frozenset("0123456789ABCDEF")


def _component_text(component: str, dingbats: bool) -> str:
    """One component of a glyph name, as the Adobe Glyph List specification
    maps it; an empty string where no rule maps it."""
    if dingbats and component in adobe_fonts.ZAPF_DINGBATS_GLYPH_LIST:
        return adobe_fonts.ZAPF_DINGBATS_GLYPH_LIST[component]
    if component in adobe_fonts.GLYPH_LIST:
        return adobe_fonts.GLYPH_LIST[component]
    digits = component[3:]
    if component.startswith("uni") and digits and len(digits) % 4 == 0:
        if set(digits) <= _HEXADECIMAL:
            values = [int(digits[i : i + 4], 16) for i in range(0, len(digits), 4)]
            if all(not 0xD800 <= v <= 0xDFFF for v in values):
                return "".join(chr(v) for v in values)
    digits = component[1:]
    if component.startswith("u") and 4 <= len(digits) <= 6 and set(digits) <= _HEXADECIMAL:
        value = int(digits, 16)
        if value <= 0x10FFFF and not 0xD800 <= value <= 0xDFFF:
            return chr(value)
    return ""


def glyph_text(name: bytes, dingbats: bool) -> str | None:
    """The text a glyph name stands for, as the Adobe Glyph List
    specification maps it: the part before a full stop, its components
    between underscores each by the ZapfDingbats list (for that font), the
    glyph list, or a name uniXXXX... or uXXXX[XX]; None where it gives none."""
    try:
        text = name.decode("ascii")
    except UnicodeDecodeError:
        return None
    base = text.split(".", 1)[0]
    found = "".join(_component_text(component, dingbats) for component in base.split("_"))
    return found or None


def font_of(entries: FontEntries) -> Font:
    """The font `entries` describe; FontNotRead or FontInvalid where the
    text of the page cannot be read with it."""
    if entries.invalid is not None:
        raise FontInvalid(entries.invalid)
    to_unicode = parse_to_unicode(entries.to_unicode) if entries.to_unicode is not None else None
    if entries.subtype == b"Type0":
        return _composite(entries, to_unicode)
    if entries.subtype == b"Type3":
        return _type3(entries, to_unicode)
    if entries.subtype in (b"Type1", b"MMType1", b"TrueType"):
        return _simple(entries, to_unicode)
    raise FontNotRead(f"pdfacts does not read a font of subtype {_shown(entries.subtype)}")


def _shown(name: bytes | None) -> str:
    return "none" if name is None else "/" + name.decode("latin-1")


# The codes below 32 that are ASCII white space: tab, line feed, form feed and
# carriage return.
WHITE_SPACE_CODES = frozenset((9, 10, 12, 13))


def _texts(to_unicode, names: tuple[bytes | None, ...] | None, dingbats: bool):
    """The text of a code: its ToUnicode entry; without one, its glyph name's
    by the encoding; and where the encoding is known and names no glyph for
    a code below 32 that is ASCII white space, that character."""
    cache: dict[int, object] = {}

    def text(code: int) -> object:
        if code not in cache:
            found = to_unicode.get(code) if to_unicode is not None else ABSENT
            if found is ABSENT:
                found = None
                if names is not None and code < len(names):
                    name = names[code]
                    if name is not None:
                        found = glyph_text(name, dingbats)
                    elif code in WHITE_SPACE_CODES:
                        found = chr(code)
            cache[code] = found
        return cache[code]

    return text


def type1_encoding(program: bytes) -> tuple[bytes | None, ...] | None:
    """The built-in encoding a Type 1 font program declares in its clear-text
    part: /Encoding StandardEncoding def, or an array that dup and put fill
    (Adobe Type 1 Font Format, 2.3); None where it declares neither."""
    found = re.search(rb"/Encoding\s+(StandardEncoding\s+def|\d+\s+array)", program)
    if found is None:
        return None
    if found.group(1).startswith(b"StandardEncoding"):
        return _names(adobe_fonts.STANDARD_ENCODING)
    names: list[bytes | None] = [None] * 256
    end = program.find(b"readonly def", found.end())
    part = program[found.end() : end if end >= 0 else len(program)]
    for code, name in re.findall(rb"dup\s+(\d+)\s*/([^\s/\[\]()<>{}%]+)\s+put", part):
        if int(code) < 256:
            names[int(code)] = name
    return tuple(names)


def _names(table) -> tuple[bytes | None, ...]:
    return tuple(n.encode("ascii") if n is not None else None for n in table)


def _own_encoding(entries: FontEntries, standard: str | None) -> tuple[bytes | None, ...] | None:
    """The font's own encoding, which /Encoding builds on: a standard 14
    font's from its AFM file; an embedded Type 1 program's as it declares
    it; StandardEncoding for a font neither embedded nor symbolic (Table
    114); None where pdfacts does not know it."""
    if standard is not None:
        return _names(adobe_fonts.STANDARD_FONTS[standard][4])
    if entries.type1_program is not None:
        return type1_encoding(entries.type1_program)
    flags = entries.flags
    if not entries.embedded and flags is not None and flags & 32 and not flags & 4:
        return _names(adobe_fonts.STANDARD_ENCODING)
    return None


def _encoding_names(entries: FontEntries, standard: str | None) -> tuple[bytes | None, ...] | None:
    """The glyph name of each code: /Encoding over the font's own encoding.
    A symbolic standard 14 font keeps its own encoding under /Differences,
    whatever /Encoding or /BaseEncoding names (9.6.6.1)."""
    own = _own_encoding(entries, standard)
    symbolic = standard in _SYMBOLIC_STANDARD
    for named in (entries.encoding, entries.base_encoding):
        if named is not None and named not in STANDARD_ENCODINGS:
            raise FontNotRead(f"pdfacts does not read the encoding {_shown(named)}")
    if entries.encoding is not None and not entries.encoding_dictionary:
        return own if symbolic else _names(STANDARD_ENCODINGS[entries.encoding])
    if not entries.encoding_dictionary:
        return own
    if entries.base_encoding is not None and not symbolic:
        names: list[bytes | None] = list(_names(STANDARD_ENCODINGS[entries.base_encoding]))
    elif own is not None:
        names = list(own)
    else:
        names = [None] * 256
    for code, name in entries.differences:
        if 0 <= code < 256:
            names[code] = name
    return tuple(names)


def _listed_widths(entries: FontEntries, unit: Callable[[float], float]) -> dict[int, float]:
    """/Widths from /FirstChar, each in text space units by `unit`;
    FontInvalid where /FirstChar is absent or /LastChar does not end them."""
    if entries.first_char is None:
        raise FontInvalid("a font with /Widths and without /FirstChar")
    first = entries.first_char
    widths = entries.widths or ()
    if entries.last_char is not None and entries.last_char - first + 1 != len(widths):
        raise FontInvalid("a font whose /Widths does not run from /FirstChar to /LastChar")
    return {first + offset: unit(value) for offset, value in enumerate(widths)}


def _simple(entries: FontEntries, to_unicode) -> Font:
    standard = standard_name(entries.base_font) if not entries.embedded else None
    names = _encoding_names(entries, standard)
    if entries.widths is not None:
        widths = _listed_widths(entries, lambda value: value / 1000)
        default: float | None = (entries.missing_width or 0.0) / 1000
    elif standard is not None:
        metrics = adobe_fonts.STANDARD_FONTS[standard][3]
        widths = {}
        for code, name in enumerate(names or ()):
            if name is not None:
                width = metrics.get(name.decode("ascii", "replace"))
                if width is not None:
                    widths[code] = width / 1000
        default = None
    else:
        raise FontInvalid("a font without /Widths that is not a standard 14 font")
    ascent, descent = entries.ascent, entries.descent
    if standard is not None:
        ascender, descender, box = adobe_fonts.STANDARD_FONTS[standard][:3]
        if ascent is None:
            ascent = ascender if ascender is not None else box[3]
        if descent is None:
            descent = descender if descender is not None else box[1]
    return Font(
        two_bytes=False,
        width_of=lambda code: widths.get(code, default),
        text=_texts(to_unicode, names, standard == "ZapfDingbats"),
        ascent=ascent / 1000 if ascent is not None else None,
        descent=descent / 1000 if descent is not None else None,
    )


def _type3(entries: FontEntries, to_unicode) -> Font:
    matrix = entries.font_matrix
    if matrix is None or len(matrix) != 6:
        raise FontInvalid("a Type3 font without a /FontMatrix of six numbers")
    a, b, c, d = matrix[:4]
    if b or c or a <= 0 or d == 0:
        raise FontNotRead("pdfacts does not read a Type3 font whose matrix turns or skews")
    if entries.widths is None:
        raise FontInvalid("a Type3 font without /Widths")
    names: list[bytes | None] = [None] * 256
    for code, name in entries.differences:
        if 0 <= code < 256:
            names[code] = name
    widths = _listed_widths(entries, lambda value: value * a)
    missing = (entries.missing_width or 0.0) * a
    ascent = descent = None
    box = entries.font_box
    if box is not None and len(box) == 4:
        low, high = sorted((box[1] * d, box[3] * d))
        if low < high:
            descent, ascent = low, high
    return Font(
        two_bytes=False,
        width_of=lambda code: widths.get(code, missing),
        text=_texts(to_unicode, tuple(names), False),
        ascent=ascent,
        descent=descent,
    )


def _cid_width(runs: tuple[tuple[int, int, float], ...], default: float):
    """The width of a CID by /W, looked up when asked for: a range of /W may
    span more CIDs than a font holds. Where /W gives a CID twice, the first
    holds."""
    single: dict[int, tuple[int, float]] = {}
    ranges: list[tuple[int, int, int, float]] = []
    for order, (first, last, width) in enumerate(runs):
        if first == last:
            single.setdefault(first, (order, width / 1000))
        else:
            ranges.append((order, first, last, width / 1000))

    def width(code: int) -> float:
        found = single.get(code)
        for order, first, last, value in ranges:
            if found is not None and order > found[0]:
                break
            if first <= code <= last:
                return value
        return found[1] if found is not None else default

    return width


def _composite(entries: FontEntries, to_unicode) -> Font:
    if entries.encoding_dictionary or entries.encoding != b"Identity-H":
        shown = "a CMap stream" if entries.encoding_dictionary else _shown(entries.encoding)
        raise FontNotRead(f"pdfacts does not read the CMap {shown}")
    default = (entries.default_width if entries.default_width is not None else 1000) / 1000
    ascent, descent = entries.ascent, entries.descent
    return Font(
        two_bytes=True,
        width_of=_cid_width(entries.cid_widths, default),
        text=_texts(to_unicode, None, False),
        ascent=ascent / 1000 if ascent is not None else None,
        descent=descent / 1000 if descent is not None else None,
    )


# --- ToUnicode ---------------------------------------------------------------------


class ToUnicode:
    """The bfchar and bfrange entries of a ToUnicode CMap (9.10.3). Ranges are
    looked up when a code is asked for; where entries overlap, the one written
    last holds."""

    __slots__ = ("chars", "ranges")

    def __init__(self) -> None:
        self.chars: dict[int, tuple[int, bytes]] = {}  # code: order, destination
        self.ranges: list[tuple[int, int, int, object]] = []  # order, low, high, destination(s)

    def get(self, code: int) -> object:
        """The text of `code`: a str; None where an entry maps it to none;
        INVALID_TEXT where its destination is not UTF-16BE, or a range's
        destination would step past its last byte, which 9.10.3 leaves
        undefined; ABSENT where no entry maps it."""
        char = self.chars.get(code)
        for order, low, high, target in reversed(self.ranges):
            if char is not None and order < char[0]:
                break
            if low <= code <= high:
                offset = code - low
                if isinstance(target, list):
                    return _utf16(target[offset]) if offset < len(target) else INVALID_TEXT
                if not target or target[-1] + offset > 255:
                    return INVALID_TEXT
                return _utf16(target[:-1] + bytes([target[-1] + offset]))
        return _utf16(char[1]) if char is not None else ABSENT


def _utf16(data: bytes) -> object:
    if not data:
        return None
    try:
        return data.decode("utf-16-be")
    except UnicodeDecodeError:
        return INVALID_TEXT


def _tokens(data: bytes) -> list[bytes]:
    out: list[bytes] = []
    i, n = 0, len(data)
    while i < n:
        c = data[i : i + 1]
        if c in b" \t\r\n\f\x00":
            i += 1
        elif c == b"%":
            while i < n and data[i : i + 1] not in b"\r\n":
                i += 1
        elif c == b"<" and data[i : i + 2] != b"<<":
            end = data.find(b">", i)
            if end < 0:
                break
            out.append(data[i : end + 1])
            i = end + 1
        elif c in b"[]":
            out.append(c)
            i += 1
        elif c == b"(":
            # A literal string: no entry of a ToUnicode CMap uses one; skipped.
            depth, i = 1, i + 1
            while i < n and depth:
                ch = data[i : i + 1]
                if ch == b"\\":
                    i += 1
                elif ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                i += 1
            out.append(b"()")
        else:
            start = i
            while i < n and data[i : i + 1] not in b" \t\r\n\f\x00<>[]()%":
                i += 1
            out.append(data[start:i] or data[start : start + 1])
            i = max(i, start + 1)
    return out


def _hex(token: bytes) -> bytes | None:
    digits = bytes(c for c in token[1:-1] if c not in b" \t\r\n\f\x00")
    if len(digits) % 2:
        digits += b"0"
    try:
        return bytes.fromhex(digits.decode("ascii"))
    except ValueError:
        return None


def parse_to_unicode(data: bytes) -> ToUnicode:
    """The bfchar and bfrange entries of `data`; an entry not written as
    hexadecimal strings is passed over."""
    tokens = _tokens(data)
    out = ToUnicode()
    order = 0
    i = 0
    while i < len(tokens):
        if tokens[i] == b"beginbfchar":
            i += 1
            while i + 1 < len(tokens) and tokens[i] != b"endbfchar":
                source, target = _hex(tokens[i]), _hex(tokens[i + 1])
                if (
                    tokens[i][:1] == b"<"
                    and tokens[i + 1][:1] == b"<"
                    and source
                    and target is not None
                ):
                    order += 1
                    out.chars[int.from_bytes(source, "big")] = (order, target)
                i += 2
        elif tokens[i] == b"beginbfrange":
            i += 1
            while i + 2 < len(tokens) and tokens[i] != b"endbfrange":
                low, high = _hex(tokens[i]), _hex(tokens[i + 1])
                if tokens[i + 2] == b"[":
                    j = i + 3
                    items: list[bytes] = []
                    while j < len(tokens) and tokens[j] != b"]":
                        found = _hex(tokens[j]) if tokens[j][:1] == b"<" else None
                        items.append(found if found is not None else b"")
                        j += 1
                    if low and high:
                        order += 1
                        out.ranges.append(
                            (order, int.from_bytes(low, "big"), int.from_bytes(high, "big"), items)
                        )
                    i = j + 1
                else:
                    target = _hex(tokens[i + 2]) if tokens[i + 2][:1] == b"<" else None
                    if low and high and target is not None:
                        order += 1
                        out.ranges.append(
                            (order, int.from_bytes(low, "big"), int.from_bytes(high, "big"), target)
                        )
                    i += 3
        else:
            i += 1
    return out
