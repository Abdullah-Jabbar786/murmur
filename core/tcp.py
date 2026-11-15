"""IPv4 TCP implementation of Contract 2 (Python 3.10+).

Linux TCP_INFO uses native byte order, unlike the swarm wire protocol.
Other platforms keep the same stats keys, with unavailable metrics set to None.
"""

import asyncio
import socket
import struct
import sys
from typing import Any, Callable, Dict, Optional, Set, Tuple

from core.transport import Connection, Transport


def _tcp_metrics(sock: Any) -> Dict[str, Any]:
    metrics = dict(cwnd_bytes=None, rtt_ms=None, retx=None, bytes_in_flight=None)
    if sock is None or sys.platform != "linux" or not hasattr(socket, "TCP_INFO"):
        return metrics
    try:
        # Stable 104-byte prefix of Linux UAPI struct tcp_info:
        # 8 bytes of flags/state, then 24 native-endian unsigned 32-bit fields.
        raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
        if len(raw) < 104:
            return metrics
        fields = struct.unpack_from("=24I", raw, 8)
        mss = fields[2]
        in_flight = max(0, fields[4] - fields[5] - fields[6] + fields[7])
        metrics.update(
            cwnd_bytes=fields[18] * mss,
            rtt_ms=fields[15] / 1000.0,
            retx=fields[23],
            # Segment-count estimate; short segments make this approximate.
            bytes_in_flight=in_flight * mss,
        )
    except OSError:
        pass  # Closed socket or kernel without TCP_INFO support.
    return metrics


class TcpConnection(Connection):
    """An ordered byte stream; sends do not create message boundaries."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 on_close: Callable[["TcpConnection"], None], close_timeout: float) -> None:
        super().__init__(writer.get_extra_info("peername")[:2])
        self.local_addr = writer.get_extra_info("sockname")[:2]
        self._reader, self._writer = reader, writer
        self._on_close, self._close_timeout = on_close, close_timeout
        self._send_lock = asyncio.Lock()
        self._close_task: Optional[asyncio.Task] = None
        self._bytes_sent = self._bytes_received = 0
        self._last_metrics = _tcp_metrics(writer.get_extra_info("socket"))

    async def send(self, data: bytes) -> None:
        async with self._send_lock:
            if self._writer.is_closing():
                raise ConnectionResetError("Connection is closed")
            # Bound each write and drain it before adding more to the buffer.
            view = memoryview(data)
            for offset in range(0, len(view), 65536):
                chunk = view[offset:offset + 65536]
                self._writer.write(chunk)
                self._bytes_sent += len(chunk)
                await self._writer.drain()

    async def recv(self, max_bytes: int = 65536) -> bytes:
        if max_bytes <= 0:
            return b""
        data = await self._reader.read(max_bytes)
        self._bytes_received += len(data)
        return data

    def stats(self) -> Dict[str, Any]:
        if not self._writer.is_closing():
            self._last_metrics = _tcp_metrics(self._writer.get_extra_info("socket"))
        return dict(self._last_metrics, proto="tcp", bytes_sent=self._bytes_sent,
                    bytes_received=self._bytes_received)

    async def close(self) -> None:
        if self._close_task is None:
            self.stats()  # Keep a final snapshot available after close.
            self._writer.close()
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        try:
            await asyncio.wait_for(self._writer.wait_closed(), self._close_timeout)
        except (OSError, asyncio.TimeoutError):
            self._writer.transport.abort()
        finally:
            self._on_close(self)


class TcpTransport(Transport):
    """Owns its listener and all accepted/outgoing connections.

    congestion_control=None preserves the OS default. Explicit algorithms such
    as 'reno' or 'cubic' require Linux TCP_CONGESTION; errors are never hidden.
    close() is terminal and wakes pending accept() calls with RuntimeError.
    """

    def __init__(self, congestion_control: Optional[str] = None,
                 close_timeout: float = 5.0) -> None:
        if close_timeout <= 0:
            raise ValueError("close_timeout must be positive")
        if congestion_control is not None:
            if not congestion_control or "\0" in congestion_control:
                raise ValueError("congestion_control must be a nonempty algorithm name")
            congestion_control.encode("ascii")
        self.congestion_control = congestion_control
        self.local_addr: Optional[Tuple[str, int]] = None
        self._close_timeout = close_timeout
        self._server: Optional[asyncio.AbstractServer] = None
        self._connections: Set[TcpConnection] = set()
        self._pending: Set[asyncio.Task] = set()
        self._accept_queue: asyncio.Queue = asyncio.Queue()
        self._listen_lock = asyncio.Lock()
        self._closed = False
        self._close_task: Optional[asyncio.Task] = None

    def _configure(self, sock: Any) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.congestion_control is not None:
            if sys.platform != "linux" or not hasattr(socket, "TCP_CONGESTION"):
                raise NotImplementedError("Explicit TCP congestion control requires Linux")
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION,
                            self.congestion_control.encode("ascii"))

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Transport is closed")

    def _wrap(self, reader: asyncio.StreamReader,
              writer: asyncio.StreamWriter) -> TcpConnection:
        conn = TcpConnection(reader, writer, self._connections.discard, self._close_timeout)
        self._connections.add(conn)
        return conn

    def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed:
            writer.transport.abort()
            return
        # Accepted sockets inherit the listener's congestion-control algorithm.
        self._accept_queue.put_nowait(self._wrap(reader, writer))

    async def listen(self, addr: Tuple[str, int]) -> None:
        await self._setup(self._listen(addr))

    async def _setup(self, operation: Any) -> Any:
        task = asyncio.create_task(operation)
        self._pending.add(task)
        try:
            return await task
        finally:
            self._pending.discard(task)

    async def _listen(self, addr: Tuple[str, int]) -> None:
        async with self._listen_lock:
            self._check_open()
            if self._server is not None:
                raise RuntimeError("Transport is already listening")
            server = await asyncio.start_server(self._on_client, *addr,
                                                family=socket.AF_INET, start_serving=False)
            try:
                self._check_open()
                for sock in server.sockets:
                    self._configure(sock)
                self._server = server
                self.local_addr = server.sockets[0].getsockname()[:2]
                await server.start_serving()
                self._check_open()
            except BaseException:
                server.close()
                await server.wait_closed()
                self._server = None
                self.local_addr = None
                raise

    async def accept(self) -> Connection:
        self._check_open()
        if self._server is None:
            raise RuntimeError("Transport is not listening")
        conn = await self._accept_queue.get()
        if conn is None:
            self._accept_queue.put_nowait(None)  # Wake every waiting acceptor.
        self._check_open()
        return conn

    async def connect(self, addr: Tuple[str, int]) -> Connection:
        return await self._setup(self._connect(addr))

    async def _connect(self, addr: Tuple[str, int]) -> Connection:
        self._check_open()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            self._configure(sock)  # Set the algorithm before the handshake.
            await asyncio.get_running_loop().sock_connect(sock, addr)
            reader, writer = await asyncio.open_connection(sock=sock)
        except BaseException:
            sock.close()
            raise
        conn = self._wrap(reader, writer)
        if self._closed:
            await conn.close()
            self._check_open()
        return conn

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._accept_queue.put_nowait(None)
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self) -> None:
        server = self._server
        if server is not None:
            server.close()
        pending = list(self._pending)
        for task in pending:
            task.cancel()
        # Closing connections also releases a cancelled listen() which may be
        # waiting for its server to finish closing on newer Python versions.
        closing = [asyncio.create_task(conn.close()) for conn in list(self._connections)]
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*closing)
        if server is not None:
            await server.wait_closed()
        self._server = None
        self.local_addr = None
        while not self._accept_queue.empty():
            self._accept_queue.get_nowait()
        self._accept_queue.put_nowait(None)
