"""The fact types of forms: what they accept, what they refuse.

One valid instance of each type is built in a helper; the tables change one
field of it, as tests/test_refs.py does.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

import pdfacts  # noqa: E402
from pdfacts import Unknown  # noqa: E402
from pdfacts.annotation.model import Annotation  # noqa: E402
from pdfacts.core.geometry import Rect  # noqa: E402
from pdfacts.core.refs import AnnotationRef, FieldRef  # noqa: E402
from pdfacts.core.strings import decode_name  # noqa: E402
from pdfacts.forms.model import (  # noqa: E402
    BorderStyle,
    ChoiceOption,
    DeviceColour,
    Field,
    FieldType,
    Widget,
)


def annotation_ref(**changes) -> AnnotationRef:
    data = dict(page_index=0, annotation_index=1, kind="Widget", object_number=5, generation=0)
    data.update(changes)
    return AnnotationRef(**data)


def field_ref(**changes) -> FieldRef:
    data = dict(object_number=8, generation=0)
    data.update(changes)
    return FieldRef(**data)


def annotation(**changes) -> Annotation:
    data = dict(ref=annotation_ref(), rect=Rect(0, 0, 10, 10), annotation_flags=4)
    data.update(changes)
    return Annotation(**data)


def field(**changes) -> Field:
    data = dict(
        ref=field_ref(),
        name="a",
        partial_names=("a",),
        type="text",
        field_flags=0,
        label=None,
        value=None,
        default_value=None,
        options=None,
        in_form=True,
    )
    data.update(changes)
    return Field(**data)


def widget(**changes) -> Widget:
    data = dict(
        ref=annotation_ref(),
        rect=Rect(0, 0, 10, 10),
        annotation_flags=4,
        field=field(),
        states=(decode_name(b"A"), decode_name(b"Off")),
        state=decode_name(b"A"),
        export_value=None,
        border_colour=None,
        background_colour=DeviceColour((0.5,)),
        border=BorderStyle(1.0, decode_name(b"S")),
    )
    data.update(changes)
    return Widget(**data)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"page_index": -1}, ValueError),
        ({"page_index": True}, TypeError),
        ({"annotation_index": -1}, ValueError),
        ({"annotation_index": 1.0}, TypeError),
        ({"kind": ""}, ValueError),
        ({"kind": 5}, TypeError),
        ({"object_number": 0}, ValueError),
        ({"object_number": True}, TypeError),
        ({"object_number": None, "generation": 0}, ValueError),
        ({"generation": -1}, ValueError),
        ({"generation": False}, TypeError),
        ({"object_number": 5, "generation": None}, ValueError),
    ],
)
def test_annotation_ref_refuses(changes, error):
    with pytest.raises(error):
        annotation_ref(**changes)


def test_annotation_ref_accepts_both_none_and_both_unknown():
    assert annotation_ref(object_number=None, generation=None).object_number is None
    both = annotation_ref(object_number=Unknown("unreadable"), generation=Unknown("unreadable"))
    assert isinstance(both.object_number, Unknown)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"object_number": 0}, ValueError),
        ({"object_number": True}, TypeError),
        ({"object_number": None, "generation": 0}, ValueError),
        ({"generation": -1}, ValueError),
        ({"generation": False}, TypeError),
        ({"object_number": 5, "generation": None}, ValueError),
    ],
)
def test_field_ref_refuses(changes, error):
    with pytest.raises(error):
        field_ref(**changes)


def test_annotation_refuses_a_rect_that_is_no_rect():
    with pytest.raises(TypeError):
        annotation(rect=(0, 0, 1, 1))


@pytest.mark.parametrize("flags", [-1, 2**32, True])
def test_annotation_refuses_bad_flags(flags):
    with pytest.raises(ValueError if type(flags) is int else TypeError):
        annotation(annotation_flags=flags)


def test_the_field_type_values():
    assert {member.value for member in FieldType} == {
        "text",
        "checkbox",
        "radio",
        "pushbutton",
        "combo-box",
        "list-box",
        "signature",
    }


def test_choice_option_refuses_bytes():
    with pytest.raises(TypeError):
        ChoiceOption(b"a", "a")


@pytest.mark.parametrize(
    ("components", "error"),
    [
        ((0.5, 0.5), ValueError),
        ((1.5,), ValueError),
        ((float("nan"),), ValueError),
        ([0.5], TypeError),
        ((True,), TypeError),
    ],
)
def test_device_colour_refuses(components, error):
    with pytest.raises(error):
        DeviceColour(components)


def test_device_colour_stores_floats():
    components = DeviceColour((1, 0, 0)).components
    assert len(components) == 3
    assert all(type(component) is float for component in components)


def test_device_colour_stores_zero_for_negative_zero():
    (component,) = DeviceColour((-0.0,)).components
    assert math.copysign(1, component) == 1


@pytest.mark.parametrize(
    ("width", "error"), [(-1, ValueError), (float("inf"), ValueError), ("1", TypeError)]
)
def test_border_style_refuses(width, error):
    with pytest.raises(error):
        BorderStyle(width, None)


def test_border_style_stores_a_float():
    assert type(BorderStyle(2, None).width) is float


def test_border_style_refuses_bytes():
    with pytest.raises(TypeError):
        BorderStyle(1.0, b"S")


@pytest.mark.parametrize("value", [["a"], ("a", 1)])
def test_field_refuses_bad_partial_names(value):
    with pytest.raises(TypeError):
        field(partial_names=value)


def test_field_converts_a_type_value():
    assert field(type="text").type is FieldType.TEXT


def test_field_refuses_an_unknown_type_value():
    with pytest.raises(ValueError):
        field(type="button")


@pytest.mark.parametrize("value", [("a", b"b"), 1])
def test_field_refuses_bad_values(value):
    with pytest.raises(TypeError):
        field(value=value)


def test_field_refuses_options_of_str():
    with pytest.raises(TypeError):
        field(options=(ChoiceOption("a", "b"),) if False else ("a",))


def test_field_refuses_an_int_for_in_form():
    with pytest.raises(TypeError):
        field(in_form=1)


@pytest.mark.parametrize("flags", [2**32, False])
def test_field_refuses_bad_field_flags(flags):
    with pytest.raises(ValueError if type(flags) is int else TypeError):
        field(field_flags=flags)


def test_widget_runs_the_annotation_checks():
    with pytest.raises(TypeError):
        widget(annotation_flags=True)
    with pytest.raises(TypeError):
        widget(rect=(0, 0, 1, 1))


def test_widget_refuses_states_out_of_order():
    states = (decode_name(b"Off"), decode_name(b"A"))
    with pytest.raises(ValueError):
        widget(states=states)


def test_widget_refuses_a_state_twice():
    states = (decode_name(b"A"), decode_name(b"A"))
    with pytest.raises(ValueError):
        widget(states=states)


def test_widget_refuses_a_list_of_states():
    with pytest.raises(TypeError):
        widget(states=[decode_name(b"A")])


def test_widget_refuses_a_field_that_is_no_field():
    with pytest.raises(TypeError):
        widget(field="x")


def test_widget_refuses_bytes_for_export_value():
    with pytest.raises(TypeError):
        widget(export_value=b"A")


def test_every_fact_is_equal_to_its_copy_and_hashable():
    facts = [
        annotation_ref(),
        field_ref(),
        annotation(),
        field(),
        widget(),
        ChoiceOption("a", "b"),
        DeviceColour((0.5,)),
        BorderStyle(1.0, decode_name(b"S")),
    ]
    for fact in facts:
        copy = dataclasses.replace(fact)
        assert fact == copy
        assert hash(fact) == hash(copy)


def test_a_widget_is_an_annotation():
    assert isinstance(widget(), Annotation)


def test_the_exports():
    for name in (
        "Annotation",
        "AnnotationRef",
        "BorderStyle",
        "ChoiceOption",
        "DeviceColour",
        "Field",
        "FieldRef",
        "FieldType",
        "Name",
        "NameEncoding",
        "Widget",
    ):
        assert name in pdfacts.__all__
    assert "decode_text_string" not in pdfacts.__all__


@pytest.mark.parametrize(
    "make",
    [
        lambda: DeviceColour((10**400,)),
        lambda: BorderStyle(10**400, None),
    ],
    ids=["a component", "a width"],
)
def test_an_int_too_large_for_a_float_is_a_value_error(make):
    with pytest.raises(ValueError):
        make()


def test_the_largest_flags_are_accepted():
    assert annotation(annotation_flags=2**32 - 1).annotation_flags == 2**32 - 1
    assert field(field_flags=2**32 - 1).field_flags == 2**32 - 1


@pytest.mark.parametrize("components", [(), (0.0, 0.25, 0.5, 1.0)], ids=["none", "four"])
def test_device_colour_accepts_zero_and_four_components(components):
    assert DeviceColour(components).components == components


@pytest.mark.parametrize(
    "value", [decode_name(b"Ja"), ("a", "b")], ids=["a name", "a tuple of text"]
)
def test_field_accepts_a_name_or_a_tuple_of_text_as_value(value):
    assert field(value=value).value == value


@pytest.mark.parametrize(
    ("make", "changes"),
    [
        (field, {"name": 1}),
        (field, {"label": 1}),
        (field, {"ref": "x"}),
        (annotation, {"ref": "x"}),
        (widget, {"border": "x"}),
        (widget, {"border_colour": "x"}),
        (widget, {"background_colour": "x"}),
    ],
    ids=[
        "field name",
        "field label",
        "field ref",
        "annotation ref",
        "border",
        "border colour",
        "background colour",
    ],
)
def test_the_other_fields_are_checked(make, changes):
    with pytest.raises(TypeError):
        make(**changes)


@pytest.mark.parametrize("make", [annotation, field, field_ref, widget])
def test_keywords_only(make):
    valid = make()
    values = [getattr(valid, one.name) for one in dataclasses.fields(valid)]
    with pytest.raises(TypeError):
        type(valid)(*values)


def test_a_widget_has_no_dict():
    assert not hasattr(widget(), "__dict__")
