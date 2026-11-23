"""Replacement sessions use new UDP endpoints, preserving v1 isolation."""
import asyncio
from types import SimpleNamespace

import pytest

from murmurtp.api import MurmurTransport
from peer.discovery import serve_discovered
from tracker.client import AnnounceResult
from tracker.protocol import Peer


async def test_discovery_retries_failed_dial_but_bounds_attempts():
    refreshed = asyncio.Event()
    calls = []
    remote = Peer(b"r" * 20, "127.0.0.2", 6882)

    class Tracker:
        announce_interval = 0.001
        refreshes = 0

        async def announce(self, *args, **kwargs):
            self.refreshes += 1
            if self.refreshes > 10:
                refreshed.set()
            return AnnounceResult((remote,), 1, 1)

    async def connect(address):
        calls.append(address)
        raise ConnectionResetError("injected dial failure")

    node = SimpleNamespace(peer_id=b"l" * 20, address=("127.0.0.1", 6882),
                           peer_ids=set(), store=SimpleNamespace(complete=True, left=0),
                           wait_complete=asyncio.sleep, connect=connect)
    task = asyncio.create_task(serve_discovered(node, Tracker(), 30, single_session=True))
    try:
        await asyncio.wait_for(refreshed.wait(), 2)
        assert calls == [("127.0.0.2", 6882)] * 8
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reset_reconnect_changes_port_and_releases_endpoints():
    server, client = MurmurTransport(), MurmurTransport()
    endpoints = []
    try:
        await server.listen(("127.0.0.1", 0))
        await client.listen(("127.0.0.1", 0))
        original = await client.connect(server.local_addr)
        incoming = await asyncio.wait_for(server.accept(), 2)
        old_port = incoming.peer_addr[1]
        with pytest.raises(RuntimeError, match="active"):
            await client.connect(server.local_addr)
        original._abort(ConnectionResetError("injected session failure"))
        replacement = await client.connect(server.local_addr)
        received = await asyncio.wait_for(server.accept(), 2)
        assert received.peer_addr[1] != old_port
        assert replacement.owner is not client
        assert original._error is not None
        await asyncio.wait_for(replacement.send(b"recovered"), 3)
        assert await asyncio.wait_for(received.recv(100), 3) == b"recovered"
        endpoints = list(client._retry_endpoints)
    finally:
        await client.close()
        await server.close()
    assert endpoints and all(e._closed and e._socket_closed.done() for e in endpoints)
    assert not client._retry_endpoints
