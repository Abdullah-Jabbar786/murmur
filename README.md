# Murmur

**A BitTorrent-style P2P file swarm running on its own reliable UDP transport (MurmurTP).**

> Status: early development (course project, Computer Networks)

## Why

Sending a big file from one server to many clients is limited by the server's bandwidth. P2P swarming fixes that, but most implementations hide the transport layer inside TCP. Murmur builds both layers and measures them.

## What we are building

- **Tracker and swarm**: chunked file distribution with rarest-first piece selection, choking and unchoking, and SHA-256 verified pieces
- **MurmurTP**: a custom reliable transport on top of UDP with selective repeat, RTT estimation, flow control and AIMD congestion control
- **Routed lab**: 2 routers and 4 subnets with delay, loss and bandwidth impairments using `tc netem` and `tc tbf`
- **Evidence**: packet captures and experiments (E1 to E5, 3 network conditions, 3 runs each)

## Progress

| Gate | Sprint | Goal | Status |
|---|---|---|---|
| S0 | Sprint 0 | Kickoff: repo settings, hygiene, contracts, one merged PR each | complete (team confirmation) |
| S1 | Sprint 1 | Foundations: topology, tracker, TcpTransport, codecs, loss shim | in progress: A1.1–A1.2 merged; A1.3 loss shim implemented |
| S2 | Sprint 2 | TCP swarm: 1 seed + 10 leechers over TCP, rarest-first, choking | not started |
| S3 | Sprint 3 | MurmurTP in swarm: completes at 5% loss, one config flag | not started |
| S4 | Sprint 4 | Experiments: E1 to E5 tables filled, 3 runs each, graphs | not started |
| S5 | Sprint 5 | Polish: stretch goal, report, demo, fresh-VM test, v1.0 | not started |

## Results

Coming after the experiments. Graphs will go here.

## Quick start

```bash
make setup   # install dependencies
make test    # run tests
```

See [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md) and [docs/reproduce.md](docs/reproduce.md).

TCP transport usage and platform-specific metrics: [docs/tcp-transport.md](docs/tcp-transport.md).
MurmurTP packet encoding and checksum validation: [docs/header-codec.md](docs/header-codec.md).
Seeded UDP impairments for local tests: [docs/loss-shim.md](docs/loss-shim.md).

## Repo layout

| Folder | What lives there |
|---|---|
| `core/` | Transport interface, LoopbackTransport, TcpTransport, events, config |
| `tracker/` | Flask tracker |
| `peer/` | Swarm logic (pieces, choking, rarest-first) |
| `murmurtp/` | Custom reliable UDP transport (MurmurTP) |
| `infra/` | Topology, routing, netem scripts |
| `dashboard/` | Live swarm view |
| `experiments/` | Harness scripts for E1 to E5 |
| `results/` | Raw data, tables and graphs |
| `captures/` | `.pcapng` traces and annotated screenshots |
| `docs/` | Master plan, contracts, architecture, protocol spec, reports |
| `tests/` | Automated tests |

## Team

| Name | Student ID | Role | GitHub |
|---|---|---|---|
| Abdullah | 24K-0859 | Lead + Transport (MurmurTP, core algorithms) | [@Abdullah-Jabbar786](https://github.com/Abdullah-Jabbar786) |
| Muhammad Hammad | 24K-0544 | Swarm (tracker, peer protocol, storage) | [@MuhammadHammadCS](https://github.com/MuhammadHammadCS) |
| Abdul Majid | 24K-0895 | Infrastructure (topology, netem, dashboard, captures) | [@AbdulMajid006](https://github.com/AbdulMajid006) |

## AI-use disclosure

See `docs/ai-disclosure.md`.

## License

To be added when the repo goes public.
