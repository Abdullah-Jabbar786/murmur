# Multi-peer transfers (H2.2)

`peer/swarm.py` adds `SwarmNode`, which downloads from multiple neighbors and
uploads verified pieces at the same time. `peer/node.py` provides the new
`swarm` command alongside the H2.1 `seed` and `download` commands. The same
session logic works with LoopbackTransport and TcpTransport.

## Automated tests

From the project root in PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_multi_peer.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **19 passed**. No manual dataset, tracker, or server is needed.
The tests prove that a third peer obtains every byte through a middle peer
without connecting to the original seed, on Loopback and real TCP. They also
test three incomplete peers exchanging complementary pieces.

Other cases cover multiple sources without duplicate downloads, simultaneous
connections, interest/choke transitions, late blocks after CHOKE, corruption
retry exhaustion and replacement sources, partial-piece recovery, request
timeouts, invalid messages, shutdown, and HAVE updates for pieces finished
during a handshake. A separate CLI process downloads and continues relaying.

## Three-terminal TCP demo

Run all commands from the project root. Generate an 8 MiB dataset once:

```powershell
.\.venv\Scripts\python.exe -m peer.metadata --size 8MiB --seed 42 --output testdata/h22/payload.bin --metadata testdata/h22/metadata.json
```

Reuse these files if they already exist; generation refuses to overwrite them.

**Terminal 1: original seed**

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --mode seed --metadata testdata/h22/metadata.json --file testdata/h22/payload.bin --port 6881
```

**Terminal 2: middle peer**

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --metadata testdata/h22/metadata.json --file testdata/h22/middle.bin --port 6882 --peer 127.0.0.1:6881
```

**Terminal 3: last peer, connecting only to the middle peer**

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --metadata testdata/h22/metadata.json --file testdata/h22/last.bin --port 6883 --peer 127.0.0.1:6882
```

All three ports are TCP on this machine. Start in order; the last peer may join
while the middle peer is downloading or after it finishes. Each prints
`Verified 32 pieces; continuing to serve: ...` on completion. Unlike the H2.1
`download` command, `swarm` keeps serving until Ctrl+C. Shutdown prints payload
counts and recent session diagnostics.

Compare files from another terminal after both downloads finish:

```powershell
Get-FileHash testdata/h22/payload.bin, testdata/h22/middle.bin, testdata/h22/last.bin -Algorithm SHA256
```

All hashes must match. Stop each node with Ctrl+C. Outputs are under Git-ignored
`testdata/`. Use `--mode resume` for an existing partial file; default `create`
refuses to overwrite. `--mode seed` requires a fully verified existing file.
Repeat `--peer IPv4:port` for multiple neighbors. Listening port zero selects
an OS-assigned port and prints it; remote peer ports must be nonzero.

## Session behavior

Sessions validate info_hash and peer ID, send one initial BITFIELD, and maintain
availability plus local/remote interest and choke state. Verified pieces trigger
HAVE to every neighbor, including catch-up HAVE messages for pieces finished
while the initial bitfield was in flight.

Each connection has one reader and one download worker; sends are serialized.
The worker sends INTERESTED while the neighbor has missing pieces, waits for
UNCHOKE, and keeps at most one block request in flight. When no useful pieces
remain it sends NOT_INTERESTED. Each piece is reserved to one source at a time;
different neighbors can supply different pieces concurrently. The baseline
finishes partial pieces first, then chooses ascending piece indices.

Uploads use verified data only. The default policy unchokes interested peers;
timed slot/rate selection is still A2.1. API `set_upload_allowed(peer_id, bool)`
explicitly sends CHOKE/UNCHOKE. CHOKE abandons an outstanding request and releases
its reservation. A late matching block is ignored; requests crossing CHOKE in
transit are discarded by the uploader. Abandoned-request bookkeeping is bounded
at 64 unresolved keys per connection. Unsolicited/mismatched blocks close the
session rather than being written.

Hash failure makes the whole piece requestable again. Two retries per piece
per connection are allowed by default, then that source is disconnected.
Disconnects, timeouts, and cancellation release reservations. Partial blocks
remain usable, subject to final SHA-256 verification. If no viable source
remains, completion times out; it is never reported as success.

Simultaneous cross-connections converge on one link per peer ID, favoring the
connection initiated by the smaller ID. A failed connection does not stop the
listener or other sessions. The last 32 session errors are retained.

## Python API and lifecycle

```python
from core.loopback import LoopbackRegistry, LoopbackTransport
from peer.swarm import SwarmNode

# Share one registry among all in-process nodes.
registry = LoopbackRegistry()
node = SwarmNode(store, transport=LoopbackTransport(registry))
try:
    await node.start(("127.0.0.1", 7001))
    await node.connect(("127.0.0.1", 7000))
    await node.wait_complete(timeout=30)
    # Keep running here if neighbors still need pieces.
finally:
    await node.close()
```

Omit `transport` for real TCP. The caller owns the store and closes it after
the node; the node owns its transport and sessions. `peer_ids` and
`remote_pieces(peer_id)` return immutable snapshots. `bytes_received`,
`bytes_sent`, and `hash_failures` count payload activity; rejected late blocks
are not counted as received. Closing is idempotent and wakes incomplete waiters
with an error.

Use one asyncio loop and serialize external store access. Storage I/O is
synchronous. Defaults are 30 seconds per frame/send and 15 seconds per block
request. Idle connections can expire after the frame timeout; the listener
remains available. CLI `--total-timeout` bounds the completion wait after initial
connects (default 1800 seconds), not the subsequent period of serving.

## Remaining work

Connections are explicit. Tracker discovery, reconnect scheduling, rarest-first
and pipelining integration and timed choking remain separate work.
H2.4 [peer event logging](peer-events.md) is available through `--run-dir`.
H2.3's optional [client-server mode](client-server-mode.md) restricts
leechers to origin-only downloads and zero payload uploads. These three-peer
tests do not establish the ten-
leecher Gate S2 or the routed topology/evidence required for Gate S1.
