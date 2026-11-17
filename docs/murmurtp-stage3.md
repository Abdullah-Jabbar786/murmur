# MurmurTP Stage 3 — A2.2 SACK and selective repeat

`murmurtp/sack.py` adds `SelectiveRepeatConnection` and
`SelectiveRepeatTransport`. Stage 3 reuses Stage 2's fixed window and bounded
reorder buffer, and Stage 1's handshake, endpoint and FIN lifecycle. The earlier
transports remain available for comparison and regression tests.

This implements the remaining Stage 3 portion of A2.2. A2.3's separate
[Hypothesis transport properties](transport-properties.md) now exercise all
three stages and the separate [Stage 4 adaptive RTO transport](murmurtp-stage4.md).
Stage 3 retains its original fixed initial timers. [Stage 5](murmurtp-stage5.md)
adds congestion control. Full flow control and swarm transport selection remain
later tasks.

## Test in VS Code PowerShell

From the repository root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_sack.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **15 passed**. Tests open localhost UDP endpoints on ephemeral ports;
no topology, Ubuntu, tracker, dashboard, or manually started peers are needed.

The acceptance case transfers **64 MiB** with **5% loss, 5% reordering and 1%
duplication on both endpoints**, checks whole-file SHA-256 and exact payload
counts, observes each impairment, and verifies graceful EOF. It uses a
32-packet window, 20 ms initial retry timeout, 100 ms maximum timeout, and 5 ms
extra reorder delay to keep the local test fast. Those are test overrides,
not final experiment settings. The successful focused run took 19.03 seconds;
its acceptance case took approximately 17.76 seconds. Timings vary by machine.

Other tests verify bitmap offsets/rebasing, single and multiple missing packets,
selective timeout retries, SACK-gap recovery before RTO, lost fast retransmits,
duplicate ACK suppression, sequence-span bounds, buffer pressure, invalid and
stale ACKs, handshake ACK separation, ACK/FIN loss, duplex and repeated sends,
and cancellation/endpoint shutdown cleanup.

## What SACK means

The frozen v1 header is unchanged. `cum_ack=K` confirms every DATA packet below
K. SACK bit i confirms that packet **K + 1 + i** is buffered by the receiver.
The missing packet K is not represented by a SACK bit.

For example, if packet 0 is missing and packets 1 and 3 are buffered, an ACK
contains `cum_ack=0, sack_mask=0b101`. After packet 0 arrives, packets 0 and 1
become contiguous: the ACK becomes `cum_ack=2, sack_mask=0b1` for packet 3.
The receiver delivers bytes only in order and never delivers duplicates.

Only actually retained packets enter the bitmap. A packet dropped because the
buffer or receive horizon is full is not SACKed. Accepted out-of-order packets
remain buffered until they become contiguous or the connection is aborted.

## Sender scoreboard and recovery

The sender records cumulatively acknowledged and selectively acknowledged
packets separately. A SACKed packet loses its retry timer and is excluded from
outstanding payload bytes. Its retained entry is released when the cumulative
ACK passes it. Thus a missing head cannot cause the sender to extend its
sequence span beyond `window_packets`, even when all its successors are SACKed.

Each unsacked packet has an independent retry deadline and exponential backoff.
Acknowledgment of another packet does not reset its deadline. Timeout retries
resend only packets still missing; SACKed successors are not retransmitted.

The frozen spec's SACK-gap rule is also active: three distinct higher SACKed
packets trigger an early retransmission of a missing packet. This fast retry
is allowed at most once per packet; repeated copies of the same ACK cannot
create a retransmission storm. If the fast retry is lost, the packet's timeout
recovers it. This is loss recovery only; congestion-window reduction and Reno
behavior belong to A3.1.

Stale cumulative ACKs and ACKs beyond the sequence range actually sent are
ignored. Bitmap bits for unsent packets cannot acknowledge data. A cumulative
ACK that claims an already SACKed packet is now missing is rejected because
this receiver does not discard accepted reordered data. Handshake ACKs remain
separate from DATA/FIN acknowledgments.

## Python usage

Use the Stage 2 configuration with the Stage 3 transport:

```python
from murmurtp.sack import SelectiveRepeatTransport
from murmurtp.window import SlidingWindowConfig

config = SlidingWindowConfig(window_packets=16)
server = SelectiveRepeatTransport(config=config)
client = SelectiveRepeatTransport(config=config)
```

The listen/connect/accept/send/recv/close calls are the same as the full example
in [murmurtp-stage2.md](murmurtp-stage2.md); replace its transport class with
`SelectiveRepeatTransport`. Read concurrently with large sends. `send()` waits
for cumulative acknowledgment; an interrupted send aborts rather than reporting
success or reusing sequence numbers for different bytes.

The packet window defaults to 16 and supports 1..32. Sequence wrap is unsupported
and one sequence number is reserved for FIN. The fresh-endpoint reconnect
limitation and inactivity timeout from Stage 1 still apply.

## Metrics and limits

Alongside earlier metrics, `stats()` exposes:

| Key | Meaning |
|---|---|
| `stage` | 3 |
| `pending_packets` | Retained window entries, including SACKed successors |
| `sacked_packets` | Selectively acknowledged entries awaiting cumulative release |
| `unacked_packets` | Missing packets with active retry deadlines |
| `bytes_in_flight` | Payload bytes not yet cumulatively ACKed or SACKed |
| `fast_retx` | DATA retries triggered by the SACK-gap rule |
| `timeout_retx` | DATA retries triggered by packet deadlines |
| `retx` | All retransmissions, including handshake and FIN retries |

`cwnd_bytes` and `rtt_ms` remain unknown (`None`). The window is fixed, not
congestion-controlled, and the sender does not yet honor advertised receive
windows or run zero-window probes. This is local transport correctness evidence;
it does not establish routed swarm acceptance or experiment performance.
