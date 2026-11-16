# Peer message codec (H1.1)

`peer/wire.py` implements Contract 1 in `MASTER_PLAN.md` section 5.1.
It adds no fields or message types to the frozen wire format.

Each frame contains a four-byte big-endian length, one type byte, then its
payload. The length includes the type and payload, but excludes the prefix.
All integer fields use unsigned, big-endian u32 encoding.

| Type | Python value | Payload |
|---|---|---|
| 0 | `Handshake(info_hash, peer_id)` | `MURMUR01`, 32 hash bytes, 20 peer ID bytes |
| 1 | `Bitfield(frozenset(piece_indices))` | ceil(num_pieces / 8) bytes, piece 0 in the MSB |
| 2 | `Have(piece)` | piece index |
| 3 | `Interested()` | empty |
| 4 | `NotInterested()` | empty |
| 5 | `Choke()` | empty |
| 6 | `Unchoke()` | empty |
| 7 | `Request(piece, offset, length)` | three u32 fields |
| 8 | `Piece(piece, offset, data)` | two u32 fields followed by block data |

## Usage

```python
from peer.wire import Handshake, Bitfield, encode_message, read_message

# Piece count and binary info_hash come from trusted local metadata.
await connection.send(encode_message(
    Handshake(info_hash, peer_id), num_pieces=num_pieces))
await connection.send(encode_message(
    Bitfield(frozenset(verified_piece_indices)), num_pieces=num_pieces))
message = await read_message(connection, num_pieces=num_pieces)
```

`decode_message(frame, num_pieces=...)` decodes exactly one complete frame.
It rejects truncation and trailing bytes, including a second appended frame.
`read_message(connection, num_pieces=...)` reads one frame through the existing
`Connection.recv` interface, handling fragmentation and leaving subsequent
frames for the next call. It returns `None` for EOF between frames and raises
`WireError` for EOF inside a frame. Use only one reader per connection.

## Validation and responsibility boundaries

- The encoder validates immutable byte fields, field widths, hash/ID lengths,
  piece indices, and request/block sizes. Constructors are simple frozen data
  classes; validation occurs when encoding.
- The decoder rejects unknown types, incorrect payload sizes, bad handshake
  magic, out-of-range piece indices, and set bitfield padding bits. Encoded
  bitfields always have zero padding and decode to a frozenset usable by the
  rarest-first selector.
- Request lengths and block data contain 1 to 16,384 bytes, allowing a short
  final block. Offsets are u32 values. Storage and the peer loop must check
  actual piece boundaries and match incoming blocks to outstanding requests.
- Before reading a body, the stream helper checks its length against the
  largest valid frame for the trusted piece count: the larger of a handshake,
  a bitfield, and a full block message. For 256 pieces, this is 16,393 bytes,
  excluding the four-byte prefix. A zero-length frame is invalid.
- Session rules belong to the future peer loop: handshake first, matching
  `info_hash`, bitfield once after handshake, and no requests while choked.
  This codec does not establish a peer session or download a file.
- The caller supplies timeouts and closes the connection after malformed input,
  timeout, or cancellation. A partially consumed frame must not be resumed by
  starting another reader. Transport exceptions propagate to the caller.

## Verification

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_wire.py -v -p no:cacheprovider
```

Tests cover independent byte vectors for all nine types, every truncation of
those vectors, malformed payloads, MSB bitfields and padding, u32 and block
boundaries, fragmented/coalesced streams, EOF, oversized prefixes rejected
before body reads, and exchange through `LoopbackTransport`. Two deterministic
Hypothesis tests each run 150 examples for round trips and arbitrary payloads.
