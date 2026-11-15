"""Pure piece-selection rules from MASTER_PLAN section 7.3.

The peer loop supplies current snapshots and retains ownership of block requests,
partial pieces, and verified completion. This module performs no network I/O.
"""

import random
from typing import Iterable, Optional, Sequence, Set, Tuple


def _check_count(num_pieces: int) -> None:
    if not isinstance(num_pieces, int) or isinstance(num_pieces, bool) or num_pieces < 0:
        raise ValueError("num_pieces must be a nonnegative integer")


def _piece_set(pieces: Iterable[int], num_pieces: int, name: str) -> Set[int]:
    result: Set[int] = set()
    for piece in pieces:
        if (not isinstance(piece, int) or isinstance(piece, bool)
                or not 0 <= piece < num_pieces):
            raise ValueError(f"{name} contains an invalid piece index: {piece!r}")
        result.add(piece)
    return result


def count_availability(num_pieces: int, peer_pieces: Iterable[Iterable[int]]) -> Tuple[int, ...]:
    """Count how many remote peers advertise each piece, once per peer.

    Supply one iterable of decoded piece indices per connected peer (not raw
    bitfield bytes). Recompute after BITFIELD/HAVE updates or disconnections.
    """
    _check_count(num_pieces)
    counts = [0] * num_pieces
    for pieces in peer_pieces:
        for piece in _piece_set(pieces, num_pieces, "peer_pieces"):
            counts[piece] += 1
    return tuple(counts)


class RarestFirstSelector:
    """Prefer started pieces, then rarity, with a private seeded tie-break RNG."""

    def __init__(self, num_pieces: int, *, seed: int = 0) -> None:
        _check_count(num_pieces)
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("seed must be an integer")
        self.num_pieces = num_pieces
        self._rng = random.Random(seed)

    def select(self, peer_pieces: Iterable[int], owned_pieces: Iterable[int],
               availability: Sequence[int], *, started_pieces: Iterable[int] = (),
               excluded_pieces: Iterable[int] = ()) -> Optional[int]:
        """Return a requestable piece index, or None when there are no candidates.

        owned_pieces are hash-verified complete pieces. excluded_pieces have no
        requestable blocks (e.g. all missing blocks already in flight). Selecting
        does not reserve a piece or mark it started/owned; the caller does that.
        """
        counts = tuple(availability)
        if len(counts) != self.num_pieces:
            raise ValueError("availability must contain one count per piece")
        if any(not isinstance(count, int) or isinstance(count, bool) or count < 0
               for count in counts):
            raise ValueError("availability counts must be nonnegative integers")
        offered = _piece_set(peer_pieces, self.num_pieces, "peer_pieces")
        owned = _piece_set(owned_pieces, self.num_pieces, "owned_pieces")
        started = _piece_set(started_pieces, self.num_pieces, "started_pieces")
        excluded = _piece_set(excluded_pieces, self.num_pieces, "excluded_pieces")
        candidates = offered - owned - excluded
        if not candidates:
            return None
        candidates = (candidates & started) or candidates
        rarity = min(counts[piece] for piece in candidates)
        # Stable ordering makes tie-breaking independent of input/set order.
        tied = sorted(piece for piece in candidates if counts[piece] == rarity)
        return tied[0] if len(tied) == 1 else self._rng.choice(tied)
