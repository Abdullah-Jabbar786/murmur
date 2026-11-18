# Running swarm algorithms (A2.1)

A2.1 integrates the existing rarest-first selector and choking policy into
`SwarmNode` in `peer/swarm.py`, used by the `peer.node swarm` command. It also
replaces sequential block requests with a bounded pipeline. No wire-format or
transport contract changes are required. The older single-download command is
still the H2.1 baseline.

## Test in VS Code PowerShell

From the repository root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_algorithms.py tests/test_swarm_full.py -v -p no:cacheprovider --basetemp="$testTemp"
```

No manual servers, generated dataset, or tracker are required. The algorithm
tests check live availability, partial-piece priority, exclusive reservations,
windows of 1/16/32 requests across pieces before any response, reverse-order
responses, timed optimistic rotation, four regular slots, upload/download rate
ranking, input validation, and CLI configuration. Whole-swarm acceptance runs
one seed and ten leechers in both swarm and client-server modes and verifies
hashes, payload totals, events, and cleanup.

The existing multi-peer regression tests also exercise corruption, partial
recovery, disconnects, request deadlines, and CHOKE with a late response.

## Selection and request lifecycle

Availability counts come from the latest BITFIELD/HAVE state of connected
neighbors. A new reservation prefers a partially received piece, then minimum
availability, with a private seeded RNG breaking ties. Complete and reserved
pieces are excluded. Only one connection owns a piece at a time; other
connections can download different pieces concurrently.

Each connection fills its request window with missing blocks, continuing into
another piece when all blocks of its current pieces are already requested.
Pending requests are keyed by piece index, offset, and length. Each has its own
deadline, so unrelated responses cannot extend a missing block's deadline.
Responses may arrive out of order; SHA-256 verification still gates every HAVE
and upload. A hash failure clears the piece and permits two retries per source
by default. With a pipeline, several pieces can fail before one exhausts retries.

CHOKE abandons unanswered requests and releases reservations. Matching late
responses are ignored. If the same block is requested again, the first matching
response satisfies the current request; the wire carries no request generation.
An allowance for each abandoned request remains until its extra reply arrives.
Repeated abandonments of one key count separately toward the limit of 64
unresolved abandoned requests per connection; exceeding it disconnects the peer.
Replies beyond this allowance remain protocol errors. `bytes_discarded` counts
the late block payload separately from accepted `bytes_received`, including
when it arrives before the worker consumes the accepted reply.
Timeout, disconnect, and shutdown cancel
pending work and release claims. Received partial blocks survive for retry from
another source. Unsolicited or mismatched responses terminate that session.

## Upload scheduling

The default policy selects four regular peers every 10 seconds plus one
optimistic peer every 30 seconds. Only interested peers with uploads allowed
are eligible. Regular slots use interval payload download rates while leeching
and payload upload rates after all pieces are verified. Rates are bytes changed
over monotonic elapsed time, converted to bits/second. The optimistic slot
rotates to a different eligible peer when alternatives exist.

A periodic task runs even without incoming messages. Interest changes,
departures, manual eligibility changes, and completion also refresh the policy.
Outgoing CHOKEs precede replacement UNCHOKEs. Client-server leechers remain
ineligible to upload, including after completion. `set_upload_allowed(id, True)`
makes a neighbor eligible; it does not bypass the slot limit.

Handshake frames and sends retain `io_timeout` (default 30 seconds). Established
idle readers stay open so choked peers can wait for optimistic turns; partial
frames on idle sessions also wait until connection/node shutdown. Outstanding
downloads retain independent block deadlines (default 15 seconds). All reader,
worker, policy, and telemetry tasks are canceled on shutdown.

## Configuration and observable evidence

The swarm CLI reads these keys from `config/default.json` or `--config path.json`:

| JSON key | Default | Meaning |
|---|---|---|
| `pipelining_depth` | 16 | Outstanding requests per connection; integer 1..32 |
| `unchoke_slots` | 4 | Regular upload slots; nonnegative integer |
| `choke_interval` | 10 | Positive finite seconds between regular reviews |
| `optimistic_unchoke_interval` | 30 | Positive finite seconds between optimistic rotations |
| `seed` | 0 | Integer seed for selection ties and optimistic choices |

Python callers use the same keyword names on `SwarmNode`, except JSON `seed`
maps to `random_seed`. Reproducible ties require the same seed and sequence of
availability snapshots; asynchronous scheduling can change that sequence.
Distinct per-peer seeds can reduce identical initial choices. The CLI still
uses TCP regardless of the config's `transport` key.

Add `--run-dir` to record the additive `piece_select` event with `piece`,
`availability`, `started`, and source `peer`. Existing `choke`/`unchoke` events
record sent slot changes. The three-terminal demo in [multi-peer.md](multi-peer.md)
uses these algorithms automatically.

A2.1 supplies Loopback correctness evidence and localhost TCP regressions.
Tracker discovery, routed TCP acceptance, captures, experiment runs, and
harness/dashboard integration remain separate work. Gate S2 is not complete.
