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
        "FONT_POLICY_EMBEDDED_ONLY",
        "FONT_POLICY_HOST",
    ]


def test_the_table_has_an_entry_for_every_capability():
    assert set(capabilities()) == set(Capability)
    assert all(isinstance(value, bool) for value in capabilities().values())


def test_the_table_cannot_be_changed():
    with pytest.raises(TypeError):
        capabilities()[Capability.RENDERING] = True  # type: ignore[index]


def test_nothing_is_claimed_that_is_not_implemented():
    # Each capability becomes True in the change that implements it. None of
    # them is implemented yet; the host font policy never will be in M1.
    assert not any(capabilities().values())


def test_require_raises_for_a_capability_that_is_not_available():
    with pytest.raises(UnsupportedError) as raised:
        require(Capability.FONT_POLICY_HOST)
    assert raised.value.capability is Capability.FONT_POLICY_HOST


def test_require_passes_for_a_capability_that_is_available(monkeypatch):
    from pdfacts.core import capabilities as module

    table = dict(module.capabilities())
    table[Capability.RENDERING] = True
    monkeypatch.setattr(module, "_TABLE", table)
    assert require(Capability.RENDERING) is None
    with pytest.raises(UnsupportedError):
        require(Capability.RECOVERY)


def test_the_capability_names_are_exported():
    for name in ("Capability", "capabilities", "require"):
        assert getattr(pdfacts, name) is not None
        assert name in pdfacts.__all__
