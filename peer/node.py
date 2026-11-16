"""Peer CLI: single TCP download and bidirectional multi-peer sessions."""

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
import math
import secrets
from typing import Optional

from core.tcp import TcpTransport
from core.transport import Connection
from peer.metadata import Metadata, load_metadata
from peer.storage import PieceHashError, PieceStore
from peer.wire import (
    Bitfield, Choke, Handshake, Have, Interested, NotInterested, Piece, Request,
    Unchoke, encode_message, read_message,
)


class PeerProtocolError(ValueError):
    """Peer ordering, identity, or outstanding-request violation."""


def _identity(peer_id: Optional[bytes]) -> bytes:
    if peer_id is None:
        return secrets.token_bytes(20)
    if not isinstance(peer_id, bytes) or len(peer_id) != 20:
        raise ValueError("peer_id must be 20 immutable bytes")
    return peer_id


def _timeout(value: float) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError("timeouts must be positive and finite")


class _Session:
    def __init__(self, connection: Connection, store: PieceStore, peer_id: bytes,
                 timeout: float) -> None:
        self.connection, self.store = connection, store
        self.peer_id, self.timeout = peer_id, timeout

    async def send(self, message) -> None:
        raw = encode_message(message, num_pieces=self.store.metadata.num_pieces)
        await asyncio.wait_for(self.connection.send(raw), self.timeout)

    async def receive(self):
        return await asyncio.wait_for(read_message(
            self.connection, num_pieces=self.store.metadata.num_pieces), self.timeout)

    async def handshake(self) -> set[int]:
        await self.send(Handshake(self.store.metadata.info_hash, self.peer_id))
        remote = await self.receive()
        if not isinstance(remote, Handshake):
            raise PeerProtocolError("HANDSHAKE must be the first message")
        if remote.info_hash != self.store.metadata.info_hash:
            raise PeerProtocolError("peer info_hash does not match metadata")
        if remote.peer_id == self.peer_id:
            raise PeerProtocolError("self connection: peer IDs must be different")
        self.remote_id = remote.peer_id
        self.advertised_pieces = self.store.owned_pieces
        await self.send(Bitfield(self.advertised_pieces))
        remote = await self.receive()
        if not isinstance(remote, Bitfield):
            raise PeerProtocolError("BITFIELD must follow HANDSHAKE exactly once")
        return set(remote.pieces)


async def serve_connection(connection: Connection, store: PieceStore, peer_id: bytes,
                           *, io_timeout: float = 30.0) -> None:
    """Serve one session; the caller owns and closes the connection and store."""
    _timeout(io_timeout)
    peer_id = _identity(peer_id)
    if store.closed or not store.complete:
        raise ValueError("seed store must be open and fully verified")
    session = _Session(connection, store, peer_id, io_timeout)
    await session.handshake()
    interested = False
    while True:
        message = await session.receive()
        if message is None:
            return
        if isinstance(message, Interested):
            interested = True
            await session.send(Unchoke())
        elif isinstance(message, NotInterested):
            interested = False
            await session.send(Choke())
        elif isinstance(message, Request):
            if not interested:
                raise PeerProtocolError("REQUEST received while choked/uninterested")
            data = store.read_block(message.piece, message.offset, message.length)
            await session.send(Piece(message.piece, message.offset, data))
        elif isinstance(message, (Have, Choke, Unchoke)):
            # This seed never downloads; remote availability/upload policy is unused.
            continue
        else:
            raise PeerProtocolError("unexpected message after initial BITFIELD")


class SeedServer:
    """TCP listener owning its sessions; the caller retains ownership of the store."""

    def __init__(self, store: PieceStore, *, peer_id: Optional[bytes] = None,
                 io_timeout: float = 30.0) -> None:
        _timeout(io_timeout)
        if store.closed or not store.complete:
            raise ValueError("seed store must be open and fully verified")
        self.store, self.peer_id = store, _identity(peer_id)
        self.io_timeout = io_timeout
        self.errors: deque[str] = deque(maxlen=32)
        self._transport = TcpTransport()
        self._accept_task = None
        self._sessions: set[asyncio.Task] = set()
        self._close_task = None

    @property
    def address(self) -> tuple[str, int]:
        if self._transport.local_addr is None:
            raise RuntimeError("seed is not listening")
        return self._transport.local_addr

    async def start(self, address: tuple[str, int] = ("127.0.0.1", 6881)) -> None:
        await self._transport.listen(address)
        self._accept_task = asyncio.create_task(self._accept())

    async def _accept(self) -> None:
        while True:
            connection = await self._transport.accept()
            task = asyncio.create_task(self._serve(connection))
            self._sessions.add(task)
            task.add_done_callback(self._sessions.discard)

    async def _serve(self, connection: Connection) -> None:
        try:
            await serve_connection(connection, self.store, self.peer_id, io_timeout=self.io_timeout)
        except Exception as exc:
            # A bad/disconnected peer must not stop the listening seed. Retain a
            # bounded diagnostic history; the CLI reports it at shutdown.
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            await connection.close()

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self) -> None:
        if self._accept_task is not None:
            self._accept_task.cancel()
            await asyncio.gather(self._accept_task, return_exceptions=True)
        sessions = list(self._sessions)
        for task in sessions:
            task.cancel()
        await asyncio.gather(*sessions, return_exceptions=True)
        await self._transport.close()


@dataclass(frozen=True)
class DownloadResult:
    bytes_received: int
    hash_failures: int
    pieces_verified: int


async def _download(session: _Session, *, max_piece_retries: int) -> DownloadResult:
    store = session.store
    available = await session.handshake()
    needed = set(range(store.metadata.num_pieces)) - store.owned_pieces
    if not needed <= available:
        raise PeerProtocolError("remote peer does not have every required piece")
    choked = True
    received_bytes = hash_failures = 0

    async def response():
        nonlocal choked
        message = await session.receive()
        if message is None:
            raise PeerProtocolError("peer closed before download completed")
        if isinstance(message, Choke):
            choked = True
        elif isinstance(message, Unchoke):
            choked = False
        elif isinstance(message, Have):
            available.add(message.piece)
        elif not isinstance(message, Piece):
            raise PeerProtocolError("unexpected message during download")
        return message

    if needed:
        await session.send(Interested())
    for piece in sorted(needed):
        for attempt in range(max_piece_retries + 1):
            try:
                for offset, length in store.missing_blocks(piece):
                    while choked:
                        if isinstance(await response(), Piece):
                            raise PeerProtocolError("unsolicited PIECE while awaiting UNCHOKE")
                    await session.send(Request(piece, offset, length))
                    while True:
                        message = await response()
                        if isinstance(message, Piece):
                            break
                    if (message.piece, message.offset, len(message.data)) != (piece, offset, length):
                        raise PeerProtocolError("PIECE does not match outstanding REQUEST")
                    received_bytes += len(message.data)
                    if store.write_block(piece, offset, message.data):
                        await session.send(Have(piece))
                break
            except PieceHashError:
                hash_failures += 1
                if attempt == max_piece_retries:
                    raise
    if not store.complete:
        raise PeerProtocolError("download ended without verifying every piece")
    await session.send(NotInterested())
    return DownloadResult(received_bytes, hash_failures, len(store.owned_pieces))


async def download_file(metadata: Metadata, path, seed_address: tuple[str, int], *,
                        peer_id: Optional[bytes] = None, resume: bool = False,
                        io_timeout: float = 30.0, total_timeout: float = 1800.0,
                        max_piece_retries: int = 2) -> DownloadResult:
    """Download from one explicit seed. Keep a partial file on failure for resume."""
    peer_id = _identity(peer_id)
    _timeout(io_timeout)
    _timeout(total_timeout)
    if type(max_piece_retries) is not int or max_piece_retries < 0:
        raise ValueError("max_piece_retries must be a nonnegative integer")
    with PieceStore(path, metadata, mode="resume" if resume else "create") as store:
        if store.complete:
            return DownloadResult(0, 0, len(store.owned_pieces))
        transport = TcpTransport()
        try:
            async def transfer():
                connection = await asyncio.wait_for(transport.connect(seed_address), io_timeout)
                session = _Session(connection, store, peer_id, io_timeout)
                return await _download(session, max_piece_retries=max_piece_retries)
            return await asyncio.wait_for(transfer(), total_timeout)
        finally:
            await transport.close()


async def _run_cli(args) -> None:
    metadata = load_metadata(args.metadata)
    if args.command == "swarm":
        from pathlib import Path

        from core.config import load_config
        from peer.swarm import SwarmNode

        if args.config is not None and not Path(args.config).is_file():
            raise FileNotFoundError(f"config file does not exist: {args.config}")
        config = load_config(args.config)
        if not isinstance(config, dict):
            raise ValueError("config must be a JSON object")
        distribution_mode = args.distribution_mode or config.get("mode", "swarm")
        if distribution_mode not in ("swarm", "client_server"):
            raise ValueError("config mode must be swarm or client_server")
        peers = list(args.peer)
        is_seed = args.mode == "seed"
        if distribution_mode == "client_server":
            if is_seed:
                if peers or args.origin_seed is not None:
                    raise ValueError("client-server seed accepts connections; omit --peer/--origin-seed")
            else:
                if args.origin_seed is None:
                    raise ValueError("client-server leechers require --origin-seed IPv4:port")
                if any(peer != args.origin_seed for peer in peers):
                    raise ValueError("client-server --peer must match --origin-seed")
                peers = [args.origin_seed]
        elif args.origin_seed is not None:
            raise ValueError("--origin-seed requires client_server distribution mode")
        with PieceStore(args.file, metadata, mode=args.mode) as store:
            node = SwarmNode(store, io_timeout=args.io_timeout, mode=distribution_mode,
                             is_seed=is_seed, seed_address=args.origin_seed)
            try:
                await node.start((args.host, args.port))
                print(f"Peer listening on {node.address[0]}:{node.address[1]}", flush=True)
                for address in peers:
                    await node.connect(address)
                await node.wait_complete(args.total_timeout)
                status = "continuing to serve" if node.uploads_enabled else "client-server uploads disabled"
                print(f"Verified {len(store.owned_pieces)} pieces; {status}: {args.file}", flush=True)
                await asyncio.Event().wait()
            finally:
                await node.close()
                print(f"Payload received: {node.bytes_received}; sent: {node.bytes_sent}; "
                      f"hash failures: {node.hash_failures}")
                for error in node.errors:
                    print(f"Peer session: {error}")
        return
    if args.command == "download":
        result = await download_file(metadata, args.file, (args.seed_host, args.seed_port),
                                     resume=args.resume, io_timeout=args.io_timeout,
                                     total_timeout=args.total_timeout)
        print(f"Verified {result.pieces_verified} pieces; file: {args.file}")
        print(f"Payload bytes received: {result.bytes_received}; hash failures: {result.hash_failures}")
        return
    with PieceStore(args.file, metadata, mode="seed") as store:
        server = SeedServer(store, io_timeout=args.io_timeout)
        try:
            await server.start((args.host, args.port))
            print(f"Seed listening on {server.address[0]}:{server.address[1]} "
                  f"({metadata.num_pieces} verified pieces)", flush=True)
            await asyncio.Event().wait()
        finally:
            await server.close()
            for error in server.errors:
                print(f"Peer session: {error}")


def _endpoint(value):
    from ipaddress import IPv4Address

    try:
        host, port = value.rsplit(":", 1)
        host, port = str(IPv4Address(host)), int(port)
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
        return host, port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("peer must be IPv4:port (port 1..65535)") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "download", "swarm"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--metadata", required=True)
        sub.add_argument("--file", required=True)
        sub.add_argument("--io-timeout", type=float, default=30.0)
        if name in ("seed", "swarm"):
            sub.add_argument("--host", default="127.0.0.1")
            sub.add_argument("--port", type=int, default=6881)
            if name == "swarm":
                sub.add_argument("--peer", type=_endpoint, action="append", default=[])
                sub.add_argument("--mode", choices=("create", "resume", "seed"), default="create")
                sub.add_argument("--total-timeout", type=float, default=1800.0)
                sub.add_argument("--config", help="JSON config; its mode selects distribution behavior")
                sub.add_argument("--distribution-mode", choices=("swarm", "client_server"),
                                 help="override the JSON mode setting")
                sub.add_argument("--origin-seed", type=_endpoint,
                                 help="sole permitted server endpoint for client-server leechers")
        else:
            sub.add_argument("--seed-host", default="127.0.0.1")
            sub.add_argument("--seed-port", type=int, default=6881)
            sub.add_argument("--resume", action="store_true")
            sub.add_argument("--total-timeout", type=float, default=1800.0)
    args = parser.parse_args()
    port = args.seed_port if args.command == "download" else args.port
    minimum_port = 1 if args.command == "download" else 0
    if not minimum_port <= port <= 65535:
        parser.error(f"port must be in [{minimum_port}, 65535]")
    try:
        asyncio.run(_run_cli(args))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        parser.exit(1, f"peer: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
