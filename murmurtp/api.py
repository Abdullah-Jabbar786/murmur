"""Stage 7 public Contract 2 transport, combining reliable UDP Stages 1..6."""

import asyncio
import socket
import sys

from core.transport import Connection, Transport
from murmurtp.flow import FlowControlConfig, FlowControlConnection, FlowControlTransport


class MurmurConnection(FlowControlConnection, Connection):
    """Ordered stream with bounded receive storage and ACK-backed send backpressure."""

    def stats(self):
        result = super().stats()
        result["stage"] = 7
        return result

    async def close(self):
        # Like TcpConnection.close(), cleanup remains idempotent after reset.
        # send()/recv() continue to expose delivery errors to their callers.
        try:
            await super().close()
        except OSError:
            if self._close_task is not None:
                await asyncio.gather(self._close_task, return_exceptions=True)


class MurmurTransport(FlowControlTransport, Transport):
    """One IPv4 UDP socket per endpoint; config is FlowControlConfig."""

    connection_type = MurmurConnection

    def connection_made(self, transport):
        if sys.platform == "win32":
            # Several peers send bursts to this one UDP socket. The Windows
            # default (64 KiB here) can overflow before asyncio drains it.
            sock = transport.get_extra_info("socket")
            try:
                if sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) < 1024 * 1024:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            except OSError:
                pass  # Keep the OS buffer if socket tuning is unavailable.
        super().connection_made(transport)
