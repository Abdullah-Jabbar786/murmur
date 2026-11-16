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
H2.4 peer event logging implemented; review and merge pending. Suggested pushing
branch: `hammad/s2-events`. Verified Contract 3 lifecycle/piece events, periodic
rates and transport snapshots, retry accounting, unknown metrics, and one final
summary per node lifecycle. Logging is enabled with `swarm --run-dir`.
Windows verification: 348 passed, 2 Linux TCP tests skipped.
Linux runtime results have not been independently verified here.
Gate S1 is not yet complete: routed topology/evidence remain outstanding.
H2.4 review/merge, tracker discovery, algorithm integration, whole-swarm tests,
and the ten-leecher gate remain future work.

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
