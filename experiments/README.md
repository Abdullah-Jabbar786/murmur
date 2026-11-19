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

## Offline analysis

Offline analysis for experiments E1 to E5:

- `aggregate.py`: validate normalized per-run metrics and write CSV statistics.
- `plot.py`: produce PNG charts with mean/sample-SD bars and explicit data labels.
- `mock_results.csv`: synthetic acceptance data, never measured network results.

See [results-analysis.md](../docs/results-analysis.md) for commands, schema and
tests. Raw-log export and the E1–E5 experiment series are pending.
