"""Stage 3 real-UDP selective recovery and 64 MiB impairment acceptance."""

import asyncio
from collections import Counter
import hashlib

import pytest

from murmurtp.header import ACK, DATA, FIN, MSS, SYN, Header, encode_packet
from murmurtp.lossy import LossConfig
from murmurtp.sack import SelectiveRepeatTransport
from murmurtp.window import SlidingWindowConfig


FAST = SlidingWindowConfig(window_packets=16, initial_rto=0.02, max_rto=0.1,
                           inactivity_timeout=5)


@pytest.fixture
async def endpoints():
    created = []

    async def make(**kwargs):
        endpoint = SelectiveRepeatTransport(config=kwargs.pop("config", FAST), **kwargs)
        created.append(endpoint)
        await endpoint.listen(("127.0.0.1", 0))
        return endpoint

    yield make
    await asyncio.wait_for(asyncio.gather(*(endpoint.close() for endpoint in created)), 3)
    assert all(not endpoint._tasks and not endpoint._peers for endpoint in created)
    assert all(endpoint._udp.stats()["pending"] == 0 for endpoint in created)


async def pair(server, client):
    outgoing = await asyncio.wait_for(client.connect(server.local_addr), 2)
    return outgoing, await asyncio.wait_for(server.accept(), 2)


async def read_exact(conn, size):
    result = bytearray()
    while len(result) < size:
        chunk = await conn.recv(min(7919, size - len(result)))
        assert chunk, "premature EOF"
        result.extend(chunk)
    return bytes(result)


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 3)


async def cancel_tasks(*tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_bitmap_tracks_gaps_and_rebases_after_cumulative_progress(endpoints, monkeypatch):
    config = SlidingWindowConfig(window_packets=4, initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, held, acks = client._send, [], []
    server_send = server._send

    def send(header, payload, addr):
        if header.flags == DATA:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    def ack(header, payload, addr):
        if header.flags == ACK and header.seq_num == 0:
            acks.append((header.cum_ack, header.sack_mask))
        server_send(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    monkeypatch.setattr(server, "_send", ack)
    data = b"a" * MSS + b"b" * MSS + b"c" * MSS + b"tail"
    sending = asyncio.create_task(outgoing.send(data))
    reading = asyncio.create_task(read_exact(incoming, len(data)))
    try:
        await eventually(lambda: len(held) == 4)
        head_deadline = outgoing._flights[0].deadline
        original(*held[1])
        original(*held[3])
        await eventually(lambda: outgoing.stats()["sacked_packets"] == 2)
        assert acks[-1] == (0, 0b101)  # Packet 1 is bit 0; packet 3 is bit 2.
        assert not reading.done()
        assert outgoing.stats()["bytes_in_flight"] == 2 * MSS
        assert outgoing._flights[0].deadline == head_deadline
        original(*held[3])  # Duplicate report must not subtract bytes twice.
        original(*held[0])
        await eventually(lambda: outgoing._send_base == 2)
        assert acks[-1] == (2, 0b1)
        assert outgoing.stats()["bytes_in_flight"] == MSS
        original(*held[2])
        await asyncio.wait_for(sending, 2)
        assert await asyncio.wait_for(reading, 2) == data
        assert acks[-1] == (4, 0)
        assert not outgoing._pending and not outgoing._sacked and not outgoing._flights
        assert outgoing.stats()["retx"] == 0
    finally:
        await cancel_tasks(sending, reading)


@pytest.mark.parametrize("holes", [(1,), (1, 4)])
async def test_three_later_sacks_retransmit_only_missing_packets_before_rto(
        endpoints, monkeypatch, holes):
    config = SlidingWindowConfig(initial_rto=2, max_rto=2)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, counts = client._send, Counter()

    def send(header, payload, addr):
        if header.flags == DATA:
            counts[header.seq_num] += 1
            if header.seq_num in holes and counts[header.seq_num] == 1:
                return
        original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"".join(bytes([i]) * MSS for i in range(8))
    _, received = await asyncio.wait_for(asyncio.gather(
        outgoing.send(data), read_exact(incoming, len(data))), 1)
    assert received == data
    assert counts == Counter({i: 2 if i in holes else 1 for i in range(8)})
    assert outgoing.stats()["fast_retx"] == len(holes)
    assert outgoing.stats()["timeout_retx"] == 0


async def test_fewer_than_three_later_sacks_use_selective_timeout(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original, counts = client._send, Counter()

    def send(header, payload, addr):
        if header.flags == DATA:
            counts[header.seq_num] += 1
            if header.seq_num == 1 and counts[1] == 1:
                return
        original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"a" * MSS + b"b" * MSS + b"tail"
    _, received = await asyncio.wait_for(asyncio.gather(
        outgoing.send(data), read_exact(incoming, len(data))), 3)
    assert received == data and counts == Counter({0: 1, 1: 2, 2: 1})
    assert outgoing.stats()["fast_retx"] == 0
    assert outgoing.stats()["timeout_retx"] == 1


async def test_lost_fast_retry_recovers_by_timeout_without_duplicate_ack_storm(
        endpoints, monkeypatch):
    config = SlidingWindowConfig(initial_rto=0.2, max_rto=0.4)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, counts = client._send, Counter()

    def send(header, payload, addr):
        if header.flags == DATA:
            counts[header.seq_num] += 1
            if header.seq_num == 0 and counts[0] <= 2:
                return  # Lose the original AND its SACK-gap retransmission.
        original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"x" * (4 * MSS)
    sending = asyncio.create_task(outgoing.send(data))
    reading = asyncio.create_task(read_exact(incoming, len(data)))
    try:
        await eventually(lambda: counts[0] == 2)
        repeated = encode_packet(Header(ACK, outgoing.conn_id, cum_ack=0, sack_mask=0b111))
        for _ in range(50):
            client.datagram_received(repeated, server.local_addr)
        await asyncio.sleep(0)
        assert counts[0] == 2
        await asyncio.wait_for(sending, 2)
        assert await asyncio.wait_for(reading, 2) == data
        assert counts == Counter({0: 3, 1: 1, 2: 1, 3: 1})
        assert outgoing.stats()["fast_retx"] == outgoing.stats()["timeout_retx"] == 1
    finally:
        await cancel_tasks(sending, reading)


async def test_sacked_packets_do_not_open_window_past_missing_head(endpoints, monkeypatch):
    config = SlidingWindowConfig(window_packets=4, initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, held = client._send, []
    hold = True

    def send(header, payload, addr):
        if hold and header.flags == DATA and header.seq_num == 0:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"x" * (12 * MSS)
    sending = asyncio.create_task(outgoing.send(data))
    reading = asyncio.create_task(read_exact(incoming, len(data)))
    try:
        await eventually(lambda: len(held) == 2)
        assert outgoing._tx_seq == 4 and outgoing._send_base == 0
        assert outgoing.stats()["sacked_packets"] == 3
        assert outgoing.stats()["unacked_packets"] == 1
        assert outgoing.stats()["pending_packets"] == 4
        hold = False
        original(*held[0])
        await asyncio.wait_for(sending, 3)
        assert await asyncio.wait_for(reading, 3) == data
    finally:
        await cancel_tasks(sending, reading)


async def test_invalid_stale_and_handshake_acks_do_not_change_scoreboard(endpoints, monkeypatch):
    config = SlidingWindowConfig(initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, held = client._send, []

    def send(header, payload, addr):
        if header.flags == DATA:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    sending = asyncio.create_task(outgoing.send(b"x" * (3 * MSS)))
    try:
        await eventually(lambda: len(held) == 3)
        for header in (Header(ACK, outgoing.conn_id, cum_ack=4, sack_mask=0xFFFFFFFF),
                       Header(ACK, outgoing.conn_id, cum_ack=0, sack_mask=1 << 31),
                       Header(ACK, outgoing.conn_id, seq_num=1, cum_ack=1),
                       Header(SYN | ACK, outgoing.conn_id, cum_ack=1)):
            client.datagram_received(encode_packet(header), server.local_addr)
        assert outgoing._send_base == 0 and not outgoing._sacked
        original(*held[0])
        original(*held[2])
        await eventually(lambda: outgoing._sacked == {2})
        for header in (Header(ACK, outgoing.conn_id, cum_ack=0, sack_mask=0xFFFFFFFF),
                       Header(ACK, outgoing.conn_id, cum_ack=2)):
            client.datagram_received(encode_packet(header), server.local_addr)
        assert outgoing._send_base == 1 and outgoing._sacked == {2}
        assert outgoing.stats()["bytes_in_flight"] == MSS
        original(*held[1])
        await asyncio.wait_for(sending, 2)
        assert await read_exact(incoming, 3 * MSS) == b"x" * (3 * MSS)
    finally:
        await cancel_tasks(sending)


async def test_small_buffer_never_sacks_a_discarded_packet(endpoints, monkeypatch):
    config = SlidingWindowConfig(window_packets=4, receive_buffer=2 * MSS,
                                 initial_rto=0.02, max_rto=0.1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, held = client._send, []
    hold = True

    def send(header, payload, addr):
        if hold and header.flags == DATA and header.seq_num == 0:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"a" * MSS + b"b" * MSS + b"c" * MSS + b"tail"
    sending = asyncio.create_task(outgoing.send(data))
    try:
        await eventually(lambda: outgoing._sacked == {1})
        assert incoming.stats()["buffered_bytes"] == MSS
        assert outgoing.stats()["bytes_in_flight"] == 2 * MSS + 4
        hold = False
        original(*held[0])
        assert await asyncio.wait_for(read_exact(incoming, len(data)), 3) == data
        await asyncio.wait_for(sending, 3)
    finally:
        await cancel_tasks(sending)


@pytest.mark.parametrize("lost", ["data_ack", "fin", "fin_ack"])
async def test_ack_and_fin_loss_preserve_buffered_stream_and_eof(endpoints, monkeypatch, lost):
    server, client = await endpoints(), await endpoints()
    source = client if lost == "fin" else server
    original, dropped = source._send, []

    def send(header, payload, addr):
        target = ((lost == "fin" and header.flags == FIN)
                  or (header.flags == ACK and header.seq_num == 0
                      and header.cum_ack == (3 if lost == "data_ack" else 4)))
        if target and not dropped:
            dropped.append(header)
        else:
            original(header, payload, addr)

    monkeypatch.setattr(source, "_send", send)
    outgoing, incoming = await pair(server, client)
    data = b"x" * (2 * MSS) + b"tail"
    await asyncio.wait_for(outgoing.send(data), 3)
    await asyncio.wait_for(outgoing.close(), 3)
    assert await read_exact(incoming, len(data)) == data
    assert await incoming.recv() == b""
    await asyncio.wait_for(incoming.close(), 3)
    assert dropped and outgoing.state == incoming.state == "CLOSED"


@pytest.mark.parametrize("shutdown", ["cancel", "endpoint"])
async def test_interrupted_pipeline_clears_scoreboard_and_timers(endpoints, monkeypatch, shutdown):
    config = SlidingWindowConfig(initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original = client._send
    monkeypatch.setattr(client, "_send", lambda h, p, a:
        None if h.flags == DATA and h.seq_num == 0 else original(h, p, a))
    sending = asyncio.create_task(outgoing.send(b"x" * (4 * MSS)))
    reading = asyncio.create_task(incoming.recv())
    try:
        await eventually(lambda: len(outgoing._sacked) == 3)
        if shutdown == "cancel":
            sending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sending
            with pytest.raises(ConnectionResetError):
                await asyncio.wait_for(reading, 2)
        else:
            await asyncio.gather(client.close(), server.close())
            with pytest.raises(ConnectionAbortedError):
                await sending
            with pytest.raises(ConnectionAbortedError):
                await reading
        assert not outgoing._pending and not outgoing._sacked and not outgoing._flights
        assert not incoming._reorder and outgoing.stats()["bytes_in_flight"] == 0
    finally:
        await cancel_tasks(sending, reading)


async def test_duplex_and_repeated_sends_with_seeded_impairments(endpoints):
    loss = LossConfig(loss=0.05, reorder=0.2, duplicate=0.2, reorder_delay=0.005)
    server, client = await endpoints(loss=loss, seed=17), await endpoints(loss=loss, seed=29)
    outgoing, incoming = await pair(server, client)
    first, second = bytes(range(251)) * 1000, b"return" * 10000
    results = await asyncio.wait_for(asyncio.gather(outgoing.send(first), incoming.send(second),
        read_exact(incoming, len(first)), read_exact(outgoing, len(second))), 10)
    assert results[2:] == [first, second]
    await outgoing.send(b"")
    await outgoing.send(b"next")
    assert await incoming.recv() == b"next"
    assert outgoing.stats()["bytes_sent"] == len(first) + 4
    await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 3)


async def test_sixty_four_mib_loss_reorder_duplicate_acceptance(endpoints, record_property):
    config = SlidingWindowConfig(window_packets=32, initial_rto=0.02, max_rto=0.1,
                                 inactivity_timeout=10)
    loss = LossConfig(loss=0.05, reorder=0.05, duplicate=0.01, reorder_delay=0.005)
    server = await endpoints(config=config, loss=loss, seed=11)
    client = await endpoints(config=config, loss=loss, seed=29)
    outgoing, incoming = await pair(server, client)
    data = bytes(range(256)) * (64 * 1024 * 1024 // 256)
    started = asyncio.get_running_loop().time()
    _, received = await asyncio.wait_for(asyncio.gather(outgoing.send(data),
        read_exact(incoming, len(data))), 120)
    record_property("stage3_64mib_seconds", round(asyncio.get_running_loop().time() - started, 3))
    assert len(received) == 64 * 1024 * 1024
    assert hashlib.sha256(received).digest() == hashlib.sha256(data).digest()
    assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(data)
    assert outgoing.stats()["fast_retx"] > 0 and outgoing.stats()["timeout_retx"] > 0
    assert outgoing.stats()["retx"] >= outgoing.stats()["fast_retx"] + outgoing.stats()["timeout_retx"]
    assert not outgoing._pending and not outgoing._sacked and not outgoing._flights
    for endpoint in (server, client):
        assert all(endpoint._udp.stats()[key] > 0 for key in ("dropped", "reordered", "duplicated"))
    await asyncio.wait_for(outgoing.close(), 3)
    assert await incoming.recv() == b""
