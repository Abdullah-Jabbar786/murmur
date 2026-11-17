"""Stage 2: fixed sliding window, cumulative ACKs, bounded reorder buffering.

Timeouts resend the outstanding window. SACK/selective retransmission and
adaptive timing/congestion control are later stages; the v1 wire format stays
unchanged. Stage 1 supplies the endpoint, handshake, FIN exchange, and cleanup.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

from murmurtp.connection import StopAndWaitConfig, StopAndWaitConnection
from murmurtp.header import ACK, DATA, FIN, MSS, Header
from murmurtp.lossy import LossConfig
from murmurtp.transport import StopAndWaitTransport


@dataclass(frozen=True)
class SlidingWindowConfig(StopAndWaitConfig):
    window_packets: int = 16

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.window_packets) is not int or not 1 <= self.window_packets <= 32:
            raise ValueError("window_packets must be an integer from 1 to 32")


class SlidingWindowConnection(StopAndWaitConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = {}
        self._reorder = {}
        self._reorder_bytes = 0

    def _send_packet(self, flags, *, seq=0, ack=None, payload=b""):
        free = self.config.receive_buffer - len(self._rx) - self._reorder_bytes
        header = Header(flags, self.conn_id, seq,
                        self._rx_seq if ack is None else ack,
                        min(65535, free // MSS),
                        timestamp=int(self.owner._loop.time() * 1000) & 0xFFFFFFFF)
        self.owner._send(header, payload, self.peer_addr)

    def _handle(self, header, payload):
        active = self.state in ("ESTABLISHED", "FIN_WAIT", "TIME_WAIT")
        if active and header.flags == ACK and header.seq_num == 0 and not payload and self._pending:
            # Ignore stale/duplicate ACKs and ACKs beyond data actually sent.
            first = next(iter(self._pending))
            if first < header.cum_ack <= self._tx_seq:
                for seq in list(self._pending):
                    if seq >= header.cum_ack:
                        break
                    self._inflight -= len(self._pending.pop(seq))
                self._ack_ready.set()
                self._touch()
            return
        if active and header.flags == DATA:
            seq = header.seq_num
            if (not self._rx_fin and payload and self._rx_seq <= seq
                    < min(0xFFFFFFFF, self._rx_seq + self.config.window_packets)
                    and seq not in self._reorder):
                free = self.config.receive_buffer - len(self._rx) - self._reorder_bytes
                # Leave space for the missing head packet, even if later packets
                # fill the reorder window. Otherwise a gap can deadlock delivery.
                reserve = MSS if seq != self._rx_seq else 0
                if len(payload) <= free - reserve:
                    self._reorder[seq] = payload
                    self._reorder_bytes += len(payload)
                    while self._rx_seq in self._reorder:
                        chunk = self._reorder.pop(self._rx_seq)
                        self._reorder_bytes -= len(chunk)
                        self._rx.extend(chunk)
                        self._rx_seq += 1
                        self._read_ready.set()
                    self._touch()
            self._send_packet(ACK)
            return
        if (active and header.flags == FIN and not payload
                and header.seq_num == self._rx_seq and self._reorder):
            self._abort(ConnectionResetError("FIN precedes buffered DATA"))
            return
        super()._handle(header, payload)

    async def send(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        async with self._send_lock:
            self._check()
            if self._closing or self.state != "ESTABLISHED":
                raise ConnectionError("Connection is not open for sending")
            try:
                packets = (len(data) + MSS - 1) // MSS
                if self._tx_seq + packets >= 0xFFFFFFFF:
                    raise OverflowError("Stage 2 does not wrap sequence numbers; reserve one for FIN")
                offset = 0
                rto = self.config.initial_rto
                while offset < len(data) or self._pending:
                    self._check()
                    self._ack_ready.clear()
                    while offset < len(data) and len(self._pending) < self.config.window_packets:
                        chunk = data[offset:offset + MSS]
                        seq = self._tx_seq
                        self._pending[seq] = chunk
                        self._tx_seq += 1
                        offset += len(chunk)
                        self._bytes_sent += len(chunk)
                        self._inflight += len(chunk)
                        self._send_packet(DATA, seq=seq, payload=chunk)
                    self._rto = rto
                    try:
                        await asyncio.wait_for(self._ack_ready.wait(), rto)
                    except asyncio.TimeoutError:
                        self._check()
                        if not self._ack_ready.is_set():
                            # Stage 2 has only cumulative acknowledgments: resend
                            # all remaining packets, including buffered successors.
                            for seq, chunk in self._pending.items():
                                self._send_packet(DATA, seq=seq, payload=chunk)
                                self._retx += 1
                            rto = min(2 * rto, self.config.max_rto)
                    else:
                        self._check()
                        rto = self.config.initial_rto
            except (asyncio.CancelledError, OSError, OverflowError):
                self._abort(ConnectionAbortedError("Send interrupted"))
                raise
            finally:
                self._pending.clear()
                self._inflight = 0

    def _abort(self, error, notify=True):
        super()._abort(error, notify=notify)
        self._pending.clear()
        self._reorder.clear()
        self._reorder_bytes = self._inflight = 0

    def stats(self):
        result = super().stats()
        result.update(stage=2, window_packets=self.config.window_packets,
                      pending_packets=len(self._pending),
                      buffered_bytes=len(self._rx) + self._reorder_bytes,
                      out_of_order_packets=len(self._reorder),
                      out_of_order_bytes=self._reorder_bytes)
        return result


class SlidingWindowTransport(StopAndWaitTransport):
    """Stage 2 endpoint; the fixed packet window is not a congestion window."""

    connection_type = SlidingWindowConnection

    def __init__(self, *, config: Optional[SlidingWindowConfig] = None,
                 loss: Optional[LossConfig] = None, seed: int = 0):
        if config is None:
            config = SlidingWindowConfig()
        if not isinstance(config, SlidingWindowConfig):
            raise TypeError("config must be SlidingWindowConfig")
        super().__init__(config=config, loss=loss, seed=seed)
