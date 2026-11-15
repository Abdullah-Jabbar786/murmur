"""Contract 2: Transport and Connection abstract interfaces.

All network implementations (LoopbackTransport, TcpTransport, MurmurTransport)
implement these interfaces. The swarm logic (peer/node.py) only interacts
with this abstraction and never directly with raw sockets.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple


class Connection(ABC):
    """Represents an active, reliable, bidirectional byte-stream connection."""

    def __init__(self, peer_addr: Tuple[str, int]) -> None:
        self.peer_addr = peer_addr

    @abstractmethod
    async def send(self, data: bytes) -> None:
        """Send data over the connection.

        Returns once data is accepted into the send buffer.
        Backpressures (awaits) if the send buffer is full.
        """
        pass

    @abstractmethod
    async def recv(self, max_bytes: int = 65536) -> bytes:
        """Receive up to max_bytes from the connection.

        Returns b"" when the remote peer has closed the connection.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Gracefully close the connection."""
        pass

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """Return synchronous snapshot of connection metrics.

        Standard keys:
            - proto: str ("loopback", "tcp", "murmurtp")
            - cwnd_bytes: Optional[int]
            - rtt_ms: Optional[float]
            - retx: Optional[int]
            - bytes_in_flight: Optional[int]
        """
        pass


class Transport(ABC):
    """Represents a transport service that can listen for and establish connections."""

    @abstractmethod
    async def listen(self, addr: Tuple[str, int]) -> None:
        """Bind and listen for incoming connections on addr (ip, port)."""
        pass

    @abstractmethod
    async def accept(self) -> Connection:
        """Accept the next incoming Connection."""
        pass

    @abstractmethod
    async def connect(self, addr: Tuple[str, int]) -> Connection:
        """Establish an outbound Connection to addr (ip, port)."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Stop listening and release all associated resources."""
        pass
