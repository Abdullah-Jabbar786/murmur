"""A2.1: exercise selection, request windows, and timed choking over the wire."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json

import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport
from peer.metadata import Metadata
from peer.node import _Session, _run_cli
from peer.storage import PieceStore
from peer.swarm import SwarmNode
from peer.wire import Choke, Have, Interested, NotInterested, Piece, Request, Unchoke


DATA = bytes(range(256)) * 2560  # 40 one-block pieces; exceeds the largest window.
META = Metadata("payload.bin", len(DATA), 16384, tuple(
    hashlib.sha256(DATA[i:i + 16384]).hexdigest() for i in range(0, len(DATA), 16384)))


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), 3)


@asynccontextmanager
async def network(tmp_path, *, metadata=META, owned=(), **options):
    registry = LoopbackRegistry()
    stores, sessions, endpoints = [], [], []
    store = PieceStore(tmp_path / "local.bin", metadata)
    stores.append(store)
    for piece in owned:
        for offset, length in store.missing_blocks(piece):
            start = piece * metadata.piece_length + offset
            store.write_block(piece, offset, DATA[start:start + length])
    node = SwarmNode(store, transport=LoopbackTransport(registry), peer_id=b"z" * 20,
                     io_timeout=options.pop("io_timeout", 5), request_timeout=3, **options)

    async def peer(pieces):
        index = len(sessions) + 1
        remote = PieceStore(tmp_path / f"remote{index}.bin", metadata)
        stores.append(remote)
        for piece in pieces:
            for offset, length in remote.missing_blocks(piece):
                start = piece * metadata.piece_length + offset
                remote.write_block(piece, offset, DATA[start:start + length])
        endpoint = LoopbackTransport(registry)
        endpoints.append(endpoint)
        connection = await endpoint.connect(node.address)
        session = _Session(connection, remote, bytes([index]) * 20, 3)
        sessions.append(session)
        await session.handshake()
        await eventually(lambda: session.peer_id in node.peer_ids)
        return session

    try:
        await node.start(("127.0.0.1", 7100))
        yield node, peer
    finally:
        await node.close()
        for session in sessions:
            await session.connection.close()
        for endpoint in endpoints:
            await endpoint.close()
        for item in stores:
            item.close()
        assert not node._claims and not node._tasks
        assert node._policy_task.done()


async def next_request(session):
    while True:
        message = await session.receive()
        assert message is not None
        if isinstance(message, Request):
            return message


async def test_have_during_not_interested_send_reannounces_interest(tmp_path, monkeypatch):
    async with network(tmp_path) as (node, peer):
        source = await peer([0])
        link = node._links[source.peer_id]
        original = link.send
        injected = False

        async def send(message):
            nonlocal injected
            if isinstance(message, NotInterested) and not injected:
                injected = True
                await source.send(Have(1))
                await eventually(lambda: 1 in link.available)
            await original(message)

        monkeypatch.setattr(link, "send", send)
        await source.send(Unchoke())
        first = await next_request(source)
        assert first.piece == 0
        await source.send(Piece(0, first.offset, source.store.read_block(0, first.offset, first.length)))
        interested, saw_not_interested = True, False
        while True:
            message = await source.receive()
            if isinstance(message, NotInterested):
                interested, saw_not_interested = False, True
            elif isinstance(message, Interested):
                interested = True
            elif isinstance(message, Request):
                assert saw_not_interested and interested and message.piece == 1
                break


@pytest.mark.parametrize("depth", [1, 16, 32])
async def test_window_fills_across_pieces_before_any_response(tmp_path, depth):
    async with network(tmp_path, pipelining_depth=depth) as (node, peer):
        source = await peer(range(META.num_pieces))
        await source.send(Unchoke())
        served = set()
        while len(served) < META.num_pieces:
            batch = [await next_request(source)
                     for _ in range(min(depth, META.num_pieces - len(served)))]
            if not served:
                assert node.bytes_received == 0
                assert len(node._links[source.peer_id].pending) == depth
            assert len({r.piece for r in batch}) == len(batch)
            for request in reversed(batch):  # Responses need not arrive in request order.
                assert request.piece not in served
                served.add(request.piece)
                await source.send(Piece(request.piece, request.offset,
                    source.store.read_block(request.piece, request.offset, request.length)))
        await node.wait_complete(3)
        assert node.store.path.read_bytes() == DATA
        assert node.bytes_received == len(DATA) and not node.errors
        assert node._links[source.peer_id].peak_pending == depth


@pytest.mark.parametrize("partial", [False, True])
async def test_live_availability_and_partial_priority_choose_first_request(tmp_path, partial):
    data = DATA[:4 * 32768]
    metadata = Metadata("small.bin", len(data), 32768, tuple(
        hashlib.sha256(data[i:i + 32768]).hexdigest() for i in range(0, len(data), 32768)))
    async with network(tmp_path, metadata=metadata, pipelining_depth=1) as (node, peer):
        if partial:
            node.store.write_block(0, 0, data[:16384])
        source = await peer(range(4))
        common = await peer(range(3))  # Piece 3 is uniquely available from source.
        await source.send(Unchoke())
        first = await next_request(source)
        assert (first.piece, first.offset) == ((0, 16384) if partial else (3, 0))
        await common.send(Unchoke())
        second = await next_request(common)
        assert second.piece != first.piece  # One source owns each in-progress piece.
        assert len(node._claims) == 2


async def test_four_regular_slots_and_timer_rotates_optimistic_slot(tmp_path, monkeypatch):
    async with network(tmp_path, owned=range(META.num_pieces), choke_interval=0.05,
                       optimistic_unchoke_interval=0.2, io_timeout=0.05) as (node, peer):
        remotes = [await peer(()) for _ in range(6)]
        for remote in remotes:
            await remote.send(Interested())
        await eventually(lambda: all(link.peer_interested for link in node._links.values()))
        await eventually(lambda: len([l for l in node._links.values() if not l.am_choking]) == 5)
        first = node._policy._optimistic
        regular = node._policy._regular
        assert len(regular) == 4
        sent = []
        original = _Session.send

        async def record(session, message):
            await original(session, message)
            if session.store is node.store and isinstance(message, (Choke, Unchoke)):
                sent.append((session.remote_id.hex(), type(message)))

        monkeypatch.setattr(_Session, "send", record)
        # No new network messages trigger this change: the node's timer must run.
        await eventually(lambda: len(sent) >= 2)
        assert node._policy._regular == regular
        assert sum(not link.am_choking for link in node._links.values()) == 5
        # Slot changes are sent to the peers, and CHOKE precedes replacement UNCHOKE.
        current = node._policy._optimistic
        assert sent[:2] == [(first, Choke), (current, Unchoke)]
        assert current != first and not node.errors
        assert node._links[bytes.fromhex(first)].am_choking
        assert not node._links[bytes.fromhex(current)].am_choking
        await remotes[0].send(NotInterested())
        await eventually(lambda: not node._links[remotes[0].peer_id].peer_interested)
        assert node._links[remotes[0].peer_id].am_choking
        assert sum(not link.am_choking for link in node._links.values()) == 5


@pytest.mark.parametrize("complete", [False, True])
async def test_rate_samples_rank_downloads_for_leechers_and_uploads_for_seeds(tmp_path, complete):
    async with network(tmp_path, owned=range(META.num_pieces) if complete else (),
                       unchoke_slots=1, choke_interval=0.05,
                       optimistic_unchoke_interval=1) as (node, peer):
        remotes = [await peer(()) for _ in range(3)]
        for remote in remotes:
            await remote.send(Interested())
        await eventually(lambda: all(link.peer_interested for link in node._links.values()))
        # Feed known cumulative payload counters at the network accounting boundary.
        winner = node._links[remotes[-1].peer_id]
        other = node._links[remotes[0].peer_id]
        if complete:
            winner.uploaded = 16384
            other.downloaded = 32768
        else:
            winner.downloaded = 16384
            other.uploaded = 32768
        await eventually(lambda: node._policy._regular == (remotes[-1].peer_id.hex(),))
        assert (winner.up_bps if complete else winner.down_bps) > 0


@pytest.mark.parametrize("depth", [0, 33, True, 1.5])
async def test_invalid_window_rejected(tmp_path, depth):
    with PieceStore(tmp_path / "invalid.bin", META) as store:
        with pytest.raises(ValueError, match="pipelining_depth"):
            SwarmNode(store, pipelining_depth=depth)


async def test_cli_passes_algorithm_config_to_running_node(tmp_path, monkeypatch):
    from peer import swarm

    metadata = tmp_path / "metadata.json"
    metadata.write_bytes(META.canonical_bytes())
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(pipelining_depth=32, unchoke_slots=2,
                                     choke_interval=0.3, optimistic_unchoke_interval=0.7,
                                     keepalive_interval=0.2, seed=42)))
    inspected = []

    class InspectNode(SwarmNode):
        async def start(self, address):
            inspected.append(self)
            assert self.pipelining_depth == 32 and self._policy.unchoke_slots == 2
            assert self._policy.choke_interval == 0.3
            assert self._policy.optimistic_unchoke_interval == 0.7
            assert self.keepalive_interval == 0.2
            raise RuntimeError("settings inspected")

    monkeypatch.setattr(swarm, "SwarmNode", InspectNode)
    args = argparse.Namespace(command="swarm", metadata=str(metadata), file=str(tmp_path / "cli.bin"),
        config=str(config), distribution_mode=None, mode="create", peer=[], origin_seed=None,
        host="127.0.0.1", port=0, io_timeout=3, run_dir=None, total_timeout=3)
    with pytest.raises(RuntimeError, match="settings inspected"):
        await _run_cli(args)
    assert inspected[0].store.closed
