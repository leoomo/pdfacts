"""The forms reader: widgets and fields from QPDF's plain data (P4, part 1).

Each test builds a file with the byte-level builder, opens it through
QpdfDocument and calls the reader's private functions; expected values are
what the file writes (AGENTS.md, rule 2). A widget below is a dictionary with
/Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30] unless its body
writes another /Rect.
"""

from __future__ import annotations

import pytest

from pdfacts import Unknown
from pdfacts.backends.qpdf import QpdfDocument
from pdfacts.core.strings import decode_name
from pdfacts.forms import reader
from pdfacts.forms.model import ChoiceOption, DeviceColour, FieldType
from synthetic.builder import build

N = decode_name
STREAM = b"<< /Length 0 >>\nstream\n\nendstream"


def forms_file(annots: dict[int, bytes], fields: bytes = b"", objects=None) -> bytes:
    """A one-page file: `annots` in the page's /Annots, `fields` in /AcroForm /Fields."""
    listing = (
        b"[" + b" ".join(b"%d 0 R" % number for number in sorted(annots)) + b"]"
        if annots
        else b"[]"
    )
    catalog = b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields %s >> >>" % (
        fields if fields else b"[]"
    )
    whole = {
        1: catalog,
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
        % listing,
    }
    whole.update(annots)
    if objects:
        whole.update(objects)
    return build(whole, 1)


def plain_file(
    objects: dict[int, bytes], annots: bytes = b"[]", acroform: bytes | None = None
) -> bytes:
    """A one-page file with full control over the catalog."""
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    if acroform is not None:
        catalog = b"<< /Type /Catalog /Pages 2 0 R /AcroForm %s >>" % acroform
    whole = {
        1: catalog,
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << >> /Annots %s >>"
        % annots,
    }
    whole.update(objects)
    return build(whole, 1)


def widget(entries: bytes, rect: bytes = b"[10 10 110 30]") -> bytes:
    return b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect %s %s >>" % (rect, entries)


def widgets_of(data: bytes):
    document = QpdfDocument.open(data, None)
    try:
        nodes = reader._nodes(document.field_tree())
        return [reader._widget(entry, 0, nodes) for entry in document.annotations(0).widgets]
    finally:
        document.close()


def one_widget(data: bytes):
    (widget_,) = widgets_of(data)
    return widget_


def fields_of(data: bytes):
    document = QpdfDocument.open(data, None)
    try:
        return reader._fields(document.field_tree())
    finally:
        document.close()


def invalid(value) -> bool:
    return isinstance(value, Unknown) and value.reason.value == "invalid-in-file"


def unreadable(value) -> bool:
    return isinstance(value, Unknown) and value.reason.value == "unreadable"


# --- A1 to A7: the field, its name, inheritance ----------------------------------------


def test_a1_a_merged_text_field():
    data = forms_file({6: widget(b"/T (a) /FT /Tx /V (x) /TU (L)")}, fields=b"[6 0 R]")
    widget_ = one_widget(data)
    field = widget_.field
    assert field.ref.object_number == 6
    assert field.name == "a"
    assert field.partial_names == ("a",)
    assert field.type is FieldType.TEXT
    assert field.value == "x"
    assert field.label == "L"
    assert field.field_flags == 0
    assert field.in_form is True
    assert (widget_.rect.x0, widget_.rect.y0, widget_.rect.x1, widget_.rect.y1) == (10, 10, 110, 30)
    assert widget_.annotation_flags == 0
    assert widget_.states is None
    assert widget_.state is None
    assert widget_.export_value is None
    assert widget_.border_colour is None
    assert widget_.background_colour is None
    assert widget_.border is None


def test_a2_a_radio_group():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R /AS /Off /AP << /N << /A 9 0 R /Off 9 0 R >> >>"),
            7: widget(b"/Parent 8 0 R /AS /B /AP << /N << /B 9 0 R /Off 9 0 R >> >>"),
            8: b"<< /T (p) /FT /Btn /Ff 32768 /V /B /Opt [(A) (B)] /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
        objects={9: STREAM},
    )
    six, seven = widgets_of(data)
    for widget_ in (six, seven):
        assert widget_.field.type is FieldType.RADIO
        assert widget_.field.value == N(b"B")
        assert widget_.field.ref.object_number == 8
    assert six.export_value == "A"
    assert six.states == (N(b"A"), N(b"Off"))
    assert six.state == N(b"Off")
    assert seven.export_value == "B"


def test_a3_a_nameless_terminal_field():
    data = forms_file(
        {
            6: b"<< /T (g) /FT /Btn /Ff 49152 /Opt [(A) (B)] /Kids [7 0 R] >>",
            7: b"<< /Parent 6 0 R /Kids [8 0 R 9 0 R] >>",
            8: widget(b"/Parent 7 0 R"),
            9: widget(b"/Parent 7 0 R"),
        },
        fields=b"[6 0 R]",
    )
    eight, nine = widgets_of(data)
    for widget_ in (eight, nine):
        assert widget_.field.ref.object_number == 7
        assert widget_.field.name == "g"
    assert eight.export_value == "A"
    assert nine.export_value == "B"


def test_a4_a_kids_own_v_does_not_count():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R /V ()"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Tx /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    six, seven = widgets_of(data)
    assert six.field.value is None
    assert seven.field.value is None


def test_a5_an_empty_t_adds_nothing():
    data = forms_file(
        {
            6: widget(b"/T (c) /Parent 8 0 R"),
            8: b"<< /T () /Parent 9 0 R /Kids [6 0 R] >>",
            9: b"<< /T (a) >>",
        },
        fields=b"[9 0 R]",
    )
    widget_ = one_widget(data)
    assert widget_.field.name == "a.c"
    assert widget_.field.partial_names == ("a", "", "c")


def test_a6_a_period_in_a_partial_name():
    data = forms_file(
        {6: widget(b"/T (c) /Parent 8 0 R"), 8: b"<< /T (a.b) >>"},
        fields=b"[8 0 R]",
    )
    widget_ = one_widget(data)
    assert invalid(widget_.field.name)
    assert widget_.field.partial_names == ("a.b", "c")


def test_a7_inherited_from_two_levels_up():
    data = forms_file(
        {
            6: widget(b"/Parent 7 0 R"),
            7: b"<< /Parent 8 0 R /Kids [6 0 R] >>",
            8: b"<< /FT /Ch /Ff 131072 /Kids [7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    widget_ = one_widget(data)
    assert widget_.field.type is FieldType.COMBO_BOX
    assert widget_.field.field_flags == 131072


# --- A8 to A12: types, values, options, export values -----------------------------------


@pytest.mark.parametrize(
    ("entries", "check"),
    [
        (b"/T (a) /FT /Btn /Ff 98304", "invalid"),
        (b"/T (a) /FT /Ch", FieldType.LIST_BOX),
        (b"/T (a) /FT /Sig", FieldType.SIGNATURE),
        (b"/T (a) /FT /Xy", "invalid"),
        (b"/T (a)", "invalid"),
        (b"/T (a) /Ff 2.0", "flags and type invalid"),
        (b"/T (a) /FT /Tx /Ff 2.0", "flags and type invalid"),
    ],
    ids=["both bits", "list box", "signature", "xy", "no ft", "ff real", "tx with real ff"],
)
def test_a8_types(entries, check):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    type_ = one_widget(data).field.type
    if check == "invalid":
        assert invalid(type_)
    elif check == "flags and type invalid":
        assert invalid(type_)
        assert invalid(one_widget(data).field.field_flags)
    else:
        assert type_ is check


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (b"/T (a) /FT /Tx /V (x)", "x"),
        (b"/T (a) /FT /Tx /V 9 0 R", "unreadable"),
        (b"/T (a) /FT /Ch /V (x)", ("x",)),
        (b"/T (a) /FT /Ch /V [(x) (y)]", ("x", "y")),
        (b"/T (a) /FT /Btn /V /Ja", N(b"Ja")),
        (b"/T (a) /FT /Btn /V ()", "invalid"),
        (b"/T (a) /FT /Btn /Ff 65536 /V (x)", "invalid"),
        (b"/T (a) /FT /Sig /V 5 0 R", "unreadable"),
    ],
)
def test_a9_values(entries, expected):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]", objects={9: STREAM, 5: STREAM})
    value = one_widget(data).field.value
    if expected == "unreadable":
        assert unreadable(value)
    elif expected == "invalid":
        assert invalid(value)
    else:
        assert value == expected


def test_a9_text_without_v():
    data = forms_file({6: widget(b"/T (a) /FT /Tx")}, fields=b"[6 0 R]")
    assert one_widget(data).field.value is None


def test_a10_default_values():
    data = forms_file({6: widget(b"/T (a) /FT /Tx /DV (d)")}, fields=b"[6 0 R]")
    assert one_widget(data).field.default_value == "d"
    data = forms_file({6: widget(b"/T (a) /FT /Ch /DV [(d)]")}, fields=b"[6 0 R]")
    assert one_widget(data).field.default_value == ("d",)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (
            b"/FT /Ch /Opt [(a) [(e) (Display)]]",
            (ChoiceOption("a", "a"), ChoiceOption("e", "Display")),
        ),
        (b"/FT /Ch /Opt [(a) 5]", "invalid"),
        (b"/FT /Tx /Opt [(a)]", None),
        (b"/FT /Xy /Opt [(a)]", "invalid"),
    ],
    ids=["pairs", "second not a string", "a text field", "unknown type"],
)
def test_a11_options(entries, expected):
    data = forms_file({6: widget(b"/T (f) " + entries)}, fields=b"[6 0 R]")
    options = one_widget(data).field.options
    if expected == "invalid":
        assert invalid(options)
    else:
        assert options == expected


def test_a11_opt_only_on_the_parent_of_a_combo_box():
    data = forms_file(
        {
            6: widget(b"/Parent 7 0 R"),
            7: b"<< /Parent 8 0 R /Kids [6 0 R] >>",
            8: b"<< /T (f) /FT /Ch /Opt [(a)] /Kids [7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    assert one_widget(data).field.options is None


def test_a12_1_a_merged_checkbox_with_kids():
    data = forms_file(
        {
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (c) /FT /Btn /Opt [(A)] /Kids [7 0 R] >>",
            7: widget(b"/Parent 6 0 R"),
        },
        fields=b"[6 0 R]",
    )
    six, seven = widgets_of(data)
    assert invalid(six.export_value)
    assert seven.export_value == "A"


def test_a12_2_a_non_widget_kid():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Btn /Ff 32768 /Opt [(A) (B)] /Kids [6 0 R 10 0 R] >>",
            10: b"<< /Parent 8 0 R >>",
        },
        fields=b"[8 0 R]",
    )
    six, seven = widgets_of(data)
    assert isinstance(six.field, Unknown) and invalid(six.field)
    assert invalid(six.export_value)
    assert isinstance(seven.field, Unknown) and invalid(seven.field)


def test_a12_3_the_widget_listed_twice_in_kids():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Btn /Ff 32768 /Opt [(A) (B)] /Kids [6 0 R 6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    six, seven = widgets_of(data)
    assert isinstance(six.field, Unknown) and invalid(six.field)
    assert invalid(six.export_value)
    assert seven.field.ref.object_number == 8
    assert invalid(seven.field.name)
    assert invalid(seven.export_value)


def test_a12_4_one_opt_entry_two_kids():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Btn /Ff 32768 /Opt [(A)] /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    six, seven = widgets_of(data)
    assert six.export_value == "A"
    assert invalid(seven.export_value)


def test_a12_5_opt_not_an_array_of_strings():
    data = forms_file(
        {
            6: b"<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
            b" /T (c) /FT /Btn /Opt [5] /Kids [7 0 R] >>",
            7: widget(b"/Parent 6 0 R"),
        },
        fields=b"[6 0 R]",
    )
    six, _ = widgets_of(data)
    assert invalid(six.export_value)


def test_a12_6_a_text_field_that_does_not_list_the_widget():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /FT /Tx /Kids [7 0 R] >>",
            7: widget(b"/Parent 8 0 R"),
        },
        fields=b"[8 0 R]",
    )
    six, _ = widgets_of(data)
    assert isinstance(six.field, Unknown) and invalid(six.field)
    assert invalid(six.export_value)


def test_a12_7_an_unknown_type():
    data = forms_file({6: widget(b"/T (x) /FT /Xy /Opt [(A)]")}, fields=b"[6 0 R]")
    six = one_widget(data)
    assert invalid(six.export_value)


# --- A13 to A17: the widget's own dictionary -------------------------------------------


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (b"/T (a) /AP << /N 9 0 R >>", None),
        (b"/T (a) /AP 5", "invalid"),
        (
            b"/T (a) /AP << /N << /Off 9 0 R /M#F6 9 0 R /A 9 0 R >> >>",
            (N(b"A"), N(b"M\xf6"), N(b"Off")),
        ),
    ],
    ids=["stream", "ap no dict", "sorted"],
)
def test_a13_states(entries, expected):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]", objects={9: STREAM})
    states = one_widget(data).states
    if expected == "invalid":
        assert invalid(states)
    else:
        assert states == expected


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        (b"[10 10 110]", "invalid"),
        (b"[110 30 10 10]", (10, 10, 110, 30)),
        (b"[1.5 2 3 4]", (1.5, 2, 3, 4)),
    ],
)
def test_a14_rectangles(rect, expected):
    data = forms_file({6: widget(b"/T (a)", rect=rect)}, fields=b"[6 0 R]")
    rect_ = one_widget(data).rect
    if expected == "invalid":
        assert invalid(rect_)
    else:
        assert (rect_.x0, rect_.y0, rect_.x1, rect_.y1) == expected


@pytest.mark.parametrize(
    ("entries", "expected"),
    [(b"/T (a) /F 2.0", "invalid"), (b"/T (a) /F -1", "invalid"), (b"/T (a)", 0)],
    ids=["real", "negative", "absent"],
)
def test_a15_annotation_flags(entries, expected):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    flags = one_widget(data).annotation_flags
    if expected == "invalid":
        assert invalid(flags)
    else:
        assert flags == expected


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (b"/T (a) /MK << /BC [] >>", ()),
        (b"/T (a) /MK << /BC [0.5] >>", (0.5,)),
        (b"/T (a) /MK << /BC [1 0 0] >>", (1.0, 0.0, 0.0)),
        (b"/T (a) /MK << /BC [0.5 0.5] >>", "invalid"),
        (b"/T (a) /MK << /BC [2] >>", "invalid"),
        (b"/T (a) /MK 1", "invalid"),
    ],
    ids=["empty", "one", "three", "two", "out of range", "mk no dict"],
)
def test_a16_colours(entries, expected):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    colour = one_widget(data).border_colour
    if expected == "invalid":
        assert invalid(colour)
    else:
        assert colour == DeviceColour(expected)


@pytest.mark.parametrize(
    ("entries", "width", "style"),
    [
        (b"/T (a) /BS << /W 2 /S /D >>", 2.0, b"D"),
        (b"/T (a) /BS << >>", None, None),
        (b"/T (a) /BS << /W (x) >>", "invalid", None),
        (b"/T (a) /BS << /S /D >>", None, b"D"),
    ],
    ids=["both", "empty", "w not a number", "s only"],
)
def test_a17_borders(entries, width, style):
    data = forms_file({6: widget(entries)}, fields=b"[6 0 R]")
    border = one_widget(data).border
    if width == "invalid":
        assert invalid(border)
    elif width is None and style is None:
        assert border is None
    else:
        assert border.width == width
        assert (border.style is None) if style is None else (border.style == N(style))


# --- A18 to A20: the field unknown, invalid nodes, in_form ------------------------------


def test_a18_a_parent_loop():
    data = forms_file(
        {6: widget(b"/Parent 8 0 R"), 8: b"<< /Parent 9 0 R >>", 9: b"<< /Parent 8 0 R >>"},
        fields=b"[8 0 R]",
    )
    assert invalid(one_widget(data).field)


def test_a18_a_parent_that_is_no_dictionary():
    data = forms_file({6: widget(b"/Parent 5")}, fields=b"[6 0 R]")
    assert invalid(one_widget(data).field)


def test_a18_thirty_four_dictionaries():
    objects = {6: widget(b"/Parent 7 0 R")}
    for number in range(7, 7 + 33):
        above = b"/Parent %d 0 R" % (number + 1) if number + 1 < 7 + 33 else b""
        objects[number] = b"<< /T (p) %s >>" % above
    data = forms_file(objects, fields=b"[7 0 R]")
    assert unreadable(one_widget(data).field)


def test_a18_a_parent_that_does_not_list_the_widget():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            7: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /Kids [7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    six, seven = widgets_of(data)
    assert invalid(six.field)
    assert not isinstance(seven.field, Unknown)


def test_a18_fields_listing_a_kid_widget():
    data = forms_file(
        {6: widget(b"/Parent 8 0 R"), 8: b"<< /T (f) /Kids [6 0 R] >>"},
        fields=b"[6 0 R 8 0 R]",
    )
    assert invalid(one_widget(data).field)


def test_a18_a_field_node_whose_kids_is_none():
    objects = {
        6: widget(b"/T (w) /FT /Tx /Parent 51 0 R /Kids [7 0 R]"),
        7: widget(b"/Parent 6 0 R"),
    }
    objects[20] = b"<< /T (n20) /Kids [21 0 R] >>"
    for number in range(21, 52):
        objects[number] = b"<< /T (n%d) /Parent %d 0 R /Kids [%d 0 R] >>" % (
            number,
            number - 1,
            number + 1,
        )
    objects[51] = b"<< /T (n51) /Parent 50 0 R /Kids [6 0 R] >>"
    data = forms_file(objects, fields=b"[20 0 R]")
    six, seven = widgets_of(data)
    assert unreadable(six.field)
    assert isinstance(seven.field, Unknown)


def test_a19_a_terminal_field_listed_in_two_kids():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /Parent 10 0 R /Kids [6 0 R] >>",
            10: b"<< /T (a) /Kids [8 0 R] >>",
            11: b"<< /T (b) /Kids [8 0 R] >>",
        },
        fields=b"[10 0 R 11 0 R]",
    )
    field = one_widget(data).field
    assert not isinstance(field, Unknown)
    assert field.ref.object_number == 8
    assert invalid(field.name)
    assert invalid(field.type)
    assert field.in_form is True


def test_a19_a_field_whose_parent_is_not_its_tree_parent():
    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R"),
            8: b"<< /T (f) /Parent 9 0 R /Kids [6 0 R] >>",
            9: b"<< /T (o) >>",
        },
        fields=b"[8 0 R 9 0 R]",
    )
    field = one_widget(data).field
    assert not isinstance(field, Unknown)
    assert invalid(field.name)


def test_a20_1_a_listed_field():
    data = forms_file({6: widget(b"/T (a) /FT /Tx")}, fields=b"[6 0 R]")
    assert one_widget(data).field.in_form is True


def test_a20_2_a_widget_not_listed_in_a_complete_tree():
    data = forms_file(
        {6: widget(b"/T (b)"), 8: b"<< /T (f) >>"},
        fields=b"[8 0 R]",
    )
    assert one_widget(data).field.in_form is False


def test_a20_3_no_acroform():
    data = plain_file({6: widget(b"/T (b)")}, annots=b"[6 0 R]")
    assert one_widget(data).field.in_form is False


def test_a20_4_a_widget_not_listed_in_a_cut_tree():
    objects = {6: widget(b"/T (b)")}
    objects[20] = b"<< /T (n20) /Kids [21 0 R] >>"
    for number in range(21, 53):
        objects[number] = b"<< /T (n%d) /Parent %d 0 R /Kids [%d 0 R] >>" % (
            number,
            number - 1,
            number + 1,
        )
    objects[52] = b"<< /T (n52) /Parent 51 0 R /Kids [53 0 R] >>"
    objects[53] = b"<< /T (leaf) /Parent 52 0 R >>"
    data = forms_file(objects, fields=b"[20 0 R]")
    in_form = one_widget(data).field.in_form
    assert unreadable(in_form)


def test_a20_5_a_null_in_fields_after_the_field():
    data = forms_file({6: widget(b"/T (a) /FT /Tx")}, fields=b"[6 0 R null]")
    assert one_widget(data).field.in_form is True


def test_a20_6_a_direct_widget_in_an_empty_tree():
    data = plain_file(
        {},
        annots=b"[<< /Type /Annot /Subtype /Widget /P 4 0 R /Rect [10 10 110 30]"
        b" /T (d) /FT /Tx >>]",
        acroform=b"<< /Fields [] >>",
    )
    in_form = one_widget(data).field.in_form
    assert unreadable(in_form)


# --- A21: fields() ------------------------------------------------------------------------


def test_a21_fields():
    data = forms_file(
        {
            8: b"<< /T (p) /FT /Btn /Kids [9 0 R 10 0 R] >>",
            9: b"<< /T (a) /Parent 8 0 R >>",
            10: b"<< /T (b) /Parent 8 0 R >>",
            11: b"<< /T (r) >>",
        },
        fields=b"[8 0 R 11 0 R]",
    )
    fields = fields_of(data)
    assert [field.name for field in fields] == ["p.a", "p.b", "r"]

    data = forms_file(
        {
            8: b"<< /T (p) /FT /Btn /Kids [9 0 R 10 0 R] >>",
            9: b"<< /T (a) /Parent 8 0 R >>",
            10: b"<< /T (b) /Parent 8 0 R >>",
            11: b"<< /T (r) >>",
        },
        fields=b"[8 0 R 11 0 R null]",
    )
    fields = fields_of(data)
    assert invalid(fields)

    objects = {20: b"<< /T (n20) /Kids [21 0 R] >>"}
    for number in range(21, 54):
        objects[number] = b"<< /T (n%d) /Parent %d 0 R /Kids [%d 0 R] >>" % (
            number,
            number - 1,
            number + 1,
        )
    objects[53] = b"<< /T (leaf) /Parent 52 0 R >>"
    data = forms_file(objects, fields=b"[20 0 R]")
    fields = fields_of(data)
    assert unreadable(fields)

    data = plain_file({})
    assert fields_of(data) == ()

    data = forms_file(
        {
            6: widget(b"/Parent 8 0 R /AS /Off"),
            7: widget(b"/Parent 8 0 R /AS /B"),
            8: b"<< /T (p) /FT /Btn /Ff 32768 /V /B /Opt [(A) (B)] /Kids [6 0 R 7 0 R] >>",
        },
        fields=b"[8 0 R]",
    )
    six = widgets_of(data)[0]
    (field,) = fields_of(data)
    assert field == six.field
