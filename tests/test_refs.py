"""PageRef: a page's index and, when known, its object in the file."""

from __future__ import annotations

import dataclasses

import pytest

from pdfacts import PageRef, Unknown, UnknownReason

UNKNOWN = Unknown(UnknownReason.ENGINES_DISAGREE, "not the same page")


def test_a_known_reference():
    ref = PageRef(page_index=2, object_number=8, generation=0)
    assert (ref.page_index, ref.object_number, ref.generation) == (2, 8, 0)


def test_an_unknown_object():
    ref = PageRef(page_index=0, object_number=UNKNOWN, generation=UNKNOWN)
    assert ref.object_number is UNKNOWN and ref.generation is UNKNOWN


def test_it_is_given_by_name_and_cannot_be_changed():
    with pytest.raises(TypeError):
        PageRef(0, 4, 0)  # type: ignore[misc]
    ref = PageRef(page_index=0, object_number=4, generation=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.object_number = 5  # type: ignore[misc]


def test_equal_references_are_equal():
    assert PageRef(page_index=1, object_number=6, generation=0) == PageRef(
        page_index=1, object_number=6, generation=0
    )


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"page_index": -1}, ValueError),
        ({"page_index": True}, TypeError),
        ({"page_index": 1.0}, TypeError),
        ({"object_number": 0}, ValueError),
        ({"object_number": -4}, ValueError),
        ({"object_number": True}, TypeError),
        ({"object_number": "4"}, TypeError),
        ({"object_number": None}, TypeError),
        ({"generation": -1}, ValueError),
        ({"generation": False}, TypeError),
        ({"generation": 0.0}, TypeError),
    ],
    ids=repr,
)
def test_what_is_not_a_reference_is_refused(fields, error):
    values = {"page_index": 0, "object_number": 4, "generation": 0} | fields
    with pytest.raises(error):
        PageRef(**values)
