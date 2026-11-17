# MurmurTP Stage 2 — A2.2, first step

`murmurtp/window.py` adds `SlidingWindowConfig`, `SlidingWindowConnection`, and
`SlidingWindowTransport`. It sends several DATA packets before waiting for
acknowledgments, processes cumulative ACKs, and buffers bounded out-of-order
arrivals. The Stage 1 classes remain available for regression and comparison.

This is the first step of A2.2. The separate [Stage 3 implementation](murmurtp-stage3.md)
now supplies SACK, selective retransmission and the 64 MiB impairment acceptance.
The frozen v1 header and handshake/FIN formats are unchanged.

## Run the tests in VS Code PowerShell

From the project root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_window.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **21 passed**. Tests start their own UDP endpoints on localhost with
ephemeral ports, generate data in memory, and shut down their endpoints. No
Ubuntu, topology, tracker, dashboard or manual swarm is required.

Coverage includes:

- Windows of 1, 4 and 32 packets; a prefix ACK frees slots immediately without
  waiting for the rest of the original window.
- Reverse-order arrivals, duplicates and a missing middle packet, with exact
  byte-stream recovery and cumulative ACK validation.
- A receive buffer smaller than the send window, including a missing head
  packet, bounded buffering and recovery when the reader drains it.
- Corrupt packets, incorrect connection IDs, out-of-window DATA, impossible
  future ACKs and delayed handshake ACKs.
- Handshake/FIN loss, duplex transfer, repeated sends, cancellation,
  inactivity timeout, sequence exhaustion and endpoint cleanup.
- **16 MiB over real localhost UDP with 5% seeded outgoing loss at both
  endpoints**, independently verified by SHA-256. The initial Windows run
  completed this test in approximately 10.50 seconds.
- A separate 64 KiB comparison with 10 ms outgoing delay on each endpoint:
  Stage 2 finishes faster than Stage 1 with the same payload and delay. This
  demonstrates pipelining locally, not routed-network throughput or E1–E5 results.

The initial focused suite passed in 13.66 seconds. Timings vary by machine.

## Behavior and bounds

`window_packets` defaults to **16** and accepts integers **1..32**. It limits
unacknowledged DATA packets per connection. Each DATA payload is at most 1200
bytes. The sender retains at most one window of payload chunks internally;
the caller still owns the complete bytes object passed to `send()`.

`cum_ack=K` acknowledges DATA packets below K. The sender discards those
outstanding entries and immediately fills the freed slots. Duplicate, stale,
and beyond-sent cumulative ACKs do not acknowledge new data. Handshake ACKs
remain separate from DATA/FIN ACKs.

The receiver accepts DATA within its configured packet horizon, keeps the
first copy of each packet, and exposes bytes only after all preceding packets
have arrived. Total in-order and out-of-order payload bytes share
`receive_buffer` (default 64 MSS). Future packets must leave one MSS available
for the missing head packet; this prevents a full reorder buffer from blocking
the packet needed to advance the stream. Far-future packets, duplicates and
packets that cannot fit receive the current cumulative ACK without being added.

On timeout, Stage 2 retransmits **all still-unacknowledged packets**. Some may
already be buffered by the receiver. SACK will eliminate those redundant
retransmissions in the next step. The header's SACK mask remains zero here.
Timeouts back off from `initial_rto` to `max_rto`; cumulative progress restarts
the window timer. This is not yet RTT-based adaptive timing or congestion control.

`send()` serializes concurrent callers and waits for cumulative acknowledgment
of the supplied bytes. Read concurrently with large sends. Cancellation or a
failed send aborts the connection because a prefix may already have arrived.
FIN follows acknowledged DATA; EOF is returned after buffered bytes are read.
Sequence wrap is unsupported, and sending reserves a sequence number for FIN.

Endpoint binding, session demultiplexing, handshake retries, FIN exchange,
TIME_WAIT, and shutdown reuse Stage 1. Its fresh-endpoint reconnect limitation
still applies. Both endpoints should use the same window setting for these tests.

## Python example

```python
import asyncio
from murmurtp.window import SlidingWindowConfig, SlidingWindowTransport

async def main():
    config = SlidingWindowConfig(window_packets=16)
    server = SlidingWindowTransport(config=config)
    client = SlidingWindowTransport(config=config)
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        payload = b"window demo" * 10000

        async def receive():
            result = bytearray()
            while len(result) < len(payload):
                chunk = await incoming.recv()
                if not chunk:
                    raise EOFError("incomplete transfer")
                result.extend(chunk)
            return bytes(result)

        _, received = await asyncio.gather(outgoing.send(payload), receive())
        assert received == payload
        await outgoing.close()
        assert await incoming.recv() == b""
        print(outgoing.stats())
    finally:
        await asyncio.gather(server.close(), client.close())

asyncio.run(main())
```

## Metrics and remaining work

`stats()` reports `stage=2`, `window_packets`, `pending_packets`, total
`buffered_bytes`, and `out_of_order_packets`/`out_of_order_bytes` alongside
Stage 1's payload counters, retransmissions and outstanding payload bytes.
`cwnd_bytes` and `rtt_ms` remain `None`: a fixed send window is not a measured
congestion window. Advertised free capacity includes the reorder buffer, but
sender-side advertised-window control and zero-window probes are later work.

SACK/selective retransmission are available in the separate Stage 3 transport.
Adaptive RTO, congestion control, complete flow control, swarm transport
selection and routed Linux acceptance remain pending.
