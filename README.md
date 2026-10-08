# Murmur

**A BitTorrent-style P2P file swarm running on its own reliable UDP transport (MurmurTP).**

> Status: early development (course project, Computer Networks)

## Why

Sending a big file from one server to many clients is limited by the server's bandwidth. P2P swarming fixes that, but most implementations hide the transport layer inside TCP. Murmur builds both layers and measures them.

## What we are building

- **Tracker and swarm**: chunked file distribution with rarest-first piece selection, choking and unchoking, and SHA-256 verified pieces
- **MurmurTP**: a custom reliable transport on top of UDP with selective repeat, RTT estimation, flow control and AIMD congestion control
- **Routed lab**: 2 routers and 3+ subnets with delay, loss and bandwidth impairments using `tc netem`
- **Evidence**: packet captures and experiments (3 network conditions, 3 runs each)

## Progress

| Gate | Goal | Status |
|---|---|---|
| 1 | Proposal, topology, traceroute | not started |
| 2 | Swarm working over TCP | not started |
| 3 | Swarm running on MurmurTP | not started |
| 4 | All experiments and graphs done | not started |
| 5 | Report, README, demo | not started |

## Results

Coming after the experiments. Graphs will go here.

## Quick start

```bash
make setup   # install dependencies
make test    # run tests
```

More commands (`topo`, `swarm`, `experiment`, `plots`) will be added as they are built. See `docs/reproduce.md`.

## Repo layout

| Folder | What lives there |
|---|---|
| `tracker/` | Flask tracker |
| `peer/` | Swarm logic (pieces, choking, rarest-first) |
| `murmurtp/` | Custom reliable UDP transport |
| `infra/` | Topology, routing, netem scripts |
| `dashboard/` | Live swarm view |
| `experiments/` | Scripts that run E1 to E5 |
| `results/` | Raw data and graphs |
| `captures/` | `.pcapng` traces and annotated screenshots |
| `docs/` | Proposal, architecture, protocol spec, reports |
| `tests/` | Automated tests |

## Team

| Name | Role | GitHub |
|---|---|---|
| (name) | Transport (MurmurTP) | @username |
| (name) | Swarm (tracker, peers) | @username |
| (name) | Infra (topology, experiments, dashboard) | @username |

## AI-use disclosure

See `docs/ai-disclosure.md`.

## License

To be added when the repo goes public.
