# Randomized transport verification (A2.3)

`tests/test_murmurtp_properties.py` adds Hypothesis properties that exercise
actual localhost UDP connections. It covers Stage 1 stop-and-wait, Stage 2
cumulative-ACK windows, Stage 3 selective repeat, Stage 4 adaptive RTO and
Stage 5 congestion control and Stage 6 receive-window flow control.
PR #25 fixed an ACK/cancellation race exposed by Python 3.11 CI. Its
deterministic regressions in `tests/test_murmurtp_cancellation.py` now cover
Stages 3..6.

## Run in VS Code PowerShell

From the project root:

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_properties.py tests/test_murmurtp_cancellation.py -v -p no:cacheprovider --basetemp="$testTemp" --hypothesis-show-statistics
```

Expected: **22 passed**: fourteen parameterized property cases plus eight deterministic
ACK/cancel race cases. The properties exercise **192 generated examples
plus 22 explicit examples**, for 214 scenarios; the eight race cases are additional.
Hypothesis may also discard
generation attempts or run additional examples when shrinking a failure.
Runtime varies with scheduling and the generated impairment combinations.

No manual server, dataset, tracker, topology, Ubuntu or dashboard is required.
Each example creates a fresh asyncio loop and fresh ephemeral UDP endpoints.
Hypothesis is already a project dependency.

## Properties checked

| Property | Cases / examples | Required result |
|---|---|---|
| Duplex streams on Stages 1..6 | 6 cases; 20 generated + 3 explicit examples each | Both byte streams match exactly, no duplicates or extra bytes, stable EOF, exact payload counters |
| Interrupted Stage 3 transfers | 2 cases: cancellation and endpoint shutdown; 12 generated + 2 explicit examples each | Received data is only a valid prefix; send fails, no graceful EOF or false success |
| Total loss on Stages 1..6 | 6 cases; 8 generated examples each | Both blocked send and receive time out, no received payload or outstanding bytes remain |

Every example checks endpoint task/socket cleanup, cancellation of pending
impairment deliveries, canceled inactivity timers, and cleared outstanding work.
Stage 3..6 cleanup includes the SACK scoreboard and retry deadlines. Stages 4..6
use short test-only RTO bounds (5..40 ms, initially 10 ms) for this suite.
Stage 6 uses a 10 ms test-only probe interval; the default is one second.

## Python 3.11 cancellation regression

PR #25's first Linux/Python 3.11 run failed when cancellation coincided with an
ACK waiter finishing. This matches [CPython issue #86296](https://github.com/python/cpython/issues/86296):
older `asyncio.wait_for()` can lose the caller's cancellation in that race.
The send then continued until inactivity timeout or peer reset instead of
raising `CancelledError`. Increasing timeouts or accepting those errors would
hide the failed cancellation behavior.

Stage 3 now uses `asyncio.wait()` for the ACK/deadline wait and explicitly cancels
and awaits its child event waiter. The regression schedules cancellation when
the ACK event's waiter completes, testing both a partial and final ACK. It
requires `CancelledError`, an aborted connection, cleared pending state and no
leaked waiter. The two CI seeds (0 and 1, window 2, read size 17, one-MSS prefix)
are also retained as explicit property examples. Python 3.10 remains supported;
CI's Python 3.11 version and cancellation assertions are unchanged.

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
Final swarm transport selection and Linux
network experiments remain later work.
