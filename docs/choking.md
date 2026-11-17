# Choking and optimistic unchoking (A1.5)

`peer/choking.py` implements the master plan's four regular unchoke slots plus
one optimistic slot. It returns decisions; A2.1's `peer/swarm.py` applies them
to sessions and logs outgoing messages through the `peer.node swarm` command.
See [algorithm-integration.md](algorithm-integration.md) for the running policy.

```python
from core.config import load_config
from peer.choking import ChokingPolicy, PeerRates

config = load_config()
policy = ChokingPolicy(
    unchoke_slots=config["unchoke_slots"],
    choke_interval=config["choke_interval"],
    optimistic_unchoke_interval=config["optimistic_unchoke_interval"], seed=42)
snapshot = [PeerRates("peer-a", interested=True, down_bps=5000, up_bps=1000),
            PeerRates("peer-b", interested=True, down_bps=2000, up_bps=4000)]
decision = policy.update(snapshot, now=0.0, is_seed=False)
assert decision.unchoked == {"peer-a", "peer-b"}
assert decision.to_unchoke == decision.unchoked
assert not decision.to_choke
```

## Selection and timing

- Supply every currently connected peer, including uninterested peers. Only
  interested peers can receive a slot. A new `PeerRates` defaults to uninterested.
- Rates are from the local node's perspective: `down_bps` is received from that
  peer and `up_bps` is sent to it. Use comparable recent measurement windows.
- A leecher ranks by download rate; a seed ranks by upload rate. At the first
  update and every 10 seconds thereafter, choose up to four regular peers.
  Equal rates use peer ID order for stable rankings.
- Pick one other interested peer randomly for the optimistic slot. Every
  30 seconds, choose a different peer if alternatives exist. With only one
  candidate, retain it; with none, the optimistic slot is `None`.
- Remove disconnected or uninterested peers immediately. Fill regular vacancies
  using current rates, preserving other regular choices until the next review.
  If the optimistic peer becomes regular or ineligible, replace it immediately
  and start a new optimistic interval. Switching between leecher and seed roles
  triggers an immediate regular review.
- Call `update` regularly and after peer/interest changes; the policy does not
  run timers itself. Use `time.monotonic()` seconds in the live peer loop and
  simulated timestamps in tests. Time must be finite and never move backwards.

## Applying decisions

`regular` and `optimistic` identify the selected slots; `unchoked` is their
combined immutable set. `to_choke` and `to_unchoke` contain only changes since
the preceding call. New connections must start choked. Disconnected peers are
removed internally without asking the caller to send messages on dead sockets.
Use distinct connection identifiers and remove closed connections from snapshots.

For each delta, the peer loop should send CHOKE/UNCHOKE, update `am_choking`, and
emit the corresponding event through `EventLog`. This module performs none of
those I/O operations and does not implement request blocking or rate sampling.

For the client-server baseline, pass `uploads_enabled=False` on leechers; that
immediately chokes all selected connected peers and clears the timers. Leave
uploads enabled on the seed. Re-enabling uploads selects peers immediately.
Keep one policy per local peer, with a private seed. The same call sequence,
snapshots, timestamps, and seed reproduce decisions regardless of input order.

Run `python -m pytest tests/test_swarm_choking.py -v -p no:cacheprovider`.
There are 16 cases, including timer boundaries, role changes, peer departures,
slot limits, disabling uploads, seeded choices, and 100 randomized changing
swarm examples. The Windows full suite gives 82 passed and 2 existing Linux TCP
skips. Python 3.10 syntax validation also passes; local runtime testing used 3.14.
