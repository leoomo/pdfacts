"""Engine start, font policy and the lock that serializes engine calls.

PDFium is initialized once per process, its font configuration belongs to the
process, and it must not be called from several threads at once
(docs/architecture.md, "Process-wide constraints"). This module holds that
state. It starts no processes and limits neither time nor memory; isolation
between documents is the caller's job.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum

from pdfacts.backends import pdfium
from pdfacts.core.capabilities import Capability, require
from pdfacts.core.errors import ConfigurationError


class FontPolicy(StrEnum):
    # Only fonts embedded in the file and the engine's built-in substitutes.
    EMBEDDED_ONLY = "embedded-only"
    # Fonts installed on the host as well. Not implemented.
    HOST = "host"


# Every call into an engine, PDFium or QPDF, happens while this is held. The
# backends do not take it; the core does, once per operation, through engine().
# start() takes it directly.
engine_lock = threading.RLock()

# Closes that a finalizer could not run because another thread held the lock.
_deferred: list[Callable[[], None]] = []


@contextmanager
def engine() -> Iterator[None]:
    """Hold the engine lock; first run the closes finalizers handed over."""
    with engine_lock:
        _run_deferred()
        yield


def close_soon(close: Callable[[], None]) -> None:
    """Run `close` under the engine lock now if it is free, or hand it over.

    For finalizers, which must not wait for a lock: the garbage collector may
    run them in any thread, and at exit a daemon thread may hold the lock for
    ever. The next holder of engine() runs what was handed over. `close` must
    not raise; a MemoryError it raises surfaces in whichever call runs it.
    """
    if engine_lock.acquire(blocking=False):
        try:
            close()
        finally:
            engine_lock.release()
        return
    _deferred.append(close)
    # The holder may have let the lock go between the attempt and the append;
    # then nobody would run the close before the next operation.
    if engine_lock.acquire(blocking=False):
        try:
            _run_deferred()
        finally:
            engine_lock.release()


def _run_deferred() -> None:
    while _deferred:
        _deferred.pop()()


_requested = FontPolicy.EMBEDDED_ONLY
_active: FontPolicy | None = None


def configure(*, font_policy: FontPolicy | str = FontPolicy.EMBEDDED_ONLY) -> None:
    """Choose the font policy before the runtime starts.

    After the start, asking for the active policy again does nothing, and
    asking for another one raises ConfigurationError.
    """
    global _requested
    policy = FontPolicy(font_policy)
    if policy is FontPolicy.HOST:
        require(Capability.FONT_POLICY_HOST)
    with engine_lock:
        if _active is not None:
            if policy is not _active:
                raise ConfigurationError(
                    f"the font policy {_active} is active; it cannot change to {policy} "
                    "in this process"
                )
            return
        _requested = policy


def start() -> None:
    """Install the font policy. Idempotent.

    Every operation that reads a document has to call it first, so that a
    caller of the library never has to.
    """
    global _active
    with engine_lock:
        if _active is not None:
            return
        pdfium.install_font_policy(_requested.value)
        _active = _requested


def active_font_policy() -> FontPolicy | None:
    """The installed policy, or None before the start."""
    return _active
