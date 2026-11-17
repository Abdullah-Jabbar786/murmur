# MurmurTP Stage 4: adaptive RTT/RTO (first part of A3.1)

`murmurtp/rto.py` provides a standalone `RtoEstimator`.
`murmurtp/adaptive.py` adds `AdaptiveRtoConfig`, `AdaptiveRtoConnection` and
`AdaptiveRtoTransport` on top of Stage 3's selective repeat. Earlier transports
remain available for comparison. The v1 wire header is unchanged.

This completes the timing portion of A3.1, merged in PR #26 (`5f74697`).
[Stage 5](murmurtp-stage5.md) adds slow start, AIMD and congestion-window
responses. Stage 4 itself still uses the fixed 1..32 packet window;
flow control, final MurmurTransport/swarm selection, topology and experiments
are separate work. This does not complete Gate S3 or all of A3.1.

## Timing rules

The defaults follow MASTER_PLAN section 6.5 (seconds internally):

```text
initial RTO = 1 second
first sample R: SRTT = R; RTTVAR = R / 2
later samples: RTTVAR = .75 * RTTVAR + .25 * abs(old SRTT - R)
               SRTT = .875 * old SRTT + .125 * R
RTO = clamp(SRTT + max(.01, 4 * RTTVAR), .2, 5)
timeout: RTO = min(2 * RTO, 5)
```

Samples use the local monotonic send-time map, never the header timestamp.
Each newly cumulatively acknowledged or SACKed DATA packet contributes at most
one sample. A later cumulative ACK does not sample a previously SACKed packet
again. Karn's rule excludes packets retransmitted for either timeout or a SACK
gap; fast retries do not themselves double the RTO.

Each outstanding packet retains its own deadline. New packets and retransmissions
use the current connection estimate. Receiving an ACK does not reset the deadlines
of other outstanding packets. A timeout round doubles the connection RTO once,
including when timers for the same original window expire in separate callbacks.
Packets record their transmission round so a burst of 16 expired timers does
not immediately apply 16 doublings. A subsequent retry timeout backs off again.
A fresh, unambiguous sample recomputes the estimate and can lower the RTO.

FIN retries use the learned RTO with backoff and the cancellation-safe ACK wait.
The inherited close lifecycle waits twice the last FIN RTO in TIME_WAIT.
Handshake retries retain the configured initial timing. Handshake and FIN ACKs
do not contribute DATA RTT samples. Inactivity and endpoint cleanup still apply.

## Usage

```python
from murmurtp.adaptive import AdaptiveRtoConfig, AdaptiveRtoTransport

config = AdaptiveRtoConfig(window_packets=16)
server = AdaptiveRtoTransport(config=config)
client = AdaptiveRtoTransport(config=config)
# Await listen/connect/accept/send/recv/close as for the earlier stages.
# Always close both endpoints in finally.
```

`stats()` reports `stage=4`, `rtt_ms` (SRTT), `rttvar_ms`, `rto_ms` and
`rtt_samples`, alongside existing payload, retransmission and scoreboard metrics.
RTT and its variation are `None` before the first eligible DATA sample.
`cwnd_bytes` remains `None` until congestion control is implemented.

Configuration validates positive finite timing values and
`min_rto <= initial_rto <= max_rto`. Shorter bounds in local tests are deliberate
overrides, not the production defaults or experiment settings.

## Verify in VS Code PowerShell

From the project root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_rto.py tests/test_murmurtp_cancellation.py tests/test_murmurtp_properties.py -v -p no:cacheprovider --basetemp="$testTemp"
```

No manual peers, dataset, dashboard, topology or Ubuntu setup is needed.
Expected with the Stage 5 extensions: **59 passed** (41 timing cases, twelve
property cases and six deterministic cancellation cases). At the Stage 4 commit,
the full Windows/Python 3.14 suite passed
with **454 passed, 2 existing Linux TCP skips** in 116.59 seconds, treating
warnings as errors. Python 3.10 syntax checks passed. See the Stage 5 guide for
current validation; no independent local Linux runtime run is claimed.

The tests cover exact estimator vectors and bounds, invalid inputs, Karn's rule
for both retry types, SACK/cumulative ACK deduplication, staggered timeout rounds,
unchanged in-flight deadlines, lost DATA and FIN recovery, stable EOF and
resource cleanup. A real UDP case increases the path delay and requires the
measured SRTT and RTO to rise. Randomized duplex and total-loss cases cover all
five transports, with deterministic partial/final ACK cancellation tests on
Stages 3..5. These localhost checks establish correctness, not routed RTT
accuracy, congestion fairness or experiment performance.
