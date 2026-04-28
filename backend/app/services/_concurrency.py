"""Live-resizable concurrency limiter.

`asyncio.Semaphore` has a fatal flaw for our use case: capacity is fixed
at construction. When the operator changes `max_concurrent_runs` from 4
to 2, the previous fix recreated the semaphore — but the 4 already-
acquired slots couldn't return their permits to the new (size-2)
semaphore, so for a window of N minutes the new semaphore counted "0
of 2 in use" while 4 actual runs were happening. Even worse, going
from 2→4 mid-flight gave new tasks no advantage because the in-flight
two were still gated against the smaller object.

This module provides `ResizableLimiter`, a refcount + condition-variable
implementation that:

  - tracks `current` (in-flight count) independent of capacity
  - blocks new acquires when `current >= capacity`
  - releases by decrementing `current` and waking a waiter
  - lets `set_capacity(n)` change the ceiling at runtime; raising it
    immediately wakes enough waiters to refill, lowering it just makes
    new acquires wait until the existing in-flight count drops below
    the new cap (no permits are forcibly revoked — that would mean
    cancelling running tasks, which we don't do).

Thread-safety: single-event-loop only (we're not threaded). Reentrancy
not supported — same task acquiring twice will deadlock; don't do that.
"""

from __future__ import annotations

import asyncio


class ResizableLimiter:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._current = 0
        self._cond = asyncio.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def in_flight(self) -> int:
        return self._current

    async def set_capacity(self, n: int) -> None:
        """Change the cap. Wakes waiters if raised; lowering is silent
        (already-running tasks are not cancelled, the new ceiling just
        applies to not-yet-acquired requests)."""
        if n < 1:
            raise ValueError(f"capacity must be >= 1, got {n}")
        async with self._cond:
            self._capacity = n
            # notify_all so any waiters can re-check; only those that
            # fit under the new cap will actually proceed.
            self._cond.notify_all()

    async def acquire(self) -> None:
        async with self._cond:
            while self._current >= self._capacity:
                await self._cond.wait()
            self._current += 1

    async def release(self) -> None:
        async with self._cond:
            self._current = max(0, self._current - 1)
            self._cond.notify(1)

    # `async with limiter:` ergonomics
    async def __aenter__(self) -> ResizableLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.release()
