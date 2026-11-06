# Explicit short-name callback accounting

`consumer_short_names` is a private opt-in in addition to the existing
`consumer_short_numbers` five-argument endpoint channel. It requires that
channel. Default, endpoint-only and number-only callers keep their prior name
fees and callback shapes. The source ledger explicitly opts in to both domains.

The new discount selects only `kind == "name"`, exact `str` values of length
1..8, an exact integer source span of length 1..8 inside the original content,
and content length strictly below 2**30. Number selection remains unchanged.
Operators, arrays, strings, escaped-name bytes and longer names retain the full
256-byte callback reservation. The name-option reference, endpoint and content
length are captured before the first reservation callback.

The selector and consumer use no newly constructed tuple, closure, iterator or
string. Short string lengths are cached integers. The compact endpoint
subtraction produces the cached span length in the eligible domain. The
consumer then performs span, kind and operand-count checks and immediately
reaches the existing independent 64-byte append reservation. The argument list
holds at most six elements, so its growth needs at most eight pointer slots.
That allocation remains independently paid. The 64-byte control reservation
does not claim to cover the operator or text-state call graph.

The fifth boolean continues to describe the fee actually paid, not the current
shape after a callback adapter. Invalid spans restore 192 before refusal. An
adapter that moves the value or kind outside both eligible domains restores 192
and clears the flag before proceeding along the original full-fee path.
`non_numeric_operand` failures restore 192 only while the actual discount flag
is still set. Thus invalid spans and a seventh name operand keep their original
error allowance, while fallback values are not charged restoration twice.
Denied restoration propagates the caller's original exception and appends no
operand. Lexical scanning, name decoding, source-language rules, all resource
validation, work charges and capacities are unchanged.

Constructed tests cover compatibility with all old modes, both selected kinds,
eight/nine-byte boundaries, escaped names, option mutation during reservation,
one-below budgets, altered spans/kinds/values, operand overflow and denied
restoration. Observing a name append is not proof that a resource name is valid;
the resource consumer still performs its original validation.
