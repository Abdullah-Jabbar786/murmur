# Reproducing the setup and experiments

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
were measured on Ubuntu. E3–E5 measurements are pending.
For E3, start with the [five-leecher TCP pilot](../experiments/README.md#e3-scaling-experiment).
