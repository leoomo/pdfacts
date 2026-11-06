# Bounded source-state transition accounting

An explicit private `consumer_short_states` option selects only `BT`, `ET` and
`EMC`. It requires the existing five-argument endpoint channel. The default,
number-only and name modes retain their prior operator fees. Both selector and
consumer use the same module-owned frozen operator set, exact strings, a short
valid integer span, and original content length below 2**30. Mode and endpoint
references are captured before callbacks. The actual fee flag survives adapter
changes; leaving the admitted domains restores 192 exactly once.

The 64-byte control bound relies on valid parser state, not on a fixed marked
nesting limit. Instruction and marked-depth counters cannot exceed the number
of tokens in the original compact source. Incrementing the instruction counter
and decrementing the marked-depth counter each needs at most one 32-byte compact
integer. `max_marked_depth` is caller-configurable and is not assumed to be 32.
BT/ET only increment the instruction counter. Span lengths and argument counts
remain cached integers. The selected success paths create no generator,
iterator, closure, argument container or source-dependent string. Each still
reaches the existing independent 64-byte reservation before constructing the
new empty argument list. BT/ET never emit a text record.

Other operators retain the original fee: their paths may create generators,
iterate text arrays, perform Fraction arithmetic or restore compound state.
Short lexical spelling alone is insufficient to admit them to this bound.

Refusals restore the omitted 192 before constructing an exception:

- source instruction limit, with both found and allowed captured before payment;
- pending clip, or a non-text transition while text is active;
- EMC operands or marked-depth underflow;
- BT/ET shape, nested text or outside-text state at `ExactTextState.apply`'s
  actual refusal points, not merely a duplicate caller precheck.

The text `require` helper receives the already selected condition and actual fee
flag. A reservation callback cannot change that condition into success. It
constructs no new per-call closure or container. A direct text caller leaving
the two text-state operators restores the full fee before the original path.
Existing default text callers pay no additional fee. Failed prepayment keeps
the caller exception unchanged. Numeric, lexical, resource and semantic checks,
work charges and resource capacities remain mandatory and unchanged.

Constructed tests cover selection and old modes, callback mode mutation,
one-below fees, each refusal and denied restoration, source-instruction limit
mutation, changed kinds/spans/operators, direct text refusal mutation, and
258-to-257 / 257-to-256 marked-depth transitions with noncached instruction
counters. This does not prove whole-document resource feasibility or writing
authority.
