"""H2.2: bidirectional transfers, HAVE propagation, and session lifecycle."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import sys

import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport
from core.tcp import TcpTransport
from peer.metadata import Metadata
from peer.node import _Session
from peer.storage import PieceStore
from peer.swarm import SwarmNode
from peer import swarm
from peer.wire import Bitfield, Handshake, Piece, Request, encode_message, read_message


DATA = b"a" * 32768 + b"b" * 32768 + b"c" * 32771
META = Metadata("payload.bin", len(DATA), 32768, tuple(
    hashlib.sha256(DATA[i:i + 32768]).hexdigest() for i in range(0, len(DATA), 32768)))


def fill(store, pieces):
    for piece in pieces:
        for offset, length in store.missing_blocks(piece):
            start = piece * META.piece_length + offset
            store.write_block(piece, offset, DATA[start:start + length])


@asynccontextmanager
async def cluster(tmp_path, owned, transport="loopback", node_type=SwarmNode):
    registry = LoopbackRegistry()
    stores, nodes = [], []
    try:
        for index, pieces in enumerate(owned):
            store = PieceStore(tmp_path / f"peer{index}.bin", META)
            stores.append(store)
            fill(store, pieces)
            endpoint = LoopbackTransport(registry) if transport == "loopback" else TcpTransport()
            node = node_type(store, transport=endpoint, peer_id=bytes([index + 1]) * 20,
                             request_timeout=0.5)
            nodes.append(node)
            await node.start(("127.0.0.1", 7000 + index if transport == "loopback" else 0))
        yield nodes
    finally:
        await asyncio.gather(*(node.close() for node in nodes))
        for store in stores:
            store.close()


async def eventually(condition, timeout=3):
    async def wait():
        while not condition():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), timeout)


@pytest.mark.parametrize("transport", ["loopback", "tcp"])
async def test_three_peer_relay_and_completion_keeps_serving(tmp_path, transport):
    async with cluster(tmp_path, [range(4), (), ()], transport) as (seed, middle, last):
        await last.connect(middle.address)  # Neither initially owns any pieces.
        await middle.connect(seed.address)
        await asyncio.gather(middle.wait_complete(5), last.wait_complete(5))
        assert middle.bytes_received == last.bytes_received == len(DATA)
        assert middle.bytes_sent == seed.bytes_sent == len(DATA)
        assert last.store.path.read_bytes() == middle.store.path.read_bytes() == DATA
        assert seed.peer_id not in last.peer_ids  # Last peer really used the relay.
        await eventually(lambda: last.remote_pieces(middle.peer_id) == frozenset(range(4)))
        assert middle.store.complete and middle.peer_ids


async def test_three_incomplete_peers_exchange_complementary_pieces(tmp_path):
    async with cluster(tmp_path, [(0, 3), (1,), (2,)]) as nodes:
        first, second, third = nodes
        await first.connect(second.address)
        await second.connect(third.address)
        await third.connect(first.address)
        await asyncio.gather(*(node.wait_complete(5) for node in nodes))
        for node in nodes:
            assert node.store.path.read_bytes() == DATA
            assert node.bytes_sent > 0 and node.bytes_received > 0
            assert not node.hash_failures
        await eventually(lambda: all(not node._claims for node in nodes))


async def test_two_sources_do_not_duplicate_piece_downloads(tmp_path):
    async with cluster(tmp_path, [range(4), range(4), ()]) as (one, two, leecher):
        await asyncio.gather(leecher.connect(one.address), leecher.connect(two.address))
        await leecher.wait_complete(5)
        assert leecher.bytes_received == len(DATA)
        assert leecher.store.path.read_bytes() == DATA
        await eventually(lambda: not leecher._claims)


@pytest.mark.parametrize("simultaneous", [False, True])
async def test_duplicate_connections_converge_and_transfer(tmp_path, simultaneous):
    async with cluster(tmp_path, [range(4), ()]) as (seed, leecher):
        if simultaneous:
            await asyncio.gather(seed.connect(leecher.address), leecher.connect(seed.address))
        else:
            await leecher.connect(seed.address)
            await leecher.connect(seed.address)
        await leecher.wait_complete(5)
        assert leecher.peer_ids == frozenset({seed.peer_id})
        assert seed.peer_ids == frozenset({leecher.peer_id})
        assert leecher.store.path.read_bytes() == DATA


class InitiallyChokedNode(SwarmNode):
    async def _run_link(self, link, ready):
        link.upload_allowed = False
        await super()._run_link(link, ready)


async def test_interest_and_explicit_unchoke_gate_requests(tmp_path):
    async with cluster(tmp_path, [range(4), ()], node_type=InitiallyChokedNode) as (seed, leecher):
        await leecher.connect(seed.address)
        await eventually(lambda: seed._links[leecher.peer_id].peer_interested)
        assert seed._links[leecher.peer_id].am_choking
        with pytest.raises(asyncio.TimeoutError):
            await leecher.wait_complete(0.03)
        assert leecher.bytes_received == 0 and not leecher._claims
        await seed.set_upload_allowed(leecher.peer_id, True)
        await leecher.wait_complete(5)
        await eventually(lambda: not seed._links[leecher.peer_id].peer_interested)
        assert seed._links[leecher.peer_id].am_choking


async def test_corrupt_source_dropped_then_good_source_completes(tmp_path, monkeypatch):
    async with cluster(tmp_path, [range(4), range(4), ()]) as (bad, good, leecher):
        read = bad.store.read_block
        def corrupted(piece, offset, length):
            data = read(piece, offset, length)
            return bytes([data[0] ^ 1]) + data[1:]
        monkeypatch.setattr(bad.store, "read_block", corrupted)
        await leecher.connect(bad.address)
        await eventually(lambda: not leecher.peer_ids)
        assert leecher.hash_failures == 3 and not leecher._claims
        await leecher.connect(good.address)
        await leecher.wait_complete(5)
        assert leecher.store.path.read_bytes() == DATA


async def test_disconnect_releases_partial_piece_for_another_source(tmp_path, monkeypatch):
    async with cluster(tmp_path, [range(4), range(4), ()]) as (bad, good, leecher):
        read = bad.store.read_block
        def disconnect(piece, offset, length):
            if offset:
                raise ConnectionResetError("simulated source departure")
            return read(piece, offset, length)
        monkeypatch.setattr(bad.store, "read_block", disconnect)
        await leecher.connect(bad.address)
        await eventually(lambda: not leecher.peer_ids)
        assert leecher.store.started_pieces == frozenset({0})
        assert not leecher._claims
        await leecher.connect(good.address)
        await leecher.wait_complete(5)
        assert leecher.bytes_received == len(DATA)


async def test_shutdown_wakes_completion_waiter_and_does_not_close_store(tmp_path):
    async with cluster(tmp_path, [()]) as (node,):
        waiter = asyncio.create_task(node.wait_complete(5))
        await node.close()
        await node.close()
        with pytest.raises(RuntimeError, match="closed before"):
            await waiter
        assert not node.store.closed
        with pytest.raises(RuntimeError, match="closed"):
            await node.connect(("127.0.0.1", 9999))


async def test_wrong_info_hash_and_self_connections_rejected(tmp_path):
    async with cluster(tmp_path, [range(4)]) as (node,):
        with pytest.raises(ValueError, match="self connection"):
            await node.connect(node.address)
        endpoint = LoopbackTransport(node.transport.registry)
        connection = await endpoint.connect(node.address)
        await connection.send(encode_message(Handshake(bytes(32), b"x" * 20), num_pieces=4))
        assert isinstance(await read_message(connection, num_pieces=4), Handshake)
        assert await asyncio.wait_for(read_message(connection, num_pieces=4), 2) is None
        await connection.close()
        await endpoint.close()


@pytest.mark.parametrize("message", [Bitfield(frozenset()), Piece(0, 0, b"x"), Request(0, 0, 1)])
async def test_bad_session_message_disconnects_only_that_peer(tmp_path, message):
    async with cluster(tmp_path, [range(4), ()]) as (seed, leecher):
        endpoint = LoopbackTransport(seed.transport.registry)
        connection = await endpoint.connect(seed.address)
        async def send(msg):
            await connection.send(encode_message(msg, num_pieces=4))
        await send(Handshake(META.info_hash, b"x" * 20))
        assert isinstance(await read_message(connection, num_pieces=4), Handshake)
        await send(Bitfield(frozenset()))
        assert isinstance(await read_message(connection, num_pieces=4), Bitfield)
        await send(message)
        assert await asyncio.wait_for(read_message(connection, num_pieces=4), 2) is None
        await connection.close()
        await endpoint.close()
        await leecher.connect(seed.address)
        await leecher.wait_complete(5)


async def test_handshake_in_progress_is_closed_on_shutdown(tmp_path):
    async with cluster(tmp_path, [()]) as (node,):
        endpoint = LoopbackTransport(node.transport.registry)
        connection = await endpoint.connect(node.address)
        assert isinstance(await read_message(connection, num_pieces=4), Handshake)
        await node.close()
        assert await asyncio.wait_for(connection.recv(1), 2) == b""
        await connection.close()
        await endpoint.close()


async def test_choke_releases_pending_request_and_ignores_late_block(tmp_path, monkeypatch):
    async with cluster(tmp_path, [range(4), ()]) as (seed, leecher):
        blocked, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = swarm._Link.send

        async def delayed(link, message):
            if link.node is seed and isinstance(message, Piece) and not sent.is_set():
                blocked.set()
                await release.wait()
                await original(link, message)
                sent.set()
            else:
                await original(link, message)

        monkeypatch.setattr(swarm._Link, "send", delayed)
        await leecher.connect(seed.address)
        await asyncio.wait_for(blocked.wait(), 2)
        await seed.set_upload_allowed(leecher.peer_id, False)
        await eventually(lambda: not leecher._claims)
        release.set()
        await asyncio.wait_for(sent.wait(), 2)
        await eventually(lambda: not leecher._links[seed.peer_id].retired)
        assert leecher.bytes_received == 0
        assert not leecher.store.started_pieces
        await seed.set_upload_allowed(leecher.peer_id, True)
        await leecher.wait_complete(5)
        assert leecher.store.path.read_bytes() == DATA
        assert not leecher.errors


async def test_request_timeout_releases_reservations(tmp_path, monkeypatch):
    async with cluster(tmp_path, [range(4), ()]) as (seed, leecher):
        original = swarm._Link.send
        stalled = asyncio.Event()

        async def never_respond(link, message):
            if link.node is seed and isinstance(message, Piece):
                await stalled.wait()
            else:
                await original(link, message)

        monkeypatch.setattr(swarm._Link, "send", never_respond)
        await leecher.connect(seed.address)
        await eventually(lambda: not leecher.peer_ids)
        assert not leecher._claims and not leecher.store.complete
        assert any("TimeoutError" in error for error in leecher.errors)


async def test_have_catches_up_pieces_finished_during_handshake(tmp_path, monkeypatch):
    async with cluster(tmp_path, [(0,), range(4), ()]) as (middle, seed, last):
        blocked, release = asyncio.Event(), asyncio.Event()
        original = _Session.send

        async def delay_bitfield(session, message):
            if (session.store is middle.store and session.connection.peer_addr == last.address
                    and isinstance(message, Bitfield)):
                blocked.set()
                await release.wait()
            await original(session, message)

        monkeypatch.setattr(_Session, "send", delay_bitfield)
        connecting = asyncio.create_task(last.connect(middle.address))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            await middle.connect(seed.address)
            await middle.wait_complete(5)
            release.set()
            await asyncio.wait_for(connecting, 2)
            await last.wait_complete(5)
            assert last.store.path.read_bytes() == DATA
        finally:
            release.set()
            connecting.cancel()
            await asyncio.gather(connecting, return_exceptions=True)


async def test_swarm_cli_downloads_then_keeps_serving(tmp_path):
    async with cluster(tmp_path, [range(4), ()], "tcp") as (seed, last):
        metadata_path = tmp_path / "metadata.json"
        metadata_path.write_bytes(META.canonical_bytes())
        destination = tmp_path / "cli.bin"
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "peer.node", "swarm", "--metadata", str(metadata_path),
            "--file", str(destination), "--port", "0",
            "--peer", f"{seed.address[0]}:{seed.address[1]}",
            "--peer", f"{last.address[0]}:{last.address[1]}",
            cwd=Path(__file__).resolve().parents[1], stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            listening = await asyncio.wait_for(process.stdout.readline(), 5)
            assert b"Peer listening on" in listening
            completed = await asyncio.wait_for(process.stdout.readline(), 5)
            assert b"continuing to serve" in completed
            assert process.returncode is None
            await last.wait_complete(5)
            assert destination.read_bytes() == last.store.path.read_bytes() == DATA
            assert seed.peer_id not in last.peer_ids
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.communicate(), 5)
