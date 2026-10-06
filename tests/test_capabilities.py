"""The capability table says what this version of the library can do, and nothing more."""

from __future__ import annotations

import pytest

import pdfacts
from pdfacts import Capability, UnsupportedError, capabilities, require


def test_the_members_are_exactly_these():
    assert [member.name for member in Capability] == [
        "PAGE_GEOMETRY",
        "USER_UNIT",
        "OBJECT_NUMBERS",
        "RENDERING",
        "RECOVERY",
        "FORMS",
        "GRAPHICS",
        "TEXT",
        "INSPECTION",
        "DECRYPTION",
        "FONT_POLICY_EMBEDDED_ONLY",
    ]


def test_the_table_has_an_entry_for_every_capability():
    assert set(capabilities()) == set(Capability)
    assert all(isinstance(value, bool) for value in capabilities().values())


def test_the_table_cannot_be_changed():
    with pytest.raises(TypeError):
        capabilities()[Capability.RENDERING] = True  # type: ignore[index]


def test_nothing_is_claimed_that_is_not_implemented():
    # Each capability becomes True in the change that implements it.
    available = {capability for capability, value in capabilities().items() if value}
    assert available == {
        Capability.PAGE_GEOMETRY,
        Capability.USER_UNIT,
        Capability.OBJECT_NUMBERS,
        Capability.RENDERING,
        Capability.RECOVERY,
        Capability.FORMS,
        Capability.GRAPHICS,
        Capability.TEXT,
        Capability.INSPECTION,
        Capability.DECRYPTION,
        Capability.FONT_POLICY_EMBEDDED_ONLY,
    }


def not_available(monkeypatch, capability: Capability) -> None:
    """Make `capability` one this version does not have: every one it lists
    is implemented, so the table is changed for the test."""
    from pdfacts.core import capabilities as module

    table = dict(module.capabilities())
    table[capability] = False
    monkeypatch.setattr(module, "_TABLE", table)


def test_every_capability_of_this_version_is_available():
    assert all(capabilities().values())


def test_require_raises_for_a_capability_that_is_not_available(monkeypatch):
    not_available(monkeypatch, Capability.TEXT)
    with pytest.raises(UnsupportedError) as raised:
        require(Capability.TEXT)
    assert raised.value.capability is Capability.TEXT


def test_require_takes_a_capability_by_its_value_and_rejects_other_names(monkeypatch):
    not_available(monkeypatch, Capability.TEXT)
    with pytest.raises(UnsupportedError):
        require("text")
    with pytest.raises(ValueError):
        require("bogus")
    with pytest.raises(ValueError):
        require("font-policy-host")


def test_require_passes_for_a_capability_that_is_available(monkeypatch):
    not_available(monkeypatch, Capability.TEXT)
    assert require(Capability.RENDERING) is None
    with pytest.raises(UnsupportedError):
        require(Capability.TEXT)


def test_the_capability_names_are_exported():
    for name in ("Capability", "capabilities", "require"):
        assert getattr(pdfacts, name) is not None
        assert name in pdfacts.__all__
