"""H2.2 bidirectional peer sessions; one block in flight per connection."""

import asyncio
from collections import deque
from typing import Optional

from core.tcp import TcpTransport
from core.transport import Transport
from peer.node import PeerProtocolError, _Session, _identity, _timeout
from peer.storage import PieceHashError, PieceStore
from peer.wire import Choke, Have, Interested, NotInterested, Piece, Request, Unchoke


class _Link:
    def __init__(self, node, connection, outgoing):
        self.node, self.connection, self.outgoing = node, connection, outgoing
        self.session = _Session(connection, node.store, node.peer_id, node.io_timeout)
        self.remote_id = None
        self.available = set()
        self.am_interested = self.peer_interested = False
        self.am_choking = self.peer_choking = True
        self.upload_allowed = True
        self.wake = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.pending = None
        self.pending_key = None
        self.retired = set()
        self.worker = None
        self.closed = False
        self.failures = {}

    async def send(self, message):
        async with self.send_lock:
            await self.session.send(message)

    async def upload_policy(self):
        choke = not (self.peer_interested and self.upload_allowed)
        if choke != self.am_choking:
            self.am_choking = choke
            await self.send(Choke() if choke else Unchoke())

    async def receive_loop(self):
        while True:
            message = await self.session.receive()
            if message is None:
                return
            if isinstance(message, Have):
                self.available.add(message.piece)
            elif isinstance(message, (Choke, Unchoke)):
                self.peer_choking = isinstance(message, Choke)
                if self.peer_choking and self.pending is not None and not self.pending.done():
                    self.retired.add(self.pending_key)
                    if len(self.retired) > 64:
                        raise PeerProtocolError("too many abandoned requests")
                    self.pending.set_result(None)
            elif isinstance(message, (Interested, NotInterested)):
                self.peer_interested = isinstance(message, Interested)
                await self.upload_policy()
            elif isinstance(message, Request):
                if not self.peer_interested:
                    raise PeerProtocolError("REQUEST while choked/uninterested")
                if self.am_choking:
                    # This may have been sent before our CHOKE reached the peer.
                    continue
                data = self.node.store.read_block(message.piece, message.offset, message.length)
                await self.send(Piece(message.piece, message.offset, data))
                self.node.bytes_sent += len(data)
            elif isinstance(message, Piece):
                key = (message.piece, message.offset, len(message.data))
                if key in self.retired and key != self.pending_key:
                    self.retired.remove(key)
                    continue  # Late response to a request abandoned on CHOKE.
                if self.pending is None or self.pending.done() or key != self.pending_key:
                    raise PeerProtocolError("PIECE does not match outstanding REQUEST")
                self.retired.discard(key)
                self.pending.set_result(message.data)
            else:
                raise PeerProtocolError("unexpected message after initial BITFIELD")
            self.wake.set()

    async def download_loop(self):
        store = self.node.store
        while True:
            self.wake.clear()
            candidates = self.available - store.owned_pieces
            interested = bool(candidates)
            if interested != self.am_interested:
                self.am_interested = interested
                await self.send(Interested() if interested else NotInterested())
            candidates -= self.node._claims.keys()
            if self.peer_choking or not candidates:
                await self.wake.wait()
                continue
            # H2.2 baseline: finish partial pieces, then ascending index.
            # Rarity ranking and pipelining remain A2.1 integration work.
            piece = min((candidates & store.started_pieces) or candidates)
            self.node._claims[piece] = self  # Reserve without yielding.
            try:
                for offset, length in store.missing_blocks(piece):
                    if self.peer_choking:
                        break
                    self.pending = asyncio.get_running_loop().create_future()
                    self.pending_key = (piece, offset, length)
                    try:
                        await self.send(Request(piece, offset, length))
                        data = await asyncio.wait_for(self.pending, self.node.request_timeout)
                    finally:
                        if not self.pending.done():
                            self.pending.cancel()
                        self.pending = self.pending_key = None
                    if data is None:  # CHOKE abandoned this request.
                        break
                    self.node.bytes_received += len(data)
                    if store.write_block(piece, offset, data):
                        await self.node._verified(piece)
            except PieceHashError:
                self.node.hash_failures += 1
                self.failures[piece] = self.failures.get(piece, 0) + 1
                if self.failures[piece] > self.node.max_piece_retries:
                    raise
            finally:
                if self.node._claims.get(piece) is self:
                    del self.node._claims[piece]
                self.node._wake_all()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.connection.close()


class SwarmNode:
    """Own a transport and sessions, while the caller owns the PieceStore.

    Use serially on one asyncio loop. Completed nodes keep uploading until close.
    Store I/O is synchronous and never yields inside a read/write operation.
    """

    def __init__(self, store: PieceStore, *, transport: Optional[Transport] = None,
                 peer_id: Optional[bytes] = None, io_timeout: float = 30.0,
                 request_timeout: float = 15.0, max_piece_retries: int = 2):
        if store.closed:
            raise ValueError("store must be open")
        _timeout(io_timeout)
        _timeout(request_timeout)
        if type(max_piece_retries) is not int or max_piece_retries < 0:
            raise ValueError("max_piece_retries must be a nonnegative integer")
        self.store, self.peer_id = store, _identity(peer_id)
        self.transport = transport if transport is not None else TcpTransport()
        self.io_timeout, self.request_timeout = io_timeout, request_timeout
        self.max_piece_retries = max_piece_retries
        self.bytes_received = self.bytes_sent = self.hash_failures = 0
        self.errors = deque(maxlen=32)
        self._links = {}
        self._claims = {}
        self._tasks = set()
        self._accept_task = None
        self._close_task = None
        self._address = None
        self._complete = asyncio.Event()
        if store.complete:
            self._complete.set()

    @property
    def address(self):
        if self._address is None:
            raise RuntimeError("node is not listening")
        return self._address

    @property
    def peer_ids(self):
        return frozenset(self._links)

    def remote_pieces(self, peer_id):
        return frozenset(self._links[peer_id].available)

    def _check_open(self):
        if self._close_task is not None:
            raise RuntimeError("node is closed")

    async def start(self, address):
        self._check_open()
        if self._accept_task is not None:
            raise RuntimeError("node is already listening")
        await self.transport.listen(address)
        self._address = getattr(self.transport, "local_addr", None) or address
        self._accept_task = asyncio.create_task(self._accept())

    def _launch(self, connection, outgoing, ready=None):
        task = asyncio.create_task(self._run_link(_Link(self, connection, outgoing), ready))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _accept(self):
        while True:
            connection = await self.transport.accept()
            self._launch(connection, False)

    async def connect(self, address):
        self._check_open()
        connection = await asyncio.wait_for(self.transport.connect(address), self.io_timeout)
        if self._close_task is not None:
            await connection.close()
            raise RuntimeError("node is closed")
        ready = asyncio.get_running_loop().create_future()
        self._launch(connection, True, ready)
        try:
            return await ready
        except asyncio.CancelledError:
            await connection.close()
            raise

    async def _run_link(self, link, ready):
        reader = None
        try:
            link.available = await link.session.handshake()
            link.remote_id = link.session.remote_id
            old = self._links.get(link.remote_id)
            if old is not None:
                # Both ends choose the same physical connection if they dial
                # simultaneously: smaller ID's outgoing connection wins.
                prefer_outgoing = self.peer_id < link.remote_id
                if link.outgoing != prefer_outgoing or old.outgoing == prefer_outgoing:
                    if ready is not None and not ready.done():
                        ready.set_result(link.remote_id)
                    return
                await old.close()
            self._links[link.remote_id] = link
            # A piece can finish while the initial bitfield is in flight.
            for piece in sorted(self.store.owned_pieces - link.session.advertised_pieces):
                await link.send(Have(piece))
            reader = asyncio.create_task(link.receive_loop())
            link.worker = asyncio.create_task(link.download_loop())
            if ready is not None and not ready.done():
                ready.set_result(link.remote_id)
            done, _ = await asyncio.wait((reader, link.worker), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            if ready is not None and not ready.done():
                ready.cancel()
            raise
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            if ready is not None and not ready.done():
                ready.set_exception(exc)
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            await link.close()
            if self._links.get(link.remote_id) is link:
                del self._links[link.remote_id]
            self._wake_all()

    def _wake_all(self):
        for link in self._links.values():
            link.wake.set()

    async def _verified(self, piece):
        if self.store.complete:
            self._complete.set()
        # A failed neighbor must not stop delivery to the other neighbors.
        links = list(self._links.values())
        results = await asyncio.gather(*(link.send(Have(piece)) for link in links),
                                       return_exceptions=True)
        for link, result in zip(links, results):
            if isinstance(result, Exception):
                self.errors.append(f"HAVE send failed: {result}")
                await link.connection.close()
        self._wake_all()

    async def set_upload_allowed(self, peer_id, allowed):
        """Explicit upload switch for integration; timed ranking is still A2.1."""
        self._check_open()
        if type(allowed) is not bool:
            raise ValueError("allowed must be bool")
        link = self._links[peer_id]
        link.upload_allowed = allowed
        await link.upload_policy()

    async def wait_complete(self, timeout=30.0):
        _timeout(timeout)
        await asyncio.wait_for(self._complete.wait(), timeout)
        if not self.store.complete:
            raise RuntimeError("node closed before completing")

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self):
        if self._accept_task is not None:
            self._accept_task.cancel()
            await asyncio.gather(self._accept_task, return_exceptions=True)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.transport.close()
        self._address = None
        self._complete.set()
