"""What this version of the library can do.

A capability is True only once it is implemented. Asking for one that is not
raises UnsupportedError: "unsupported" is a different answer from "absent"
and from "unknown" (docs/conventions.md, "Three answers").
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class Capability(StrEnum):
    PAGE_GEOMETRY = "page-geometry"
    USER_UNIT = "user-unit"
    OBJECT_NUMBERS = "object-numbers"
    RENDERING = "rendering"
    RECOVERY = "recovery"
    FORMS = "forms"
    GRAPHICS = "graphics"
    TEXT = "text"
    INSPECTION = "inspection"
    DECRYPTION = "decryption"
    FONT_POLICY_EMBEDDED_ONLY = "font-policy-embedded-only"


# Each entry becomes True in the change that implements it.
_TABLE: Mapping[Capability, bool] = MappingProxyType(
    {
        Capability.PAGE_GEOMETRY: True,
        Capability.USER_UNIT: True,
        Capability.OBJECT_NUMBERS: True,
        Capability.RENDERING: True,
        Capability.RECOVERY: True,
        Capability.FORMS: True,
        Capability.GRAPHICS: True,
        Capability.TEXT: True,
        Capability.INSPECTION: True,
        Capability.DECRYPTION: True,
        Capability.FONT_POLICY_EMBEDDED_ONLY: True,
    }
)


def capabilities() -> Mapping[Capability, bool]:
    """Every capability of this version, and whether it is available. Read-only."""
    return _TABLE


def require(capability: Capability | str) -> None:
    """Raise UnsupportedError when `capability` is not available.

    A name that is not a capability at all raises ValueError.
    """
    # Imported here: the errors module imports this one.
    from pdfacts.core.errors import UnsupportedError

    member = Capability(capability)
    if not _TABLE[member]:
        raise UnsupportedError(capability=member)
