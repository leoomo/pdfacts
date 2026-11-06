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

## Word scanning and its immediate consumer

The word-scanning refinement uses the same strict compact-length domain. Its
64-byte normal reservation covers two possible 32-byte integers: the terminal
absolute index, and `read`'s subsequent `index - start` token length. All relative
offsets and escape counters remain cached; window construction keeps its own
768-byte reservation. Larger inputs keep 256. The selected domain is captured
before the reservation callback and returned as an existing boolean to `read`,
so the caller's exception accounting uses the fee actually selected.

Five refusal paths restore the original 256-byte reservation **before** locally
constructing an exception, by reserving an additional 192 in the compact domain:

| Refusal | Placement of the additional reservation |
| --- | --- |
| Invalid hex byte in a name escape | Before error-index arithmetic and raising |
| Nonprintable byte encountered inside a word | Freeze the window-base reference, then reserve before error-index arithmetic and raising |
| Incomplete name escape at EOF | Before error-index arithmetic and raising |
| Completed token exceeds `max_token_bytes` | Before calling the unchanged limit-check helper |
| Invalid first byte of a non-name word | Before raising in `read` |

Error offsets retain their original meaning. No exception is constructed and
caught merely to pay afterward. A caller's budget exception propagates unchanged;
it does not construct a library error or publish a token. The two scanner escape
paths already keep their error bases in local references. No new tuple, closure,
container or diagnostic object is created by the fee selection or restoration.
The token-limit allowance is captured before restoration and the same reference
is passed to the limit-check helper, even if the callback replaces the parser's
limits object.

After these checks, every `read` branch reaches an existing independent
reservation before further allocating work: common operators reserve 256,
single-byte values proceed to `token`'s unchanged reservation, and general
scalars reserve 256 before their copy-fee arithmetic. Short scalar-cache lookup
has no intervening allocating arithmetic: length is 2..8, the printable first
byte is at most 126, and `first_byte + 13 * length` is at most 230; its mask and
entry-length results are also cached. A hit proceeds directly to `token`; a miss
proceeds to the scalar-copy reservation. Cache lookup's byte comparison creates
no slice. Boolean/null rejection, numeric validation and name decoding follow
their existing independent scalar-copy/iterator/decoder reservations. Those
fees, work charges, capacity checks and error conditions remain unchanged.

The restored error allowance retains the existing 256-byte accounting envelope;
this change does not introduce a new claim about interpreter exception-frame
allocation or take unassigned credit from parser startup. Negative tests must
observe restoration before the relevant raise, preserve error reason/index,
and refuse with the original caller exception when restoration cannot be paid.

## Short-number consumer protocol

An explicit private `consumer_short_numbers` option requires the existing
endpoint channel. Ordinary default and endpoint consumers keep their four
arguments and 256-byte reservation. The new source-interpreter caller accepts a
fifth boolean describing the reservation actually selected. This boolean is
accounting metadata, not authority to accept a token or trust its contents.

Before the first budget callback, capture the endpoint, original raw length and
channel choice; the consumer, kind, value and start are already local argument
references. Prepay 64 before evaluating the selector. A discounted token must be
a number with exact bytes of length 1..8, exact integer start/end in the original
raw span, raw length below 2**30, and span length 1..8. The selector creates at
most one temporary integer: either the length of long bytes before rejection,
or a compact span difference before rejection. Eligible short values use cached
integers throughout. Any other token immediately reserves the remaining 192.
The fifth argument records that actual decision; the consumer must not infer
it again from a span that an intervening adapter might have changed.

The source interpreter restores 192 before any span refusal for a discounted
call. After validating the span, it restores the allowance before continuing if
the kind/value/length no longer belongs to the short-number domain. This permits
the ordinary full-fee path to apply its unchanged grammar and error rules.
The span-length test precedes the bytes-length test: a non-short span restores
immediately, while a short span uses a cached integer before checking even a
substituted very large bytes value. Thus only one potentially uncached length
exists before restoration, including values larger than the original content.
Remaining discounted numeric refusals restore the allowance before errors for
operand-count/token-length limits or source-byte disagreement. Each path pays
at most one restoration, and none constructs and catches an error to pay later.

In the still-discounted successful path, numeric length, `3*length+4`, the
operand index and value length are cached integers. At most six arguments are
retained, so list growth has at most an eight-pointer (64-byte) backing array.
The argument list object already exists. Numeric-cache hits reuse their existing
entries and OrderedDict nodes; misses reserve independently before decimal and
Fraction construction, insertion and eviction. No numeric result allocation is
charged to the consumer's 64-byte allowance. Non-numeric, long-number, default
and ordinary endpoint callbacks retain the full reservation.

Validation must observe both sides of the actual fee protocol: default callback
arity/cost, opt-in arity/flags, frozen endpoint/channel inputs, successful short
numbers and fallback types/spans, byte-disagreement and forged-span adapters,
six-operand refusal, and caller exceptions when restoration is denied. Tests
must preserve the fifth flag while corrupting source spans; recomputing it from
the corrupted input would hide precisely the accounting mismatch being guarded.
