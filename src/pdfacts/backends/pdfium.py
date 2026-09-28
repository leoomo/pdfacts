"""PDFium backend: text, geometry and rendering.

The only module allowed to import pypdfium2.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import pypdfium2
import pypdfium2.raw as pdfium_c

# Every font info handed to PDFium by install_font_policy(). PDFium keeps a
# pointer to the last one, so none may ever be freed: the list is never
# cleared. It has more than one entry only when an exception interrupted an
# installation and it was repeated.
_FONT_INFOS: list[pdfium_c.FPDF_SYSFONTINFO] = []
# True once FPDF_SetSystemFontInfo has returned.
_font_policy_installed = False


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
    global _font_policy_installed
    if policy != "embedded-only":
        raise ValueError(f"unknown font policy {policy!r}")
    if _font_policy_installed:
        return
    info = pdfium_c.FPDF_SYSFONTINFO()
    info.version = 1
    # Kept before PDFium gets the pointer and never dropped. An exception, for
    # example from a signal handler, can arrive just before the call or just
    # after it, and after it PDFium holds the pointer.
    _FONT_INFOS.append(info)
    pdfium_c.FPDF_SetSystemFontInfo(ctypes.byref(info))
    _font_policy_installed = True
