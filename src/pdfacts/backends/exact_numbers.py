"""Finite QPDF source-number ledger candidate. No native/path authority by itself."""

import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
from math import isfinite

from pdfacts.backends import exact_matrix_witness as matrix_witness
from pdfacts.backends.exact_decimal import parse_pdf_number
from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit
from pdfacts.backends.exact_text import TEXT_COUNTS, ExactTextState
from pdfacts.backends.finite_content import ContentParser
from pdfacts.backends.protocols import DrawnImage, DrawnPath
from pdfacts.backends.source_runtime import require_source_runtime

# Own this format at module load; hot conversions never populate global caches.
_BINARY32 = struct.Struct("f")


@dataclass(frozen=True)
class SourcePathNumbers:
    stream_identity: tuple
    instruction: int
    ordinal: int
    matrix: tuple
    width: Fraction
    operations: tuple
    paint: str
    line_cap: int = 0
    line_join: int = 0
    miter_limit: Fraction = Fraction(10)
    _matrix_plan: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class SourceImageNumbers:
    instruction: int
    resource_identity: tuple
    matrix: tuple
    clip: tuple | None
    pixel_width: int
    pixel_height: int
    colour_space: int
    bits_per_pixel: int
    _matrix_plan: object = field(default=None, repr=False, compare=False)


IDENTITY = matrix_witness.EXACT_IDENTITY
# Finite path grammar with explicit text emission accounting.
# Named marking, images and isolated rectangular clips use closed schemas.
NUMERIC_NEUTRAL = {"g": 1, "G": 1, "rg": 3, "RG": 3, "k": 4, "K": 4}
STROKE_STATE_COUNTS = {"J": 1, "j": 1, "M": 1}
DEFAULT_MITER_LIMIT = Fraction(10)
BARE = {"q", "Q", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n", "h", "W"}
COUNTS = {"cm": 6, "w": 1, "m": 2, "l": 2, "re": 4, **NUMERIC_NEUTRAL, **STROKE_STATE_COUNTS}
RECTANGLE_OPERATION_SHAPES = (("re",), ("re", "h"))
RECTANGLE_PAINT_MODES = {
    "S": (0, True),
    "f": (2, False),
    "F": (2, False),
    "f*": (1, False),
    "B": (2, True),
    "B*": (1, True),
}


class _ExactNumberCache:
    def __init__(self, bank):
        bank.reserve(32768 + 128)
        self.bank = bank
        self._entries = OrderedDict()

    def number(self, token, limits):
        cached = self._entries.get(token)
        if cached is not None:
            self._entries.move_to_end(token)
            return cached
        exact = parse_pdf_number(token, self.bank, limits=limits)
        self.bank.reserve(256)
        if len(self._entries) == 256:
            self.bank.reserve(128)
            self._entries.popitem(last=False)
        self._entries[token] = exact
        return exact


def source_ledger(
    parts, bank, resources=None, with_emissions=False, *, limits, _numeric_cache=None
):
    # parts: the entire original Contents array in source order as
    # ((stream objgen, decoded bytes), ...). The digest of every member is
    # recomputed here so the published identity cannot outrun the bytes.
    require_source_runtime()
    if type(parts) is not tuple or len(parts) == 0:
        raise UnsupportedExactSource("ledger_input_domain")
    check_source_limit("source_streams", len(parts), limits.max_streams)
    bank.charge(4 * len(parts))
    total = 0
    for item in parts:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not tuple
            or len(item[0]) != 2
            or type(item[1]) is not bytes
        ):
            raise UnsupportedExactSource("ledger_input_domain")
        for component in item[0]:
            if type(component) is not int or not 0 <= component <= 2**63 - 1:
                raise UnsupportedExactSource("ledger_input_domain")
        total += len(item[1])
        check_source_limit("content_bytes", total, limits.max_content_bytes)
    # Bound containers and source identity processing before materialization.
    bank.reserve(1024 + 512 * len(parts))
    bank.charge(4 * len(parts))
    seen = set()
    for objgen, _ in parts:
        if objgen in seen:
            raise UnsupportedExactSource("duplicate_stream_identity")
        seen.add(objgen)
    bank.reserve(1024 + total + 256 * len(parts))
    bank.charge(2 * total)
    identity = tuple((objgen, sha256(data).hexdigest()) for objgen, data in parts)
    raw = b"".join(item[1] for item in parts)
    # Source-state allocations survive removal of the scratch QPDF parser.
    # Prepay records/emissions, local class/functions, instance dictionaries,
    # operand/state/path lists and the ExactTextState before creating them.
    bank.reserve(8192)
    records = []
    emissions = []
    if _numeric_cache is None:
        numeric_cache = _ExactNumberCache(bank)
    else:
        if type(_numeric_cache) is not _ExactNumberCache or _numeric_cache.bank is not bank:
            raise UnsupportedExactSource("numeric_cache_domain")
        numeric_cache = _numeric_cache

    class Parser:
        def __init__(self):
            self.args = []
            self.text_operand = None
            self.saved = []
            self.matrix = IDENTITY
            self.width = Fraction(1)
            self.path = []
            self.index = 0
            self.text = ExactTextState(resources, bank, limits)
            self.marked_depth = 0
            self.last_operation = None
            self.line_cap = 0
            self.line_join = 0
            self.miter_limit = DEFAULT_MITER_LIMIT
            self.clip = None
            self.pending_clip = None
            bank.reserve(256)
            self.numeric_tokens = [None] * 6
            self.matrix_plan = matrix_witness.IDENTITY_PLAN
            self.path_plan = None

        def bounded(self, x):
            if x.numerator.bit_length() > 4096 or x.denominator.bit_length() > 4096:
                raise UnsupportedExactSource("rational_bits")
            return x

        def op(self, a, b, kind):
            bank.charge(8)
            if kind == "mul":
                if a == 1:
                    return b
                if b == 1:
                    return a
                if not a or not b:
                    return IDENTITY[1]
            else:
                if not a:
                    return b
                if not b:
                    return a
            bank.charge(4)
            previous = self.last_operation
            if (
                previous is not None
                and a == previous[0]
                and b == previous[1]
                and kind == previous[2]
            ):
                return previous[3]
            bank.reserve(4096)
            result = self.bounded(a * b if kind == "mul" else a + b)
            bank.reserve(256)
            self.last_operation = (a, b, kind, result)
            return result

        def rectangle_clip(self):
            # Numeric comparisons of bounded Fractions can allocate cross-products.
            bank.reserve(32768)
            bank.charge(64)
            if len(self.path) != 1 or self.path[0][0] != "re":
                raise UnsupportedExactSource("clip_path_domain")
            operation, coordinates, matrix = self.path[0]
            if matrix != self.matrix:
                raise UnsupportedExactSource("clip_matrix_drift")
            x, y, width, height = coordinates
            if not width or not height:
                raise UnsupportedExactSource("clip_rectangle_degenerate")
            right = self.op(x, width, "add")
            top = self.op(y, height, "add")
            left_bound = self.op(self.op(matrix[0], x, "mul"), matrix[4], "add")
            right_bound = self.op(self.op(matrix[0], right, "mul"), matrix[4], "add")
            bottom_bound = self.op(self.op(matrix[3], y, "mul"), matrix[5], "add")
            top_bound = self.op(self.op(matrix[3], top, "mul"), matrix[5], "add")
            return (
                min(left_bound, right_bound),
                min(bottom_bound, top_bound),
                max(left_bound, right_bound),
                max(bottom_bound, top_bound),
            )

        def commit_clip(self):
            bank.reserve(32768)
            bank.charge(64)
            left, bottom, right, top = self.pending_clip
            if self.clip is not None:
                left = max(left, self.clip[0])
                bottom = max(bottom, self.clip[1])
                right = min(right, self.clip[2])
                top = min(top, self.clip[3])
            if left >= right or bottom >= top:
                raise UnsupportedExactSource("clip_empty")
            self.clip = (left, bottom, right, top)
            self.pending_clip = None

        def handle_token(self, kind, value, offset, length):
            bank.charge()
            if (
                type(offset) is not int
                or type(length) is not int
                or offset < 0
                or length <= 0
                or offset + length > len(raw)
            ):
                raise UnsupportedExactSource("source_token_span")
            if kind == "number":
                if len(self.args) >= 6 or length > limits.max_token_bytes:
                    raise UnsupportedExactSource("numeric_operand_domain")
                bank.charge(3 * length + 4)
                if (
                    type(value) is not bytes
                    or len(value) != length
                    or not raw.startswith(value, offset, offset + length)
                ):
                    raise UnsupportedExactSource("source_token_disagreement")
                token = value
                exact = numeric_cache.number(token, limits)
                bank.charge()
                self.numeric_tokens[len(self.args)] = token
                self.args.append(exact)
                return
            if kind != "operator":
                if len(self.args) >= 6 or kind not in ("name", "string", "array"):
                    raise UnsupportedExactSource("non_numeric_operand")
                if kind in ("string", "array"):
                    bank.reserve(128)
                    self.text_operand = (value, length)
                bank.reserve(64)
                self.args.append(value)
                return
            operator = value
            self.index += 1
            check_source_limit("source_instructions", self.index, limits.max_instructions)
            if self.pending_clip is not None and operator != "n":
                raise UnsupportedExactSource("clip_pending")
            if operator in TEXT_COUNTS:
                if self.clip is not None and operator in ("Tj", "TJ"):
                    raise UnsupportedExactSource("clipped_text")
                emitted = self.text.apply(operator, self.args, self.text_operand)
                if emitted is not None:
                    bank.reserve(128)
                    emissions.append((self.index, emitted, None))
                bank.reserve(64)
                self.args = []
                self.text_operand = None
                return
            if self.text.active:
                raise UnsupportedExactSource("graphics_inside_text")
            if operator == "Do":
                bank.charge(16)
                if not with_emissions:
                    raise UnsupportedExactSource("image_emission_required")
                if len(self.args) != 1 or self.path:
                    raise UnsupportedExactSource("image_operator_shape")
                resource, width, height, colour, bits = resources.image_metadata(self.args[0], bank)
                bank.reserve(4096)
                bank.reserve(256)
                record = SourceImageNumbers(
                    self.index,
                    resource,
                    self.matrix,
                    self.clip,
                    width,
                    height,
                    colour,
                    bits,
                    self.matrix_plan,
                )
                bank.reserve(128)
                emissions.append((self.index, 3, record))
                bank.reserve(64)
                self.args = []
                self.text_operand = None
                return
            if operator in ("BDC", "EMC", "gs"):
                if operator == "BDC":
                    if len(self.args) != 2:
                        raise UnsupportedExactSource("marked_operator_shape")
                    if self.marked_depth >= limits.max_marked_depth:
                        check_source_limit(
                            "marked_depth", self.marked_depth + 1, limits.max_marked_depth
                        )
                    resources.marked_property(self.args[0], self.args[1], bank)
                    self.marked_depth += 1
                elif operator == "EMC":
                    if self.args:
                        raise UnsupportedExactSource("marked_operator_shape")
                    if not self.marked_depth:
                        raise UnsupportedExactSource("marked_underflow")
                    self.marked_depth -= 1
                else:
                    if len(self.args) != 1:
                        raise UnsupportedExactSource("graphics_state_shape")
                    resources.graphics_state(self.args[0], bank)
                bank.reserve(64)
                self.args = []
                self.text_operand = None
                return
            if operator not in BARE and operator not in COUNTS:
                raise UnsupportedExactSource("operator_domain")
            if len(self.args) != COUNTS.get(operator, 0):
                raise UnsupportedExactSource("operator_shape")
            if not all(isinstance(value, Fraction) for value in self.args):
                raise UnsupportedExactSource("geometry_numeric_operand")
            a = self.args
            if operator == "q":
                if len(self.saved) >= limits.max_graphics_depth:
                    check_source_limit(
                        "graphics_depth", len(self.saved) + 1, limits.max_graphics_depth
                    )
                bank.reserve(192)
                bank.reserve(64)  # Additional saved clip reference and bookkeeping.
                bank.reserve(64)
                self.saved.append(
                    (
                        self.matrix,
                        self.width,
                        self.line_cap,
                        self.line_join,
                        self.miter_limit,
                        self.text.snapshot(),
                        self.clip,
                        self.matrix_plan,
                    )
                )
            elif operator == "Q":
                if not self.saved:
                    raise UnsupportedExactSource("state_underflow")
                (
                    self.matrix,
                    self.width,
                    self.line_cap,
                    self.line_join,
                    self.miter_limit,
                    text_state,
                    self.clip,
                    self.matrix_plan,
                ) = self.saved.pop()
                self.text.restore(text_state)
            elif operator == "W":
                self.pending_clip = self.rectangle_clip()
            elif operator == "w":
                if a[0] <= 0:
                    raise UnsupportedExactSource("nonpositive_width")
                self.width = a[0]
            elif operator in ("J", "j"):
                if a[0].denominator != 1 or not 0 <= a[0] <= 2:
                    raise UnsupportedExactSource("stroke_state_domain")
                if operator == "J":
                    self.line_cap = int(a[0])
                else:
                    self.line_join = int(a[0])
            elif operator == "M":
                if a[0] < 1:
                    raise UnsupportedExactSource("stroke_state_domain")
                self.miter_limit = a[0]
            elif operator == "cm":
                # Initial certificate domain: axis aligned compositions only.
                if a[1] or a[2] or not a[0] or not a[3]:
                    raise UnsupportedExactSource("matrix_domain")
                m = self.matrix
                bank.reserve(256)
                self.matrix = (
                    self.op(m[0], a[0], "mul"),
                    Fraction(0),
                    Fraction(0),
                    self.op(m[3], a[3], "mul"),
                    self.op(self.op(m[0], a[4], "mul"), m[4], "add"),
                    self.op(self.op(m[3], a[5], "mul"), m[5], "add"),
                )
                bank.reserve(384)
                bank.charge(16)
                depth = (
                    1
                    if self.matrix_plan is matrix_witness.IDENTITY_PLAN
                    else self.matrix_plan[4] + 1
                )
                self.matrix_plan = (
                    self.matrix_plan,
                    self.matrix,
                    tuple(a),
                    tuple(self.numeric_tokens),
                    depth,
                )
            elif operator in ("m", "l", "re", "h"):
                if operator == "l" and not self.path:
                    raise UnsupportedExactSource("path_without_move")
                if len(self.path) >= limits.max_path_segments:
                    check_source_limit(
                        "path_segments", len(self.path) + 1, limits.max_path_segments
                    )
                if not self.path:
                    self.path_plan = self.matrix_plan
                elif self.path_plan is not self.matrix_plan:
                    self.path_plan = matrix_witness.INCOMPATIBLE_PATH_PLAN
                bank.reserve(512)
                self.path.append((operator, tuple(a), self.matrix))
            elif operator in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
                if self.clip is not None:
                    raise UnsupportedExactSource("clipped_path")
                if not self.path:
                    raise UnsupportedExactSource("paint_path_domain")
                check_source_limit("paint_paths", len(records) + 1, limits.max_paths)
                if not any(o[0] in ("l", "re") for o in self.path):
                    raise UnsupportedExactSource("degenerate_path")
                if any(o[2] != self.matrix for o in self.path):
                    raise UnsupportedExactSource("path_paint_ctm_drift")
                bank.reserve(576 + len(self.path) * 8)
                bank.reserve(256)
                records.append(
                    SourcePathNumbers(
                        identity,
                        self.index,
                        len(records),
                        self.matrix,
                        self.width,
                        tuple(self.path),
                        operator,
                        self.line_cap,
                        self.line_join,
                        self.miter_limit,
                        self.matrix_plan if self.path_plan is self.matrix_plan else None,
                    )
                )
                self.path = []
                self.path_plan = None
                bank.reserve(128)
                emissions.append((self.index, 2, len(records) - 1))
            elif operator == "n":
                if self.pending_clip is not None:
                    self.commit_clip()
                self.path = []
                self.path_plan = None
            bank.reserve(64)
            self.args = []
            self.text_operand = None

        def handle_eof(self):
            if (
                self.args
                or self.saved
                or self.path
                or self.text.active
                or self.marked_depth
                or self.pending_clip is not None
                or self.clip is not None
            ):
                raise UnsupportedExactSource("incomplete_state")

    parser = Parser()
    ContentParser(raw, bank, limits).consume(parser.handle_token)
    parser.handle_eof()
    bank.reserve(64 + len(records) * 8)
    if with_emissions:
        bank.reserve(64 + len(emissions) * 8)
        return tuple(records), tuple(emissions)
    return tuple(records)


def _straight_subpath_segments(source, bank, held):
    # Association only. Closed retraces do not establish an ink-support bound.
    operations = source.operations
    if not operations:
        raise UnsupportedExactSource("native_path_domain")
    bank.charge(8 * len(operations))
    bank.reserve(512 + 512 * len(operations))
    expected = []
    index = 0
    while index < len(operations):
        if index + 1 >= len(operations):
            raise UnsupportedExactSource("native_path_domain")
        move, line = operations[index], operations[index + 1]
        if move[0] != "m" or line[0] != "l" or move[1] == line[1]:
            raise UnsupportedExactSource("native_path_domain")
        x, y = held(move[1][0]), held(move[1][1])
        end_x, end_y = held(line[1][0]), held(line[1][1])
        if x == end_x and y == end_y:
            raise UnsupportedExactSource("native_path_domain")
        expected.append((2, x, y, False))
        expected.append((0, end_x, end_y, False))
        index += 2
        closed = index < len(operations) and operations[index][0] == "h"
        if closed:
            index += 1
        if closed or (index == len(operations) and source.paint == "s"):
            expected.append((0, x, y, True))
    return tuple(expected)


def _compound_rectangle_segments(source, bank, held, limits):
    """Match ordered re/h subpaths without deriving any ink support."""
    operations = source.operations
    bank.reserve(512 + 128 * len(operations))
    bank.charge(8 * len(operations))
    index = rectangle_count = 0
    while index < len(operations):
        operation = operations[index]
        if (
            type(operation) is not tuple
            or len(operation) != 3
            or operation[0] != "re"
            or type(operation[1]) is not tuple
            or len(operation[1]) != 4
            or operation[2] != source.matrix
        ):
            raise UnsupportedExactSource("native_path_domain")
        rectangle_count += 1
        index += 1
        if index < len(operations) and operations[index][0] == "h":
            closing = operations[index]
            if len(closing) != 3 or closing[1] != () or closing[2] != source.matrix:
                raise UnsupportedExactSource("native_path_domain")
            index += 1
    segment_count = 5 * rectangle_count
    # Matches the native capture's existing per-path segment limit.
    if rectangle_count < 2:
        raise UnsupportedExactSource("native_path_domain")
    check_source_limit("native_path_segments", segment_count, limits.max_path_segments)
    bank.reserve(256 + 8 * segment_count)
    expected = [None] * segment_count
    index = target = 0
    while index < len(operations):
        bank.reserve(1024)
        bank.charge(16)
        x, y, width, height = operations[index][1]
        if (
            type(x) is not Fraction
            or type(y) is not Fraction
            or type(width) is not Fraction
            or type(height) is not Fraction
            or not width
            or not height
        ):
            raise UnsupportedExactSource("native_path_domain")
        try:
            # Preserve the single-re witness: round each operand first, then
            # round the two binary32 endpoint additions. Do not normalize signs.
            left, bottom = held(x), held(y)
            native_width, native_height = held(width), held(height)
            if not native_width or not native_height:
                raise UnsupportedExactSource("native_rectangle_degenerate")
            bank.charge(2)
            bank.reserve(128)
            right, top = held(left + native_width), held(bottom + native_height)
        except (OverflowError, ValueError):
            raise UnsupportedExactSource("native_rectangle_degenerate") from None
        if right == left or top == bottom:
            raise UnsupportedExactSource("native_rectangle_degenerate")
        expected[target] = (2, left, bottom, False)
        expected[target + 1] = (0, right, bottom, False)
        expected[target + 2] = (0, right, top, False)
        expected[target + 3] = (0, left, top, False)
        expected[target + 4] = (0, left, bottom, True)
        target += 5
        index += 1
        if index < len(operations) and operations[index][0] == "h":
            index += 1
    return expected


def reconcile_root_paths(ledger, drawing, bank, emissions=None, *, limits):
    """Strict complete ordinal join. No coordinate lookup and no partial results.

    Complete source text/path emissions must match the full native kind order.
    Text is accounted for without issuing text identity or visibility claims.
    Supported Image events remain in the full order; Forms and unknown kinds refuse.
    """
    require_source_runtime()
    inventory = getattr(drawing, "root_object_types", None)
    if inventory is None or drawing.stopped or drawing.too_deep:
        raise UnsupportedExactSource("native_inventory_incomplete")
    if emissions is None:
        bank.reserve(128 + 8 * len(ledger))
        expected_kinds = (2,) * len(ledger)
        ordered_sources = ledger
    else:
        # Fixed-size storage avoids cumulative append growth. The reservation
        # includes traversal ordinals and the final compact tuple/slice.
        bank.reserve(1024 + 160 * len(emissions) + 16 * len(drawing.objects))
        bank.charge(8 * len(emissions))
        expected = [None] * len(emissions)
        ordered_sources = [None] * len(drawing.objects)
        kind_count = drawing_count = path_ordinal = last_instruction = 0
        for event in emissions:
            if type(event) is not tuple or len(event) != 3:
                raise UnsupportedExactSource("source_emission_order")
            instruction, kind, value = event
            if (
                type(instruction) is not int
                or instruction <= last_instruction
                or type(kind) is not int
                or kind not in (0, 1, 2, 3)
            ):
                raise UnsupportedExactSource("source_emission_order")
            last_instruction = instruction
            source = None
            if kind == 2:
                if (
                    type(value) is not int
                    or value != path_ordinal
                    or value >= len(ledger)
                    or ledger[value].instruction != instruction
                ):
                    raise UnsupportedExactSource("source_path_emission")
                source = ledger[value]
                path_ordinal += 1
            elif kind == 3:
                if (
                    type(value) is not SourceImageNumbers
                    or type(value.instruction) is not int
                    or value.instruction != instruction
                ):
                    raise UnsupportedExactSource("source_image_emission")
                source = value
            elif value is not None:
                raise UnsupportedExactSource("source_text_emission")
            if kind:
                expected[kind_count] = kind
                kind_count += 1
            if source is not None:
                if drawing_count >= len(ordered_sources):
                    raise UnsupportedExactSource("native_inventory_count")
                ordered_sources[drawing_count] = source
                drawing_count += 1
        if path_ordinal != len(ledger):
            raise UnsupportedExactSource("source_path_emission")
        if drawing_count != len(ordered_sources):
            raise UnsupportedExactSource("native_inventory_count")
        expected_kinds = tuple(expected[:kind_count])
    if tuple(inventory) != expected_kinds:
        raise UnsupportedExactSource("native_inventory_kind_or_order")
    if len(drawing.objects) != len(ordered_sources):
        raise UnsupportedExactSource("native_inventory_count")
    bank.reserve(64 + len(ordered_sources) * 8)
    joined = []
    bank.reserve(512)
    witness_memo = None

    def witness_matches(source, native_matrix):
        nonlocal witness_memo
        if witness_memo is None:
            bank.reserve(1024)
            witness_memo = [None, None, None, None, 0]
        return matrix_witness.matches(
            source._matrix_plan,
            source.matrix,
            native_matrix,
            witness_memo,
            bank,
        )

    def held(value):
        # Narrow exact numerics need no wide-formula temporaries. Prepay
        # their common objects before any public getter or bit-length read.
        value_type = type(value)
        if value_type is Fraction or value_type is int or value_type is float:
            bank.reserve(160)
        else:
            bank.reserve(512)
        bank.charge(9)
        if value_type is Fraction:
            numerator_bits = value.numerator.bit_length()
            denominator_bits = value.denominator.bit_length()
        elif value_type is int:
            numerator_bits = value.bit_length()
            denominator_bits = 1
        else:
            numerator_bits = denominator_bits = 0
        if numerator_bits > 53 or denominator_bits > 53:
            bank.reserve(352)  # Restore the original fixed512 before formula work.
            # Pinned CPython long_true_divide uses at most four temporary
            # PyLongs. bit_length handles negative values without abs copies.
            bank.charge(8)
            limbs = (numerator_bits + denominator_bits + 1076 + 29) // 30 + 2
            bank.reserve(4 * (24 + 4 * limbs))
        return _BINARY32.unpack(_BINARY32.pack(float(value)))[0]

    path_ordinal = 0
    for ordinal, (source, native) in enumerate(zip(ordered_sources, drawing.objects, strict=True)):
        bank.charge(8)
        if type(source) is SourceImageNumbers:
            bank.reserve(1024)
            bank.charge(32)
            if type(native) is not DrawnImage:
                raise UnsupportedExactSource("native_image_kind")
            if (
                type(source.pixel_width) is not int
                or type(source.pixel_height) is not int
                or not 0 < source.pixel_width <= 2147483647
                or not 0 < source.pixel_height <= 2147483647
                or type(source.colour_space) is not int
                or type(source.bits_per_pixel) is not int
                or (source.colour_space, source.bits_per_pixel) not in ((1, 8), (2, 24), (10, 8))
            ):
                raise UnsupportedExactSource("source_image_metadata")
            if (
                type(native.width) is not int
                or type(native.height) is not int
                or type(native.colour_space) is not int
                or type(native.bits_per_pixel) is not int
                or native.width != source.pixel_width
                or native.height != source.pixel_height
                or native.colour_space != source.colour_space
                or native.bits_per_pixel != source.bits_per_pixel
            ):
                raise UnsupportedExactSource("native_image_metadata_disagreement")
            identity = source.resource_identity
            if (
                type(identity) is not tuple
                or len(identity) != 2
                or type(identity[0]) is not int
                or identity[0] <= 0
                or type(identity[1]) is not int
                or identity[1] < 0
            ):
                raise UnsupportedExactSource("source_image_identity")
            if (
                type(source.matrix) is not tuple
                or len(source.matrix) != 6
                or type(native.matrix) is not tuple
                or len(native.matrix) != 6
            ):
                raise UnsupportedExactSource("native_matrix_shape")
            # Six bounded rational conversions plus binary32 wrappers. Refuse
            # overflow with a fixed code, without exposing original operands.
            bank.reserve(32768)
            bank.charge(64)
            old_matrix_matches = True
            for exact, current in zip(source.matrix, native.matrix, strict=True):
                if (
                    type(exact) is not Fraction
                    or exact.numerator.bit_length() > 4096
                    or exact.denominator.bit_length() > 4096
                    or type(current) is not float
                    or not isfinite(current)
                ):
                    raise UnsupportedExactSource("native_matrix_disagreement")
                try:
                    converted = held(exact)
                except (OverflowError, ValueError, struct.error):
                    raise UnsupportedExactSource("native_matrix_disagreement") from None
                if not isfinite(converted) or converted != current:
                    old_matrix_matches = False
                    break
            if not old_matrix_matches and not witness_matches(source, native.matrix):
                raise UnsupportedExactSource("native_matrix_disagreement")
            if (
                source.matrix[1]
                or source.matrix[2]
                or not source.matrix[0]
                or not source.matrix[3]
                or native.matrix[0] == 0
                or native.matrix[3] == 0
            ):
                raise UnsupportedExactSource("source_image_matrix")
            if source.clip is not None:
                bank.reserve(16384)
                bank.charge(32)
                if type(source.clip) is not tuple or len(source.clip) != 4:
                    raise UnsupportedExactSource("source_image_clip")
                for coordinate in source.clip:
                    if (
                        type(coordinate) is not Fraction
                        or coordinate.numerator.bit_length() > 4096
                        or coordinate.denominator.bit_length() > 4096
                    ):
                        raise UnsupportedExactSource("source_image_clip")
                if source.clip[0] >= source.clip[2] or source.clip[1] >= source.clip[3]:
                    raise UnsupportedExactSource("source_image_clip")
            bank.reserve(128)
            joined.append((ordinal, source, native))
            continue
        if (
            type(source) is not SourcePathNumbers
            or type(native) is not DrawnPath
            or source.ordinal != path_ordinal
            or native.source_level != 0
        ):
            raise UnsupportedExactSource("native_inventory_kind_or_order")
        path_ordinal += 1
        bank.charge(len(source.operations))
        bank.reserve(64 + len(source.operations) * 8)
        operations = tuple(operation[0] for operation in source.operations)
        rectangle = operations in RECTANGLE_OPERATION_SHAPES
        explicit_segments = None
        if not rectangle and operations and operations[0] == "re":
            mode = RECTANGLE_PAINT_MODES.get(source.paint)
            if mode is None:
                raise UnsupportedExactSource("native_path_domain")
            bank.reserve(512)
            explicit_segments = _compound_rectangle_segments(source, bank, held, limits)
        elif not rectangle and (operations != ("m", "l") or source.paint == "s"):
            if source.paint not in ("S", "s"):
                raise UnsupportedExactSource("native_path_domain")
            explicit_segments = _straight_subpath_segments(source, bank, held)
            mode = (0, True)
        else:
            mode = RECTANGLE_PAINT_MODES.get(source.paint)
            if mode is None or (operations == ("m", "l") and source.paint != "S"):
                raise UnsupportedExactSource("native_path_domain")
        expected_fill, expected_stroke = mode
        segment_count = (
            len(explicit_segments) if explicit_segments is not None else (5 if rectangle else 2)
        )
        if (
            native.fill_mode != expected_fill
            or native.stroke is not expected_stroke
            or native.segments is None
            or len(native.segments) != segment_count
        ):
            raise UnsupportedExactSource("native_paint_shape")
        if expected_stroke:
            bank.charge(12)
            if (
                type(source.line_cap) is not int
                or type(source.line_join) is not int
                or type(native.cap) is not int
                or type(native.join) is not int
                or not 0 <= source.line_cap <= 2
                or not 0 <= source.line_join <= 2
                or not 0 <= native.cap <= 2
                or not 0 <= native.join <= 2
                or source.line_cap != native.cap
                or source.line_join != native.join
            ):
                raise UnsupportedExactSource("native_stroke_state_disagreement")
        if native.matrix is None or len(native.matrix) != 6:
            raise UnsupportedExactSource("native_matrix_shape")
        old_matrix_matches = True
        for exact, current in zip(source.matrix, native.matrix, strict=True):
            if held(exact) != current:
                old_matrix_matches = False
                break
        if not old_matrix_matches and not witness_matches(source, native.matrix):
            raise UnsupportedExactSource("native_matrix_disagreement")
        if held(source.width) != native.width:
            raise UnsupportedExactSource("native_width_disagreement")
        bank.reserve(64 + segment_count * 128)
        if explicit_segments is not None:
            expected = explicit_segments
        elif rectangle:
            x, y, width, height = source.operations[0][1]
            if not width or not height:
                raise UnsupportedExactSource("native_path_domain")
            left, bottom = held(x), held(y)
            native_width, native_height = held(width), held(height)
            if not native_width or not native_height:
                raise UnsupportedExactSource("native_rectangle_degenerate")
            # PDFium's rectangle path adds the already-converted binary32
            # operands. Exact source operands remain unchanged in the ledger.
            bank.charge(2)
            bank.reserve(128)
            right, top = held(left + native_width), held(bottom + native_height)
            if right == left or top == bottom:
                raise UnsupportedExactSource("native_rectangle_degenerate")
            expected = (
                (2, left, bottom, False),
                (0, right, bottom, False),
                (0, right, top, False),
                (0, left, top, False),
                (0, left, bottom, True),
            )
        else:
            first, second = source.operations
            expected = (
                (2, held(first[1][0]), held(first[1][1]), False),
                (0, held(second[1][0]), held(second[1][1]), False),
            )
        for wanted, segment in zip(expected, native.segments, strict=True):
            bank.charge(4)
            if segment[0] != wanted[0] or segment[3] is not wanted[3]:
                raise UnsupportedExactSource("native_segment_shape")
            if wanted[1] != segment[1] or wanted[2] != segment[2]:
                raise UnsupportedExactSource("native_segment_disagreement")
        # Association is ordinal from full inventory; coordinates only reject it.
        bank.reserve(128)
        joined.append((ordinal, source, native))
    bank.reserve(64 + len(joined) * 8)
    return tuple(joined)
