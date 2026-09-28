"""A call made in another thread that waits for the engine lock meanwhile."""

from __future__ import annotations

import threading
from collections.abc import Callable

from pdfacts.core import runtime


class WatchedLock:
    """The engine lock; it tells when a thread other than the test's waits for it."""

    def __init__(self) -> None:
        self._lock = runtime.engine_lock
        self._test = threading.get_ident()
        self.waited = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if blocking and threading.get_ident() != self._test:
            self.waited.set()
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> WatchedLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def after_waiting(monkeypatch, call: Callable[[], object], meanwhile: Callable[[], None]) -> object:
    """What `call` gives, or raises, in another thread that waits for the lock while
    `meanwhile` runs in this one, which holds it."""
    lock = WatchedLock()
    monkeypatch.setattr(runtime, "engine_lock", lock)
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(call())
        except Exception as error:
            outcome.append(error)

    thread = threading.Thread(target=run)
    with lock:
        thread.start()
        assert lock.waited.wait(timeout=10)
        meanwhile()
    thread.join(timeout=10)
    assert not thread.is_alive()
    return outcome[0]
