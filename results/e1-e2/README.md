# E1/E2 measured results

These exports come from the 64 MiB Ubuntu sweep on `ebe8b38`. Each
condition ran a warm-up and three recorded ten-leecher transfers.
[metrics.csv](metrics.csv) contains individual trials and peer outcomes.
[summary.csv](summary.csv) excludes warm-ups and reports means, sample
standard deviations, ranges and observation counts. The [sweep manifest](run.json)
identifies the settings and source revision. Each cell's run.json records its
attempts and payload verification. The interrupted E1 MurmurTP 0% trial
completed on attempt 2 and contributes one recorded observation.

## E1 — WAN loss at 20 ms configured RTT

| Loss per direction | TCP swarm goodput (Mbit/s) | MurmurTP swarm goodput (Mbit/s) | TCP retransmissions | MurmurTP retransmissions |
|---|---:|---:|---:|---:|
| 0% | 41.05 | 14.08 | 4,904 | 6,596 |
| 2% | 41.92 | 10.77 | 4,146 | 7,907 |
| 5% | 40.77 | 9.25 | 9,578 | 11,924 |

Values are means of three trials. Swarm goodput counts verified bits
delivered to all ten peers over the time to the last completion. It can
exceed the 10 Mbit/s WAN cap because peers also exchange data on their
local LANs. Retransmissions sum the final peer counters for each transport.
MurmurTP took longer and reported more retransmissions as configured loss
increased. TCP's goodput means stayed within 1.2 Mbit/s of one another in
this sample, even as its retransmissions rose from the 0% to the 5% condition.

[E1 distribution time](E1-distribution_time_s-all.png) ·
[Swarm goodput](E1-goodput_mbps-all.png) · [Retransmissions](E1-retx-all.png)

## E2 — RTT at zero configured loss

| Configured RTT | TCP LAN A | TCP LAN B | MurmurTP LAN A | MurmurTP LAN B |
|---|---:|---:|---:|---:|
| 20 ms | 7.21 | 4.39 | 2.55 | 2.34 |
| 100 ms | 7.33 | 4.59 | 2.24 | 1.53 |
| 200 ms | 7.46 | 4.51 | 1.49 | 0.83 |

Each entry averages the five leechers' mean verified goodputs on that LAN
(Mbit/s). Each peer has three observations. LAN B is across the shaped WAN
from the seed. Its MurmurTP peer goodput fell from 2.34 to 0.83 Mbit/s as
configured RTT rose. TCP's corresponding means stayed close to 4.5 Mbit/s.
These observations describe this topology and sample. In particular, the two
independent baseline groups at 20 ms and 0% loss showed appreciable variation
for MurmurTP. The measurements do not isolate a particular transport algorithm
as the cause.

[E2 distribution time](E2-distribution_time_s-all.png) ·
[Example LAN A peer](E2-goodput_mbps-10.0.1.11.png) ·
[Example LAN B peer](E2-goodput_mbps-10.0.2.11.png) ·
[Distribution-time table with sample SD](../../experiments/README.md#final-64-mib-distribution-times)

Recompute the summary from the repository root:

```bash
python -m experiments.aggregate results/e1-e2/metrics.csv --output results/m41/recomputed-summary.csv
```
