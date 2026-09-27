"""The error types of pdfacts: their hierarchy, their attributes and their messages."""

from __future__ import annotations

import pickle

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


def test_the_attributes_are_keyword_only():
    with pytest.raises(TypeError):
        DamagedFileError("no cross-reference table")  # type: ignore[misc]
    with pytest.raises(TypeError):
        LimitExceededError("max_pages", 30, 31)  # type: ignore[misc]


@pytest.mark.parametrize(("error", "attributes"), INSTANCES, ids=lambda e: type(e).__name__)
def test_every_error_survives_pickling(error, attributes):
    # Callers are advised to use one process per document; errors cross that boundary.
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is type(error)
    assert str(copy) == str(error)
    for name, value in attributes.items():
        assert getattr(copy, name) == value


def test_every_error_is_exported():
    for error, _ in HIERARCHY:
        if error is not Exception:
            assert getattr(pdfacts, error.__name__) is error
            assert error.__name__ in pdfacts.__all__
