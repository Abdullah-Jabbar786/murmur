# Murmur — Definitive Master Plan

> **Status note:** This is the planning snapshot from 15 Nov 2026. Unticked gate boxes below describe the plan at that time, not current progress. See [PROGRESS.md](../PROGRESS.md) for the build history and [report.md](report.md) for final results.

**Project:** Murmur — BitTorrent-style P2P swarm over kernel TCP or MurmurTP (custom reliable, congestion-controlled transport on UDP)  
**Course:** CS3001 Computer Networks, FAST-NUCES Karachi, Fall 2026, Miss Aqsa Aslam, BCS-5H  
**Team:** Abdullah (24K-0859, lead + transport), Muhammad Hammad (24K-0544, swarm), Abdul Majid (24K-0895, infra)  
**Repo:** [github.com/Abdullah-Jabbar786/murmur](https://github.com/Abdullah-Jabbar786/murmur) (private)  
**Version:** 2.0, 15 Nov 2026  
**Location in repo:** `docs/MASTER_PLAN.md` — changes only by a PR all three approve

---

## 0. How to use this document

1. Everyone reads sections 1–4 once. They are the shared picture.
2. After that, read only **your own backlog (section 10)** and **the current sprint (section 12)**.
3. When this file disagrees with `PROJECT_PLAN.md` or the proposal PDF, **this file wins**. Overrides in section 3.
4. A sprint is finished only when its **gate checklist** is fully ticked. Abdullah tags it, we move on.
5. Stuck for more than half a day → ask. Silence is the only real way to fail this project.

---

## 1. Current Repo Status (Honest Assessment)

### What exists
| Item | State |
|---|---|
| Repo skeleton | ✅ 42 files, CI workflow, issue/PR templates, folder structure |
| Dashboard skeleton | ✅ Mock data generator, `http.server`, 10-peer piece grid + throughput chart |
| Mock event data | ✅ 10 JSONL files in `dashboard/mock_run/` |
| Git history | 2 commits on `main`. Branches: `abdullah/dashboard-skeleton` (current), team `add-team-info` branches |
| Docs stubs | ✅ Architecture, MurmurTP spec header table, PROJECT_CONTEXT, proposal outline |

### What does NOT exist yet
- ❌ No Python code in `tracker/`, `peer/`, `murmurtp/`, `infra/`, `experiments/`
- ❌ No `core/` package (new — transport interface, loopback, events, config)
- ❌ No `__init__.py` files (imports will fail)
- ❌ No contracts written as proper docs
- ❌ No topology scripts
- ❌ No tests beyond `test_smoke.py` (asserts True)
- ❌ PROGRESS.md blank, README team table placeholder
- ❌ No `pytest.ini` (async tests will fail)
- ❌ No `config/default.json`

---

## 2. Mission — What "ace" means

**Three layers, built strictly in this order:**

| Layer | What it is | Done when |
|---|---|---|
| **1. Rubric-safe core** | TCP swarm on routed topology, tracker, rarest-first, choking, hash verification | Gate S2 |
| **2. Evidence** | MurmurTP under the same swarm, experiments E1–E5 with 3 runs each, annotated pcaps | Gates S3 + S4 |
| **3. Standout** | Dashboard replay, Wireshark dissector, fuzz tests, fault-injection log, one-command reproducibility, polished README + demo | Gate S5 |

**Scope-cut order if time runs short** (cut from top, never bottom):
1. Stretch goal (50-peer, CUBIC, DHT)
2. Standout items, lowest value first
3. Extra E5 pairings
4. **Never cut:** Gates S0–S4. Those ARE the project.

### Definition of "ace" (finish line)
- [ ] 1 seed + 10 leechers over two LANs finish 64 MiB, every piece hash-verified, over **both** TCP and MurmurTP
- [ ] MurmurTP still completes at 5% loss
- [ ] E1–E5 tables filled, 3 runs each, mean + sample std dev, produced by scripts
- [ ] Every key exchange has an annotated `.pcapng`
- [ ] Every graph has 3–5 sentences: expected, observed, why. Losses to TCP explained, not hidden
- [ ] `make experiments` regenerates every table and figure
- [ ] Fresh-VM reproduction by someone who didn't write the code using only `docs/reproduce.md`
- [ ] Dashboard replay, demo video, README with results and GIF
- [ ] Git history shows all three members with real commits, reviews and issues
- [ ] `docs/ai-disclosure.md` complete and honest

---

## 3. Locked Decisions (override older documents)

| # | Decision | Why |
|---|---|---|
| D1 | **Order:** TCP swarm → MurmurTP → experiments → one stretch goal | Protects the rubric |
| D2 | **Transport interface is async**, reliable in-order **byte stream** (`await conn.recv(n)`) | Matches asyncio + TCP semantics. Framing is the swarm's job |
| D3 | **Three transports:** `LoopbackTransport` (in-memory, tests), `TcpTransport`, `MurmurTransport` | Loopback lets Hammad and CI work before anything else exists |
| D4 | **Shared package `core/`** owned by Abdullah: transport interface, loopback, TCP, event logger, config | One place for contract code |
| D5 | **Namespaces + bash**, not Mininet | No dependency, easy to script and reset |
| D6 | **Flask tracker** with `GET /announce` | Already in `requirements.txt` |
| D7 | **Dashboard polls every 1 s**, no WebSocket, Chart.js vendored locally | Simple and offline-safe |
| D8 | **Config is JSON** (`config/default.json`) plus CLI overrides | `tomllib` needs 3.11, VM may have 3.10 |
| D9 | **Python 3.10+ compatible.** No `asyncio.timeout` — use `asyncio.wait_for` | CI runs 3.11, VMs may differ |
| D10 | **MurmurTP window in packets, not bytes** | 2-byte byte-window caps at 64 KB → ~2.6 Mbit/s at 200ms RTT, poisons E2 |
| D11 | **Sequence numbers are packet numbers.** cum_ack = next expected. SACK bit i = `cum_ack+1+i` | 32-bit SACK bitmap only makes sense with packet numbers |
| D12 | **RTT sampled from local send-time map** (first-transmission only, Karn's rule). Header timestamp stays for pcap debugging | One 4-byte field can't carry both sender + echo time |
| D13 | **TCP congestion control chosen per socket** with `setsockopt(TCP_CONGESTION)` | Per-socket is reliable. Sysctl varies by namespace |
| D14 | **Add `NOT_INTERESTED`** as 9th swarm message | Choking logic depends on interest state. Real BitTorrent has it |
| D15 | **Seed uplink capped at 10 Mbit** in all swarm experiments | Without it LAN A leechers pull at veth speed, E3 shows no swarm benefit |
| D16 | **Event timestamps** are seconds since `MURMUR_T0` (env var, epoch float set by harness) | All processes share one clock and one origin |
| D17 | **Gate tags: `gate-s0` to `gate-s5`**, then `v1.0` | Sprints, not weeks |
| D18 | **Merge rule:** Abdullah merges with Squash and merge. 24h rule: reviewer may merge if Abdullah unreachable | Speed without bottleneck |
| D19 | **Free tools only.** Repo private until marked | Team constraint |
| D20 | **Harness runs as root via venv:** `sudo .venv/bin/python experiments/run.py` | Namespaces need root, system Python lacks packages |

---

## 4. System Architecture

### 4.1 Software layers

```
        dashboard (8080/TCP)  <-- reads JSONL event logs from results/<run-id>/
                ^
                |
 tracker (8000/TCP) <-- announce -- peers (peer/node.py)
                                      |
                       swarm logic: pieces, rarest-first, choking, pipelining
                                      |
                        Transport interface  (Contract 2, async, byte stream)
                       /            |               \
              LoopbackTransport  TcpTransport    MurmurTransport
              (tests, in memory) (6881/TCP)      (6882/UDP, MurmurTP)

 experiments harness: builds topology, applies netem/tbf, runs E1–E5, collects data
 infra: network namespaces, 2 routers, tc netem + tbf, tcpdump pcaps
```

### 4.2 Topology

Every host is a Linux network namespace. Each LAN is a bridge inside its router namespace, with one veth pair per host.

```
 LAN A 10.0.1.0/24            LAN C 10.0.3.0/24 (origin)           LAN B 10.0.2.0/24
 leechers .11 to .(10+k)      tracker .10, seed .11, dash .12      leechers .11 to .(10+k)
     | gateway R1 10.0.1.1        | gateway R1 10.0.3.1                  | gateway R2 10.0.2.1
     +----------------[ R1 ]====== WAN 10.0.12.0/30 ======[ R2 ]--------+
                          .1   (netem + tbf, both directions)   .2
```

`k` = leechers per LAN. Standard: 5 (10 total). E3 at N=20: k=10.

| Segment | Subnet | Gateway | Hosts |
|---|---|---|---|
| LAN A | 10.0.1.0/24 | R1 10.0.1.1 | Leechers from .11 |
| LAN B | 10.0.2.0/24 | R2 10.0.2.1 | Leechers from .11 |
| LAN C (origin) | 10.0.3.0/24 | R1 10.0.3.1 | Tracker .10, Seed .11, Dashboard .12 |
| WAN | 10.0.12.0/30 | — | R1 .1, R2 .2 |

Routes: R1 reaches 10.0.2.0/24 via 10.0.12.2. R2 reaches 10.0.1.0/24 and 10.0.3.0/24 via 10.0.12.1. Hosts default-route to their gateway.

### 4.3 Impairments

| Where | What | Why |
|---|---|---|
| WAN, R1 + R2 egress | `netem` delay/loss, then `tbf` 10 Mbit | Proposal's bottleneck. RTT 20ms = 10ms each way |
| **Seed uplink** | `tbf` 10 Mbit on seed's own interface | Without it, LAN A pulls at veth speed; E3 shows no swarm benefit |
| LAN links | None | Fast and boring |
| All veths | `ethtool -K <if> tso off gso off gro off` | Offloads make pcaps show fake giant packets |

### 4.4 Ports and fixed parameters

| Item | Value |
|---|---|
| Tracker | 8000/TCP |
| Peer protocol, TCP | 6881/TCP |
| Peer protocol, MurmurTP | 6882/UDP |
| Dashboard | 8080/TCP |
| Test file (final runs) | 64 MiB random, 256 pieces |
| Dev file | 8 MiB, via `--size` (fast loops) |
| Piece / block | 256 KiB / 16 KiB (16 blocks per piece) |
| Integrity | SHA-256 per piece |
| MurmurTP MSS | 1200 B payload, 25 B header, 28 B IP+UDP = 1253 B |
| Unchoke | 4 regular slots every 10 s + 1 optimistic every 30 s |

---

## 5. The Three Contracts (frozen in Sprint 0)

After Gate S0, a change to any contract is a PR that **all three** approve.

### 5.1 Contract 1: Swarm Wire Protocol (Hammad drafts)

Frame: **4-byte big-endian length** (counts type + payload) + **1-byte type** + **payload**. All integers big-endian.

| Type | Name | Payload |
|---|---|---|
| 0 | HANDSHAKE | magic `MURMUR01` (8 B), info_hash (32 B), peer_id (20 B) |
| 1 | BITFIELD | ceil(num_pieces / 8) bytes, piece 0 = MSB of byte 0 |
| 2 | HAVE | piece index (u32) |
| 3 | INTERESTED | empty |
| 4 | NOT_INTERESTED | empty (D14) |
| 5 | CHOKE | empty |
| 6 | UNCHOKE | empty |
| 7 | REQUEST | piece (u32), offset (u32), length (u32, ≤ 16384) |
| 8 | PIECE | piece (u32), offset (u32), block data |

Rules: HANDSHAKE first both directions, `info_hash` must match or close. BITFIELD once, right after handshake. Never REQUEST while choked. PIECE carries one 16 KiB block. Piece verified after all 16 blocks arrive.

### 5.2 Contract 2: Transport Interface (Abdullah drafts)

```python
class Transport:
    async def listen(self, addr): ...
    async def accept(self) -> "Connection": ...
    async def connect(self, addr) -> "Connection": ...
    async def close(self): ...

class Connection:
    peer_addr: tuple
    async def send(self, data: bytes) -> None: ...     # waits if send buffer full
    async def recv(self, max_bytes: int) -> bytes: ...  # b"" means peer closed
    async def close(self) -> None: ...                  # graceful
    def stats(self) -> dict: ...                        # sync, reads state only
```

`stats()` keys: `proto` ("tcp" / "murmurtp" / "loopback"), `cwnd_bytes`, `rtt_ms`, `retx`, `bytes_in_flight`. Unknown = `None`. Config flag `"transport": "tcp" | "murmurtp"` picks implementation.

### 5.3 Contract 3: Event Log (Abdullah writes, Majid reviews)

Each node appends JSONL to `results/<run-id>/events-<node>.jsonl`. Every event has `t` (float seconds since `MURMUR_T0`), `node` (IP or `harness`), `type`. **Types only added, never renamed.**

```json
{"t": 0.00,  "node": "10.0.1.11", "type": "join"}
{"t": 12.43, "node": "10.0.1.11", "type": "have", "piece": 17}
{"t": 12.50, "node": "10.0.1.11", "type": "rate", "down_bps": 812000, "up_bps": 120000}
{"t": 12.60, "node": "10.0.1.11", "type": "transport", "peer": "10.0.2.12", "proto": "murmurtp", "cwnd_bytes": 14400, "rtt_ms": 104.2, "retx": 3}
{"t": 12.70, "node": "10.0.1.11", "type": "unchoke", "peer": "10.0.1.12"}
{"t": 12.70, "node": "10.0.1.11", "type": "choke", "peer": "10.0.1.13"}
{"t": 13.00, "node": "10.0.1.11", "type": "hash_fail", "piece": 42}
{"t": 40.00, "node": "10.0.1.11", "type": "complete"}
{"t": 40.01, "node": "10.0.1.11", "type": "summary", "bytes_down": 67108864, "bytes_up": 31457280, "retx": 12, "hash_fails": 0}
{"t": 40.10, "node": "harness", "type": "marker", "label": "loss=2%"}
```

Emission: `rate` + `transport` once/sec/node (per-connection for `transport`). `summary` once at exit. Seed also writes `join` and `rate`. Writers flush each line; readers skip half-written last lines. Everyone logs through `core/events.py`: `EventLog(node, run_dir).emit("have", piece=17)`.

---

## 6. MurmurTP Design (Abdullah)

### 6.1 Header (25 bytes, big-endian)

| Field | Bytes | Notes |
|---|---|---|
| Flags | 1 | `SYN=0x01, ACK=0x02, FIN=0x04, DATA=0x08, RST=0x10` |
| Connection ID | 2 | Assigned by listener in SYN-ACK |
| Sequence number | 4 | Packet number of this DATA packet (D11) |
| Cumulative ACK | 4 | Next expected packet |
| Receive window | 2 | Free buffer slots, **in packets** (D10) |
| SACK bitmap | 4 | Bit i = packet `cum_ack+1+i` has arrived |
| Timestamp | 4 | Sender's clock ms, low 32 bits (debugging/pcaps only, D12) |
| Payload length | 2 | 0–1200 |
| Checksum | 2 | 16-bit Internet checksum over header (checksum zeroed) + payload |

One UDP socket per node on port 6882, demuxed by `(remote addr, connection ID)`.

### 6.2 Connection lifecycle
- **Open:** SYN → SYN-ACK (with conn ID) → ACK. SYN retried up to 5× with exponential backoff.
- **Data:** byte stream cut into ≤1200 B packets, numbered from 0.
- **Close:** FIN → ACK → 2×RTO wait. RST aborts. 30s idle with no ACK progress → drop.

### 6.3 Sender
- In-flight map: `seq → (first_send_time, tx_count, payload)`
- **Effective window** = `min(cwnd, rwnd)` in packets. New data only while `in_flight < effective_window`.
- **Loss detection:** (1) ≥3 later packets SACKed, or (2) RTO timer fires. Only lost packets retransmitted (selective repeat).
- SACK covers at most 32 packets past a hole.

### 6.4 Receiver
- Reorder buffer holds out-of-order packets, releases contiguous prefix to app.
- Every DATA triggers an ACK with cum_ack + SACK bitmap + current window.
- Full buffer advertises window=0; sender probes with timer.

### 6.5 RTO (Jacobson/Karels)

```
first sample R:   SRTT = R,  RTTVAR = R / 2
later samples R:  RTTVAR = 0.75 * RTTVAR + 0.25 * |SRTT - R|
                  SRTT   = 0.875 * SRTT + 0.125 * R
RTO = SRTT + max(10 ms, 4 * RTTVAR)     clamped to [200 ms, 5 s]
on timeout: RTO = min(2 * RTO, 5 s)
```

Initial RTO = 1 s. **Karn's rule:** never sample RTT from retransmitted packets.

### 6.6 Congestion control (Reno-style)

Counted in packets internally, reported in bytes (`cwnd_packets × 1200`).

- Initial `cwnd = 10`, `ssthresh = ∞`
- **Slow start:** `cwnd += 1` per newly ACKed packet until `cwnd ≥ ssthresh`
- **Congestion avoidance:** `cwnd += 1/cwnd` per ACK (≈ +1/RTT)
- **SACK loss (once per recovery):** `ssthresh = max(2, cwnd/2)`, `cwnd = ssthresh`. Fast retransmit.
- **Timeout:** `ssthresh = max(2, cwnd/2)`, `cwnd = 1`, RTO doubles.
- CUBIC is stretch only, after Gate S4.

### 6.7 Build stages

| Stage | Adds | Proving test |
|---|---|---|
| 0 | Header codec | Round-trip, bad checksum rejected, fuzzed bytes never crash decoder |
| 1 | Handshake, teardown, stop-and-wait, checksum, timeout retransmit | 1 MiB through loss shim at 10% loss, hash matches |
| 2 | Sliding window + cumulative ACK | 16 MiB at 5% loss, hash matches, throughput > stage 1 |
| 3 | SACK bitmap, reorder buffer, selective retransmit | 64 MiB at 5% loss + 5% reorder + 1% dup, hash matches |
| 4 | RTT/RTO with Karn + backoff | Unit tests on RTO formulas, RTO rises under delay |
| 5 | Slow start, AIMD, fast retransmit | cwnd trace shows sawtooth at 2% loss |
| 6 | Flow control | Slow reader → shrinking window, no overflow |
| 7 | `stats()`, `transport` events, `MurmurTransport` behind Contract 2 | Same swarm test passes on Loopback AND MurmurTransport |

**Loss shim** (`murmurtp/lossy.py`): seeded UDP wrapper with drop, delay, jitter, reorder, duplicate. Tests on laptop in seconds. Hypothesis property tests assert random shim settings never corrupt bytes.

### 6.8 Debug tooling
`MURMUR_TRACE=1` writes per-connection JSONL trace. Wireshark **Lua dissector** (~60 lines) for the 25-byte header — standout item.

### 6.9 Honest limits (state in report)
No pacing (bursts hit tbf queue). Userspace Python CPU cost. SACK covers 32 packets. No path-MTU discovery.

---

## 7. Swarm Design (Hammad plumbing, Abdullah algorithms)

### 7.1 Tracker (`tracker/server.py`)
`GET /announce?peer_id=&ip=&port=&event=&left=` returns `{"peers": [...], "seeds": n, "leechers": m}`. Events: `started`, `completed`, `stopped`. Peers expire after 90 s. Re-announce every 30 s. Port depends on transport.

### 7.2 Metadata (`peer/metadata.py`)
Generates test file (seeded random bytes, `--size`) + `metadata.json`: name, length, piece_length, SHA-256 hashes. `info_hash` = SHA-256 of canonical JSON.

### 7.3 Peer behaviour
Per connection: `am_choking, am_interested, peer_choking, peer_interested`, remote bitfield, in-flight requests.

- **Rarest-first** (`peer/piece_selection.py`): among pieces we lack that peer has, pick lowest availability. Seeded RNG tie-break. Finish started piece first.
- **Pipelining:** 16–32 outstanding block requests per unchoked peer (tuned for 200 ms RTT).
- **Choking** (`peer/choking.py`): every 10 s, unchoke top 4 by download rate (seed uses upload rate) + 1 optimistic every 30 s.
- **Integrity:** SHA-256 per piece. Failure → `hash_fail` event, discard, re-request.
- **Completion:** broadcast HAVE, send `completed` to tracker, keep serving as seed.

### 7.4 Client-server baseline (E3)
Config flag `"mode": "client_server"` → leechers never upload (choke everyone, connect only to seed).

### 7.5 Storage (`peer/storage.py`)
Preallocated file, block assembly, bitfield, hash verification. Unit-tested with corrupted block.

---

## 8. Infrastructure & Experiments (Majid builds the lab)

### 8.1 Topology scripts
- `infra/topology.sh [--leechers-per-lan K]` — namespaces, bridges, veths, addresses, routes, `ip_forward`, offloads off
- `infra/teardown.sh` — removes everything, works from half-built state
- `infra/run_in_ns.sh <ns> <cmd>` — wraps `ip netns exec`
- `infra/netem.sh apply --delay MS --loss PCT --rate MBIT` / `netem.sh clear`

### 8.2 Harness (`experiments/run.py`)
Per run: (1) topology, (2) impairments, (3) set `MURMUR_T0`, start tracker/seed/leechers/tcpdump, (4) wait for completion or timeout, (5) collect into `results/<experiment>/<run-id>/`, (6) teardown. Resumable, unattended, timeout-safe. Each cell: 1 warm-up + 3 recorded runs.

### 8.3 Metrics

| Metric | Definition |
|---|---|
| Distribution time | `MURMUR_T0` to last leecher's `complete` |
| Per-peer completion | Time to each leecher's `complete` |
| Goodput | File bytes / completion time, per peer |
| Retransmissions | `retx` from `summary` |
| WAN bytes | Interface counters on R1, before/after |
| Upload share | Seed's `bytes_up` / total `bytes_up` |
| RTT estimate error | `|SRTT - true RTT|` over time (E4) |
| Jain fairness | `J = (Σx_i)² / (n · Σx_i²)` over **steady-state** throughput |

### 8.4 Experiments

Baseline: WAN 10 Mbit, seed uplink 10 Mbit, RTT 20 ms, 0% loss, 10 leechers, 64 MiB.

| ID | Variable | Compare | Runs | Measures |
|---|---|---|---|---|
| E1 | Loss 0/2/5% | TCP vs MurmurTP swarm | 18 | Distribution time, goodput, retx |
| E2 | RTT 20/100/200 ms | TCP vs MurmurTP swarm | 18 | Distribution time, per-peer throughput |
| E3 | N = 2/5/10/20 | Swarm vs client-server | 24 | Distribution time, upload share, WAN bytes saved |
| E4 | Loss 0/1/2/5%, single flow | MurmurTP vs Reno vs CUBIC | 36 | Goodput, cwnd over time, RTT estimate error |
| E5 | Two competing flows, 10 Mbit | MurmurTP vs TCP, MurmurTP vs MurmurTP, TCP vs TCP | 9 | Jain fairness |

E4/E5 use `murmurtp/flowbench.py` (bulk transfer through any `Transport`) — no swarm dependency. TCP `cwnd` from `ss -ti` sampled 1/sec.

**Time budget:** ~105 runs, plan **8–12 hours** unattended. Dev with 8 MiB file; 64 MiB for final runs only (overnight).

**Statistics:** mean + sample std dev (n-1), plus min/max. Same file, same seeds, warm-up discarded, one variable at a time.

---

## 9. Dashboard (Majid)

Reads JSONL logs (Contract 3). Views: swarm map (peer cards grouped by LAN, 256-cell piece grid, completion %, choke state), throughput lines, transport panel (cwnd/RTT/retx overlay).

Endpoints: `/api/state`, `/api/events?since=N`.

**Known bugs to fix:**

| # | Issue |
|---|---|
| 1 | `server.py` rereads all files every poll, no `since`, crashes on half-written JSONL, single-threaded |
| 2 | `app.js` fixed 90s x-axis, overcounts rate events, throws on `have` before `join`, wrong LAN coloring for seed |
| 3 | `mock_events.py` doesn't follow Contract 3 (integer `t`, no `up_bps`, no seed, no `unchoke`/`transport`/`marker`) |

**Build order:** D0 mock events → D1 incremental server → D2 swarm map → D3 throughput → D4 transport panel (after S2) → D5 **replay mode** with time slider → D6 theme polish

**Design:** dark green/black + burgundy accent. System font stack. 14–16 px rounded cards. Chart.js vendored locally. **Replay mode matters most** — live demos fail, recorded runs always work, produces the README GIF.

---

## 10. Team Split — Work Division (~70-75% Abdullah)

### 10.1 Principles
- **Abdullah owns design, the hard algorithms, and integration.** Hammad and Majid own well-specified modules with their names on commits.
- **Folders are ownership.** Need a change in someone else's → issue or joint PR.
- The pure-logic modules (`piece_selection.py`, `choking.py`) are written by Abdullah; Hammad wires them into the peer loop.
- **Every person writes code, reviews code, and owns at least one experiment.**

### 10.2 Ownership matrix

| | Abdullah (~70-75%) | Hammad (~13-15%) | Majid (~13-15%) |
|---|---|---|---|
| Role | Lead + transport + algorithms | Swarm plumbing | Infra |
| Folders | `core/`, `murmurtp/`, `config/`, `peer/piece_selection.py`, `peer/choking.py`, `experiments/aggregate.py`, `experiments/plot.py` | `tracker/`, `peer/wire.py`, `peer/storage.py`, `peer/metadata.py`, `peer/node.py` | `infra/`, `experiments/run.py`, `dashboard/`, `captures/`, `Makefile` |
| Features | F6–F10 + F2/F3/F4 algorithms | F1–F5 plumbing | F11, F12 |
| Experiments | E4, E5 | E3 | E1, E2 |
| Contract | #2 transport, #3 events | #1 wire protocol | Reviews #3 |
| Default reviewer | Majid (Hammad for pure logic) | Abdullah | Hammad |

### 10.3 Reality check
- **Match the proposal.** Feature areas must align with the proposal's role table. Viva questions follow it — each person must explain their own part.
- **Git history is a deliverable.** Small commits from all three, reviews from all three.
- If load gets heavy, move a task to a teammate early. Don't absorb it silently.

---

## 11. Repository Layout (target)

```
.github/            workflows/ci.yml, ISSUE_TEMPLATE/task.md, pull_request_template.md, CODEOWNERS
config/             default.json                                   (Abdullah)
core/               transport.py, loopback.py, tcp.py, events.py, config.py   (Abdullah)
murmurtp/           header.py, lossy.py, rto.py, congestion.py, connection.py,
                    transport.py, flowbench.py                     (Abdullah)
peer/               wire.py, storage.py, metadata.py, node.py      (Hammad)
                    piece_selection.py, choking.py                 (Abdullah, pure logic)
tracker/            server.py, client.py                           (Hammad)
infra/              topology.sh, teardown.sh, netem.sh, run_in_ns.sh, capture.sh   (Majid)
experiments/        run.py (Majid), aggregate.py, plot.py (Abdullah)
dashboard/          server.py, mock_events.py, static/             (Majid)
captures/           annotated pcaps and screenshots                (Majid)
results/            CSVs and figures (raw logs and big pcaps git-ignored)
docs/               MASTER_PLAN.md, contracts, spec, reproduce, report, ai-disclosure
tests/              test_core_*, test_murmurtp_*, test_swarm_*, test_infra_*
Makefile            (Majid owns, others add targets by tiny PR)
pytest.ini          pythonpath = ., asyncio_mode = auto
```

Empty `__init__.py` in `core/`, `murmurtp/`, `peer/`, `tracker/`.

---

## 12. Sprints, Integration Points & Gates

> [!IMPORTANT]
> **Gates do not move. Scope does.**

| Sprint | Target days | Goal |
|---|---|---|
| S0 Kickoff | 1–2 | Repo settings, hygiene, contracts, one merged PR each |
| S1 Foundations | 3–9 | Topology, tracker, TcpTransport, codecs, loss shim, MurmurTP stage 1 |
| S2 TCP Swarm | 10–18 | Multi-peer swarm, harness, dashboard on real logs, SACK transport |
| S3 MurmurTP | 19–30 | RTO, congestion control, flow control, swarm over MurmurTP |
| S4 Experiments | 31–38 | E1–E5, graphs, analysis, reproduce.md |
| S5 Polish | 39–45 | Standout items, stretch, report, demo, v1.0 |

### Gate S0 (`gate-s0`)
- [ ] Squash-only + auto-delete set, milestones, labels, issues assigned
- [ ] Each member has a merged PR
- [ ] Contracts 1, 2, 3 merged in `docs/`
- [ ] `LoopbackTransport` + `core/events.py` merged, `make test` runs async test
- [ ] Everyone confirmed asyncio comfort and VM setup

### Gate S1 (`gate-s1`)
- [ ] `make topo` builds from clean, `make topo-down` fully removes
- [ ] Traceroute shows R1→R2, iperf3 ~10 Mbit, netem/tbf work
- [ ] `TcpTransport` merged, tracker + metadata tool merged
- [ ] **I1:** one seed → one leecher over real TCP, hashes verified

### Gate S2 (`gate-s2`) — the rubric-safe core
- [ ] 1 seed + 10 leechers (5 per LAN) finish with correct hashes over TCP
- [ ] Rarest-first and choking visible in logs
- [ ] Fresh clone, `make test` green in CI
- [ ] Swarm pcap in `captures/`
- [ ] Harness runs one full swarm unattended, dashboard shows real run

### Gate S3 (`gate-s3`)
- [ ] 64 MiB swarm completes over MurmurTP with correct hashes, **including at 5% loss**
- [ ] TCP ↔ MurmurTP = one config flag, no code change
- [ ] Spec matches implementation, reviewed by teammate
- [ ] Annotated pcaps for 5 exchanges

### Gate S4 (`gate-s4`)
- [ ] E1–E5 tables filled, 3 runs each, mean + sample std dev
- [ ] Every figure from a script, `make experiments` regenerates all
- [ ] Each result has written explanation
- [ ] Run a small E1 by mid-S3 to shake out the harness early

### Gate S5 (`gate-s5`, then `v1.0`)
- [ ] One stretch goal done + documented (or consciously skipped)
- [ ] Report, demo video, README polished
- [ ] Fresh-VM reproduction passed
- [ ] Release checklist (section 17) complete

---

## 13. Per-Person Backlogs

### 13.1 Abdullah's Backlog

| ID | Sprint | Task | Branch | Done when |
|---|---|---|---|---|
| A0.1 | S0 | Repo hygiene: `pytest.ini`, `requirements.txt` (+`pytest-asyncio`, `hypothesis`), `CODEOWNERS`, CONTRIBUTING.md fixes, README gate table, `__init__.py` files, PR template `Closes #` line, `.gitignore` fixes | `abdullah/s0-repo-hygiene` | CI green, async test runs |
| A0.2 | S0 | Repo settings (squash only, auto-delete), milestones, labels, issues | n/a | Issues exist, assigned |
| A0.3 | S0 | Contract 2 in `core/transport.py`, `LoopbackTransport`, tests | `abdullah/s0-contract-transport` | Two loopback endpoints exchange bytes |
| A0.4 | S0 | `core/events.py` + Contract 3 doc, `config/default.json` + `core/config.py` | `abdullah/s0-events-config` | Event log writes valid JSONL in test |
| A0.5 | S0 | `docs/murmurtp-spec.md` v1 (sections 6.1–6.6) | `abdullah/s0-murmurtp-spec` | Reviewed by Majid |
| A1.1 | S1 | `core/tcp.py` TcpTransport (with `TCP_CONGESTION`, `stats()` from `TCP_INFO`) | `abdullah/s1-tcp-transport` | Same test passes on Loopback + Tcp. **Hammad needs this by day 5** |
| A1.2 | S1 | `murmurtp/header.py` codec + tests | `abdullah/s1-header-codec` | Round-trip, checksum, fuzz tests |
| A1.3 | S1 | `murmurtp/lossy.py` loss shim | `abdullah/s1-loss-shim` | Measured drop rate matches setting |
| A1.4 | S1 | `peer/piece_selection.py` rarest-first + tests | `abdullah/s1-rarest-first` | Picks rarest, seeded ties |
| A1.5 | S1 | `peer/choking.py` (4+1 optimistic) + tests | `abdullah/s1-choking` | Picks correct peers |
| A1.6 | S1 | MurmurTP stage 1: handshake, teardown, stop-and-wait | `abdullah/s1-murmurtp-stage1` | 1 MiB at 10% loss, hash matches |
| A2.1 | S2 | Integrate selection, choking, pipelining into `peer/node.py` (pair with Hammad) | `abdullah/s2-node-integration` | 1 seed + 10 leechers pass on Loopback |
| A2.2 | S2 | MurmurTP stages 2+3 (sliding window, SACK, selective repeat) | `abdullah/s2-murmurtp-sack` | 64 MiB at 5% loss + reorder + dup |
| A2.3 | S2 | Hypothesis property tests for transport | `abdullah/s2-transport-fuzz` | Random shim settings never corrupt bytes |
| A2.4 | S2 | `experiments/aggregate.py` + `plot.py` against mock results | `abdullah/s2-aggregate-plot` | Produces table + PNG from sample data |
| A3.1 | S3 | RTO (Karn, backoff) + congestion control (slow start, AIMD, fast retransmit) | `abdullah/s3-rto-cc` | cwnd sawtooth at 2% loss |
| A3.2 | S3 | Flow control, `stats()`, `transport` events, `MurmurTransport` | `abdullah/s3-murmur-transport` | Swarm passes on MurmurTransport |
| A3.3 | S3 | Swarm over MurmurTP in topology, debug with Hammad + Majid | `abdullah/s3-integration-fixes` | **I3: completes at 5% loss, one config flag** |
| A3.4 | S3 | Wireshark Lua dissector | `abdullah/s3-lua-dissector` | Pcap shows decoded MurmurTP fields |
| A4.1 | S4 | `murmurtp/flowbench.py`, run E4 + E5, analysis text | `abdullah/s4-e4-e5` | Tables + 3–5 sentences per figure |
| A5.1 | S5 | Report lead, README, demo script, release, final review | `abdullah/s5-release` | `v1.0` tagged |

### 13.2 Hammad's Backlog

| ID | Sprint | Task | Branch | Done when |
|---|---|---|---|---|
| H0.1 | S0 | README team row | `hammad/s0-readme-team-row` | Merged (proves workflow) |
| H0.2 | S0 | Contract 1 in `docs/` (section 5.1), including NOT_INTERESTED | `hammad/s0-contract-wire` | All three approve |
| H1.1 | S1 | `peer/wire.py` encode/decode for all 9 messages + tests | `hammad/s1-wire-codec` | Round-trip, malformed rejected |
| H1.2 | S1 | `peer/metadata.py` file generator + `metadata.json` + `info_hash` | `hammad/s1-metadata-tool` | 64 MiB file + 256 hashes, deterministic |
| H1.3 | S1 | `tracker/server.py` + `tracker/client.py` + tests | `hammad/s1-tracker` | Announce returns peers, expiry works |
| H1.4 | S1 | `peer/storage.py` piece store, block assembly, SHA-256, bitfield | `hammad/s1-storage` | Corrupt block detected in test |
| H2.1 | S2 | `peer/node.py` single download seed→leecher over TcpTransport | `hammad/s2-single-download` | **I1: one file, hashes correct** |
| H2.2 | S2 | Multi-peer: bitfield, HAVE, INTERESTED, CHOKE, UNCHOKE | `hammad/s2-multi-peer` | 3 peers on Loopback finish |
| H2.3 | S2 | Client-server mode flag | `hammad/s2-client-server-mode` | Leechers upload nothing in that mode |
| H2.4 | S2 | Event hooks per Contract 3, `summary` at exit | `hammad/s2-events` | Logs validate against schema |
| H2.5 | S2 | Whole-swarm test on Loopback (CI) | `hammad/s2-swarm-test` | `make test` runs it in <30 s |
| H4.1 | S4 | Run E3, write analysis | `hammad/s4-e3` | Table, figure, 3–5 sentences |
| H5.1 | S5 | Peer protocol doc, report swarm sections, README swarm section | `hammad/s5-docs` | Reviewed by Abdullah |

### 13.3 Majid's Backlog

| ID | Sprint | Task | Branch | Done when |
|---|---|---|---|---|
| M0.1 | S0 | README team row | `majid/s0-readme-team-row` | Merged |
| M0.2 | S0 | VM setup notes + netem test in `infra/README.md` | `majid/s0-vm-netem-check` | Documented |
| M1.1 | S1 | `infra/topology.sh`, `teardown.sh`, `run_in_ns.sh`, `make topo` / `topo-down` | `majid/s1-topology` | Builds from clean, tears from any state |
| M1.2 | S1 | `infra/netem.sh` apply/clear, WAN + seed-uplink tbf, offloads off | `majid/s1-netem-tbf` | iperf3 ~10 Mbit, delay/loss in ping |
| M1.3 | S1 | Evidence: ping, traceroute, iperf3, `ip route` screenshots | `majid/s1-evidence` | **Gate S1** |
| M2.1 | S2 | Dashboard fixes + `mock_events.py` per Contract 3 | `majid/s2-dashboard-fixes` | Dashboard shows mock without errors |
| M2.2 | S2 | `experiments/run.py` skeleton | `majid/s2-harness` | **I2: runs 1 seed + 10 leechers end-to-end** |
| M2.3 | S2 | `infra/capture.sh` (tcpdump start/stop) | `majid/s2-capture` | Pcap saved per run |
| M3.1 | S3 | Dashboard transport panel + replay mode | `majid/s3-dashboard-transport-replay` | Replays recorded run with slider |
| M3.2 | S3 | Annotated pcaps for 5 exchanges | `majid/s3-pcaps` | Screenshots + notes in `captures/README.md` |
| M3.3 | S3 | Harness hardening: resumable, timeouts, `run.json`, warm-up | `majid/s3-harness-hardening` | Kill + restart resumes |
| M4.1 | S4 | Run E1 + E2, then `make experiments` | `majid/s4-e1-e2` | Tables, figures, analysis |
| M4.2 | S4 | `docs/reproduce.md` + fresh-VM test | `majid/s4-reproduce` | Verified by someone else |
| M5.1 | S5 | Dashboard theme, README GIF, 50-peer if chosen | `majid/s5-polish` | GIF in README |

### 13.4 Pairing sessions (30–60 min, screen share)
- **Day 5:** Abdullah + Hammad — `TcpTransport` meets `node.py`
- **Sprint 2:** Abdullah + Hammad on node integration. Abdullah + Majid on harness + event logs.
- **Sprint 3:** All three — debugging MurmurTP in real topology

---

## 14. Git Workflow

### One-time setup
```bash
git clone https://github.com/Abdullah-Jabbar786/murmur.git && cd murmur
git config user.name "Your Name"
git config user.email "email-linked-to-github@example.com"
git config pull.rebase false
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### Every task
```bash
git checkout main && git pull
git checkout -b hammad/s1-tracker
# ... work ...
make test                                    # must be green
git status                                   # check what you're adding
git add tracker/ tests/test_swarm_tracker.py  # NEVER "git add ."
git commit -m "feat: tracker announce endpoint"
git push -u origin hammad/s1-tracker
```
Then: PR → fill template → `Closes #12` → label → reviewer auto-requested by CODEOWNERS.

### Rules
1. Never commit to `main`. Never force push. No rebase of pushed branches.
2. One task per PR, under ~300 changed lines.
3. Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `exp:`, `chore:`.
4. **`main` is always green.** Broken main → revert first, debug second.
5. Contract changes need approval from all three.
6. Large files: never commit raw pcaps or 64 MiB test files.

### CODEOWNERS (lists reviewer, not owner)
```
/core/                      @majid-handle
/murmurtp/                  @majid-handle
/config/                    @majid-handle
/peer/                      @Abdullah-Jabbar786
/peer/piece_selection.py    @hammad-handle
/peer/choking.py            @hammad-handle
/tracker/                   @Abdullah-Jabbar786
/infra/                     @hammad-handle
/experiments/               @hammad-handle
/dashboard/                 @hammad-handle
/captures/                  @hammad-handle
/docs/                      @Abdullah-Jabbar786 @hammad-handle @majid-handle
```

### Reviewer checklist
- [ ] CI green, PR small and focused
- [ ] Does what the linked issue says, nothing else
- [ ] Follows contracts (message types, `stats()` keys, event schema)
- [ ] Has a test that would fail if code were wrong
- [ ] Runs from fresh clone with documented commands
- [ ] No secrets, big files, stray files
- [ ] Docs updated if behaviour changed

---

## 15. Quality Bar

### Definition of done (every feature)
1. Runs from **fresh clone** with documented commands
2. Has automated test, `make test` stays green
3. Writes events to JSONL log where relevant
4. Described in `docs/`
5. Reviewed by a teammate via PR

### Test pyramid

| Level | What | CI? |
|---|---|---|
| Unit | Codecs, piece selection, choking, RTO, storage, tracker | Yes |
| Component | MurmurTP through loss shim, swarm on Loopback | Yes (<30 s) |
| Property | Hypothesis: random loss/reorder/dup never corrupts bytes | Yes (small budget) |
| Network | Topology, netem, real swarm in namespaces | No — marked `@pytest.mark.net`, run with `make test-net` |
| Experiment | `make experiments-quick` (8 MiB, 1 run/cell) | No, local only |

### Fault-injection log (cheap standout item)
Deliberately break things, record what happened:
1. Kill seed mid-run (do leechers finish from each other?)
2. Kill a leecher
3. WAN link down for 5 s (`ip link set down`)
4. Spike loss to 20% for 10 s
5. Corrupt a block (flip bits) → `hash_fail` → re-request
6. Stop tracker for 60 s
7. Reorder + duplicate injection through shim

---

## 16. Standout Checklist (after Gate S4)

| Item | Value | Effort | Owner |
|---|---|---|---|
| Annotated pcaps for all 5 exchanges | Very high | Medium | Majid |
| Honest analysis, including where MurmurTP loses | Very high | Low | Everyone |
| cwnd + RTT overlay graphs, TCP vs MurmurTP | High | Low | Abdullah |
| Dashboard replay mode + README GIF | High | Low | Majid |
| Wireshark Lua dissector | High | Low | Abdullah |
| Hypothesis fuzz tests | High | Low | Abdullah |
| Fault-injection log | High | Low | Abdullah + Majid |
| `make experiments-quick` (~10 min) | High | Medium | Majid |
| 50-peer scale test | Medium | Low | Majid |
| CUBIC in MurmurTP | Medium | Medium | Abdullah (only if everything else done) |
| DHT | Medium | High | **Skip** |

---

## 17. Report, Demo & Release

### Report outline (`docs/report.md`)
1. Problem and objectives
2. Architecture and addressing (topology figure, tables)
3. Swarm design (wire protocol, rarest-first, choking, client-server)
4. MurmurTP design (header, state machine, RTO, congestion control, limits)
5. Methodology (lab, impairments, metrics, statistics)
6. Results E1–E5 (each figure: expected / observed / why)
7. Packet-trace evidence (annotated pcaps)
8. Fault injection
9. Limitations and next steps
10. AI-use disclosure and team contributions

### Demo script (3–5 min, record backup video)
1. `make topo`, `ip route`, traceroute L1→L6 (20 s)
2. Swarm over MurmurTP at 10 Mbit, 20 ms, 2% loss, dashboard open (60 s)
3. Hash-verified completion of 256 pieces on all peers (20 s)
4. Live cwnd plot + Wireshark loss recovery (45 s)
5. Flip config to TCP, same swarm, side-by-side graphs (45 s)
6. Headline result graph + replay mode (30 s)

### Release checklist
- [ ] README: one-line pitch, topology diagram, dashboard GIF, headline results
- [ ] "How to run" / "How to reproduce" work
- [ ] Team table filled, placeholder READMEs replaced
- [ ] `docs/ai-disclosure.md` complete, LICENSE added
- [ ] No secrets/tokens/personal data in history, large files checked
- [ ] `v1.0` tagged with GitHub Release (notes + results CSV)
- [ ] After marking: repo public, pinned on all three profiles

### Viva preparation
- **Abdullah:** why selective repeat + SACK, RTO computation + Karn's rule, why AIMD, window field, why MurmurTP may lose to TCP
- **Hammad:** what each message does, why rarest-first, how choking + optimistic slot work, hash integrity, tracker role
- **Majid:** topology + routing, netem + tbf placement, harness reproducibility, what each pcap shows

---

## 18. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Abdullah becomes bottleneck | High | 24h review rule, reviewer fallback, small PRs, scope-cut order |
| Teammate falls behind silently | Medium | Daily check-in, issues by name, shortlog at each integration point |
| MurmurTP takes longer | High | Stage-by-stage, loss shim first, S2 already protects rubric |
| MurmurTP too slow in Python | Medium | Measure CPU, batch syscalls, report honestly |
| Async confusion | Medium | Confirm in S0, keep `node.py` small and typed, review early |
| VM/netem problems | Low | `modprobe sch_netem` check in S0, Ubuntu VM eliminates WSL2 risk |
| Experiments flaky | Medium | One harness, warm-up, resumable, `run.json` with git SHA, offloads off |
| Late bug invalidates results | Medium | Small E1 by mid-S3, tag commit used for final runs |
| Big files bloat repo | Medium | `.gitignore` rules, CSVs + small samples only |
| Scope creep | High | Stretch only after Gate S4, only one |

---

## 19. Communication Rhythm

- **Daily (group chat, 3 lines):** done, stuck, next. Two minutes.
- **Start of each sprint (20 min):** goals, owners, risks.
- **Integration sessions (I1–I4, 1–2 hours, all three, screen share):** one person runs full stack from fresh clone. What breaks → issues.
- **End of each week:** one line to `PROGRESS.md` + one screenshot/graph for README story.
- **Stuck rule:** half a day → ask. Full day → pair for 30 min.

---

## 20. Makefile Targets (Majid owns, others add by tiny PR)

| Target | What it does |
|---|---|
| `make setup` | Create `.venv`, install requirements |
| `make test` | Unit, component, property tests (CI runs this) |
| `make test-net` | Network tests in namespaces (VM only, sudo) |
| `make topo` / `make topo-down` | Build or remove topology |
| `make netem ARGS="--delay 20 --loss 2"` / `make netem-clear` | Apply/clear WAN impairments |
| `make swarm` | One local swarm run |
| `make dashboard` | Start dashboard on a run dir |
| `make experiments-quick` | 8 MiB, 1 run/cell, ~10 min |
| `make experiments` | Full E1–E5, 3 runs, then tables + plots |
| `make plots` | Regenerate figures from `results/` |
| `make clean` | Remove caches + temp run data |

---

## 21. Open Decisions

- [ ] Real GitHub handles for CODEOWNERS (Hammad, Majid)
- [ ] Final calendar dates for sprint targets (if course sets any)
- [ ] VM sizing confirmed per person (4 vCPU, 6–8 GB RAM, 30 GB disk)
- [ ] Which one stretch goal (decide at Gate S4)
- [ ] Report format required by instructor
- [ ] Student Developer Pack → branch protection available?
