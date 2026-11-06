"""Exact PDF decimals with explicit token and normalized rational bounds."""

from fractions import Fraction

from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit
from pdfacts.backends.source_runtime import require_source_runtime

_POWERS_OF_TEN = (1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000)


def _integer_step(value, bank, outputs=1):
    # The fixed phase precedes bit-length and reservation arithmetic. Each
    # result gets a spare limb for a single-limb multiply or add.
    bank.reserve(512)
    bits = value.bit_length()
    limbs = (bits + 29) // 30
    bank.reserve(384 + outputs * (32 + 4 * (limbs + 2)))
    bank.charge(8 + outputs * limbs)


def _long_fraction(digits, scale, negative, bank):
    significant = digits.lstrip(b"0")
    if not significant:
        bank.reserve(512)
        return Fraction(0)
    stripped = significant.rstrip(b"0")
    removable = min(scale, len(significant) - len(stripped))
    if removable:
        significant = significant[:-removable]
        scale -= removable
    numerator = 0
    position = 0
    count = len(significant)
    while position < count:
        _integer_step(numerator, bank, 2)
        chunk = significant[position : position + 9]
        # At most nine digits, independent of the interpreter's global limit.
        numerator = numerator * (_POWERS_OF_TEN[len(chunk)]) + int(chunk)
        position += 9
    twos = scale
    fives = scale
    if scale:
        last = significant[-1]
        prime = 2 if last % 2 == 0 else 5 if last == 53 else 0
        removed = 0
        if prime:
            while removed < scale:
                _integer_step(numerator, bank)
                quotient, remainder = divmod(numerator, prime)
                if remainder:
                    break
                numerator = quotient
                removed += 1
            if prime == 2:
                twos -= removed
            else:
                fives -= removed
    bank.reserve(256)
    if numerator.bit_length() > 4096:
        raise UnsupportedExactSource("rational_bits")
    denominator = 1
    # Fixed single-limb factors keep every arithmetic allocation bounded by
    # the current operand size. The denominator grows monotonically.
    for prime, remaining in ((2, twos), (5, fives)):
        while remaining:
            _integer_step(denominator, bank)
            denominator *= prime
            remaining -= 1
            if denominator.bit_length() > 4096:
                raise UnsupportedExactSource("rational_bits")
    _integer_step(numerator, bank)
    bank.reserve(512)
    if negative:
        numerator = -numerator
    # All common factors of the decimal denominator have been removed above.
    return Fraction._from_coprime_ints(numerator, denominator)


def parse_pdf_number(token, bank, *, limits):
    require_source_runtime()
    bank.reserve(256)
    if type(token) is not bytes or not token:
        raise UnsupportedExactSource("numeric_token_domain")
    length = len(token)
    check_source_limit("token_bytes", length, limits.max_token_bytes)
    short_token = length <= 8
    allocation = 1280 if short_token else 4096
    bank.reserve(allocation + length * 8)
    bank.charge((4 if short_token else 16) * length + 8)
    negative = token.startswith(b"-")
    unsigned = token[1:] if token[:1] in (b"+", b"-") else token
    whole, _, fractional = unsigned.partition(b".")
    digits = whole + fractional
    if not digits or not digits.isdigit():
        if short_token:
            bank.reserve(256)
        raise UnsupportedExactSource("numeric_token_domain")
    if short_token:
        numerator = int(digits)
        if negative:
            numerator = -numerator
        return Fraction(numerator, _POWERS_OF_TEN[len(fractional)])
    return _long_fraction(digits, len(fractional), negative, bank)
