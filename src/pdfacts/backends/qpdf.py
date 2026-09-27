"""QPDF backend: object access, rewrite and decryption.

The only module allowed to import pikepdf.
"""

from __future__ import annotations

from dataclasses import dataclass

import pikepdf


@dataclass(frozen=True)
class QpdfVersions:
    binding: str
    engine: str


def versions() -> QpdfVersions:
    return QpdfVersions(
        binding=str(pikepdf.__version__),
        engine=str(pikepdf.__libqpdf_version__),
    )
