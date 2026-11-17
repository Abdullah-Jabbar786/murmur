"""Deterministic ACK/cancel race regression, including Python 3.10/3.11."""

import asyncio

import pytest

from murmurtp.header import DATA, MSS
from murmurtp.adaptive import AdaptiveRtoConfig, AdaptiveRtoTransport
from murmurtp.congestion import CongestionTransport
from murmurtp.flow import FlowControlConfig, FlowControlTransport
from murmurtp.sack import SelectiveRepeatTransport
from murmurtp.window import SlidingWindowConfig


@pytest.mark.parametrize("final_ack", [False, True])
@pytest.mark.parametrize("stage", [3, 4, 5, 6])
async def test_cancellation_wins_when_ack_waiter_completes(final_ack, stage):
    config_type = SlidingWindowConfig if stage == 3 else AdaptiveRtoConfig
    if stage == 6:
        config_type = FlowControlConfig
    extra = {} if stage == 3 else {"min_rto": 0.01}
    config = config_type(**extra, window_packets=2, initial_rto=0.05, max_rto=0.1,
                                 inactivity_timeout=0.5)
    transport = SelectiveRepeatTransport if stage == 3 else AdaptiveRtoTransport
    if stage == 5:
        transport = CongestionTransport
    elif stage == 6:
        transport = FlowControlTransport
    server, client = transport(config=config), transport(config=config)
    sending = None
    waiters = []
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        original = client._send

        def send(header, payload, addr):
            if header.flags != DATA or header.seq_num == 0:
                original(header, payload, addr)

        client._send = send  # Permit packet 0; keep a later packet missing when requested.

        class CancelOnAck(asyncio.Event):
            async def wait(self):
                waiters.append(asyncio.current_task())
                result = await super().wait()
                # Schedule cancellation before the inner task's completion
                # callbacks resume the sender. No network timing assumption.
                asyncio.get_running_loop().call_soon(sending.cancel)
                return result

        outgoing._ack_ready = CancelOnAck()
        sending = asyncio.create_task(outgoing.send(b"x" * (MSS if final_ack else 2 * MSS)))
        with pytest.raises(asyncio.CancelledError):
            await sending
        assert sending.cancelled() and outgoing.state == "CLOSED"
        assert not outgoing._pending and not outgoing._flights and not outgoing._sacked
        assert outgoing.stats()["bytes_in_flight"] == 0
        assert waiters and all(task.done() for task in waiters)
        assert await incoming.recv() == b"x" * MSS
        with pytest.raises(ConnectionResetError):
            await asyncio.wait_for(incoming.recv(), 2)
    finally:
        if sending is not None:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
        await asyncio.gather(server.close(), client.close())
        assert not server._tasks and not client._tasks
