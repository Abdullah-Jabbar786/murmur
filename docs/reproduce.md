# Reproducing the setup and experiments

## Start from a fresh Ubuntu installation

Use Ubuntu 24.04 with a user who can run `sudo`. In a VM, keep the
checkout, virtual environment and experiment output on its Linux filesystem.
From the Ubuntu terminal, install the tools used by the lab and test suite.

```bash
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y git make python3 python3-venv iproute2 ethtool procps util-linux iputils-ping traceroute iperf3 tcpdump tshark
cd ~
git clone https://github.com/Abdullah-Jabbar786/murmur.git murmur-reproduce
cd murmur-reproduce
grep PRETTY_NAME /etc/os-release
git rev-parse HEAD
make setup
source .venv/bin/activate
MURMUR_REQUIRE_TSHARK=1 make test
```

To check a pull request before merge, add `--branch BRANCH_NAME` to the
clone command. Save the printed commit and the final pytest summary. Run
the next commands from the same checkout, with the lab idle. The topology
and shaping checks print their own PASS lines and remove their namespaces.

```bash
sudo make test-topo
sudo make test-netem
make -n experiments
```

The dry run prints four runner commands in order: E1/E2, E3, E4 and E5.
For a short end-to-end check, run a single E1 condition at 8 MiB. This
exercises the routed transfer, capture, checkpoints, CSV export and plots.

```bash
sudo .venv/bin/python experiments/run.py --sweep results/reproduce-work/pilot --cell E1-tcp-loss0 --size 8MiB --keep-seed-only
grep 'distribution_time_s' results/reproduce-work/pilot/summary.csv
```

Expect four checkpointed trials, each with ten verified downloads. The
distribution-time row should show `runs=3`, `n=3` and `missing=0`. For
M4.2, record the Ubuntu version, commit, pytest summary, lab PASS lines and
pilot row. Identify whether the check used a fresh VM or a separate clone
on an existing VM.

## Reproduce all five experiments

### M4.2 checkout verification

Majid followed the setup instructions in a new `~/murmur-reproduce` clone
at revision `3c2348a` on his existing Ubuntu 24.04.3 LTS VM. The new virtual
environment passed 677 tests with two skips in 300.04 seconds, with
`MURMUR_REQUIRE_TSHARK=1`. The topology checks passed, including resizing
to 20 leechers and restoring the original namespace inventory. The Make dry
run printed E1/E2, E3, E4 and E5 in order.

The shaping test required three attempts. The first measured reverse WAN
goodput at 7.20 Mbit/s, below the 8 Mbit/s acceptance threshold. The second
passed both WAN directions at 9.09 and 9.16 Mbit/s, but measured seed-to-LAN
goodput at 7.48 Mbit/s. A separate 20-second seed diagnostic measured
8.51 Mbit/s receiver goodput, with the configured 10 Mbit/s TBF and 66 queue
drops. The third complete shaping test reported PASS, including reapply and
clear. The cause of the short-test variability remains unresolved.

The 8 MiB E1 TCP pilot at 0% configured loss and 20 ms RTT passed its warm-up
and three recorded trials on their first attempts. Each verified ten files
and its WAN capture, removed the topology and retained the verified seed.
The supplied summary row reports mean distribution time 30.642 seconds,
sample standard deviation 2.458439 seconds, minimum 29.098 and maximum 33.477,
with three observations and zero missing. Reports are under
`results/reproduce-work/pilot/` in that checkout.

This evidence covers a fresh checkout on an existing VM. A fresh-VM test
and execution of the combined 140-trial command remain pending. The published
E1–E5 results came from the individual experiment runs.

### Full sweep command

From the repository root, check available space and run the full sweep.

```bash
df -h .
sudo make experiments
```

The command runs 35 conditions at 64 MiB, each with one warm-up and three
recorded trials (140 trials in total). E1/E2 and E3 keep only the verified
seed payload after each successful trial. Allow space for captures, logs,
failed attempts and active transfers as well as the retained payloads.
Each stage writes its own reports under `results/reproduce-work/`.

| Stage | Conditions | Output subdirectory | Standalone target |
|---|---:|---|---|
| E1/E2 loss and RTT | 12 | `e1-e2/` | `experiments-e1-e2` |
| E3 distribution scaling | 8 | `e3/` | `experiments-e3` |
| E4 single-flow transport | 12 | `e4/` | `experiments-e4` |
| E5 two-flow fairness | 3 | `e5/` | `experiments-e5` |

For another Linux disk, supply its output path, for example
`sudo make experiments REPRO_DIR=/mnt/data/murmur-reproduction`.
To pause, press Ctrl+C once and wait for the shell prompt. Repeat the same
command on the same commit. The harness skips verified trials, retries the
unfinished trial in a new attempt directory and regenerates the reports.
If a stage fails, later stages wait until the failed stage passes on rerun.

After completion, the following command lists the four summary tables. Each
contains the means and sample standard deviations for its recorded trials.
Use the [published results](../results/README.md) for comparison. Timing and
goodput will vary with the VM and kernel.

```bash
wc -l results/reproduce-work/{e1-e2,e3,e4,e5}/summary.csv
```

For the default matrix the expected counts, including headers, are 277,
189, 25 and 10 respectively. The standalone targets use the output paths
in the individual guides below.

## Individual experiments and lab checks

For the Ubuntu routed lab, start with the [topology guide](../infra/README.md).
From the repository root, run `sudo make test-topo` to exercise setup,
connectivity, resizing and teardown. From a clean lab run `sudo make test-netem`
for the M1.2 WAN/seed bandwidth, delay, loss and clear checks.

For one routed TCP swarm, use the [M2.2 runner instructions](../experiments/README.md#m22-routed-tcp-swarm).
The command starts the tracker and all eleven peers, saves their logs and
checks every downloaded file. With tcpdump installed, the same command
saves and verifies the [M2.3 WAN capture](../experiments/README.md#m23-wan-packet-capture).
For Sprint 3, follow the [routed MurmurTP commands](../experiments/README.md#routed-murmurtp-swarm-i3)
in order, starting with the 8 MiB run before the 64 MiB test at 5% WAN loss.
To verify interruption and restart, follow the [M3.3 resumable series](../experiments/README.md#m33-resumable-series).
For E1/E2, run the [8 MiB pilot and final sweep](../experiments/README.md#m41-loss-and-rtt-sweeps).
For a space-constrained VM, use the compact pilot command in that guide and
measure its disk usage before choosing the final output location.
The [final E1/E2 distribution times](../experiments/README.md#final-64-mib-distribution-times)
were measured on Ubuntu.
For E3, start with the [five-leecher TCP pilot](../experiments/README.md#e3-scaling-experiment).
The [final E3 results](../results/e3/README.md) cover four peer counts.
For E4, follow the [single-flow pilot and full sweep](../experiments/README.md#e4-single-flow-transport-comparison).
For E5, run the [two-flow fairness pilot](../experiments/README.md#e5-competing-flow-fairness) before the 64 MiB sweep.
