"""The text state of a content stream, and the codes it shows: ISO 32000-1,
8.4 (graphics state), 9.3 (text state) and 9.4 (text objects).

backends/qpdf.py reads the operators on QPDF's tokens and calls a TextMachine
with plain values; the machine keeps the state and makes a ContentGlyph for
each code a string shows. What it cannot read raises TextUnread with the
reason the page's text is unknown for.
"""

from __future__ import annotations

from pdfacts.backends.fonts import INVALID_TEXT, Font
from pdfacts.backends.protocols import ContentGlyph, Transform

IDENTITY: Transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class TextUnread(Exception):
    """The page's text cannot be read: `reason` is "not-read",
    "invalid-in-file" or "unreadable", as pdfacts.UnknownReason has them."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class TooMany(Exception):
    """More codes than the cap."""


def then(m: Transform, n: Transform) -> Transform:
    """The matrix that applies m first, then n (row vectors, as ISO 32000-1)."""
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


class _State:
    """The parts of the graphics state the text depends on."""

    __slots__ = ("ctm", "font", "size", "char_spacing", "word_spacing", "scale", "leading")
    __slots__ += ("render_mode", "rise")

    def __init__(self) -> None:
        self.ctm: Transform = IDENTITY
        self.font: Font | None = None
        self.size = 0.0
        self.char_spacing = 0.0
        self.word_spacing = 0.0
        self.scale = 1.0
        self.leading = 0.0
        self.render_mode = 0
        self.rise = 0.0

    def copy(self) -> _State:
        other = _State.__new__(_State)
        for name in _State.__slots__:
            setattr(other, name, getattr(self, name))
        return other


class TextMachine:
    """The text state of one page's content, and the glyphs it shows."""

    def __init__(self, cap: int | None) -> None:
        self.glyphs: list[ContentGlyph] = []
        self._cap = cap
        self._state = _State()
        self._saved: list[_State] = []
        # For each form being drawn, the number of states saved when it began:
        # its own Q cannot restore what was saved before it.
        self._floors: list[int] = []
        self._matrix: Transform = IDENTITY  # Tm
        self._line: Transform = IDENTITY  # Tlm

    # Graphics state: q, Q, cm.

    def save(self) -> None:
        self._saved.append(self._state.copy())

    def restore(self) -> None:
        # A Q without its q, in the page or in a form, is passed over.
        if len(self._saved) > (self._floors[-1] if self._floors else 0):
            self._state = self._saved.pop()

    def concat(self, matrix: Transform) -> None:
        self._state.ctm = then(matrix, self._state.ctm)

    def begin_form(self, matrix: Transform) -> None:
        """Do: the state saved, then the form's /Matrix (8.10.1)."""
        self.save()
        self.concat(matrix)
        self._floors.append(len(self._saved))

    def end_form(self) -> None:
        """The state saved when the form began, whatever the form left."""
        floor = self._floors.pop()
        while len(self._saved) >= floor:
            self._state = self._saved.pop()

    # Text objects and the text state.

    def begin(self) -> None:
        self._matrix = self._line = IDENTITY

    def char_spacing(self, value: float) -> None:
        self._state.char_spacing = value

    def word_spacing(self, value: float) -> None:
        self._state.word_spacing = value

    def horizontal_scaling(self, percent: float) -> None:
        self._state.scale = percent / 100

    def leading(self, value: float) -> None:
        self._state.leading = value

    def render_mode(self, mode: int) -> None:
        self._state.render_mode = mode

    def rise(self, value: float) -> None:
        self._state.rise = value

    def font(self, font: Font, size: float) -> None:
        self._state.font = font
        self._state.size = size

    def move(self, tx: float, ty: float) -> None:
        """Td: the start of the next line, offset from the start of this one."""
        self._line = then((1.0, 0.0, 0.0, 1.0, tx, ty), self._line)
        self._matrix = self._line

    def set_matrix(self, matrix: Transform) -> None:
        """Tm."""
        self._matrix = self._line = matrix

    def next_line(self) -> None:
        """T*: as Td 0 -leading."""
        self.move(0.0, -self._state.leading)

    # Showing text.

    def show(self, data: bytes) -> None:
        """Tj: each code of `data`, advanced by its width and the spacing."""
        state = self._state
        font = state.font
        if font is None:
            raise TextUnread("invalid-in-file", "the content shows text before Tf sets a font")
        size, scale = state.size, state.scale
        for code in font.codes(data):
            width = font.width(code)
            if width is None:
                raise TextUnread(
                    "not-read", "pdfacts does not read the width of a code its metrics lack"
                )
            text = font.text(code)
            invalid = text is INVALID_TEXT
            rendering = then(
                then((size * scale, 0.0, 0.0, size, 0.0, state.rise), self._matrix), state.ctm
            )
            self.glyphs.append(
                ContentGlyph(
                    text=None if invalid else text,  # type: ignore[arg-type]
                    text_invalid=invalid,
                    matrix=rendering,
                    width=width,
                    ascent=font.ascent,
                    descent=font.descent,
                    heights_invalid=font.heights_invalid,
                    render_mode=state.render_mode,
                )
            )
            if self._cap is not None and len(self.glyphs) > self._cap:
                raise TooMany()
            spacing = state.char_spacing
            if code == 32 and not font.two_bytes:
                spacing += state.word_spacing
            self._advance((width * size + spacing) * scale)

    def adjust(self, amount: float) -> None:
        """A number of TJ: thousandths of a unit of text space, against the
        writing direction."""
        self._advance(-amount / 1000 * self._state.size * self._state.scale)

    def _advance(self, tx: float) -> None:
        self._matrix = then((1.0, 0.0, 0.0, 1.0, tx, 0.0), self._matrix)
