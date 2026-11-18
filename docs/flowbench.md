# A4.1 local foundation: one verified bulk flow

`murmurtp/flowbench.py` sends an existing file through the common Transport
interface, without swarm, tracker or piece-selection dependencies. The public
`run_once()` function accepts any Contract 2 endpoint and owns its cleanup.
The CLI supports real TCP and final MurmurTP. Loopback is available to callers
and tests in one process. This is the coding foundation for E4/E5, not completion
of their routed experiments, competing-flow analysis or fairness results.

## Quick verification

From the repository root in VS Code PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_flowbench.py -v -W error -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **24 passed**. Tests start/stop their own local endpoints and real CLI
processes; no manual terminal pair, dashboard or Ubuntu setup is required.
Checks include exact SHA-256/byte counts, bounded reads, real TCP/MurmurTP,
2% bidirectional shim loss/reorder, duplicate packets, bad/truncated framing,
corruption, missing/invalid receipts, changed source data, timeout/cancellation,
summary accounting, unknown TCP metrics and resource cleanup.

## Optional manual demonstration

Create a deterministic 1 MiB input once:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; p=Path('results/raw_tmp/flowbench-input.bin'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(bytes(range(256))*4096)"
```

Terminal 1, receiver (start first):

```powershell
.\.venv\Scripts\python.exe -m murmurtp.flowbench receive --transport murmurtp --host 127.0.0.1 --port 16882 --run-dir results/raw_tmp/flow-receiver
```

Terminal 2, sender:

```powershell
.\.venv\Scripts\python.exe -m murmurtp.flowbench send --transport murmurtp --host 127.0.0.1 --port 16882 --file results/raw_tmp/flowbench-input.bin --run-dir results/raw_tmp/flow-sender
```

Both commands exit after one verified transfer and graceful close, printing JSON
with `bytes: 1048576`, identical `sha256`, `verified: true`, measured seconds
and decimal Mbit/s. The receiver hashes and discards bytes; it does not create
a downloaded file. To compare functional TCP behavior, change both commands to
`--transport tcp`. Localhost numbers are not WAN experiment performance.

Use a new run directory per attempt: EventLog appends, so reusing a directory
combines multiple runs. Each invocation emits exactly one summary.

## Interface and configuration

- `--host`: local bind address for receiver, remote address for sender; default
  127.0.0.1. IPv4 only through existing transports.
- `--port`: override port; default TCP 6881 or UDP 6882. Receiver accepts 0 for
  an ephemeral port and prints its actual listening address. Sender rejects 0.
- `--config`: project-style JSON object accepted by `core/transports.py`.
  `--transport` optionally overrides its transport name. Default is TCP.
- `--timeout`: total setup/accept/connect/transfer/receipt/graceful-close budget,
  default 120 seconds. Timeout and cancellation tear down the endpoint.
- `--max-bytes`: receiver's positive payload bound, default 1 GiB. Oversized or
  empty declarations are rejected before readiness. This is a limit, not a
  whole-file allocation.
- `--file`: sender's existing nonempty file. The source is hashed before timing,
  then streamed through 64 KiB reads and checked again for concurrent changes.
- `--run-dir`: required event directory. In-process callers may supply their
  own EventLog and sampling interval (default one second).

`tcp_congestion_control: "reno"` or `"cubic"` uses the existing TCP configuration
on Linux. Unsupported explicit algorithms fail rather than silently falling back.
Transport settings and optional loss shim are passed unchanged to the factory.
Use matching transport settings at both ends; loss shim settings are outbound
per endpoint. Keep this tool's deadline appropriate to the file/path conditions.

## Benchmark framing and verification

The sender declares an 8-byte `MRFLOW01` magic, unsigned 64-bit byte count and
32-byte SHA-256, in network order. The receiver validates the declaration and
sends a one-byte readiness token. Payload follows, with at most 64 KiB held per
read/write iteration. The receiver computes SHA-256 and sends a `MRDONE01`
receipt containing verified size and digest only after a match. The sender
requires the matching receipt before declaring success. This is a single-flow
application protocol, not a change to the MurmurTP header or swarm wire codec.
It is an integrity check for controlled experiments, not peer authentication.

## Timing and logs

Both endpoints time locally using a monotonic clock:

- Receiver duration begins just before readiness and ends after payload hashing.
- Sender duration begins after readiness and ends after receipt verification.
- Neither duration includes file pre-hashing, connection setup or graceful close.
- `goodput_mbps = payload_bytes * 8 / seconds / 1,000,000` for that endpoint's
  interval. Sender/receiver durations need not be equal. Use receiver metrics
  consistently when comparing E4 completion goodput.

EventLog supplies Contract 3 `t`, `node`, `type` and supports a shared MURMUR_T0.
The tool emits join, periodic rate/transport samples, verified completion and a
final summary. Rate and summary counters exclude benchmark framing. Sender
bytes count successful payload send calls; receiver bytes count payload reads,
including a partial failed transfer. Transport counters include protocol traffic.
Missing TCP metrics remain null, especially on Windows; they are never invented.

Summary contains `complete`, `bytes_up`, `bytes_down`, `retx`, `hash_fails`,
`result` and `error`. Failure gives no successful result; a close failure can
follow a verified-completion event and still makes the final summary incomplete.
Sampling failures fail the run rather than silently dropping metric errors.

## Work still required for E4/E5

Routed bottleneck setup, synchronized competing-flow starts, Linux Reno/CUBIC
and `ss -ti` evidence, steady-state interval selection, Jain fairness, independent
warm-up/repetitions and analysis plots remain future work. Whole-transfer
goodput is not a substitute for steady-state fairness. These JSONL logs are not
automatically normalized into the A2.4 aggregation CSV yet.

## Recorded local verification

18 November 2026, Windows/Python 3.14: final focused suite **24 passed** in
4.86s, including real sender/receiver CLI processes with explicit transport
selection. Full suite **605 passed, 2 existing Linux TCP skips** in 196.43s
with warnings treated as errors. Both new Python files pass Python 3.10 syntax
checks; Git whitespace checks pass. Linux CI remains pending after push.
