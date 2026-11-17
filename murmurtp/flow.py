"""Stage 6: advertised receive slots, read updates and zero-window persist."""

from dataclasses import dataclass
import math

from murmurtp.adaptive import AdaptiveRtoConfig
from murmurtp.congestion import CongestionConnection, CongestionTransport
from murmurtp.header import ACK, DATA, MSS, SYN


@dataclass(frozen=True)
class FlowControlConfig(AdaptiveRtoConfig):
    probe_interval: float = 1.0

    def __post_init__(self):
        super().__post_init__()
        value = self.probe_interval
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError("probe_interval must be finite and positive")


class FlowControlConnection(CongestionConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._peer_window = 0  # Learned from SYN-ACK or the final handshake ACK.
        self._window_ack = 0
        self._advertised_window = self._receive_slots()
        self._persist_deadline = None
        self._window_probes = 0

    def _receive_slots(self):
        free = self.config.receive_buffer - len(self._rx) - self._reorder_bytes
        return min(65535, free // MSS)

    def _send_packet(self, flags, **kwargs):
        self._advertised_window = self._receive_slots()
        super()._send_packet(flags, **kwargs)

    def _update_window(self, window, cumulative):
        old = self._peer_window
        self._peer_window, self._window_ack = window, cumulative
        # FIN waits share the event but must wake only for their matching ACK;
        # a pure window update is not a FIN timeout or acknowledgment.
        if window != old and self._waiting_ack is None:
            self._ack_ready.set()
        if window > old and self.state not in ("TIME_WAIT", "CLOSED"):
            self._touch()  # Receiver made buffer space; repeated zero ACKs do not.
        if window and old == 0:
            self._persist_deadline = None
            # Paused DATA timers must not turn receiver backpressure into loss.
            now = self.owner._loop.time()
            for flight in self._flights.values():
                flight.deadline = now + flight.rto

    def _handle(self, header, payload):
        if not payload:
            if (self.client and self.state == "SYN_SENT" and header.flags == SYN | ACK
                    and header.seq_num == 0 and header.cum_ack == 1 and header.conn_id
                    and self.conn_id in (0, header.conn_id)):
                self._update_window(header.window, 0)
            elif (not self.client and self.state == "SYN_RCVD" and header.flags == ACK
                  and header.seq_num == 1 and header.cum_ack == 1):
                self._update_window(header.window, 0)
            elif (self.state in ("ESTABLISHED", "FIN_WAIT", "TIME_WAIT")
                  and header.flags == ACK and header.seq_num == 0
                  and max(self._send_base, self._window_ack) <= header.cum_ack
                  <= max(self._tx_seq, self._waiting_ack or 0)
                  and header.cum_ack not in self._sacked):
                self._update_window(header.window, header.cum_ack)
        super()._handle(header, payload)

    def _can_send(self):
        return super()._can_send() and len(self._flights) < self._peer_window

    def _retry_due(self, now):
        if self._peer_window:
            super()._retry_due(now)

    async def _wait_for_send_progress(self):
        if self._peer_window:
            await super()._wait_for_send_progress()
            return
        now = self.owner._loop.time()
        if self._persist_deadline is None:
            self._persist_deadline = now + self.config.probe_interval
        if now >= self._persist_deadline:
            # Empty DATA already elicits an ACK without delivery/sequence advance.
            # It is a query, never a new flight or an RTT/congestion sample.
            self._send_packet(DATA, seq=self._tx_seq)
            self._window_probes += 1
            self._persist_deadline = now + self.config.probe_interval
        await self._wait_for_ack(max(0, self._persist_deadline - self.owner._loop.time()))

    async def recv(self, max_bytes=65536):
        data = await super().recv(max_bytes)
        if (data and self._error is None and self.state != "CLOSED"
                and self._receive_slots() > self._advertised_window):
            self._send_packet(ACK)
        return data

    def _abort(self, error, notify=True):
        super()._abort(error, notify=notify)
        self._persist_deadline = None

    def stats(self):
        result = super().stats()
        result.update(stage=6, peer_window_packets=self._peer_window,
                      receive_window_packets=self._receive_slots(),
                      window_probes=self._window_probes)
        return result


class FlowControlTransport(CongestionTransport):
    """Stage 6 endpoint with fixed-interval persist probes (one second by default)."""

    connection_type = FlowControlConnection

    def __init__(self, *, config=None, loss=None, seed=0):
        if config is None:
            config = FlowControlConfig()
        if not isinstance(config, FlowControlConfig):
            raise TypeError("config must be FlowControlConfig")
        super().__init__(config=config, loss=loss, seed=seed)
