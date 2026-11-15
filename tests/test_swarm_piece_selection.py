"""Selection behavior, reproducibility, and randomized eligibility checks."""

import random

import pytest
from hypothesis import given, settings, strategies as st

from peer.piece_selection import RarestFirstSelector, count_availability


def test_availability_counts_peers_not_duplicate_advertisements():
    assert count_availability(4, [[0, 0, 1], [1, 2], [], [2, 2]]) == (1, 2, 2, 0)
    assert count_availability(3, []) == (0, 0, 0)
    assert count_availability(0, [[], []]) == ()


def test_rarest_piece_must_be_missing_and_offered_by_target():
    selector = RarestFirstSelector(4)
    # Piece 0 is already owned; piece 2 is not available from this peer.
    assert selector.select({0, 1, 3}, {0}, [1, 3, 0, 5]) == 1


def test_started_piece_precedes_rarer_unstarted_piece():
    selector = RarestFirstSelector(4)
    assert selector.select({0, 1, 2, 3}, set(), [1, 1, 2, 8], started_pieces={3}) == 3
    assert selector.select({0, 1, 2, 3}, set(), [1, 1, 2, 8], started_pieces={2, 3}) == 2


def test_started_priority_obeys_ownership_availability_and_exclusions():
    selector = RarestFirstSelector(5)
    # Owned 0, excluded 1, and unoffered 2 must not win via started priority.
    assert selector.select({0, 1, 3, 4}, {0}, [1, 1, 1, 3, 2],
                           started_pieces={0, 1, 2}, excluded_pieces={1}) == 4
    # An excluded partial piece becomes eligible again when requests time out.
    assert selector.select({1, 4}, set(), [1, 1, 1, 3, 2], started_pieces={1}) == 1


@pytest.mark.parametrize("offered,owned,excluded", [
    (set(), set(), set()), ({0, 1}, {0, 1}, set()),
    ({0, 1}, set(), {0, 1}), ({0, 1}, {0}, {1}),
])
def test_no_eligible_piece_returns_none(offered, owned, excluded):
    assert RarestFirstSelector(2).select(offered, owned, [1, 1],
                                         excluded_pieces=excluded) is None


def test_empty_torrent():
    assert RarestFirstSelector(0).select([], [], []) is None


def test_seeded_ties_are_reproducible_and_input_order_independent():
    global_state = random.getstate()
    first, second = RarestFirstSelector(3, seed=42), RarestFirstSelector(3, seed=42)
    left = [first.select([0, 1, 2], [], [1, 1, 1]) for _ in range(60)]
    right = [second.select([2, 1, 0], [], [1, 1, 1]) for _ in range(60)]
    assert left == right
    assert set(left) == {0, 1, 2}
    assert random.getstate() == global_state


def test_snapshots_are_not_mutated_and_selection_does_not_reserve():
    offered, owned, started, excluded, counts = {0, 1, 2}, {0}, {1}, {2}, [1, 2, 1]
    selector = RarestFirstSelector(3)
    for _ in range(2):
        assert selector.select(offered, owned, counts, started_pieces=started,
                               excluded_pieces=excluded) == 1
    assert (offered, owned, started, excluded, counts) == ({0, 1, 2}, {0}, {1}, {2}, [1, 2, 1])


def test_new_advertisements_and_disconnects_change_rarity():
    selector = RarestFirstSelector(2)
    peers = [[0, 1], [0]]
    assert selector.select(peers[0], [], count_availability(2, peers)) == 1
    peers = [[0, 1], [1], [1]]
    assert selector.select(peers[0], [], count_availability(2, peers)) == 0


def test_invalid_sizes_seeds_counts_and_indices():
    for size in (-1, True, 1.5):
        with pytest.raises(ValueError, match="num_pieces"):
            RarestFirstSelector(size)
        with pytest.raises(ValueError, match="num_pieces"):
            count_availability(size, [])
    for seed in (True, None, 1.5):
        with pytest.raises(ValueError, match="seed"):
            RarestFirstSelector(2, seed=seed)
    selector = RarestFirstSelector(2)
    for counts in ([], [1], [1, 1, 1], [-1, 1], [True, 1], [1.5, 1]):
        with pytest.raises(ValueError, match="availability"):
            selector.select([], [], counts)
    for invalid in (-1, 2, True, 0.5):
        with pytest.raises(ValueError, match="peer_pieces"):
            count_availability(2, [[invalid]])
        for name in ("peer_pieces", "owned_pieces", "started_pieces", "excluded_pieces"):
            args = dict(peer_pieces=[], owned_pieces=[], availability=[1, 1])
            args[name] = [invalid]
            with pytest.raises(ValueError, match=name):
                selector.select(**args)


@st.composite
def swarm_snapshots(draw):
    size = draw(st.integers(1, 32))
    pieces = st.sets(st.integers(0, size - 1))
    peers = draw(st.lists(pieces, min_size=1, max_size=8))
    return size, peers, draw(pieces), draw(pieces), draw(pieces)


@settings(max_examples=150, derandomize=True, database=None, deadline=None)
@given(swarm_snapshots())
def test_random_snapshots_obey_eligibility_priority_and_rarity(snapshot):
    size, peers, owned, started, excluded = snapshot
    counts = count_availability(size, peers)
    assert counts == tuple(sum(piece in peer for peer in peers) for piece in range(size))
    selected = RarestFirstSelector(size, seed=7).select(
        peers[0], owned, counts, started_pieces=started, excluded_pieces=excluded)
    eligible = peers[0] - owned - excluded
    if not eligible:
        assert selected is None
    else:
        assert selected in eligible
        # Lexicographic priority gives an independent oracle for the winner.
        rank = lambda piece: (piece not in started, counts[piece])
        assert rank(selected) == min(rank(piece) for piece in eligible)
