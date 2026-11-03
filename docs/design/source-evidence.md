# Private source evidence

This implementation is a private graphics capability under development. It is
not exported from the package root or advertised in the public capability table.
It binds original content operations to one complete same-read native root
inventory. It grants no permission to modify a document.

Engine resource access stays in the QPDF backend. Font, marked-content,
graphics-state and image-resource checks return only finite categories or plain
immutable metadata. The numeric interpreter receives no native dictionary,
stream or name. The graphics feature performs public-fact association, while
core coordinates engine records without importing the feature.

A caller supplies every `SourceEvidenceLimits` field explicitly. The fields bound
source and decoded bytes, joined content, streams, instructions, paths, root
objects, path segments, tokens, token nesting, saved graphics states, marked
content, resource ancestry, font encoding entries, text-array items and immutable payload
depth. The
additional work/allocation bank is a caller-owned `OperationBudget`. Every layer
receives the same bank and the same immutable configuration. A capture is
single-use and rejects a different bank or configuration object. The graphics
resolver checks the original Path and reference objects plus the frozen reference
coordinates. A cloned or mutated reference cannot borrow another fact's evidence. There are no
new defaults in `Limits`, and ordinary graphics reads retain their contracts.

Internal source-language failures become `SourceEvidenceError`, a `PdfactsError`
with a `reason`. Internal resource failures become `LimitExceededError` with
`limit`, `allowed` and `found`. A caller's budget exception propagates unchanged.
No failure issues a partial certificate. A source association may be refused
while ordinary graphics facts remain available.

Numerical support is separately bounded by the proved arithmetic language,
including finite binary32 interpretation, bounded exact rationals and the
supported operation/resource schemas. Resource limits are not silently
reclassified as unsupported geometry. Decoding chunk size and cache retention
are implementation strategies, not caller capacity limits.

Decimal tokens obey the caller token-byte limit. Their normalized numerator and
denominator each have a 4096-bit mathematical domain. Conversion uses bounded
decimal chunks and does not change the interpreter-wide integer digit limit.
A source value still needs independent agreement with the native record before
evidence can be issued.

The bank accounts for additional certification work and storage. It does not
claim to bound memory inside the ordinary native page load or image decode.
The existing single metadata capture is reused. No second image decode is
introduced for reconciliation. Separate-process isolation remains the caller's
responsibility as described in the existing limits convention.

A usable distribution must pass architecture checks, standalone byte-built
regressions and built-wheel import tests. Public activation, compatibility
commitments and wider operator support require separate review.

## Private runtime support

The cumulative Python-allocation proofs currently cover released CPython3.13.5,
with64-bit pointers,30-bit integer digits stored in4bytes, the ordinary GIL
build, and neither debug nor trace-reference object layout. Private source
entry points reject other runtimes before source reads, meter access or proof
construction. The predicate is a declared source-proof domain, not a conclusion
drawn from sampling object sizes. Ordinary library features keep their existing
runtime support and do not consult this guard. Package error translation applies
to the private graphics/core entry points. The backend's internal failures remain
backend signals.

The repository's pinned3.13.5 platform images exercise positive source cases.
Supporting another runtime requires a new allocation review and positive tests.
This private restriction changes neither engine versions nor existing limits.
