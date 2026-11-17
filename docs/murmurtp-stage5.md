# MurmurTP Stage 5: Reno-style congestion control (A3.1)

`murmurtp/congestion.py` adds the standalone `RenoController`,
`CongestionConnection` and `CongestionTransport`. Stage 5 builds on Stage 4's
adaptive RTO and Stage 3's selective repeat without changing the v1 header.
Earlier transports remain available as baselines.

This implements the remaining congestion-control part of A3.1, merged in
PR #27 (`7d2fa45`). [Stage 6](murmurtp-stage6.md) adds flow control. Gate S3
still requires final transport and swarm integration, routed acceptance and
experiment evidence.

## Rules from MASTER_PLAN section 6.6

All internal congestion values count packets, including short final packets:

| Event | Change |
|---|---|
| Connection starts | `cwnd = 10`, `ssthresh = infinity` |
| Newly ACKed DATA, `cwnd < ssthresh` | `cwnd += 1` (slow start) |
| Newly ACKed DATA, `cwnd >= ssthresh` | `cwnd += 1 / cwnd` (additive increase) |
| SACK gap, first loss of a recovery period | `ssthresh = max(2, cwnd / 2)`; `cwnd = ssthresh` |
| New timeout round | `ssthresh = max(2, cwnd / 2)`; `cwnd = 1`; inherited RTO doubles |

Cumulative ACKs and SACK bits grow the window once per newly acknowledged DATA
packet. Duplicates, invalid ACKs, handshake ACKs and FIN ACKs cannot grow it.
An ACK for a retransmitted packet still grows the window, while Karn's rule
excludes that packet from RTT estimation.

At loss detection, recovery records the exclusive end of DATA already sent.
Further SACK gaps can trigger selective retries, but cannot halve the window
again until a cumulative ACK reaches that boundary. The boundary does not move
when newer data is sent. Growth on newly ACKed packets continues according to
the project's rules; there is no classic Reno duplicate-ACK window inflation.
A timeout can reset the window during recovery. As in Stage 4, staggered timers
from one transmission round cause one RTO backoff and one congestion response.
Another retry timeout starts another response.

## Enforcing the window

A new packet is admitted only while both conditions hold:

```text
number of unsacked outstanding packets < floor(cwnd)
next sequence - cumulative ACK < configured window_packets
```

The second condition keeps the existing bounded sequence horizon (1..32,
default 16). Thus the starting burst is at most ten packets and may be smaller
when the configured horizon is smaller. SACKed successors stop consuming
congestion flight slots but still occupy the sequence horizon until the gap
is repaired. This is not yet full advertised-window flow control.

Loss detection runs before admitting new DATA. A shrinking window cannot undo
already transmitted packets; it blocks new sends until flight permits them.
Recovery retransmissions remain allowed, preventing a deadlock when the window
falls below the outstanding packet count. FIN uses Stage 4's adaptive retry
timing; the congestion controller governs DATA.

## Usage and metrics

```python
from murmurtp.adaptive import AdaptiveRtoConfig
from murmurtp.congestion import CongestionTransport

endpoint = CongestionTransport(config=AdaptiveRtoConfig(window_packets=32))
# Same async listen/connect/accept/send/recv/close interface as earlier stages.
# Always close endpoints in finally.
```

`stats()` reports `stage=5`, `cwnd_packets`, integer `cwnd_bytes` (`cwnd * 1200`),
`ssthresh_packets`, `recovery_end`, `fast_reductions` and `timeout_reductions`,
along with existing RTT, RTO, retransmission and payload metrics. Infinite
initial threshold is reported as `None` for JSON compatibility. Reduction
counters count congestion events; retransmission counters count packets.

## Verify in VS Code PowerShell

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_congestion.py tests/test_murmurtp_cancellation.py tests/test_murmurtp_properties.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected with Stage 6 extensions: **35 passed** (13 congestion tests, eight deterministic
cancellation cases and fourteen randomized property cases). No manual peers, Ubuntu, topology
or dashboard are required.

Verification at the Stage 5 commit on Windows/Python 3.14: **471 passed, 2 existing Linux TCP skips**
in 123.88 seconds for the full suite with warnings treated as errors. All five
changed Python files passed Python 3.10 syntax checks. See Stage 6 for the
current verification results; no independent local Linux execution is claimed.

The congestion tests check exact growth formulas, threshold transitions,
duplicate ACK suppression, recovery boundaries, multi-gap selective retries,
timeout rounds, packet/byte units and actual send admission. The acceptance
case sends **1 MiB over real localhost UDP with 2% outgoing loss on each endpoint**,
verifies SHA-256, payload counts, graceful EOF and cleanup, and requires multiple
window reductions with intervening growth. It writes `cwnd-2pct.csv` into that
test's temporary directory. Locate it after running with:

```powershell
Get-ChildItem -LiteralPath $testTemp -Filter cwnd-2pct.csv -Recurse
```

The trace captures every ACK-growth and loss-response callback. The test uses
1 ms shim delay and shorter RTO bounds (10 ms..1 s, initially 500 ms); these
are local correctness settings. The exact trace depends on OS scheduling even
with fixed seeds. It is evidence of congestion response, not a routed E4 result
or proof of TCP fairness. Python 3.11 execution remains for GitHub CI.
