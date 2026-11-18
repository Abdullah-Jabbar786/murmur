# Whole-swarm acceptance (H2.5 and A3.2)

`tests/test_swarm_full.py` runs **one seed and ten leechers** through the real
SwarmNode, message codec, piece storage, and event logger, using an isolated
LoopbackRegistry or real localhost MurmurTP endpoints for each case. It is collected automatically by the existing
`make test` command and GitHub CI's `python -m pytest -q`; no workflow change or
separate server process is needed.

## Run in VS Code

From the project root in PowerShell:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_full.py -v --durations=2 -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **4 passed**: both distribution modes on both transports. Each case
runs eleven nodes. The tests generate and cleanly close their own files,
logs, transports, connections, and tasks. No manual dataset, tracker, or network
namespace setup is required. Pytest may retain temporary files after success.

## Scenarios

| Mode | Connections | Additional acceptance checks |
|---|---|---|
| `swarm` | Only two leechers connect to the seed; the other eight use relay paths, with extra cross-links for multiple sources | At least eight file-equivalents are uploaded by leechers; no duplicate accepted blocks |
| `client_server` | All ten leechers connect directly to the origin seed | Every leecher uploads zero payload bytes; seed uploads exactly ten file-equivalents |

In the swarm case, nonseed links are established before introducing seed data.
This exercises HAVE propagation as pieces become available, rather than starting
every relay with a completed file. Different sources may serve different pieces,
while reservations prevent overlapping downloads of the same piece.

All cases verify:

- Every leecher owns all pieces and has zero bytes left.
- Independent disk reads match every metadata piece hash and the source's
  whole-file SHA-256, with exact file size and EOF.
- Each leecher accepts exactly one file's payload, with no hash failures.
- Total sent payload equals the ten accepted file-equivalents plus explicitly
  counted `bytes_discarded` from late responses to requests abandoned on CHOKE.
  Choking and retrying can produce these extra replies even on a lossless link.
  Only a matching abandoned request authorizes a discard; unsolicited replies
  still fail. The origin's two-file bound includes this accounted overhead.
- Node summaries match payload counters and report completion and zero hash
  failures. Loopback retransmissions are zero; MurmurTP reports actual counters.
- Each node logs one completion and one final summary, plus all verified pieces.
- Shutdown removes registry listeners or UDP sockets and releases session tasks, samplers,
  reservations, connection tracking, and file handles.

## Size and time limits

The deterministic CI payload is **2 MiB + 37 bytes**, using the project's normal
256 KiB pieces and 16 KiB blocks. It has eight full pieces and a 37-byte final
piece. Each scenario uses approximately 22 MiB of payload disk space plus logs.
The smaller payload keeps CI fast while testing ten concurrent leechers and
the final short-block boundary. It does not replace the 64 MiB experiment runs.

Each Loopback scenario retains a 20-second transfer deadline and total runtime
below 30 seconds. MurmurTP scenarios allow 60 seconds for transfer and 70 seconds
total; all cases have a five-second cleanup deadline. UDP test timing uses short
explicit RTO/probe overrides and no injected loss; separate Stage 7 relay/event
tests inject 5% loss. All cases include file verification.
Timeout diagnostics show each peer's remaining bytes, recent errors, sent,
accepted and discarded byte counts, and outstanding piece reservations.
The initial Windows/Python 3.14 run passed both scenarios in **4.57 seconds**
total; runtime varies with the machine. `--durations=2` reports measured case
times. The under-30-second assertion applies to each Loopback whole-swarm scenario,
not to the entire repository's growing test suite. Stage 7 focused UDP timings
were 19.36s (swarm) and 18.32s (client-server); these are localhost test runtimes.

## What this establishes

H2.5/A3.2 check whole-swarm correctness and cleanup on in-memory and UDP transports.
The same tests now run with A2.1's rarest-first selection, timed choking, and
pipelining enabled. They do **not** establish Gate S2 by themselves: routed TCP,
tracker discovery, captures, the unattended harness, and dashboard evidence
still require their own work. Linux topology
and network evidence also remain outstanding for Gate S1. No real link-speed
or E3 performance claims are made from Loopback timing.
