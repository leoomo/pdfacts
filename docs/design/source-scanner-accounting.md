# Ignorable source scanner accounting

The private source parser already restricts its allocation proofs to ordinary
64-bit CPython 3.13.5 with 30-bit integer digits. This refinement changes no
caller capacity, lexical rule, work fee or window-loading fee.

For content shorter than 1073741824 bytes, `skip_ignorable` reserves 64 bytes
instead of 256. Larger content keeps the original 256-byte reservation. The
threshold comparison itself uses an existing integer constant. The proof uses
the parser's original valid window and index invariants, not arbitrary mutation
of its private attributes.

| Operation | Additional allocation within this reservation |
| --- | --- |
| Byte indexing and membership checks | Cached byte integers and booleans |
| `position - window_start` after loading the containing window | Both absolute operands are compact; the result 0..255 is cached |
| `len(window)` | Cached integer 1..256 |
| Offset increments | Cached integers through 256 |
| Comment state and index assignments | Existing references |
| Final `window_start + offset` when a non-ignorable byte is found | At most one compact integer, 32 bytes |
| EOF exit | Reuses the existing raw-length integer |

There are no per-byte containers, iterators, slices, explicit bound-method
objects, callback adapters or locally constructed exceptions. Ordinary direct
method calls use the pinned interpreter's method-call path; interpreter frames
retain the existing source accounting convention. A budget exception propagates
unchanged. Each new window retains its independent 768-byte prepayment, including
its bytes slice and endpoint arithmetic. Crossing a window does not multiply the
final-index allocation: the scan either returns at its first non-ignorable byte
or reaches EOF using existing window-end references.

The strict threshold matters. A small subtraction *result* does not prove that
large operands allocate nothing: CPython's noncompact subtraction can allocate a
temporary before normalizing it to a cached integer. With raw length below
2**30, every valid absolute index is at most 2**30-1 and uses the compact path.
Window loading may compute a larger temporary endpoint, but pays for it in its
own unchanged reservation.

Constructed tests pin exact and one-byte-below reservations, all six PDF
whitespace bytes, window crossings, comment endings, EOF and caller-exception
identity. A stopped metadata probe checks both sides of the threshold without
claiming to parse a gigabyte input. This bound does not itself establish that a
complete real document fits the cumulative bank or has writable geometry.
