# Whole-swarm Loopback acceptance (H2.5)

`tests/test_swarm_full.py` runs **one seed and ten leechers** through the real
SwarmNode, message codec, piece storage, and event logger, using an isolated
LoopbackRegistry for each case. It is collected automatically by the existing
`make test` command and GitHub CI's `python -m pytest -q`; no workflow change or
separate server process is needed.

## Run in VS Code

From the project root in PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_full.py -v --durations=2 -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **2 passed**. These are two complete eleven-node scenarios, not two
individual peer checks. The tests generate and cleanly close their own files,
logs, transports, connections, and tasks. No manual dataset, tracker, or network
namespace setup is required. Pytest may retain temporary files after success.

## Scenarios

| Mode | Connections | Additional acceptance checks |
|---|---|---|
| `swarm` | Only two leechers connect to the seed; the other eight use relay paths, with extra cross-links for multiple sources | At least eight file-equivalents are uploaded by leechers; no duplicate payload downloads |
| `client_server` | All ten leechers connect directly to the origin seed | Every leecher uploads zero payload bytes; seed uploads exactly ten file-equivalents |

In the swarm case, nonseed links are established before introducing seed data.
This exercises HAVE propagation as pieces become available, rather than starting
every relay with a completed file. Different sources may serve different pieces,
while reservations prevent overlapping downloads of the same piece.

Both cases verify:

- Every leecher owns all pieces and has zero bytes left.
- Independent disk reads match every metadata piece hash and the source's
  whole-file SHA-256, with exact file size and EOF.
- Each leecher receives exactly one file's payload, with no hash failures.
- Node summaries match payload counters and report completion, zero Loopback
  retransmissions, and zero hash failures.
- Each node logs one completion and one final summary, plus all verified pieces.
- Shutdown removes registry listeners and releases session tasks, samplers,
  reservations, connection tracking, and file handles.

## Size and time limits

The deterministic CI payload is **2 MiB + 37 bytes**, using the project's normal
256 KiB pieces and 16 KiB blocks. It has eight full pieces and a 37-byte final
piece. Each scenario uses approximately 22 MiB of payload disk space plus logs.
The smaller payload keeps CI fast while testing ten concurrent leechers and
the final short-block boundary. It does not replace the 64 MiB experiment runs.

Each scenario has a 20-second transfer deadline, a five-second cleanup deadline,
and an asserted total runtime below 30 seconds, including file verification.
Timeout diagnostics show each peer's remaining bytes and recent errors.
The initial Windows/Python 3.14 run passed both scenarios in **4.57 seconds**
total; runtime varies with the machine. `--durations=2` reports measured case
times. The under-30-second assertion applies to each whole-swarm scenario,
not to the entire repository's growing test suite.

## What this establishes

H2.5 checks whole-swarm correctness and cleanup on the in-memory transport.
The same tests now run with A2.1's rarest-first selection, timed choking, and
pipelining enabled. They do **not** establish Gate S2 by themselves: routed TCP,
tracker discovery, captures, the unattended harness, and dashboard evidence
still require their own work. Linux topology
and network evidence also remain outstanding for Gate S1. No real link-speed
or E3 performance claims are made from Loopback timing.
