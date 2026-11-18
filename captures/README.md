# captures

Small real .pcapng traces and annotated screenshots belong here when collected.

- `murmurtp.lua`: Wireshark/TShark decoder for MurmurTP v1 on UDP/6882.
- `generate_sample.py`: generates explicitly synthetic decoder fixtures.

See [the dissector guide](../docs/wireshark-dissector.md) for testing, sample
generation, filters and Decode As. Generated mock files go under ignored
`results/mock-analysis/`, not the real evidence collection. Real annotated
exchanges and routed packet captures are still pending.
