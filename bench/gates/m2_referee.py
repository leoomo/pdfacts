"""The referee of the M2 gate: the widgets and fields a file writes.

A reading of the dictionaries with pikepdf's raw objects that follows the
Forms rules of docs/conventions.md (issues P1 to P4): the decoding of text
strings and names, the chain of a widget, the /Kids of its /Parent, the field
tree walk, the terminal field with its inheritance, and the facts of a widget
and its field. It is written to be compared with pdfacts, and for that it
imports pikepdf and the standard library only — nothing of pdfacts, nothing
of bench/ — and it reads one engine, so it does not run the run-time check.

read(data) gives the widgets of every page in page order and /Annots order,
each a dictionary with page, index and the nineteen facts, in a plain form:
a name as {"name": "<hex of its bytes>"}, an unknown fact as
{"unknown": "<reason>"}, a float as float(value) + 0.0 (so a negative zero
is zero); and the number of terminal fields of the tree — 0 without
/AcroForm, an unknown for an invalid or cut tree. The form holds text of
documents: it is compared in memory and never written.
"""

from __future__ import annotations

import io
import math
from decimal import Decimal

import pikepdf

# The reasons an unknown fact carries, as pdfacts names them.
_INVALID = "invalid-in-file"
_UNREADABLE = "unreadable"

# ISO 32000-2, Annex D, Table D.2; the three bytes it leaves undefined as
# U+FFFD, as the conventions say (the engines disagree on them).
_PDFDOC = [chr(code) for code in range(256)]
_PDFDOC[0x18:0x20] = "˘ˇˆ˙˝˛˚˜"
_PDFDOC[0x80:0x9F] = "•†‡…—–ƒ⁄‹›−‰„“”‘’‚™ﬁﬂŁŒŠŸŽıłœšž"
_PDFDOC[0xA0] = "€"
for _code in (0x7F, 0x9F, 0xAD):
    _PDFDOC[_code] = "�"

# The nineteen facts, in the order the gate compares them.
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

# The facts of the field, from field_ref to in_form, and export_value: the
# ones that are one unknown when the field is unknown as a whole.
_FIELD_FACTS = FACTS[3:13] + ("export_value",)


def _unknown(reason: str) -> dict:
    return {"unknown": reason}


def _text(data: bytes) -> str:
    """A text string as the conventions decode it; it never fails."""
    if data[:2] == b"\xfe\xff":
        return _without_escapes(data[2:].decode("utf-16-be", "replace"))
    if data[:2] == b"\xff\xfe":
        return _without_escapes(data[2:].decode("utf-16-le", "replace"))
    if data[:3] == b"\xef\xbb\xbf":
        return _without_escapes(data[3:].decode("utf-8", "replace"))
    return "".join(_PDFDOC[byte] for byte in data)


def _without_escapes(text: str) -> str:
    """The text without language escape sequences (ISO 32000-2, 7.9.2.2)."""
    out: list[str] = []
    start = 0
    while (at := text.find("\x1b", start)) >= 0:
        out.append(text[start:at])
        closing = text.find("\x1b", at + 1)
        if closing < 0:
            return "".join(out)
        start = closing + 1
    out.append(text[start:])
    return "".join(out)


def _name_bytes(name: pikepdf.Name | str) -> bytes:
    """The bytes of a name: unparse() without its slash, #xx resolved."""
    syntax = name.unparse(resolved=True) if isinstance(name, pikepdf.Name) else None
    if syntax is None:
        # A key of a dictionary comes as str, with #xx still written.
        return name.encode("utf-8", "surrogateescape")[1:]
    out = bytearray()
    at = 1
    while at < len(syntax):
        if syntax[at : at + 1] == b"#":
            out.append(int(syntax[at + 1 : at + 3], 16))
            at += 3
        else:
            out += syntax[at : at + 1]
            at += 1
    return bytes(out)


def _name(name: pikepdf.Name) -> dict:
    return {"name": _name_bytes(name).hex()}


def _objgen(dictionary: pikepdf.Dictionary) -> tuple[int, int] | None:
    return dictionary.objgen if dictionary.is_indirect else None


def _a_number(value: object) -> float | None:
    """A number of the file as a finite float, or None where it is none.

    An integer or a real of the file, not a boolean; a real too large for a
    float counts as no number, as the conventions treat it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _Node:
    """One node of the field tree, as the walk reached it."""

    def __init__(self, dictionary: pikepdf.Dictionary, parent: int | None) -> None:
        self.dictionary = dictionary
        self.kids: tuple[int | None, ...] | None = None
        self.parent = parent
        self.parent_matches = parent is None and dictionary.get("/Parent") is None
        self.reached_twice = False
        self.is_field = False
        self.terminal = False
        self.invalid = False


class _Tree:
    """The walk of /AcroForm /Fields: its nodes, and whether it holds.

    invalid: /AcroForm is not a dictionary, /Fields is absent or not an
    array, or an entry of /Fields is no dictionary. cut: a node 32 levels
    below a root has /Kids, which were not followed. Without /AcroForm the
    tree is empty and `none` is set.
    """

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        self.nodes: list[_Node] = []
        self.by_objgen: dict[tuple[int, int], int] = {}
        self.invalid = False
        self.cut = False
        self.none = False
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None:
            self.none = True
            return
        if not isinstance(acroform, pikepdf.Dictionary):
            self.invalid = True
            return
        fields = acroform.get("/Fields")
        if not isinstance(fields, pikepdf.Array):
            self.invalid = True
            return
        self._arrays_done: dict[tuple[int, int], tuple[int | None, ...]] = {}
        self._arrays_active: set[tuple[int, int]] = set()
        self._array_nodes: dict[tuple[int, int], list[int]] = {}
        self._array_marked: set[tuple[int, int]] = set()
        self._array_waiting: dict[tuple[int, int], list[int]] = {}
        holder = fields.objgen if fields.is_indirect else "fields"
        for position, item in enumerate(fields):
            if not isinstance(item, pikepdf.Dictionary):
                self.invalid = True
            self._entry(item, (holder, position), None, 0)
        self._table()

    def _entry(self, entry: object, key: object, parent: int | None, level: int) -> int | None:
        """One entry of /Fields or of a /Kids array: its node's index, or None.

        An indirect dictionary is one node however often it is named; a
        direct one is a node of its own at each place it stands.
        """
        if not isinstance(entry, pikepdf.Dictionary):
            return None
        if entry.is_indirect:
            if entry.objgen in self.by_objgen:
                index = self.by_objgen[entry.objgen]
                self.nodes[index].reached_twice = True
                return index
        index = len(self.nodes)
        if entry.is_indirect:
            self.by_objgen[entry.objgen] = index
        node = _Node(entry, parent)
        if parent is not None:
            named = entry.get("/Parent")
            above = self.nodes[parent].dictionary
            node.parent_matches = (
                isinstance(named, pikepdf.Dictionary)
                and named.is_indirect
                and above.is_indirect
                and above.objgen == named.objgen
            )
        self.nodes.append(node)
        kids = entry.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            if level >= 32:
                self.cut = True
            else:
                node.kids = self._kids(
                    kids, entry.objgen if entry.is_indirect else key, index, level
                )
        else:
            node.kids = ()
        return index

    def _kids(
        self, array: pikepdf.Array, node_key: object, node_index: int, level: int
    ) -> tuple[int | None, ...]:
        """The node indices of a /Kids array's entries; an indirect one is walked once."""
        if not array.is_indirect:
            return self._kids_entries(array, (node_key, "direct kids"), node_index, level)
        key = array.objgen
        if key in self._arrays_done:
            if key not in self._array_marked:
                self._array_marked.add(key)
                for held in self._array_nodes[key]:
                    self.nodes[held].reached_twice = True
            return self._arrays_done[key]
        if key in self._arrays_active:
            # A loop through the array: it is not walked again. The node gets
            # its kids when the walk of the array ends, and they count as met
            # again.
            self._array_waiting.setdefault(key, []).append(node_index)
            return ()
        self._arrays_active.add(key)
        try:
            kids = self._kids_entries(array, key, node_index, level)
        finally:
            self._arrays_active.discard(key)
        self._arrays_done[key] = kids
        self._array_nodes[key] = [index for index in kids if index is not None]
        waiting = self._array_waiting.pop(key, [])
        for node in waiting:
            self.nodes[node].kids = kids
        if waiting:
            self._array_marked.add(key)
            for held in self._array_nodes[key]:
                self.nodes[held].reached_twice = True
        return kids

    def _kids_entries(
        self, array: pikepdf.Array, holder_key: object, parent_index: int, level: int
    ) -> tuple[int | None, ...]:
        return tuple(
            self._entry(item, (holder_key, position), parent_index, level + 1)
            for position, item in enumerate(array)
        )

    def _table(self) -> None:
        """The node table of D7: which nodes are fields, which terminal, which invalid."""
        for node in self.nodes:
            # A root of the walk has no parent; its index is one of /Fields' own.
            node.is_field = (
                node.parent is None
                or node.dictionary.get("/T") is not None
                or isinstance(node.dictionary.get("/Kids"), pikepdf.Array)
            )
        for node in self.nodes:
            if node.is_field:
                node.terminal = node.kids is not None and not any(
                    self.nodes[kid].is_field for kid in node.kids if kid is not None
                )
        for node in self.nodes:
            if not node.is_field:
                continue
            bad = node.reached_twice or not node.parent_matches
            if node.kids is not None:
                kid_fields = [self.nodes[kid].is_field for kid in node.kids if kid is not None]
                mix = any(kid_fields) and not all(kid_fields)
                none_kid = any(kid is None for kid in node.kids)
                terminal_bad = node.terminal and any(
                    not self.nodes[kid].parent_matches or self.nodes[kid].reached_twice
                    for kid in node.kids
                    if kid is not None
                )
                bad = bad or mix or none_kid or terminal_bad
            node.invalid = bad
        for node in self.nodes:
            if not node.is_field or node.invalid:
                continue
            above = node.parent
            while above is not None:
                if self.nodes[above].is_field and self.nodes[above].invalid:
                    node.invalid = True
                    break
                above = self.nodes[above].parent


def read(data: bytes) -> tuple[list[dict], int | dict]:
    """The widgets of the file, and the number of its terminal fields."""
    with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
        tree = _Tree(pdf)
        out: list[dict] = []
        for page_index, page in enumerate(pdf.pages):
            annots = page.obj.get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                continue
            for index, item in enumerate(annots):
                if not isinstance(item, pikepdf.Dictionary):
                    continue
                if item.get("/Subtype") != pikepdf.Name("/Widget"):
                    continue
                out.append(_widget(item, page_index, index, tree))
        return out, _field_count(tree)


def _field_count(tree: _Tree) -> int | dict:
    if tree.none:
        return 0
    if tree.invalid:
        return _unknown(_INVALID)
    if tree.cut:
        return _unknown(_UNREADABLE)
    return sum(1 for node in tree.nodes if node.is_field and node.terminal)


def _widget(dictionary: pikepdf.Dictionary, page_index: int, index: int, tree: _Tree) -> dict:
    out = {"page": page_index, "index": index}
    objgen = _objgen(dictionary)
    out["object"] = [objgen[0], objgen[1]] if objgen else None
    out["rect"] = _rect_fact(dictionary.get("/Rect"))
    out["annotation_flags"] = _flags_fact(dictionary.get("/F"))
    out["states"], out["state"] = _state_facts(dictionary)
    out["border_colour"], out["background_colour"], out["border"] = _appearance_facts(dictionary)
    chain, loops, cut, parent_invalid = _chain_of(dictionary)
    terminal = 0 if chain[0].get("/T") is not None or len(chain) == 1 else 1
    _field_facts(out, dictionary, chain, terminal, objgen, loops, cut, parent_invalid, tree)
    return out


def _rect_fact(written: object) -> list[float] | dict:
    if not isinstance(written, pikepdf.Array) or len(written) != 4:
        return _unknown(_INVALID)
    numbers = [_a_number(item) for item in written]
    if any(number is None for number in numbers):
        return _unknown(_INVALID)
    x0, y0, x1, y1 = numbers
    return [
        min(x0, x1) + 0.0,
        min(y0, y1) + 0.0,
        max(x0, x1) + 0.0,
        max(y0, y1) + 0.0,
    ]


def _flags_fact(written: object) -> int | dict:
    if written is None:
        return 0
    if _is_integer(written) and 0 <= written <= 2**32 - 1:
        return written
    return _unknown(_INVALID)


def _state_facts(
    dictionary: pikepdf.Dictionary,
) -> tuple[list[dict] | None | dict, dict | None | dict]:
    ap = dictionary.get("/AP")
    states: tuple[bytes, ...] | None = None
    states_invalid = False
    if ap is not None:
        if not isinstance(ap, pikepdf.Dictionary):
            states_invalid = True
        else:
            n = ap.get("/N")
            if isinstance(n, pikepdf.Dictionary):
                states = tuple(_name_bytes(key) for key in n)
            elif n is not None and not isinstance(n, pikepdf.Stream):
                states_invalid = True
    if states_invalid:
        return _unknown(_INVALID), _state_fact(dictionary.get("/AS"))
    if states is None:
        return None, _state_fact(dictionary.get("/AS"))
    return [{"name": one.hex()} for one in sorted(states)], _state_fact(dictionary.get("/AS"))


def _state_fact(written: object) -> dict | None | dict:
    if written is None:
        return None
    if isinstance(written, pikepdf.Name):
        return _name(written)
    return _unknown(_INVALID)


def _appearance_facts(dictionary: pikepdf.Dictionary) -> tuple[dict | list | None, ...]:
    mk = dictionary.get("/MK")
    mk_invalid = mk is not None and not isinstance(mk, pikepdf.Dictionary)
    if mk_invalid:
        colours = (_unknown(_INVALID), _unknown(_INVALID))
    else:
        colours = (
            _colour_fact(mk.get("/BC") if mk is not None else None),
            _colour_fact(mk.get("/BG") if mk is not None else None),
        )
    bs = dictionary.get("/BS")
    bs_invalid = bs is not None and not isinstance(bs, pikepdf.Dictionary)
    border = _unknown(_INVALID) if bs_invalid else _border_fact(bs)
    return (*colours, border)


def _colour_fact(written: object) -> list[float] | dict | None:
    if written is None:
        return None
    if not isinstance(written, pikepdf.Array) or len(written) not in (0, 1, 3, 4):
        return _unknown(_INVALID)
    numbers = [_a_number(item) for item in written]
    if any(number is None or not 0 <= number <= 1 for number in numbers):
        return _unknown(_INVALID)
    return [number + 0.0 for number in numbers]


def _border_fact(bs: pikepdf.Dictionary | None) -> list | dict | None:
    if bs is None:
        return None
    width_written = bs.get("/W")
    style_written = bs.get("/S")
    if width_written is None and style_written is None:
        return None
    width = _a_number(width_written) if width_written is not None else None
    if width_written is not None and (width is None or width < 0):
        return _unknown(_INVALID)
    if style_written is not None and not isinstance(style_written, pikepdf.Name):
        return _unknown(_INVALID)
    return [
        width + 0.0 if width is not None else None,
        _name(style_written) if style_written is not None else None,
    ]


def _chain_of(dictionary: pikepdf.Dictionary) -> tuple[list[pikepdf.Dictionary], bool, bool, bool]:
    """The widget's own dictionary, then each /Parent; at most 33 of them."""
    chain = [dictionary]
    seen = {dictionary.objgen} if dictionary.is_indirect else set()
    loops = cut = parent_invalid = False
    current = dictionary
    while True:
        parent = current.get("/Parent")
        if parent is None:
            break
        if not isinstance(parent, pikepdf.Dictionary):
            parent_invalid = True
            break
        if len(chain) >= 33:
            cut = True
            break
        if parent.is_indirect and parent.objgen in seen:
            loops = True
            break
        chain.append(parent)
        if parent.is_indirect:
            seen.add(parent.objgen)
        current = parent
    return chain, loops, cut, parent_invalid


def _parent_kids(parent: object) -> tuple[dict[tuple[int, int], tuple[int, ...]], bool] | None:
    """The /Kids of the widget's own /Parent, with the positions of its widgets."""
    if not (isinstance(parent, pikepdf.Dictionary) and parent.is_indirect):
        return None
    kids = parent.get("/Kids")
    if not isinstance(kids, pikepdf.Array):
        return None
    positions: dict[tuple[int, int], list[int]] = {}
    all_widgets = True
    widgets = 0
    for item in kids:
        objgen = _objgen(item) if isinstance(item, pikepdf.Dictionary) else None
        widget = isinstance(item, pikepdf.Dictionary) and item.get("/Subtype") == pikepdf.Name(
            "/Widget"
        )
        if widget:
            if objgen is not None:
                positions.setdefault(objgen, []).append(widgets)
            widgets += 1
        else:
            all_widgets = False
    return {key: tuple(value) for key, value in positions.items()}, all_widgets


def _first_up(chain: list[pikepdf.Dictionary], key: str) -> object:
    """The first value of `key` up the chain from its first dictionary, or None."""
    for dictionary in chain:
        value = dictionary.get(key)
        if value is not None:
            return value
    return None


def _field_facts(
    out: dict,
    dictionary: pikepdf.Dictionary,
    chain: list[pikepdf.Dictionary],
    terminal: int,
    objgen: tuple[int, int] | None,
    loops: bool,
    cut: bool,
    parent_invalid: bool,
    tree: _Tree,
) -> None:
    """D5: the facts of the widget's field, in the plain form."""
    whole: str | None = None
    if loops or parent_invalid:
        whole = _INVALID
    elif cut:
        whole = _UNREADABLE
    term_objgen = _objgen(chain[terminal])
    node_index = tree.by_objgen.get(term_objgen) if term_objgen is not None else None
    if whole is None:
        if node_index is not None and tree.nodes[node_index].kids is None:
            whole = _UNREADABLE
        elif not _link_holds(chain, terminal, objgen, term_objgen, node_index, tree):
            whole = _INVALID
    if whole is not None:
        for fact in _FIELD_FACTS:
            out[fact] = _unknown(whole)
        return

    out["field_ref"] = [term_objgen[0], term_objgen[1]] if term_objgen else None
    field_chain = chain[terminal:]
    node_bad = node_index is not None and tree.nodes[node_index].invalid
    if node_bad:
        for fact in _FIELD_FACTS:
            if fact not in ("field_ref", "in_form"):
                out[fact] = _unknown(_INVALID)
    else:
        _names_and_numbers(out, field_chain)
        type_fact, flags = _type_fact(field_chain)
        out["type"] = type_fact
        out["field_flags"] = flags
        out["label"] = _label_fact(chain[terminal])
        type_reason = type_fact.get("unknown") if isinstance(type_fact, dict) else None
        out["value"] = _value_fact(field_chain, "/V", type_fact, type_reason)
        out["default_value"] = _value_fact(field_chain, "/DV", type_fact, type_reason)
        out["options"] = _options_fact(field_chain, type_fact, type_reason)
        out["export_value"] = _export_fact(
            chain, terminal, field_chain, type_fact, type_reason, objgen
        )
    out["in_form"] = _in_form(term_objgen, node_index, tree)


def _in_form(
    term_objgen: tuple[int, int] | None, node_index: int | None, tree: _Tree
) -> bool | dict:
    """D5's in_form: whether the tree holds the terminal field as a terminal node."""
    if (
        term_objgen is not None
        and node_index is not None
        and tree.nodes[node_index].is_field
        and tree.nodes[node_index].terminal
    ):
        return True
    if tree.none:
        return False
    if tree.invalid or tree.cut or term_objgen is None:
        return _unknown(_UNREADABLE)
    return False


def _link_holds(
    chain: list[pikepdf.Dictionary],
    terminal: int,
    objgen: tuple[int, int] | None,
    term_objgen: tuple[int, int] | None,
    node_index: int | None,
    tree: _Tree,
) -> bool:
    if terminal == 1:
        kids = _parent_kids(chain[0].get("/Parent"))
        if kids is None or not kids[1]:
            return False
        if len(kids[0].get(objgen, ())) != 1:
            return False
        if objgen is not None:
            widget_node = tree.by_objgen.get(objgen)
            if widget_node is not None and tree.nodes[widget_node].is_field:
                return False
    if node_index is not None and not tree.nodes[node_index].terminal:
        return False
    return True


def _names_and_numbers(out: dict, field_chain: list[pikepdf.Dictionary]) -> None:
    partials: list[str] = []
    ok = True
    for dictionary in reversed(field_chain):
        t = dictionary.get("/T")
        if t is None:
            continue
        if isinstance(t, pikepdf.String):
            partials.append(_text(bytes(t)))
        else:
            ok = False
    if not ok:
        out["partial_names"] = _unknown(_INVALID)
        out["name"] = _unknown(_INVALID)
        return
    out["partial_names"] = partials
    out["name"] = (
        _unknown(_INVALID)
        if any("." in one for one in partials)
        else ".".join(one for one in partials if one)
    )


def _type_fact(field_chain: list[pikepdf.Dictionary]) -> tuple[dict | str, dict | int]:
    """The type of the field and its flags; the type may be an unknown."""
    written = _first_up(field_chain, "/Ff")
    if written is None:
        flags: int | dict = 0
    elif _is_integer(written) and 0 <= written <= 2**32 - 1:
        flags = written
    else:
        flags = _unknown(_INVALID)
    if isinstance(flags, dict):
        return _unknown(_INVALID), flags
    ft = _first_up(field_chain, "/FT")
    if not isinstance(ft, pikepdf.Name):
        return _unknown(_INVALID), flags
    name = _name_bytes(ft)
    if name == b"Tx":
        return "text", flags
    if name == b"Sig":
        return "signature", flags
    if name == b"Btn":
        if flags & 65536 and flags & 32768:
            return _unknown(_INVALID), flags
        if flags & 65536:
            return "pushbutton", flags
        if flags & 32768:
            return "radio", flags
        return "checkbox", flags
    if name == b"Ch":
        return ("combo-box" if flags & 131072 else "list-box"), flags
    return _unknown(_INVALID), flags


def _label_fact(terminal_field: pikepdf.Dictionary) -> str | None | dict:
    written = terminal_field.get("/TU")
    if written is None:
        return None
    if isinstance(written, pikepdf.String):
        return _text(bytes(written))
    return _unknown(_INVALID)


def _value_fact(
    field_chain: list[pikepdf.Dictionary], key: str, type_fact: dict | str, type_reason: str | None
) -> str | list | dict | None:
    written = _first_up(field_chain, key)
    if written is None:
        return None
    if type_reason is not None:
        return _unknown(type_reason)
    kind = type_fact if isinstance(type_fact, str) else None
    if kind == "text":
        if isinstance(written, pikepdf.String):
            return _text(bytes(written))
        if isinstance(written, pikepdf.Stream):
            return _unknown(_UNREADABLE)
        return _unknown(_INVALID)
    if kind in ("combo-box", "list-box"):
        if isinstance(written, pikepdf.String):
            return [_text(bytes(written))]
        if isinstance(written, pikepdf.Array) and all(
            isinstance(item, pikepdf.String) for item in written
        ):
            return [_text(bytes(item)) for item in written]
        return _unknown(_INVALID)
    if kind in ("checkbox", "radio"):
        if isinstance(written, pikepdf.Name):
            return _name(written)
        return _unknown(_INVALID)
    if kind == "pushbutton":
        return _unknown(_INVALID)
    return _unknown(_UNREADABLE)


def _options_fact(
    field_chain: list[pikepdf.Dictionary], type_fact: dict | str, type_reason: str | None
) -> list | dict | None:
    written = field_chain[0].get("/Opt")
    if type_reason is not None:
        return None if written is None else _unknown(type_reason)
    kind = type_fact if isinstance(type_fact, str) else None
    if kind not in ("combo-box", "list-box"):
        return None
    if written is None:
        return None
    if not isinstance(written, pikepdf.Array):
        return _unknown(_INVALID)
    out: list[list[str]] = []
    for item in written:
        if isinstance(item, pikepdf.String):
            text = _text(bytes(item))
            out.append([text, text])
        elif (
            isinstance(item, pikepdf.Array)
            and len(item) == 2
            and all(isinstance(one, pikepdf.String) for one in item)
        ):
            out.append([_text(bytes(item[0])), _text(bytes(item[1]))])
        else:
            return _unknown(_INVALID)
    return out


def _export_fact(
    chain: list[pikepdf.Dictionary],
    terminal: int,
    field_chain: list[pikepdf.Dictionary],
    type_fact: dict | str,
    type_reason: str | None,
    widget_objgen: tuple[int, int] | None,
) -> str | dict | None:
    """D5's export value of a checkbox or radio, by the widget's place in /Kids."""
    if type_reason is not None:
        return _unknown(type_reason)
    kind = type_fact if isinstance(type_fact, str) else None
    if kind not in ("checkbox", "radio"):
        return None
    written = _first_up(field_chain, "/Opt")
    if written is None:
        return None
    if terminal == 0:
        if isinstance(chain[0].get("/Kids"), pikepdf.Array):
            return _unknown(_INVALID)
        index = 0
    else:
        kids = _parent_kids(chain[0].get("/Parent"))
        if kids is None or len(kids[0].get(widget_objgen, ())) != 1:
            return _unknown(_INVALID)
        index = kids[0][widget_objgen][0]
    if not isinstance(written, pikepdf.Array):
        return _unknown(_INVALID)
    if index >= len(written):
        return _unknown(_INVALID)
    item = written[index]
    if not isinstance(item, pikepdf.String):
        return _unknown(_INVALID)
    return _text(bytes(item))
