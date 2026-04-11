"""Threading-based concurrency semaphore with priority waiter queue."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Iterator, AsyncIterator


@dataclass
class _Waiter:
    priority: bool
    event: threading.Event = field(default_factory=threading.Event)


class ConcurrencySemaphore:
    """Semaphore that gates concurrent access with priority support.

    Priority waiters are served before non-priority waiters, allowing
    main-agent calls to jump ahead of auxiliary calls in the queue.
    """

    def __init__(self, max_concurrent: int = 1) -> None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._max_concurrent = max_concurrent
        self._active = 0
        self._waiters: list[_Waiter] = []
        self._lock = threading.Lock()

    @property
    def max_concurrent(self) -> int:
        with self._lock:
            return self._max_concurrent

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @property
    def waiting(self) -> int:
        with self._lock:
            return len(self._waiters)

    def acquire(self, *, priority: bool = False, timeout: float | None = None) -> bool:
        with self._lock:
            if self._active < self._max_concurrent and not self._waiters:
                self._active += 1
                return True

            if timeout == 0:
                return False

            waiter = _Waiter(priority=priority)
            # Insert priority waiters after existing priority waiters
            # but before non-priority waiters.
            if priority:
                insert_idx = 0
                for i, w in enumerate(self._waiters):
                    if w.priority:
                        insert_idx = i + 1
                    else:
                        break
                else:
                    # All existing waiters are priority (or list is empty)
                    insert_idx = len(self._waiters)
                self._waiters.insert(insert_idx, waiter)
            else:
                self._waiters.append(waiter)

        # Wait outside the lock
        signaled = waiter.event.wait(timeout)

        if not signaled:
            with self._lock:
                # Race: waiter may have been signaled between timeout and
                # lock acquisition. If so, the slot was already granted.
                if waiter.event.is_set():
                    return True
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass  # already removed
                return False

        return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            if self._waiters and self._active < self._max_concurrent:
                waiter = self._waiters.pop(0)
                self._active += 1
                waiter.event.set()

    @contextmanager
    def slot(
        self, *, priority: bool = False, timeout: float | None = None
    ) -> Iterator[bool]:
        acquired = self.acquire(priority=priority, timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self.release()

    @asynccontextmanager
    async def async_slot(
        self, *, priority: bool = False, timeout: float | None = None
    ) -> AsyncIterator[bool]:
        acquired = await asyncio.to_thread(
            self.acquire, priority=priority, timeout=timeout
        )
        try:
            yield acquired
        finally:
            if acquired:
                self.release()
