# Reproducing the setup and experiments

For the Ubuntu routed lab, start with the [topology guide](../infra/README.md).
From the repository root, run `sudo make test-topo` to exercise setup,
connectivity, resizing and teardown. From a clean lab run `sudo make test-netem`
for the M1.2 WAN/seed bandwidth, delay, loss and clear checks. Experiment
reproduction is pending.
