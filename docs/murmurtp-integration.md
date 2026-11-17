# MurmurTP Stage 7: public transport, events and swarm selection (A3.2)

`MurmurTransport` now implements Contract 2 on top of Stages 1..6. It is
available from `murmurtp.transport` and implemented in `murmurtp/api.py` to avoid
circular imports through the earlier stage classes. `MurmurConnection` implements
the Connection ABC. Earlier staged transports remain available for regression.

The local A3.2 implementation is complete; review, merge and Linux CI are pending.
A3.3 routed-topology acceptance and Gates S1/S2/S3 still require their separate
network, tracker-discovery, experiment and dashboard evidence.

## Select the transport

The `peer.node swarm` command now reads `transport` from its JSON configuration:

```json
{"transport": "murmurtp", "mode": "swarm"}
```

Change `transport` to `tcp` to use kernel TCP with the same swarm implementation.
`config/default.json` now defaults to TCP, preserving the CLI's previous real
network behavior. The old `loopback` value was a bootstrap default that the CLI
previously ignored. Loopback remains supported by `core.transports.create_transport`
for in-process tests; the CLI rejects it because separate processes cannot share
an in-memory registry. It never silently substitutes TCP for an invalid selection.

With no explicit `--port`, TCP uses `peer_port_tcp` (default 6881), while MurmurTP
uses `peer_port_udp` (default 6882). `--port` overrides either, including port zero
for an ephemeral local listener. `--peer` and `--origin-seed` must identify the
remote endpoint's selected transport port. Distribution mode and transport are
independent: both swarm and client-server modes work with MurmurTP.

The historical `peer.node seed` and `peer.node download` commands remain TCP-only
H2.1 demos. Use `peer.node swarm` for transport selection. Existing `SwarmNode`
Python callers that omit `transport` still get TCP.

Optional JSON settings:

| Key | Meaning |
|---|---|
| `murmurtp` | Object of validated `FlowControlConfig` settings; omitted uses protocol defaults |
| `loss_shim` | Object of `LossConfig` settings for outgoing UDP impairment; omitted is no impairment |
| `seed` | Integer for repeatable shim randomness and existing swarm tie-breaking |
| `tcp_congestion_control` | Optional OS TCP algorithm; explicit selection still requires Linux |

The factory validates selection/configuration before the CLI creates output files.
Local tests explicitly override timing for speed; real experiment settings should
be recorded with results. This change does not run an experiment automatically.

## Public Python API

```python
from murmurtp.flow import FlowControlConfig
from murmurtp.transport import MurmurTransport
from peer.swarm import SwarmNode

node = SwarmNode(store, transport=MurmurTransport(config=FlowControlConfig()))
# Await node.start(), node.connect(), node.wait_complete(), then node.close().
# The caller owns store and closes it after the node.
```

The transport owns one IPv4 UDP socket, connections and protocol tasks. Sends
apply backpressure through ACK completion; callers must concurrently consume
incoming data for large duplex streams. Reads preserve byte order without
message boundaries. FIN gives stable EOF after buffered data drains. Connection
cleanup is idempotent even after reset/abort, while send/recv still raise delivery
errors. Endpoint shutdown releases UDP sockets, timers and owned tasks.

The frozen v1 lifecycle allows one session per remote address for an endpoint's
lifetime. Reconnect with fresh endpoints; this step does not add a nonce or change
the wire protocol. Both peers must use the same transport.

## Contract 3 events

The existing `PeerEvents` sampler reads the public connection's synchronous
`stats()` once per second by default and emits `transport` records through
`EventLog`. There is no second transport-owned sampler. Records include
`proto="murmurtp"`, `cwnd_bytes`, `rtt_ms`, `retx`, `bytes_in_flight`, and the
existing peer/connection identifiers. RTT is `null` before an eligible sample.

Closing samples are retained in the final summary, with retransmissions counted
once per connection. Peer payload counters count file bytes; transport byte
counters additionally include peer-protocol framing, so they are different
quantities. Use separate `--run-dir` directories for peers on the same host to
avoid writing the same node-named log file.

## Swarm ordering fix

The larger UDP acceptance test exposed a race when a HAVE arrived while sending
NOT_INTERESTED under backpressure. The download worker could request the new
piece before announcing INTERESTED again. Request admission now requires the
local interested state; the next loop pass announces interest first. A dedicated
test injects this exact ordering, retaining strict receiver protocol validation.

## Verify in VS Code PowerShell

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_transport.py tests/test_swarm_full.py tests/test_swarm_algorithms.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **41 passed**. The focused run passed in 45.15 seconds on Windows;
runtime varies by machine. Tests require no manual peers, topology or dashboard.

- Public duplex stream, stats, graceful close and reset cleanup.
- Transport/configuration validation, selected ports and real CLI downloads over
  TCP and MurmurTP, including final summary on cancellation.
- Same three-node relay scenario over Loopback and MurmurTP; UDP uses 5% loss,
  5% reorder and 1% duplication. Client-server mode preserves zero leecher uploads.
- Identical one-seed/ten-leecher scenarios on Loopback and MurmurTP, with independent
  piece/whole-file hashes for 2 MiB + 37 bytes, exact payload accounting, metrics
  and cleanup. The two UDP cases took 19.36s and 18.32s in the focused run.
- Existing selection/choking/pipeline checks and the new interest-order regression.

This is localhost integration evidence. It does not replace the routed 5%-loss
A3.3 acceptance, 64 MiB experiments or TCP fairness comparisons.

Full Windows/Python 3.14 regression: **523 passed, 2 existing Linux TCP skips**
in 182.23 seconds with warnings treated as errors. Nine changed Python files
pass Python 3.10 syntax checks, and Git whitespace checks pass. GitHub's actual
Linux/Python 3.11 run remains pending after pushing.
