"""ResizableLimiter — the concurrency primitive that replaces asyncio.Semaphore.

The limiter has to handle the awkward case the previous fix couldn't:
"operator changes max_concurrent_runs from 4 to 2 while 4 runs are
in-flight." The semaphore approach stranded those 4 acquired slots
against an old object; this version's `current` counter decouples the
in-flight count from the cap so the new limit takes effect for new
acquires while the running tasks finish naturally.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services._concurrency import ResizableLimiter


@pytest.mark.asyncio
async def test_acquire_blocks_at_capacity():
    lim = ResizableLimiter(2)
    await lim.acquire()
    await lim.acquire()

    # Third acquire should block — wait_for with a tight timeout proves it.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lim.acquire(), timeout=0.05)
    assert lim.in_flight == 2


@pytest.mark.asyncio
async def test_release_wakes_a_waiter():
    lim = ResizableLimiter(1)
    await lim.acquire()
    waiter = asyncio.create_task(lim.acquire())
    await asyncio.sleep(0.01)
    assert not waiter.done(), "should be blocked"
    await lim.release()
    await asyncio.wait_for(waiter, timeout=0.5)
    assert lim.in_flight == 1


@pytest.mark.asyncio
async def test_set_capacity_raise_wakes_waiters():
    lim = ResizableLimiter(1)
    await lim.acquire()
    # Two more would normally block forever
    waiters = [asyncio.create_task(lim.acquire()) for _ in range(2)]
    await asyncio.sleep(0.01)
    assert all(not w.done() for w in waiters)

    await lim.set_capacity(3)
    await asyncio.wait_for(asyncio.gather(*waiters), timeout=0.5)
    assert lim.in_flight == 3


@pytest.mark.asyncio
async def test_set_capacity_lower_does_not_cancel_running():
    """Lowering the cap doesn't yank permits from in-flight tasks; new
    acquires just have to wait until the in-flight count drops below
    the new cap."""
    lim = ResizableLimiter(4)
    for _ in range(4):
        await lim.acquire()
    assert lim.in_flight == 4

    await lim.set_capacity(2)
    assert lim.in_flight == 4  # unchanged
    assert lim.capacity == 2

    # New acquire blocks because in_flight (4) >= capacity (2)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lim.acquire(), timeout=0.05)

    # Release down to 1 below cap — a new acquire should succeed
    await lim.release()
    await lim.release()
    await lim.release()  # now in_flight=1, cap=2 → next acquire fits
    await asyncio.wait_for(lim.acquire(), timeout=0.5)
    assert lim.in_flight == 2


@pytest.mark.asyncio
async def test_async_with_acquires_and_releases():
    lim = ResizableLimiter(2)
    async with lim:
        assert lim.in_flight == 1
        async with lim:
            assert lim.in_flight == 2
        assert lim.in_flight == 1
    assert lim.in_flight == 0


@pytest.mark.asyncio
async def test_set_capacity_zero_rejected():
    lim = ResizableLimiter(2)
    with pytest.raises(ValueError):
        await lim.set_capacity(0)
    with pytest.raises(ValueError):
        ResizableLimiter(0)


@pytest.mark.asyncio
async def test_release_below_zero_clamped():
    """Defensive: extra release() calls (bug elsewhere) shouldn't underflow
    in_flight to negative — clamp at 0 so the limiter stays usable."""
    lim = ResizableLimiter(1)
    await lim.release()
    assert lim.in_flight == 0
    await lim.acquire()
    assert lim.in_flight == 1
