"""A2.2 first step: cumulative-ACK windows over real localhost UDP."""

import asyncio
from collections import Counter
import hashlib

import pytest

from murmurtp.connection import StopAndWaitConfig
from murmurtp.header import ACK, DATA, FIN, MSS, SYN, Header, encode_packet
from murmurtp.lossy import LossConfig
from murmurtp.transport import StopAndWaitTransport
from murmurtp.window import SlidingWindowConfig, SlidingWindowTransport


FAST = SlidingWindowConfig(initial_rto=0.01, max_rto=0.05, inactivity_timeout=5)


@pytest.fixture
async def endpoints():
    created = []

    async def make(**kwargs):
        endpoint = SlidingWindowTransport(config=kwargs.pop("config", FAST), **kwargs)
        created.append(endpoint)
        await endpoint.listen(("127.0.0.1", 0))
        return endpoint

    yield make
    await asyncio.wait_for(asyncio.gather(*(endpoint.close() for endpoint in created)), 3)
    assert all(not endpoint._tasks and not endpoint._peers for endpoint in created)
    assert all(endpoint._udp.stats()["pending"] == 0 for endpoint in created)


async def pair(server, client):
    outgoing = await asyncio.wait_for(client.connect(server.local_addr), 2)
    incoming = await asyncio.wait_for(server.accept(), 2)
    return outgoing, incoming


async def read_exact(conn, size):
    data = bytearray()
    while len(data) < size:
        chunk = await conn.recv(min(7919, size - len(data)))
        assert chunk, "premature EOF"
        data.extend(chunk)
    return bytes(data)


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 2)


async def cancel_tasks(*tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("window", [1, 4, 32])
async def test_window_bound_and_partial_cumulative_ack_slide_without_waiting_for_batch(
        endpoints, monkeypatch, window):
    config = SlidingWindowConfig(window_packets=window, initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, blocked = server._send, True

    def send(header, payload, addr):
        if not (blocked and header.flags == ACK and header.seq_num == 0):
            original(header, payload, addr)

    monkeypatch.setattr(server, "_send", send)
    data = bytes(range(251)) * (3 * window * MSS // 251 + 1)
    sending = asyncio.create_task(outgoing.send(data))
    reading = asyncio.create_task(read_exact(incoming, len(data)))
    try:
        await eventually(lambda: incoming._rx_seq == window)
        assert outgoing.stats()["pending_packets"] == window
        assert outgoing.stats()["bytes_in_flight"] == window * MSS
        assert outgoing._tx_seq == window and not sending.done()
        # ACK a prefix while withholding the rest: refill just the freed slots.
        prefix = max(1, window // 2)
        original(Header(ACK, outgoing.conn_id, cum_ack=prefix), b"", client.local_addr)
        await eventually(lambda: incoming._rx_seq == window + prefix)
        assert len(outgoing._pending) == window
        assert next(iter(outgoing._pending)) == prefix
        blocked = False
        incoming._send_packet(ACK)
        await asyncio.wait_for(sending, 3)
        assert await asyncio.wait_for(reading, 3) == data
        assert outgoing.stats()["bytes_in_flight"] == 0
        assert outgoing.stats()["retx"] == 0
    finally:
        await cancel_tasks(sending, reading)


async def test_reverse_order_and_duplicates_wait_for_missing_head(endpoints, monkeypatch):
    config = SlidingWindowConfig(window_packets=4, initial_rto=1, max_rto=1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, held = client._send, []

    def send(header, payload, addr):
        if header.flags == DATA:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"a" * MSS + b"b" * MSS + b"c" * MSS + b"tail"
    sending = asyncio.create_task(outgoing.send(data))
    reading = asyncio.create_task(read_exact(incoming, len(data)))
    try:
        await eventually(lambda: len(held) == 4)
        for packet in reversed(held[1:]):
            original(*packet)
            original(*packet)
        await eventually(lambda: incoming.stats()["out_of_order_packets"] == 3)
        assert incoming._rx_seq == 0 and not reading.done()
        assert incoming.stats()["out_of_order_bytes"] == 2 * MSS + 4
        original(*held[0])
        await asyncio.wait_for(sending, 2)
        assert await asyncio.wait_for(reading, 2) == data
        assert incoming.stats()["out_of_order_packets"] == 0
        assert outgoing.stats()["retx"] == 0
    finally:
        await cancel_tasks(sending, reading)


async def test_missing_middle_packet_recovers_with_cumulative_retransmission(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original, counts, acks = client._send, Counter(), []
    server_send = server._send

    def send(header, payload, addr):
        if header.flags == DATA:
            counts[header.seq_num] += 1
            if header.seq_num == 1 and counts[1] == 1:
                return
        original(header, payload, addr)

    def ack(header, payload, addr):
        if header.flags == ACK and header.seq_num == 0:
            acks.append(header)
        server_send(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    monkeypatch.setattr(server, "_send", ack)
    data = b"a" * MSS + b"b" * MSS + b"tail"
    _, received = await asyncio.wait_for(asyncio.gather(
        outgoing.send(data), read_exact(incoming, len(data))), 3)
    assert received == data
    assert counts[0] == 1 and counts[1] >= 2 and counts[2] >= 2
    assert outgoing.stats()["retx"] == sum(counts.values()) - 3
    assert 1 in [h.cum_ack for h in acks] and acks[-1].cum_ack == 3
    assert all(h.sack_mask == 0 for h in acks)  # Selective ACK is the next step.


async def test_small_receive_buffer_reserves_space_for_gap_and_resumes(endpoints, monkeypatch):
    config = SlidingWindowConfig(window_packets=4, receive_buffer=2 * MSS,
                                 initial_rto=0.01, max_rto=0.05)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    original, head = client._send, []
    block_head = True

    def send(header, payload, addr):
        if block_head and header.flags == DATA and header.seq_num == 0:
            head.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    data = b"a" * MSS + b"b" * MSS + b"c" * MSS + b"tail"
    sending = asyncio.create_task(outgoing.send(data))
    try:
        await eventually(lambda: incoming.stats()["out_of_order_bytes"] == MSS)
        assert incoming.stats()["buffered_bytes"] == MSS
        assert not incoming._rx and not sending.done()
        block_head = False
        original(*head[0])
        await eventually(lambda: incoming._rx_seq == 2)
        assert incoming.stats()["buffered_bytes"] == 2 * MSS
        assert not sending.done()
        assert await asyncio.wait_for(read_exact(incoming, len(data)), 3) == data
        await asyncio.wait_for(sending, 3)
        assert incoming.stats()["buffered_bytes"] == 0
    finally:
        await cancel_tasks(sending)


async def test_bad_future_data_and_ack_do_not_advance_stream(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original, held = client._send, []

    def send(header, payload, addr):
        if header.flags == DATA:
            held.append((header, payload, addr))
        else:
            original(header, payload, addr)

    monkeypatch.setattr(client, "_send", send)
    sending = asyncio.create_task(outgoing.send(b"valid"))
    try:
        await eventually(lambda: bool(held))
        for header in (Header(ACK, outgoing.conn_id, cum_ack=2),
                       Header(ACK, outgoing.conn_id, cum_ack=0, sack_mask=0xFFFFFFFF),
                       Header(ACK, outgoing.conn_id, seq_num=1, cum_ack=1),
                       Header(SYN | ACK, outgoing.conn_id, cum_ack=1)):
            client.datagram_received(encode_packet(header), server.local_addr)
        wrong = encode_packet(Header(DATA, incoming.conn_id), b"bad")
        server.datagram_received(wrong[:-1] + bytes([wrong[-1] ^ 1]), client.local_addr)
        server.datagram_received(encode_packet(Header(DATA, incoming.conn_id,
            seq_num=FAST.window_packets), b"too far"), client.local_addr)
        server.datagram_received(encode_packet(Header(DATA, incoming.conn_id + 1), b"wrong"), client.local_addr)
        assert not sending.done() and len(outgoing._pending) == 1
        assert incoming.stats()["buffered_bytes"] == 0 and server.invalid_packets == 1
        original(*held[0])
        await asyncio.wait_for(sending, 2)
        assert await incoming.recv() == b"valid"
    finally:
        await cancel_tasks(sending)


@pytest.mark.parametrize("lost", ["final_ack", "fin", "fin_ack"])
async def test_handshake_and_fin_recovery_after_multiple_data_packets(endpoints, monkeypatch, lost):
    server, client = await endpoints(), await endpoints()
    source = server if lost == "fin_ack" else client
    original, dropped = source._send, []

    def send(header, payload, addr):
        target = ((lost == "final_ack" and header.flags == ACK and header.seq_num == 1)
                  or (lost == "fin" and header.flags == FIN)
                  or (lost == "fin_ack" and header.flags == ACK
                      and header.seq_num == 0 and header.cum_ack == 4))
        if target and not dropped:
            dropped.append(header)
        else:
            original(header, payload, addr)

    monkeypatch.setattr(source, "_send", send)
    outgoing, incoming = await pair(server, client)
    data = b"a" * (2 * MSS) + b"tail"
    await asyncio.wait_for(outgoing.send(data), 3)
    await asyncio.wait_for(outgoing.close(), 3)
    assert await read_exact(incoming, len(data)) == data
    assert await incoming.recv() == b""
    await asyncio.wait_for(incoming.close(), 3)
    assert outgoing.state == incoming.state == "CLOSED" and dropped


async def test_duplex_reordered_duplicate_streams_and_serial_sends(endpoints):
    loss = LossConfig(duplicate=0.5, reorder=0.5, reorder_delay=0.005)
    server, client = await endpoints(loss=loss, seed=17), await endpoints(loss=loss, seed=29)
    outgoing, incoming = await pair(server, client)
    first, second = bytes(range(251)) * 100, b"return" * 3000
    results = await asyncio.wait_for(asyncio.gather(outgoing.send(first), incoming.send(second),
        read_exact(incoming, len(first)), read_exact(outgoing, len(second))), 5)
    assert results[2:] == [first, second]
    await outgoing.send(b"")
    await outgoing.send(b"next")
    assert await incoming.recv() == b"next"
    assert outgoing.stats()["bytes_sent"] == len(first) + 4
    await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 3)


async def test_cancelled_pipeline_aborts_and_releases_buffers(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original = server._send
    monkeypatch.setattr(server, "_send", lambda h, p, a:
                        None if h.flags == ACK else original(h, p, a))
    sending = asyncio.create_task(outgoing.send(b"x" * (3 * MSS)))
    try:
        assert await asyncio.wait_for(read_exact(incoming, 3 * MSS), 2) == b"x" * (3 * MSS)
        assert outgoing.stats()["pending_packets"] == 3
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
        assert not outgoing._pending and not outgoing._reorder
        assert outgoing.stats()["bytes_in_flight"] == 0
        with pytest.raises(ConnectionAbortedError):
            await outgoing.send(b"different")
        with pytest.raises(ConnectionResetError):
            await asyncio.wait_for(incoming.recv(), 2)
    finally:
        await cancel_tasks(sending)


async def test_inactivity_aborts_pipeline_and_waiting_reader(endpoints):
    config = SlidingWindowConfig(initial_rto=0.01, max_rto=0.02, inactivity_timeout=0.1)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    server._udp.config = client._udp.config = LossConfig(loss=1)
    results = await asyncio.wait_for(asyncio.gather(outgoing.send(b"x" * (3 * MSS)),
        incoming.recv(), return_exceptions=True), 2)
    assert all(isinstance(result, TimeoutError) for result in results)
    assert not outgoing._pending and outgoing.stats()["bytes_in_flight"] == 0


async def test_sequence_exhaustion_reserves_fin_and_aborts_cleanly(endpoints):
    server, client = await endpoints(), await endpoints()
    outgoing, _ = await pair(server, client)
    outgoing._tx_seq = 0xFFFFFFFE
    with pytest.raises(OverflowError, match="reserve one for FIN"):
        await outgoing.send(b"would wrap FIN")
    assert outgoing.state == "CLOSED" and not outgoing._pending


async def test_sixteen_mib_at_five_percent_loss_matches_sha256(endpoints, record_property):
    server = await endpoints(loss=LossConfig(loss=0.05), seed=11)
    client = await endpoints(loss=LossConfig(loss=0.05), seed=29)
    outgoing, incoming = await pair(server, client)
    data = bytes(range(256)) * (16 * 1024 * 1024 // 256)
    started = asyncio.get_running_loop().time()
    _, received = await asyncio.wait_for(asyncio.gather(outgoing.send(data),
        read_exact(incoming, len(data))), 90)
    elapsed = asyncio.get_running_loop().time() - started
    record_property("stage2_16mib_seconds", round(elapsed, 3))
    assert hashlib.sha256(received).digest() == hashlib.sha256(data).digest()
    assert len(received) == 16 * 1024 * 1024
    assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(data)
    assert outgoing.stats()["retx"] > 0 and not outgoing._pending
    assert server._udp.stats()["dropped"] > 0 and client._udp.stats()["dropped"] > 0
    await asyncio.wait_for(outgoing.close(), 3)
    assert await incoming.recv() == b""


async def test_window_beats_stop_and_wait_with_controlled_delay(record_property):
    data = bytes(range(256)) * 256  # 64 KiB; timing evidence only for this local scenario.
    elapsed = {}
    for name, transport, config in (
        ("stage1", StopAndWaitTransport, StopAndWaitConfig(initial_rto=0.5, max_rto=0.5)),
        ("stage2", SlidingWindowTransport, SlidingWindowConfig(initial_rto=0.5, max_rto=0.5))):
        server = transport(config=config, loss=LossConfig(delay=0.01))
        client = transport(config=config, loss=LossConfig(delay=0.01))
        try:
            await server.listen(("127.0.0.1", 0))
            outgoing, incoming = await pair(server, client)
            started = asyncio.get_running_loop().time()
            _, received = await asyncio.wait_for(asyncio.gather(outgoing.send(data),
                read_exact(incoming, len(data))), 10)
            elapsed[name] = asyncio.get_running_loop().time() - started
            assert received == data and outgoing.stats()["retx"] == 0
        finally:
            await asyncio.gather(server.close(), client.close())
    for name, seconds in elapsed.items():
        record_property(name + "_delay_seconds", round(seconds, 3))
    assert elapsed["stage2"] < elapsed["stage1"], elapsed


@pytest.mark.parametrize("window", [0, 33, True, 1.5])
def test_invalid_window_rejected(window):
    with pytest.raises(ValueError, match="window_packets"):
        SlidingWindowConfig(window_packets=window)


def test_stage1_config_is_not_silently_accepted():
    with pytest.raises(TypeError, match="SlidingWindowConfig"):
        SlidingWindowTransport(config=StopAndWaitConfig())
