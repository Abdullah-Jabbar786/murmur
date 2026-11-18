"""Bounded waits that preserve caller cancellation on Python 3.10/3.11."""

import asyncio


async def wait_for_cancel_safe(awaitable, timeout):
    """Own/join the inner task, with external cancellation taking precedence.

    Unlike legacy asyncio.wait_for, simultaneous inner completion must not turn
    a shutdown cancellation into success (CPython issue 86296). Callers supply
    cancellation-cooperative operations, just as with asyncio.wait_for.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout)
        if not done:
            raise asyncio.TimeoutError
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif not task.cancelled():
            # Consume an exception if cancellation won over a completed child.
            # A done task needs no extra event-loop turn merely to join it.
            task.exception()
