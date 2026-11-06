# Staged source accounting

This is the implementation inventory for the private staged source path. It
uses the existing pinned CPython 3.13.5, 64-bit ordinary-build accounting domain.
It does not change caller budgets, engine pins or the existing eager payload.

## Additional explicit completion access

An explicit capture followed by explicit completion creates one additional
Document._access context. The eager operation shares its existing outer access
between the private capture and completion bodies. The ordinary reader's nested
access is unchanged.

Reserve 4096 bytes and 32 work before constructing the additional context.
Observed primitive sizes: each context manager instance 48; conservatively each
instance dictionary 296; document generator 240; runtime generator 208;
document argument tuple 48; runtime empty argument tuple at most 40;
keyword dictionaries 64 each; FeatureAccess instance 48 and dictionary 296;
bound check-forms method 64. This named inventory totals 1760 bytes. Empty
immutable argument objects may be reused, but no credit is required.

A further 768 bytes covers constructor/call intermediates: two possible
three-argument manager-constructor tuples (128), the five-argument FeatureAccess
constructor tuple (80), three native/exceptional exit triples (192), and two
bound-method intermediates (128), totaling 528. Another 1152 bytes covers at
most 32 fixed whole-size scalar intermediates at 36 bytes. The listed envelope
is 3680, below 4096. No additional generator,
closure, stack or callback adapter is constructed by the staged wrapper.
Function frame accounting retains the existing source-runtime convention.

## Final reference scan

Before any final publication scope, reserve a 512-byte arithmetic envelope,
then reserve 192 bytes plus 72 * max(0, N - 256), and charge 24*N + 96 work.
The final scan is an indexed while loop over all retained nontext roots, with
one root index and one path-only index. It allocates no zip/enumerate objects,
no reference copies, and no new tuple per root. The variable fee covers both
noncached index streams, independently of capture's earlier snapshot counters.
The 192 base covers fixed len results and guard intermediates; the arithmetic
512 covers at most twelve 36-byte formula intermediates. Image-only pages use
the same scan and publication validation.

The scan checks original ref identity and coordinates, and checked registry
entries against original path/ref identities. It performs no budget callback,
engine operation or cache-missing page lookup while the identity lock is held.

## Other staging fees

Generation holder: reserve 128 before the dynamic three-slot list (80), marker
(16), and extra generation-tuple slot (8). Recovery pays separately.
Capture controls: reserve 1280 before node (96), binding (112), captured (72),
original Page/PageRef plus object-number/generation tuple (72), token (16),
return tuple (64), up to four native-lock exit tuples (256), and up to twelve
whole-size intermediates (432): total 1120. This is 16 bytes more than the
previous named rows, which actually summed to 1104; the inherited 1204 subtotal
was an arithmetic overcount of 100. The 1280 reservation is unchanged.
Capture prepays 192 work for control and both binding guards, and its initial
generation lookup separately prepays 32 work and a 64-byte lock exit.
Reference snapshot: reserve 512 arithmetic, then 192+16*N plus 36*(N-256) for
N>256, and 8*N work, before constructing/filling the list and final tuple.
Claim controls: reserve 512 plus one 64-byte abort exit before claim, and pay
64 for the initial scope and for each visited node; charge 128 fixed work plus
8 per node. The node
is unlinked and its next cleared before installing active completion. Ready,
registry snapshot and publication each pay a separate 64 exit. Ready and the
registry snapshot each prepay 64 work for their binding guard. The final
24*N+96 work covers the whole-root type/ref scan and final binding guard.
No callbacks
execute under these new identity scopes. Cleanup uses the setup-time fee and
never calls the bank. Existing source, payload and registry fees remain due.

Actual code and discriminating accounting tests must be reviewed together;
these envelopes are not evidence that a particular application's complete
operation fits its original cumulative bank.

## Checks against the implemented instructions

The actual capture helper contains BUILD_LIST1 for the prepaid reference-list
multiplication, and BUILD_TUPLE4/9/4/3 for the page witness, binding, retained
capture and return. Publication contains BUILD_LIST5. Both generation creation
paths contain BUILD_LIST3 and BUILD_TUPLE6. The claim and final-reference helpers
contain no BUILD_LIST, BUILD_TUPLE, BUILD_MAP, MAKE_FUNCTION or generator
constructor. Core completion contains only BUILD_TUPLE4 and BUILD_TUPLE5; these
use 72+80 bytes of the existing 256-byte exact-result allowance. The existing
128-byte core operation allowance is retained separately from new claim fees.

The executed context-constructor observer follows an actual successful staged
completion: exactly two generator context managers and one FeatureAccess are
constructed, and their measured instance/dictionary/generator/argument/method
sum stays within 1760. A native-call profile of a three-node claim observes
four claim lock exits, one ready exit, two registry exits and one abort exit:
exactly K+5. These tests assert their observers actually ran. Exact/one-below
claim tests cover K=0,1,3. A 300-root completion exercises noncached final-scan
indices and its exact final budget boundary under the original cumulative bank.

## Callback-free exact domains

Every new identity scope that reads the document or its providers first checks
exact concrete classes using module-owned witnesses. Core owns the Document,
Page, PageRef and provider class tuples; graphics owns Path, Image and GraphicRef.
Their three fixed module-initialization tuples occupy 64+56+64=184 bytes, like
other immutable module metadata. Operations pass these existing references
with ordinary positional calls; they create no class-witness tuple or per-root
record. The backend does not import core or graphics and never derives an
expected class from caller objects or a pending record.

Before page-cache access, the cache must be an exact dict. Before page/ref
attributes, the objects must have the original exact Page/PageRef classes and
identities. Page-ref scalars must be exact ints before comparison. Closed flags
are tested by identity with False, never user-defined truthiness. Root graphics
and GraphicRef likewise pass exact-class guards before attribute reads. The
extra type checks allocate no per-operation objects or counters; the retained
page witness adds the 16 bytes listed above. Their work is charged explicitly
in the revised fixed and per-root fees. Same-layout class substitution, custom
page values/cache/flags, and equal-but-distinct PageRef replacement all refuse
without invoking user getters, publishing a batch, or retaining active work.

## Mixed-root support batches

The optional internal support mode uses the same access context, pending claim,
source parser, native reconciliation, registry and final publication scopes.
There are no new locks, context managers, callbacks or source reads. The path-only
mode keeps its original batch shape and image-only None return. Mixed mode adds
a fixed `root-support-v1` discriminator to its existing owner-scoped batch tuple;
actual tuple size is64bytes instead of56. A64-byte and64-work prepayment covers
this addition and fixed mode/schema checks.

The original image/source pair is already present during publication. Mixed mode
serializes its eight source fields and five native witness fields using the
existing `_record_payload` and `_freeze` contracts. The eight-field source tuple
is104bytes and temporary list120; the six-entry frozen matrix tuple is88 and its
six fraction wrappers64 each. The five-field native tuple is80 and list96.
Matrix/clip list and tuple copies, fraction wrappers and noncached counters keep
the existing recursive prepayments; source numbers are not converted to floats.
An optional four-coordinate exact clip uses the same independent freeze fees.

Before the image payload header and input pair, reserve512bytes: measured96 for
the seven-reference payload and56 for its pair. The original image/ref checked
entry has four56-byte pairs,224total; registration retains the existing
512+288*N reservation applied to every mixed root, and charges20*N work instead
of16*N for the image-domain branch. Preallocated input and checked lists grew
64→72bytes and registered tuple48→56 in a constructed one-image/one-path page;
existing count-sized fees cover these increments. No duplicate image dataclass,
resource object, matrix plan or decoded pixel buffer is created.

The final scan charges28*N+96work in mixed mode, keeping the existing arithmetic
and two-counter byte fees. It checks every root against the entry at its mixed
ordinal. Both modes still enforce max_paths on the counted paths; mixed mode
also enforces max_root_objects on its entries. Resolver feature work increases
from8 to24 for explicit format/domain branches and exact-class rechecks before
postcallback attributes. Its256-byte prepayment is unchanged; it constructs no
new normal-path container. Backend resolver format checks reject cross-use.

Measured on a constructed mixed page under CPython3.13.5, path-only completion
reserved213724bytes and2867work; mixed completion reserved220396bytes and3326work.
The increment is6672bytes/459work including existing recursive validation fees.
Image verification charges96work (64+32) instead of the original32 for repeated
exact domains of both private native records and public image/corner reads after
comparison callbacks. This
constructs no new normal-path object and protects both completion modes.
These measurements exclude instrumentation allocations. Neither cap nor engine
pin changed. Tests cover actual source payloads, exact/one-below cumulative
limits, original identity, no second native read, late subtype mutations with
zero getters, callback-close/revocation and atomic cleanup. A late resolver-ref
mutation failed the same test before the postcallback type guard was moved ahead
of every attribute read. Association alone does not compute paint envelopes or
grant writable geometry authority.

The serializer now accepts only the four module-owned record/field-list pairs:
SourcePathNumbers, DrawnPath, SourceImageNumbers and DrawnImage. It checks the
expected exact type immediately before each record attribute read and before
returning, after the final callback. Selection constructs no tuple/dictionary or
new witness object; the expected type is a reference to a module-owned class.
Its work is16+5*F instead of F for field count F. Recursive freeze charges2work
instead of1 per visited value and rechecks exact Fraction after the128-byte
wrapper reservation before reading numerator/denominator. Byte constructors and
reservations are unchanged, confirmed by repeated actual-size measurement.

The independent review found source/native record subclasses could execute
getters during serialization; the mutated source image record could publish.
Five same-test cases (native, reconciled native, source record, native record and
Fraction) fail before these guards and pass afterward with zero getter calls and
no publication/active residue. Additional last-field callback cases ensure the
final record check is exercised. The cache-reuse unit test now constructs an
actual SourcePathNumbers and uses the owned full field list, preserving its
original equal-value and matrix-identity assertions. Arbitrary custom field lists
are explicitly rejected instead of expanding the closed serializer domain.
