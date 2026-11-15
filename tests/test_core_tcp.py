"""Contract parity on real sockets plus TCP lifecycle and Linux metric checks."""

import asyncio
import socket
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport
from core import tcp
from core.tcp import TcpTransport


async def read_bytes(conn, count):
    data = bytearray()
    while len(data) < count:
        chunk = await asyncio.wait_for(conn.recv(min(4093, count - len(data))), 5)
        assert chunk, "Unexpected EOF"
        data.extend(chunk)
    return bytes(data)


@pytest.fixture(params=["loopback", "tcp"])
async def pair(request):
    if request.param == "tcp":
        server, client = TcpTransport(), TcpTransport()
    else:
        registry = LoopbackRegistry()
        server, client = LoopbackTransport(registry), LoopbackTransport(registry)
    conns = []
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        conns.append(outgoing)
        incoming = await asyncio.wait_for(server.accept(), 5)
        conns.append(incoming)
        yield outgoing, incoming
    finally:
        await asyncio.gather(*(conn.close() for conn in conns))
        await asyncio.gather(server.close(), client.close())


async def test_contract_bidirectional_stream_and_counters(pair):
    outgoing, incoming = pair
    payload = bytes(range(256)) * 4096  # 1 MiB, spanning many send/read buffers.
    _, received = await asyncio.wait_for(
        asyncio.gather(outgoing.send(payload), read_bytes(incoming, len(payload))), 10)
    assert received == payload
    await incoming.send(b"verified")
    assert await read_bytes(outgoing, 8) == b"verified"
    assert outgoing.stats()["bytes_sent"] == len(payload)
    assert incoming.stats()["bytes_received"] == len(payload)
    assert {"proto", "cwnd_bytes", "rtt_ms", "retx", "bytes_in_flight"} <= outgoing.stats().keys()


async def test_contract_buffered_data_before_repeatable_eof(pair):
    outgoing, incoming = pair
    await outgoing.send(b"")
    assert await incoming.recv(0) == b""
    assert await incoming.recv(-1) == b""
    await outgoing.send(b"first")
    await outgoing.send(b"second")
    await outgoing.close()
    await outgoing.close()
    assert await read_bytes(incoming, 11) == b"firstsecond"
    assert await asyncio.wait_for(incoming.recv(), 5) == b""
    assert await asyncio.wait_for(incoming.recv(), 5) == b""
    with pytest.raises(ConnectionResetError):
        await outgoing.send(b"too late")


async def test_close_wakes_all_acceptors_and_is_terminal():
    server = TcpTransport()
    with pytest.raises(RuntimeError, match="not listening"):
        await server.accept()
    await server.listen(("127.0.0.1", 0))
    waiters = [asyncio.create_task(server.accept()) for _ in range(3)]
    await asyncio.sleep(0)
    await asyncio.gather(server.close(), server.close())
    outcomes = await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), 5)
    assert all(isinstance(result, RuntimeError) for result in outcomes)
    for operation in (server.accept(), server.listen(("127.0.0.1", 0)),
                      server.connect(("127.0.0.1", 1))):
        with pytest.raises(RuntimeError, match="closed"):
            await operation


async def test_transport_close_releases_accepted_and_queued_connections():
    server, client = TcpTransport(), TcpTransport()
    try:
        await server.listen(("127.0.0.1", 0))
        first = await client.connect(server.local_addr)
        accepted = await asyncio.wait_for(server.accept(), 5)
        second = await client.connect(server.local_addr)
        # Wait for the second server callback without accepting that connection.
        async def wait_for_queue():
            while server._accept_queue.empty():
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_queue(), 5)
        await server.close()
        assert await asyncio.wait_for(first.recv(), 5) == b""
        assert await asyncio.wait_for(second.recv(), 5) == b""
        with pytest.raises(ConnectionResetError):
            await accepted.send(b"closed")
        assert not server._connections
    finally:
        await asyncio.gather(server.close(), client.close())


async def test_bind_conflict_and_retry():
    first, second = TcpTransport(), TcpTransport()
    try:
        await first.listen(("127.0.0.1", 0))
        with pytest.raises(RuntimeError, match="already listening"):
            await first.listen(("127.0.0.1", 0))
        with pytest.raises(OSError):
            await second.listen(first.local_addr)
        await second.listen(("127.0.0.1", 0))
        assert second.local_addr[1] != first.local_addr[1]
    finally:
        await asyncio.gather(first.close(), second.close())


async def test_connection_refused():
    client = TcpTransport()
    # Reserve an unused local port without listening, avoiding a fixed port.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        try:
            with pytest.raises(ConnectionRefusedError):
                await asyncio.wait_for(client.connect(reserved.getsockname()), 5)
        finally:
            await client.close()


async def test_close_cancels_pending_connect(monkeypatch):
    started = asyncio.Event()
    async def delayed_connect(*args):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", delayed_connect)
    client = TcpTransport()
    connecting = asyncio.create_task(client.connect(("127.0.0.1", 12345)))
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(client.close(), 5)
    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert not client._pending


async def test_close_during_listener_startup_releases_connections(monkeypatch):
    started = asyncio.Event()
    original = asyncio.Server.start_serving
    async def delayed_start(server):
        await original(server)
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(asyncio.Server, "start_serving", delayed_start)
    server, client = TcpTransport(), TcpTransport()
    listening = asyncio.create_task(server.listen(("127.0.0.1", 0)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        outgoing = await client.connect(server.local_addr)
        await asyncio.wait_for(server.accept(), 5)
        await asyncio.wait_for(server.close(), 5)
        with pytest.raises(asyncio.CancelledError):
            await listening
        assert await asyncio.wait_for(outgoing.recv(), 5) == b""
    finally:
        await asyncio.gather(server.close(), client.close())
        await asyncio.gather(listening, return_exceptions=True)


async def test_slow_reader_backpressures_sender_then_resumes():
    server, client = TcpTransport(), TcpTransport()
    sending = None
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await asyncio.wait_for(server.accept(), 5)
        outgoing._writer.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
        payload = b"slow-reader" * (1024 * 1024)
        sending = asyncio.create_task(outgoing.send(payload))
        async def wait_for_backpressure():
            # A paused reader plus a full writer buffer means the application
            # must consume data before the sender can make progress.
            while not (incoming._reader._paused and
                       outgoing._writer.transport.get_write_buffer_size() > 65536):
                assert not sending.done()
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_for_backpressure(), 5)
        assert outgoing.stats()["bytes_sent"] < len(payload)
        received = await asyncio.wait_for(read_bytes(incoming, len(payload)), 15)
        await asyncio.wait_for(sending, 5)
        assert received == payload
    finally:
        if sending is not None and not sending.done():
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
        await asyncio.gather(server.close(), client.close())


@pytest.mark.parametrize("platform,raw", [("win32", b""), ("linux", b"short"),
                                         ("linux", OSError("unavailable"))])
def test_unavailable_metrics_are_none(monkeypatch, platform, raw):
    monkeypatch.setattr(tcp, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
    sock = Mock()
    if isinstance(raw, OSError):
        sock.getsockopt.side_effect = raw
    else:
        sock.getsockopt.return_value = raw
    assert all(value is None for value in tcp._tcp_metrics(sock).values())


def test_linux_tcp_info_units_and_total_retransmissions(monkeypatch):
    monkeypatch.setattr(tcp, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
    raw = bytearray(104)
    # Byte offsets from Linux UAPI tcp.h, including distracting retrans fields.
    for offset, value in {16: 1448, 24: 8, 28: 2, 32: 1, 36: 1,
                          68: 12345, 80: 10, 100: 42}.items():
        struct.pack_into("=I", raw, offset, value)
    sock = Mock()
    sock.getsockopt.return_value = bytes(raw)
    assert tcp._tcp_metrics(sock) == dict(cwnd_bytes=14480, rtt_ms=12.345,
                                         retx=42, bytes_in_flight=8688)


async def test_explicit_congestion_control_is_not_silently_ignored(monkeypatch):
    monkeypatch.setattr(tcp, "sys", SimpleNamespace(platform="win32"))
    transport = TcpTransport(congestion_control="reno")
    try:
        with pytest.raises(NotImplementedError, match="Linux"):
            await transport.listen(("127.0.0.1", 0))
        assert transport.local_addr is None
        with pytest.raises(NotImplementedError, match="Linux"):
            await transport.connect(("127.0.0.1", 1))
    finally:
        await transport.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux TCP socket options")
@pytest.mark.parametrize("algorithm", ["reno", "cubic"])
async def test_linux_algorithm_on_both_ends_and_live_metrics(algorithm):
    server, client = TcpTransport(algorithm), TcpTransport(algorithm)
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await asyncio.wait_for(server.accept(), 5)
        await outgoing.send(b"linux")
        assert await read_bytes(incoming, 5) == b"linux"
        for conn in (outgoing, incoming):
            sock = conn._writer.get_extra_info("socket")
            actual = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, 32)
            assert actual.rstrip(b"\0").decode() == algorithm
            assert conn.stats()["cwnd_bytes"] > 0
            assert conn.stats()["rtt_ms"] >= 0
            assert conn.stats()["retx"] >= 0
    finally:
        await asyncio.gather(server.close(), client.close())
