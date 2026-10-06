"""The probe of M4b: a page's glyphs read from its content stream with QPDF's
tokens (pikepdf), every glyph the content shows, white space included.

A prototype for the measurement of the probe (ROADMAP.md, M4b), not part of
pdfacts. It follows ISO 32000-1, 9.4 (text objects), 9.2.4 (glyph
positioning), 9.6 to 9.7 (simple and composite fonts) and 9.10.3 (ToUnicode).
The metrics of the standard fonts and the glyph names come from pypdf's
tables (BSD; the AFM files' notice is in pypdf._codecs.core_font_metrics).

What it does not read raises Unreadable, for the whole page: CMaps other
than Identity-H, vertical writing, a Type3 font matrix that rotates or
skews.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pikepdf
from pypdf._codecs import adobe_glyphs, charset_encoding
from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FORM_DEPTH = 12


class Unreadable(Exception):
    """The page holds something this prototype does not read."""


def multiply(m, n):
    """m then n: the matrix of applying m, then n (row vectors, as PDF)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


def numbers(operands, count):
    if len(operands) != count:
        raise Unreadable(f"{count} operands expected, not {len(operands)}")
    try:
        return [float(value) for value in operands]
    except (TypeError, ValueError) as error:
        raise Unreadable("an operand is not a number") from error


@dataclass(frozen=True)
class RawGlyph:
    """One code a text showing operator shows."""

    code: int
    text: str | None  # None where neither ToUnicode nor the encoding maps it
    trm: tuple  # the text rendering matrix: text space to user space
    width: float  # the glyph's advance in text space units (em)
    ascent: float | None  # per em
    descent: float | None
    render_mode: int


# --- fonts --------------------------------------------------------------------------


_TOKEN = re.compile(rb"<[0-9A-Fa-f\s]*>|\[|\]|[^\s<>\[\]]+")


def _hex(token: bytes) -> bytes:
    digits = re.sub(rb"\s", b"", token[1:-1])
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode())


def _utf16(data: bytes) -> str:
    """The text of a ToUnicode destination; U+FFFD for what is no UTF-16BE."""
    return data.decode("utf-16-be", "replace")


class ToUnicode:
    """bfchar and bfrange of a ToUnicode CMap. Ranges are kept as ranges and
    looked up when a code is asked for: a CMap of a few kilobytes may span
    tens of thousands of codes. Where entries overlap, the one written last
    holds."""

    def __init__(self) -> None:
        self.chars: dict[int, tuple[int, str]] = {}  # code: order, text
        self.ranges: list[tuple[int, int, int, object]] = []  # order, low, high, bytes or list of bytes
        self.order = 0

    def get(self, code: int) -> str | None:
        """The text of `code`; None where the CMap does not map it."""
        char = self.chars.get(code)
        for order, low, high, target in reversed(self.ranges):
            if char is not None and order < char[0]:
                break
            if low <= code <= high:
                offset = code - low
                if isinstance(target, list):
                    return _utf16(target[offset]) if offset < len(target) else None
                if len(target) >= 2:
                    last = int.from_bytes(target[-2:], "big") + offset
                    return _utf16(target[:-2] + (last & 0xFFFF).to_bytes(2, "big"))
                return _utf16(bytes([(target[-1] + offset) & 0xFF]))
        return char[1] if char is not None else None


def parse_to_unicode(data: bytes) -> ToUnicode:
    tokens = _TOKEN.findall(data)
    out = ToUnicode()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == b"beginbfchar":
            i += 1
            while i + 1 < len(tokens) and tokens[i] != b"endbfchar":
                if tokens[i].startswith(b"<") and tokens[i + 1].startswith(b"<"):
                    out.order += 1
                    out.chars[int.from_bytes(_hex(tokens[i]), "big")] = (out.order, _utf16(_hex(tokens[i + 1])))
                i += 2
        elif token == b"beginbfrange":
            i += 1
            while i + 2 < len(tokens) and tokens[i] != b"endbfrange":
                low = int.from_bytes(_hex(tokens[i]), "big")
                high = int.from_bytes(_hex(tokens[i + 1]), "big")
                if tokens[i + 2] == b"[":
                    j = i + 3
                    items = []
                    while j < len(tokens) and tokens[j] != b"]":
                        items.append(_hex(tokens[j]))
                        j += 1
                    out.order += 1
                    out.ranges.append((out.order, low, high, items))
                    i = j + 1
                else:
                    out.order += 1
                    out.ranges.append((out.order, low, high, _hex(tokens[i + 2])))
                    i += 3
        else:
            i += 1
    return out


def _encoding(name: str) -> list[str | None]:
    table = charset_encoding.get(name)
    if table is None:
        raise Unreadable(f"the encoding {name}")
    return [None if ch in ("\x00", "") and code else ch for code, ch in enumerate(table)]


def _glyph_name_text(name: str) -> str | None:
    text = adobe_glyphs.get(name if name.startswith("/") else "/" + name)
    if text:
        return text
    bare = name.lstrip("/")
    if bare.startswith("uni") and len(bare) >= 7:
        try:
            return "".join(chr(int(bare[i : i + 4], 16)) for i in range(3, len(bare) - 3, 4))
        except ValueError:
            return None
    if bare.startswith("u") and 5 <= len(bare) <= 7:
        try:
            return chr(int(bare[1:], 16))
        except ValueError:
            return None
    return None


def _base_name(font) -> str:
    name = str(font.get("/BaseFont", ""))[1:]
    return name.split("+", 1)[1] if "+" in name[:7] else name


_STANDARD = {
    "Arial": "Helvetica",
    "Arial,Bold": "Helvetica-Bold",
    "Arial,Italic": "Helvetica-Oblique",
    "Arial,BoldItalic": "Helvetica-BoldOblique",
    "TimesNewRoman": "Times-Roman",
    "CourierNew": "Courier",
}


@dataclass
class Font:
    composite: bool
    widths: dict[int, float] = field(default_factory=dict)  # per em
    default_width: float = 0.0
    to_unicode: ToUnicode = field(default_factory=lambda: ToUnicode())
    encoding: list[str | None] | None = None
    ascent: float | None = None
    descent: float | None = None
    _texts: dict = field(default_factory=dict)

    def codes(self, data: bytes):
        if self.composite:
            if len(data) % 2:
                data = data[:-1]
            return [(int.from_bytes(data[i : i + 2], "big"), 2) for i in range(0, len(data), 2)]
        return [(byte, 1) for byte in data]

    def width(self, code: int) -> float:
        return self.widths.get(code, self.default_width)

    def text(self, code: int) -> str | None:
        if code not in self._texts:
            self._texts[code] = self._text(code)
        return self._texts[code]

    def _text(self, code: int) -> str | None:
        mapped = self.to_unicode.get(code)
        if mapped is not None:
            return mapped or None
        if self.encoding is not None and code < 256:
            return self.encoding[code]
        return None


def _descriptor_metrics(descriptor):
    if not isinstance(descriptor, pikepdf.Dictionary):
        return None, None
    ascent, descent = descriptor.get("/Ascent"), descriptor.get("/Descent")
    a = float(ascent) / 1000 if ascent is not None else None
    d = float(descent) / 1000 if descent is not None else None
    return a, d


def load_font(font) -> Font:
    subtype = font.get("/Subtype")
    to_unicode = ToUnicode()
    if isinstance(font.get("/ToUnicode"), pikepdf.Stream):
        to_unicode = parse_to_unicode(font.ToUnicode.read_bytes())
    if subtype == "/Type0":
        encoding = font.get("/Encoding")
        if encoding not in ("/Identity-H",):
            raise Unreadable(f"the CMap {encoding}")
        cid = font.DescendantFonts[0]
        out = Font(composite=True, to_unicode=to_unicode)
        out.default_width = float(cid.get("/DW", 1000)) / 1000
        w = cid.get("/W")
        if w is not None:
            items = list(w)
            i = 0
            while i < len(items):
                first = int(items[i])
                if isinstance(items[i + 1], pikepdf.Array):
                    for offset, value in enumerate(items[i + 1]):
                        out.widths[first + offset] = float(value) / 1000
                    i += 2
                else:
                    last, value = int(items[i + 1]), float(items[i + 2]) / 1000
                    for code in range(first, last + 1):
                        out.widths[code] = value
                    i += 3
        out.ascent, out.descent = _descriptor_metrics(cid.get("/FontDescriptor"))
        return out
    if subtype == "/Type3":
        return _load_type3(font, to_unicode)
    if subtype not in ("/Type1", "/TrueType", "/MMType1"):
        raise Unreadable(f"a font of subtype {subtype}")
    out = Font(composite=False, to_unicode=to_unicode)
    base = _base_name(font)
    standard = CORE_FONT_METRICS.get(_STANDARD.get(base, base))
    encoding = font.get("/Encoding")
    if base == "ZapfDingbats":
        table = _encoding("/ZapfDingbats")
    elif base == "Symbol":
        table = _encoding("/Symbol")
    elif isinstance(encoding, pikepdf.Name):
        table = _encoding(str(encoding))
    elif isinstance(encoding, pikepdf.Dictionary):
        table = _encoding(str(encoding.get("/BaseEncoding", "/StandardEncoding")))
        differences = encoding.get("/Differences")
        if differences is not None:
            code = 0
            for item in differences:
                if isinstance(item, pikepdf.Name):
                    if 0 <= code < 256:
                        table[code] = _glyph_name_text(str(item))
                    code += 1
                else:
                    code = int(item)
    else:
        table = _encoding("/StandardEncoding")
    out.encoding = table
    descriptor = font.get("/FontDescriptor")
    widths = font.get("/Widths")
    if widths is not None:
        first = int(font.get("/FirstChar", 0))
        for offset, value in enumerate(widths):
            out.widths[first + offset] = float(value) / 1000
        missing = descriptor.get("/MissingWidth", 0) if isinstance(descriptor, pikepdf.Dictionary) else 0
        out.default_width = float(missing) / 1000
    elif standard is not None:
        by_char = standard.character_widths
        for code, ch in enumerate(table):
            if ch is not None and ch in by_char:
                out.widths[code] = by_char[ch] / 1000
        out.default_width = by_char.get("default", 0) / 1000
    else:
        raise Unreadable(f"a simple font without /Widths that is not standard: {base}")
    out.ascent, out.descent = _descriptor_metrics(descriptor)
    if (out.ascent is None or out.descent is None) and standard is not None:
        out.ascent = standard.font_descriptor.ascent / 1000
        out.descent = standard.font_descriptor.descent / 1000
    return out


def _load_type3(font, to_unicode) -> Font:
    """A Type3 font: widths in glyph space, mapped to text space by
    /FontMatrix (9.6.5); the ascent and descent from /FontBBox likewise."""
    matrix = [float(v) for v in font.get("/FontMatrix", [0.001, 0, 0, 0.001, 0, 0])]
    if matrix[1] or matrix[2] or matrix[0] <= 0 or matrix[3] == 0:
        raise Unreadable("a Type3 font matrix that rotates or skews")
    out = Font(composite=False, to_unicode=to_unicode)
    table: list[str | None] = [None] * 256
    encoding = font.get("/Encoding")
    if isinstance(encoding, pikepdf.Dictionary):
        code = 0
        for item in encoding.get("/Differences", []):
            if isinstance(item, pikepdf.Name):
                if 0 <= code < 256:
                    table[code] = _glyph_name_text(str(item))
                code += 1
            else:
                code = int(item)
    out.encoding = table
    first = int(font.get("/FirstChar", 0))
    for offset, value in enumerate(font.get("/Widths", [])):
        out.widths[first + offset] = float(value) * matrix[0]
    box = [float(v) for v in font.get("/FontBBox", [0, 0, 0, 0])]
    ys = sorted((box[1] * matrix[3], box[3] * matrix[3]))
    if ys[0] < ys[1]:
        out.descent, out.ascent = ys
    else:
        out.ascent, out.descent = _descriptor_metrics(font.get("/FontDescriptor"))
    return out


# --- content ------------------------------------------------------------------------


@dataclass
class State:
    ctm: tuple = IDENTITY
    font: Font | None = None
    size: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    scale: float = 1.0
    leading: float = 0.0
    render_mode: int = 0
    rise: float = 0.0

    def copy(self) -> State:
        return State(**self.__dict__)


def _inherited_resources(page):
    node = page
    for _ in range(64):
        resources = node.get("/Resources")
        if isinstance(resources, pikepdf.Dictionary):
            return resources
        node = node.get("/Parent")
        if not isinstance(node, pikepdf.Dictionary):
            return pikepdf.Dictionary()
    raise Unreadable("the page tree loops")


class Reader:
    def __init__(self) -> None:
        self.fonts: dict = {}
        self.glyphs: list[RawGlyph] = []

    def font(self, resources, name):
        fonts = resources.get("/Font")
        if not isinstance(fonts, pikepdf.Dictionary) or name not in fonts:
            raise Unreadable(f"the font {name} is not in the resources")
        font = fonts[name]
        key = font.objgen if font.is_indirect else id(font)
        if key not in self.fonts:
            self.fonts[key] = load_font(font)
        return self.fonts[key]

    def run(self, stream, resources, state: State, depth: int, entered: tuple) -> None:
        stack: list[State] = []
        tm = tlm = IDENTITY
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except pikepdf.PdfError as error:
            raise Unreadable("the content does not parse") from error
        for instruction in instructions:
            if isinstance(instruction, pikepdf.ContentStreamInlineImage):
                continue
            operands, operator = instruction.operands, str(instruction.operator)
            if operator == "q":
                stack.append(state.copy())
            elif operator == "Q":
                if stack:
                    state = stack.pop()
            elif operator == "cm":
                state.ctm = multiply(tuple(numbers(operands, 6)), state.ctm)
            elif operator == "BT":
                tm = tlm = IDENTITY
            elif operator == "ET":
                pass
            elif operator == "Tc":
                state.char_spacing = numbers(operands, 1)[0]
            elif operator == "Tw":
                state.word_spacing = numbers(operands, 1)[0]
            elif operator == "Tz":
                state.scale = numbers(operands, 1)[0] / 100
            elif operator == "TL":
                state.leading = numbers(operands, 1)[0]
            elif operator == "Tr":
                state.render_mode = int(numbers(operands, 1)[0])
            elif operator == "Ts":
                state.rise = numbers(operands, 1)[0]
            elif operator == "Tf":
                if len(operands) != 2 or not isinstance(operands[0], pikepdf.Name):
                    raise Unreadable("Tf without a name and a size")
                state.font = self.font(resources, operands[0])
                state.size = float(operands[1])
            elif operator in ("Td", "TD"):
                tx, ty = numbers(operands, 2)
                if operator == "TD":
                    state.leading = -ty
                tlm = multiply((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                tm = tlm
            elif operator == "Tm":
                tm = tlm = tuple(numbers(operands, 6))
            elif operator == "T*":
                tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
                tm = tlm
            elif operator in ("Tj", "'", '"'):
                if operator == '"':
                    if len(operands) != 3:
                        raise Unreadable('" without three operands')
                    state.word_spacing, state.char_spacing = float(operands[0]), float(operands[1])
                    operands = operands[2:]
                if operator in ("'", '"'):
                    tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -state.leading), tlm)
                    tm = tlm
                if len(operands) != 1 or not isinstance(operands[0], pikepdf.String):
                    raise Unreadable(f"{operator} without a string")
                tm = self.show(bytes(operands[0]), state, tm)
            elif operator == "TJ":
                if len(operands) != 1 or not isinstance(operands[0], pikepdf.Array):
                    raise Unreadable("TJ without an array")
                for item in operands[0]:
                    if isinstance(item, pikepdf.String):
                        tm = self.show(bytes(item), state, tm)
                    else:
                        tx = -float(item) / 1000 * state.size * state.scale
                        tm = multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
            elif operator == "Do":
                self.do(operands, resources, state, depth, entered)

    def show(self, data: bytes, state: State, tm):
        font = state.font
        if font is None:
            raise Unreadable("text shown without a font")
        for code, length in font.codes(data):
            width = font.width(code)
            trm = multiply(
                multiply((state.size * state.scale, 0.0, 0.0, state.size, 0.0, state.rise), tm),
                state.ctm,
            )
            self.glyphs.append(
                RawGlyph(
                    code=code,
                    text=font.text(code),
                    trm=trm,
                    width=width,
                    ascent=font.ascent,
                    descent=font.descent,
                    render_mode=state.render_mode,
                )
            )
            spacing = state.char_spacing + (state.word_spacing if length == 1 and code == 32 else 0.0)
            tx = (width * state.size + spacing) * state.scale
            tm = multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
        return tm

    def do(self, operands, resources, state: State, depth: int, entered: tuple) -> None:
        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
            raise Unreadable("Do without a name")
        xobjects = resources.get("/XObject")
        if not isinstance(xobjects, pikepdf.Dictionary) or operands[0] not in xobjects:
            raise Unreadable(f"the XObject {operands[0]} is not in the resources")
        xobject = xobjects[operands[0]]
        if xobject.get("/Subtype") != "/Form":
            return
        key = xobject.objgen
        if key in entered:
            raise Unreadable("a form draws itself")
        if depth >= FORM_DEPTH:
            raise Unreadable("forms nested deeper than the prototype reads")
        matrix = tuple(float(v) for v in xobject.get("/Matrix", IDENTITY))
        inner = state.copy()
        inner.ctm = multiply(matrix, state.ctm)
        own = xobject.get("/Resources")
        self.run(xobject, own if isinstance(own, pikepdf.Dictionary) else resources, inner, depth + 1, entered + (key,))


def page_glyphs(pdf: pikepdf.Pdf, index: int) -> list[RawGlyph]:
    """Every code the page's content shows, in content order."""
    page = pdf.pages[index]
    reader = Reader()
    reader.run(page.obj, _inherited_resources(page.obj), State(), 0, ())
    return reader.glyphs


def frame(trm):
    """The direction in degrees, the size across the baseline, and the length
    of one text space unit along it, of a text rendering matrix."""
    a, b, c, d = trm[:4]
    length = math.hypot(a, b)
    area = abs(a * d - b * c)
    if length == 0 or area == 0:
        return None
    return math.degrees(math.atan2(b, a)) % 360.0, area / length, length


def _made(kind, **values):
    """An instance of a frozen fact class without its checks: for values the
    library computes itself (the probe measures what that saves)."""
    made = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(made, name, value)
    return made


def glyphs_of(raws: list[RawGlyph], page_index: int, rotation: int, checked: bool = True):
    """pdfacts glyphs of the interpreter's, under the conventions the probe
    measures: a box from the origin to the advance, across from the descent
    to the ascent, both scaled to make one em where they make less. Glyphs
    without extent are left out; the count is returned with them. With
    `checked` false, the facts are made without their checks."""
    import pdfacts

    if not checked:
        def point(x, y):
            return _made(pdfacts.Point, x=x, y=y)

        def quad(p1, p2, p3, p4):
            return _made(pdfacts.Quad, p1=p1, p2=p2, p3=p3, p4=p4)

        def glyph(**values):
            return _made(pdfacts.Glyph, **values)

        def ref(page_index, glyph_index):
            return _made(pdfacts.GlyphRef, page_index=page_index, glyph_index=glyph_index)
    else:
        point, quad, glyph, ref = pdfacts.Point, pdfacts.Quad, pdfacts.Glyph, pdfacts.GlyphRef

    unknown = pdfacts.Unknown(pdfacts.UnknownReason.UNREADABLE, "no ToUnicode entry and no encoding for the code")
    glyphs = []
    left_out = 0
    for raw in raws:
        made = frame(raw.trm)
        if made is None:
            left_out += 1
            continue
        direction, size, unit = made
        if direction >= 360.0:
            direction = 0.0
        ux, uy = math.cos(math.radians(direction)), math.sin(math.radians(direction))
        vx, vy = -uy, ux
        ox, oy = raw.trm[4], raw.trm[5]
        start, end = sorted((0.0, raw.width * unit))
        shown = (direction - rotation) % 360
        upright = min(shown, 360 - shown) <= 1.0
        if raw.ascent is None or raw.descent is None:
            box = pdfacts.Unknown(pdfacts.UnknownReason.UNREADABLE, "no ascent or descent")
        else:
            low, high = raw.descent * size, raw.ascent * size
            if high - low < size:
                scale = size / (high - low) if high > low else 1.0
                low, high = low * scale, high * scale

            def at(u, v):
                return point(ox + u * ux + v * vx, oy + u * uy + v * vy)

            box = quad(at(start, low), at(end, low), at(end, high), at(start, high))
        glyphs.append(
            glyph(
                ref=ref(page_index=page_index, glyph_index=len(glyphs)),
                text=raw.text if raw.text else unknown,
                origin=point(ox, oy),
                direction=direction,
                size=size,
                box=box,
                upright=upright,
                render_mode=raw.render_mode if 0 <= raw.render_mode <= 7 else 0,
                hyphen=False,
            )
        )
    return glyphs, left_out
