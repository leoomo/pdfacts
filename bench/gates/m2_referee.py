"""A referee for the M2 gate: the widgets of a file read from its dictionaries.

It reads one engine, QPDF through pikepdf's raw objects, and implements the
rules of the conventions ("Forms") and of the issues P1 (widget chain, the
/Kids of a widget's /Parent, the field tree walk), P3 (text strings and
names) and P4 (D4, D5, D7, D8). It imports pikepdf and the standard library
only. It was written clean-room, from the texts of issues #36, #38, #39 and
#40, docs/conventions.md and tests/synthetic, by an author who had not read
the source of pdfacts (review of #53), so that it does not share its
readings.

`read(data)` gives the widgets of every page, in page order and /Annots order,
each a dictionary of plain values, and the number of terminal fields of the
tree of /AcroForm /Fields. A name is {"name": hex of its bytes}, an unknown
fact {"unknown": reason}, a float float(value) + 0.0.
"""

from __future__ import annotations

import io
import math
from decimal import Decimal

import pikepdf

INVALID = "invalid-in-file"
UNREADABLE = "unreadable"
NOT_READ = "not-read"

CHAIN_LIMIT = 33  # dictionaries in a widget's chain, its own included
TREE_DEPTH = 32  # levels below a root whose /Kids are still followed

FACTS = (
    "object",
    "rect",
    "annotation_flags",
    "field_ref",
    "name",
    "partial_names",
    "type",
    "field_flags",
    "label",
    "value",
    "default_value",
    "options",
    "in_form",
    "states",
    "state",
    "export_value",
    "border_colour",
    "background_colour",
    "border",
)
# The facts that are the field's: each is the field's unknown when the field
# is unknown as a whole.
FIELD_FACTS = FACTS[3:13] + ("export_value",)

_FLAG_LIMIT = 2**32 - 1
_PUSHBUTTON = 1 << 16  # bit 17
_RADIO = 1 << 15  # bit 16
_COMBO = 1 << 17  # bit 18


def _unknown(reason: str) -> dict:
    return {"unknown": reason}


def _is_unknown(value) -> bool:
    return isinstance(value, dict) and "unknown" in value


# ---------------------------------------------------------------------------
# Text strings and names (P3, decisions 2 and 3)

_PDFDOC = [chr(code) for code in range(256)]
_PDFDOC[0x18:0x20] = "˘ˇˆ˙˝˛˚˜"
_PDFDOC[0x80:0x9F] = "•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž"
_PDFDOC[0xA0] = "€"
for _code in (0x7F, 0x9F, 0xAD):
    _PDFDOC[_code] = "�"


def _strip_escapes(text: str) -> str:
    """Remove each language escape: U+001B, what follows, up to the next U+001B."""
    out = []
    at = 0
    while True:
        start = text.find("\x1b", at)
        if start < 0:
            out.append(text[at:])
            break
        out.append(text[at:start])
        end = text.find("\x1b", start + 1)
        if end < 0:
            break  # not closed: it and all that follows go
        at = end + 1
    return "".join(out)


def text_string(data: bytes) -> str:
    """A text string decoded as ISO 32000-2 7.9.2.2, UTF-16LE too; never fails."""
    if type(data) is not bytes:
        raise TypeError("a text string is bytes")
    if data[:2] == b"\xfe\xff":
        return _strip_escapes(data[2:].decode("utf-16-be", "replace"))
    if data[:2] == b"\xff\xfe":
        return _strip_escapes(data[2:].decode("utf-16-le", "replace"))
    if data[:3] == b"\xef\xbb\xbf":
        return _strip_escapes(data[3:].decode("utf-8", "replace"))
    return "".join(_PDFDOC[byte] for byte in data)


def name_bytes(name) -> bytes:
    """The bytes of a name object without its slash, each #xx resolved."""
    syntax = name.unparse(resolved=True)
    out = bytearray()
    i = 1
    while i < len(syntax):
        if syntax[i] == 0x23:
            out.append(int(syntax[i + 1 : i + 3], 16))
            i += 3
        else:
            out.append(syntax[i])
            i += 1
    return bytes(out)


def key_bytes(key: str) -> bytes:
    """The bytes of a dictionary key as pikepdf gives it, without the slash."""
    return key.encode("utf-8", "surrogateescape")[1:]


def _plain_name(raw: bytes) -> dict:
    return {"name": raw.hex()}


# ---------------------------------------------------------------------------
# Kinds of objects


def _is_dictionary(value) -> bool:
    """A dictionary that is not a stream."""
    return isinstance(value, pikepdf.Dictionary) and not isinstance(value, pikepdf.Stream)


def _is_int(value) -> bool:
    return type(value) is int


def _number(value) -> float | None:
    """A finite number as a float (negative zero as zero), else None."""
    if _is_int(value):
        return float(value) + 0.0
    if isinstance(value, Decimal):
        result = float(value)
        if math.isfinite(result):
            return result + 0.0
    return None


def _ref(value) -> tuple[int, int] | None:
    """Object number and generation of an indirect object, None for a direct one."""
    if isinstance(value, pikepdf.Object) and value.is_indirect:
        return tuple(value.objgen)
    return None


def _get(dictionary, key: str):
    """An entry; None when absent or null (an explicit null counts as absent)."""
    return dictionary.get(key)


def _has(dictionary, key: str) -> bool:
    return _get(dictionary, key) is not None


def _is_name(value, raw: bytes) -> bool:
    return isinstance(value, pikepdf.Name) and name_bytes(value) == raw


def _is_widget(value) -> bool:
    return _is_dictionary(value) and _is_name(_get(value, "/Subtype"), b"Widget")


# ---------------------------------------------------------------------------
# The field tree (P1, decision 6) and its node table (P4, D7)


class _Node:
    __slots__ = (
        "dictionary",
        "ref",
        "kids",
        "parent",
        "parent_matches",
        "reached_twice",
        "has_t",
        "has_kids",
        "field",
        "terminal",
        "invalid",
    )

    def __init__(self, dictionary, parent: int | None, parent_matches: bool):
        self.dictionary = dictionary
        self.ref = _ref(dictionary)
        self.kids: tuple[int | None, ...] | None = ()
        self.parent = parent
        self.parent_matches = parent_matches
        self.reached_twice = False
        self.has_t = _has(dictionary, "/T")
        self.has_kids = isinstance(_get(dictionary, "/Kids"), pikepdf.Array)
        self.field = False
        self.terminal = False
        self.invalid = False


class _Tree:
    """The walk of /AcroForm /Fields, and the node table on it."""

    def __init__(self, pdf: pikepdf.Pdf):
        self.present = False  # the catalog has /AcroForm
        self.invalid = False
        self.cut = False
        self.nodes: list[_Node] = []
        self.roots: tuple[int | None, ...] = ()
        self.by_ref: dict[tuple[int, int], int] = {}
        self._seen: dict[tuple, int] = {}
        self._arrays_done: dict[tuple[int, int], tuple[int | None, ...]] = {}
        self._arrays_walking: dict[tuple[int, int], int] = {}
        self._arrays_met_again: set[tuple[int, int]] = set()

        acroform = _get(pdf.Root, "/AcroForm")
        if acroform is None:
            return
        self.present = True
        if not _is_dictionary(acroform):
            self.invalid = True
            return
        fields = _get(acroform, "/Fields")
        if not isinstance(fields, pikepdf.Array):
            self.invalid = True
            return
        fields_ref = _ref(fields)
        holder = ("array", fields_ref) if fields_ref is not None else ("fields",)
        if fields_ref is not None:
            self._arrays_walking[fields_ref] = 1
        self.roots = tuple(
            self._entry(item, holder, position, None, 0) for position, item in enumerate(fields)
        )
        if fields_ref is not None:
            self._arrays_walking.pop(fields_ref)
        if any(index is None for index in self.roots):
            self.invalid = True
        for array in self._arrays_met_again:
            for index in self._arrays_done[array]:
                if index is not None:
                    self.nodes[index].reached_twice = True
        self._table()

    def _entry(self, item, holder: tuple, position: int, parent: int | None, level: int):
        """The node index of one entry of /Fields or /Kids; None for no dictionary."""
        if not _is_dictionary(item):
            return None
        ref = _ref(item)
        identity = ("object", ref) if ref is not None else (holder, position)
        if identity in self._seen:
            index = self._seen[identity]
            self.nodes[index].reached_twice = True
            return index
        written_parent = _get(item, "/Parent")
        if parent is None:
            matches = written_parent is None
        else:
            parent_ref = self.nodes[parent].ref
            matches = (
                parent_ref is not None
                and _is_dictionary(written_parent)
                and _ref(written_parent) == parent_ref
            )
        node = _Node(item, parent, matches)
        index = len(self.nodes)
        self.nodes.append(node)
        self._seen[identity] = index
        if ref is not None:
            self.by_ref[ref] = index
        kids = _get(item, "/Kids")
        if isinstance(kids, pikepdf.Array):
            if level >= TREE_DEPTH:
                node.kids = None
                self.cut = True
            else:
                node.kids = self._kids(kids, index, level + 1)
        return index

    def _kids(self, kids, owner: int, level: int) -> tuple[int | None, ...]:
        ref = _ref(kids)
        if ref is not None:
            if ref in self._arrays_done and ref not in self._arrays_walking:
                self._arrays_met_again.add(ref)
                return self._arrays_done[ref]
            holder = ("array", ref)
            self._arrays_walking[ref] = self._arrays_walking.get(ref, 0) + 1
        else:
            holder = ("node", owner)
        result = tuple(
            self._entry(item, holder, position, owner, level) for position, item in enumerate(kids)
        )
        if ref is not None:
            self._arrays_walking[ref] -= 1
            if not self._arrays_walking[ref]:
                del self._arrays_walking[ref]
            self._arrays_done.setdefault(ref, result)
        return result

    def _table(self) -> None:
        roots = {index for index in self.roots if index is not None}
        for index, node in enumerate(self.nodes):
            node.field = index in roots or node.has_t or node.has_kids
        for node in self.nodes:
            if node.field and node.kids is not None:
                node.terminal = not any(
                    self.nodes[kid].field for kid in node.kids if kid is not None
                )
        # Nodes are in depth-first order: a parent comes before its kids.
        for node in self.nodes:
            if not node.field:
                continue
            invalid = node.reached_twice or not node.parent_matches
            if not invalid and node.kids is not None:
                kids = [self.nodes[kid] for kid in node.kids if kid is not None]
                if len(kids) != len(node.kids):
                    invalid = True
                elif any(kid.field for kid in kids) and not all(kid.field for kid in kids):
                    invalid = True
                elif node.terminal and any(
                    not kid.parent_matches or kid.reached_twice for kid in kids
                ):
                    invalid = True
            if not invalid and node.parent is not None and self.nodes[node.parent].invalid:
                invalid = True
            node.invalid = invalid

    def node(self, ref: tuple[int, int] | None) -> _Node | None:
        if ref is None or ref not in self.by_ref:
            return None
        return self.nodes[self.by_ref[ref]]

    def field_count(self) -> int | dict:
        """D8: 0 without /AcroForm, the unknown of an invalid or cut tree, else the count."""
        if not self.present:
            return 0
        if self.invalid:
            return _unknown(INVALID)
        if self.cut:
            return _unknown(UNREADABLE)
        return sum(1 for node in self.nodes if node.field and node.terminal)


# ---------------------------------------------------------------------------
# A widget's chain and the /Kids of its /Parent (P1, decision 5)


class _Chain:
    __slots__ = ("dictionaries", "loops", "cut", "parent_invalid")

    def __init__(self, widget):
        self.dictionaries = [widget]
        self.loops = self.cut = self.parent_invalid = False
        seen = {_ref(widget)} - {None}
        current = widget
        while True:
            parent = _get(current, "/Parent")
            if parent is None:
                break
            if not _is_dictionary(parent):
                self.parent_invalid = True
                break
            if len(self.dictionaries) >= CHAIN_LIMIT:
                self.cut = True
                break
            ref = _ref(parent)
            if ref is not None and ref in seen:
                self.loops = True
                break
            self.dictionaries.append(parent)
            if ref is not None:
                seen.add(ref)
            current = parent


class _ParentKids:
    __slots__ = ("positions", "all_widgets")

    def __init__(self, kids):
        self.positions: dict[tuple[int, int], list[int]] = {}
        self.all_widgets = True
        widget_index = 0
        for item in kids:
            if not _is_widget(item):
                self.all_widgets = False
                continue
            ref = _ref(item)
            if ref is not None:
                self.positions.setdefault(ref, []).append(widget_index)
            widget_index += 1


def _parent_kids(widget) -> _ParentKids | None:
    parent = _get(widget, "/Parent")
    if not _is_dictionary(parent) or _ref(parent) is None:
        return None
    kids = _get(parent, "/Kids")
    if not isinstance(kids, pikepdf.Array):
        return None
    return _ParentKids(kids)


# ---------------------------------------------------------------------------
# The facts of the widget's own dictionary (P4, D4)


def _rect(widget):
    rect = _get(widget, "/Rect")
    if not isinstance(rect, pikepdf.Array) or len(rect) != 4:
        return _unknown(INVALID)
    numbers = [_number(item) for item in rect]
    if any(number is None for number in numbers):
        return _unknown(INVALID)
    x0, y0, x1, y1 = numbers
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _flags(value, absent):
    if value is None:
        return absent
    if _is_int(value) and 0 <= value <= _FLAG_LIMIT:
        return value
    return _unknown(INVALID)


def _states(widget):
    appearance = _get(widget, "/AP")
    if appearance is None:
        return None
    if not _is_dictionary(appearance):
        return _unknown(INVALID)
    normal = _get(appearance, "/N")
    if normal is None or isinstance(normal, pikepdf.Stream):
        return None
    if not _is_dictionary(normal):
        return _unknown(INVALID)
    return [_plain_name(raw) for raw in sorted(key_bytes(key) for key in normal)]


def _state(widget):
    state = _get(widget, "/AS")
    if state is None:
        return None
    if isinstance(state, pikepdf.Name):
        return _plain_name(name_bytes(state))
    return _unknown(INVALID)


def _colour(value):
    if value is None:
        return None
    if not isinstance(value, pikepdf.Array) or len(value) not in (0, 1, 3, 4):
        return _unknown(INVALID)
    components = [_number(item) for item in value]
    if any(c is None or not 0 <= c <= 1 for c in components):
        return _unknown(INVALID)
    return components


def _colours(widget):
    characteristics = _get(widget, "/MK")
    if characteristics is None:
        return None, None
    if not _is_dictionary(characteristics):
        return _unknown(INVALID), _unknown(INVALID)
    return _colour(_get(characteristics, "/BC")), _colour(_get(characteristics, "/BG"))


def _border(widget):
    style = _get(widget, "/BS")
    if style is None:
        return None
    if not _is_dictionary(style):
        return _unknown(INVALID)
    width, kind = _get(style, "/W"), _get(style, "/S")
    if width is None and kind is None:
        return None
    if width is not None:
        width = _number(width)
        if width is None or width < 0:
            return _unknown(INVALID)
    if kind is not None:
        if not isinstance(kind, pikepdf.Name):
            return _unknown(INVALID)
        kind = _plain_name(name_bytes(kind))
    return [width, kind]


# ---------------------------------------------------------------------------
# The field of a widget (P4, D5)


def _first(chain, key: str):
    """The first entry of `key` up the field chain, None when no entry has it."""
    for dictionary in chain:
        value = _get(dictionary, key)
        if value is not None:
            return value
    return None


def _field_type(chain, flags):
    """The FieldType's value, or the unknown."""
    kind = _first(chain, "/FT")
    if _is_unknown(flags) or not isinstance(kind, pikepdf.Name):
        return _unknown(INVALID)
    raw = name_bytes(kind)
    if raw == b"Btn":
        if flags & _PUSHBUTTON and flags & _RADIO:
            return _unknown(INVALID)
        if flags & _PUSHBUTTON:
            return "pushbutton"
        if flags & _RADIO:
            return "radio"
        return "checkbox"
    if raw == b"Tx":
        return "text"
    if raw == b"Ch":
        return "combo-box" if flags & _COMBO else "list-box"
    if raw == b"Sig":
        return "signature"
    return _unknown(INVALID)


def _value(value, kind):
    if value is None:
        return None
    if _is_unknown(kind):
        return dict(kind)
    if kind == "text":
        if isinstance(value, pikepdf.String):
            return text_string(bytes(value))
        if isinstance(value, pikepdf.Stream):
            return _unknown(NOT_READ)  # a text field's value written as a stream is not read
        return _unknown(INVALID)
    if kind in ("combo-box", "list-box"):
        if isinstance(value, pikepdf.String):
            return [text_string(bytes(value))]
        if isinstance(value, pikepdf.Array) and all(
            isinstance(item, pikepdf.String) for item in value
        ):
            return [text_string(bytes(item)) for item in value]
        return _unknown(INVALID)
    if kind in ("checkbox", "radio"):
        if isinstance(value, pikepdf.Name):
            return _plain_name(name_bytes(value))
        return _unknown(INVALID)
    if kind == "pushbutton":
        return _unknown(INVALID)  # a pushbutton has no value
    return _unknown(NOT_READ)  # the value of a signature field is not read


def _options(terminal, kind):
    options = _get(terminal, "/Opt")
    if options is None:
        return None
    if _is_unknown(kind):
        return dict(kind)
    if kind not in ("combo-box", "list-box"):
        return None
    if not isinstance(options, pikepdf.Array):
        return _unknown(INVALID)
    out = []
    for item in options:
        if isinstance(item, pikepdf.String):
            text = text_string(bytes(item))
            out.append([text, text])
        elif (
            isinstance(item, pikepdf.Array)
            and len(item) == 2
            and all(isinstance(part, pikepdf.String) for part in item)
        ):
            out.append([text_string(bytes(item[0])), text_string(bytes(item[1]))])
        else:
            return _unknown(INVALID)
    return out


def _names(chain):
    """partial_names and name."""
    partial = []
    for dictionary in reversed(chain):
        t = _get(dictionary, "/T")
        if t is None:
            continue
        if not isinstance(t, pikepdf.String):
            return _unknown(INVALID), _unknown(INVALID)
        partial.append(text_string(bytes(t)))
    if any("." in part for part in partial):
        return partial, _unknown(INVALID)
    return partial, ".".join(part for part in partial if part)


def _in_form(terminal, tree: _Tree):
    node = tree.node(_ref(terminal))
    if node is not None and node.field and node.terminal:
        return True
    if not tree.present:
        return False
    if tree.invalid or tree.cut or _ref(terminal) is None:
        return _unknown(UNREADABLE)
    return False


def _export_value(field_chain, kind, terminal_index, widget, chain0, parent_kids):
    if _is_unknown(kind):
        return dict(kind)
    if kind not in ("checkbox", "radio"):
        return None
    options = _first(field_chain, "/Opt")
    if options is None:
        return None
    if terminal_index == 0:
        if isinstance(_get(chain0, "/Kids"), pikepdf.Array):
            return _unknown(INVALID)  # a widget-field with kids
        position = 0
    else:
        # The link holds, so the widget has exactly one position.
        (position,) = parent_kids.positions[_ref(widget)]
    if not isinstance(options, pikepdf.Array) or position >= len(options):
        return _unknown(INVALID)
    entry = options[position]
    if not isinstance(entry, pikepdf.String):
        return _unknown(INVALID)
    return text_string(bytes(entry))


def _field_facts(widget, tree: _Tree) -> dict:
    """field_ref to in_form, and export_value (D5)."""
    chain = _Chain(widget)
    dictionaries = chain.dictionaries
    terminal_index = 0 if _has(dictionaries[0], "/T") or len(dictionaries) == 1 else 1

    whole = None
    if chain.loops or chain.parent_invalid:
        whole = _unknown(INVALID)
    elif chain.cut:
        whole = _unknown(UNREADABLE)
    else:
        terminal = dictionaries[terminal_index]
        node = tree.node(_ref(terminal))
        parent_kids = _parent_kids(widget) if terminal_index == 1 else None
        if node is not None and node.kids is None:
            whole = _unknown(UNREADABLE)  # the tree is deeper than 32 levels here
        else:
            holds = True
            if terminal_index == 1:
                widget_node = tree.node(_ref(widget))
                holds = (
                    parent_kids is not None
                    and parent_kids.all_widgets
                    and len(parent_kids.positions.get(_ref(widget), ())) == 1
                    and not (widget_node is not None and widget_node.field)
                )
            if holds and node is not None:
                holds = node.field and node.terminal
            if not holds:
                whole = _unknown(INVALID)
    if whole is not None:
        return {fact: dict(whole) for fact in FIELD_FACTS}

    field_chain = dictionaries[terminal_index:]
    terminal = field_chain[0]
    ref = _ref(terminal)
    facts = {"field_ref": list(ref) if ref is not None else None}
    facts["in_form"] = _in_form(terminal, tree)
    node = tree.node(ref)
    if node is not None and node.invalid:
        for fact in FIELD_FACTS:
            if fact not in facts:
                facts[fact] = _unknown(INVALID)  # the field tree is not valid here
        return facts

    facts["partial_names"], facts["name"] = _names(field_chain)
    flags = _flags(_first(field_chain, "/Ff"), 0)
    facts["field_flags"] = flags
    kind = _field_type(field_chain, flags)
    facts["type"] = kind
    label = _get(terminal, "/TU")
    if label is None:
        facts["label"] = None
    elif isinstance(label, pikepdf.String):
        facts["label"] = text_string(bytes(label))
    else:
        facts["label"] = _unknown(INVALID)
    facts["value"] = _value(_first(field_chain, "/V"), kind)
    facts["default_value"] = _value(_first(field_chain, "/DV"), kind)
    facts["options"] = _options(terminal, kind)
    facts["export_value"] = _export_value(
        field_chain, kind, terminal_index, widget, dictionaries[0], _parent_kids(widget)
    )
    return facts


def _widget(widget, page: int, index: int, tree: _Tree) -> dict:
    ref = _ref(widget)
    border_colour, background_colour = _colours(widget)
    facts = {
        "object": list(ref) if ref is not None else None,
        "rect": _rect(widget),
        "annotation_flags": _flags(_get(widget, "/F"), 0),
        "states": _states(widget),
        "state": _state(widget),
        "border_colour": border_colour,
        "background_colour": background_colour,
        "border": _border(widget),
    }
    facts.update(_field_facts(widget, tree))
    out = {"page": page, "index": index}
    out.update((fact, facts[fact]) for fact in FACTS)
    return out


def read(data: bytes) -> tuple[list[dict], int | dict]:
    """The widgets of every page, in page and /Annots order, and the terminal field count."""
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False, conversion_mode="implicit") as pdf:
        tree = _Tree(pdf)
        widgets = []
        for page_index, page in enumerate(pdf.pages):
            annots = _get(page.obj, "/Annots")
            if not isinstance(annots, pikepdf.Array):
                continue  # absent, or not an array: no widget entry to read
            for index, item in enumerate(annots):
                if _is_widget(item):
                    widgets.append(_widget(item, page_index, index, tree))
        return widgets, tree.field_count()
