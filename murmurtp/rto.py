"""Jacobson/Karels RTT estimator with the frozen MurmurTP timing bounds."""

import math


def _finite(value, name, *, zero=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (value == 0 and not zero)):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")


class RtoEstimator:
    """All values are seconds; callers apply Karn's rule before observe()."""

    def __init__(self, *, initial_rto=1.0, min_rto=0.2, max_rto=5.0, granularity=0.01):
        for name, value in (("initial_rto", initial_rto), ("min_rto", min_rto),
                            ("max_rto", max_rto), ("granularity", granularity)):
            _finite(value, name)
        if not min_rto <= initial_rto <= max_rto:
            raise ValueError("require min_rto <= initial_rto <= max_rto")
        self.min_rto, self.max_rto, self.granularity = min_rto, max_rto, granularity
        self.rto = float(initial_rto)
        self.srtt = self.rttvar = None
        self.samples = 0

    def observe(self, sample):
        _finite(sample, "RTT sample", zero=True)
        if self.srtt is None:
            self.srtt, self.rttvar = float(sample), sample / 2
        else:
            # Variation must use the OLD smoothed RTT.
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - sample)
            self.srtt = 0.875 * self.srtt + 0.125 * sample
        estimate = self.srtt + max(self.granularity, 4 * self.rttvar)
        self.rto = max(self.min_rto, min(self.max_rto, estimate))
        self.samples += 1
        return self.rto

    def backoff(self):
        self.rto = min(2 * self.rto, self.max_rto)
        return self.rto
