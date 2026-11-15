# Seeded UDP loss shim (A1.3)

`murmurtp/lossy.py` wraps an asyncio UDP transport's outgoing `sendto` calls.
Incoming datagrams continue to reach the existing `DatagramProtocol`. It adds
no reliability or framing and never edits packet bytes. The future MurmurTP
connection layer will handle the losses and duplicates it introduces.

Inside an async function, wrap a transport created for your receiving protocol:

```python
import asyncio
from murmurtp.lossy import LossConfig, LossyDatagramTransport

loop = asyncio.get_running_loop()
raw, protocol = await loop.create_datagram_endpoint(
    MyReceivingProtocol, local_addr=("127.0.0.1", 0))
udp = LossyDatagramTransport(
    raw, LossConfig(loss=0.1, delay=0.02, jitter=0.005,
                    reorder=0.05, reorder_delay=0.05, duplicate=0.01), seed=42)
try:
    udp.sendto(encoded_murmurtp_packet, destination_addr)
    # Keep the event loop running while your protocol receives replies.
finally:
    udp.close()
```

`MyReceivingProtocol`, `encoded_murmurtp_packet`, and `destination_addr` are
supplied by the caller. Send through `udp`, not `raw`. Wrap each endpoint to
impair both directions, using distinct seeds (for example 42 and 43).
No sudo, namespaces, or `tc netem` are needed for this local test tool.

## Configuration and behavior

- `loss`, `reorder`, and `duplicate` are probabilities from 0 to 1: `0.1` means 10%.
- `delay`, `jitter`, and `reorder_delay` are seconds; all values must be finite
  and nonnegative. Enabled reordering requires a positive `reorder_delay`.
- Loss is decided first. A surviving original gets one extra identical copy
  with probability `duplicate`. Both copies share the chosen delay.
- Delay is `max(0, delay + uniform(-jitter, jitter))`. Selected originals get
  an additional `reorder_delay`, allowing later sends to overtake them.
  Jitter can also change order. A reorder selection does not guarantee an
  inversion: sparse traffic, or delaying every packet equally, may stay ordered.
- Same seed, configuration, and send sequence reproduce impairment choices.
  OS scheduling and real UDP delivery are not deterministic. No global RNG is used.
- Closing cancels scheduled, unsent copies and closes the underlying socket.
  Already-dispatched packets cannot be recalled. Closing twice is safe;
  sending after close raises `RuntimeError`. Use immutable `bytes` payloads.
- Immediate socket errors reach the caller; errors in delayed callbacks are
  reported by asyncio. Asynchronous UDP errors reach the existing protocol.

## Counters and tests

`stats()` returns a snapshot. `attempted` and `dropped` count original sends;
`duplicated` and `reordered` count selected surviving originals. `sent` counts
copies handed to the raw transport, not confirmed arrivals. `pending` counts
scheduled copies; `cancelled` counts copies suppressed during shutdown.
`send_errors` counts synchronous socket errors during dispatch. Real network
losses are separate from the shim's simulated `dropped` count.

Run `python -m pytest tests/test_murmurtp_lossy.py -v -p no:cacheprovider`.
Nine tests cover drop-rate accuracy, seeds, delay/jitter, duplication, reordering,
shutdown, socket errors, invalid configuration, real UDP exchange, and randomized
byte preservation. Virtual-time checks avoid timing-dependent statistical tests.
The full suite on Windows currently gives 52 passed and 2 existing Linux TCP skips.

References: [asyncio datagram transports](https://docs.python.org/3.10/library/asyncio-protocol.html#datagram-transports)
and [event-loop scheduling](https://docs.python.org/3.10/library/asyncio-eventloop.html#scheduling-delayed-callbacks).
