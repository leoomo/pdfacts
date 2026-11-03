"""Finite text state for path-only source emission accounting."""

from fractions import Fraction

from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit

TEXT_COUNTS = {"BT": 0, "ET": 0, "Tf": 2, "Tm": 6, "Td": 2, "Tw": 1, "Tj": 1, "TJ": 1}


def require(condition, reason):
    if not condition:
        raise UnsupportedExactSource(reason)


class ExactTextState:
    def __init__(self, resources, bank, limits):
        self.resources = resources
        self.limits = limits
        self.bank = bank
        self.active = False
        self.font = None
        self.size = None
        self.spacing = Fraction(0)

    def snapshot(self):
        return self.font, self.size, self.spacing

    def restore(self, state):
        self.font, self.size, self.spacing = state

    def apply(self, operator, args, source_operand=None):
        require(len(args) == TEXT_COUNTS[operator], "text_operator_shape")
        if operator == "BT":
            require(not self.active, "text_nested")
            self.active = True
            return None
        require(self.active, "text_outside_object")
        if operator == "ET":
            self.active = False
        elif operator == "Tf":
            require(isinstance(args[1], Fraction) and args[1] > 0, "text_font_size")
            self.font = self.resources.font_encoding(args[0], self.bank)
            self.size = args[1]
        elif operator == "Td":
            self.bank.charge(2)
            require(
                isinstance(args[0], Fraction) and isinstance(args[1], Fraction),
                "text_numeric_operand",
            )
        elif operator in ("Tm", "Tw"):
            require(all(isinstance(value, Fraction) for value in args), "text_numeric_operand")
            if operator == "Tw":
                self.spacing = args[0]
        else:
            require(self.font is not None, "text_font_unset")
            if operator == "Tj":
                require(type(args[0]) is bytes, "text_show_string")
                items = args
            else:
                require(
                    type(args[0]) is tuple,
                    "text_show_array",
                )
                check_source_limit(
                    "text_array_items", len(args[0]), self.limits.max_text_array_items
                )
                items = args[0]
            require(
                isinstance(source_operand, tuple)
                and len(source_operand) == 2
                and source_operand[0] is args[0],
                "text_operand_span",
            )
            source_length = source_operand[1]
            require(
                type(source_length) is int and 0 < source_length <= self.limits.max_content_bytes,
                "text_operand_span",
            )
            self.bank.charge(len(items))
            remaining = source_length
            emitted = False
            for item in items:
                if operator == "TJ":
                    if item.kind == "number":
                        continue
                    require(item.kind == "string", "text_array_item")
                    value = item.value
                else:
                    value = item
                require(type(value) is bytes, "text_array_item")
                require(len(value) <= self.limits.max_token_bytes, "text_string_size")
                remaining -= len(value)
                require(remaining >= 0, "text_operand_span")
                self.bank.charge(len(value))
                require(self.font != "identity_h" or len(value) % 2 == 0, "text_identity_h_odd")
                emitted = emitted or bool(value)
            return 1 if emitted else 0
        return None
