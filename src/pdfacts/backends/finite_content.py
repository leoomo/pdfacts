"""Isolated finite parser prototype. No application certificate by itself."""

from dataclasses import dataclass
from reprlib import recursive_repr

from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit
from pdfacts.backends.qpdf import decode_exact_name
from pdfacts.backends.source_runtime import require_source_runtime

WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"
TERMINATORS = WHITESPACE + DELIMITERS
HEX = b"0123456789abcdefABCDEF"
ESCAPES = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
SHORT_STATE_OPERATORS = frozenset(("BT", "ET", "EMC"))

# Fixed lexical values only; no source-dependent data enters these tables.
_SINGLE_DIGIT_WORDS = (b"0", b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8", b"9")
_SINGLE_BYTE_OPERATORS = tuple(chr(value) for value in range(33, 127))

# Static lexical constants. The source-state parser still decides admissibility.
_COMMON_OPERATORS = {
    2: {
        66: ((b"BT", "BT"), (b"B*", "B*")),
        69: ((b"ET", "ET"),),
        84: ((b"Tf", "Tf"), (b"Tm", "Tm"), (b"Tw", "Tw"), (b"Tj", "Tj"), (b"TJ", "TJ")),
        82: ((b"RG", "RG"),),
        98: ((b"b*", "b*"),),
        99: ((b"cm", "cm"),),
        102: ((b"f*", "f*"),),
        103: ((b"gs", "gs"),),
        114: ((b"re", "re"), (b"rg", "rg")),
    },
    3: {66: ((b"BDC", "BDC"),), 69: ((b"EMC", "EMC"),)},
}


@dataclass(frozen=True, init=False)
class SourceToken:
    __slots__ = ("_payload",)
    _payload: tuple
    __match_args__ = ("kind", "value", "offset", "length")

    def __init__(self, kind, value, offset, length):
        object.__setattr__(self, "_payload", (kind, value, offset, length))

    @property
    def kind(self):
        return self._payload[0]

    @property
    def value(self):
        return self._payload[1]

    @property
    def offset(self):
        return self._payload[2]

    @property
    def length(self):
        return self._payload[3]

    def __getstate__(self):
        return list(self._payload)

    def __setstate__(self, state):
        object.__setattr__(self, "_payload", tuple(state))

    def __hash__(self):
        return hash(self._payload)

    @recursive_repr()
    def __repr__(self):
        return (
            f"{self.__class__.__qualname__}(kind={self.kind!r}, "
            f"value={self.value!r}, offset={self.offset!r}, length={self.length!r})"
        )


def numeric_word(raw):
    first = True
    seen_digit = False
    seen_point = False
    for byte in raw:
        if first:
            first = False
            if byte in (43, 45):
                continue
        if 48 <= byte <= 57:
            seen_digit = True
        elif byte == 46 and not seen_point:
            seen_point = True
        else:
            return False
    return seen_digit


class ContentParser:
    def __init__(
        self,
        raw,
        bank,
        limits,
        *,
        consumer_end_offsets=False,
        consumer_short_numbers=False,
        consumer_short_names=False,
        consumer_short_states=False,
    ):
        require_source_runtime()
        if type(raw) is not bytes:
            raise UnsupportedExactSource("content_size")
        if type(consumer_end_offsets) is not bool:
            raise UnsupportedExactSource("content_consumer_mode")
        if type(consumer_short_numbers) is not bool or (
            consumer_short_numbers and not consumer_end_offsets
        ):
            raise UnsupportedExactSource("content_consumer_mode")
        if type(consumer_short_names) is not bool or (
            consumer_short_names and not consumer_short_numbers
        ):
            raise UnsupportedExactSource("content_consumer_mode")
        if type(consumer_short_states) is not bool or (
            consumer_short_states and not consumer_short_numbers
        ):
            raise UnsupportedExactSource("content_consumer_mode")
        bank.reserve(128)
        raw_length = len(raw)
        check_source_limit("content_bytes", raw_length, limits.max_content_bytes)
        self.limits = limits
        bank.reserve(512 + 32768)
        bank.charge(3 * raw_length)
        self.raw = raw
        self.raw_length = raw_length
        self.bank = bank
        self.index = 0
        self.consumer_end_offsets = consumer_end_offsets
        self.consumer_short_numbers = consumer_short_numbers
        self.consumer_short_names = consumer_short_names
        self.consumer_short_states = consumer_short_states
        self.names = {}
        self.window = b""
        self.window_start = 0
        self.window_end = 0
        bank.reserve(768)
        bank.charge(64)
        self._scalar_cache = [None] * 64

    def _cached_scalar(self, start, end, length, first_byte):
        self.bank.charge(8)
        slot = (first_byte + 13 * length) & 63
        entry = self._scalar_cache[slot]
        if entry is None or len(entry[0]) != length or entry[0][0] != first_byte:
            return None
        self.bank.charge(length)
        if self.raw.startswith(entry[0], start, end):
            return entry
        return None

    def _remember_scalar(self, word, kind, value, length):
        if 2 <= length <= 8:
            self.bank.reserve(128)
            self.bank.charge(4)
            slot = (word[0] + 13 * length) & 63
            self._scalar_cache[slot] = (word, kind, value)

    def load_window(self, position):
        if self.window_start <= position < self.window_end:
            return
        self.bank.reserve(768)
        self.bank.charge(8)
        end = position + 256
        if end > self.raw_length:
            end = self.raw_length
        self.window = self.raw[position:end]
        self.window_start = position
        self.window_end = end

    def skip_ignorable(self):
        if self.index == self.raw_length:
            return
        byte = self.raw[self.index]
        if byte not in WHITESPACE and byte != 37:
            return
        # Compact absolute indices make window-offset arithmetic use cached
        # integers. Only the final absolute index allocates (at most 32 bytes).
        # Window loading retains its separate fee; larger inputs retain 256.
        self.bank.reserve(64 if self.raw_length < 1073741824 else 256)
        position = self.index
        comment = False
        while position < self.raw_length:
            self.load_window(position)
            window = self.window
            offset = position - self.window_start
            size = len(window)
            while offset < size:
                byte = window[offset]
                if comment:
                    if byte in (10, 13):
                        comment = False
                    else:
                        offset += 1
                        continue
                if byte in WHITESPACE:
                    offset += 1
                elif byte == 37:
                    comment = True
                    offset += 1
                else:
                    self.index = self.window_start + offset
                    return
            position = self.window_end
            self.index = position
        self.index = self.raw_length

    def scan_word(self, start, is_name):
        compact = self.raw_length < 1073741824
        # Terminal index and read()'s length each need at most 32 bytes.
        # Refusals restore the original allowance before constructing errors.
        self.bank.reserve(64 if compact else 256)
        self.load_window(start)
        offset = start - self.window_start
        offset += 1
        escape_remaining = 0
        escape_start = 0
        escape_offset = 0
        while True:
            window = self.window
            size = len(window)
            while offset < size:
                byte = window[offset]
                if escape_remaining:
                    if byte not in HEX:
                        if compact:
                            self.bank.reserve(192)
                        self.index = escape_start + escape_offset
                        raise UnsupportedExactSource("name_escape")
                    escape_remaining -= 1
                else:
                    if byte in TERMINATORS:
                        self.index = self.window_start + offset
                        return compact
                    if not 33 <= byte <= 126:
                        error_base = self.window_start
                        if compact:
                            self.bank.reserve(192)
                        self.index = error_base + offset
                        raise UnsupportedExactSource("bare_token_byte")
                    if is_name and byte == 35:
                        escape_start = self.window_start
                        escape_offset = offset
                        escape_remaining = 2
                offset += 1
            if self.window_end == self.raw_length:
                if escape_remaining:
                    if compact:
                        self.bank.reserve(192)
                    self.index = escape_start + escape_offset
                    raise UnsupportedExactSource("name_escape")
                self.index = self.raw_length
                return compact
            self.load_window(self.window_end)
            offset = 0

    def token(self, kind, value, start, consumer):
        if consumer is not None:
            if self.consumer_short_numbers:
                # Freeze the selected channel and span before budget callbacks.
                end = self.index
                raw_length = self.raw_length
                short_names = self.consumer_short_names
                short_states = self.consumer_short_states
                self.bank.reserve(64)
                discounted = (
                    raw_length < 1073741824
                    and (
                        kind == "number"
                        and type(value) is bytes
                        and 1 <= len(value) <= 8
                        or short_names
                        and kind == "name"
                        and type(value) is str
                        and 1 <= len(value) <= 8
                        or short_states
                        and kind == "operator"
                        and type(value) is str
                        and value in SHORT_STATE_OPERATORS
                    )
                    and type(start) is int
                    and type(end) is int
                    and 0 <= start < end <= raw_length
                    and 1 <= end - start <= 8
                )
                if not discounted:
                    self.bank.reserve(192)
                consumer(kind, value, start, end, discounted)
                return None
            self.bank.reserve(256)
            if self.consumer_end_offsets:
                consumer(kind, value, start, self.index)
            else:
                consumer(kind, value, start, self.index - start)
            return None
        self.bank.reserve(512)
        return SourceToken(kind, value, start, self.index - start)

    def consume(self, consumer):
        while True:
            self.skip_ignorable()
            if self.index == self.raw_length:
                return
            self.read(consumer=consumer)

    def read(self, depth=0, consumer=None):
        self.skip_ignorable()
        raw = self.raw
        if self.index == self.raw_length:
            return None
        start = self.index
        byte = raw[start]
        self.bank.charge()
        if byte == 91:
            return self.read_array(depth, consumer)
        if raw.startswith(b"<<", start):
            raise UnsupportedExactSource("non_numeric_operand")
        if byte in b")>]}{":
            raise UnsupportedExactSource("unexpected_delimiter")
        if byte in b"(<":
            if depth >= self.limits.max_token_depth:
                check_source_limit("token_depth", depth + 1, self.limits.max_token_depth)
            # The encoded span is bounded before constructing a decoded buffer.
            end = self.string_end(start, depth)
            value = self.decode_literal(start, end) if byte == 40 else self.decode_hex(start, end)
            self.index = end
            return self.token("string", value, start, consumer)
        is_name = byte == 47
        compact_word = self.scan_word(start, is_name)
        length = self.index - start
        allowed = self.limits.max_token_bytes
        if compact_word and length > allowed:
            self.bank.reserve(192)
        check_source_limit("token_bytes", length, allowed)
        if not is_name and not 33 <= raw[start] <= 126:
            if compact_word:
                self.bank.reserve(192)
            raise UnsupportedExactSource("bare_token_byte")
        if not is_name:
            self.bank.charge(2)
            by_initial = _COMMON_OPERATORS.get(length)
            candidates = by_initial.get(raw[start], ()) if by_initial is not None else ()
            if candidates:
                self.bank.reserve(64)  # One outer tuple iterator, including cold execution.
                self.bank.charge(len(candidates) * length)
                for candidate in candidates:
                    # Pair unpacking can allocate another iterator before specialization.
                    if raw.startswith(candidate[0], start, self.index):
                        return self.token("operator", candidate[1], start, consumer)
        if not is_name and length == 1:
            if 48 <= byte <= 57:
                return self.token("number", _SINGLE_DIGIT_WORDS[byte - 48], start, consumer)
            return self.token("operator", _SINGLE_BYTE_OPERATORS[byte - 33], start, consumer)
        if not is_name and 2 <= length <= 8:
            cached = self._cached_scalar(start, self.index, length, byte)
            if cached is not None:
                return self.token(cached[1], cached[2], start, consumer)
        short_copy = length <= 16
        if not short_copy:
            self.bank.reserve(256)  # Short copy-fee arithmetic uses cached integers.
        self.bank.reserve(192 + 4 * length)
        word = raw[start : self.index]
        if is_name:
            if word in self.names:
                return self.token("name", self.names[word], start, consumer)
            if length > 1 and b"#" not in word:
                # The completed scan proved an unescaped printable ASCII name.
                # Its bytes and Unicode copies share the scalar prepayment above.
                value = word.decode("ascii")
            else:
                self.bank.reserve(1024 + 128 * length)
                value = decode_exact_name(word, self.bank)
            if len(self.names) < 256:
                self.bank.reserve(256)
                self.names[word] = value
            return self.token("name", value, start, consumer)
        if word in (b"true", b"false", b"null"):
            if short_copy:
                self.bank.reserve(256)
            raise UnsupportedExactSource("non_numeric_operand")
        self.bank.reserve(256)  # The bytes iterator is separate from word copies.
        if numeric_word(word):
            if b"." not in word:
                # numeric_word proved one optional sign followed only by digits.
                magnitude = word.lstrip(b"+-0")
                limit = b"9223372036854775808" if word[0] == 45 else b"9223372036854775807"
                if len(magnitude) > 19 or len(magnitude) == 19 and magnitude > limit:
                    raise UnsupportedExactSource("non_numeric_operand")
            self._remember_scalar(word, "number", word, length)
            return self.token("number", word, start, consumer)
        value = word.decode("ascii")
        self._remember_scalar(word, "operator", value, length)
        return self.token("operator", value, start, consumer)

    def read_array(self, depth, consumer):
        """Parse nested arrays with a prepaid explicit stack, not Python recursion."""
        check_source_limit("token_depth", depth + 1, self.limits.max_token_depth)
        self.bank.reserve(256)
        capacity = min(self.limits.max_token_depth - depth, self.raw_length - self.index)
        # Two fixed arrays; per-step arithmetic is paid in the loop.
        self.bank.reserve(512 + 16 * capacity)
        starts = [None] * capacity
        children = [None] * capacity
        level = 0
        while True:
            self.skip_ignorable()
            if self.index == self.raw_length:
                raise UnsupportedExactSource("compound_unclosed")
            # Includes uncached depth/index arithmetic and a new child list.
            # Pay each step, since sibling arrays can outnumber stack depth.
            self.bank.reserve(256)
            self.bank.charge(4)
            byte = self.raw[self.index]
            if byte == 91:
                check_source_limit("token_depth", depth + level + 1, self.limits.max_token_depth)
                starts[level] = self.index
                children[level] = []
                self.index += 1
                level += 1
            elif byte == 93:
                self.index += 1
                level -= 1
                items = children[level]
                self.bank.reserve(64 + 8 * len(items))
                value = tuple(items)
                if level == 0:
                    return self.token("array", value, starts[level], consumer)
                child = self.token("array", value, starts[level], None)
                children[level] = None
                self.bank.reserve(256)
                children[level - 1].append(child)
            else:
                child = self.read(depth + level)
                self.bank.reserve(256)
                children[level - 1].append(child)

    def string_end(self, start, depth):
        self.bank.reserve(256)
        position = start + 1
        maximum_end = start + min(self.limits.max_token_bytes, self.raw_length - start + 1)
        literal = self.raw[start] == 40
        nesting = 1
        escaped = False
        while position < self.raw_length:
            self.load_window(position)
            self.bank.reserve(256)
            window = self.window
            offset = position - self.window_start
            size = len(window)
            maximum_offset = maximum_end - self.window_start
            while offset < size:
                byte = window[offset]
                if literal:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                        offset += 1
                        continue
                    elif byte == 40:
                        if depth >= 128 or nesting >= 128:
                            self.bank.reserve(128)
                        nesting += 1
                        if depth + nesting > self.limits.max_token_depth:
                            check_source_limit(
                                "token_depth", depth + nesting, self.limits.max_token_depth
                            )
                    elif byte == 41:
                        if nesting > 256:
                            self.bank.reserve(64)
                        nesting -= 1
                elif byte == 62:
                    nesting = 0
                elif byte not in HEX and byte not in WHITESPACE:
                    raise UnsupportedExactSource("hex_string_digit")
                offset += 1
                if offset > maximum_offset:
                    self.bank.reserve(128)
                    check_source_limit(
                        "token_bytes",
                        self.window_start + offset - start,
                        self.limits.max_token_bytes,
                    )
                if nesting == 0:
                    return self.window_start + offset
            position = self.window_end
        if escaped:
            raise UnsupportedExactSource("literal_string_escape")
        raise UnsupportedExactSource(
            "literal_string_unclosed" if literal else "hex_string_unclosed"
        )

    def finish_decoded(self, chunks, chunk_index, block, written, body_length):
        self.bank.reserve(256)
        if written:
            self.bank.reserve(256 + 2 * written)
            chunks[chunk_index] = bytes(block[:written])
        # CPython uses a heap Py_buffer array above its ten-slot join stack.
        if len(chunks) > 10:
            self.bank.reserve(80 * len(chunks))
        self.bank.reserve(64 + body_length)
        self.bank.charge(len(chunks) + 1)
        return b"".join(chunks)

    def decode_literal(self, start, end):
        self.bank.reserve(256)
        body_length = end - start - 2
        self.bank.reserve(256 + body_length)
        body = self.raw[start + 1 : end - 1]
        # Pay for both membership scans, including when decoding still follows.
        self.bank.reserve(256)
        self.bank.charge(2 * body_length)
        if b"\\" not in body and b"\r" not in body:
            block_size = 256 if body_length >= 254 else body_length + 2
            # Retain the original full-block and finalization work allowance.
            self.bank.charge(9 * (body_length // block_size) + 2)
            return body
        iterator = iter(body)
        block_size = 256 if body_length >= 254 else body_length + 2
        self.bank.reserve(192 + block_size)
        block = bytearray(block_size)
        self.bank.reserve(256)
        chunk_count = body_length // block_size + 1
        self.bank.reserve(128 + 8 * chunk_count)
        chunks = [b""] * chunk_count
        chunk_index = 0
        written = 0
        pending = None
        while True:
            byte = next(iterator, None) if pending is None else pending
            pending = None
            if byte is None:
                break
            if byte == 92:
                byte = next(iterator, None)
                if byte in (10, 13):
                    if byte == 13:
                        following = next(iterator, None)
                        if following != 10:
                            pending = following
                    continue
                if 48 <= byte <= 55:
                    self.bank.reserve(256)
                    value = byte - 48
                    digits = 1
                    while digits < 3:
                        following = next(iterator, None)
                        if following is None:
                            break
                        if not 48 <= following <= 55:
                            pending = following
                            break
                        value = value * 8 + (following - 48)
                        digits += 1
                    byte = value & 255
                else:
                    byte = ESCAPES.get(byte, byte)
            elif byte == 13:
                byte = 10
                following = next(iterator, None)
                if following != 10:
                    pending = following
            block[written] = byte
            written += 1
            if written == block_size:
                self.bank.reserve(768)
                self.bank.charge(8)
                chunks[chunk_index] = bytes(block)
                chunk_index += 1
                block = bytearray(block_size)
                written = 0
        return self.finish_decoded(chunks, chunk_index, block, written, body_length)

    def decode_hex(self, start, end):
        self.bank.reserve(256)
        body_length = end - start - 2
        self.bank.reserve(256 + body_length)
        body = self.raw[start + 1 : end - 1]
        block_size = 256 if body_length >= 254 else body_length + 2
        self.bank.reserve(192 + block_size)
        block = bytearray(block_size)
        self.bank.reserve(256)
        chunk_count = body_length // block_size + 1
        self.bank.reserve(128 + 8 * chunk_count)
        chunks = [b""] * chunk_count
        chunk_index = 0
        written = 0
        high = None
        for byte in body:
            if byte in WHITESPACE:
                continue
            nibble = byte - 48 if byte <= 57 else (byte | 32) - 87
            if high is None:
                high = nibble
            else:
                block[written] = high * 16 + nibble
                written += 1
                high = None
                if written == block_size:
                    self.bank.reserve(768)
                    self.bank.charge(8)
                    chunks[chunk_index] = bytes(block)
                    chunk_index += 1
                    block = bytearray(block_size)
                    written = 0
        if high is not None:
            block[written] = high * 16
            written += 1
        return self.finish_decoded(chunks, chunk_index, block, written, body_length)
