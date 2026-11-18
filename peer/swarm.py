"""Bidirectional peers with rarest-first selection, choking, and pipelining."""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Optional

from core.tcp import TcpTransport
from core.asyncio_utils import wait_for_cancel_safe
from core.transport import Transport
from peer.choking import ChokingPolicy, PeerRates
from peer.piece_selection import RarestFirstSelector, count_availability
from peer.node import PeerProtocolError, _Session, _identity, _timeout
from peer.storage import PieceHashError, PieceStore
from peer.telemetry import PeerEvents
from peer.wire import Choke, Have, Interested, NotInterested, Piece, Request, Unchoke, read_message


@dataclass
class _Pending:
    future: asyncio.Future
    deadline: float


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
        self.pending = {}
        self.peak_pending = 0
        self.retired = set()
        self.worker = None
        self.keepalive = None
        self._last_sent = asyncio.get_running_loop().time()
        self.closed = False
        self.failures = {}
        self.downloaded = self.uploaded = 0
        self.down_bps = self.up_bps = 0.0
        self._rate_down = self._rate_up = 0
        self._rate_at = asyncio.get_running_loop().time()

    async def send(self, message):
        async with self.send_lock:
            await self.session.send(message)
            self._last_sent = asyncio.get_running_loop().time()
            if isinstance(message, (Choke, Unchoke)):
                self.node._emit("choke" if isinstance(message, Choke) else "unchoke",
                                peer=str(self.connection.peer_addr[0]),
                                peer_port=self.connection.peer_addr[1],
                                peer_id=self.remote_id.hex() if self.remote_id else None)

    async def upload_policy(self):
        await self.node._refresh_uploads(
            force_choke=self if not self.node.uploads_enabled and self.peer_interested else None)

    async def keepalive_loop(self):
        # Reannounce existing interest state: no new wire message or payload
        # accounting. A choked peer may wait longer than the transport idle
        # deadline for an optimistic slot. Reliable sends still fail if its
        # neighbor stops acknowledging, so dead connections cannot live forever.
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.node.keepalive_interval)
            async with self.send_lock:
                if loop.time() - self._last_sent >= self.node.keepalive_interval:
                    await self.session.send(Interested() if self.am_interested else NotInterested())
                    self._last_sent = loop.time()

    async def receive_loop(self):
        while True:
            # Choked peers may legitimately wait beyond io_timeout for a slot.
            # Handshakes/sends remain bounded; downloads have per-request deadlines.
            message = await read_message(self.connection, num_pieces=self.node.store.metadata.num_pieces)
            if message is None:
                return
            if isinstance(message, Have):
                self.available.add(message.piece)
            elif isinstance(message, (Choke, Unchoke)):
                self.peer_choking = isinstance(message, Choke)
                if self.peer_choking:
                    for key, pending in self.pending.items():
                        if not pending.future.done():
                            self.retired.add(key)
                            pending.future.set_result(None)
                    if len(self.retired) > 64:
                        raise PeerProtocolError("too many abandoned requests")
            elif isinstance(message, (Interested, NotInterested)):
                self.peer_interested = isinstance(message, Interested)
                await self.upload_policy()
            elif isinstance(message, Request):
                if not self.node.uploads_enabled:
                    await self.send(Choke())
                    continue  # A completed client-server leecher still never serves data.
                if not self.peer_interested:
                    raise PeerProtocolError("REQUEST while choked/uninterested")
                if self.am_choking:
                    # This may have been sent before our CHOKE reached the peer.
                    continue
                data = self.node.store.read_block(message.piece, message.offset, message.length)
                await self.send(Piece(message.piece, message.offset, data))
                self.node.bytes_sent += len(data)
                self.uploaded += len(data)
            elif isinstance(message, Piece):
                key = (message.piece, message.offset, len(message.data))
                pending = self.pending.get(key)
                if key in self.retired and (pending is None or
                        (pending.future.done() and pending.future.result() is None)):
                    self.retired.remove(key)
                    continue  # Late response to a request abandoned on CHOKE.
                if pending is None or pending.future.done():
                    raise PeerProtocolError("PIECE does not match outstanding REQUEST")
                self.retired.discard(key)
                pending.future.set_result(message.data)
            else:
                raise PeerProtocolError("unexpected message after initial BITFIELD")
            self.wake.set()

    async def download_loop(self):
        store = self.node.store
        loop = asyncio.get_running_loop()
        try:
            while True:
                self.wake.clear()
                for key, pending in list(self.pending.items()):
                    if not pending.future.done():
                        if loop.time() >= pending.deadline:
                            raise asyncio.TimeoutError("block request deadline expired")
                        continue
                    del self.pending[key]
                    data = pending.future.result()
                    if data is None:  # Abandoned by CHOKE; a late response is ignored.
                        continue
                    piece, offset, _ = key
                    self.node.bytes_received += len(data)
                    self.downloaded += len(data)
                    try:
                        if store.write_block(piece, offset, data):
                            del self.node._claims[piece]
                            await self.node._verified(piece)
                    except PieceHashError:
                        self.node.hash_failures += 1
                        self.node._emit("hash_fail", piece=piece)
                        self.failures[piece] = self.failures.get(piece, 0) + 1
                        self.node._claims.pop(piece, None)
                        self.node._wake_all()
                        if self.failures[piece] > self.node.max_piece_retries:
                            raise
                if self.peer_choking:
                    self._release_claims()
                interested = bool(self.available - store.owned_pieces)
                if interested != self.am_interested:
                    self.am_interested = interested
                    await self.send(Interested() if interested else NotInterested())
                # HAVE may arrive while NOT_INTERESTED is awaiting transport
                # backpressure. Announce INTERESTED on the next pass before
                # requesting newly available data.
                while (self.am_interested and not self.peer_choking
                       and len(self.pending) < self.node.pipelining_depth):
                    key = self._next_request()
                    if key is None:
                        break
                    self.pending[key] = _Pending(loop.create_future(), loop.time() + self.node.request_timeout)
                    self.peak_pending = max(self.peak_pending, len(self.pending))
                    await self.send(Request(*key))
                if any(p.future.done() for p in self.pending.values()):
                    continue
                if self.pending:
                    remaining = min(p.deadline for p in self.pending.values()) - loop.time()
                    await wait_for_cancel_safe(self.wake.wait(), max(0, remaining))
                else:
                    await self.wake.wait()
        finally:
            for pending in self.pending.values():
                pending.future.cancel()
            self.pending.clear()
            self._release_claims()

    def _release_claims(self):
        released = [piece for piece, owner in self.node._claims.items() if owner is self]
        for piece in released:
            del self.node._claims[piece]
        if released:
            self.node._wake_all()

    def _next_request(self):
        store = self.node.store
        for piece, owner in self.node._claims.items():
            if owner is self:
                for offset, length in store.missing_blocks(piece):
                    key = (piece, offset, length)
                    if key not in self.pending:
                        return key
        availability = count_availability(store.metadata.num_pieces,
                                          (link.available for link in self.node._links.values()))
        piece = self.node._selector.select(
            self.available, store.owned_pieces, availability,
            started_pieces=store.started_pieces, excluded_pieces=self.node._claims)
        if piece is None:
            return None
        self.node._claims[piece] = self
        self.node._emit("piece_select", piece=piece, availability=availability[piece],
                        started=piece in store.started_pieces, peer=self.connection.peer_addr[0])
        offset, length = store.missing_blocks(piece)[0]
        return piece, offset, length

    async def close(self):
        if self.closed:
            return
        self.closed = True
        tasks = [task for task in (self.worker, self.keepalive) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.connection.close()


class SwarmNode:
    """Own a transport and sessions, while the caller owns the PieceStore.

    Use serially on one asyncio loop. Swarm peers keep uploading after completion;
    client-server leechers retain their non-uploading role until close.
    Store I/O is synchronous and never yields inside a read/write operation.
    """

    def __init__(self, store: PieceStore, *, transport: Optional[Transport] = None,
                 peer_id: Optional[bytes] = None, io_timeout: float = 30.0,
                 request_timeout: float = 15.0, max_piece_retries: int = 2,
                 mode: str = "swarm", is_seed: bool = False, seed_address=None,
                 event_log=None, sample_interval: float = 1.0, pipelining_depth: int = 16,
                 unchoke_slots: int = 4, choke_interval: float = 10.0,
                 optimistic_unchoke_interval: float = 30.0, random_seed: int = 0,
                 keepalive_interval: float = 3.0):
        if store.closed:
            raise ValueError("store must be open")
        if mode not in ("swarm", "client_server"):
            raise ValueError("mode must be swarm or client_server")
        if type(is_seed) is not bool:
            raise ValueError("is_seed must be bool")
        if is_seed and not store.complete:
            raise ValueError("the origin seed must have every piece verified")
        if mode == "client_server" and not is_seed:
            from ipaddress import IPv4Address

            if (not isinstance(seed_address, tuple) or len(seed_address) != 2
                    or not isinstance(seed_address[0], str)
                    or type(seed_address[1]) is not int or not 1 <= seed_address[1] <= 65535):
                raise ValueError("client-server leechers require seed_address=(IPv4, port)")
            seed_address = (str(IPv4Address(seed_address[0])), seed_address[1])
        elif seed_address is not None:
            raise ValueError("seed_address is only for a client-server leecher")
        self._mode, self._is_seed, self._seed_address = mode, is_seed, seed_address
        _timeout(io_timeout)
        _timeout(request_timeout)
        _timeout(sample_interval)
        _timeout(keepalive_interval)
        self.keepalive_interval = keepalive_interval
        if type(max_piece_retries) is not int or max_piece_retries < 0:
            raise ValueError("max_piece_retries must be a nonnegative integer")
        if type(pipelining_depth) is not int or not 1 <= pipelining_depth <= 32:
            raise ValueError("pipelining_depth must be an integer in [1, 32]")
        self.pipelining_depth = pipelining_depth
        self._selector = RarestFirstSelector(store.metadata.num_pieces, seed=random_seed)
        self._policy = ChokingPolicy(unchoke_slots=unchoke_slots, choke_interval=choke_interval,
                                     optimistic_unchoke_interval=optimistic_unchoke_interval,
                                     seed=random_seed)
        self._policy_lock = asyncio.Lock()
        self._policy_task = None
        self._policy_error = None
        self._last_rate_sample = None
        self._policy_seed = store.complete
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
        self._events = PeerEvents(self, event_log, interval=sample_interval) if event_log is not None else None

    def _emit(self, kind, **fields):
        if self._events is not None:
            self._events.emit(kind, **fields)

    @property
    def mode(self):
        return self._mode

    @property
    def is_seed(self):
        return self._is_seed

    @property
    def uploads_enabled(self):
        return self.mode == "swarm" or self.is_seed

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
        self._ensure_policy_task()
        if self._events is not None:
            self._events.start()
            self._events.raise_if_failed()

    def _launch(self, connection, outgoing, ready=None):
        task = asyncio.create_task(self._run_link(_Link(self, connection, outgoing), ready))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _accept(self):
        while True:
            connection = await self.transport.accept()
            if self.mode == "client_server" and not self.is_seed:
                await connection.close()  # Only our outbound connection to the seed is allowed.
                continue
            self._launch(connection, False)

    async def connect(self, address):
        self._check_open()
        if self.mode == "client_server":
            if self.is_seed:
                raise ValueError("client-server origin seed accepts connections; it does not dial peers")
            if address != self._seed_address:
                raise ValueError("client-server leechers may connect only to their origin seed")
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
            if self._events is not None:
                self._events.opened(link.connection)
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
            self._ensure_policy_task()
            # A piece can finish while the initial bitfield is in flight.
            for piece in sorted(self.store.owned_pieces - link.session.advertised_pieces):
                await link.send(Have(piece))
            reader = asyncio.create_task(link.receive_loop())
            link.worker = asyncio.create_task(link.download_loop())
            link.keepalive = asyncio.create_task(link.keepalive_loop())
            if ready is not None and not ready.done():
                ready.set_result(link.remote_id)
            done, _ = await asyncio.wait((reader, link.worker, link.keepalive),
                                         return_when=asyncio.FIRST_COMPLETED)
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
            if self._events is not None:
                self._events.closed(link.connection)
            if self._links.get(link.remote_id) is link:
                del self._links[link.remote_id]
                if self._close_task is None:
                    await self._refresh_uploads()
            self._wake_all()

    def _wake_all(self):
        for link in self._links.values():
            link.wake.set()

    def _ensure_policy_task(self):
        if self._policy_task is None:
            self._policy_task = asyncio.create_task(self._policy_loop())

    async def _policy_loop(self):
        try:
            while True:
                await self._refresh_uploads()
                await asyncio.sleep(min(1.0, self._policy.choke_interval,
                                        self._policy.optimistic_unchoke_interval))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._policy_error = exc
            self.errors.append(f"choking policy failed: {exc}")
            self._complete.set()

    async def _refresh_uploads(self, force_choke=None):
        async with self._policy_lock:
            now = asyncio.get_running_loop().time()
            links = list(self._links.values())
            seed = self.store.complete
            if (self._last_rate_sample is None or seed != self._policy_seed
                    or now - self._last_rate_sample >= self._policy.choke_interval):
                for link in links:
                    elapsed = now - link._rate_at
                    if elapsed > 0:
                        link.down_bps = (link.downloaded - link._rate_down) * 8 / elapsed
                        link.up_bps = (link.uploaded - link._rate_up) * 8 / elapsed
                    link._rate_at, link._rate_down, link._rate_up = now, link.downloaded, link.uploaded
                self._last_rate_sample, self._policy_seed = now, seed
            decision = self._policy.update(
                [PeerRates(link.remote_id.hex(), link.peer_interested and link.upload_allowed,
                           link.down_bps, link.up_bps) for link in links],
                now, is_seed=seed, uploads_enabled=self.uploads_enabled)
            # Apply CHOKEs before UNCHOKEs, preserving the slot bound on wire.
            for choke in (True, False):
                for link in links:
                    desired = link.remote_id.hex() not in decision.unchoked
                    if desired != choke or self._links.get(link.remote_id) is not link or link.closed:
                        continue
                    if link.am_choking == desired and not (desired and link is force_choke):
                        continue
                    link.am_choking = desired
                    try:
                        await link.send(Choke() if desired else Unchoke())
                    except (OSError, asyncio.TimeoutError) as exc:
                        self.errors.append(f"choking message failed: {exc}")
                        await link.connection.close()

    async def _verified(self, piece):
        self._emit("have", piece=piece, initial=False)
        if self.store.complete:
            await self._refresh_uploads()
            if self._events is not None:
                self._events.complete()
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
        """Gate one peer's eligibility for the timed upload policy."""
        self._check_open()
        if type(allowed) is not bool:
            raise ValueError("allowed must be bool")
        if allowed and not self.uploads_enabled:
            raise ValueError("client-server leechers cannot enable uploads")
        link = self._links[peer_id]
        link.upload_allowed = allowed
        await link.upload_policy()

    async def wait_complete(self, timeout=30.0):
        _timeout(timeout)
        await asyncio.wait_for(self._complete.wait(), timeout)
        if self._policy_error is not None:
            raise self._policy_error
        if self._events is not None:
            self._events.raise_if_failed()
        if not self.store.complete:
            raise RuntimeError("node closed before completing")

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self):
        if self._policy_task is not None:
            self._policy_task.cancel()
            await asyncio.gather(self._policy_task, return_exceptions=True)
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
        if self._events is not None:
            await self._events.finish()
