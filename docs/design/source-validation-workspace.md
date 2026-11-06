# Batch-local payload validation workspace

Both eager and staged storage still validate every payload before publishing a
batch. They now create one private validator for the batch rather than three
33-element DFS lists for every entry. The staged empty batch creates none.
The independent `_validate_evidence_payload` entry retains its signature and
creates a new validator for each call.

The validator and its lists remain local to the storage call. They are never
placed in the bank, pending state, entries, identity registry or callback
arguments. No caller-supplied mutable workspace is accepted. Reentering storage
creates a separate validator; callbacks have no reference through which to
reenter the same validator. Stack introspection or direct invocation of private
factory results is outside that closed production-call domain. Every successful
DFS clears its parent references. A failed validation aborts the batch, whose
local workspace is discarded. Generation, pending node, ready marker, previous
batches and reference checks at atomic publication remain unchanged.

## Allocation order and amounts

The outer `_make_payload_validator` has no closure cells. It reserves 2048
before calling the inner factory, because `MAKE_CELL` allocates captured cells
at function entry. The inner factory then constructs three lists (960 bytes),
their one-element construction temporaries (192), four cells (at most 160), a
closure tuple (72) and function object (152): a conservative 1536 total below
2048 on the existing pinned runtime. No source-dependent container is retained
outside this one batch.

Each validation reserves 64 once for a possible noncached tuple length. Small
tuple lengths and indices through 256 use cached integers, so that unspent
allowance remains available for later nodes. For a large tuple, reserve 128
before calculating the original `32*count` counter fee. This covers the fee
expression and replenishes the next node's length allowance after the preceding
length integer. The existing large-index counter reservation remains payable.
Every DFS work charge remains in place; no subtree or repeated immutable value
is skipped.

Depth, nonfinite-float and unsupported-type refusals independently reserve 4096
before raising. The selected depth allowance is captured before that callback.
The two possible local validation/wrapper frames are each structurally bounded
by 512 bytes including traceback; the previously established exception/helper
inventory is bounded by 2944, giving at most 3968 below 4096. The factories have
returned before traversal begins, so their frames are not part of this local
exception chain. Arbitrary external caller stacks are not claimed by this
inventory. A failed reservation propagates the caller exception unchanged.

Tests cover cached and large tuple boundaries, exact and one-below budgets,
workspace reuse and parent cleanup, independent reentrant workspaces, every
local refusal and denied payment, callback changes to the depth allowance,
prepayment before the cell factory, frame slots including free variables, and
both production storage paths with two constructed paths. Corrupting the later
payload prevents publication of the entire batch. This optimization neither
changes the authority model nor proves whole-document resource feasibility.
