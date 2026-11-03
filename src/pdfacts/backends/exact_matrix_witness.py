"""Source-derived binary32 association witness; never source geometry."""

from fractions import Fraction
from math import isfinite

from . import exact_binary32 as binary32
from .exact_stream import UnsupportedExactSource

EXACT_IDENTITY = (Fraction(1), Fraction(0), Fraction(0), Fraction(1), Fraction(0), Fraction(0))
IDENTITY_PLAN = object()
INCOMPATIBLE_PATH_PLAN = object()
_IDENTITY_BITS = (0x3F800000, 0, 0, 0x3F800000, 0, 0)
_NOT_FOUND = object()
_ZERO_BITS = (0, 0x80000000)


def _lookup(plan, memo, bank):
    bank.reserve(384)
    bank.charge(8)
    index = 0
    while index < 4:
        entry = memo[index]
        if entry is not None:
            if type(entry) is not tuple or len(entry) != 2:
                raise UnsupportedExactSource("matrix_witness_memo")
            if entry[0] is plan:
                result = entry[1]
                if result is not None:
                    if type(result) is not tuple or len(result) != 6:
                        raise UnsupportedExactSource("matrix_witness_memo")
                    for value in result:
                        if (
                            type(value) is not int
                            or not 0 <= value <= 0xFFFFFFFF
                            or value & 0x7F800000 == 0x7F800000
                        ):
                            raise UnsupportedExactSource("matrix_witness_memo")
                return result
        index += 1
    return _NOT_FOUND


def _store(plan, result, memo, bank):
    bank.reserve(128)
    bank.charge(4)
    index = memo[4]
    memo[index] = (plan, result)
    memo[4] = (index + 1) % 4


def _operand(exact, token, bank):
    bank.reserve(128)
    if type(exact) is not Fraction or type(token) is not bytes or not 0 < len(token) <= 1024:
        return None
    bank.charge(len(token) + 8)
    numerator = exact.numerator
    denominator = exact.denominator
    decimal = b"." in token
    if not decimal:
        if denominator != 1:
            return None
        if token[0] == 43:
            if not 0 <= numerator <= 0x7FFFFFFF:
                return None
        elif token[0] == 45:
            if not -0x80000000 <= numerator <= 0:
                return None
        elif not 0 <= numerator <= 0xFFFFFFFF:
            return None
    return binary32.round_ratio(
        numerator,
        denominator,
        bank,
        negative_zero=decimal and token.startswith(b"-") and numerator == 0,
    )


def _round(value, bank):
    numerator, denominator = binary32.ratio(value, bank)
    return binary32.round_ratio(numerator, denominator, bank)


def _translation(product, old_offset, bank):
    rounded_product = _round(product, bank)
    if rounded_product is None:
        return None
    separate = _round(binary32.add(binary32.decode(rounded_product, bank), old_offset, bank), bank)
    fused = _round(binary32.add(product, old_offset, bank), bank)
    if separate is None or fused is None:
        return None
    if separate != fused and not (separate in _ZERO_BITS and fused in _ZERO_BITS):
        return None
    return separate


def _evaluate_node(plan, previous, bank):
    bank.reserve(1024)
    bank.charge(64)
    if previous is None:
        return None
    parent, exact_matrix, operands, tokens, depth = plan
    if (
        type(operands) is not tuple
        or len(operands) != 6
        or type(tokens) is not tuple
        or len(tokens) != 6
    ):
        return None
    bits = [None] * 6
    index = 0
    while index < 6:
        bits[index] = _operand(operands[index], tokens[index], bank)
        if bits[index] is None:
            return None
        index += 1
    old_bits = previous
    old_a = binary32.decode(old_bits[0], bank)
    old_d = binary32.decode(old_bits[3], bank)
    scale_a = _round(binary32.multiply(binary32.decode(bits[0], bank), old_a, bank), bank)
    scale_d = _round(binary32.multiply(binary32.decode(bits[3], bank), old_d, bank), bank)
    offset_e = _translation(
        binary32.multiply(binary32.decode(bits[4], bank), old_a, bank),
        binary32.decode(old_bits[4], bank),
        bank,
    )
    offset_f = _translation(
        binary32.multiply(binary32.decode(bits[5], bank), old_d, bank),
        binary32.decode(old_bits[5], bank),
        bank,
    )
    if scale_a is None or scale_d is None or offset_e is None or offset_f is None:
        return None
    return (scale_a, 0, 0, scale_d, offset_e, offset_f)


def matches(plan, exact_matrix, native_matrix, memo, bank):
    bank.reserve(256)
    bank.charge(16)
    if plan is None:
        return False
    bank.reserve(1024)
    bank.charge(64)
    if type(exact_matrix) is not tuple or len(exact_matrix) != 6:
        return False
    for value in exact_matrix:
        if (
            type(value) is not Fraction
            or value.numerator.bit_length() > 4096
            or value.denominator.bit_length() > 4096
        ):
            return False
    if type(native_matrix) is not tuple or len(native_matrix) != 6:
        return False
    for value in native_matrix:
        if type(value) is not float or not isfinite(value):
            return False
    if plan is IDENTITY_PLAN:
        return exact_matrix is EXACT_IDENTITY and native_matrix == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if type(plan) is not tuple or len(plan) != 5 or plan[1] is not exact_matrix:
        return False
    result = _lookup(plan, memo, bank)
    if result is _NOT_FOUND:
        bank.reserve(256)
        depth = plan[4]
        if type(depth) is not int or depth <= 0:
            return False
        bank.reserve(256 + 8 * depth)
        stack = [None] * depth
        remaining = depth
        current = plan
        result = _IDENTITY_BITS
        while current is not IDENTITY_PLAN:
            bank.reserve(128)
            bank.charge(16)
            if (
                type(current) is not tuple
                or len(current) != 5
                or type(current[4]) is not int
                or current[4] != remaining
                or remaining <= 0
            ):
                return False
            cached = _lookup(current, memo, bank)
            if cached is not _NOT_FOUND:
                result = cached
                break
            remaining -= 1
            stack[remaining] = current
            current = current[0]
        if current is IDENTITY_PLAN and remaining != 0:
            return False
        while remaining < depth:
            bank.reserve(128)
            bank.charge(16)
            current = stack[remaining]
            result = _evaluate_node(current, result, bank)
            _store(current, result, memo, bank)
            remaining += 1
    if result is None:
        return False
    index = 0
    while index < 6:
        if binary32.as_float(result[index], bank) != native_matrix[index]:
            return False
        index += 1
    return True
