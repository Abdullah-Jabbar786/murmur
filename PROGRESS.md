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
A2.3 randomized transport properties implemented; review and merge pending.
Initial focused run: 8 passed in 13.90s, covering 108 generated and 9 explicit examples.
Verified exact duplex streams across Stages 1..3 with independent impairments,
MSS/send/read boundaries, small buffers, stable EOF and payload accounting;
interruption preserves only valid prefixes, total loss times out, and resources
are cleaned up. Initial Windows full suite: 407 passed, 2 Linux TCP tests skipped.
PR #25 Linux/Python 3.11 CI then exposed a lost-cancellation race in Stage 3's
ACK wait. Replaced that wait with cancellation-propagating asyncio.wait and
explicit child-task cleanup; added two deterministic ACK/cancel regressions
and retained both CI seeds. Properties now cover 108 generated + 13 explicit
examples. Adaptive timing/congestion/flow control remain later work.
Cancellation-fix verification: 25 focused tests passed; Windows full suite
409 passed, 2 Linux TCP tests skipped in 115.03s. Python 3.10 syntax checks pass.
The fixed commit still needs its Linux/Python 3.11 CI rerun after pushing.
Linux runtime results have not been independently verified here.
Gate S1 is not yet complete: routed topology/evidence remain outstanding.
A2.3 review/merge and remaining transport work, tracker discovery, routed TCP swarm
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
