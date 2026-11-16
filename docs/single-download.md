# Single seed-to-leecher TCP download (H2.1 / I1)

`peer/node.py` connects the existing TCP transport, wire codec, metadata, and
piece store into a working download. H2.1 is listed under Sprint 2, but its
I1 acceptance test is also a prerequisite for Gate S1 in `MASTER_PLAN.md`.
Routed topology and evidence are still separate requirements for that gate.

## Automated test

From the repository root in PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_single_download.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **21 passed**. The tests create their own files and TCP listeners.
No manual seed or tracker is required. The I1 test transfers a full 64 MiB file
over real localhost TCP, independently recomputes all 256 piece hashes, and
compares whole-file SHA-256. Temporary source/download files use about 128 MiB
for this case and may be retained by pytest after success.

Other tests cover short/empty files, resume, corruption retry and exhaustion,
bad handshakes, missing pieces, duplicate bitfields, wrong blocks, early EOF,
requests while choked, timeouts, cancellation, seed shutdown, self connections,
and the actual download CLI. A total-deadline test keeps sending control
messages to ensure activity cannot postpone the overall timeout indefinitely.

## Manual demo in VS Code

Use two terminals, both in the project root. Generate the dataset once:

```powershell
.\.venv\Scripts\python.exe -m peer.metadata --size 64MiB --seed 42 --output testdata/i1/payload.bin --metadata testdata/i1/metadata.json
```

If these files already exist from a previous demo, reuse them. The generator
refuses to overwrite them. For a quicker demo, use `--size 8MiB` in a fresh
directory and adjust the following paths accordingly.

**Terminal 1 — start the seed:**

```powershell
.\.venv\Scripts\python.exe -m peer.node seed --metadata testdata/i1/metadata.json --file testdata/i1/payload.bin --host 127.0.0.1 --port 6881
```

Wait for `Seed listening on 127.0.0.1:6881 (256 verified pieces)`.
The seed checks the source file before listening and remains running until
Ctrl+C. A failed peer session does not stop the listener; recent diagnostics
are printed when the seed shuts down.

**Terminal 2 — download:**

```powershell
.\.venv\Scripts\python.exe -m peer.node download --metadata testdata/i1/metadata.json --file testdata/i1/download.bin --seed-host 127.0.0.1 --seed-port 6881
```

Expected for a fresh 64 MiB transfer:

```text
Verified 256 pieces; file: testdata/i1/download.bin
Payload bytes received: 67108864; hash failures: 0
```

Independently compare both files:

```powershell
Get-FileHash testdata/i1/payload.bin, testdata/i1/download.bin -Algorithm SHA256
```

Both hash values must match. Stop the seed with Ctrl+C when finished.
Everything under `testdata/` is already ignored by Git.

The download refuses to overwrite an existing output. To continue a partial
download, append `--resume` to the download command. Resume rehashes the existing
file and fetches only missing pieces. An already verified file (or empty file)
returns successfully without opening a TCP connection. For a new full transfer,
choose a new output path.

## Protocol behavior

1. Both peers send HANDSHAKE first, check the metadata `info_hash`, and reject
   a connection using their own peer ID.
2. Both send exactly one BITFIELD immediately after the handshake. This baseline
   requires the seed to advertise every piece the leecher still needs.
3. The leecher sends INTERESTED. The seed sends UNCHOKE; no block is requested
   before that. NOT_INTERESTED causes the seed to choke again.
4. The leecher requests one block at a time, checks the returned piece index,
   offset, and length against that request, then writes it through `PieceStore`.
5. Only a hash-verified piece triggers HAVE. A failed hash clears that piece's
   received-block history and causes the whole piece to be requested again.
6. When every piece is verified, the leecher sends NOT_INTERESTED, closes its
   connection, and exits. The seed remains available for later connections.

Default peer IDs are random 20-byte values. The Python API permits explicit
IDs for tests. Duplicate handshakes/bitfields and unsolicited or mismatched
blocks are protocol errors. The seed serves only verified data and rejects
requests while the session is choked or uninterested.

## Python API and lifecycle

- `SeedServer(store, peer_id=None, io_timeout=30)` requires an open, complete
  `PieceStore`. Call `await start((host, port))`, inspect `.address`, and always
  call `await close()`. Port zero is useful for tests. The caller closes the
  store after closing the server.
- `download_file(metadata, path, seed_address, resume=False, ...)` owns its
  download store and TCP transport and closes both on success, error, or
  cancellation. It returns a `DownloadResult` containing payload bytes received
  (including retries), hash-failure count, and total verified pieces.
- `serve_connection(connection, store, peer_id, ...)` is the session helper;
  its caller owns the connection and store.

Each send, receive, and connect has a 30-second timeout by default. Downloads
also have an 1800-second network-transfer deadline (`--total-timeout`). Startup
metadata loading and synchronous resume hashing happen before that deadline;
cleanup may add time after it expires. `--io-timeout` changes the I/O limit.
API `max_piece_retries=2` means at most three complete attempts per bad piece.
Failures keep the partial output for an explicit resume; they never report it
as a completed file. `PieceHashError`, `PeerProtocolError`, and transport/disk
errors propagate to API callers; the CLI prints an error and exits nonzero.

## Current scope

This section describes the original H2.1 commands. For the newer H2.2 `swarm`
command and bidirectional sharing, see [multi-peer.md](multi-peer.md).

This is a direct-address TCP baseline: it does not yet call the tracker, download
from several peers, run rarest-first/choking timers, pipeline requests, or turn
the completed leecher into a serving peer. Those behaviors belong to H2.2 and
A2.1 integration. Full event logging remains H2.4. The listener can service
separate sessions, but that does not establish the multi-peer swarm acceptance
gate. MurmurTP selection is also deferred.

Only one request is outstanding per connection, so high-latency performance
will improve substantially when pipelining is added. Storage calls are
synchronous and serialized by the event loop in this baseline. The localhost
test proves TCP transfer correctness; it does not prove routed-namespace or
Linux impairment results.
