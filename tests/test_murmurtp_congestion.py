"""Stage 5 congestion arithmetic, admission control and loss-trace acceptance."""

import asyncio
import csv
import hashlib
import math
from types import SimpleNamespace

import pytest

from murmurtp.adaptive import AdaptiveRtoConfig
from murmurtp.congestion import CongestionConnection, CongestionTransport, RenoController
from murmurtp.header import ACK, DATA, MSS, Header
from murmurtp.lossy import LossConfig


def test_initial_window_and_slow_start_per_new_packet():
    cc = RenoController()
    assert cc.cwnd == 10 and math.isinf(cc.ssthresh)
    for _ in range(10):
        cc.acknowledged()
    assert cc.cwnd == 20


def test_slow_start_transitions_to_additive_increase_at_threshold():
    cc = RenoController()
    cc.timeout(20)  # threshold 5, window 1
    for expected in (2, 3, 4, 5):
        cc.acknowledged()
        assert cc.cwnd == expected
    cc.acknowledged()
    assert cc.cwnd == pytest.approx(5.2)
    cc.acknowledged()
    assert cc.cwnd == pytest.approx(5.2 + 1 / 5.2)


def test_fast_loss_reduces_once_until_cumulative_recovery_boundary():
    cc = RenoController()
    cc.sack_loss(20)
    assert cc.cwnd == cc.ssthresh == 5
    cc.acknowledged()
    cc.sack_loss(30)
    assert cc.cwnd == pytest.approx(5.2) and cc.recovery_end == 20
    cc.ack_progress(19)
    cc.sack_loss(30)
    assert cc.fast_reductions == 1
    cc.ack_progress(20)
    cc.sack_loss(30)
    assert cc.cwnd == cc.ssthresh == pytest.approx(2.6)
    assert cc.fast_reductions == 2 and cc.recovery_end == 30


def test_timeout_during_recovery_resets_window_and_threshold_has_floor():
    cc = RenoController()
    cc.sack_loss(10)
    cc.timeout(15)
    assert cc.cwnd == 1 and cc.ssthresh == 2.5 and cc.recovery_end == 15
    cc.timeout(15)
    assert cc.cwnd == 1 and cc.ssthresh == 2
    cc.sack_loss(20)
    assert cc.cwnd == 1 and cc.fast_reductions == 1
    assert cc.timeout_reductions == 2


class Clock:
    now = 0.0

    def time(self):
        return self.now

    def call_later(self, *args):
        return SimpleNamespace(cancel=lambda: None)


def connection(packets=10, window=32):
    clock, sent = Clock(), []
    owner = SimpleNamespace(config=AdaptiveRtoConfig(window_packets=window),
                            _loop=clock, _closed=False,
                            _send=lambda header, payload, addr: sent.append(header))
    conn = CongestionConnection(owner, ("127.0.0.1", 1), 7, client=True)
    conn.state = "ESTABLISHED"
    for seq in range(packets):
        conn._pending[seq] = b"x" * MSS
        conn._flights[seq] = conn._new_flight(seq)
    conn._tx_seq = packets
    conn._inflight = packets * MSS
    return conn, clock, sent


def ack(conn, cumulative, bitmap=0):
    conn._handle(Header(ACK, 7, 0, cumulative, 64, sack_mask=bitmap), b"")


def test_ack_growth_counts_sack_and_cumulative_once_ignores_invalid_acks():
    conn, clock, _ = connection(4)
    clock.now = .1
    ack(conn, 0, 0b101)
    assert conn._congestion.cwnd == 12
    ack(conn, 0, 0b101)
    ack(conn, 5)
    ack(conn, 1)  # already SACKed, impossible missing-head claim
    assert conn._congestion.cwnd == 12
    ack(conn, 4)
    assert conn._congestion.cwnd == 14
    ack(conn, 4)
    assert conn._congestion.cwnd == 14


def test_multiple_gaps_fast_retransmit_but_only_one_window_reduction():
    conn, clock, sent = connection(6)
    clock.now = .1
    ack(conn, 0, 0b11100)  # packets 3, 4, 5 received; holes 0, 1, 2
    conn._retry_due(clock.now)
    assert [header.seq_num for header in sent] == [0, 1, 2]
    assert conn._congestion.cwnd == 6.5
    assert conn.stats()["fast_reductions"] == 1
    assert conn.stats()["fast_retx"] == 3
    conn._retry_due(clock.now)
    assert len(sent) == 3
    clock.now = .2
    ack(conn, 6)
    assert conn._congestion.recovery_end is None
    assert conn._congestion.cwnd > 6.5
    assert conn.stats()["rtt_samples"] == 3  # Karn still excludes repaired holes.


def test_staggered_timeout_backs_off_and_reduces_once_per_round():
    conn, clock, _ = connection(3)
    conn._flights[1].deadline = 1.001
    conn._flights[2].deadline = 1.002
    for now in (1, 1.001, 1.002):
        clock.now = now
        conn._retry_due(now)
        assert conn._congestion.cwnd == 1 and conn._congestion.ssthresh == 5
        assert conn.stats()["timeout_reductions"] == 1
        assert conn.stats()["rto_ms"] == 2000
    assert not conn._can_send()
    clock.now = 3.01
    conn._retry_due(clock.now)
    assert conn._congestion.cwnd == 1 and conn._congestion.ssthresh == 2
    assert conn.stats()["timeout_reductions"] == 2
    assert conn.stats()["rto_ms"] == 4000
    ack(conn, 3)
    assert conn._can_send() and conn.stats()["rtt_samples"] == 0


def test_fractional_cwnd_counts_packets_and_keeps_sequence_horizon():
    conn, clock, _ = connection(6, window=6)
    conn._congestion.cwnd = 5.9
    assert not conn._can_send()
    clock.now = .1
    ack(conn, 0, 0b11111)
    assert len(conn._flights) == 1 and not conn._can_send()  # horizon still full
    ack(conn, 6)
    assert conn._can_send()
    conn._flights = {seq: conn._new_flight(seq) for seq in range(5)}
    conn._congestion.cwnd = 5.9
    assert not conn._can_send()
    conn._flights.pop(0)
    assert conn._can_send()


def test_stats_report_packet_window_in_bytes_and_unknown_threshold_as_null():
    conn, _, _ = connection(0)
    stats = conn.stats()
    assert stats["stage"] == 5 and stats["cwnd_bytes"] == 12000
    assert stats["ssthresh_packets"] is None and stats["rtt_ms"] is None
    conn._congestion.sack_loss(10)
    conn._congestion.acknowledged()
    assert conn.stats()["cwnd_bytes"] == 6240


async def pair(window=32):
    config = AdaptiveRtoConfig(window_packets=window, initial_rto=.5, min_rto=.01,
                               max_rto=1, clock_granularity=.001, inactivity_timeout=5)
    server, client = CongestionTransport(config=config, seed=42), CongestionTransport(config=config, seed=43)
    await server.listen(("127.0.0.1", 0))
    outgoing = await client.connect(server.local_addr)
    incoming = await server.accept()
    return server, client, outgoing, incoming


async def cleanup(server, client, tasks=()):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(server.close(), client.close())
    for endpoint in (server, client):
        assert not endpoint._tasks and not endpoint._peers
        assert endpoint._udp.stats()["pending"] == 0


@pytest.mark.parametrize("window", [4, 32])
async def test_real_sender_respects_initial_cwnd_and_configured_horizon(window):
    server, client, outgoing, incoming = await pair(window)
    tasks, held, first_sent = [], [], []
    ready = asyncio.Event()
    original, original_client = server._send, client._send
    limit = min(10, window)

    def hold(header, payload, addr):
        if header.flags == ACK and header.seq_num == 0:
            held.append((header, payload, addr))
            if header.cum_ack == limit:
                ready.set()
        else:
            original(header, payload, addr)

    def record(header, payload, addr):
        if header.flags == DATA:
            if header.seq_num not in first_sent:
                assert len(outgoing._flights) <= math.floor(outgoing._congestion.cwnd)
                assert outgoing._tx_seq - outgoing._send_base <= window
            first_sent.append(header.seq_num)
        original_client(header, payload, addr)

    try:
        server._send, client._send = hold, record
        data = b"a" * (40 * MSS)
        tasks = [asyncio.create_task(outgoing.send(data))]
        await asyncio.wait_for(ready.wait(), 2)
        assert first_sent == list(range(limit))
        assert not tasks[0].done() and outgoing._congestion.cwnd == 10
        server._send = original
        original(*held[-1])
        await asyncio.wait_for(tasks[0], 3)
        assert await incoming.recv(len(data)) == data
        # Local UDP can still lose packets or experience scheduling delays.
        # Exact growth arithmetic is checked above without a network clock.
        assert outgoing.stats()["bytes_sent"] == len(data)
    finally:
        await cleanup(server, client, tasks)


async def test_loss_response_happens_before_sender_admits_new_data():
    server, client, outgoing, incoming = await pair()
    original_server, original_client = server._send, client._send
    ready, retried = asyncio.Event(), asyncio.Event()
    held, sends, tasks = [], [], []

    def hold(header, payload, addr):
        if header.flags == ACK and header.seq_num == 0:
            held.append((header, payload, addr))
            if header.cum_ack == 10:
                ready.set()
        else:
            original_server(header, payload, addr)

    def record(header, payload, addr):
        if header.flags == DATA:
            if header.seq_num == 0 and 0 in sends:
                retried.set()
            sends.append(header.seq_num)
        original_client(header, payload, addr)

    try:
        server._send, client._send = hold, record
        tasks = [asyncio.create_task(outgoing.send(b"a" * (20 * MSS)))]
        await asyncio.wait_for(ready.wait(), 2)
        original_server(Header(ACK, outgoing.conn_id, 0, 0, 64, sack_mask=0b111),
                        b"", incoming.peer_addr)
        await asyncio.wait_for(retried.wait(), 2)
        assert max(sends) == 9  # seven flights exceed floor(13 / 2); no new packet
        assert outgoing._congestion.cwnd == 6.5
        server._send = original_server
        original_server(*held[-1])
        await asyncio.wait_for(tasks[0], 3)
        assert await incoming.recv(20 * MSS) == b"a" * (20 * MSS)
    finally:
        await cleanup(server, client, tasks)


async def test_two_percent_loss_has_cwnd_growth_and_reductions(tmp_path):
    server, client, outgoing, incoming = await pair()
    tasks, trace = [], []
    try:
        for name in ("acknowledged", "sack_loss", "timeout"):
            original = getattr(outgoing._congestion, name)

            def record(*args, _original=original, _name=name):
                before = outgoing._congestion.cwnd
                _original(*args)
                trace.append((client._loop.time(), _name, before, outgoing._congestion.cwnd))

            setattr(outgoing._congestion, name, record)
        client._udp.config = server._udp.config = LossConfig(loss=.02, delay=.001)
        data = bytes(range(256)) * 4096

        async def read():
            received = bytearray()
            while True:
                chunk = await incoming.recv(4096)
                if not chunk:
                    return bytes(received)
                received.extend(chunk)

        tasks = [asyncio.create_task(read())]
        await asyncio.wait_for(outgoing.send(data), 20)
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 5)
        received = await asyncio.wait_for(tasks[0], 2)
        assert len(received) == len(data)
        assert hashlib.sha256(received).digest() == hashlib.sha256(data).digest()
        reductions = [i for i, (_, _, before, after) in enumerate(trace) if after < before]
        assert len(reductions) >= 2
        assert any(after > before for _, _, before, after in trace[reductions[0] + 1:reductions[-1]])
        assert outgoing.stats()["fast_reductions"] >= 2
        assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(data)
        assert outgoing.stats()["bytes_in_flight"] == 0
        assert await incoming.recv() == await outgoing.recv() == b""
        with (tmp_path / "cwnd-2pct.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("monotonic_seconds", "event", "cwnd_before_packets", "cwnd_after_packets"))
            writer.writerows(trace)
    finally:
        await cleanup(server, client, tasks)
