# Piece storage (H1.4)

`peer/storage.py` implements the file store from `MASTER_PLAN.md` section 7.5.
It assembles blocks on disk, verifies completed pieces with the metadata's
SHA-256 hashes, and exposes verified ownership for the wire bitfield and
rarest-first selector. It does not connect to peers or schedule downloads.

## Open a store

```python
from peer.metadata import load_metadata
from peer.storage import PieceStore

metadata = load_metadata("testdata/metadata.json")
with PieceStore("testdata/download/payload.bin", metadata) as store:
    print(store.missing_blocks(0))  # (offset, length) pairs to request
    print(store.left)              # bytes not yet in verified pieces
```

| Mode | Behavior |
|---|---|
| `create` (default) | Exclusively creates a new file and sets its length to the metadata length; refuses to overwrite existing files |
| `seed` | Opens an existing file read-only and verifies every piece before accepting it as a seed |
| `resume` | Opens an existing file for writing and rehashes every piece; valid pieces are retained and invalid pieces must be downloaded again |

Seed and resume files must have exactly the metadata length. Failed opens do
not truncate or rewrite them. Create mode makes missing parent directories.
Preallocation sets the logical file length with `truncate`; it does not promise
physical disk-space reservation. The new file initially owns no pieces.

For a seed:

```python
with PieceStore("testdata/payload.bin", metadata, mode="seed") as seed:
    block = seed.read_block(0, 0, 16384)
```

## Receiving and verifying blocks

`missing_blocks(piece)` supplies `(offset, length)` tuples: offsets start at
zero and advance by 16 KiB, with a shorter final block when necessary. A
default 256 KiB piece has 16 blocks. Write exactly those block boundaries:

```python
from peer.storage import PieceHashError

try:
    newly_verified = store.write_block(piece_index, offset, received_bytes)
except PieceHashError as error:
    # Future peer loop: emit hash_fail for error.piece and re-request its blocks.
    requests = store.missing_blocks(error.piece)
else:
    if newly_verified:
        # Future peer loop: emit have and broadcast HAVE(piece_index).
        pass
```

Writes accept immutable bytes and validate piece index, alignment, and exact
block length before touching disk. Blocks may arrive out of order or interleaved
across pieces. Duplicate offsets are ignored: the first accepted block wins,
and duplicates cannot increase completion counts or overwrite verified data.

`write_block` returns `True` exactly once when a piece newly verifies, and
`False` for a partial piece or duplicate. A hash mismatch raises `PieceHashError`
with a `.piece` attribute. All block receipt records for that piece are cleared,
and the entire piece becomes missing. Untrusted bytes can remain physically
on disk but are never served or advertised; successful retry overwrites all
of them before verification. Other verified pieces are unaffected.

Disk errors propagate as `OSError`. A final verification read error also clears
receipt tracking so the piece can be retried. Invalid operations raise
`StorageError`. The future peer loop owns connection handling and retry policy.

## Sharing and progress

- `read_block(piece, offset, length)` reads 1–16384 bytes within a verified
  piece. It accepts arbitrary valid read offsets, but never crosses a piece
  boundary or serves an unverified piece.
- `owned_pieces` is a frozenset of verified piece indices.
- `started_pieces` is a frozenset of pieces with some received blocks awaiting
  verification; supply this to the rarest-first selector.
- `bitfield` returns the existing `peer.wire.Bitfield` value, ready for
  `encode_message(store.bitfield, num_pieces=metadata.num_pieces)`.
- `bytes_verified` counts actual bytes in verified pieces, including a short
  final piece. `left` is the remaining unverified byte count for tracker calls.
- `complete` is true only when all pieces verify. An empty file is complete
  with no pieces and `left=0`.

The store keeps receipt sets in memory and bytes on disk, hashing with at most
1 MiB read buffers. Resume does not trust old receipt history or file size
alone: it rehashes content, then requests every block of each unverified piece.

## Ownership and lifecycle

Methods are synchronous and must be called serially by the owning peer. If
using `asyncio.to_thread`, serialize operations on the same store. Do not modify
the file externally while the store is open; verification assumes exclusive
ownership of its content. Scheduling and event emission remain in `peer/node.py`.

Writes flush to the operating system before verification; there is no `fsync`
durability guarantee. Resume rechecks content after reopening. Use a context
manager or `close()` to release the file. Closing is idempotent; further I/O
operations fail cleanly. Property snapshots remain readable after closing.

## Tests

From the repository root in PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_storage.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **20 passed**. Tests cover default 16-block assembly, four randomized
arrival orders, duplicate protection, corrupted-block detection and retry,
short/empty files, block boundaries, bitfield compatibility, readonly seed
validation, preservation of existing files, resume, simulated disk-read failure,
and resource cleanup. Tests create their own temporary files; no manual dataset
generation or tracker server is needed.
