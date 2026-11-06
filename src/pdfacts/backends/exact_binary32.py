"""Private binary32 arithmetic witnesses. No write authority."""

import struct

_PACK_UINT = struct.Struct(">I")
_UNPACK_FLOAT = struct.Struct(">f")
_SIGN = 0x80000000
_FRACTION = 0x7FFFFF


def _long_size(bits):
    return 24 + 4 * ((bits + 29) // 30)


def round_ratio(numerator, denominator, bank, *, negative_zero=False):
    """Round an exact ratio directly to finite binary32 bits, nearest even."""
    bank.reserve(3072)
    bank.charge(64)
    if type(numerator) is not int or type(denominator) is not int:
        raise TypeError("binary32_ratio_type")
    if denominator <= 0:
        raise ValueError("binary32_denominator")
    numerator_bits = numerator.bit_length()
    denominator_bits = denominator.bit_length()
    if numerator_bits > 4096 or denominator_bits > 4096:
        raise ValueError("binary32_ratio_bound")
    if numerator == 0:
        return _SIGN if negative_zero else 0
    sign = _SIGN if numerator < 0 else 0
    if sign:
        bank.reserve(_long_size(numerator_bits))
        numerator = -numerator
    exponent = numerator_bits - denominator_bits
    alignment_bits = max(numerator_bits, denominator_bits)
    bank.reserve(_long_size(alignment_bits + 30))
    bank.charge(alignment_bits)
    if exponent >= 0:
        if numerator < denominator << exponent:
            exponent -= 1
    elif numerator << -exponent < denominator:
        exponent -= 1
    if exponent > 127:
        return None
    if exponent < -150:
        return sign
    quantum = exponent - 23 if exponent >= -126 else -149
    if quantum >= 0:
        bank.reserve(_long_size(denominator_bits + quantum + 30))
        denominator = denominator << quantum
    else:
        bank.reserve(_long_size(numerator_bits - quantum + 30))
        numerator = numerator << -quantum
    a_limbs = (numerator.bit_length() + 29) // 30
    b_limbs = (denominator.bit_length() + 29) // 30
    # Positive quotient is strictly below 2**24, hence x_divrem k <= 2.
    bank.reserve(56 + (24 + 4 * (a_limbs + 1)) + (24 + 4 * b_limbs) + 32)
    bank.charge(64 + 32 * (a_limbs + b_limbs + 2))
    quotient, remainder = divmod(numerator, denominator)
    bank.reserve(_long_size(denominator.bit_length() + 31))
    twice_remainder = remainder << 1
    if twice_remainder > denominator or (twice_remainder == denominator and quotient & 1):
        quotient += 1
    if exponent < -126:
        return sign | quotient
    if quotient == 0x1000000:
        quotient >>= 1
        exponent += 1
    if exponent > 127:
        return None
    return sign | ((exponent + 127) << 23) | (quotient - 0x800000)


def decode(bits, bank):
    """Return an exact signed mantissa/exponent pair for finite binary32."""
    bank.reserve(256)
    bank.charge(16)
    if type(bits) is not int or bits < 0 or bits > 0xFFFFFFFF:
        raise ValueError("binary32_bits")
    exponent = (bits >> 23) & 255
    if exponent == 255:
        raise ValueError("binary32_nonfinite")
    mantissa = bits & _FRACTION
    if exponent:
        mantissa |= 0x800000
        exponent -= 150
    else:
        exponent = -149
    if bits & _SIGN:
        mantissa = -mantissa
    return mantissa, exponent


def multiply(left, right, bank):
    """Multiply two decoded finite binary32 dyadics exactly."""
    bank.reserve(256)
    bank.charge(16)
    return left[0] * right[0], left[1] + right[1]


def add(left, right, bank):
    """Add a finite product and finite binary32 dyadic, or two binary32s."""
    bank.reserve(512)
    bank.charge(64)
    exponent = min(left[1], right[1])
    left_mantissa = left[0] << (left[1] - exponent)
    right_mantissa = right[0] << (right[1] - exponent)
    return left_mantissa + right_mantissa, exponent


def ratio(value, bank):
    """Convert a decoded dyadic/product/aligned sum to a bounded exact ratio."""
    bank.reserve(256)
    bank.charge(32)
    mantissa, exponent = value
    if exponent >= 0:
        return mantissa << exponent, 1
    return mantissa, 1 << -exponent


def as_float(bits, bank):
    """Decode expected bits for direct comparison with untouched native data."""
    bank.reserve(256)
    bank.charge(8)
    return _UNPACK_FLOAT.unpack(_PACK_UINT.pack(bits))[0]
