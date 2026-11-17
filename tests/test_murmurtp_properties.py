"""A2.3: bounded, reproducible Hypothesis properties over real localhost UDP."""

import asyncio
from dataclasses import dataclass

import pytest
from hypothesis import example, given, settings, strategies as st

from murmurtp.connection import StopAndWaitConfig
from murmurtp.adaptive import AdaptiveRtoConfig, AdaptiveRtoTransport
from murmurtp.congestion import CongestionTransport
from murmurtp.header import DATA, MSS
from murmurtp.lossy import LossConfig
from murmurtp.sack import SelectiveRepeatTransport
from murmurtp.transport import StopAndWaitTransport
from murmurtp.window import SlidingWindowConfig, SlidingWindowTransport


@dataclass(frozen=True)
class TransferCase:
    left: bytes
    right: bytes
    cuts_left: tuple
    cuts_right: tuple
    window: int
    buffer_slots: int
    read_size: int
    seed: int
    left_loss: LossConfig
    right_loss: LossConfig


@st.composite
def impairments(draw):
    # Integer grids shrink cleanly and avoid irrelevant floating-point extremes.
    return LossConfig(loss=draw(st.integers(0, 15)) / 100,
                      delay=draw(st.integers(0, 2)) / 1000,
                      jitter=draw(st.integers(0, 2)) / 1000,
                      reorder=draw(st.integers(0, 50)) / 100,
                      duplicate=draw(st.integers(0, 40)) / 100,
                      reorder_delay=draw(st.integers(1, 5)) / 1000)


@st.composite
def transfers(draw):
    left = draw(st.binary(max_size=4 * MSS + 17))
    right = draw(st.binary(max_size=4 * MSS + 17))
    cuts = lambda data: tuple(sorted(draw(st.lists(st.integers(0, len(data)), max_size=3))))
    return TransferCase(left, right, cuts(left), cuts(right),
                        draw(st.sampled_from([1, 2, 4, 16, 32])),
                        draw(st.sampled_from([1, 2, 4, 16, 64])),
                        draw(st.sampled_from([17, 127, MSS - 1, MSS, 4096, 65536])),
                        draw(st.integers(0, 2**32 - 1)),
                        draw(impairments()), draw(impairments()))


EMPTY = TransferCase(b"", b"", (0,), (), 1, 1, 17, 0, LossConfig(), LossConfig())
BOUNDARIES = TransferCase(bytes(range(256)) * 19, b"reply" * 721,
    (MSS - 1, MSS, MSS + 1), (1, MSS, 2 * MSS), 4, 1, 127, 42,
    LossConfig(loss=0.15, reorder=0.5, duplicate=0.4, delay=0.002, jitter=0.002,
               reorder_delay=0.005),
    LossConfig(loss=0.1, reorder=0.3, duplicate=0.2, reorder_delay=0.003))
WINDOW_EDGE = TransferCase(bytes(range(251)) * 158, b"other direction" * 301,
    (), (MSS + 1,), 32, 2, MSS - 1, 123456,
    LossConfig(loss=0.05, reorder=0.5, duplicate=0.4, reorder_delay=0.005),
    LossConfig(loss=0.15, delay=0.001, jitter=0.002, duplicate=0.3))


def make_endpoint(stage, *, window=16, buffer_slots=64, seed=0, inactivity=3):
    values = dict(initial_rto=0.01, max_rto=0.04,
                  receive_buffer=buffer_slots * MSS, inactivity_timeout=inactivity)
    if stage == 1:
        return StopAndWaitTransport(config=StopAndWaitConfig(**values), seed=seed)
    if stage in (4, 5):
        transport = AdaptiveRtoTransport if stage == 4 else CongestionTransport
        return transport(config=AdaptiveRtoConfig(
            window_packets=window, min_rto=0.005, clock_granularity=0.001, **values), seed=seed)
    transport = SlidingWindowTransport if stage == 2 else SelectiveRepeatTransport
    return transport(config=SlidingWindowConfig(window_packets=window, **values), seed=seed)


async def connect(server, client):
    await server.listen(("127.0.0.1", 0))
    outgoing = await asyncio.wait_for(client.connect(server.local_addr), 2)
    incoming = await asyncio.wait_for(server.accept(), 2)
    return outgoing, incoming


async def dispose(endpoints, tasks, retained_connections=()):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    connections = list(retained_connections)
    connections.extend(conn for endpoint in endpoints for conn in endpoint._peers.values())
    await asyncio.wait_for(asyncio.gather(*(endpoint.close() for endpoint in endpoints)), 3)
    for endpoint in endpoints:
        assert not endpoint._tasks and not endpoint._peers
        assert endpoint._socket_closed.done()
        assert endpoint._udp.stats()["pending"] == 0
    for conn in connections:
        assert conn.state == "CLOSED" and conn._idle.cancelled()
        assert not getattr(conn, "_pending", {})
        assert not getattr(conn, "_reorder", {})
        assert not getattr(conn, "_sacked", set())
        assert not getattr(conn, "_flights", {})
        assert conn._close_task is None or conn._close_task.done()


async def write_chunks(conn, data, cuts):
    await conn.send(b"")
    start = 0
    for end in (*cuts, len(data)):
        await conn.send(data[start:end])
        start = end
    await conn.send(b"")


async def read_to_eof(conn, expected, read_size):
    result = bytearray()
    while True:
        chunk = await conn.recv(read_size)
        if not chunk:
            break
        assert len(chunk) <= read_size
        result.extend(chunk)
        assert len(result) <= len(expected), "duplicate or unexpected payload bytes"
    assert bytes(result) == expected
    assert await conn.recv(read_size) == b""  # EOF remains stable.
    return bytes(result)


async def exercise_transfer(stage, case):
    server = make_endpoint(stage, window=case.window, buffer_slots=case.buffer_slots, seed=case.seed)
    client = make_endpoint(stage, window=case.window, buffer_slots=case.buffer_slots, seed=case.seed ^ 0xABCDEF)
    tasks = []
    try:
        outgoing, incoming = await connect(server, client)
        # Establish reliably, then vary DATA, ACK and FIN delivery in both directions.
        client._udp.config, server._udp.config = case.left_loss, case.right_loss
        tasks = [asyncio.create_task(write_chunks(outgoing, case.left, case.cuts_left)),
                 asyncio.create_task(write_chunks(incoming, case.right, case.cuts_right)),
                 asyncio.create_task(read_to_eof(incoming, case.left, case.read_size)),
                 asyncio.create_task(read_to_eof(outgoing, case.right, case.read_size))]
        await asyncio.wait_for(asyncio.gather(*tasks[:2]), 10)
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 3)
        await asyncio.wait_for(asyncio.gather(*tasks[2:]), 3)
        assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(case.left)
        assert incoming.stats()["bytes_sent"] == outgoing.stats()["bytes_received"] == len(case.right)
        for conn in (outgoing, incoming):
            assert conn.stats()["bytes_in_flight"] == conn.stats()["buffered_bytes"] == 0
    finally:
        await dispose((server, client), tasks)


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@settings(max_examples=20, derandomize=True, database=None, deadline=None)
@example(case=EMPTY)
@example(case=BOUNDARIES)
@example(case=WINDOW_EDGE)
@given(case=transfers())
def test_random_duplex_streams_are_exact_once_and_clean_up(stage, case):
    # One event loop and fresh ephemeral sockets per example, including shrinking.
    asyncio.run(exercise_transfer(stage, case))


async def interrupted_stream(action, window, seed, read_size, prefix_packets):
    server = make_endpoint(3, window=window, seed=seed)
    client = make_endpoint(3, window=window, seed=seed ^ 0xABCDEF)
    tasks = []
    data = bytes(range(251)) * ((window + 4) * MSS // 251 + 1)
    received = bytearray()
    connections = ()
    try:
        outgoing, incoming = await connect(server, client)
        connections = (outgoing, incoming)
        original = client._send
        stalled = asyncio.Event()

        def send(header, payload, addr):
            if header.flags == DATA and header.seq_num >= prefix_packets:
                stalled.set()
            else:
                original(header, payload, addr)

        client._send = send  # Deterministic permanent hole after a valid prefix.
        server._udp.config = client._udp.config = LossConfig(
            delay=0.001, jitter=0.001, reorder=0.4, reorder_delay=0.003, duplicate=0.4)

        async def read_prefix():
            try:
                while True:
                    chunk = await incoming.recv(read_size)
                    assert chunk, "interruption must not report graceful EOF"
                    received.extend(chunk)
            except OSError as error:
                return error

        tasks = [asyncio.create_task(outgoing.send(data)), asyncio.create_task(read_prefix())]
        await asyncio.wait_for(stalled.wait(), 3)

        async def prefix_arrived():
            while len(received) < prefix_packets * MSS:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(prefix_arrived(), 3)
        if action == "cancel":
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert isinstance(await asyncio.wait_for(tasks[1], 3), ConnectionResetError)
        else:
            await asyncio.gather(client.close(), server.close())
            with pytest.raises(ConnectionAbortedError):
                await tasks[0]
            assert isinstance(await asyncio.wait_for(tasks[1], 3), ConnectionAbortedError)
        assert bytes(received) == data[:len(received)]
        assert len(received) == prefix_packets * MSS < len(data)
        assert incoming.stats()["bytes_received"] == len(received)
        assert outgoing.stats()["bytes_in_flight"] == 0
    finally:
        await dispose((server, client), tasks, connections)


@pytest.mark.parametrize("action", ["cancel", "shutdown"])
@settings(max_examples=12, derandomize=True, database=None, deadline=None)
@example(window=2, seed=0, read_size=17, prefix_packets=1)
@example(window=2, seed=1, read_size=17, prefix_packets=1)
@given(window=st.sampled_from([1, 2, 4, 16, 32]), seed=st.integers(0, 2**32 - 1),
       read_size=st.sampled_from([17, 127, 4096]), prefix_packets=st.integers(0, 3))
def test_random_interruption_preserves_prefix_and_releases_resources(action, window, seed,
                                                                    read_size, prefix_packets):
    asyncio.run(interrupted_stream(action, window, seed, read_size, prefix_packets))


async def total_loss(stage, window, seed, data):
    server = make_endpoint(stage, window=window, seed=seed, inactivity=0.08)
    client = make_endpoint(stage, window=window, seed=seed + 1, inactivity=0.08)
    tasks = []
    try:
        outgoing, incoming = await connect(server, client)
        server._udp.config = client._udp.config = LossConfig(loss=1)
        tasks = [asyncio.create_task(outgoing.send(data)), asyncio.create_task(incoming.recv())]
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert all(isinstance(result, TimeoutError) for result in results)
        assert incoming.stats()["bytes_received"] == 0
        assert outgoing.stats()["bytes_in_flight"] == 0
    finally:
        await dispose((server, client), tasks)


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@settings(max_examples=8, derandomize=True, database=None, deadline=None)
@given(window=st.sampled_from([1, 4, 32]), seed=st.integers(0, 2**32 - 1),
       data=st.binary(min_size=1, max_size=2 * MSS + 1))
def test_random_total_loss_times_out_without_false_success(stage, window, seed, data):
    asyncio.run(total_loss(stage, window, seed, data))
