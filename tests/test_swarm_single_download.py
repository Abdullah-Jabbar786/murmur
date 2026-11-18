"""I1: real TCP downloads and peer session failure boundaries."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import sys

import pytest

from core.tcp import TcpTransport
from peer.metadata import build_metadata, generate_dataset
from peer.node import PeerProtocolError, SeedServer, download_file
from peer.storage import PieceHashError, PieceStore
from peer.wire import (
    Bitfield, Choke, Handshake, Have, Interested, NotInterested, Piece, Request,
    Unchoke, encode_message, read_message,
)


@asynccontextmanager
async def running_seed(tmp_path, data):
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    metadata = build_metadata(source)
    with PieceStore(source, metadata, mode="seed") as store:
        server = SeedServer(store, peer_id=b"s" * 20)
        await server.start(("127.0.0.1", 0))
        try:
            yield metadata, store, server
        finally:
            await server.close()


async def test_i1_64_mib_real_tcp_hash_verified(tmp_path):
    source = tmp_path / "seed/payload.bin"
    metadata = generate_dataset(source, tmp_path / "metadata.json")
    destination = tmp_path / "leecher/payload.bin"
    with PieceStore(source, metadata, mode="seed") as store:
        server = SeedServer(store)
        await server.start(("127.0.0.1", 0))
        try:
            result = await download_file(metadata, destination, server.address, total_timeout=60)
            assert (result.bytes_received, result.pieces_verified, result.hash_failures) == (
                64 * 1024 * 1024, 256, 0)
        finally:
            await server.close()
    assert build_metadata(destination) == metadata
    def digest(path):
        value = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                value.update(chunk)
        return value.digest()
    assert digest(source) == digest(destination)


@pytest.mark.parametrize("size", [0, 1, 16385, 262151])
async def test_short_and_empty_downloads(tmp_path, size):
    data = (bytes(range(251)) * (size // 251 + 1))[:size]
    async with running_seed(tmp_path, data) as (metadata, store, server):
        destination = tmp_path / "download.bin"
        result = await download_file(metadata, destination, server.address)
        assert result.bytes_received == size and result.hash_failures == 0
        assert destination.read_bytes() == data


async def test_resume_requests_only_unverified_pieces(tmp_path):
    data = b"a" * 262144 + b"short final piece"
    async with running_seed(tmp_path, data) as (metadata, store, server):
        destination = tmp_path / "download.bin"
        with PieceStore(destination, metadata) as partial:
            for offset, size in partial.missing_blocks(0):
                partial.write_block(0, offset, data[offset:offset + size])
        result = await download_file(metadata, destination, server.address, resume=True)
        assert result.bytes_received == len(b"short final piece")
        assert result.pieces_verified == 2
        assert destination.read_bytes() == data
        # Already complete resumes do not need a reachable server.
        result = await download_file(metadata, destination, ("127.0.0.1", 1), resume=True)
        assert result.bytes_received == 0
        with pytest.raises(FileExistsError):
            await download_file(metadata, destination, server.address)
        assert destination.read_bytes() == data


@pytest.mark.parametrize("always_corrupt", [False, True])
async def test_corruption_retry_and_retry_limit(tmp_path, monkeypatch, always_corrupt):
    data = b"a" * 20000
    async with running_seed(tmp_path, data) as (metadata, store, server):
        read = store.read_block
        corruptions = []

        def corrupted(piece, offset, length):
            block = read(piece, offset, length)
            if offset == 0 and (always_corrupt or not corruptions):
                corruptions.append(piece)
                return bytes([block[0] ^ 1]) + block[1:]
            return block

        monkeypatch.setattr(store, "read_block", corrupted)
        destination = tmp_path / "download.bin"
        if always_corrupt:
            with pytest.raises(PieceHashError):
                await download_file(metadata, destination, server.address, max_piece_retries=1)
            assert len(corruptions) == 2
            with PieceStore(destination, metadata, mode="resume") as partial:
                assert not partial.complete
        else:
            result = await download_file(metadata, destination, server.address)
            assert result.hash_failures == 1 and result.bytes_received == len(data) * 2
            assert destination.read_bytes() == data


@asynccontextmanager
async def scripted_seed(metadata, script):
    transport = TcpTransport()
    await transport.listen(("127.0.0.1", 0))

    async def run():
        connection = await transport.accept()
        try:
            await script(connection)
        finally:
            await connection.close()

    task = asyncio.create_task(run())
    try:
        yield transport.local_addr
    finally:
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        await transport.close()
        if isinstance(results[0], BaseException) and not isinstance(results[0], asyncio.CancelledError):
            raise results[0]


async def send(connection, metadata, message):
    await connection.send(encode_message(message, num_pieces=metadata.num_pieces))


async def fake_handshake(connection, metadata, pieces=None):
    assert isinstance(await read_message(connection, num_pieces=metadata.num_pieces), Handshake)
    await send(connection, metadata, Handshake(metadata.info_hash, b"s" * 20))
    assert isinstance(await read_message(connection, num_pieces=metadata.num_pieces), Bitfield)
    if pieces is None:
        pieces = frozenset(range(metadata.num_pieces))
    await send(connection, metadata, Bitfield(pieces))


@pytest.mark.parametrize("failure", ["wrong_hash", "no_handshake", "missing_piece",
                                     "duplicate_bitfield", "wrong_block", "early_eof"])
async def test_bad_seed_protocol_rejected(tmp_path, failure):
    source = tmp_path / "source.bin"
    source.write_bytes(b"expected")
    metadata = build_metadata(source)

    async def script(connection):
        if failure in ("no_handshake", "wrong_hash"):
            # Consume the client's greeting before closing with a bad reply.
            # Closing TCP with unread input can reset instead of delivering it.
            assert isinstance(await read_message(connection, num_pieces=1), Handshake)
        if failure == "no_handshake":
            await send(connection, metadata, Interested())
            return
        if failure == "wrong_hash":
            await send(connection, metadata, Handshake(bytes(32), b"s" * 20))
            return
        await fake_handshake(connection, metadata,
                             frozenset() if failure == "missing_piece" else None)
        if failure == "missing_piece":
            return
        assert isinstance(await read_message(connection, num_pieces=1), Interested)
        if failure == "duplicate_bitfield":
            await send(connection, metadata, Bitfield(frozenset({0})))
            return
        await send(connection, metadata, Unchoke())
        assert isinstance(await read_message(connection, num_pieces=1), Request)
        if failure == "wrong_block":
            await send(connection, metadata, Piece(0, 1, b"expected"))

    async with scripted_seed(metadata, script) as address:
        with pytest.raises(PeerProtocolError):
            await download_file(metadata, tmp_path / "download.bin", address, total_timeout=3)
    with PieceStore(tmp_path / "download.bin", metadata, mode="resume") as store:
        assert not store.complete


async def test_seed_rejects_request_while_choked_and_keeps_listening(tmp_path):
    async with running_seed(tmp_path, b"hello") as (metadata, store, server):
        transport = TcpTransport()
        try:
            connection = await transport.connect(server.address)
            await send(connection, metadata, Handshake(metadata.info_hash, b"l" * 20))
            assert isinstance(await read_message(connection, num_pieces=1), Handshake)
            await send(connection, metadata, Bitfield(frozenset()))
            assert isinstance(await read_message(connection, num_pieces=1), Bitfield)
            await send(connection, metadata, Request(0, 0, 5))
            assert await asyncio.wait_for(read_message(connection, num_pieces=1), 2) is None
        finally:
            await transport.close()
        assert any("while choked" in error for error in server.errors)
        assert (await download_file(metadata, tmp_path / "download.bin", server.address)).bytes_received == 5


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_cancellation_closes_connection_and_preserves_partial(tmp_path, cancel):
    source = tmp_path / "source.bin"
    source.write_bytes(b"hello")
    metadata = build_metadata(source)
    waiting, closed = asyncio.Event(), asyncio.Event()

    async def script(connection):
        await fake_handshake(connection, metadata)
        assert isinstance(await read_message(connection, num_pieces=1), Interested)
        waiting.set()  # Never unchoke; no requests are allowed yet.
        assert await connection.recv(1) == b""
        closed.set()

    async with scripted_seed(metadata, script) as address:
        task = asyncio.create_task(download_file(metadata, tmp_path / "download.bin", address,
                                                io_timeout=0.3, total_timeout=2))
        await asyncio.wait_for(waiting.wait(), 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
            await task
        await asyncio.wait_for(closed.wait(), 2)
    # No open handle remains; the user may explicitly resume or remove the file.
    (tmp_path / "download.bin").rename(tmp_path / "partial.bin")


async def test_self_identity_rejected(tmp_path):
    async with running_seed(tmp_path, b"hello") as (metadata, store, server):
        with pytest.raises(PeerProtocolError, match="self connection"):
            await download_file(metadata, tmp_path / "download.bin", server.address,
                                peer_id=server.peer_id)


async def test_total_deadline_bounds_continuing_control_messages(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"hello")
    metadata = build_metadata(source)

    async def script(connection):
        await fake_handshake(connection, metadata)
        assert isinstance(await read_message(connection, num_pieces=1), Interested)
        while True:
            await send(connection, metadata, Have(0))
            await asyncio.sleep(0.02)

    async with scripted_seed(metadata, script) as address:
        with pytest.raises(asyncio.TimeoutError):
            await download_file(metadata, tmp_path / "download.bin", address,
                                io_timeout=5, total_timeout=0.2)


async def test_seed_shutdown_closes_idle_session_and_is_idempotent(tmp_path):
    async with running_seed(tmp_path, b"hello") as (metadata, store, server):
        transport = TcpTransport()
        try:
            connection = await transport.connect(server.address)
            assert isinstance(await read_message(connection, num_pieces=1), Handshake)
            await server.close()
            await server.close()
            assert await asyncio.wait_for(connection.recv(1), 2) == b""
            assert not store.closed
        finally:
            await transport.close()


async def test_download_cli(tmp_path):
    async with running_seed(tmp_path, b"cli payload") as (metadata, store, server):
        meta_path, destination = tmp_path / "metadata.json", tmp_path / "download.bin"
        meta_path.write_bytes(metadata.canonical_bytes())
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "peer.node", "download", "--metadata", str(meta_path),
            "--file", str(destination), "--seed-port", str(server.address[1]),
            cwd=Path(__file__).resolve().parents[1], stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            assert process.returncode == 0, stderr.decode()
            assert b"Verified 1 pieces" in stdout
            assert destination.read_bytes() == b"cli payload"
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
