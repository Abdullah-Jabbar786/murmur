"""Stage 4: exact clock arithmetic plus real UDP timing/recovery checks."""

import asyncio
from types import SimpleNamespace

import pytest

from murmurtp.adaptive import AdaptiveRtoConfig, AdaptiveRtoConnection, AdaptiveRtoTransport
from murmurtp.header import ACK, DATA, FIN, MSS, Header
from murmurtp.lossy import LossConfig
from murmurtp.rto import RtoEstimator
from murmurtp.window import SlidingWindowConfig


def test_estimator_known_vector_uses_previous_srtt_for_variation():
    timer = RtoEstimator()
    assert (timer.rto, timer.srtt, timer.rttvar, timer.samples) == (1, None, None, 0)
    for sample, srtt, variation, rto in [(.1, .1, .05, .3),
                                       (.14, .105, .0475, .295),
                                       (.08, .101875, .041875, .269375)]:
        assert timer.observe(sample) == pytest.approx(rto)
        assert timer.srtt == pytest.approx(srtt)
        assert timer.rttvar == pytest.approx(variation)
    assert timer.samples == 3


def test_bounds_backoff_and_clean_sample_recovery():
    timer = RtoEstimator()
    assert [timer.backoff() for _ in range(4)] == [2, 4, 5, 5]
    assert timer.observe(.01) == .2
    assert timer.observe(100) == 5
    assert timer.backoff() == 5
    fine = RtoEstimator(initial_rto=.01, min_rto=.001, granularity=.01)
    assert fine.observe(0) == .01


@pytest.mark.parametrize("field", ["initial_rto", "min_rto", "max_rto", "granularity"])
@pytest.mark.parametrize("bad", [0, -1, True, float("inf"), float("nan"), "1"])
def test_invalid_timer_configuration(field, bad):
    with pytest.raises(ValueError):
        RtoEstimator(**{field: bad})


@pytest.mark.parametrize("bad", [-1, True, float("inf"), float("nan"), "1"])
def test_invalid_sample_does_not_change_estimator(bad):
    timer = RtoEstimator()
    with pytest.raises(ValueError):
        timer.observe(bad)
    assert timer.samples == 0 and timer.srtt is None and timer.rto == 1


def test_endpoint_configuration_bounds_and_type():
    for kwargs in ({"min_rto": 2}, {"initial_rto": .1}, {"clock_granularity": 0},
                   {"max_rto": .5}, {"min_rto": True}):
        with pytest.raises(ValueError):
            AdaptiveRtoConfig(**kwargs)
    with pytest.raises(TypeError):
        AdaptiveRtoTransport(config=SlidingWindowConfig())


class Clock:
    now = 0.0

    def time(self):
        return self.now

    def call_later(self, *args):
        return SimpleNamespace(cancel=lambda: None)


def connection(packets=4):
    clock, sent = Clock(), []
    owner = SimpleNamespace(config=AdaptiveRtoConfig(), _loop=clock, _closed=False,
                            _send=lambda header, payload, addr: sent.append(header))
    conn = AdaptiveRtoConnection(owner, ("127.0.0.1", 1), 7, client=True)
    conn.state = "ESTABLISHED"
    for seq in range(packets):
        conn._pending[seq] = b"x" * MSS
        conn._flights[seq] = conn._new_flight(seq)
    conn._tx_seq = packets
    conn._inflight = packets * MSS
    return conn, clock, sent


def ack(conn, cumulative, bitmap=0):
    conn._handle(Header(ACK, 7, 0, cumulative, 64, sack_mask=bitmap), b"")


def test_sack_and_cumulative_ack_sample_each_packet_only_once():
    conn, clock, _ = connection()
    clock.now = .1
    ack(conn, 0, 0b101)  # packets 1 and 3 arrive before the head.
    assert conn.stats()["rtt_samples"] == 2
    assert conn.stats()["rtt_ms"] == pytest.approx(100)
    ack(conn, 0, 0b101)
    ack(conn, 5)  # beyond sent
    ack(conn, 1)  # impossible: the receiver already SACKed 1
    assert conn.stats()["rtt_samples"] == 2
    clock.now = .2
    ack(conn, 4)
    assert conn.stats()["rtt_samples"] == 4
    ack(conn, 4)
    assert conn.stats()["rtt_samples"] == 4
    assert not conn._flights and conn._inflight == 0


@pytest.mark.parametrize("timeout", [False, True])
def test_karn_excludes_retried_packets_but_keeps_clean_samples(timeout):
    conn, clock, _ = connection(2)
    clock.now = .05
    conn._retry(0, timeout=timeout)
    clock.now = .1
    ack(conn, 2)
    assert conn.stats()["rtt_samples"] == 1
    assert conn.stats()["rtt_ms"] == pytest.approx(100)


def test_new_packets_use_estimate_without_rewriting_inflight_deadline():
    conn, clock, _ = connection(2)
    clock.now = .1
    ack(conn, 1)
    assert conn._flights[1].deadline == 1
    new = conn._new_flight(2)
    assert new.rto == pytest.approx(.3)
    assert new.deadline == pytest.approx(.4)


def test_timeout_round_backs_off_once_for_staggered_window_then_again():
    conn, clock, sent = connection(3)
    conn._flights[1].deadline = 1.001
    conn._flights[2].deadline = 1.002
    for now in (1, 1.001, 1.002):
        clock.now = now
        conn._retry_due(now)
        assert conn.stats()["rto_ms"] == 2000
    assert [header.seq_num for header in sent] == [0, 1, 2]
    assert all(f.retransmitted and f.rto == 2 for f in conn._flights.values())
    clock.now = 3.01
    conn._retry_due(clock.now)
    assert conn.stats()["rto_ms"] == 4000
    clock.now = 7.02
    conn._retry_due(clock.now)
    assert conn.stats()["rto_ms"] == 5000
    assert conn.stats()["timeout_retx"] == 9
    ack(conn, 3)
    assert conn.stats()["rtt_samples"] == 0


def test_fast_retry_does_not_back_off_or_resample_the_missing_head():
    conn, clock, sent = connection()
    clock.now = .1
    ack(conn, 0, 0b111)
    learned = conn._timer.rto
    conn._retry_due(clock.now)
    assert [header.seq_num for header in sent] == [0]
    assert conn._timer.rto == learned
    conn._retry_due(clock.now)
    assert len(sent) == 1
    clock.now = .2
    ack(conn, 4)
    assert conn._timer.samples == 3 and conn.stats()["fast_retx"] == 1


async def pair(config):
    server, client = AdaptiveRtoTransport(config=config), AdaptiveRtoTransport(config=config)
    await server.listen(("127.0.0.1", 0))
    outgoing = await client.connect(server.local_addr)
    incoming = await server.accept()
    return server, client, outgoing, incoming


async def cleanup(server, client):
    await asyncio.gather(server.close(), client.close())
    for endpoint in (server, client):
        assert not endpoint._tasks and not endpoint._peers
        assert endpoint._udp.stats()["pending"] == 0


async def test_real_udp_lost_data_uses_karn_then_clean_data_learns():
    config = AdaptiveRtoConfig(initial_rto=.05, min_rto=.01, max_rto=.2,
                               clock_granularity=.001, inactivity_timeout=3)
    server, client, outgoing, incoming = await pair(config)
    try:
        original, dropped = client._send, []

        def send(header, payload, addr):
            if header.flags == DATA and not dropped:
                dropped.append(header.seq_num)
            else:
                original(header, payload, addr)

        client._send = send
        await asyncio.wait_for(outgoing.send(b"first"), 2)
        assert await incoming.recv() == b"first"
        assert outgoing.stats()["rtt_samples"] == 0
        assert outgoing.stats()["timeout_retx"] >= 1
        client._send = original
        await asyncio.wait_for(outgoing.send(b"second"), 2)
        assert await incoming.recv() == b"second"
        assert outgoing.stats()["rtt_samples"] == 1
        assert .01 <= outgoing._timer.rto <= .2
    finally:
        await cleanup(server, client)


async def test_real_udp_fin_retries_with_learned_timer_and_preserves_eof():
    config = AdaptiveRtoConfig(initial_rto=.1, min_rto=.01, max_rto=.2,
                               clock_granularity=.001, inactivity_timeout=3)
    server, client, outgoing, incoming = await pair(config)
    try:
        await outgoing.send(b"complete")
        assert await incoming.recv() == b"complete"
        samples, learned = outgoing._timer.samples, outgoing._timer.rto
        original, fin_times = client._send, []

        def send(header, payload, addr):
            if header.flags == FIN:
                fin_times.append(client._loop.time())
                if len(fin_times) == 1:
                    return
            original(header, payload, addr)

        client._send = send
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 2)
        assert len(fin_times) >= 2
        assert fin_times[1] - fin_times[0] >= learned * .9
        assert outgoing._timer.samples == samples
        assert outgoing._timer.rto >= min(2 * learned, config.max_rto)
        assert await incoming.recv() == await outgoing.recv() == b""
    finally:
        await cleanup(server, client)


async def test_real_udp_estimator_tracks_increased_path_delay():
    server, client, outgoing, incoming = await pair(AdaptiveRtoConfig(inactivity_timeout=5))
    try:
        for _ in range(4):
            await outgoing.send(b"low delay")
            assert await incoming.recv() == b"low delay"
        before = outgoing.stats()
        server._udp.config = client._udp.config = LossConfig(delay=.06)
        for _ in range(8):
            await asyncio.wait_for(outgoing.send(b"higher delay"), 3)
            assert await incoming.recv() == b"higher delay"
        after = outgoing.stats()
        assert after["rtt_samples"] > before["rtt_samples"]
        assert after["rtt_ms"] > before["rtt_ms"]
        assert after["rto_ms"] > before["rto_ms"]
        assert 200 <= after["rto_ms"] <= 5000
        assert after["stage"] == 4 and after["cwnd_bytes"] is None
    finally:
        await cleanup(server, client)
