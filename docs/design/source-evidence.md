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

## Private staged completion

`capture_root_path_evidence(document, page_index, bank, limits=...)` returns
ordinary paths/images plus an opaque pending token. This is not a source
certificate. Keep the original document open and the analysis owner alive.
`complete_root_path_evidence(document, bank, pending_token, limits=...)`
reconciles the entire original page and returns the ordinary batch token, or
None after full image-only completion. The same bank and exact limits object
are required throughout. A claimed token is spent even when completion fails.
Invalid foreign-token, bank or limits attempts do not consume another pending
operation. Pages can be completed in any sequential order.

Only one completion is active per analysis generation. Recursive capture or
completion during its budget callbacks refuses. Close, recovery and owner exit
invalidate pending or active work; publication rechecks original providers,
generation, references and registry state after all callbacks. Pure Python
source parsing can finish after revocation, but cannot publish a certificate.
The existing before-native-read close guards remain in force.

The eager issuer shares the same private legs within one outer access region;
it still returns fresh ordinary objects and a completed batch in one call.
Existing completed tokens remain resolvable after document close while the
original analysis owner remains alive. Staging does not change source-language
support, allow partial-page validation, or introduce another allocation bank.
See [the cumulative allocation inventory](staged-source-accounting.md).

## Open joined-line association

Private source association also accepts open, stroke-only sequences of one or
more `m l+` subpaths, including consecutive line segments. The original ordered
operations remain in `root-association-v2`; there is no new support or write
permission. A caller needing positive rectangle or independent-line authority
must classify those original operations, not infer their syntax from the
public shape. An open sequence returning to its start can have rectangle shape.

A separate helper preserves the prior independent-line/closed-retrace branch.
Arbitrary joined closed paths, mixed transforms, adjacent exact or native
collapsed vertices, and unsupported unrelated source activity remain refused.
Every native segment, close flag, paint state and complete matrix witness still
passes the existing common reconciliation checks. All original page activity
remains in the ledger, with one caller bank and unchanged limits.

The new dispatch scan prepays512bytes and32+16*N work. It iterates the existing
operation-name tuple without per-iteration counters or projected tuples. The
open helper prepays1024bytes/64work for fixed controls, then256bytes/32work for
its six exact matrix-component type checks. Known-count output construction
prepays192+16*N bytes: a temporary one-item list64, output list56+8*N and final
tuple40+8*N total160+16*N. Each vertex prepays512bytes/64work for its72-byte
four-tuple,36-byte noncached index increment and bounded validation/control
intermediates. Existing `held` conversions remain independently charged.

An exact-Fraction comparison prepays512bytes/32work before reading types and
bit lengths, then16+4*ceil(B/30) work before integer numerator/denominator
comparisons, where B is the maximum bit length of their four integers. It
refuses values beyond the existing4096-bit domain, creates no Fraction or
cross-product, and invokes no polymorphic equality. The512-byte prephase covers
at most twelve36-byte scalar intermediates plus64bytes of call slack. Matrix
identity is a fast path; a distinct matrix tuple prepays256bytes/32work for a
six-entry loop and independently charges each scalar comparison. Function frame
accounting follows the existing source-runtime convention.

No old fee is reduced. Old nonrectangle `S` paths pay the additive selection
scan; old `s` and rectangle branches do not. Bytecode tests check that the new
helpers build only the one-item output-list seed and one four-tuple per vertex,
with no generator or hidden per-vertex collection. Exact/one-below tests cover
every helper reservation and comparison charge, wide integer comparison, and
complete issuance. These inventories do not claim any caller's complete
operation fits its cumulative bank. No stroke support or rendered blankness is
computed by association.

Post-reconciliation provider validation translates only backend EngineFailure
into EngineError with engine `qpdf`, at both the eager-core and staged-core
boundaries. This also covers a document closed by a reconciliation budget
callback. Caller budget exceptions propagate by identity; no broad catch or
new normal-path constructor was added. The same close-callback regression is
exercised with old independent subpaths and new joined subpaths.

## Mixed-root support completion

`capture_root_path_evidence` can feed either existing path-only completion or
`complete_root_support_evidence(document, bank, pending_token, *, limits)`.
Both consume the same pending claim once; neither may follow the other.
The support operation returns one opaque mixed-root batch, including an image-only
page. Existing eager/path-only APIs retain their return contracts and path ordinals.

`resolve_root_support_evidence(owner, handle, data, bank, token, root_ordinal,
graphic)` resolves the exact original Path or Image and GraphicRef. Ordinals are
original mixed nontext root drawing positions. Its batches and path-only batches
reject cross-use. All owner/bank/generation, complete-source validation, original
page/provider lifetime and callback-safe final publication requirements remain.

Path payloads retain `root-association-v2`; image payloads use
`root-image-association-v1` with source digest, page index, page reference,
content identities, source tuple and native witness tuple. Source image fields
are instruction, resource identity, exact matrix, optional exact rectangular clip,
pixel width/height, colour-space identifier and bits per pixel. Native fields are
matrix, width, height, colour-space identifier and bits per pixel. Exact rationals
use the existing immutable fraction representation. The record is the already
reconciled source image emission; no pixels are decoded or graphics reread.

These are source associations, not raster/vector support envelopes or positive
writable-row proofs. Annotation appearances are not root-image/path certificates.
Consumers must not infer absence of unrelated paint from a filtered root subset,
an image-only completion result, or a native bounding box. Positive path operation
allowlists remain independent of negative-support association.

### Empty mixed-root completion

A mixed-root completion may return `None` for a source-validated page with no
root paths or images. It still parses the complete supported source, reconciles
the empty native inventory, validates the live owner and consumes the pending
claim once. This proves no graphical association was issued; it does not prove
the page is blank or writable, since text may remain. Unsupported content still
refuses. The legacy path-only nonempty-inventory contract is unchanged.
