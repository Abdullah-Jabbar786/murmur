# experiments

## M2.2 routed TCP swarm

From the repository root in the Ubuntu VM, prepare the project environment
with `make setup` on first use. Run the following from an idle Murmur lab:

```bash
sudo .venv/bin/python experiments/run.py
```

The runner builds the two LANs, applies the default 10 ms delay in each WAN
direction and the 10 Mbit/s WAN and seed limits, then starts a tracker, a seed
and five leechers on each LAN. The peers announce their listening addresses
to the tracker and dial the peers it returns. A completed leecher continues
serving until every leecher reports completion to the tracker.

The default development payload is 8 MiB with 256 KiB pieces. For a 64 MiB
acceptance run use `sudo .venv/bin/python experiments/run.py --size 64MiB`.
The transfer deadline defaults to 900 seconds and can be set with `--timeout`.
Each invocation prints its own directory under `results/m22/`. It contains
the config, metadata, individual payloads, process logs and per-IP JSONL
events with a shared time origin. In a second Ubuntu terminal, open the run
in the dashboard with `.venv/bin/python -m dashboard.server <run-directory>`
and visit the printed URL in the Ubuntu browser.

After all ten downloads finish, peers flush their summaries and stop. The
runner checks each final summary, the completion events, the file sizes and
the whole-file SHA-256 against the seed. A passing run prints the hash,
leecher upload bytes and a final PASS after topology teardown. The runner
also stops its processes and tears down the lab on transfer failure or Ctrl+C.
The run directory is kept for inspection. If a child exits early, read its
named log in that directory. Budget about 704 MiB for the eleven payloads of
a 64 MiB run, plus logs and the WAN trace. Each retry creates a fresh directory.

## M2.3 WAN packet capture

Install Ubuntu's `tcpdump` package if needed. The normal runner command now
captures TCP swarm traffic traversing the WAN. For the first capture test use
`sudo .venv/bin/python experiments/run.py --size 8MiB` from the repo root.
The runner starts `infra/capture.sh` on R1's `wan0`, waits for tcpdump to
report that it is listening, then starts the tracker and peers. Its filter
selects the swarm TCP port between LAN B and either LAN A or the origin LAN.
Each record keeps the first 128 bytes of the frame and its original wire length.
Use the trace to inspect IPs, TCP ports, flags and sequence numbers.

After peer shutdown the runner interrupts tcpdump, waits for it to flush and
reads the entire saved pcap. The count of swarm TCP packets must be positive
and match tcpdump's live capture count. `Verified WAN capture` prints the
count, file size and path to `r1-wan0.pcap`. The sibling `capture.log`
includes tcpdump's kernel drop counter. The final M2.3 PASS follows teardown.
To repeat the earlier transfer-only check, pass `--no-capture`.

For a manual capture in an already running topology, use
`sudo bash infra/capture.sh 6881 > "$HOME/r1-wan0.pcap"` from a second terminal.
Press Ctrl+C there to stop and flush it before tearing down the topology.

## Routed MurmurTP swarm (I3)

Use the same Ubuntu VM, venv, tracker and topology as the routed TCP test.
First verify discovery, UDP capture and file integrity with a small transfer:

```bash
sudo .venv/bin/python experiments/run.py --transport murmurtp --loss 0 --size 8MiB
```

After all ten files verify and teardown succeeds, run the acceptance case:

```bash
sudo .venv/bin/python experiments/run.py --transport murmurtp --loss 5 --size 64MiB --timeout 7200
```

`--transport` overrides the transport in `config/default.json` for this
invocation. The tracker continues to use HTTP. Each pair of MurmurTP peers
uses the same endpoint ordering to choose which peer opens its UDP session.
`--loss` configures netem on each WAN direction. Seed and same-LAN traffic
that bypasses the WAN does not encounter that loss.

Each invocation prints its own run directory and saves its settings in
`config.json`. The runner checks transport events for every peer, all ten
file hashes and the closed capture on the configured swarm port (UDP/6882 by
default for MurmurTP). After a successful lossy transfer, `wan-qdisc.log`
records both routers' queue settings and packet drop counters before teardown.
Keep the terminal output and run directory for the I3 evidence. The timeout
is an upper bound on the transfer, in seconds.

### Ubuntu I3 acceptance

On branch revision `3b1ce46`, one seed served ten MurmurTP leechers across
two LANs. Both runs used 10 ms delay per WAN direction and 10 Mbit/s limits
on the WAN and seed uplink. The runner verified every file and WAN capture
and removed the topology after each run.

| Run directory under `results/m22/` | File per leecher | Configured WAN loss per direction | Verified downloads | Captured UDP packets | Capture bytes | Leecher upload bytes |
|---|---:|---:|---:|---:|---:|---:|
| `20261120T112506Z-b93b2669` | 8 MiB | 0% | 10/10 | 110,281 | 11,324,764 | 68,698,112 |
| `20261120T113231Z-2ba9a49b` | 64 MiB | 5% | 10/10 | 697,261 | 72,798,272 | 382,648,320 |

Verified whole-file SHA-256 values from the runner:

- 8 MiB: `0d7cf1d2830e2b4e5ee581fd1735241ca7ad06880a287d0c1d7190ca96f5184c`
- 64 MiB: `45581d305941fbabc46eac1027f88e99fdc8dfd3de474343dc93a61766d2fd61`

## M3.3 resumable series

On Ubuntu, start one configuration in a new directory. The standard series
runs one full warm-up followed by three measured repetitions. Each attempt
builds and tears down the topology and verifies all ten files. Capturing and
verifying a WAN pcap is enabled by default.

```bash
cd ~/murmur
series_dir="results/m22/m33-$(date -u +%Y%m%dT%H%M%SZ)"
sudo .venv/bin/python experiments/run.py --series "$series_dir" --transport tcp --size 8MiB
```

To check recovery, wait for `Checkpointed warmup` and `Starting recorded-1`.
Press Ctrl+C during the transfer, then wait for the shell prompt. Resume in
the same terminal:

```bash
sudo .venv/bin/python experiments/run.py --resume "$series_dir"
sudo .venv/bin/python -m json.tool "$series_dir/run.json"
```

The runner skips the verified warm-up and retries the interrupted repetition
from the beginning in a fresh attempt directory. Earlier logs and partial
captures remain available. Once complete, `status` is `complete` and
`measurements` lists exactly three verified directories. Repeating the resume
command on a finished series skips all four trials. For dashboard replay, use
one attempt directory from `measurements`.

The atomic `run.json` records the configuration, source Git revision, attempt
directories, timestamps, deadlines, status and verified results. A timeout
returns a failure exit code. To retry with a longer per-transfer deadline,
use `--resume "$series_dir" --timeout 1800`. Resume restores the original
transport, loss, payload size and capture setting. On Linux the worker holds
the lab lock during cleanup, including when its supervising CLI is killed.

Existing single-transfer commands keep their output layout. New single runs
also write a checkpoint. Older recordings can still be opened in the dashboard.

### Ubuntu interruption and resume check

In TCP series `m33-resume-20261120T191739Z`, the runner reported
`Skipping verified warmup` after restart. It retried the interrupted first
measurement in `recorded-1/attempt-2`. The next two measurements each
completed on their first attempt. The WAN used 10 ms delay per direction,
0% configured loss and a 10 Mbit/s cap, with a 10 Mbit/s seed uplink.

| Verified attempt | Downloads | WAN TCP packets | Capture bytes | Leecher upload bytes |
|---|---:|---:|---:|---:|
| `recorded-1/attempt-2` | 10/10 | 44,296 | 5,393,411 | 70,008,832 |
| `recorded-2/attempt-1` | 10/10 | 41,634 | 5,023,548 | 65,634,304 |
| `recorded-3/attempt-1` | 10/10 | 50,679 | 6,173,889 | 65,961,984 |

Each downloaded file was 8,388,608 bytes and matched seed SHA-256
`0d7cf1d2830e2b4e5ee581fd1735241ca7ad06880a287d0c1d7190ca96f5184c`.
The runner verified each capture, removed the topology, and checkpointed
all three measurements.

## Offline analysis

Offline analysis for experiments E1 to E5:

- `aggregate.py`: validate normalized per-run metrics and write CSV statistics.
- `plot.py`: produce PNG charts with mean/sample-SD bars and explicit data labels.
- `mock_results.csv`: synthetic acceptance data, never measured network results.

See [results-analysis.md](../docs/results-analysis.md) for commands, schema and
tests. M4.1 uses the same CSV schema for measured E1/E2 data.

## M4.1 loss and RTT sweeps

On Ubuntu, from the repository root, run one 8 MiB pilot cell:

```bash
sudo .venv/bin/python experiments/run.py --sweep results/m41/pilot --cell E2-tcp-rtt100 --size 8MiB
```

Expect 50 ms delay per WAN direction, 0% configured loss and ten verified
downloads in each of four transfers. The pilot ends by writing
`results/m41/pilot/metrics.csv`, `summary.csv` and PNGs. Verify the three
recorded observations in the distribution-time row:

```bash
grep 'distribution_time_s' results/m41/pilot/summary.csv
```

Repeat the same command after an interruption. The outer checkpoint pins
the cell selection, file size, capture setting and Git revision. Each cell
retains its own M3.3 checkpoint with attempts, failures, retries and file
hash verification. Completed cells can regenerate reports from their logs.
Use a separate directory for a different profile or revision.

For the final matrix check free space with `df -h .` first. The 12 cells
retain **33 GiB of payload files alone** (48 transfers × 11 files × 64 MiB).
Allow additional space for WAN captures, logs and any retries. To use a
larger Linux filesystem set `SWEEP_DIR=/absolute/path/to/a/new/directory`
on the make command. When ready, run:

```bash
sudo make experiments
```

For a smaller disk, add `--keep-seed-only` to a new sweep. After each
successful trial, the runner saves the verification checkpoint, rechecks
the seed and remaining leecher files against its SHA-256, and removes the
ten leecher payload copies. It records `retained_payload: seed/payload.bin`
in the attempt checkpoint. Failed attempts retain their files for diagnosis.
If cleanup is interrupted, repeat the same sweep command to finish it.
Each passed trial keeps its logs, capture, metadata and seed. The 48 final
trials retain 3 GiB of seed payloads, plus space for evidence, failed attempts,
and the current transfer's downloads.

Run this fresh pilot on Ubuntu to check measured usage:

```bash
sudo .venv/bin/python experiments/run.py --sweep results/m41/pilot-compact --cell E2-tcp-rtt100 --size 8MiB --keep-seed-only
find results/m41/pilot-compact -name payload.bin -printf '%P %s bytes\n'
du -sh results/m41/pilot-compact
grep 'distribution_time_s' results/m41/pilot-compact/summary.csv
df -h .
```

With four first-attempt passes, expect four seed paths of 8,388,608 bytes
each and three recorded observations. Compare the directory size and free
space before scheduling the larger matrix. The compact final command is
`sudo make experiments KEEP_SEED_ONLY=1 SWEEP_DIR=results/m41/final-compact`.

M4.1 covers six E1 cells (TCP/MurmurTP × 0/2/5% WAN loss per direction at
20 ms RTT) and six E2 cells (TCP/MurmurTP × 20/100/200 ms configured RTT
at 0% loss). Even the common baseline runs separately for each experiment.
The default output is `results/m41/final/`. The dataset generator uses seed
42 for identical files and the peer scheduler uses seed 0 across variants.
Kernel loss and process scheduling can vary between repetitions.

Each passed attempt contributes rows from the seed and leecher JSONL logs.
Warm-ups are marked in the raw CSV and excluded from statistics. Distribution
time is the last leecher's completion timestamp since the common start.
Peer goodput divides verified file bits by that peer's completion time.
Swarm goodput divides all ten files' bits by distribution time. Run-level
retransmissions sum the eleven final summaries and are blank if any count
is unavailable. The final summary includes mean, sample SD, min, max and
observation counts. E1 plots distribution time, swarm goodput and available
retransmissions. E2 plots distribution time and goodput for each leecher.

### Ubuntu E2 pilot result

Majid ran the 8 MiB pilot in `results/m41/pilot` with TCP, 50 ms configured
delay per direction, 0% configured loss and 10 Mbit/s WAN and seed caps.
The warm-up and all three recorded trials passed on their first attempt.
Each trial verified all ten file hashes and the WAN capture, then removed
the topology. The runner reported saving both CSV files and the plots.

| Metric | Recorded | Missing | Mean | Sample SD | Min | Max |
|---|---:|---:|---:|---:|---:|---:|
| Time to last leecher completion (s) | 3 | 0 | 30.549 | 2.801 | 28.584 | 33.756 |

Majid repeated the same cell on Ubuntu with `--keep-seed-only` at
`ebe8b38`. The warm-up and three recorded trials each verified ten downloads
and the WAN capture on attempt 1. The file inventory showed exactly four
seed payloads of 8,388,608 bytes each. Including captures, logs and reports,
`du -sh` reported 56M for the compact pilot and 374M for the original pilot.
Across the three recorded compact trials, time to the last completion averaged
32.525 s (sample SD 0.457 s, range 32.030–32.932 s, zero missing).

### Final 64 MiB distribution times

Ubuntu sweep `results/m41/final-compact` used revision `ebe8b38` and
ten leechers per trial. Values below are seconds to the last leecher's
completion, reported as mean ± sample SD. Each entry has three recorded
trials and zero missing observations. The 12 warm-ups are excluded.

| Experiment and setting | TCP (s) | MurmurTP (s) |
|---|---:|---:|
| E1, 0% loss per direction | 131.27 ± 10.00 | 387.97 ± 64.97 |
| E1, 2% loss per direction | 128.09 ± 1.87 | 513.33 ± 105.79 |
| E1, 5% loss per direction | 132.24 ± 10.78 | 594.15 ± 114.92 |
| E2, 20 ms configured RTT | 122.79 ± 5.77 | 268.78 ± 47.47 |
| E2, 100 ms configured RTT | 117.54 ± 1.78 | 476.97 ± 82.76 |
| E2, 200 ms configured RTT | 119.61 ± 5.89 | 651.07 ± 20.80 |

E1 held RTT at 20 ms and E2 held loss at 0%. Their common baseline was
measured independently. MurmurTP mean completion time increased across
both sweeps. TCP means varied by less than six seconds within each sweep.
The distribution-time rows alone cannot identify the cause.

## E3 scaling experiment

From the repository root on Ubuntu, run the five-leecher pilot in both
distribution modes:

```bash
sudo .venv/bin/python experiments/run.py --e3 --count 5 --sweep results/e3-work/pilot --size 8MiB --keep-seed-only
cat results/e3-work/pilot/wan-savings.csv
grep -E 'distribution_time_s|seed_upload_share|wan_bytes' results/e3-work/pilot/summary.csv
```

Expect one warm-up and three recorded trials per mode. Each trial checks the
downloaded files, capture and R1 WAN counters. Client-server clients connect
directly to the seed and must upload zero payload. Swarm peers discover each
other through the tracker. Both use TCP, 10 Mbit/s seed and WAN caps, 20 ms
configured RTT and 0% configured loss. Active leechers alternate between LAN A
and LAN B. At N=5 the runner starts three on A and two on B. The unused third
namespace on B stays idle in both modes.

After pilot review, the full command is `sudo make experiments-e3`. It runs
N=2, 5, 10 and 20 with 64 MiB files (8 warm-ups and 24 recorded trials).
Repeat the same command on the same Git revision to resume. Checkpoints, logs,
captures, seed payloads, two metric CSVs and three plots go under
`results/e3-work/final/`.

Distribution time ends at the last verified leecher completion event. Seed
upload share divides seed payload bytes sent by payload bytes sent by all peers.
Each attempt records R1 wan0 RX and TX byte counters immediately before and
after the transfer harness. Their summed deltas include both directions and
control traffic. For each N, `wan-savings.csv` subtracts swarm mean WAN bytes
from client-server mean WAN bytes across recorded trials. Positive savings
mean the swarm used fewer WAN bytes. Negative savings are retained.

### Five-leecher Ubuntu pilot

All eight 8 MiB trials passed. Each mode supplied three recorded
observations after its warm-up, with zero missing values.

| TCP distribution mode | Mean time to last completion (s) | Mean seed upload share | Mean R1 WAN bytes (RX + TX) |
|---|---:|---:|---:|
| Swarm | 18.135 | 31.23% | 17,827,458 |
| Client-server | 44.904 | 100.00% | 17,994,523 |

Peers supplied most of the swarm payload. Its mean completion time was
about 60% shorter, while its mean WAN traffic was only 0.93% lower. The
observed WAN byte ranges overlapped across the two modes.

### Interrupted ten-leecher warm-up

In the original 64 MiB client-server warm-up, nine clients finished and
a3 verified 48 of 256 pieces. Its session reported `too many abandoned requests`
and zero hash failures. This attempt supplies diagnostic logs only.
An incomplete direct client now reconnects to its configured seed when the
per-connection abandoned-request limit closes the link. It keeps verified
pieces and partial blocks. At most eight reconnects are allowed within the
original transfer deadline.

To check the change on Ubuntu, use a fresh directory on the updated revision:

```bash
sudo .venv/bin/python experiments/run.py --e3 --count 10 --sweep results/e3-work/pilot-n10-reconnect --size 8MiB --keep-seed-only
```

The 64 MiB matrix on the updated revision also needs its own directory, for
example `sudo make experiments-e3 E3_DIR=results/e3-work/final-reconnect`.
The earlier checkpoints and logs remain in `results/e3-work/final/`.

## E4 single-flow transport comparison

Each trial sends one deterministic file from a1 to b1 across R1 and R2 using
the bulk-flow benchmark. The receiver hashes the payload and returns a receipt.
The runner checks both endpoint summaries against the source digest and saves
the WAN capture. The WAN cap is 10 Mbit/s with 10 ms delay per direction.
Loss is applied independently in each direction. All variants use the same
dataset seed (0), size, route and shaping settings.

On Ubuntu, start with 8 MiB and 0% loss. This exercises TCP Reno, TCP CUBIC
and MurmurTP, each with one warm-up and three recorded trials.

```bash
cd ~/murmur
sudo .venv/bin/python experiments/run.py --e4 --sweep results/e4-work/pilot --loss 0 --size 8MiB
cat results/e4-work/pilot/summary.csv
```

After reviewing the pilot, run `sudo make experiments-e4` for the 64 MiB
matrix. The four loss levels (0%, 1%, 2%, 5%) give 12 conditions, 12 warm-ups
and 36 recorded trials. Repeat the same command on the same Git revision to
resume in `results/e4-work/final/`. Each attempt keeps its source payload,
logs and verified capture by default.

Receiver goodput counts verified payload bits divided by receiver transfer
time. TCP samples come from the sender's established socket via `ss -nti`
once per second. Each sample confirms the selected algorithm and converts
the congestion window from segments to bytes using its current MSS.
MurmurTP supplies its own congestion window and smoothed RTT at the same
nominal interval. Samples outside the receiver payload interval are excluded.

A concurrent one-per-second ICMP echo measures the same route under load.
Each sender sample is paired with the nearest successful echo reply within
one second. The absolute difference between smoothed RTT and echo RTT is
a proxy for RTT estimation error. ICMP can experience different queuing from
data packets. Unmatched samples have blank reference and error fields. A
trial needs at least one pair and reports the mean error over its pairs.

`metrics.csv` includes warm-ups. `summary.csv` aggregates the three recorded
values per condition. `timeseries.csv` contains all recorded window, RTT and
probe samples. Goodput and RTT error plots show means and sample SD. The
time-series figure for each loss level shows recorded trial 1 for all three
transports, with each trace ending at its own completion time.

### E4 Ubuntu pilot results

All 12 routed 8 MiB transfers at 0% configured loss verified their payload
and WAN capture on the first attempt. The table uses the three recorded
trials per transport. All six summary rows report three observations.

| Transport | Mean receiver goodput (Mbit/s) | Mean absolute difference between sender SRTT and ping RTT (ms) |
|---|---:|---:|
| TCP Reno | 9.11 | 87.45 |
| TCP CUBIC | 8.66 | 49.86 |
| MurmurTP | 4.02 | 10.84 |

Reno had the highest receiver goodput in this pilot. MurmurTP's smoothed
RTT was closest to the concurrent ping observations. The [64 MiB results](../results/e4/README.md)
extend this comparison to 1%, 2% and 5% configured loss.
