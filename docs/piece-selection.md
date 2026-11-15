# Rarest-first piece selection (A1.4)

`peer/piece_selection.py` implements the pure selection rule in master plan
section 7.3. It performs no networking, disk operations, or block scheduling.

```python
from peer.piece_selection import RarestFirstSelector, count_availability

peers = [{0, 1, 2, 3}, {0, 2, 3}, {0, 3}]
availability = count_availability(4, peers)  # (3, 1, 2, 3)
selector = RarestFirstSelector(4, seed=42)

# Piece 1 is the rarest missing piece offered by peer 0.
assert selector.select(peers[0], {0}, availability) == 1
# A partially downloaded piece takes priority over starting piece 1.
assert selector.select(peers[0], {0}, availability, started_pieces={2}) == 2
# If all remaining blocks of piece 2 are in flight, choose another piece.
assert selector.select(peers[0], {0}, availability,
                       started_pieces={2}, excluded_pieces={2}) == 1
```

Selection order:

1. Keep pieces advertised by the target peer, excluding locally owned pieces
   and `excluded_pieces` supplied by the request scheduler.
2. If any eligible piece is in `started_pieces`, consider only those pieces.
3. Pick the lowest availability count within that group.
4. Break ties using the selector's private seeded RNG and sorted piece indices.
5. Return `None` when no eligible pieces remain. Piece index `0` is valid:
   callers must check `piece is not None`, rather than `if piece`.

## Integration responsibilities

- Pass decoded piece indices, not raw BITFIELD bytes. All indices must be
  integers from zero through `num_pieces - 1`.
- Availability is a count for every piece, across connected remote peers,
  including the target peer. `count_availability` counts each piece at most
  once per peer; refresh the snapshot after BITFIELD/HAVE updates and disconnects.
- `owned_pieces` contains hash-verified complete pieces. `started_pieces`
  contains partial pieces with received blocks. Update these after completion
  or hash failure in the storage/peer layer.
- Exclude pieces with no currently requestable blocks, such as pieces awaiting
  responses for all missing blocks. Clear the exclusion when retries become
  possible. Do not exclude every partial piece: that would defeat finish-first.
- Check interest/choking before issuing requests. The selector does not handle
  choke state, reserve work, emit events, or mark a returned piece started/owned.
  Input snapshots are never modified. Keep one selector per local peer; the same
  seed and sequence of snapshots/calls reproduce tie choices. Use distinct seeds
  across peers to avoid identical tie choices throughout the swarm.
- Invalid indices, sizes, seeds, or availability counts raise `ValueError`.

Run `python -m pytest tests/test_swarm_piece_selection.py -v -p no:cacheprovider`.
There are 14 cases, including a property test over 150 randomized swarm snapshots.
The Windows full suite currently gives 66 passed and 2 existing Linux TCP skips.
