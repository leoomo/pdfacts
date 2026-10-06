"""Identity of the engines this process is running on.

Extracted facts depend on the exact engine build, so every digest, receipt or
bug report should be able to name it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import metadata

from pdfacts.backends import pdfium, qpdf
from pdfacts.core import runtime


@dataclass(frozen=True)
class EngineInfo:
    pdfacts: str
    pypdfium2: str
    pdfium: str
    pdfium_origin: str
    pdfium_flags: tuple[str, ...]
    pikepdf: str
    qpdf: str
    # The font policy changes the facts, so a receipt has to name it; None
    # until the runtime has started.
    font_policy: runtime.FontPolicy | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def engine_info() -> EngineInfo:
    """Return the versions of pdfacts and of both engines, and the font policy.

    The font policy is None until the runtime has started; asking does not
    start it.
    """
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
        font_policy=runtime.active_font_policy(),
    )
