"""Deterministic completion/cancellation races at peer shutdown boundaries."""

import asyncio
from types import SimpleNamespace

import pytest

from core.asyncio_utils import wait_for_cancel_safe
from peer.node import _Session
from peer.swarm import _Link, _Pending
from peer.wire import NotInterested


async def assert_cancelled(task):
    # A broken implementation may swallow cancellation and block again.
    try:
        done, _ = await asyncio.wait((task,), timeout=1)
        assert task in done, "caller cancellation was lost"
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_bounded_wait_returns_result_and_propagates_error():
    async def operation(error=False):
        if error:
            raise OSError("operation failed")
        return 42
    assert await wait_for_cancel_safe(operation(), 1) == 42
    with pytest.raises(OSError, match="operation failed"):
        await wait_for_cancel_safe(operation(True), 1)


@pytest.mark.parametrize("cancel", [False, True])
async def test_bounded_wait_joins_inner_task_on_timeout_or_cancel(cancel):
    started, finished = asyncio.Event(), asyncio.Event()
    children = []

    async def operation():
        children.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    task = asyncio.create_task(wait_for_cancel_safe(operation(), 5 if cancel else .02))
    await started.wait()
    if cancel:
        task.cancel()
        await assert_cancelled(task)
    else:
        with pytest.raises(asyncio.TimeoutError):
            await task
    assert finished.is_set() and all(child.done() for child in children)


async def test_cancellation_wins_over_simultaneous_inner_completion():
    children = []

    async def operation():
        children.append(asyncio.current_task())
        asyncio.get_running_loop().call_soon(parent.cancel)
        return "completed"

    parent = asyncio.create_task(wait_for_cancel_safe(operation(), 1))
    await assert_cancelled(parent)
    assert children and all(child.done() for child in children)


async def test_session_send_does_not_swallow_shutdown_at_send_completion():
    children = []

    async def send(raw):
        assert raw  # The real wire encoder is exercised.
        children.append(asyncio.current_task())
        asyncio.get_running_loop().call_soon(parent.cancel)

    session = _Session(SimpleNamespace(send=send),
                       SimpleNamespace(metadata=SimpleNamespace(num_pieces=1)), b"p" * 20, 1)
    parent = asyncio.create_task(session.send(NotInterested()))
    await assert_cancelled(parent)
    assert children and all(child.done() for child in children)


async def test_download_worker_cancellation_wins_over_request_wakeup():
    node = SimpleNamespace(store=SimpleNamespace(owned_pieces=frozenset()),
                           peer_id=b"p" * 20, io_timeout=1, _claims={},
                           _wake_all=lambda: None)
    link = _Link(node, SimpleNamespace(), True)
    pending = asyncio.get_running_loop().create_future()
    link.pending[(0, 0, 1)] = _Pending(pending, asyncio.get_running_loop().time() + 5)
    children = []

    class CancelOnWake(asyncio.Event):
        async def wait(self):
            children.append(asyncio.current_task())
            if len(children) > 1:
                # If the first cancellation is lost, block so the test's final
                # cleanup cancellation can still terminate the broken worker.
                return await super().wait()
            asyncio.get_running_loop().call_soon(parent.cancel)
            return True

    link.wake = CancelOnWake()
    parent = asyncio.create_task(link.download_loop())
    await assert_cancelled(parent)
    assert pending.cancelled() and not link.pending
    assert children and all(child.done() for child in children)
