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
