"""Idle choked MurmurTP peers stay alive; silent neighbors still expire."""

import asyncio
from contextlib import asynccontextmanager, ExitStack
import hashlib

import pytest

from murmurtp.api import MurmurTransport
from murmurtp.flow import FlowControlConfig
from peer.metadata import Metadata
from peer.storage import PieceStore
from peer.swarm import SwarmNode


DATA = b"idle peer regression" * 100
META = Metadata("payload.bin", len(DATA), 4096, (hashlib.sha256(DATA).hexdigest(),))


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 3)


@asynccontextmanager
async def peers(tmp_path):
    nodes, links = [], []
    with ExitStack() as resources:
        try:
            for index in range(2):
                store = resources.enter_context(PieceStore(tmp_path / f"peer{index}.bin", META))
                config = FlowControlConfig(initial_rto=.02, min_rto=.01, max_rto=.1,
                                           inactivity_timeout=.3, probe_interval=.02)
                node = SwarmNode(store, transport=MurmurTransport(config=config),
                                 peer_id=bytes([index]) * 20, keepalive_interval=.05)
                nodes.append(node)
                await node.start(("127.0.0.1", 0))
            await nodes[1].connect(nodes[0].address)
            await eventually(lambda: all(node.peer_ids for node in nodes))
            links = [next(iter(node._links.values())) for node in nodes]
            yield nodes, links
        finally:
            await asyncio.wait_for(asyncio.gather(*(node.close() for node in nodes)), 3)
            assert all(not node._tasks and not node._links for node in nodes)
            assert all(link.keepalive.done() and link.worker.done() for link in links)


async def test_choked_peer_survives_idle_then_downloads_without_extra_payload(tmp_path):
    async with peers(tmp_path) as (nodes, links):
        source, leecher = nodes
        await source.set_upload_allowed(leecher.peer_id, False)
        assert source.store.write_block(0, 0, DATA)
        await source._verified(0)
        await eventually(lambda: links[0].peer_interested)
        # Wait for more than two transport idle deadlines while no slot exists.
        await asyncio.sleep(.7)
        assert links[0].am_choking and links[1].peer_choking
        assert all(link.connection.state == "ESTABLISHED" for link in links)
        assert all(not node.errors and node.bytes_sent == node.bytes_received == 0 for node in nodes)
        await source.set_upload_allowed(leecher.peer_id, True)
        await leecher.wait_complete(3)
        await eventually(lambda: source.bytes_sent == len(DATA))
        assert leecher.store.path.read_bytes() == DATA
        assert leecher.bytes_received == len(DATA) and leecher.bytes_sent == 0
        assert all(not node.errors for node in nodes)


async def test_keepalive_does_not_mask_silent_neighbor(tmp_path, monkeypatch):
    async with peers(tmp_path) as (nodes, links):
        # Blackhole all datagrams, including ACKs and RSTs, after setup.
        for node in nodes:
            monkeypatch.setattr(node.transport, "_send", lambda *args: None)
        await eventually(lambda: all(node.errors for node in nodes))
        assert all(any("inactivity timeout" in error for error in node.errors) for node in nodes)
        await eventually(lambda: all(link.keepalive.done() for link in links))


@pytest.mark.parametrize("interval", [0, -1, float("nan"), True])
def test_invalid_keepalive_interval(tmp_path, interval):
    with PieceStore(tmp_path / "invalid.bin", META) as store:
        with pytest.raises(ValueError):
            SwarmNode(store, keepalive_interval=interval)
