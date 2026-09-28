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

    PDFium's header marks GetFontData, DeleteFont and one of MapFont and
    GetFont as required. The pinned build checks every callback for null
    before calling it (fpdfsdk/fpdf_sysfontinfo.cpp, CFX_ExternalFontInfo, on
    the branch chromium/7999); that is what makes a font info without
    callbacks safe. An engine upgrade has to check it again. Any version other
    than 1 or 2 is ignored without an error.
    """
    global _FONT_INFO
    if policy != "embedded-only":
        raise ValueError(f"unknown font policy {policy!r}")
    if _FONT_INFO is not None:
        return
    info = pdfium_c.FPDF_SYSFONTINFO()
    info.version = 1
    # Referenced before PDFium gets the pointer, so that an interrupt between
    # the two statements cannot leave PDFium holding freed memory.
    _FONT_INFO = info
    try:
        pdfium_c.FPDF_SetSystemFontInfo(ctypes.byref(info))
    except Exception:
        # ctypes refused the call, so PDFium never saw the pointer.
        _FONT_INFO = None
        raise
