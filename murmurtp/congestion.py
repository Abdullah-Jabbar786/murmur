"""Stage 5: the frozen MurmurTP Reno-style congestion rules (packets)."""

import math

from murmurtp.adaptive import AdaptiveRtoConnection, AdaptiveRtoTransport
from murmurtp.header import MSS


class RenoController:
    """Recovery ends when the cumulative ACK covers data sent before loss."""

    def __init__(self):
        self.cwnd = 10.0
        self.ssthresh = math.inf
        self.recovery_end = None  # Exclusive sequence boundary, not a packet count.
        self.fast_reductions = self.timeout_reductions = 0

    def acknowledged(self):
        # The scoreboard calls this once per DATA packet, including SACK.
        self.cwnd += 1 if self.cwnd < self.ssthresh else 1 / self.cwnd

    def ack_progress(self, cumulative):
        if self.recovery_end is not None and cumulative >= self.recovery_end:
            self.recovery_end = None

    def sack_loss(self, next_seq):
        if self.recovery_end is not None:
            return
        self.ssthresh = max(2.0, self.cwnd / 2)
        self.cwnd = self.ssthresh
        self.recovery_end = next_seq
        self.fast_reductions += 1

    def timeout(self, next_seq):
        self.ssthresh = max(2.0, self.cwnd / 2)
        self.cwnd = 1.0
        self.recovery_end = next_seq
        self.timeout_reductions += 1


class CongestionConnection(AdaptiveRtoConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._congestion = RenoController()

    def _can_send(self):
        # SACKed packets no longer count as in flight, but the fixed sequence
        # horizon still prevents overrunning the receiver's bounded scoreboard.
        return (super()._can_send()
                and len(self._flights) < math.floor(self._congestion.cwnd))

    def _acknowledged(self, seq):
        super()._acknowledged(seq)  # Karn restricts RTT sampling, not ACK growth.
        self._congestion.acknowledged()

    def _ack_progress(self):
        self._congestion.ack_progress(self._send_base)

    def _on_timeout_batch(self, expired):
        previous_round = self._timeout_round
        super()._on_timeout_batch(expired)
        if self._timeout_round != previous_round:
            self._congestion.timeout(self._tx_seq)

    def _retry(self, seq, *, timeout):
        if not timeout:
            self._congestion.sack_loss(self._tx_seq)
        # Recovery retransmits already outstanding DATA even after cwnd shrinks.
        super()._retry(seq, timeout=timeout)

    def stats(self):
        result = super().stats()
        cc = self._congestion
        result.update(stage=5, cwnd_bytes=int(cc.cwnd * MSS), cwnd_packets=cc.cwnd,
                      ssthresh_packets=None if math.isinf(cc.ssthresh) else cc.ssthresh,
                      recovery_end=cc.recovery_end,
                      fast_reductions=cc.fast_reductions,
                      timeout_reductions=cc.timeout_reductions)
        return result


class CongestionTransport(AdaptiveRtoTransport):
    """Stage 5 endpoint with AdaptiveRtoConfig and a fixed receive horizon."""

    connection_type = CongestionConnection
