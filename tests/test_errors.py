"""The error types of pdfacts: their hierarchy, their attributes and their messages."""

from __future__ import annotations

import pickle
import typing

import pytest

import pdfacts
from pdfacts import (
    Capability,
    ClosedError,
    ConfigurationError,
    DamagedFileError,
    EmptyInputError,
    EngineError,
    InputError,
    LimitError,
    LimitExceededError,
    LimitUncheckedError,
    NotPdfError,
    PasswordRequiredError,
    PdfactsError,
    RecoveryFailedError,
    Unknown,
    UnknownReason,
    UnsupportedError,
)

HIERARCHY = [
    (PdfactsError, Exception),
    (InputError, PdfactsError),
    (EmptyInputError, InputError),
    (NotPdfError, InputError),
    (DamagedFileError, InputError),
    (PasswordRequiredError, PdfactsError),
    (LimitError, PdfactsError),
    (LimitExceededError, LimitError),
    (LimitUncheckedError, LimitError),
    (RecoveryFailedError, PdfactsError),
    (UnsupportedError, PdfactsError),
    (ClosedError, PdfactsError),
    (ConfigurationError, PdfactsError),
    (EngineError, PdfactsError),
]

UNREADABLE = Unknown(UnknownReason.UNREADABLE, "no /Size in the trailer")

# One instance of every error that can be raised, with its attributes.
INSTANCES = [
    (EmptyInputError(), {}),
    (NotPdfError(), {}),
    (DamagedFileError(detail="no cross-reference table"), {"detail": "no cross-reference table"}),
    (PasswordRequiredError(password_was_given=True), {"password_was_given": True}),
    (
        LimitExceededError(limit="max_pages", allowed=30, found=31),
        {"limit": "max_pages", "allowed": 30, "found": 31},
    ),
    (
        LimitUncheckedError(limit="max_objects", allowed=1000, reason=UNREADABLE),
        {"limit": "max_objects", "allowed": 1000, "reason": UNREADABLE},
    ),
    (RecoveryFailedError(detail="no pages left"), {"detail": "no pages left"}),
    (
        UnsupportedError(capability=Capability.FONT_POLICY_HOST),
        {"capability": Capability.FONT_POLICY_HOST},
    ),
    (ClosedError(), {}),
    (ConfigurationError("the font policy is already set"), {}),
    (
        EngineError(engine="qpdf", detail="unexpected state"),
        {"engine": "qpdf", "detail": "unexpected state"},
    ),
]


@pytest.mark.parametrize(("error", "parent"), HIERARCHY, ids=lambda c: c.__name__)
def test_every_error_has_its_place_in_the_hierarchy(error, parent):
    assert issubclass(error, parent)
    assert error.__bases__ == (parent,)


def test_input_errors_are_caught_together_and_a_password_is_not_an_input_error():
    with pytest.raises(InputError):
        raise EmptyInputError()
    with pytest.raises(PdfactsError):
        raise EmptyInputError()
    with pytest.raises(PasswordRequiredError):
        try:
            raise PasswordRequiredError(password_was_given=False)
        except InputError:  # pragma: no cover - must not be reached
            pytest.fail("a missing password is not an input error")


@pytest.mark.parametrize(("error", "attributes"), INSTANCES, ids=lambda e: type(e).__name__)
def test_every_attribute_is_set_from_its_keyword(error, attributes):
    for name, value in attributes.items():
        assert getattr(error, name) == value


@pytest.mark.parametrize(("error", "attributes"), INSTANCES, ids=lambda e: type(e).__name__)
def test_every_message_is_not_empty_and_names_the_attributes(error, attributes):
    message = str(error)
    assert message
    for value in attributes.values():
        if isinstance(value, bool):
            continue
        shown = value.value if isinstance(value, Capability) else value
        shown = value.reason.value if isinstance(value, Unknown) else shown
        assert str(shown) in message


def test_the_limit_message_says_which_limit_what_was_allowed_and_what_was_found():
    message = str(LimitExceededError(limit="max_pages", allowed=30, found=31))
    assert "max_pages" in message and "30" in message and "31" in message


def test_the_password_message_says_whether_a_password_was_given():
    assert "wrong" in str(PasswordRequiredError(password_was_given=True))
    assert "needed" in str(PasswordRequiredError(password_was_given=False))


@pytest.mark.parametrize(
    ("error", "attributes"),
    [(error, attributes) for error, attributes in INSTANCES if attributes],
    ids=lambda e: type(e).__name__,
)
def test_the_attributes_are_keyword_only(error, attributes):
    with pytest.raises(TypeError):
        type(error)(*attributes.values())


def test_a_limit_error_needs_its_limit_and_allowed_value():
    with pytest.raises(TypeError):
        LimitError("a limit was hit")  # type: ignore[call-arg]
    error = LimitError("a limit was hit", limit="max_pages", allowed=3)
    assert (error.limit, error.allowed) == ("max_pages", 3)


@pytest.mark.parametrize(
    "error",
    [
        DamagedFileError(detail=""),
        RecoveryFailedError(detail=""),
        EngineError(engine="qpdf", detail=""),
    ],
    ids=lambda e: type(e).__name__,
)
def test_an_empty_detail_leaves_no_dangling_colon(error):
    assert not str(error).rstrip().endswith(":")


def test_a_capability_can_be_given_by_its_value_and_nothing_else():
    assert UnsupportedError(capability="rendering").capability is Capability.RENDERING
    with pytest.raises(ValueError):
        UnsupportedError(capability="bogus")


def test_the_reason_of_an_unchecked_limit_has_to_be_an_unknown_value():
    with pytest.raises(TypeError):
        LimitUncheckedError(limit="max_objects", allowed=10, reason="unreadable")  # type: ignore[arg-type]


def test_the_annotations_of_the_errors_resolve():
    for error, _ in HIERARCHY:
        typing.get_type_hints(error)


@pytest.mark.parametrize(("error", "attributes"), INSTANCES, ids=lambda e: type(e).__name__)
def test_every_error_survives_pickling(error, attributes):
    # Callers are advised to use one process per document; errors cross that boundary.
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is type(error)
    assert str(copy) == str(error)
    for name, value in attributes.items():
        assert getattr(copy, name) == value


def test_notes_and_extra_attributes_survive_pickling():
    error = DamagedFileError(detail="no cross-reference table")
    error.add_note("page 3 was being read")
    error.file_name = "form.pdf"
    copy = pickle.loads(pickle.dumps(error))
    assert copy.__notes__ == ["page 3 was being read"]
    assert copy.file_name == "form.pdf"
    assert copy.detail == "no cross-reference table"


def test_a_subclass_of_a_later_change_survives_pickling():
    copy = pickle.loads(pickle.dumps(_LaterError()))
    assert type(copy) is _LaterError
    assert str(copy) == "a later error"
    assert copy.extra == 1


class _LaterError(InputError):
    """Stands for an error a later change adds: no fields, one attribute set directly."""

    def __init__(self) -> None:
        Exception.__init__(self, "a later error")
        self.extra = 1


def test_every_error_is_exported():
    for error, _ in HIERARCHY:
        if error is not Exception:
            assert getattr(pdfacts, error.__name__) is error
            assert error.__name__ in pdfacts.__all__
