"""Choking selection, lifecycle, timer boundaries, and changing swarm snapshots."""

from dataclasses import replace
import random

import pytest
from hypothesis import given, settings, strategies as st

from peer.choking import ChokingPolicy, PeerRates


def peers(count=7):
    return [PeerRates(f"p{i}", True, 100 - i, i) for i in range(count)]


def test_four_regular_and_one_distinct_optimistic_peer():
    decision = ChokingPolicy(seed=42).update(peers(), 0)
    assert decision.regular == ("p0", "p1", "p2", "p3")
    assert decision.optimistic in {"p4", "p5", "p6"}
    assert len(decision.unchoked) == 5
    assert decision.to_unchoke == decision.unchoked
    assert decision.to_choke == frozenset()


def test_seed_ranks_upload_rate_and_role_change_is_immediate():
    policy = ChokingPolicy()
    policy.update(peers(), 0)
    assert policy.update(peers(), 1, is_seed=True).regular == ("p6", "p5", "p4", "p3")


def test_regular_rate_review_at_ten_seconds():
    policy = ChokingPolicy()
    first = policy.update(peers(), 0)
    changed = [replace(peer, down_bps=peer.up_bps) for peer in peers()]
    assert policy.update(changed, 9.999).regular == first.regular
    assert policy.update(changed, 10).regular == ("p6", "p5", "p4", "p3")


def test_optimistic_rotation_at_thirty_seconds():
    policy = ChokingPolicy(seed=42)
    first = policy.update(peers(), 0)
    assert policy.update(peers(), 10).optimistic == first.optimistic
    assert policy.update(peers(), 29.999).optimistic == first.optimistic
    rotated = policy.update(peers(), 30)
    assert rotated.optimistic != first.optimistic
    assert rotated.to_choke == {first.optimistic}
    assert rotated.to_unchoke == {rotated.optimistic}


@pytest.mark.parametrize("count", [0, 1, 4, 5])
def test_small_swarm_unchokes_every_interested_peer(count):
    policy = ChokingPolicy()
    decision = policy.update(peers(count), 0)
    assert decision.unchoked == {f"p{i}" for i in range(count)}
    assert len(decision.regular) == min(count, 4)
    again = policy.update(peers(count), 30)
    assert again.to_choke == again.to_unchoke == frozenset()


def test_uninterested_peers_never_take_slots_even_with_high_rates():
    snapshot = [PeerRates("not-interested", False, 1000, 1000), PeerRates("new", True)]
    assert ChokingPolicy().update(snapshot, 0).unchoked == {"new"}


@pytest.mark.parametrize("disconnected", [False, True])
def test_regular_removal_refills_slot_immediately(disconnected):
    policy = ChokingPolicy()
    policy.update(peers(), 0)
    snapshot = peers()[1:] if disconnected else [replace(peers()[0], interested=False)] + peers()[1:]
    result = policy.update(snapshot, 1)
    assert result.regular == ("p1", "p2", "p3", "p4")
    assert "p0" not in result.unchoked
    assert result.to_choke == (set() if disconnected else {"p0"})


def test_promoting_optimistic_peer_fills_a_new_optimistic_slot():
    policy = ChokingPolicy(seed=7)
    first = policy.update(peers(), 0)
    changed = [replace(peer, down_bps=1000) if peer.peer_id == first.optimistic else peer
               for peer in peers()]
    result = policy.update(changed, 10)
    assert first.optimistic in result.regular
    assert result.optimistic is not None and result.optimistic not in result.regular
    assert first.optimistic not in result.to_choke | result.to_unchoke
    assert len(result.unchoked) == 5


def test_disabling_uploads_chokes_all_then_reenabling_selects_immediately():
    policy = ChokingPolicy()
    first = policy.update(peers(), 0)
    disabled = policy.update(peers(), 1, uploads_enabled=False)
    assert not disabled.unchoked
    assert disabled.to_choke == first.unchoked
    assert policy.update(peers(), 2, uploads_enabled=False).to_choke == frozenset()
    enabled = policy.update(peers(), 3)
    assert len(enabled.unchoked) == 5
    assert enabled.to_unchoke == enabled.unchoked


def test_seeded_choices_and_rate_ties_ignore_input_order():
    state = random.getstate()
    snapshot = [PeerRates(f"p{i}", True) for i in range(8)]
    first, second = ChokingPolicy(seed=4), ChokingPolicy(seed=4)
    for now in (0, 10, 30, 60, 90):
        left = first.update(snapshot, now)
        assert left == second.update(reversed(snapshot), now)
        assert left.regular == ("p0", "p1", "p2", "p3")
    assert random.getstate() == state


def test_invalid_inputs_and_backwards_time_do_not_change_state():
    for kwargs in ({"unchoke_slots": -1}, {"unchoke_slots": True}, {"seed": None},
                   {"choke_interval": 0}, {"optimistic_unchoke_interval": float("inf")}):
        with pytest.raises(ValueError):
            ChokingPolicy(**kwargs)
    for kwargs in ({"peer_id": ""}, {"interested": 1}, {"down_bps": -1},
                   {"up_bps": float("nan")}):
        with pytest.raises(ValueError):
            PeerRates(**(dict(peer_id="x") | kwargs))
    policy = ChokingPolicy()
    previous = policy.update(peers(), 10)
    for snapshot, now in ((peers(), 9), (peers(), float("nan")), ([peers()[0]] * 2, 11)):
        with pytest.raises(ValueError):
            policy.update(snapshot, now)
    result = policy.update(peers(), 11)
    assert result.unchoked == previous.unchoked
    assert result.to_choke == result.to_unchoke == frozenset()


snapshot_strategy = st.lists(st.tuples(st.booleans(), st.integers(0, 100),
                                      st.integers(0, 100)), max_size=12)


@settings(max_examples=100, derandomize=True, database=None, deadline=None)
@given(st.lists(snapshot_strategy, min_size=1, max_size=5), st.integers(0, 4), st.booleans())
def test_changing_swarms_obey_slot_limits_ranking_and_message_deltas(snapshots, slots, is_seed):
    policy, previous = ChokingPolicy(unchoke_slots=slots, seed=42), frozenset()
    for tick, snapshot in enumerate(snapshots):
        current = [PeerRates(f"p{i:02}", *values) for i, values in enumerate(snapshot)]
        decision = policy.update(current, tick * 10, is_seed=is_seed)
        eligible = {peer.peer_id for peer in current if peer.interested}
        connected = {peer.peer_id for peer in current}
        assert decision.unchoked <= eligible
        assert len(decision.regular) == min(slots, len(eligible))
        assert len(decision.unchoked) == min(slots + 1, len(eligible))
        assert decision.optimistic not in decision.regular
        rates = {peer.peer_id: peer.up_bps if is_seed else peer.down_bps for peer in current}
        assert all(rates[chosen] >= rates[other] for chosen in decision.regular
                   for other in eligible - set(decision.regular))
        assert decision.to_choke == (previous - decision.unchoked) & connected
        assert decision.to_unchoke == decision.unchoked - previous
        previous = decision.unchoked
