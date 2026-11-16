"""Stage 1 only: bidirectional stop-and-wait, retries, and graceful FIN exchange."""

import asyncio
from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Optional, Tuple

from murmurtp.header import ACK, DATA, FIN, MSS, RST, SYN, Header

if TYPE_CHECKING:
    from murmurtp.transport import StopAndWaitTransport


@dataclass(frozen=True)
class StopAndWaitConfig:
    initial_rto: float = 1.0
    max_rto: float = 5.0
    inactivity_timeout: float = 30.0
    syn_retries: int = 5
    receive_buffer: int = 64 * MSS

    def __post_init__(self) -> None:
        for name in ("initial_rto", "max_rto", "inactivity_timeout"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
        if self.max_rto < self.initial_rto:
            raise ValueError("max_rto must be at least initial_rto")
        if (type(self.syn_retries) is not int or not 0 <= self.syn_retries <= 5):
            raise ValueError("syn_retries must be an integer from 0 to 5")
        if type(self.receive_buffer) is not int or self.receive_buffer < MSS:
            raise ValueError("receive_buffer must hold at least one MSS")


class StopAndWaitConnection:
    def __init__(self, owner: "StopAndWaitTransport", peer_addr: Tuple[str, int],
                 conn_id: int, *, client: bool) -> None:
        self.owner, self.peer_addr, self.conn_id = owner, peer_addr, conn_id
        self.client, self.config = client, owner.config
        self.state = "SYN_SENT" if client else "SYN_RCVD"
        self._tx_seq = self._rx_seq = 0
        self._rx = bytearray()
        self._rx_fin = self._closing = False
        self._error: Optional[Exception] = None
        self._established = asyncio.Event()
        self._ack_ready, self._read_ready = asyncio.Event(), asyncio.Event()
        self._fin_ready, self._aborted = asyncio.Event(), asyncio.Event()
        self._waiting_ack: Optional[int] = None
        self._acked = False
        self._send_lock, self._recv_lock = asyncio.Lock(), asyncio.Lock()
        self._close_task: Optional[asyncio.Task] = None
        self._idle = None
        self._rto = self.config.initial_rto
        self._retx = self._bytes_sent = self._bytes_received = self._inflight = 0
        self._touch()

    def _touch(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        self._idle = self.owner._loop.call_later(
            self.config.inactivity_timeout, self._abort,
            TimeoutError("No MurmurTP progress before inactivity timeout"))

    def _check(self) -> None:
        if self._error is not None:
            raise self._error

    def _send_packet(self, flags: int, *, seq: int = 0,
                     ack: Optional[int] = None, payload: bytes = b"") -> None:
        header = Header(flags, self.conn_id, seq,
                        self._rx_seq if ack is None else ack,
                        min(65535, (self.config.receive_buffer - len(self._rx)) // MSS),
                        timestamp=int(self.owner._loop.time() * 1000) & 0xFFFFFFFF)
        self.owner._send(header, payload, self.peer_addr)

    async def _handshake(self) -> None:
        rto = self.config.initial_rto
        for attempt in range(self.config.syn_retries + 1):
            self._check()
            if self._established.is_set():
                return
            if attempt:
                self._retx += 1
            self._send_packet(SYN if self.client else SYN | ACK, ack=0 if self.client else 1)
            try:
                await asyncio.wait_for(self._established.wait(), rto)
            except asyncio.TimeoutError:
                rto = min(2 * rto, self.config.max_rto)
            else:
                self._check()
                return
        error = ConnectionRefusedError("MurmurTP handshake retries exhausted")
        self._abort(error)
        raise error

    def _establish(self) -> None:
        if not self._established.is_set():
            self.state = "ESTABLISHED"
            self._established.set()
            self._touch()
            if not self.client:
                self.owner._accept_queue.put_nowait(self)

    def _handle(self, header: Header, payload: bytes) -> None:
        if self.state == "CLOSED":
            # Retain only the duplicate-FIN response after TIME_WAIT. A peer
            # whose final ACK was lost must still be able to finish closing.
            if self._error is None and self._rx_fin and header.flags == FIN and not payload:
                if header.seq_num == self._rx_seq - 1:
                    self._send_packet(ACK)
            return
        if header.flags != DATA and payload:
            self._abort(ConnectionResetError("Payload on a control packet"))
            return
        if header.flags == RST:
            self._abort(ConnectionResetError("Peer reset MurmurTP connection"), notify=False)
            return
        if header.flags == SYN | ACK and self.client:
            if header.seq_num == 0 and header.cum_ack == 1 and header.conn_id:
                if self.conn_id in (0, header.conn_id):
                    self.conn_id = header.conn_id
                    self._send_packet(ACK, seq=1, ack=1)
                    self._establish()
            return
        # Handshake ACK is seq=1, ack=1. DATA/FIN ACKs always have seq=0.
        # Never let a delayed handshake ACK acknowledge DATA packet zero.
        if header.flags == ACK and header.seq_num == 1 and header.cum_ack == 1:
            if not self.client and self.state == "SYN_RCVD":
                self._establish()
            return
        if self.state == "SYN_RCVD":
            self._retx += 1
            self._send_packet(SYN | ACK, ack=1)
            return
        if self.state == "SYN_SENT":
            return
        if header.flags == ACK and header.seq_num == 0:
            if header.cum_ack == self._waiting_ack and not self._acked:
                self._acked = True
                self._ack_ready.set()
                self._touch()
        elif header.flags in (DATA, FIN):
            if not self._rx_fin and header.seq_num == self._rx_seq:
                if header.flags == DATA:
                    if not payload or len(self._rx) + len(payload) > self.config.receive_buffer:
                        self._send_packet(ACK)  # Retry after the application drains the buffer.
                        return
                    self._rx.extend(payload)
                else:
                    self._rx_fin = True
                    self._fin_ready.set()
                self._rx_seq += 1
                self._read_ready.set()
                self._touch()
            self._send_packet(ACK)  # ACK duplicates; never deliver them twice.
            if self._rx_fin and self._close_task is None:
                self._start_close()
        else:
            self._abort(ConnectionResetError("Unexpected MurmurTP flags"))

    async def _exchange(self, flags: int, payload: bytes = b"") -> None:
        if self._tx_seq >= 0xFFFFFFFF:
            raise OverflowError("Stage 1 does not wrap packet sequence numbers")
        self._waiting_ack, self._acked = self._tx_seq + 1, False
        self._ack_ready.clear()
        self._inflight = len(payload)
        rto = self.config.initial_rto
        try:
            while True:
                self._check()
                self._rto = rto
                self._send_packet(flags, seq=self._tx_seq, payload=payload)
                try:
                    await asyncio.wait_for(self._ack_ready.wait(), rto)
                except asyncio.TimeoutError:
                    if not self._acked:
                        self._retx += 1
                        rto = min(2 * rto, self.config.max_rto)
                        continue
                self._check()
                if self._acked:
                    self._tx_seq += 1
                    return
        finally:
            self._waiting_ack, self._inflight = None, 0

    async def send(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        async with self._send_lock:
            self._check()
            if self._closing or self.state != "ESTABLISHED":
                raise ConnectionError("Connection is not open for sending")
            try:
                for offset in range(0, len(data), MSS):
                    chunk = data[offset:offset + MSS]
                    self._bytes_sent += len(chunk)
                    await self._exchange(DATA, chunk)
            except (asyncio.CancelledError, OSError, OverflowError):
                # A cancelled send may already have delivered a prefix. Abort
                # rather than reusing its sequence number for unrelated bytes.
                self._abort(ConnectionAbortedError("Send interrupted"))
                raise

    async def recv(self, max_bytes: int = 65536) -> bytes:
        if max_bytes <= 0:
            return b""
        async with self._recv_lock:
            while not self._rx:
                self._check()
                if self._rx_fin:
                    return b""
                self._read_ready.clear()
                await self._read_ready.wait()
            data = bytes(self._rx[:max_bytes])
            del self._rx[:max_bytes]
            self._bytes_received += len(data)
            return data

    def _start_close(self) -> None:
        self._closing = True
        self._close_task = self.owner._spawn(self._finish_close())

    async def close(self) -> None:
        self._check()
        if self._close_task is None:
            self._start_close()
        await asyncio.shield(self._close_task)
        self._check()

    async def _finish_close(self) -> None:
        try:
            async with self._send_lock:
                self._check()
                self.state = "FIN_WAIT"
                await self._exchange(FIN)
            await self._fin_ready.wait()
            self._check()
            self.state = "TIME_WAIT"
            self._idle.cancel()
            try:
                await asyncio.wait_for(self._aborted.wait(), 2 * self._rto)
            except asyncio.TimeoutError:
                pass
            self._check()
            self.state = "CLOSED"
        except (asyncio.CancelledError, OSError, OverflowError):
            self._abort(ConnectionAbortedError("Close interrupted"))
            raise

    def _abort(self, error: Exception, notify: bool = True) -> None:
        if self._error is not None or self.state == "CLOSED":
            return
        if notify and not self.owner._closed:
            try:
                self._send_packet(RST)
            except OSError:
                pass
        self._error, self.state, self._closing = error, "CLOSED", True
        self._idle.cancel()
        for event in (self._established, self._ack_ready, self._read_ready,
                      self._fin_ready, self._aborted):
            event.set()

    def stats(self) -> dict:
        return dict(proto="murmurtp", stage=1, cwnd_bytes=None, rtt_ms=None,
                    retx=self._retx, bytes_in_flight=self._inflight,
                    bytes_sent=self._bytes_sent, bytes_received=self._bytes_received,
                    buffered_bytes=len(self._rx), state=self.state)
