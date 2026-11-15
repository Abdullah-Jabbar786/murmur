# TCP transport (A1.1)

`core/tcp.py` implements the existing Contract 2 using asyncio streams. Swarm
code uses the same `listen`, `connect`, `accept`, `send`, `recv`, `stats`, and
`close` methods as LoopbackTransport. No application framing is added here.

```python
import asyncio
from core.tcp import TcpTransport

async def demo():
    server, client = TcpTransport(), TcpTransport()
    try:
        await server.listen(("127.0.0.1", 0))  # OS chooses a free test port
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        await outgoing.send(b"hello")
        received = bytearray()
        while len(received) < 5:
            chunk = await incoming.recv(5 - len(received))
            if not chunk:
                raise EOFError("Peer closed before the full message arrived")
            received.extend(chunk)
        print(bytes(received), outgoing.stats())
    finally:
        await asyncio.gather(server.close(), client.close())

asyncio.run(demo())
```

## Behavior

- IPv4, including the project's namespace addresses; port 0 is supported for tests.
- `send` serializes concurrent callers and drains bounded writes for backpressure.
  A return means bytes were accepted locally, not acknowledged by the peer.
- `recv(n)` returns up to n bytes; callers must assemble complete wire frames.
  Buffered data is delivered before EOF. Nonpositive n returns empty bytes,
  matching the current loopback behavior; only positive reads detect EOF.
- `close` is idempotent. Transport close is terminal, cancels pending setup,
  wakes waiting accepts with `RuntimeError`, and closes active/queued connections.
  Connection close flushes buffered output; after `close_timeout` (default 5 s),
  an unresponsive connection is aborted. Callers should stop producers first.
- Failed binds, refused connections, and unsupported algorithms propagate errors.
  `asyncio.wait_for(transport.connect(addr), timeout)` bounds connection setup.

## Linux experiments

Use `TcpTransport(congestion_control="reno")` or `"cubic"` on **both** endpoints.
The option is set on each outgoing socket before connecting and on the listener
before accepting; accepted sockets inherit it. `None` retains the OS default.
An explicit unsupported algorithm raises an error rather than silently falling
back. Ordinary transfers also work on Windows without an explicit algorithm.

`stats()` always includes `proto`, `cwnd_bytes`, `rtt_ms`, `retx`,
`bytes_in_flight`, `bytes_sent`, and `bytes_received`. Linux TCP_INFO supplies
the congestion window (segments times MSS), RTT (microseconds converted to ms),
and lifetime total retransmissions. In-flight bytes are an **estimate**:
`max(0, unacked - sacked - lost + retrans) * MSS`; short segments and offloads
limit its precision. Application byte counters exclude headers/retransmissions.
Unavailable kernel metrics are `None`; after close, metrics retain their last
snapshot. Sample before close when an experiment needs a final measurement.

Implementation references: [Python asyncio streams](https://docs.python.org/3.10/library/asyncio-stream.html)
and [Linux TCP UAPI](https://github.com/torvalds/linux/blob/master/include/uapi/linux/tcp.h).

## Verification

Run `python -m pytest -q` in an environment with `requirements.txt` installed.
On Windows, use `.venv\Scripts\python.exe -m pytest -q`.
The shared loopback/TCP tests transfer 1 MiB and check byte integrity, counters,
and EOF. TCP tests cover shutdown, bind conflicts, refusal, backpressure,
and Linux metric decoding. Two Linux-only tests verify Reno/CUBIC on real
sockets and live metrics; run these in Ubuntu or the existing Linux CI job.
The routed seed-to-leecher integration remains a later Sprint 1 gate task.
