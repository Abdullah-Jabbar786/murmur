"""Stage 1 over real UDP, including deterministic control loss and 1 MiB at 10%."""

import asyncio
import hashlib

import pytest

from murmurtp.connection import StopAndWaitConfig
from murmurtp.header import ACK, DATA, FIN, MSS, SYN, Header, encode_packet
from murmurtp.lossy import LossConfig
from murmurtp.transport import StopAndWaitTransport


FAST = StopAndWaitConfig(initial_rto=0.005, max_rto=0.02, inactivity_timeout=3)


@pytest.fixture
async def endpoints():
    created = []
    async def make(**kwargs):
        bind = kwargs.pop("bind", True)
        endpoint = StopAndWaitTransport(config=kwargs.pop("config", FAST), **kwargs)
        created.append(endpoint)
        if bind:
            await endpoint.listen(("127.0.0.1", 0))
        return endpoint
    yield make
    await asyncio.wait_for(asyncio.gather(*(endpoint.close() for endpoint in created)), 3)
    assert all(not endpoint._tasks for endpoint in created)


async def pair(server, client):
    outgoing = await asyncio.wait_for(client.connect(server.local_addr), 2)
    incoming = await asyncio.wait_for(server.accept(), 2)
    return outgoing, incoming


async def read_exact(conn, length):
    result = bytearray()
    while len(result) < length:
        chunk = await conn.recv(min(7919, length - len(result)))
        assert chunk, "Unexpected EOF"
        result.extend(chunk)
    return bytes(result)


@pytest.mark.parametrize("lost", ["syn", "syn_ack", "final_ack", "data", "data_ack", "fin", "fin_ack"])
async def test_each_control_or_data_packet_can_be_lost_once(endpoints, monkeypatch, lost):
    server, client = await endpoints(), await endpoints()
    source = client if lost in ("syn", "final_ack", "data", "fin") else server
    original, dropped = source._send, []
    def send(header, payload, addr):
        target = {
            "syn": header.flags == SYN,
            "syn_ack": header.flags == SYN | ACK,
            "final_ack": header.flags == ACK and header.seq_num == 1,
            "data": header.flags == DATA,
            "data_ack": header.flags == ACK and header.seq_num == 0 and header.cum_ack == 1,
            "fin": header.flags == FIN,
            "fin_ack": header.flags == ACK and header.seq_num == 0 and header.cum_ack == 2,
        }[lost]
        if target and not dropped:
            dropped.append(header)
        else:
            original(header, payload, addr)
    monkeypatch.setattr(source, "_send", send)
    outgoing, incoming = await pair(server, client)
    await asyncio.wait_for(outgoing.send(b"hello"), 2)
    assert await incoming.recv() == b"hello"
    await asyncio.wait_for(outgoing.close(), 2)
    assert await incoming.recv() == b""
    await asyncio.wait_for(incoming.close(), 2)
    await outgoing.close()
    assert dropped
    assert outgoing.state == incoming.state == "CLOSED"
    assert server._accept_queue.empty()  # Retries must not create duplicate accepts.
    with pytest.raises(ConnectionError):
        await outgoing.send(b"too late")


async def test_one_mib_at_ten_percent_loss_matches_sha256(endpoints):
    server = await endpoints(loss=LossConfig(loss=0.1), seed=11)
    client = await endpoints(loss=LossConfig(loss=0.1), seed=29)
    outgoing, incoming = await pair(server, client)
    payload = bytes(range(256)) * 4096
    _, received = await asyncio.wait_for(
        asyncio.gather(outgoing.send(payload), read_exact(incoming, len(payload))), 30)
    assert hashlib.sha256(received).digest() == hashlib.sha256(payload).digest()
    assert len(received) == 1024 * 1024
    assert outgoing.stats()["retx"] > 0
    assert client._udp.stats()["dropped"] > 0
    assert server._udp.stats()["dropped"] > 0
    assert outgoing.stats()["bytes_sent"] == incoming.stats()["bytes_received"] == len(payload)
    await asyncio.wait_for(outgoing.close(), 3)
    assert await incoming.recv() == b""


async def test_bidirectional_duplicate_reordered_packets_are_delivered_once(endpoints):
    loss = LossConfig(duplicate=1, reorder=0.5, reorder_delay=0.01, jitter=0.001)
    server, client = await endpoints(loss=loss, seed=42), await endpoints(loss=loss, seed=43)
    outgoing, incoming = await pair(server, client)
    first, second = b"abc" * 3000, b"xyz" * 3000
    results = await asyncio.wait_for(asyncio.gather(
        outgoing.send(first), incoming.send(second),
        read_exact(incoming, len(first)), read_exact(outgoing, len(second))), 5)
    assert results[2:] == [first, second]
    await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 3)
    assert await incoming.recv() == await outgoing.recv() == b""


@pytest.mark.parametrize("flags,seq", [(ACK, 1), (SYN | ACK, 0)])
async def test_delayed_handshake_cannot_acknowledge_first_data(endpoints, monkeypatch, flags, seq):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original = server._send
    block = True
    def send(header, payload, addr):
        if not (block and header.flags == ACK and header.seq_num == 0):
            original(header, payload, addr)
    monkeypatch.setattr(server, "_send", send)
    sending = asyncio.create_task(outgoing.send(b"first"))
    try:
        assert await asyncio.wait_for(incoming.recv(), 2) == b"first"
        client.datagram_received(encode_packet(
            Header(flags, outgoing.conn_id, seq_num=seq, cum_ack=1)), server.local_addr)
        await asyncio.sleep(0.02)
        assert not sending.done()
        assert outgoing._tx_seq == 0
        block = False
        await asyncio.wait_for(sending, 2)
    finally:
        if not sending.done():
            sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


async def test_bad_checksum_wrong_id_and_future_data_never_reach_stream(endpoints):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    packet = encode_packet(Header(DATA, incoming.conn_id), b"bad")
    corrupt = packet[:-1] + bytes([packet[-1] ^ 1])
    server.datagram_received(corrupt, client.local_addr)
    server.datagram_received(encode_packet(Header(DATA, incoming.conn_id + 1), b"wrong-id"), client.local_addr)
    server.datagram_received(encode_packet(Header(DATA, incoming.conn_id, seq_num=10), b"future"), client.local_addr)
    assert server.invalid_packets == 1
    await asyncio.wait_for(outgoing.send(b"valid"), 2)
    await asyncio.wait_for(outgoing.close(), 2)
    assert await incoming.recv() == b"valid"
    assert await incoming.recv() == b""


async def test_handshake_retry_budget_is_bounded(endpoints):
    config = StopAndWaitConfig(initial_rto=0.005, max_rto=0.01, syn_retries=2)
    server = await endpoints(config=config, loss=LossConfig(loss=1))
    client = await endpoints(config=config)
    with pytest.raises(ConnectionRefusedError):
        await asyncio.wait_for(client.connect(server.local_addr), 2)
    assert client._peers[server.local_addr].stats()["retx"] == 2
    assert server._accept_queue.empty()


async def test_inactivity_aborts_send_and_waiting_reader(endpoints):
    config = StopAndWaitConfig(initial_rto=0.005, max_rto=0.01, inactivity_timeout=0.08)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    server._udp.config = client._udp.config = LossConfig(loss=1)
    results = await asyncio.wait_for(asyncio.gather(
        outgoing.send(b"lost"), incoming.recv(), return_exceptions=True), 2)
    assert all(isinstance(result, TimeoutError) for result in results)
    assert outgoing.state == incoming.state == "CLOSED"


async def test_bounded_receive_buffer_resumes_when_reader_drains(endpoints):
    config = StopAndWaitConfig(initial_rto=0.005, max_rto=0.02,
                              inactivity_timeout=3, receive_buffer=MSS)
    server, client = await endpoints(config=config), await endpoints(config=config)
    outgoing, incoming = await pair(server, client)
    payload = b"x" * (3 * MSS)
    sending = asyncio.create_task(outgoing.send(payload))
    try:
        await asyncio.sleep(0.03)
        assert not sending.done()
        assert incoming.stats()["buffered_bytes"] == MSS
        assert await asyncio.wait_for(read_exact(incoming, len(payload)), 2) == payload
        await asyncio.wait_for(sending, 2)
    finally:
        if not sending.done():
            sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


async def test_multiple_lost_final_acks_are_answered_after_time_wait(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    await outgoing.send(b"x")
    original, states = server._send, []
    def send(header, payload, addr):
        if header.flags == ACK and header.seq_num == 0 and header.cum_ack == 2:
            states.append(incoming.state)
            if len(states) <= 4:
                return
        original(header, payload, addr)
    monkeypatch.setattr(server, "_send", send)
    await asyncio.wait_for(outgoing.close(), 2)
    assert "CLOSED" in states
    assert await incoming.recv() == b"x"
    assert await incoming.recv() == b""


async def test_transport_shutdown_wakes_blocked_calls(endpoints):
    server, client = await endpoints(), await endpoints()
    _, incoming = await pair(server, client)
    readers = [asyncio.create_task(incoming.recv())]
    acceptors = [asyncio.create_task(server.accept()) for _ in range(2)]
    await asyncio.sleep(0)
    await server.close()
    results = await asyncio.wait_for(asyncio.gather(*readers, *acceptors, return_exceptions=True), 2)
    assert isinstance(results[0], ConnectionAbortedError)
    assert all(isinstance(result, RuntimeError) for result in results[1:])


async def test_cancelled_send_aborts_instead_of_reusing_sequence(endpoints, monkeypatch):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    original = server._send
    monkeypatch.setattr(server, "_send", lambda h, p, a: None if h.flags == ACK else original(h, p, a))
    sending = asyncio.create_task(outgoing.send(b"prefix"))
    assert await asyncio.wait_for(incoming.recv(), 2) == b"prefix"
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    with pytest.raises(ConnectionAbortedError):
        await outgoing.send(b"different")
    with pytest.raises(ConnectionResetError):
        await asyncio.wait_for(incoming.recv(), 2)


async def test_one_socket_demultiplexes_multiple_peers(endpoints):
    server, first, second = await endpoints(), await endpoints(), await endpoints()
    outgoing1, incoming1 = await pair(server, first)
    outgoing2, incoming2 = await pair(server, second)
    assert incoming1.conn_id != incoming2.conn_id
    await asyncio.wait_for(asyncio.gather(outgoing1.send(b"one"), outgoing2.send(b"two")), 2)
    assert await incoming1.recv() == b"one"
    assert await incoming2.recv() == b"two"
    assert incoming1.owner is incoming2.owner is server
    with pytest.raises(RuntimeError, match="reconnect"):
        await first.connect(server.local_addr)


async def test_client_connect_binds_an_ephemeral_udp_socket(endpoints):
    server, client = await endpoints(), await endpoints(bind=False)
    assert client.local_addr is None
    outgoing, incoming = await pair(server, client)
    assert client.local_addr[1] > 0
    await asyncio.wait_for(outgoing.send(b"auto-bound"), 2)
    assert await incoming.recv() == b"auto-bound"


async def test_close_during_handshake_releases_connector(endpoints):
    server = await endpoints(loss=LossConfig(loss=1))
    client = await endpoints()
    connecting = asyncio.create_task(client.connect(server.local_addr))
    async def started():
        while not client._peers:
            await asyncio.sleep(0)
    await asyncio.wait_for(started(), 2)
    await asyncio.wait_for(client.close(), 2)
    with pytest.raises(ConnectionAbortedError):
        await asyncio.wait_for(connecting, 2)


async def test_cancelled_close_waiter_does_not_cancel_fin_exchange(endpoints):
    server, client = await endpoints(), await endpoints()
    outgoing, incoming = await pair(server, client)
    closing = asyncio.create_task(outgoing.close())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await asyncio.wait_for(outgoing.close(), 2)
    assert outgoing.state == "CLOSED"
    assert await incoming.recv() == b""


async def test_accept_after_peer_closed_still_delivers_buffered_data(endpoints):
    server, client = await endpoints(), await endpoints()
    outgoing = await asyncio.wait_for(client.connect(server.local_addr), 2)
    await asyncio.wait_for(outgoing.send(b"arrived before accept"), 2)
    await asyncio.wait_for(outgoing.close(), 2)
    await asyncio.wait_for(server._peers[client.local_addr].close(), 2)
    incoming = await asyncio.wait_for(server.accept(), 2)
    assert incoming.state == "CLOSED"
    assert await incoming.recv() == b"arrived before accept"
    assert await incoming.recv() == b""


def test_stage1_config_rejects_invalid_limits():
    for kwargs in ({"initial_rto": 0}, {"max_rto": float("inf")},
                   {"inactivity_timeout": -1}, {"initial_rto": 6},
                   {"syn_retries": 6}, {"syn_retries": True}, {"receive_buffer": MSS - 1}):
        with pytest.raises(ValueError):
            StopAndWaitConfig(**kwargs)
