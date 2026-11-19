# Reproducing the setup and experiments

For the Ubuntu routed lab, start with the [topology guide](../infra/README.md).
From the repository root, run `sudo make test-topo` to exercise setup,
connectivity, resizing and teardown. From a clean lab run `sudo make test-netem`
for the M1.2 WAN/seed bandwidth, delay, loss and clear checks.

For one routed TCP swarm, use the [M2.2 runner instructions](../experiments/README.md#m22-routed-tcp-swarm).
The command starts the tracker and all eleven peers, saves their logs and
checks every downloaded file. Packet capture and the E1–E5 runs come later.
