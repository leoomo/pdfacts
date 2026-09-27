"""Identity of the engines this process is running on.

Extracted facts depend on the exact engine build, so every digest, receipt or
bug report should be able to name it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata

from pdfacts.backends import pdfium, qpdf


@dataclass(frozen=True)
class EngineInfo:
    pdfacts: str
    pypdfium2: str
    pdfium: str
    pdfium_origin: str
    pdfium_flags: tuple[str, ...]
    pikepdf: str
    qpdf: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def engine_info() -> EngineInfo:
    """Return the versions of pdfacts and of both engines."""
    pdfium_versions = pdfium.versions()
    qpdf_versions = qpdf.versions()
    return EngineInfo(
        pdfacts=metadata.version("pdfacts"),
        pypdfium2=pdfium_versions.binding,
        pdfium=pdfium_versions.engine,
        pdfium_origin=pdfium_versions.origin,
        pdfium_flags=pdfium_versions.flags,
        pikepdf=qpdf_versions.binding,
        qpdf=qpdf_versions.engine,
    )
