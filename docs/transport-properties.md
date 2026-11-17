# Randomized transport verification (A2.3)

`tests/test_murmurtp_properties.py` adds Hypothesis properties that exercise
actual localhost UDP connections. It covers Stage 1 stop-and-wait, Stage 2
cumulative-ACK windows, and Stage 3 selective repeat. No transport implementation
changes were required by the successful test run.

## Run in VS Code PowerShell

From the project root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_properties.py -v -p no:cacheprovider --basetemp="$testTemp" --hypothesis-show-statistics
```

Expected: **8 passed**. These are eight parameterized property-test cases, not
eight network scenarios. A successful run exercises **108 generated examples
plus 9 explicit examples**, for 117 scenarios. Hypothesis may also discard
generation attempts or run additional examples when shrinking a failure.
The final focused Windows run passed in **13.90 seconds**.

No manual server, dataset, tracker, topology, Ubuntu or dashboard is required.
Each example creates a fresh asyncio loop and fresh ephemeral UDP endpoints.
Hypothesis is already a project dependency.

## Properties checked

| Property | Cases / examples | Required result |
|---|---|---|
| Duplex streams on Stages 1, 2 and 3 | 3 cases; 20 generated + 3 explicit examples each | Both byte streams match exactly, no duplicates or extra bytes, stable EOF, exact payload counters |
| Interrupted Stage 3 transfers | 2 cases: cancellation and endpoint shutdown; 12 generated examples each | Received data is only a valid prefix; send fails, no graceful EOF or false success |
| Total loss on Stages 1, 2 and 3 | 3 cases; 8 generated examples each | Both blocked send and receive time out, no received payload or outstanding bytes remain |

Every example checks endpoint task/socket cleanup, cancellation of pending
impairment deliveries, canceled inactivity timers, and cleared outstanding work.
Stage 3 cleanup includes its SACK scoreboard and retry deadlines.

## Generated input space

The duplex property independently varies each direction's impairment settings:

- Loss: 0..15%; duplication: 0..40%; reordering: 0..50%.
- Base delay and jitter: 0..2 ms; additional reorder delay: 1..5 ms.
- UDP shim seeds, binary payloads up to 4 MSS + 17 bytes per direction,
  and up to three split points for repeated `send()` calls.
- Window sizes 1, 2, 4, 16 and 32 on windowed transports; receive buffers of
  1, 2, 4, 16 or 64 MSS; read sizes from 17 to 65,536 bytes.

Explicit examples include empty streams, sends split just below/on/above MSS,
asymmetric impairments, and a payload exceeding the largest packet window
while the receive buffer holds only two MSS. The byte oracle is the original
concatenated application payload, not the connection's internal bookkeeping.

Handshakes are established before enabling generated impairments. These
properties target DATA/ACK/FIN behavior; existing stage tests separately cover
lost handshake packets. Interrupted-transfer cases use a deterministic permanent
DATA hole after a varying valid prefix, with delayed/reordered/duplicated control
traffic. They wait for the entire chosen prefix to arrive before interrupting,
then verify its exact bytes and receive counter. Total-loss cases drop all packets after establishment and require the
configured inactivity failure instead of pretending delivery must succeed.

## Repeatability and scope

Settings use `derandomize=True`, `database=None`, bounded example counts, and
per-operation asyncio timeouts. Hypothesis's wall-clock deadline is disabled
because operating-system scheduling varies. Tests do not share fixtures or
event loops between generated examples. A failing property prints a falsifying
example and Hypothesis attempts to shrink it.

Input generation is reproducible for the same Hypothesis version and test code;
seeded impairments do not make operating-system packet scheduling deterministic.
Do not interpret a passing finite sample as proof for every possible network
condition or arbitrary sustained loss. These tests complement the Stage 2
16 MiB and Stage 3 64 MiB acceptance cases rather than replacing them.

This adds correctness coverage, not routed throughput or fairness evidence.
Adaptive RTO, congestion/flow control, swarm transport selection and Linux
network experiments remain later work.
