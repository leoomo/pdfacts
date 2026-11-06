# Plain literal byte reuse

Literal strings are still scanned by `string_end` before decoding. Its closure,
escape, nesting and token-size checks remain mandatory. After the original
prepaid body slice, a literal whose body contains neither backslash nor carriage
return is already its decoded value. Line feed, NUL, high bytes and balanced
nested parentheses remain unchanged. Return that exact bytes body without
constructing an iterator, bytearray, chunk list, chunk copies or join result.

The transformation branch remains the original decoder: escape sequences,
octal escapes, escaped delimiters, line continuations and CR/CRLF normalization
still use the same buffered algorithm. Hex strings are unaffected.

The initial 256-byte reservation and `256 + body_length` copy fee are preserved.
An additional independent 256-byte prepayment covers the selection/work-fee
arithmetic before any new scan. It covers the product `2*n`, division by the
block size, multiplication by nine and addition of two, all bounded integer
operations in the existing supported runtime and valid source-span domain.
The block size is either a module constant 256 or a cached value at most 255.
Exact bytes membership creates no iterator, slice or result container; the
booleans and constant search bytes already exist. No source-derived diagnostic
or cache is created.

Before either membership test, charge `2*n` work, covering two full scans even
when the first test short-circuits. This additional work is charged on both the
reuse and transformation branches; it does not borrow unspecified startup work.
The fast path also retains the original decoder work allowance. With
`block_size = 256 if n >= 254 else n + 2` and `q = n // block_size`, the original
algorithm charges eight for each full block and `q + 2` during finalization:
`9*q + 2`. Empty strings therefore still pay two, and exact block multiples
retain the extra finalization slot. The fast-path bytes total is `768+n` and
its work total is `2*n + 9*q + 2`, excluding the unchanged earlier scanner and
later token publication. Caller prepayment failures propagate unchanged.

Constructed tests exercise empty strings, 253/254/255/256/257 and larger block
boundaries, a size beyond the original join stack, exact and one-below byte/work
budgets, unchanged LF/NUL/high bytes/nested parentheses, CR and escape fallbacks,
cross-block escapes, and lexical/size refusals before decoding. A chunk-builder
trap proves the plain path bypasses buffered construction. None of this proves
whole-document feasibility or writable geometry.
