# MurmurTP Stage 6: receive-window flow control (first part of A3.2)

`murmurtp/flow.py` adds `FlowControlConfig`, `FlowControlConnection` and
`FlowControlTransport` on top of Stage 5. It implements the receive-window rules
in MASTER_PLAN sections 6.3/6.4 and the fixed one-second probe interval in
murmurtp-spec section 5.1. The v1 header stays unchanged; Stages 1..5 remain
available for regression and comparison.

This completes the flow-control implementation portion of A3.2. Review and Linux
CI remain pending. The final `MurmurTransport` wrapper, transport-event integration,
swarm selection and routed acceptance are later steps; Gate S3 is not complete.

## Receive capacity and send admission

Receive slots are `floor((receive_buffer - readable_bytes - reordered_bytes) / 1200)`,
capped at 65,535 for the wire field. Both readable and reordered data count toward
the byte budget. Less than one MSS of free space advertises zero, even if a short
packet could fit. The existing receiver retains its missing-head reservation
and rejects data exceeding its byte budget or sequence horizon.

The client learns the initial window from SYN-ACK; the server learns it from
the final handshake ACK. Later valid DATA/FIN ACKs carry updates. A new DATA
packet is admitted only when:

```text
unsacked outstanding packets < min(floor(cwnd), peer_window_packets)
next sequence - cumulative ACK < configured window_packets
```

ACKs below the latest accepted cumulative position, beyond sent DATA/expected
FIN, or contradicting a retained SACK cannot change the window. Late handshake
messages cannot overwrite it. A pure window update can wake a blocked DATA
sender without increasing cwnd or contributing an RTT sample. FIN waiters wake
only for their matching ACK or abort, not a pure window update.

The frozen wire format has no window-update serial number. Updates at the same
cumulative ACK are accepted so application reads can reopen the window. Such
updates can reorder; conservative admission and receiver byte-budget checks
still bound memory, and probes repair a stale zero advertisement. The debug
timestamp is not repurposed as a reliable ordering field.

## Reader updates and zero-window probes

After an application read frees at least one additional advertised slot, the
receiver sends an ACK with its current cumulative ACK, bitmap and free slots.
Tiny reads that do not free a full slot do not generate additional window ACKs.

With a zero window the sender pauses new DATA and DATA retries. A waiting send
uses the existing cancellation-safe ACK wait and a persist deadline. Every
`probe_interval` seconds (default **1 second**) it sends an empty DATA packet
at its next sequence number. Existing receiver semantics reply with a current
ACK without delivering bytes or advancing the receive sequence.

Probes do not allocate a sequence number, create a flight, add payload or retry
counters, or alter congestion/RTT state. `window_probes` counts them separately.
Lost updates, probes and replies are recovered by subsequent periodic probes.
When the window reopens, paused packet deadlines restart from their existing
RTOs, so time spent waiting for buffer space is not charged as packet loss.

Repeated zero ACKs and probes do not refresh the inactivity timer. A connection
with no progress still aborts under the configured inactivity deadline (30 seconds
by default). Cancellation and endpoint shutdown wake blocked sends and clean up
their event waiters. Persist uses no detached background task. FIN does not
consume a receive slot and can complete while unread data fills the buffer;
buffered data remains readable before EOF.

## Usage and metrics

```python
from murmurtp.flow import FlowControlConfig, FlowControlTransport

endpoint = FlowControlTransport(config=FlowControlConfig(
    window_packets=16, receive_buffer=64 * 1200))
# Same async listen/connect/accept/send/recv/close interface as earlier stages.
# Always close the endpoint in finally.
```

`FlowControlConfig` extends the RTT/window settings with a positive finite
`probe_interval`. `stats()` adds `stage=6`, `peer_window_packets`,
`receive_window_packets` and `window_probes` alongside existing transport metrics.

## Test in VS Code PowerShell

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_flow.py tests/test_murmurtp_cancellation.py tests/test_murmurtp_properties.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **44 passed** (22 flow-control cases, eight cancellation cases and
fourteen randomized property cases). No topology, Ubuntu, dashboard or manual
peers are required. Focused tests cover asymmetric receive capacities, slow
readers, exact byte delivery and bounded buffers, stale/invalid ACKs, fixed probe
intervals, lost reopen updates and probe replies, zero-window cancellation,
shutdown/inactivity, partial-slot reads and FIN with a full buffer. Property tests
exercise duplex transfers with small buffers and loss/reorder/duplication.
Late window updates in TIME_WAIT must not restart the inactivity timer.

Short RTO/probe intervals in tests are explicit local overrides. These checks
establish localhost correctness, not routed performance or experiment evidence.

Windows/Python 3.14 validation: full regression run **496 passed, 2 existing
Linux TCP skips** in 136.14 seconds. Final review then added a TIME_WAIT idle-timer
guard and its regression; the final focused run passed **44 tests** in 31.75
seconds. Both runs treated warnings as errors. All five changed Python files
pass Python 3.10 syntax checks. Actual Linux/Python 3.11 verification remains
for GitHub CI after pushing.
