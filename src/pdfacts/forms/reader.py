"""The forms reader: widgets and fields from QPDF's plain data (issue #39).

Part 1: the computation only — the node table of D7, the facts of one widget
of D4 and D5, and the fields of D8 — as private functions over the plain data
of P1. No engine is called, no check is made (part 2), nothing is public.
"""

from __future__ import annotations

from dataclasses import dataclass

from pdfacts.backends.protocols import FieldEntry, FieldTree, WidgetEntry, Written
from pdfacts.core.geometry import Rect
from pdfacts.core.refs import AnnotationRef, FieldRef
from pdfacts.core.strings import decode_name, decode_text_string
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.forms.model import (
    BorderStyle,
    ChoiceOption,
    DeviceColour,
    Field,
    FieldType,
    Widget,
)

_INVALID = UnknownReason.INVALID
_UNREADABLE = UnknownReason.UNREADABLE

_KIND = {"text": FieldType.TEXT, "Btn": None, "Ch": None, "Sig": FieldType.SIGNATURE}


def _invalid(detail: str) -> Unknown:
    return Unknown(_INVALID, detail)


def _unreadable(detail: str) -> Unknown:
    return Unknown(_UNREADABLE, detail)


@dataclass(frozen=True)
class _NodeTable:
    """D7's table: for every node of the tree, what it is; and the tree."""

    tree: FieldTree | None
    fields: tuple[bool, ...]  # per node index: a field
    terminal: tuple[bool, ...]  # per node index: a terminal field
    invalid: tuple[bool, ...]  # per node index: a structurally invalid field
    by_object: dict[tuple[int, int], int]  # object number and generation -> index


def _nodes(tree: FieldTree | None) -> _NodeTable:
    """The table of D7, computed once per call of widgets() and fields()."""
    if tree is None:
        return _NodeTable(None, (), (), (), {})
    count = len(tree.nodes)
    fields = [False] * count
    for index in tree.roots:
        if index is not None:
            fields[index] = True
    for index, node in enumerate(tree.nodes):
        if b"T" in node.entry.entries or node.entry.kids:
            fields[index] = True
    kid_nodes: list[tuple[int, ...]] = []
    for node in tree.nodes:
        if node.kids is None:
            kid_nodes.append(())
        else:
            kid_nodes.append(tuple(kid for kid in node.kids if kid is not None))
    terminal = [False] * count
    for index in range(count):
        if fields[index]:
            terminal[index] = not any(fields[kid] for kid in kid_nodes[index])
            if tree.nodes[index].kids is None:
                terminal[index] = False
    invalid = [False] * count
    by_object: dict[tuple[int, int], int] = {}
    for index, node in enumerate(tree.nodes):
        if node.entry.object_number is not None:
            by_object[(node.entry.object_number, node.entry.generation)] = index
    # The rules that look at a node's kids, then the ancestors, each once.
    for index, node in enumerate(tree.nodes):
        if not fields[index]:
            continue
        bad = node.reached_twice or not node.parent_matches
        if node.kids is not None:
            mix = any(fields[kid] for kid in kid_nodes[index]) and any(
                not fields[kid] for kid in kid_nodes[index]
            )
            none_kid = any(kid is None for kid in node.kids)
            terminal_bad = terminal[index] and any(
                not tree.nodes[kid].parent_matches or tree.nodes[kid].reached_twice
                for kid in kid_nodes[index]
            )
            bad = bad or mix or none_kid or terminal_bad
        invalid[index] = bad
    # A node is invalid also when one of its ancestors is.
    for index in range(count):
        if not fields[index] or invalid[index]:
            continue
        parent = tree.nodes[index].parent
        while parent is not None:
            if fields[parent] and invalid[parent]:
                invalid[index] = True
                break
            parent = tree.nodes[parent].parent
    return _NodeTable(tree, tuple(fields), tuple(terminal), tuple(invalid), by_object)


def _fields(tree: FieldTree | None) -> tuple[Field, ...] | Unknown:
    """D8's computation: the terminal field nodes, depth first, as facts."""
    nodes = _nodes(tree)
    if tree is None:
        return ()
    if tree.invalid:
        return _invalid("the field tree of /AcroForm is not valid")
    if tree.cut:
        return _unreadable("the field tree is deeper than 32 levels")
    fields = []
    for index, node in enumerate(tree.nodes):
        if not nodes.terminal[index]:
            continue
        # The field chain: the node's entry, then each tree ancestor's.
        chain = [node.entry]
        parent = node.parent
        while parent is not None:
            chain.append(tree.nodes[parent].entry)
            parent = tree.nodes[parent].parent
        entry = node.entry
        ref = _field_ref(entry)
        facts = _field_facts(chain, 0, ref, nodes, index)
        if nodes.invalid[index]:
            facts = {name: _invalid("the field tree is not valid here") for name in facts}
            facts["ref"] = ref
            facts["in_form"] = True
        fields.append(
            Field(
                ref=ref,
                name=facts["name"],
                partial_names=facts["partial_names"],
                type=facts["type"],
                field_flags=facts["field_flags"],
                label=facts["label"],
                value=facts["value"],
                default_value=facts["default_value"],
                options=facts["options"],
                in_form=True,
            )
        )
    return tuple(fields)


def _widget(entry: WidgetEntry, page_index: int, nodes: _NodeTable) -> Widget:
    """D4 and D5: one widget's facts from QPDF's plain data, unchecked."""
    field, export_value = _field_of(entry, nodes)
    values = entry.entries
    return Widget(
        ref=AnnotationRef(
            page_index=page_index,
            annotation_index=entry.annotation.index,
            kind="Widget",
            object_number=entry.annotation.object_number,
            generation=entry.annotation.generation,
        ),
        rect=_rect(values.get(b"Rect")),
        annotation_flags=_flags(values.get(b"F")),
        field=field,
        states=_states(entry),
        state=_state(values.get(b"AS")),
        export_value=export_value,
        border_colour=_colour(entry, b"MK/BC"),
        background_colour=_colour(entry, b"MK/BG"),
        border=_border(entry),
    )


def _field_of(entry: WidgetEntry, nodes: _NodeTable) -> tuple[Field | Unknown, object]:
    """D5: the widget's field and its export value."""
    chain = entry.chain
    terminal = 0 if (b"T" in chain[0].entries or len(chain) == 1) else 1
    field_chain = chain[terminal:]

    # The field as a whole, unknown in order.
    if entry.chain_loops or entry.parent_invalid:
        unknown = _invalid("the /Parent chain of the widget is not usable")
        return unknown, unknown
    if entry.chain_cut:
        unknown = _unreadable("the field chain is longer than 33 dictionaries")
        return unknown, unknown
    head = field_chain[0]
    node_index = None
    if head.object_number is not None:
        node_index = nodes.by_object.get((head.object_number, head.generation))
    if node_index is not None and nodes.tree.nodes[node_index].kids is None:
        unknown = _unreadable("the field tree is deeper than 32 levels here")
        return unknown, unknown
    # The link.
    if not _link_holds(entry, terminal, nodes, node_index):
        unknown = _invalid("the /Parent of the widget does not list it")
        return unknown, unknown

    ref = _field_ref(head)
    facts = _field_facts(field_chain, terminal, ref, nodes, node_index)
    if node_index is not None and nodes.invalid[node_index]:
        for name in (
            "name",
            "partial_names",
            "type",
            "field_flags",
            "label",
            "value",
            "default_value",
            "options",
        ):
            facts[name] = _invalid("the field tree is not valid here")
    field = Field(
        ref=ref,
        name=facts["name"],
        partial_names=facts["partial_names"],
        type=facts["type"],
        field_flags=facts["field_flags"],
        label=facts["label"],
        value=facts["value"],
        default_value=facts["default_value"],
        options=facts["options"],
        in_form=_in_form(nodes, node_index, head),
    )
    return field, _export_value(entry, terminal, field_chain, facts, nodes, node_index)


def _link_holds(
    entry: WidgetEntry, terminal: int, nodes: _NodeTable, node_index: int | None
) -> bool:
    """D5's case 4: the /Parent link that makes the widget's field its field."""
    if terminal == 1:
        kids = entry.parent_kids
        objgen = (entry.annotation.object_number, entry.annotation.generation)
        widget_node = nodes.by_object.get(objgen) if objgen[0] is not None else None
        if not (
            kids is not None
            and kids.all_widgets
            and len(kids.widget_positions.get(objgen, ())) == 1
            and not (widget_node is not None and nodes.fields[widget_node])
        ):
            return False
    if node_index is not None and not nodes.terminal[node_index]:
        return False
    return True


def _is_field_node(nodes: _NodeTable, index: int | None) -> bool:
    return index is not None and nodes.fields[index]


def _field_ref(entry: FieldEntry) -> FieldRef:
    return FieldRef(
        object_number=entry.object_number,
        generation=entry.generation,
    )


def _field_facts(
    chain: list[FieldEntry] | tuple[FieldEntry, ...],
    terminal: int,
    ref: FieldRef,
    nodes: _NodeTable,
    node_index: int | None,
) -> dict:
    """The facts of D5's table for one field chain (its head at index 0)."""
    head = chain[0]
    facts: dict[str, object] = {}

    # Partial names, top first; a period invalidates the name, not the parts.
    partials: list[str] = []
    name_unknown: Unknown | None = None
    partials_unknown = False
    for one in reversed(list(chain)):
        written = one.entries.get(b"T")
        if written is None:
            continue
        if written.kind == "string":
            text = decode_text_string(written.data)
            partials.append(text)
            if "." in text:
                name_unknown = _invalid("a partial name holds a period")
        else:
            partials_unknown = True
            name_unknown = _invalid("a partial name is not a string")
    if partials_unknown:
        facts["partial_names"] = _invalid("a partial name is not a string")
    else:
        facts["partial_names"] = tuple(partials)
    facts["name"] = (
        name_unknown if name_unknown is not None else ".".join(part for part in partials if part)
    )

    # field flags.
    flags: object = 0
    for one in chain:
        written = one.entries.get(b"Ff")
        if written is None:
            continue
        if written.kind == "integer" and 0 <= written.data <= 2**32 - 1:
            flags = written.data
        else:
            flags = _invalid("the /Ff of the field is not a 32-bit integer")
        break

    # type.
    type_: object = _invalid("the field has no usable /FT")
    type_written = None
    for one in chain:
        written = one.entries.get(b"FT")
        if written is None:
            continue
        type_written = written
        break
    if type_written is not None and type_written.kind == "name":
        name = type_written.data
        if name == b"Tx":
            type_ = FieldType.TEXT
        elif name == b"Sig":
            type_ = FieldType.SIGNATURE
        elif name in (b"Btn", b"Ch"):
            bit = None
            if isinstance(flags, int):
                bit = flags
            if name == b"Btn":
                if bit is None:
                    type_ = _invalid("the field flags are unknown")
                elif bit & 65536 and bit & 32768:
                    type_ = _invalid("the radio and pushbutton bits are both set")
                elif bit & 65536:
                    type_ = FieldType.PUSHBUTTON
                elif bit & 32768:
                    type_ = FieldType.RADIO
                else:
                    type_ = FieldType.CHECKBOX
            else:
                if bit is None:
                    type_ = _invalid("the field flags are unknown")
                elif bit & 131072:
                    type_ = FieldType.COMBO_BOX
                else:
                    type_ = FieldType.LIST_BOX
        else:
            type_ = _invalid("the /FT of the field is not one of the known names")
    elif type_written is not None:
        type_ = _invalid("the /FT of the field is not a name")
    elif node_index is not None and nodes.invalid[node_index]:
        type_ = _invalid("the field tree is not valid here")
    facts["type"] = type_
    facts["field_flags"] = flags

    # label.
    written = head.entries.get(b"TU")
    if written is None:
        facts["label"] = None
    elif written.kind == "string":
        facts["label"] = decode_text_string(written.data)
    else:
        facts["label"] = _invalid("the /TU of the field is not a string")

    # value and default value.
    facts["value"] = _value(chain, b"V", type_, FieldType if isinstance(type_, FieldType) else None)
    facts["default_value"] = _value(chain, b"DV", type_, None)

    # options.
    opt = head.entries.get(b"Opt")
    if isinstance(type_, FieldType) and type_ in (FieldType.COMBO_BOX, FieldType.LIST_BOX):
        facts["options"] = _options(opt)
    elif isinstance(type_, FieldType):
        facts["options"] = None
    elif opt is None:
        facts["options"] = None
    else:
        facts["options"] = type_ if isinstance(type_, Unknown) else None
    return facts


def _value(chain, key: bytes, type_, _) -> object:
    """D5's value rule for one of /V and /DV."""
    written = None
    for one in chain:
        written = one.entries.get(key)
        if written is not None:
            break
    if written is None:
        return None
    if not isinstance(type_, FieldType):
        if isinstance(type_, Unknown):
            return type_
        return _invalid("the type of the field is unknown")
    kind = written.kind
    if type_ is FieldType.TEXT:
        if kind == "string":
            return decode_text_string(written.data)
        if kind == "stream":
            return _unreadable("the value of the field is a stream")
        return _invalid("the value of the field is not a string")
    if type_ in (FieldType.COMBO_BOX, FieldType.LIST_BOX):
        if kind == "string":
            return (decode_text_string(written.data),)
        if kind == "array":
            parts = []
            for item in written.data:
                if item.kind != "string":
                    return _invalid("the value of the field is not an array of strings")
                parts.append(decode_text_string(item.data))
            return tuple(parts)
        return _invalid("the value of the field is not a string or an array of strings")
    if type_ in (FieldType.CHECKBOX, FieldType.RADIO):
        if kind == "name":
            return decode_name(written.data)
        return _invalid("the value of the field is not a name")
    if type_ is FieldType.PUSHBUTTON:
        return _invalid("a pushbutton has no value")
    return _unreadable("pdfacts does not read the value of a signature field")


def _options(opt: Written | None) -> object:
    if opt is None:
        return None
    if opt.kind != "array":
        return _invalid("the /Opt of the field is not an array")
    options = []
    for item in opt.data:
        if item.kind == "string":
            text = decode_text_string(item.data)
            options.append(ChoiceOption(text, text))
        elif (
            item.kind == "array"
            and len(item.data) == 2
            and all(part.kind == "string" for part in item.data)
        ):
            options.append(
                ChoiceOption(
                    decode_text_string(item.data[0].data), decode_text_string(item.data[1].data)
                )
            )
        else:
            return _invalid("an entry of /Opt is neither a string nor a pair of strings")
    return tuple(options)


def _in_form(nodes: _NodeTable, node_index: int | None, head: FieldEntry) -> object:
    """D5's in_form rule."""
    if node_index is not None:
        return True
    if nodes.tree is None:
        return False
    if nodes.tree.invalid or nodes.tree.cut:
        return _unreadable("the field tree is not usable for this field")
    if head.object_number is None:
        return _unreadable("the field has no object number")
    return False


def _export_value(entry: WidgetEntry, terminal: int, chain, facts, nodes, node_index) -> object:
    """D5's export value, in its order."""
    if node_index is not None and nodes.invalid[node_index]:
        return _invalid("the field tree is not valid here")
    if isinstance(facts["type"], Unknown):
        return facts["type"]
    type_ = facts["type"]
    if type_ not in (FieldType.CHECKBOX, FieldType.RADIO):
        return None
    opt = None
    for one in chain:
        opt = one.entries.get(b"Opt")
        if opt is not None:
            break
    if opt is None:
        return None
    if terminal == 0:
        if chain[0].kids:
            return _invalid("the widget is a widget-field with kids")
        index = 0
    else:
        kids = entry.parent_kids
        if kids is None:
            return _invalid("the /Kids of the widget's parent is not known")
        objgen = (entry.annotation.object_number, entry.annotation.generation)
        positions = kids.widget_positions.get(objgen, ())
        if len(positions) != 1:
            return _invalid("the widget is not once among its parent's widget kids")
        index = positions[0]
    if opt.kind != "array":
        return _invalid("the /Opt of the field is not an array")
    if index >= len(opt.data):
        return _invalid("the /Opt of the field has no entry for this widget")
    item = opt.data[index]
    if item.kind != "string":
        return _invalid("the /Opt entry of this widget is not a string")
    return decode_text_string(item.data)


def _rect(written: Written | None) -> Rect | Unknown:
    if written is None or written.kind != "array" or len(written.data) != 4:
        return _invalid("the /Rect of the widget is not an array of four numbers")
    numbers = []
    for item in written.data:
        if item.kind not in ("integer", "real"):
            return _invalid("the /Rect of the widget is not an array of four numbers")
        numbers.append(float(item.data))
    try:
        return Rect(*numbers)
    except (ValueError, TypeError):
        return _invalid("the /Rect of the widget is not usable")


def _flags(written: Written | None) -> int | Unknown:
    if written is None:
        return 0
    if written.kind == "integer" and 0 <= written.data <= 2**32 - 1:
        return written.data
    return _invalid("the /F of the widget is not a 32-bit integer")


def _states(entry: WidgetEntry) -> tuple | None | Unknown:
    if entry.states_invalid:
        return _invalid("the /AP of the widget is not usable")
    if entry.states is None:
        return None
    names = [decode_name(key) for key in entry.states]
    ordered = tuple(sorted(names, key=lambda name: name.raw))
    return ordered


def _state(written: Written | None) -> object:
    if written is None:
        return None
    if written.kind == "name":
        return decode_name(written.data)
    return _invalid("the /AS of the widget is not a name")


def _colour(entry: WidgetEntry, key: bytes) -> DeviceColour | None | Unknown:
    if entry.mk_invalid:
        return _invalid("the /MK of the widget is not a dictionary")
    written = entry.entries.get(key)
    if written is None:
        return None
    if written.kind != "array":
        return _invalid("the colour of the widget is not an array")
    numbers = []
    for item in written.data:
        if item.kind not in ("integer", "real"):
            return _invalid("the colour of the widget is not an array of numbers")
        numbers.append(float(item.data))
    if len(numbers) not in (0, 1, 3, 4):
        return _invalid("the colour of the widget has not 0, 1, 3 or 4 components")
    for number in numbers:
        if not 0 <= number <= 1:
            return _invalid("a component of the colour is not between 0 and 1")
    return DeviceColour(tuple(numbers))


def _border(entry: WidgetEntry) -> BorderStyle | None | Unknown:
    if entry.bs_invalid:
        return _invalid("the /BS of the widget is not a dictionary")
    width_written = entry.entries.get(b"BS/W")
    style_written = entry.entries.get(b"BS/S")
    if width_written is None and style_written is None:
        return None
    width: float | None = None
    if width_written is not None:
        if width_written.kind not in ("integer", "real"):
            return _invalid("the width of the border is not a number")
        width = float(width_written.data)
        if width < 0:
            return _invalid("the width of the border is negative")
    style = None
    if style_written is not None:
        if style_written.kind != "name":
            return _invalid("the style of the border is not a name")
        style = decode_name(style_written.data)
    return BorderStyle(width, style)
