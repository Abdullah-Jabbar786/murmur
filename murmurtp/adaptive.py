"""Stage 4: adaptive DATA/FIN retry timing on top of selective repeat."""

from dataclasses import dataclass

from murmurtp.rto import RtoEstimator
from murmurtp.sack import SelectiveRepeatConnection, SelectiveRepeatTransport, _Flight
from murmurtp.window import SlidingWindowConfig


@dataclass(frozen=True)
class AdaptiveRtoConfig(SlidingWindowConfig):
    min_rto: float = 0.2
    clock_granularity: float = 0.01

    def __post_init__(self):
        super().__post_init__()
        RtoEstimator(initial_rto=self.initial_rto, min_rto=self.min_rto,
                     max_rto=self.max_rto, granularity=self.clock_granularity)


@dataclass
class _TimedFlight(_Flight):
    sent_at: float = 0.0
    retransmitted: bool = False
    timeout_round: int = 0


class AdaptiveRtoConnection(SelectiveRepeatConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timer = RtoEstimator(initial_rto=self.config.initial_rto,
                                  min_rto=self.config.min_rto, max_rto=self.config.max_rto,
                                  granularity=self.config.clock_granularity)
        self._timeout_round = 0

    def _new_flight(self, seq):
        now, rto = self.owner._loop.time(), self._timer.rto
        return _TimedFlight(now + rto, rto, sent_at=now, timeout_round=self._timeout_round)

    def _acknowledged(self, seq):
        flight = self._flights.get(seq)
        if flight is not None and not flight.retransmitted:
            self._timer.observe(self.owner._loop.time() - flight.sent_at)

    def _on_timeout_batch(self, expired):
        # Timers in a flight are slightly staggered. Back off once for that
        # transmission round, not once for each packet/callback in the burst.
        if any(self._flights[seq].timeout_round == self._timeout_round for seq in expired):
            self._timer.backoff()
            self._timeout_round += 1

    def _retry_rto(self, flight, timeout):
        return self._timer.rto

    def _retry(self, seq, *, timeout):
        flight = self._flights[seq]
        flight.retransmitted = True  # Karn: applies to timeout AND SACK-gap retries.
        flight.timeout_round = self._timeout_round
        super()._retry(seq, timeout=timeout)

    async def _exchange(self, flags, payload=b""):
        # Selective-repeat send handles DATA. The inherited graceful-close path
        # calls this for FIN, using the learned RTO without taking an RTT sample.
        if self._tx_seq >= 0xFFFFFFFF:
            raise OverflowError("Stage 4 does not wrap packet sequence numbers")
        self._waiting_ack, self._acked = self._tx_seq + 1, False
        self._ack_ready.clear()
        self._inflight = len(payload)
        try:
            while True:
                self._check()
                self._rto = self._timer.rto
                self._send_packet(flags, seq=self._tx_seq, payload=payload)
                await self._wait_for_ack(self._rto)
                self._check()
                if self._acked:
                    self._tx_seq += 1
                    return
                self._retx += 1
                self._timer.backoff()
        finally:
            self._waiting_ack, self._inflight = None, 0

    def stats(self):
        result = super().stats()
        result.update(stage=4, rtt_ms=None if self._timer.srtt is None else self._timer.srtt * 1000,
                      rttvar_ms=None if self._timer.rttvar is None else self._timer.rttvar * 1000,
                      rto_ms=self._timer.rto * 1000, rtt_samples=self._timer.samples)
        return result


class AdaptiveRtoTransport(SelectiveRepeatTransport):
    """Stage 4 endpoint; window size is still fixed, not congestion-controlled."""

    connection_type = AdaptiveRtoConnection

    def __init__(self, *, config=None, loss=None, seed=0):
        if config is None:
            config = AdaptiveRtoConfig()
        if not isinstance(config, AdaptiveRtoConfig):
            raise TypeError("config must be AdaptiveRtoConfig")
        super().__init__(config=config, loss=loss, seed=seed)
