"""In-memory loopback transport implementing Contract 2.

Allows peers, tracker, and tests to run full end-to-end swarm communications
entirely in-process without network overhead or port collisions.
"""

import asyncio
from typing import Any, Dict, Optional, Tuple

from core.transport import Connection, Transport


class LoopbackRegistry:
    """Thread/task-safe in-memory address lookup registry for LoopbackTransport."""

    def __init__(self) -> None:
        self._listeners: Dict[Tuple[str, int], "LoopbackTransport"] = {}

    def register(self, addr: Tuple[str, int], transport: "LoopbackTransport") -> None:
        if addr in self._listeners:
            raise OSError(f"Address already in use: {addr}")
        self._listeners[addr] = transport

    def unregister(self, addr: Tuple[str, int]) -> None:
        self._listeners.pop(addr, None)

    def lookup(self, addr: Tuple[str, int]) -> Optional["LoopbackTransport"]:
        return self._listeners.get(addr)

    def clear(self) -> None:
        self._listeners.clear()


# Default process-wide registry
default_loopback_registry = LoopbackRegistry()


class LoopbackConnection(Connection):
    """Bidirectional byte-stream connection backed by asyncio queues."""

    def __init__(
        self,
        peer_addr: Tuple[str, int],
        local_addr: Tuple[str, int],
        tx_queue: asyncio.Queue,
        rx_queue: asyncio.Queue,
    ) -> None:
        super().__init__(peer_addr)
        self.local_addr = local_addr
        self._tx: asyncio.Queue = tx_queue
        self._rx: asyncio.Queue = rx_queue
        self._closed: bool = False
        self._eof: bool = False
        self._rx_buf: bytearray = bytearray()
        self._bytes_sent: int = 0
        self._bytes_received: int = 0

    async def send(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionResetError("Connection is closed")
        if not data:
            return
        await self._tx.put(data)
        self._bytes_sent += len(data)

    async def recv(self, max_bytes: int = 65536) -> bytes:
        if max_bytes <= 0:
            return b""

        # Return from buffer if available
        if self._rx_buf:
            chunk = bytes(self._rx_buf[:max_bytes])
            del self._rx_buf[:max_bytes]
            self._bytes_received += len(chunk)
            return chunk

        if self._eof:
            return b""

        item = await self._rx.get()
        if item is None:
            # EOF sentinel received from remote close
            self._eof = True
            return b""

        if len(item) <= max_bytes:
            self._bytes_received += len(item)
            return item

        # Buffer excess bytes
        chunk = item[:max_bytes]
        self._rx_buf.extend(item[max_bytes:])
        self._bytes_received += len(chunk)
        return chunk

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._tx.put(None)  # notify remote peer of EOF

    def stats(self) -> Dict[str, Any]:
        return {
            "proto": "loopback",
            "cwnd_bytes": None,
            "rtt_ms": 0.0,
            "retx": 0,
            "bytes_in_flight": self._tx.qsize(),
            "bytes_sent": self._bytes_sent,
            "bytes_received": self._bytes_received,
        }


class LoopbackTransport(Transport):
    """In-memory Transport enabling connections between simulated network endpoints."""

    def __init__(self, registry: Optional[LoopbackRegistry] = None) -> None:
        self.registry = registry or default_loopback_registry
        self.local_addr: Optional[Tuple[str, int]] = None
        self._accept_queue: Optional[asyncio.Queue[LoopbackConnection]] = None
        self._is_listening: bool = False

    async def listen(self, addr: Tuple[str, int]) -> None:
        if self._is_listening:
            raise RuntimeError("Transport is already listening")
        self.local_addr = addr
        self._accept_queue = asyncio.Queue()
        self.registry.register(addr, self)
        self._is_listening = True

    async def accept(self) -> Connection:
        if not self._is_listening or self._accept_queue is None:
            raise RuntimeError("Transport is not listening")
        return await self._accept_queue.get()

    async def connect(self, addr: Tuple[str, int]) -> Connection:
        target = self.registry.lookup(addr)
        if target is None or not target._is_listening or target._accept_queue is None:
            raise ConnectionRefusedError(f"Connection refused to {addr}")

        # Local ephemeral address if not explicitly listening
        client_addr = self.local_addr or ("127.0.0.1", id(self) % 65535)

        client_to_server: asyncio.Queue = asyncio.Queue()
        server_to_client: asyncio.Queue = asyncio.Queue()

        client_conn = LoopbackConnection(
            peer_addr=addr,
            local_addr=client_addr,
            tx_queue=client_to_server,
            rx_queue=server_to_client,
        )

        server_conn = LoopbackConnection(
            peer_addr=client_addr,
            local_addr=addr,
            tx_queue=server_to_client,
            rx_queue=client_to_server,
        )

        await target._accept_queue.put(server_conn)
        return client_conn

    async def close(self) -> None:
        if self._is_listening and self.local_addr:
            self.registry.unregister(self.local_addr)
            self._is_listening = False
            self.local_addr = None
            self._accept_queue = None
