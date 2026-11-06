# Short decimal conversion accounting

The private exact decimal parser retains its pinned ordinary 64-bit CPython
3.13.5 runtime, input-type and token-length checks, work fees, and exact Fraction
normalization. For source tokens of at most eight bytes, it reads the denominator
from the existing immutable powers-of-ten tuple rather than constructing a power.
The fractional digit count is within that tuple's existing 0..9 domain.

The initial 256-byte reservation is unchanged. The next short conversion fee is
1280 plus eight times the source length; longer tokens retain 4096 plus eight
times the length and the existing long conversion algorithm. The successful
short-path inventory is independent of interpreter specialization:

| Allocation group | Conservative bytes |
| --- | ---: |
| Two slice objects | 112 |
| Unsigned bytes copy | 41 |
| One-byte sign-test bytes result | 34 |
| Two partition bytes copies | 82 |
| Partition tuple | 64 |
| Concatenated digits | 41 |
| Cold partition-unpacking iterator | 48 |
| Integer parsing, sign, gcd and quotient temporaries including the zero-value division edge | 256 |
| Fraction instance, super object, call-argument containers and native method wrapper | 512 |

The byte preparation subtotal is 422. The integer allowance covers 32 bytes each
for the parsed integer, sign and gcd result, 64 for the two quotients, and a
further 64 for the zero-value division edge: 224 in total. The construction
allowance covers the 48-byte Fraction, 56-byte super object, at most four
64-byte argument containers and an 80-byte native method wrapper: 440 in total.
Thus 422+256+512=1190 is below 1280, without taking credit from the unchanged
initial reservation. The eight-times-length component remains conservative.
The denominator lookup reuses a module-owned integer; no exponentiation
intermediates remain on this path. Ordinary interpreter frame handling retains
the existing source-accounting convention.

Malformed digits restore the omitted 256 before constructing the existing
`numeric_token_domain` exception. This preserves their original reservation,
rather than borrowing spare space from successful conversion. A caller failure
while paying that restoration propagates unchanged. The selected short flag,
length and exact bytes token are local values; the refusal is not reconsidered
after the new callback. Wrong-type, empty-token and token-limit refusals precede
the changed reservation and remain unchanged.

Constructed tests pin exact values, normalized signs and fractions, eight-byte
boundaries, exact and one-below fees, all malformed-digit refusal paths and
denied restoration, the unchanged nine-byte path and token-limit ordering.
These accounting tests do not establish whole-document feasibility or writing
authority.
