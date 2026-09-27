"""PDFium backend: text, geometry and rendering.

The only module allowed to import pypdfium2.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import pypdfium2
import pypdfium2.raw as pdfium_c

# The font info installed by install_font_policy(). PDFium keeps a pointer to
# it, so it has to stay referenced for the life of the process.
_FONT_INFO: pdfium_c.FPDF_SYSFONTINFO | None = None


@dataclass(frozen=True)
class PdfiumVersions:
    binding: str
    engine: str
    origin: str
    flags: tuple[str, ...]


def versions() -> PdfiumVersions:
    info = pypdfium2.PDFIUM_INFO
    return PdfiumVersions(
        binding=str(pypdfium2.PYPDFIUM_INFO),
        engine=str(info),
        origin=str(info.origin),
        flags=tuple(str(flag) for flag in info.flags),
    )


def install_font_policy(policy: str) -> None:
    """Install the font policy for the whole process.

    "embedded-only": fonts installed on the host are ignored. PDFium gets a
    font info of version 1 whose callbacks are all null, so it uses only the
    fonts embedded in a file and its own built-in substitutes. A second call
    does nothing.
    """
    global _FONT_INFO
    if policy != "embedded-only":
        raise ValueError(f"unknown font policy {policy!r}")
    if _FONT_INFO is not None:
        return
    info = pdfium_c.FPDF_SYSFONTINFO()
    info.version = 1
    pdfium_c.FPDF_SetSystemFontInfo(ctypes.byref(info))
    _FONT_INFO = info
