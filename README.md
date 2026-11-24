# Murmur

<p align="center">
  <img src="https://img.shields.io/badge/status-final%20review-green" alt="status" />
  <img src="https://img.shields.io/badge/language-Python-blue" alt="language" />
  <img src="https://img.shields.io/badge/network-UDP%2FTCP%20swarm-green" alt="network" />
</p>

<p align="center">
  <strong>A BitTorrent-style P2P file swarm running on its own reliable UDP transport, MurmurTP.</strong>
</p>

> Course project • Computer Networks • P2P networking research prototype

## Overview

Murmur explores how a large file can be distributed efficiently without relying on a single server bottleneck. The project combines a tracker-driven peer swarm with a custom reliable transport built on top of UDP, making it a practical lab for networked file sharing and protocol experimentation.

### What we are building

- Tracker + swarm logic with chunked file distribution and rarest-first piece selection
- Choking/unchoking, verification, and peer lifecycle events
- Custom MurmurTP reliability layer with selective repeat, RTT estimation, flow control, and AIMD congestion control
- Routed lab topology with delay, loss, and bandwidth shaping using `tc netem` and `tc tbf`
- Experiment pipeline for E1–E5 with packet captures and summary results

## Architecture at a glance

```text
Client / Seed / Leecher
        │
        ▼
   tracker + swarm
        │
        ├── peer selection
        ├── piece validation
        ├── choking policy
        └── file delivery
        │
        ▼
    MurmurTP over UDP
```

## Progress

| Gate | Sprint | Goal | Status |
|---|---|---|---|
| S0 | Sprint 0 | Repo setup, hygiene, contracts, one merged PR each | complete |
| S1 | Sprint 1 | Topology, tracker, transport, codecs, loss shim | [Acceptance checks passed; evidence ready for review](captures/s1/README.md) |
| S2 | Sprint 2 | TCP swarm with rarest-first and choking | [Technical checks passed on merged main at `ab7d63c`](captures/README.md#gate-s2-ci) |
| S3 | Sprint 3 | MurmurTP swarm at 5% loss, one config flag | [Routed acceptance passed](experiments/README.md#ubuntu-i3-acceptance). [Five annotated exchanges](captures/README.md#gate-s3-murmurtp-exchanges) are documented. Protocol spec review remains open. |
| S4 | Sprint 4 | E1–E5 experiments and graphs | [E1/E2](results/e1-e2/README.md), [E3](results/e3/README.md), [E4](results/e4/README.md) and [E5](results/e5/README.md) measured and analyzed. |
| S5 | Sprint 5 | Report, demo, verification, polishing | [Report drafted for review](docs/report.md); final verification, demo and release review pending. |

## Results

The [results index](results/README.md) links the loss, RTT, swarm scaling, single-flow and two-flow fairness measurements.

## Quick start

```bash
make setup   # install dependencies
source .venv/bin/activate
make test    # run tests
```

The [reproduction guide](docs/reproduce.md) covers the routed pilot and the
single command for all five experiments.

## Documentation

See the docs for the full design and workflow:

- [Project report draft](docs/report.md)
- [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md)
- [docs/reproduce.md](docs/reproduce.md)
- [docs/tcp-transport.md](docs/tcp-transport.md)
- [docs/header-codec.md](docs/header-codec.md)
- [docs/loss-shim.md](docs/loss-shim.md)
- [docs/piece-selection.md](docs/piece-selection.md)
- [docs/choking.md](docs/choking.md)
- [docs/murmurtp-stage1.md](docs/murmurtp-stage1.md)
- [docs/murmurtp-stage2.md](docs/murmurtp-stage2.md)
- [docs/murmurtp-stage3.md](docs/murmurtp-stage3.md)
- [docs/murmurtp-stage4.md](docs/murmurtp-stage4.md)
- [docs/murmurtp-stage5.md](docs/murmurtp-stage5.md)
- [docs/murmurtp-stage6.md](docs/murmurtp-stage6.md)
- [docs/murmurtp-integration.md](docs/murmurtp-integration.md)
- [docs/results-analysis.md](docs/results-analysis.md)
- [docs/transport-properties.md](docs/transport-properties.md)
- [docs/wire-codec.md](docs/wire-codec.md)
- [docs/metadata-tool.md](docs/metadata-tool.md)
- [docs/tracker.md](docs/tracker.md)
- [docs/piece-storage.md](docs/piece-storage.md)
- [docs/single-download.md](docs/single-download.md)
- [docs/multi-peer.md](docs/multi-peer.md)
- [docs/client-server-mode.md](docs/client-server-mode.md)
- [docs/peer-events.md](docs/peer-events.md)
- [docs/whole-swarm-test.md](docs/whole-swarm-test.md)
- [docs/algorithm-integration.md](docs/algorithm-integration.md)
- [docs/wireshark-dissector.md](docs/wireshark-dissector.md)
- [docs/flowbench.md](docs/flowbench.md)

## Repo layout

| Folder | Purpose |
|---|---|
| `core/` | Transport interface, loopback, transport events, config |
| `tracker/` | Flask tracker |
| `peer/` | Swarm logic, pieces, choking, rarest-first |
| `murmurtp/` | Reliable UDP transport |
| `infra/` | Routing and netem scripts |
| `dashboard/` | Live swarm view |
| `experiments/` | E1 to E5 harness |
| `results/` | Raw data and graphs |
| `captures/` | Packet captures and screenshots |
| `docs/` | Design documents, specs, reports |
| `tests/` | Automated verification |

## Team

| Name | Student ID | Role | GitHub |
|---|---|---|---|
| Abdullah | 24K-0859 | Lead + Transport | [@Abdullah-Jabbar786](https://github.com/Abdullah-Jabbar786) |
| Muhammad Hammad | 24K-0544 | Swarm + storage | [@MuhammadHammadCS](https://github.com/MuhammadHammadCS) |
| Abdul Majid | 24K-0895 | Infrastructure + captures | [@AbdulMajid006](https://github.com/AbdulMajid006) |

## AI-use disclosure

See `docs/ai-disclosure.md`.

## License

To be added when the repository becomes public.
