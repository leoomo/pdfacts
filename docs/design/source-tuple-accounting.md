# Evidence tuple control and refusal accounting

This refines successful tuple traversal without removing any comparison, finite
value check, type check, depth bound or representation copy. It uses the existing
64-bit CPython 3.13.5 runtime domain and original valid internal calls.

Both `_freeze` and `_same_native` reserve 64 before reading a tuple length. A
count greater than 256 immediately restores 128 before further large-count
arithmetic, keeping the original 192 plus noncached-counter fee. `_same_native`
restores for a large left tuple before reading the right length. For a small
left tuple, even a much larger right length creates only one length integer,
covered by 64; a mismatch still returns false. Signed zero and nonfinite values
retain their original handling and independent fees.

For a small tuple, length and index increments use cached integers. Payload depth
is at most 32 by the `SourceEvidenceLimits` constructor. In `_freeze`, the later
copy-fee expression `128 + 16*count` needs at most two 32-byte integer temporaries,
covered by 64. List and result-tuple construction retain their own existing
reservation. Iteration uses explicit indices, not a new iterator per member.
For large tuples, all original arithmetic and counter reservations remain due.

## Independent refusal allowance

Reducing parent control fees must not remove the allowance for errors in scalar
children. Four previously unreserved local refusal paths therefore prepay 32768
before creating an exception: excessive depth, matrix-hint depth, nonfinite
floats and unsupported value types. Fraction handling retains its existing
independent reservation and postcallback type check.

The fixed refusal allowance covers the current `_freeze` recursion chain and
its local limit helper/constructor, not arbitrary external caller stacks:

| Named allocation | Conservative allowance |
| --- | ---: |
| At most 34 materialized `_freeze` frames and tracebacks, each at most 512 bytes | 17408 |
| Local exception instance | 256 |
| Exception argument tuple | 128 |
| Three-attribute exception dictionary and keys | 1024 |
| `super`, method wrapper and constructor-call intermediates | 512 |
| Local limit-helper frame and traceback | 512 |
| Local exception-constructor frame allowance | 512 |

The constructor/helper rows total 2944, bounded by an explicit 4096 envelope.
Thus 17408+4096=21504 stays below 32768. The extra space is not credit for any
other operation. Depth 33 can refuse below 33 enclosing tuples, so counting only
32 frames would be insufficient. On this runtime, a frame plus traceback is
bounded by `208 + 8*(co_nlocals + len(co_cellvars) + len(co_freevars) + co_stacksize)`;
a structural test pins this at
512 or less for `_freeze` and its local helper/constructor. Ordinary interpreter
stack handling retains the existing convention; this inventory explicitly
includes frames materialized by these local exceptions.

Each depth refusal captures its `allowed` value before the new reservation
callback and passes that same value to the limit helper. The matrix-hint check
captures after its own work callback. A callback cannot turn an already selected
depth refusal into acceptance by replacing the limit. If refusal prepayment
fails, the caller's original budget exception propagates and no payload returns.

Validation covers 0/1/256/257-member boundaries, equal and unequal native tuples,
signed zero, immutable reuse and changed Fraction representations, maximum-depth
success and depth-33 refusal, all four independently prepaid refusals, denied
prepayment and callback changes to depth limits. This is neither a whole-page
blankness proof nor writing authority.
