# MurmurTP Stage 1 (A1.6)

`murmurtp/transport.py` provides `StopAndWaitTransport` and
`murmurtp/connection.py` provides `StopAndWaitConnection` and `StopAndWaitConfig`.
These are experimental Stage 1 classes. The final `MurmurTransport`, swarm
configuration switch, SACK, adaptive RTO, and congestion control come later.
The existing header codec and seeded outgoing loss shim are reused unchanged
on the wire; the shim gains `abort()` for endpoint cleanup.

## Local example

```python
import asyncio
from murmurtp.transport import StopAndWaitTransport

async def main():
    server, client = StopAndWaitTransport(), StopAndWaitTransport()
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        await outgoing.send(b"hello over UDP")
        print(await incoming.recv())
        await outgoing.close()  # FIN exchange; peer automatically replies with FIN.
        await incoming.close()
        print(outgoing.stats())
    finally:
        await asyncio.gather(server.close(), client.close())

asyncio.run(main())
```

Endpoints use one IPv4 UDP socket each. `listen` accepts an IPv4 literal and
port, including port zero for tests. `connect` binds an ephemeral socket if
needed. A bound listener can also initiate connections. Session routing checks
both source address and connection ID; several remote peers share the socket.

## Reliability and lifecycle

- SYN uses sequence zero and connection ID zero. The listener assigns a
  nonzero ID in SYN-ACK. Only the final handshake ACK makes a server connection
  available to `accept()`. Duplicate handshakes never create duplicate accepts.
- The diagram's final handshake ACK uses `seq_num=1, cum_ack=1`. Stage 1 DATA
  and FIN ACKs use `seq_num=0`; their `cum_ack` is the next expected packet.
  This keeps delayed handshake ACKs from acknowledging DATA packet zero.
  A repeated SYN-ACK gets another final handshake ACK if that ACK was lost.
- Each direction permits one unacknowledged DATA packet, with at most 1200
  payload bytes. DATA starts at sequence zero. An in-order packet is appended
  once; duplicates and future packets receive the current cumulative ACK.
  Corrupt datagrams and mismatched connection IDs are discarded.
- `send(bytes)` serializes callers and waits for each chunk's acknowledgment.
  `recv(n)` returns up to n bytes, assembling the stream across datagrams. Read
  concurrently with large sends so the receive buffer can drain. Positive reads
  return empty bytes only after all buffered data preceding FIN is consumed.
- Retry timeout starts at 1 second and doubles up to 5 seconds. There are five
  SYN retries after the initial transmission. DATA and FIN retry until progress
  resumes or the 30-second inactivity deadline aborts the session. Each new
  packet restarts the base timeout; RTT estimation is not implemented yet.
- FIN consumes one packet sequence number. A received FIN produces EOF and
  automatically starts the local FIN exchange after any active send finishes.
  Closing twice is safe. Each side waits twice its last FIN timeout in TIME_WAIT.
  Closed sessions retain duplicate-FIN replies until endpoint shutdown, so
  repeated final-ACK loss cannot strand the other peer.
- A connection completed before `accept()` still exposes its buffered data
  and EOF. Failed sessions are skipped by `accept()`. RST and inactivity errors
  wake blocked operations. Cancelling an in-progress send aborts its session
  because some bytes may already have arrived. Cancelling a `close()` waiter
  leaves the FIN exchange running.
- `transport.close()` is terminal cleanup: abort sessions, cancel background
  work and delayed sends, discard raw UDP output, and wake pending accepts.
  Use `connection.close()` first when graceful delivery matters. Raw socket
  abort also avoids a Windows Proactor hang when closing during a pending send.

## Explicit Stage 1 limits

- One session per remote address per endpoint lifetime; reconnect with a fresh
  endpoint. IDs are not reused. The v1 initial SYN has no nonce to distinguish
  a fresh connection from a delayed copy of an old SYN. Simultaneous open and
  sequence-number wrap are not supported at this stage.
- The default receive buffer holds 64 MSS-sized packets. When full, packets
  are not accepted or acknowledged as received; timeout retransmission retries
  after the reader drains it. Full advertised-window/probe behavior is Stage 6.
- No sliding window, out-of-order buffer, SACK recovery, RTT estimator, Reno,
  event-log integration, or automatic swarm transport selection is claimed.
- `stats()` reports stage, state, retransmissions, pending payload bytes, and
  application byte counters. `cwnd_bytes` and `rtt_ms` are `None`. Send counters
  count original payload submitted, excluding retransmissions; receive counters
  count bytes returned to the application.

## Verification

Run `python -m pytest tests/test_murmurtp_stage1.py -v -p no:cacheprovider`.
The 24 cases cover individually lost control/data packets, duplicate/reordered
duplex traffic, corruption, delayed handshake ACKs, bounded buffering, multiple
peers, late accepts, cancelled operations, and cleanup during setup.

The acceptance case is `test_one_mib_at_ten_percent_loss_matches_sha256`:
1 MiB over real localhost UDP, 10% seeded outgoing loss on **both** endpoints,
retransmissions and drops observed, matching SHA-256 hashes, and graceful EOF.
Tests deliberately use a 5 ms initial timeout, 20 ms cap, and 3-second inactivity
deadline to keep local checks fast. These are test overrides, not the normal
protocol defaults or experiment settings.

Windows/Python 3.14 full-suite result: **107 passed, 2 existing Linux TCP skips**.
Python 3.10 syntax checks passed; Linux runtime verification remains for CI/VM.
Gate S1 still also requires the topology and hash-verified TCP seed/leecher run.

References: [Python datagram protocols](https://docs.python.org/3.10/library/asyncio-protocol.html#datagram-protocols)
and [asyncio timeout/cancellation](https://docs.python.org/3.10/library/asyncio-task.html#asyncio.wait_for).
