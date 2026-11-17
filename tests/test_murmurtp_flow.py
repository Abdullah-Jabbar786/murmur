"""Stage 6: bounded slow readers, lost window updates and persist lifecycle."""

import asyncio
from types import SimpleNamespace

import pytest

from murmurtp.adaptive import AdaptiveRtoConfig
from murmurtp.flow import FlowControlConfig, FlowControlConnection, FlowControlTransport
from murmurtp.header import ACK, DATA, MSS, SYN, Header


class Clock:
    now = 0.0

    def time(self):
        return self.now

    def call_later(self, *args):
        return SimpleNamespace(cancel=lambda: None)


def connection():
    sent, clock = [], Clock()
    owner = SimpleNamespace(config=FlowControlConfig(receive_buffer=3 * MSS + 17),
                            _loop=clock, _closed=False,
                            _send=lambda h, p, a: sent.append((h, p)))
    conn = FlowControlConnection(owner, ("127.0.0.1", 1), 7, client=True)
    conn.state = "ESTABLISHED"
    return conn, clock, sent


def ack(conn, cumulative, window, bitmap=0):
    conn._handle(Header(ACK, 7, 0, cumulative, window, sack_mask=bitmap), b"")


def test_window_limits_are_in_packet_slots_and_count_reorder_bytes():
    conn, _, sent = connection()
    assert conn.stats()["receive_window_packets"] == 3
    conn._rx.extend(b"a" * (MSS + 18))
    conn._reorder[3] = b"b" * MSS
    conn._reorder_bytes = MSS
    conn._send_packet(ACK)
    assert sent[-1][0].window == 0  # 1199 bytes free cannot admit a full packet.
    conn.config = FlowControlConfig(receive_buffer=70000 * MSS)
    assert conn._receive_slots() == 65535


def test_admission_obeys_peer_window_congestion_and_sequence_horizon():
    conn, _, _ = connection()
    ack(conn, 0, 1)
    assert conn._can_send()
    conn._flights[0] = conn._new_flight(0)
    conn._pending[0] = b"x"
    conn._tx_seq, conn._inflight = 1, 1
    assert not conn._can_send()
    ack(conn, 1, 0)
    assert not conn._can_send() and not conn._flights
    ack(conn, 1, 2)
    assert conn._can_send()
    conn._congestion.cwnd = 1
    conn._flights[1] = conn._new_flight(1)
    assert not conn._can_send()
    conn._flights.clear()
    conn._tx_seq = conn._send_base + conn.config.window_packets
    assert not conn._can_send()


def test_stale_future_and_late_handshake_cannot_change_window():
    conn, _, _ = connection()
    conn._tx_seq = 4
    ack(conn, 3, 2)
    ack(conn, 2, 100)
    ack(conn, 5, 100)
    conn._handle(Header(SYN | ACK, 7, 0, 1, 100), b"")
    assert conn.stats()["peer_window_packets"] == 2
    # Same cumulative ACK may legitimately reopen after an application read.
    ack(conn, 3, 0)
    ack(conn, 3, 1)
    assert conn.stats()["peer_window_packets"] == 1
    assert conn.stats()["rtt_samples"] == 0 and conn.stats()["cwnd_packets"] == 10


def test_zero_window_pauses_retries_without_congestion_penalty():
    conn, clock, sent = connection()
    conn._update_window(2, 0)
    conn._pending[0], conn._flights[0] = b"x", conn._new_flight(0)
    conn._tx_seq, conn._inflight = 1, 1
    ack(conn, 0, 0)
    clock.now = 10
    conn._retry_due(clock.now)
    assert not sent and conn.stats()["retx"] == 0
    assert conn.stats()["timeout_reductions"] == 0 and conn._timer.rto == 1
    ack(conn, 0, 1)
    assert conn._flights[0].deadline == 11
    conn._retry_due(clock.now)
    assert not sent
    clock.now = 11
    conn._retry_due(clock.now)
    assert len(sent) == 1 and conn.stats()["timeout_reductions"] == 1


async def test_probes_use_fixed_interval_and_do_not_change_data_state():
    conn, clock, sent = connection()
    waits = []

    async def wait(timeout):
        waits.append(timeout)

    conn._wait_for_ack = wait
    await conn._wait_for_send_progress()
    for now in (1, 2, 3, 4):
        clock.now = now
        await conn._wait_for_send_progress()
    assert waits == [1, 1, 1, 1, 1]
    assert len(sent) == 4 and all(h.flags == DATA and h.seq_num == 0 and not p for h, p in sent)
    assert conn._tx_seq == 0 and not conn._pending and not conn._flights
    assert conn.stats()["bytes_sent"] == conn.stats()["retx"] == conn.stats()["rtt_samples"] == 0
    assert conn.stats()["window_probes"] == 4 and conn.stats()["cwnd_bytes"] == 12000


@pytest.mark.parametrize("bad", [0, -1, True, float("inf"), float("nan"), "1"])
def test_invalid_probe_interval(bad):
    with pytest.raises(ValueError):
        FlowControlConfig(probe_interval=bad)


def test_default_probe_interval_and_endpoint_configuration():
    assert FlowControlConfig().probe_interval == 1
    with pytest.raises(TypeError):
        FlowControlTransport(config=AdaptiveRtoConfig())


async def test_read_updates_only_when_a_full_slot_reopens():
    conn, _, sent = connection()
    conn._rx.extend(b"x" * (3 * MSS + 17))
    conn._advertised_window = 0
    assert len(await conn.recv(MSS - 1)) == MSS - 1
    assert not sent
    assert await conn.recv(1) == b"x"
    assert len(sent) == 1 and sent[0][0].window == 1
    assert await conn.recv(0) == b""
    assert len(sent) == 1


def test_empty_data_probe_only_returns_current_ack():
    conn, _, sent = connection()
    conn._rx_seq = 5
    conn._handle(Header(DATA, 7, 999, 0, 64), b"")
    assert conn._rx_seq == 5 and not conn._rx and not conn._reorder
    assert len(sent) == 1 and sent[0][0].flags == ACK and sent[0][0].cum_ack == 5
    assert conn.stats()["bytes_received"] == 0


def test_window_only_update_does_not_wake_fin_ack_waiter():
    conn, _, _ = connection()
    conn._waiting_ack = 1
    conn._ack_ready.clear()
    ack(conn, 0, 2)
    assert not conn._ack_ready.is_set() and not conn._acked
    ack(conn, 1, 2)
    assert conn._ack_ready.is_set() and conn._acked


def test_late_window_update_does_not_restart_time_wait_idle_timer():
    conn, _, _ = connection()
    conn.state = "TIME_WAIT"
    conn._touch = lambda: pytest.fail("TIME_WAIT must not restart the inactivity timer")
    ack(conn, 0, 2)
    assert conn._peer_window == 2


async def pair(server_slots=1, client_slots=4, inactivity=3):
    values = dict(initial_rto=.03, min_rto=.01, max_rto=.08,
                  clock_granularity=.001, inactivity_timeout=inactivity, probe_interval=.02)
    server = FlowControlTransport(config=FlowControlConfig(receive_buffer=server_slots * MSS, **values))
    client = FlowControlTransport(config=FlowControlConfig(receive_buffer=client_slots * MSS, **values))
    await server.listen(("127.0.0.1", 0))
    outgoing = await client.connect(server.local_addr)
    incoming = await server.accept()
    return server, client, outgoing, incoming


async def eventually(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), 2)


async def cleanup(server, client, tasks=()):
    connections = list(server._peers.values()) + list(client._peers.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(server.close(), client.close())
    for endpoint in (server, client):
        assert not endpoint._tasks and not endpoint._peers
        assert endpoint._udp.stats()["pending"] == 0
    for conn in connections:
        assert not conn._flights and not conn._pending
        assert conn._persist_deadline is None and conn._idle.cancelled()


async def test_asymmetric_handshake_and_slow_reader_keep_buffers_bounded():
    server, client, outgoing, incoming = await pair(server_slots=2)
    tasks = []
    try:
        assert outgoing.stats()["peer_window_packets"] == 2
        assert incoming.stats()["peer_window_packets"] == 4
        data = bytes(range(251)) * 61
        tasks = [asyncio.create_task(outgoing.send(data))]
        await eventually(lambda: outgoing._peer_window == 0)
        assert outgoing._tx_seq == 2 and len(incoming._rx) == 2 * MSS
        assert not tasks[0].done()
        received = bytearray()
        while len(received) < len(data):
            received.extend(await asyncio.wait_for(incoming.recv(127), 2))
            assert incoming.stats()["buffered_bytes"] <= 2 * MSS
            await asyncio.sleep(.001)
        await asyncio.wait_for(tasks[0], 2)
        assert bytes(received) == data
        assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(data)
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 2)
        assert await incoming.recv() == await outgoing.recv() == b""
    finally:
        await cleanup(server, client, tasks)


async def test_lost_reopen_update_and_probe_reply_eventually_resume():
    server, client, outgoing, incoming = await pair()
    tasks, probes, dropped = [], [], []
    drained = False
    original_server, original_client = server._send, client._send

    def hold_update(header, payload, addr):
        if drained and header.flags == ACK and header.window > 0 and len(probes) < 2:
            dropped.append(header)
            return
        original_server(header, payload, addr)

    def record_probe(header, payload, addr):
        if drained and header.flags == DATA and not payload:
            probes.append(header.seq_num)
        original_client(header, payload, addr)

    try:
        data = b"a" * MSS + b"b" * MSS
        tasks = [asyncio.create_task(outgoing.send(data))]
        await eventually(lambda: outgoing._peer_window == 0)
        server._send, client._send = hold_update, record_probe
        drained = True
        assert await incoming.recv() == data[:MSS]
        await asyncio.wait_for(tasks[0], 2)
        assert await incoming.recv() == data[MSS:]
        assert len(dropped) >= 2 and len(probes) >= 2
        assert all(seq == 1 for seq in probes)
        assert outgoing._tx_seq == 2 and outgoing.stats()["bytes_sent"] == 2 * MSS
        assert outgoing.stats()["retx"] == 0 and outgoing.stats()["timeout_reductions"] == 0
    finally:
        await cleanup(server, client, tasks)


@pytest.mark.parametrize("action", ["cancel", "shutdown", "timeout"])
async def test_zero_window_wait_terminates_and_releases_resources(action):
    server, client, outgoing, incoming = await pair(inactivity=.25 if action == "timeout" else 3)
    tasks = []
    try:
        await outgoing.send(b"x" * MSS)
        await eventually(lambda: outgoing._peer_window == 0)
        tasks = [asyncio.create_task(outgoing.send(b"never delivered"))]
        await eventually(lambda: outgoing.stats()["window_probes"] >= 1)
        if action == "cancel":
            tasks[0].cancel()
            error = asyncio.CancelledError
        elif action == "shutdown":
            await asyncio.gather(server.close(), client.close())
            error = ConnectionAbortedError
        else:
            # Suppress the peer's idle RST so this side's own inactivity timer
            # is the deterministic failure under test.
            incoming._idle.cancel()
            error = TimeoutError
        with pytest.raises(error):
            await asyncio.wait_for(tasks[0], 2)
        assert outgoing._tx_seq == 1 and outgoing.stats()["bytes_sent"] == MSS
        assert outgoing.stats()["timeout_reductions"] == 0
        assert len(incoming._rx) <= MSS
    finally:
        await cleanup(server, client, tasks)


async def test_fin_is_not_blocked_by_full_receive_window():
    server, client, outgoing, incoming = await pair()
    try:
        await outgoing.send(b"x" * MSS)
        assert outgoing._peer_window == 0
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 2)
        assert await incoming.recv() == b"x" * MSS
        assert await incoming.recv() == b""
    finally:
        await cleanup(server, client)
