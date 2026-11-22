# E4 single-flow measurements

Each trial sent one 64 MiB file from LAN A to LAN B over a 10 Mbit/s WAN
with 20 ms of configured round-trip delay. The table gives the mean of
three recorded transfers per condition. Each condition also had one warm-up.
The resumed sweep reused verified checkpoints and completed the first
recorded MurmurTP transfer at 5% loss on its second attempt.

| WAN loss per direction | Reno goodput (Mbit/s) | CUBIC goodput (Mbit/s) | MurmurTP goodput (Mbit/s) |
|---:|---:|---:|---:|
| 0% | 8.85 | 8.59 | 3.96 |
| 1% | 5.02 | 4.57 | 2.73 |
| 2% | 3.31 | 3.12 | 1.91 |
| 5% | 1.71 | 1.48 | 0.99 |

All three transports delivered fewer verified payload bits per second as
loss increased. Reno had the highest mean goodput at every loss level.
All three recorded MurmurTP transfers at 5% loss still delivered the full
file, with receiver goodput from 0.959 to 1.027 Mbit/s.

| WAN loss per direction | Reno RTT difference (ms) | CUBIC RTT difference (ms) | MurmurTP RTT difference (ms) |
|---:|---:|---:|---:|
| 0% | 56.72 | 61.62 | 10.90 |
| 1% | 14.21 | 13.29 | 17.96 |
| 2% | 12.76 | 10.71 | 19.05 |
| 5% | 14.03 | 16.80 | 24.19 |

Each entry averages the per-trial mean absolute difference between sender
smoothed RTT and a concurrent ping RTT on the same route. MurmurTP had the
smallest difference at 0% loss and the largest at 5%. ICMP probes and data
packets can encounter different queue delays, so this comparison measures
agreement with the probe.

### Congestion window over time

Each trace figure shows the first recorded transfer at that loss level.
In these trials, the peak sampled window fell for every transport at each
increase in configured loss. For example, MurmurTP peaked at 5,942.6 KiB
at 0% and 18.4 KiB at 5%. A large sender window alone did not guarantee high
receiver goodput: MurmurTP had the largest sampled peak at 0% while Reno
delivered the highest mean goodput there. The time series retains all 36
recorded transfers for comparisons beyond the illustrated trials.

| WAN loss per direction | Trace figure | Reno peak (KiB) | CUBIC peak (KiB) | MurmurTP peak (KiB) |
|---:|---|---:|---:|---:|
| 0% | [Window and RTT](E4-traces-loss0.png) | 3,359.8 | 1,791.6 | 5,942.6 |
| 1% | [Window and RTT](E4-traces-loss1.png) | 45.2 | 36.8 | 49.2 |
| 2% | [Window and RTT](E4-traces-loss2.png) | 32.5 | 25.5 | 30.9 |
| 5% | [Window and RTT](E4-traces-loss5.png) | 19.8 | 15.6 | 18.4 |

The [goodput plot](E4-goodput_mbps-all.png) and [RTT difference plot](E4-rtt_error_ms-all.png)
show the three-trial mean and sample standard deviation. The [sweep manifest](run.json)
identifies revision `a95279a`. The [trial metrics](metrics.csv), [summary statistics](summary.csv)
and [recorded time series](timeseries.csv) accompany the figures.
