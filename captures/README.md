# captures

Small real .pcapng traces and annotated screenshots belong here when collected.

- `murmurtp.lua`: Wireshark/TShark decoder for MurmurTP v1 on UDP/6882.
- `generate_sample.py`: generates explicitly synthetic decoder fixtures.

See [the dissector guide](../docs/wireshark-dissector.md) for testing, sample
generation, filters and Decode As. Generated mock files go under ignored
`results/mock-analysis/`, not the real evidence collection. Real annotated
exchanges and routed packet captures are still pending.

## Collect the Gate S2 TCP sample

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
