"""Experimental Stage 1 endpoint, not yet the final swarm MurmurTransport."""

import asyncio
import ipaddress
import socket
from typing import Optional, Tuple

from murmurtp.connection import StopAndWaitConfig, StopAndWaitConnection
from murmurtp.header import ACK, RST, SYN, Header, PacketError, decode_packet, encode_packet
from murmurtp.lossy import LossConfig, LossyDatagramTransport


class StopAndWaitTransport(asyncio.DatagramProtocol):
    """One IPv4 UDP socket, with one session per remote address for its lifetime.

    Closed sessions retain their IDs and duplicate-FIN replies until endpoint
    close. Reconnect using a fresh endpoint; SYN v1 has no connection nonce.
    """

    def __init__(self, *, config: Optional[StopAndWaitConfig] = None,
                 loss: Optional[LossConfig] = None, seed: int = 0) -> None:
        self.config = config if config is not None else StopAndWaitConfig()
        self.loss = loss if loss is not None else LossConfig()
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        self.seed = seed
        self.local_addr = None
        self._udp = self._loop = self._socket_closed = None
        self._closed = self._listening = False
        self._bind_lock = asyncio.Lock()
        self._accept_queue = asyncio.Queue()
        self._peers = {}
        self._tasks = set()
        self._next_id = 1
        self.invalid_packets = self.socket_errors = 0

    @staticmethod
    def _address(addr: Tuple[str, int]) -> Tuple[str, int]:
        host, port = addr
        host = str(ipaddress.IPv4Address(host))
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer from 0 to 65535")
        return host, port

    def _check(self) -> None:
        if self._closed:
            raise RuntimeError("Transport is closed")

    async def _bind(self, addr: Tuple[str, int]) -> None:
        self._check()
        self._loop = asyncio.get_running_loop()
        self._socket_closed = self._loop.create_future()
        try:
            await self._loop.create_datagram_endpoint(lambda: self, local_addr=addr,
                                                       family=socket.AF_INET)
            self._check()
        except BaseException:
            if self._udp is not None:
                await self.close()
            raise

    def connection_made(self, transport) -> None:
        self._udp = LossyDatagramTransport(transport, self.loss, seed=self.seed)
        self.local_addr = self._udp.get_extra_info("sockname")
        if self._closed:
            self._udp.abort()

    def connection_lost(self, exc) -> None:
        if self._socket_closed is not None and not self._socket_closed.done():
            self._socket_closed.set_result(None)
        if not self._closed:
            self._closed = True
            for conn in self._peers.values():
                conn._abort(ConnectionAbortedError("UDP socket closed"), notify=False)
            self._accept_queue.put_nowait(None)

    def error_received(self, exc) -> None:
        # UDP errors are not reliably associated with a connection; retries and
        # inactivity deadlines decide session failure, not unrelated ICMP errors.
        self.socket_errors += 1

    async def listen(self, addr: Tuple[str, int]) -> None:
        addr = self._address(addr)
        async with self._bind_lock:
            self._check()
            if self._udp is not None:
                raise RuntimeError("Endpoint is already bound")
            await self._bind(addr)
            self._listening = True

    async def connect(self, addr: Tuple[str, int]) -> StopAndWaitConnection:
        addr = self._address(addr)
        if addr[1] == 0:
            raise ValueError("remote port must not be zero")
        async with self._bind_lock:
            self._check()
            if self._udp is None:
                await self._bind(("0.0.0.0", 0))
            if addr in self._peers:
                raise RuntimeError("Use a fresh endpoint to reconnect to this peer")
            conn = StopAndWaitConnection(self, addr, 0, client=True)
            self._peers[addr] = conn
        try:
            await conn._handshake()
            return conn
        except BaseException:
            conn._abort(ConnectionAbortedError("Connection setup interrupted"))
            raise

    async def accept(self) -> StopAndWaitConnection:
        self._check()
        if not self._listening:
            raise RuntimeError("Transport is not listening")
        while True:
            conn = await self._accept_queue.get()
            if conn is None:
                self._accept_queue.put_nowait(None)
            self._check()
            # A peer may finish sending and close before the application calls
            # accept(). Preserve its buffered data and EOF; skip only failures.
            if conn._error is None:
                return conn

    def _send(self, header: Header, payload: bytes, addr: Tuple[str, int]) -> None:
        if not self._closed:
            self._udp.sendto(encode_packet(header, payload), addr)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if self._closed:
            return
        try:
            header, payload = decode_packet(data)
        except PacketError:
            self.invalid_packets += 1
            return
        conn = self._peers.get(addr)
        if header.flags == SYN and header.conn_id == 0:
            if payload or header.seq_num != 0 or header.cum_ack != 0:
                return
            if conn is not None:
                if not conn.client and conn.state != "CLOSED":
                    conn._retx += 1
                    conn._send_packet(SYN | ACK, ack=1)
                else:
                    self._send(Header(RST), b"", addr)
            elif self._listening and self._next_id <= 65535:
                conn = StopAndWaitConnection(self, addr, self._next_id, client=False)
                self._next_id += 1
                self._peers[addr] = conn
                self._spawn(self._server_handshake(conn))
            else:
                self._send(Header(RST), b"", addr)
        elif conn is not None:
            if (header.conn_id == conn.conn_id or
                    (conn.client and conn.conn_id == 0 and header.flags == SYN | ACK)):
                conn._handle(header, payload)

    async def _server_handshake(self, conn: StopAndWaitConnection) -> None:
        try:
            await conn._handshake()
        except OSError as exc:
            conn._abort(exc)

    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        def finished(done):
            self._tasks.discard(done)
            if not done.cancelled():
                error = done.exception()  # Expected failures remain on the connection.
                if error is not None and not isinstance(error, OSError):
                    self._loop.call_exception_handler(
                        dict(message="Unexpected Stage 1 background failure", exception=error))
        task.add_done_callback(finished)
        return task

    async def close(self) -> None:
        self._closed = True
        self._accept_queue.put_nowait(None)
        for conn in self._peers.values():
            conn._abort(ConnectionAbortedError("Transport closed"), notify=False)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if self._udp is not None:
            # Endpoint shutdown aborts sessions. Discard raw UDP output as well;
            # a graceful raw close can wait indefinitely on a pending Windows
            # Proactor send. Individual connection.close() does the FIN exchange.
            self._udp.abort()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._socket_closed is not None and self._udp is not None:
            await asyncio.shield(self._socket_closed)
        self._peers.clear()
        while not self._accept_queue.empty():
            self._accept_queue.get_nowait()
        self._accept_queue.put_nowait(None)
        self.local_addr = None
