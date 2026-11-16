"""H2.3: origin-only downloads and zero leecher payload uploads."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import sys

import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport
from core.tcp import TcpTransport
from peer.metadata import Metadata
from peer.storage import PieceStore
from peer.swarm import SwarmNode
from peer.wire import Bitfield, Choke, Handshake, Interested, Request, encode_message, read_message

DATA = b"a" * 32768 + b"b" * 32768 + b"tail"
META = Metadata("payload.bin", len(DATA), 32768, tuple(
    hashlib.sha256(DATA[i:i + 32768]).hexdigest() for i in range(0, len(DATA), 32768)))


def fill(store, pieces):
    for piece in pieces:
        for offset, size in store.missing_blocks(piece):
            start = piece * META.piece_length + offset
            store.write_block(piece, offset, DATA[start:start + size])


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 3)


@asynccontextmanager
async def setup_nodes(tmp_path, transport="loopback", count=2):
    registry, stores, nodes = LoopbackRegistry(), [], []
    def endpoint():
        return LoopbackTransport(registry) if transport == "loopback" else TcpTransport()
    try:
        for index in range(count + 1):
            store = PieceStore(tmp_path / f"node{index}.bin", META)
            stores.append(store)
            if index == 0:
                fill(store, range(3))
            node = SwarmNode(store, transport=endpoint(), mode="client_server", is_seed=index == 0,
                             seed_address=None if index == 0 else nodes[0].address,
                             peer_id=bytes([index + 1]) * 20)
            nodes.append(node)
            await node.start(("127.0.0.1", 8000 + index if transport == "loopback" else 0))
        yield nodes, endpoint
    finally:
        await asyncio.gather(*(node.close() for node in nodes))
        for store in stores:
            store.close()


@pytest.mark.parametrize("transport", ["loopback", "tcp"])
async def test_two_leechers_download_only_from_seed_and_upload_zero(tmp_path, transport):
    async with setup_nodes(tmp_path, transport) as ((seed, one, two), endpoint):
        await asyncio.gather(one.connect(seed.address), two.connect(seed.address))
        await asyncio.gather(one.wait_complete(5), two.wait_complete(5))
        await eventually(lambda: seed.bytes_sent == 2 * len(DATA))
        for node in (one, two):
            assert node.store.path.read_bytes() == DATA
            assert node.bytes_received == len(DATA) and node.bytes_sent == 0
            assert not node.uploads_enabled and not node.is_seed
            assert node.peer_ids == frozenset({seed.peer_id})
            with pytest.raises(ValueError, match="cannot enable"):
                await node.set_upload_allowed(seed.peer_id, True)
        # Complete clients still refuse leecher-to-leecher transfers.
        with pytest.raises(ValueError, match="only to their origin seed"):
            await one.connect(two.address)
        intruder = endpoint()
        connection = await intruder.connect(one.address)
        try:
            assert await asyncio.wait_for(connection.recv(1), 2) == b""
            assert one.bytes_sent == 0
        finally:
            await connection.close()
            await intruder.close()


async def test_nonseed_outbound_rejected_before_transport_is_called(tmp_path, monkeypatch):
    async with setup_nodes(tmp_path) as ((seed, one, two), endpoint):
        async def forbidden(address):
            raise AssertionError("nonseed address reached transport.connect")
        monkeypatch.setattr(one.transport, "connect", forbidden)
        with pytest.raises(ValueError, match="only to their origin seed"):
            await one.connect(two.address)
        with pytest.raises(ValueError, match="does not dial"):
            await seed.connect(one.address)


@pytest.mark.parametrize("complete", [False, True])
async def test_even_origin_seed_cannot_request_upload_from_leecher(tmp_path, complete):
    registry = LoopbackRegistry()
    source = LoopbackTransport(registry)
    await source.listen(("127.0.0.1", 8000))
    with PieceStore(tmp_path / "leecher.bin", META) as store:
        fill(store, range(3) if complete else (0,))
        node = SwarmNode(store, transport=LoopbackTransport(registry), mode="client_server",
                         seed_address=source.local_addr, peer_id=b"l" * 20)
        await node.start(("127.0.0.1", 8001))
        connecting = asyncio.create_task(node.connect(source.local_addr))
        connection = await source.accept()
        try:
            async def send(message):
                await connection.send(encode_message(message, num_pieces=3))
            assert isinstance(await read_message(connection, num_pieces=3), Handshake)
            await send(Handshake(META.info_hash, b"s" * 20))
            assert isinstance(await read_message(connection, num_pieces=3), Bitfield)
            await send(Bitfield(frozenset()))
            await asyncio.wait_for(connecting, 2)
            await send(Interested())
            assert isinstance(await asyncio.wait_for(read_message(connection, num_pieces=3), 2), Choke)
            await send(Request(0, 0, 100))
            assert isinstance(await asyncio.wait_for(read_message(connection, num_pieces=3), 2), Choke)
            assert node.bytes_sent == 0
            assert node._links[b"s" * 20].am_choking
        finally:
            connecting.cancel()
            await asyncio.gather(connecting, return_exceptions=True)
            await node.close()
            await connection.close()
            await source.close()


def test_resumed_complete_leecher_is_not_promoted(tmp_path):
    path = tmp_path / "complete.bin"
    path.write_bytes(DATA)
    with PieceStore(path, META, mode="resume") as store:
        node = SwarmNode(store, mode="client_server", seed_address=("127.0.0.1", 8000))
        assert store.complete and not node.is_seed and not node.uploads_enabled
        with pytest.raises(AttributeError):
            node.mode = "swarm"
        with pytest.raises(AttributeError):
            node.is_seed = True


@pytest.mark.parametrize("options", [
    {"mode": "unknown"}, {"is_seed": "yes"}, {"is_seed": True},
    {"mode": "client_server"}, {"mode": "client_server", "seed_address": ("host", 8000)},
    {"mode": "client_server", "seed_address": ("127.0.0.1", 0)},
    {"mode": "client_server", "seed_address": ("127.0.0.1", True)},
    {"mode": "client_server", "seed_address": ("127.0.0.1", 65536)},
    {"mode": "swarm", "seed_address": ("127.0.0.1", 8000)},
])
def test_invalid_roles_and_endpoint_settings(tmp_path, options):
    with PieceStore(tmp_path / "data.bin", META) as store:
        with pytest.raises(ValueError):
            SwarmNode(store, **options)


@pytest.mark.parametrize("selection", ["config", "flag", "override"])
async def test_cli_config_and_override(tmp_path, selection):
    async with setup_nodes(tmp_path, "tcp", count=0) as ((seed,), endpoint):
        meta_path, config_path = tmp_path / "metadata.json", tmp_path / "config.json"
        meta_path.write_bytes(META.canonical_bytes())
        config_path.write_text(json.dumps({"mode": "client_server"}), encoding="utf-8")
        destination = tmp_path / "cli.bin"
        command = [sys.executable, "-m", "peer.node", "swarm", "--metadata", str(meta_path),
                   "--file", str(destination), "--port", "0"]
        address = f"{seed.address[0]}:{seed.address[1]}"
        if selection == "flag":
            command += ["--distribution-mode", "client_server", "--origin-seed", address]
        elif selection == "override":
            command += ["--config", str(config_path), "--distribution-mode", "swarm", "--peer", address]
        else:
            command += ["--config", str(config_path), "--origin-seed", address]
        process = await asyncio.create_subprocess_exec(
            *command, cwd=Path(__file__).resolve().parents[1],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            listening = await asyncio.wait_for(process.stdout.readline(), 5)
            assert b"Peer listening on" in listening
            completed = await asyncio.wait_for(process.stdout.readline(), 5)
            expected = b"continuing to serve" if selection == "override" else b"uploads disabled"
            assert expected in completed
            assert destination.read_bytes() == DATA
            port = int(listening.decode().strip().rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                first = await asyncio.wait_for(reader.read(1), 2)
                assert bool(first) == (selection == "override")
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.communicate(), 5)


@pytest.mark.parametrize("extra", [[], ["--origin-seed", "127.0.0.1:8000", "--peer", "127.0.0.1:8001"]])
async def test_cli_missing_or_conflicting_seed_creates_no_output(tmp_path, extra):
    meta_path, destination = tmp_path / "metadata.json", tmp_path / "download.bin"
    meta_path.write_bytes(META.canonical_bytes())
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "peer.node", "swarm", "--metadata", str(meta_path),
        "--file", str(destination), "--distribution-mode", "client_server", *extra,
        cwd=Path(__file__).resolve().parents[1],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 1
        assert b"origin-seed" in stderr
        assert not destination.exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
