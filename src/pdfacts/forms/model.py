"""Field and Widget: the facts of a form's field and of one widget of it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pdfacts.annotations.model import Annotation
from pdfacts.core.geometry import _finite_float
from pdfacts.core.refs import FieldRef
from pdfacts.core.strings import Name
from pdfacts.core.values import Unknown

__all__ = ["BorderStyle", "ChoiceOption", "DeviceColour", "Field", "FieldType", "Widget"]


class FieldType(StrEnum):
    """The /FT of a terminal field, as text."""

    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    PUSHBUTTON = "pushbutton"
    COMBO_BOX = "combo-box"
    LIST_BOX = "list-box"
    SIGNATURE = "signature"


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    """One entry of a /Opt array: what is exported and what is shown."""

    export: str
    display: str

    def __post_init__(self) -> None:
        for name, value in (("export", self.export), ("display", self.display)):
            if type(value) is not str:
                raise TypeError(f"{name} must be a str, not {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class DeviceColour:
    """A colour of the device space: 0, 1, 3 or 4 components, each from 0 to 1."""

    components: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.components) is not tuple:
            raise TypeError(f"components must be a tuple, not {type(self.components).__name__}")
        if len(self.components) not in (0, 1, 3, 4):
            raise ValueError(f"a colour has 0, 1, 3 or 4 components, not {len(self.components)}")
        components = tuple(_finite_float(component, "a component") for component in self.components)
        for component in components:
            if not 0 <= component <= 1:
                raise ValueError(f"a component is between 0 and 1, not {component}")
        object.__setattr__(self, "components", components)


@dataclass(frozen=True, slots=True)
class BorderStyle:
    """A widget's border: how wide and of which style."""

    width: float | None
    style: Name | None

    def __post_init__(self) -> None:
        if self.width is not None:
            width = _finite_float(self.width, "width")
            if width < 0:
                raise ValueError(f"width is a finite number from 0, not {width}")
            object.__setattr__(self, "width", width)
        if self.style is not None and not isinstance(self.style, Name):
            raise TypeError(f"style must be a Name or None, not {type(self.style).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Field:
    """One terminal field of the form, its own dictionary's values.

    `Unknown` compares by value, so two unknown refs are equal; group widgets
    by `field.ref` only where its `object_number` is an int.
    """

    ref: FieldRef
    name: str | Unknown
    partial_names: tuple[str, ...] | Unknown
    # The /FT as a FieldType; a value of the enum is converted.
    type: FieldType | Unknown
    # /Ff as written, an int from 0 to 2^32 - 1.
    field_flags: int | Unknown
    # /TU; None where the file has no label.
    label: str | None | Unknown
    # /V and /DV: one string, several strings or a name.
    value: str | tuple[str, ...] | Name | None | Unknown
    default_value: str | tuple[str, ...] | Name | None | Unknown
    # The /Opt array as ChoiceOptions; None where the file has none.
    options: tuple[ChoiceOption, ...] | None | Unknown
    # Whether the field tree reaches the widget from /AcroForm /Fields.
    in_form: bool | Unknown

    def __post_init__(self) -> None:
        if not isinstance(self.ref, FieldRef):
            raise TypeError(f"ref must be a FieldRef, not {type(self.ref).__name__}")
        if not isinstance(self.type, Unknown):
            object.__setattr__(self, "type", FieldType(self.type))
        _check_str_or_unknown("name", self.name)
        if not isinstance(self.partial_names, Unknown):
            _check_str_tuple("partial_names", self.partial_names)
        if not isinstance(self.field_flags, Unknown):
            _check_flags("field_flags", self.field_flags)
        if not (self.label is None or isinstance(self.label, (str, Unknown))):
            raise TypeError(
                f"label must be a str, None or Unknown, not {type(self.label).__name__}"
            )
        for name in ("value", "default_value"):
            value = getattr(self, name)
            if not _is_a_value(value):
                raise TypeError(
                    f"{name} must be a str, a tuple of str, a Name, None or Unknown, "
                    f"not {type(value).__name__}"
                )
        if not (self.options is None or isinstance(self.options, (tuple, Unknown))):
            raise TypeError(
                f"options must be a tuple, None or Unknown, not {type(self.options).__name__}"
            )
        if isinstance(self.options, tuple):
            for option in self.options:
                if not isinstance(option, ChoiceOption):
                    raise TypeError(f"options holds ChoiceOptions, not {type(option).__name__}")
        if not isinstance(self.in_form, (bool, Unknown)):
            raise TypeError(f"in_form must be a bool or Unknown, not {type(self.in_form).__name__}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Widget(Annotation):
    """One widget of a page: an annotation that belongs to a field."""

    field: Field | Unknown
    # The keys of /AP /N in ascending order of raw, no two equal.
    states: tuple[Name, ...] | None | Unknown
    # /AS, the state the file says is on.
    state: Name | None | Unknown
    # The export value of a widget of a radio button or a check box.
    export_value: str | None | Unknown
    border_colour: DeviceColour | None | Unknown
    background_colour: DeviceColour | None | Unknown
    border: BorderStyle | None | Unknown

    def __post_init__(self) -> None:
        Annotation.__post_init__(self)
        if not isinstance(self.field, (Field, Unknown)):
            raise TypeError(f"field must be a Field or Unknown, not {type(self.field).__name__}")
        for name in ("states", "state"):
            value = getattr(self, name)
            if value is None or isinstance(value, Unknown):
                continue
            if not isinstance(value, tuple) and name == "states":
                raise TypeError(
                    f"states must be a tuple, None or Unknown, not {type(value).__name__}"
                )
            if name == "state" and not isinstance(value, Name):
                raise TypeError(
                    f"state must be a Name, None or Unknown, not {type(value).__name__}"
                )
        if not isinstance(self.states, (tuple, Unknown)) and self.states is not None:
            raise TypeError(
                f"states must be a tuple, None or Unknown, not {type(self.states).__name__}"
            )
        if isinstance(self.states, tuple):
            previous = None
            for one in self.states:
                if not isinstance(one, Name):
                    raise TypeError(f"states holds Names, not {type(one).__name__}")
                if previous is not None and one.raw <= previous.raw:
                    raise ValueError("states are in ascending order of raw, no two equal")
                previous = one
        if self.state is not None and not isinstance(self.state, (Name, Unknown)):
            raise TypeError(
                f"state must be a Name, None or Unknown, not {type(self.state).__name__}"
            )
        if not (self.export_value is None or isinstance(self.export_value, (str, Unknown))):
            raise TypeError(
                f"export_value must be a str, None or Unknown, "
                f"not {type(self.export_value).__name__}"
            )
        for name in ("border_colour", "background_colour"):
            value = getattr(self, name)
            if not (value is None or isinstance(value, (DeviceColour, Unknown))):
                raise TypeError(
                    f"{name} must be a DeviceColour, None or Unknown, not {type(value).__name__}"
                )
        if not (self.border is None or isinstance(self.border, (BorderStyle, Unknown))):
            raise TypeError(
                f"border must be a BorderStyle, None or Unknown, not {type(self.border).__name__}"
            )


def _check_str_or_unknown(name: str, value: object) -> None:
    if not isinstance(value, (str, Unknown)):
        raise TypeError(f"{name} must be a str or Unknown, not {type(value).__name__}")


def _check_str_tuple(name: str, value: object) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple, not {type(value).__name__}")
    for part in value:
        if type(part) is not str:
            raise TypeError(f"{name} holds str, not {type(part).__name__}")


def _check_flags(name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if not 0 <= value <= 2**32 - 1:
        raise ValueError(f"{name} is a 32-bit flag field, not {value}")


def _is_a_value(value: object) -> bool:
    if isinstance(value, (str, Name, Unknown)) or value is None:
        return True
    if type(value) is tuple:
        return all(type(part) is str for part in value)
    return False
