"""Stage 3: SACK scoreboard, selective repeat, and SACK-gap fast retransmit.

The fixed window and bounded receiver come from Stage 2. Each unacknowledged
packet has its own retry deadline; RTT estimation and congestion control remain
later stages. The v1 bitmap describes cum_ack + 1 through cum_ack + 32.
"""

import asyncio
from dataclasses import dataclass

from murmurtp.header import ACK, DATA, MSS, Header
from murmurtp.window import SlidingWindowConnection, SlidingWindowTransport


@dataclass
class _Flight:
    deadline: float
    rto: float
    fast_retransmitted: bool = False


class SelectiveRepeatConnection(SlidingWindowConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._send_base = 0
        self._sacked = set()
        self._flights = {}
        self._fast_retx = self._timeout_retx = 0

    def _send_packet(self, flags, *, seq=0, ack=None, payload=b""):
        cumulative = self._rx_seq if ack is None else ack
        mask = 0
        if flags == ACK and seq == 0:
            for buffered in self._reorder:
                bit = buffered - cumulative - 1
                if 0 <= bit < 32:
                    mask |= 1 << bit
        free = self.config.receive_buffer - len(self._rx) - self._reorder_bytes
        header = Header(flags, self.conn_id, seq, cumulative, min(65535, free // MSS),
                        sack_mask=mask,
                        timestamp=int(self.owner._loop.time() * 1000) & 0xFFFFFFFF)
        self.owner._send(header, payload, self.peer_addr)

    def _handle(self, header, payload):
        if (self.state in ("ESTABLISHED", "FIN_WAIT", "TIME_WAIT")
                and header.flags == ACK and header.seq_num == 0
                and not payload and self._pending):
            if not self._send_base <= header.cum_ack <= self._tx_seq:
                return  # Stale or beyond-sent ACK; its bitmap cannot advance state.
            if header.cum_ack in self._sacked:
                return  # A non-reneging receiver cannot report a buffered packet missing.
            progressed = header.cum_ack > self._send_base
            self._send_base = header.cum_ack
            for seq in list(self._pending):
                if seq >= self._send_base:
                    break
                chunk = self._pending.pop(seq)
                if seq not in self._sacked:
                    self._acknowledged(seq)
                    self._inflight -= len(chunk)
                self._sacked.discard(seq)
                self._flights.pop(seq, None)
            for bit in range(32):
                if header.sack_mask & (1 << bit):
                    seq = header.cum_ack + 1 + bit
                    if seq in self._pending and seq not in self._sacked:
                        self._acknowledged(seq)
                        self._sacked.add(seq)
                        self._inflight -= len(self._pending[seq])
                        self._flights.pop(seq, None)
                        progressed = True
            if progressed:
                self._ack_progress()
                self._ack_ready.set()
                self._touch()
            return
        super()._handle(header, payload)

    def _new_flight(self, seq):
        return _Flight(self.owner._loop.time() + self.config.initial_rto, self.config.initial_rto)

    def _can_send(self):
        return self._tx_seq - self._send_base < self.config.window_packets

    def _ack_progress(self):
        """Hook after processing all newly acknowledged packets in an ACK."""

    def _acknowledged(self, seq):
        """Hook for timing a newly acknowledged DATA packet before removing it."""

    def _on_timeout_batch(self, expired):
        """Hook for connection-wide state when one or more packet timers expire."""

    def _retry_rto(self, flight, timeout):
        return min(2 * flight.rto, self.config.max_rto) if timeout else flight.rto

    def _retry(self, seq, *, timeout):
        flight = self._flights[seq]
        flight.rto = self._retry_rto(flight, timeout)
        if timeout:
            self._timeout_retx += 1
        else:
            flight.fast_retransmitted = True
            self._fast_retx += 1
        self._send_packet(DATA, seq=seq, payload=self._pending[seq])
        self._retx += 1
        self._rto = flight.rto
        flight.deadline = self.owner._loop.time() + flight.rto

    def _retry_due(self, now):
        expired = {seq for seq, flight in self._flights.items() if now >= flight.deadline}
        if expired:
            self._on_timeout_batch(expired)
        for seq, flight in self._flights.items():
            if seq in expired:
                self._retry(seq, timeout=True)
            elif (not flight.fast_retransmitted
                  and sum(later > seq for later in self._sacked) >= 3):
                self._retry(seq, timeout=False)

    async def _wait_for_ack(self, timeout):
        # Python 3.10/3.11 wait_for can swallow caller cancellation if its
        # inner task finishes at the same time (CPython issue #86296).
        # wait propagates that cancellation; explicitly reap our child task.
        waiter = asyncio.create_task(self._ack_ready.wait())
        try:
            done, _ = await asyncio.wait((waiter,), timeout=timeout)
            if done:
                waiter.result()
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

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
                    raise OverflowError("Stage 3 does not wrap sequence numbers; reserve one for FIN")
                offset = 0
                while offset < len(data) or self._pending:
                    self._check()
                    self._ack_ready.clear()
                    # React to loss before admitting any new application data.
                    self._retry_due(self.owner._loop.time())
                    # Bound the SEQUENCE SPAN, not just unsacked packet count.
                    # SACKed successors must not let us outrun a missing head.
                    while offset < len(data) and self._can_send():
                        chunk = data[offset:offset + MSS]
                        seq = self._tx_seq
                        self._pending[seq] = chunk
                        self._flights[seq] = self._new_flight(seq)
                        self._tx_seq += 1
                        offset += len(chunk)
                        self._bytes_sent += len(chunk)
                        self._inflight += len(chunk)
                        self._send_packet(DATA, seq=seq, payload=chunk)
                    # Every nonempty window has an unsacked cumulative head.
                    deadline = min(f.deadline for f in self._flights.values())
                    await self._wait_for_ack(max(0, deadline - self.owner._loop.time()))
                    self._check()  # Abort clears the window; that must not look like success.
            except (asyncio.CancelledError, OSError, OverflowError):
                self._abort(ConnectionAbortedError("Send interrupted"))
                raise
            finally:
                self._pending.clear()
                self._sacked.clear()
                self._flights.clear()
                self._inflight = 0

    def _abort(self, error, notify=True):
        super()._abort(error, notify=notify)
        self._sacked.clear()
        self._flights.clear()

    def stats(self):
        result = super().stats()
        result.update(stage=3, sacked_packets=len(self._sacked),
                      unacked_packets=len(self._flights),
                      fast_retx=self._fast_retx, timeout_retx=self._timeout_retx)
        return result


class SelectiveRepeatTransport(SlidingWindowTransport):
    """Stage 3 endpoint; accepts the existing SlidingWindowConfig (1..32)."""

    connection_type = SelectiveRepeatConnection
