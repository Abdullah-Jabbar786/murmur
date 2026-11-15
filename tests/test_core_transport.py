"""Tests for Contract 2 Transport interface and LoopbackTransport implementation."""

import asyncio
import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport


@pytest.fixture
def registry():
    reg = LoopbackRegistry()
    yield reg
    reg.clear()


@pytest.mark.asyncio
async def test_loopback_bidirectional_transfer(registry):
    server_transport = LoopbackTransport(registry)
    client_transport = LoopbackTransport(registry)

    server_addr = ("10.0.3.11", 6881)
    await server_transport.listen(server_addr)

    # Connect client to server
    client_conn = await client_transport.connect(server_addr)
    server_conn = await server_transport.accept()

    # Client -> Server
    await client_conn.send(b"ping from client")
    received = await server_conn.recv(1024)
    assert received == b"ping from client"

    # Server -> Client
    await server_conn.send(b"pong from server")
    received = await client_conn.recv(1024)
    assert received == b"pong from server"

    await client_conn.close()
    await server_conn.close()
    await server_transport.close()
    await client_transport.close()


@pytest.mark.asyncio
async def test_loopback_stream_chunking(registry):
    server_transport = LoopbackTransport(registry)
    client_transport = LoopbackTransport(registry)

    server_addr = ("10.0.1.11", 6881)
    await server_transport.listen(server_addr)

    client_conn = await client_transport.connect(server_addr)
    server_conn = await server_transport.accept()

    # Send 100 bytes all at once
    payload = b"X" * 100
    await client_conn.send(payload)

    # Read in small chunks of 25 bytes
    chunks = []
    for _ in range(4):
        c = await server_conn.recv(25)
        chunks.append(c)

    assert b"".join(chunks) == payload

    await client_conn.close()
    await server_conn.close()
    await server_transport.close()
    await client_transport.close()


@pytest.mark.asyncio
async def test_loopback_eof_on_close(registry):
    server_transport = LoopbackTransport(registry)
    client_transport = LoopbackTransport(registry)

    server_addr = ("10.0.2.11", 6881)
    await server_transport.listen(server_addr)

    client_conn = await client_transport.connect(server_addr)
    server_conn = await server_transport.accept()

    # Close client side
    await client_conn.close()

    # Server should receive EOF (empty bytes)
    eof = await server_conn.recv(1024)
    assert eof == b""

    # Further reads continue to return EOF
    assert await server_conn.recv(1024) == b""

    await server_conn.close()
    await server_transport.close()
    await client_transport.close()


@pytest.mark.asyncio
async def test_loopback_connection_refused(registry):
    client_transport = LoopbackTransport(registry)
    non_existent_addr = ("10.0.99.99", 9999)

    with pytest.raises(ConnectionRefusedError):
        await client_transport.connect(non_existent_addr)

    await client_transport.close()


@pytest.mark.asyncio
async def test_loopback_address_already_in_use(registry):
    t1 = LoopbackTransport(registry)
    t2 = LoopbackTransport(registry)

    addr = ("10.0.1.11", 6881)
    await t1.listen(addr)

    with pytest.raises(OSError, match="already in use"):
        await t2.listen(addr)

    await t1.close()
    await t2.close()


@pytest.mark.asyncio
async def test_loopback_stats_reporting(registry):
    server_transport = LoopbackTransport(registry)
    client_transport = LoopbackTransport(registry)

    addr = ("10.0.1.15", 6881)
    await server_transport.listen(addr)

    client_conn = await client_transport.connect(addr)
    server_conn = await server_transport.accept()

    await client_conn.send(b"hello stats")
    _ = await server_conn.recv(1024)

    c_stats = client_conn.stats()
    s_stats = server_conn.stats()

    assert c_stats["proto"] == "loopback"
    assert c_stats["bytes_sent"] == 11
    assert s_stats["bytes_received"] == 11

    await client_conn.close()
    await server_conn.close()
    await server_transport.close()
    await client_transport.close()
