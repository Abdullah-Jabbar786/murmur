# Tracker server and client (H1.3)

The tracker implements `MASTER_PLAN.md` section 7.1: peers announce their
listening endpoint and remaining bytes, then receive other available peers.
It transfers no file content. `tracker/server.py` provides the Flask server,
`tracker/client.py` provides an async HTTP client, and `tracker/protocol.py`
shares the peer representation and timer constants.

## Run locally

In a VS Code PowerShell terminal at the project root:

```powershell
.\.venv\Scripts\python.exe -m tracker.server
```

This starts the tracker on `127.0.0.1:8000`. Leave the terminal running;
Ctrl+C stops it. For the later namespace lab, bind the tracker namespace's
address with `--host 10.0.3.10 --port 8000`. The default configuration already
points to that lab address; local clients must explicitly use the localhost URL.
The CLI uses Flask's development server with debugging and reloading disabled.

To see discovery manually, run in a second PowerShell terminal:

```powershell
$seedId = '73' * 20
$leecherId = '6c' * 20
Invoke-RestMethod "http://127.0.0.1:8000/announce?peer_id=$seedId&ip=127.0.0.1&port=6881&event=started&left=0"
Invoke-RestMethod "http://127.0.0.1:8000/announce?peer_id=$leecherId&ip=127.0.0.1&port=6882&event=started&left=67108864"
```

The first response has no other peers, one seed, and zero leechers. If run
within 90 seconds, the second lists the seed and reports one seed and one
leecher. These are demo registrations; no peer file-transfer servers are
started by these commands. Ports here identify two separate local peers.

## Announce format

`GET /announce` accepts these query parameters:

| Parameter | Meaning |
|---|---|
| `peer_id` | 40 hex characters representing the same 20 bytes used in the handshake |
| `ip` | Numeric unicast IPv4 address on which this peer listens |
| `port` | Listening port, 1–65535; choose the TCP or UDP port for the current run |
| `left` | Nonnegative decimal bytes still needed, at most 20 digits |
| `event` | `started`, `completed`, `stopped`, or empty/omitted for a refresh |

The response is `{"peers": [...], "seeds": n, "leechers": m}`. Each peer entry
has `peer_id` (lowercase hex), `ip`, and integer `port`. The peer list excludes
the requester and is sorted by peer ID. Counts include all currently registered
peers, including the requester unless it stopped. `left=0` identifies a seed;
positive `left` identifies a leecher. `completed` requires `left=0`.

Repeated starts and refreshes replace a peer's record, including its endpoint,
remaining bytes, and last-seen time. Stops remove the record and are idempotent.
Malformed, missing, duplicate, or unknown query parameters receive HTTP 400
with a JSON `error`; they do not modify registration state. Responses use
`Cache-Control: no-store`.

Peers expire at **90 seconds** since their last accepted announcement, using
a monotonic clock. Expiration is evaluated on valid announce requests, before
updating the caller and calculating the response; stale peers are never returned.
The future peer loop should refresh every **30 seconds** and announce completion
and shutdown explicitly. A regular refresh can register again after expiry or
a tracker restart.

## Async client integration

```python
from tracker.client import TrackerClient, TrackerError
from tracker.protocol import Peer

client = TrackerClient("http://127.0.0.1:8000/announce", timeout=5.0)
me = Peer(peer_id=b"l" * 20, ip="127.0.0.1", port=6882)
result = await client.announce(me, left=67108864, event="started")
for peer in result.peers:
    print(peer.peer_id.hex(), peer.ip, peer.port)
# Later: refresh every client.announce_interval (30 seconds) with current left.
# await client.announce(me, left=remaining_bytes)
# await client.announce(me, left=0, event="completed")
# await client.announce(me, left=0, event="stopped")
```

The client performs standard-library HTTP work through `asyncio.to_thread`,
so a request does not block the asyncio event loop. It disables environment
HTTP proxies for direct lab routing, validates responses, and limits bodies
to 1 MiB. HTTP errors, connection failures, socket timeouts, incomplete HTTP
responses, and invalid JSON/peer records raise `TrackerError`. Invalid local
arguments raise `ValueError` before sending an announce.

The timeout bounds socket operations, not the total elapsed request duration.
Cancelling an async call does not stop its underlying worker immediately; it
can finish the request and update the tracker. The peer loop owns scheduling,
retry/backoff, overall deadlines, and serialization of its own announcements.
There is no automatic refresh task in this client.

## Scope

This is one in-memory swarm per tracker process, matching the plan's endpoint
without `info_hash`. Run a fresh tracker per experiment, and keep TCP and UDP
runs separate. Restarting clears registrations. Peers still validate the
`info_hash` in their wire handshake before exchanging data.

The server trusts advertised endpoints and peer IDs in the controlled lab;
there is no authentication. A lock protects updates and snapshots within one
process; multiple independent server processes would not share state.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_tracker.py -v -p no:cacheprovider
```

Expected: **39 passed**. The tests start and stop their own localhost HTTP server; a manual server is
not needed. Tests cover registration, discovery, endpoint changes, completion,
idempotent stop, exact expiry and refresh boundaries, invalid requests, isolation,
concurrency, real async HTTP exchange, nonblocking I/O, malformed responses,
network errors, and the response size limit. They use Flask's documented
[test client](https://flask.palletsprojects.com/en/stable/testing/) for endpoint
validation and a controlled clock for expiry, so no 90-second sleep is needed.
