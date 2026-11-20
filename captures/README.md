# captures

Small real .pcapng traces and annotated screenshots belong here when collected.

- `murmurtp.lua`: Wireshark/TShark decoder for MurmurTP v1 on UDP/6882.
- `generate_sample.py`: generates explicitly synthetic decoder fixtures.

See [the dissector guide](../docs/wireshark-dissector.md) for testing, sample
generation, filters and Decode As. Generated mock files go under ignored
`results/mock-analysis/`. Ubuntu run `20261119T203919Z-a3653c43` produced
`r1-wan0.pcap` with 41,389 routed TCP packets (5,005,461 bytes). All ten
8 MiB downloads matched SHA-256
`0d7cf1d2830e2b4e5ee581fd1735241ca7ad06880a287d0c1d7190ca96f5184c`.

## Gate S2 TCP sample

The [R1 WAN sample](s2-tcp-wan.pcapng) contains the first 200 frames from
the run above. Tcpdump reported 41,466 packets received by the filter,
41,389 captured and zero kernel drops. The capture used a 128-byte snapshot
length. All 200 packet records retain their original timestamps, lengths and
captured bytes in the pcapng copy.

Open frames 1–3 in Wireshark to follow the TCP handshake from LAN B leecher
`10.0.2.13:54558` to LAN A leecher `10.0.1.11:6881` (SYN, SYN/ACK, ACK).
Frames 4 and 6 each carry 65 bytes of TCP data, one in each direction.

### Reproduce the sample

After a passing M2.3 Ubuntu run, use the directory printed by the runner.
In the Ubuntu terminal, set `run_dir` to that path and run:

```bash
cat "$run_dir/capture.log"
tcpdump -nn -r "$run_dir/r1-wan0.pcap" -c 5
tcpdump -r "$run_dir/r1-wan0.pcap" -c 200 -w "$HOME/murmur-s2-tcp.pcap"
```

The last command writes up to 200 real frames for review. Open the sample
in Wireshark and inspect the endpoints and TCP flags. For the repository,
save the reviewed sample as `captures/s2-tcp-wan.pcapng` in pcapng format
and record its source run ID. The full trace stays in the ignored run directory.

## Gate S2 routed dashboard

Majid opened the saved 64 MiB TCP run `20261119T201545Z-e1a12b8c` in
Ubuntu. The dashboard shows 10/10 downloads complete and 256/256 pieces
verified for LAN A leecher `10.0.1.11`. [Dashboard screenshot](s2-tcp-dashboard.png)

### Piece selection and upload decisions

The same leecher logged these events in `events-10.0.1.11.jsonl`. Times are
seconds from the run's shared start.

| Time (s) | Event | Remote peer | Observation |
|---:|---|---|---|
| 6.357 | piece_select | 10.0.1.15 | Selected piece 197, advertised by one connected peer. |
| 6.510 | unchoke | 10.0.2.14 | Allowed this peer to request data. |
| 6.511 | unchoke | 10.0.2.11 | Allowed this peer to request data. |
| 6.814 | choke | 10.0.2.15 | Withheld uploads to this peer. |
| 7.719 | choke | 10.0.2.14 | Withdrew the upload permission granted at 6.510 s. |
| 8.082 | piece_select | 10.0.2.12 | Selected piece 194, advertised by four connected peers. |

Both selections started fresh pieces. The availability field records how
many connected peers advertised the chosen piece at that instant. Comparing
its rarity with every eligible alternative would require the candidate set
for that same decision.

## Gate S2 CI

The push workflow for merged `main` commit `ab7d63c` completed successfully.
On a fresh Ubuntu 24.04 runner, the workflow checked out the repository and
ran the pytest suite under Python 3.11 with TShark required. It invokes
the same suite as the Makefile's `test` target. [GitHub Actions run 44617294078](https://github.com/Abdullah-Jabbar786/murmur/actions/runs/44617294078)
