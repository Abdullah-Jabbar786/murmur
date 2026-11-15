"""Timed 4+1 choking policy from MASTER_PLAN section 7.3; no network I/O."""

from dataclasses import dataclass
import math
import random
from typing import FrozenSet, Iterable, Optional, Tuple


def _number(value: float, name: str, *, positive: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")


@dataclass(frozen=True)
class PeerRates:
    """One connected peer; rates are bits/sec from the LOCAL node's perspective."""

    peer_id: str
    interested: bool = False
    down_bps: float = 0.0  # Received from this peer.
    up_bps: float = 0.0  # Sent to this peer.

    def __post_init__(self) -> None:
        if not isinstance(self.peer_id, str) or not self.peer_id:
            raise ValueError("peer_id must be a nonempty string")
        if not isinstance(self.interested, bool):
            raise ValueError("interested must be a bool")
        _number(self.down_bps, "down_bps")
        _number(self.up_bps, "up_bps")


@dataclass(frozen=True)
class ChokeDecision:
    regular: Tuple[str, ...]
    optimistic: Optional[str]
    to_choke: FrozenSet[str]
    to_unchoke: FrozenSet[str]

    @property
    def unchoked(self) -> FrozenSet[str]:
        return frozenset(self.regular) | (frozenset([self.optimistic])
                                          if self.optimistic is not None else frozenset())


class ChokingPolicy:
    """Update with connected-peer snapshots and monotonic seconds.

    Regular rate ties use peer_id order. The optimistic slot uses a private RNG
    and rotates to a different eligible peer when alternatives exist.
    """

    def __init__(self, *, unchoke_slots: int = 4, choke_interval: float = 10,
                 optimistic_unchoke_interval: float = 30, seed: int = 0) -> None:
        if (not isinstance(unchoke_slots, int) or isinstance(unchoke_slots, bool)
                or unchoke_slots < 0):
            raise ValueError("unchoke_slots must be a nonnegative integer")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("seed must be an integer")
        _number(choke_interval, "choke_interval", positive=True)
        _number(optimistic_unchoke_interval, "optimistic_unchoke_interval", positive=True)
        self.unchoke_slots = unchoke_slots
        self.choke_interval = choke_interval
        self.optimistic_unchoke_interval = optimistic_unchoke_interval
        self._rng = random.Random(seed)
        self._regular: Tuple[str, ...] = ()
        self._optimistic: Optional[str] = None
        self._last_regular: Optional[float] = None
        self._last_optimistic: Optional[float] = None
        self._last_now: Optional[float] = None
        self._is_seed: Optional[bool] = None

    def update(self, peers: Iterable[PeerRates], now: float, *, is_seed: bool = False,
               uploads_enabled: bool = True) -> ChokeDecision:
        """Select peers and return changes, assuming new connections start choked.

        Call regularly and after interest/disconnection changes. Omitted peers
        are disconnected, so no CHOKE message is requested for them.
        """
        _number(now, "now")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("now must not move backwards")
        if not isinstance(is_seed, bool) or not isinstance(uploads_enabled, bool):
            raise ValueError("is_seed and uploads_enabled must be bools")
        connected = {}
        for peer in peers:
            if not isinstance(peer, PeerRates) or peer.peer_id in connected:
                raise ValueError("peers must contain unique PeerRates entries")
            connected[peer.peer_id] = peer
        previous = set(self._regular)
        if self._optimistic is not None:
            previous.add(self._optimistic)
        eligible = {key: peer for key, peer in connected.items() if peer.interested}
        ranked = sorted(eligible, key=lambda key: (
            -(eligible[key].up_bps if is_seed else eligible[key].down_bps), key))

        if not uploads_enabled:
            self._regular, self._optimistic = (), None
            self._last_regular = self._last_optimistic = None
        else:
            if (self._last_regular is None or is_seed != self._is_seed
                    or now - self._last_regular >= self.choke_interval):
                self._regular = tuple(ranked[:self.unchoke_slots])
                self._last_regular = now
            else:
                # Remove ineligible peers immediately, filling vacancies without
                # replacing other regular peers before the next rate review.
                kept = [key for key in self._regular if key in eligible]
                replacements = [key for key in ranked if key not in kept]
                self._regular = tuple((kept + replacements)[:self.unchoke_slots])
            remaining = sorted(set(eligible) - set(self._regular))
            if (self._optimistic not in remaining or self._last_optimistic is None
                    or now - self._last_optimistic >= self.optimistic_unchoke_interval):
                choices = [key for key in remaining if key != self._optimistic] or remaining
                self._optimistic = self._rng.choice(choices) if choices else None
                self._last_optimistic = now

        self._last_now, self._is_seed = now, is_seed
        selected = set(self._regular)
        if self._optimistic is not None:
            selected.add(self._optimistic)
        return ChokeDecision(self._regular, self._optimistic,
                             frozenset((previous - selected) & connected.keys()),
                             frozenset(selected - previous))
