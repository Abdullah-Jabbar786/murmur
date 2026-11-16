# Peer event logging (H2.4)

The current `SwarmNode` peer path emits Contract 3 events through the existing
`core.events.EventLog`. `peer/telemetry.py` handles periodic samples and final
accounting; `peer/swarm.py` supplies lifecycle and piece events. Logging works
in both swarm and client-server modes, with Loopback and TCP transports.

## Enable logs

Add `--run-dir` to a `peer.node swarm` command:

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --mode seed --metadata testdata/h22/metadata.json --file testdata/h22/payload.bin --port 6881 --run-dir results/h24-demo/seed
```

For a leecher, use another terminal and a different log directory:

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --metadata testdata/h22/metadata.json --file testdata/h22/events-download.bin --port 6882 --peer 127.0.0.1:6881 --run-dir results/h24-demo/leecher
```

These examples reuse the dataset from [multi-peer.md](multi-peer.md). The
download output must be new, or explicitly use `--mode resume`. For H2.3, add
the same `--run-dir` option to the client-server commands in that guide.

The commands write `events-127.0.0.1.jsonl` in their respective directories.
Each event is one JSON object, followed by a newline and flushed immediately.
View an active log in another PowerShell terminal:

```powershell
Get-Content results/h24-demo/leecher/events-127.0.0.1.jsonl -Wait
```

Ctrl+C ends the viewer. Separately stop the peer with Ctrl+C to write its final
summary. File transfer completion alone does not stop a serving peer, so its
summary appears at shutdown, not immediately after the completion message.

On localhost, use separate directories for each peer because they share an IP.
In the routed lab, distinct peer IPs can share one run directory. The log's
`node` field is the CLI `--host` address; use the actual node IP in lab runs.
Use a fresh run directory for a fresh experiment. EventLog appends, so reusing
the same file retains previous lifecycles and summaries. Generated JSONL under
`results/` is already ignored by Git.

Logging is opt-in via `--run-dir` or the Python `event_log` argument. The older
H2.1 `seed`/`download` demonstration commands remain unchanged; use `swarm` for
logged peer runs. Tracker/harness instrumentation is outside this peer task.

## Events and counters

Every row contains `t`, `node`, and `type`, using the existing Contract 3 names:

| Event | Meaning |
|---|---|
| `join` | Peer starts; includes mode, origin_seed and initial_complete |
| `have` | A verified piece is present; includes piece and initial |
| `hash_fail` | A completed piece failed SHA-256; includes piece |
| `choke` / `unchoke` | A CHOKE/UNCHOKE message was successfully sent to a neighbor |
| `complete` | All pieces are verified; emitted once per node lifecycle |
| `rate` | Payload down_bps/up_bps, sampled every second by default |
| `transport` | Per-connection proto, cwnd_bytes, rtt_ms, retx and bytes_in_flight |
| `summary` | Final bytes_down, bytes_up, retx, hash_fails and complete |

Initial verified pieces, including resumed pieces, emit `have` with
`initial=true`. A seed or already-complete resume emits `complete` with
`initial=true` at startup. A freshly verified download emits `initial=false`.
These markers prevent initial seed/resume state from being mistaken for newly
downloaded data. A failed piece is never logged as newly owned until it verifies.

Choke events describe outgoing messages, including reasserting CHOKE in
client-server mode. `peer` remains the remote IP; additional `peer_port` and
`peer_id` identify neighbors when available. Transport samples have a local
`connection_id` to distinguish reconnects and multiple connections to one IP.

Payload counters follow the node's existing accounting. Downloads include
accepted blocks that later fail piece hashing and their retries. Uploads count
block payload accepted by the send operation. They exclude protocol framing,
handshakes, TCP/IP overhead, and rejected late blocks. Consequently a
client-server leecher has zero payload upload even though it sends control
messages. Rates use monotonic elapsed time and convert byte deltas to bits/s.

The sampler produces one-second rate and per-live-connection transport samples.
There are also initial/final snapshots, so a short transfer still has metrics.
Sampling is best effort on the asyncio loop, not a hard real-time deadline.
Connection-close snapshots are retained in summary accounting. Cumulative
retransmission counters are counted once per connection, never summed across
repeated samples. Unavailable metrics stay JSON `null`; if any connection's
final retransmission count is unavailable, summary `retx` is also `null`.
Loopback retransmissions are zero. Windows TCP metrics normally remain unknown.

`summary` is emitted once during normal close, including timeout/error cleanup
and cancellation that reaches the close path. A partial transfer has
`complete=false` and no false completion event. Force-killing a process or
losing power cannot guarantee a final summary. Log write failures are reported
by `wait_complete()` or `close()` rather than silently treated as successful
logging; socket cleanup still runs. A failed writer cannot promise a summary.

## Python integration

```python
from core.events import EventLog
from peer.swarm import SwarmNode

with EventLog("10.0.1.11", "results/run-01") as log:
    node = SwarmNode(store, event_log=log)
    try:
        await node.start(("10.0.1.11", 6881))
        await node.connect(seed_address)
        await node.wait_complete(timeout=1800)
        # Keep the peer running here if it should continue serving neighbors.
    finally:
        await node.close()
```

The caller owns the store and EventLog. Keep both open until node.close()
finishes. A shared `MURMUR_T0` epoch can align event timestamps across processes;
without it, each logger uses elapsed time from its own creation. Rate math uses
its own monotonic clock, independently of wall-clock timestamp alignment.
`sample_interval` is an API override used for fast tests; production default is
one second. Full dashboard/aggregation tooling remains later work.

## Tests

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_events.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **11 passed**. Tests validate schemas against real transfers on
Loopback and TCP, client-server counters, corrupt-piece retries, one-second
idle sampling, deterministic rate math, cumulative/unknown retransmissions,
summary uniqueness, partial shutdown, logging failures, and CLI cancellation
with flushed output and released file handles. No manual servers are required.
