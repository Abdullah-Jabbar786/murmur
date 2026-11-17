# Weekly progress

One short line per person per week.

17 Nov 2026: Sprint 0 complete per team confirmation. A1.1 merged in PR #7
(`14677e3`), A1.2 in PR #8 (`0760813`), and A1.3 in PR #9 (`ce0c221`).
A1.4 merged in PR #10 (`d3cca91`), A1.5 in PR #11 (`1d152f5`).
A1.6 stop-and-wait UDP stage merged in PR #12 (`1bbcd4a`); acceptance:
1 MiB at 10% loss in both directions, SHA-256 verified.
H1.1 peer wire codec merged in PR #13 (`d38b41d`).
H1.2 deterministic file generator and metadata tool merged in PR #14 (`de760e8`).
H1.3 tracker server and async client merged in PR #15 (`78e2cba`).
H1.4 disk-backed piece store merged in PR #16 (`3bb4b4d`).
H2.1 single TCP download merged in PR #17 (`c64f3cf`). I1 verified on localhost:
64 MiB over real TCP, all 256 piece hashes and whole-file SHA-256 matched.
H2.2 multi-peer transfers merged in PR #18 (`418bbe5`).
H2.3 client-server mode merged in PR #19 (`8ca36a8`).
H2.4 peer event logging merged in PR #20 (`dbf15c8`); local main also includes
dashboard styling commit `f35f6cd`.
H2.5 whole-swarm Loopback tests merged in PR #21 (`ac7eed2`).
A2.1 algorithm integration merged in PR #22 (`94fbda3`). SwarmNode
uses live rarest-first selection, partial-piece priority, exclusive reservations,
configurable 1..32 request windows (default 16), and timed 4+1 upload slots.
The swarm CLI reads pipeline/choking settings; piece_select events expose choices.
A2.1 focused tests: 13 passed. One seed and ten leechers still pass in swarm and
client-server modes with independent hashes, payload accounting, and cleanup.
CI payload: 2 MiB + 37 bytes; each scenario asserts a runtime below 30 seconds.
A2.2 first step merged in PR #23 (`10b0310`). Separate Stage 2 UDP
transport provides a fixed 1..32-packet send window (default 16), cumulative
ACKs, bounded out-of-order buffering, and timeout retransmission of the
unacknowledged window. Stage 1 remains available for regression/comparison.
Stage 2 focused tests: 21 passed in 13.66s. Verified 16 MiB at 5% loss in both
directions with matching SHA-256; Stage 2 beats Stage 1 in a separate controlled
delay test.
A2.2 Stage 3 merged in PR #24 (`0fbf831`). Separate selective-repeat
transport uses the v1 SACK bitmap, a bounded sender scoreboard, independent
packet deadlines, selective timeout retries and the three-later-SACK gap rule.
Stage 3 focused tests: 15 passed in 19.03s. A2.2 acceptance: 64 MiB over localhost
UDP with 5% loss, 5% reorder and 1% duplication on both endpoints, SHA-256 and
payload counters verified (acceptance case 17.76s).
A2.3 randomized transport properties and cancellation fix merged in PR #25
(`a7a0f75`) and pulled into local main. Both CI checks passed per team confirmation.
Initial focused run: 8 passed in 13.90s, covering 108 generated and 9 explicit examples.
Verified exact duplex streams across Stages 1..3 with independent impairments,
MSS/send/read boundaries, small buffers, stable EOF and payload accounting;
interruption preserves only valid prefixes, total loss times out, and resources
are cleaned up. Initial Windows full suite: 407 passed, 2 Linux TCP tests skipped.
PR #25 Linux/Python 3.11 CI then exposed a lost-cancellation race in Stage 3's
ACK wait. Replaced that wait with cancellation-propagating asyncio.wait and
explicit child-task cleanup; added two deterministic ACK/cancel regressions
and retained both CI seeds. At merge, properties covered 108 generated + 13 explicit
examples. Congestion/flow control remain later work.
Cancellation-fix verification: 25 focused tests passed; Windows full suite
409 passed, 2 Linux TCP tests skipped in 115.03s. Python 3.10 syntax checks pass.
The team confirmed the fixed commit's Linux/Python 3.11 CI passed before merging;
that CI run has not been independently inspected here.
A3.1 timing portion merged in PR #26 (`5f74697`), pulled into local main.
Separate Stage 4 AdaptiveRtoTransport provides:
Jacobson/Karels SRTT/RTTVAR, 1s initial RTO with 200ms..5s bounds, Karn's rule
for timeout and fast retries, and exponential backoff once per timeout round.
DATA deadlines remain independent; FIN uses learned timing. Stats expose SRTT,
variation, current RTO and sample count. Stage 1..3 baselines remain available.
Stage 4 is included in randomized duplex/total-loss properties and deterministic
ACK/cancel regressions. At the Stage 4 commit, verification comprised
41 new timing cases plus 14 property/cancellation cases.
Full Windows/Python 3.14 suite: 454 passed, 2 Linux TCP tests skipped in 116.59s,
with warnings treated as errors. Python 3.10 syntax checks passed.
A3.1 congestion portion merged in PR #27 (`7d2fa45`), pulled into local main.
Separate Stage 5 CongestionTransport provides:
initial 10-packet window, slow start, additive increase, one SACK reduction per
recovery boundary and one reset per timeout round. New DATA admission respects
both floor(cwnd) and the configured sequence horizon. Stats report cwnd in bytes.
Focused congestion tests: 13 passed in 2.93s. The 1 MiB acceptance case at 2%
outgoing loss on both localhost UDP endpoints verified SHA-256 and repeated
window growth/reductions; CSV trace is saved in pytest's temporary directory.
Randomized duplex/total-loss properties now cover Stages 1..5; deterministic
ACK/cancel regressions at that commit covered Stages 3..5. A3.1 is merged.
No routed E4 or fairness result is claimed.
Stage 5 Windows/Python 3.14 verification: 471 passed, 2 existing Linux TCP skips
in 123.88s with warnings treated as errors. The five changed Python files pass
Python 3.10 syntax checks. Git diff whitespace checks pass.
A3.2 first portion merged in PR #28 (`29eaedc`), pulled into local main.
Stage 6 FlowControlTransport provides advertised
receive slots limit new sends alongside cwnd and the sequence horizon. Reads
reopen the window with ACK updates; empty-DATA persist probes use the frozen
one-second default without consuming bytes/sequence numbers. Zero-window DATA
timers pause, while inactivity still bounds stalled readers. Tests cover lost
updates/replies, partial-slot reads, buffer bounds, cancellation, shutdown and
FIN with unread data. Properties now cover Stages 1..6 and ACK/cancel regressions
Stages 3..6.
Stage 6 Windows/Python 3.14 full regression run: 496 passed, 2 existing Linux TCP
skips in 136.14s. Final review added the TIME_WAIT idle-timer guard/regression;
final focused verification: 44 passed in 31.75s. Both runs used warnings as errors.
Five changed Python files pass Python 3.10 syntax checks; Linux CI pending.
A3.2 Stage 7 implemented: public Contract 2 MurmurTransport/MurmurConnection,
validated transport factory, TCP/MurmurTP swarm CLI selection and matching default
ports. Default config now selects TCP (the CLI previously ignored loopback).
Existing PeerEvents emits real MurmurTP metrics and exact final retransmission
summaries without a second sampler. Corrected a HAVE/NOT_INTERESTED backpressure
race exposed by the larger UDP swarm, with deterministic regression coverage.
Focused verification: 41 passed in 45.15s; both distribution modes passed the same
one-seed/ten-leecher test on Loopback and localhost MurmurTP (2 MiB + 37 bytes).
Independent hashes, exact payload accounting, relay uploads, zero client-server
leecher uploads, logs and cleanup verified. Smaller relay/event tests also pass
at 5% loss, 5% reorder and 1% duplication. A3.2 local implementation is complete;
Stage 7 review/merge and Linux CI remain pending. Routed A3.3 evidence is outstanding.
Stage 7 full Windows/Python 3.14 regression: 523 passed, 2 existing Linux TCP
skips in 182.23s with warnings treated as errors. Nine changed Python files pass
Python 3.10 syntax checks; Git whitespace checks pass.
Gate S1 is not yet complete: routed topology/evidence remain outstanding.
Remaining transport work, tracker discovery, routed TCP swarm
evidence, harness/dashboard integration, and the remaining Gate S2 checks are pending.

| Week | Person A (Transport) | Person B (Swarm) | Person C (Infra) |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
