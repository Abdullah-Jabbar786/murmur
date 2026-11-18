# Gate S1 evidence

| Gate check | Evidence available |
|---|---|
| Topology setup and teardown | Ubuntu `make test-topo` passed, including a rebuild with 20 leechers and cleanup from a partial topology. See [M1.1 progress](../../PROGRESS.md). |
| Bandwidth, delay and loss | Majid's Ubuntu `make test-netem` run measured WAN receiver goodput of 8.30 and 8.60 Mbit/s, 4.88 Mbit/s at the 5 Mbit/s override, and 9.53 Mbit/s on the separate seed path. See [M1.2 measurements](../../infra/README.md#m12-acceptance-on-ubuntu). The original terminal log is in `~/murmur-m12-netem.log` on the VM. |
| Routed path | The [router tables](routes.png) show the WAN next hops. The [traceroute](traceroute.png) records a1 reaching b1 through R1 and R2. |
| TCP transport, tracker and metadata tool | Merged in PRs #7, #15 and #14 respectively. |
| I1, one seed to one leecher | On Windows at `d590b24`, `tests/test_swarm_single_download.py::test_i1_64_mib_real_tcp_hash_verified` passed in a focused pytest run. It checks every piece and the whole-file SHA-256 over localhost TCP. See the [I1 guide](../../docs/single-download.md). |

## Ubuntu routing and ping at d590b24

Majid used five leechers per LAN, 10 ms delay per WAN direction, 0% configured
loss, and 10 Mbit/s WAN and seed caps. R1 routed LAN B via 10.0.12.2 on wan0.
R2 routed LAN A and the origin LAN via 10.0.12.1 on wan0.

| ICMP traceroute hop from a1 | Address | Probe RTT |
|---|---|---|
| 1. R1, LAN A gateway | 10.0.1.1 | 0.119 ms |
| 2. R2, WAN interface | 10.0.12.2 | 41.475 ms |
| 3. b1, LAN B destination | 10.0.2.11 | 41.459 ms |

All five pings returned (0% observed loss). Minimum, mean and maximum RTT
were 21.047, 36.775 and 67.479 ms. The VM transcript is
`~/murmur-m13-routing.txt`. The [ping screenshot](ping.png) shows all five
replies and the RTT summary. The [iperf3 screenshot](iperf3.png) displays
the receiver rates and PASS line from the earlier M1.2 throughput run.
