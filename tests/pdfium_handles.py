"""Which PDFium document handles a test loads, and which of them PDFium frees.

Only handles loaded while the watch runs count: the garbage collector can free
documents that earlier tests left behind at any moment of a later test.
"""

from __future__ import annotations

import ctypes
import gc
from collections.abc import Callable

import pypdfium2.raw


def _address(handle: object) -> int:
    return ctypes.cast(handle, ctypes.c_void_p).value or 0


class Handles:
    def __init__(self) -> None:
        self.loaded: set[int] = set()
        # The address of every handle of this test that PDFium was asked to free.
        self.freed: list[int] = []
        # What `on_free` returned at each of those.
        self.seen: list[object] = []

    def owns(self, handle: object) -> bool:
        """Whether `handle` was loaded while the watch runs."""
        return _address(handle) in self.loaded


def watch(monkeypatch, on_free: Callable[[], object] | None = None) -> Handles:
    """Record the loads and frees from now on; `on_free` runs before each free."""
    gc.collect()
    handles = Handles()
    load, free = pypdfium2.raw.FPDF_LoadMemDocument64, pypdfium2.raw.FPDF_CloseDocument

    def loading(*args):
        handle = load(*args)
        if handle:
            handles.loaded.add(_address(handle))
        return handle

    def freeing(handle):
        if _address(handle) in handles.loaded:
            handles.freed.append(_address(handle))
            if on_free is not None:
                handles.seen.append(on_free())
        return free(handle)

    monkeypatch.setattr(pypdfium2.raw, "FPDF_LoadMemDocument64", loading)
    monkeypatch.setattr(pypdfium2.raw, "FPDF_CloseDocument", freeing)
    return handles
