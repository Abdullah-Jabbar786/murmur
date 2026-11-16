# Weekly progress

One short line per person per week.

16 Nov 2026: Sprint 0 complete per team confirmation. A1.1 merged in PR #7
(`14677e3`), A1.2 in PR #8 (`0760813`), and A1.3 in PR #9 (`ce0c221`).
A1.4 merged in PR #10 (`d3cca91`), A1.5 in PR #11 (`1d152f5`).
A1.6 stop-and-wait UDP stage merged in PR #12 (`1bbcd4a`); acceptance:
1 MiB at 10% loss in both directions, SHA-256 verified.
H1.1 peer wire codec merged in PR #13 (`d38b41d`).
H1.2 deterministic file generator and metadata tool merged in PR #14 (`de760e8`).
H1.3 tracker server and async client merged in PR #15 (`78e2cba`).
H1.4 disk-backed piece store merged in PR #16 (`3bb4b4d`).
H2.1 single TCP download implemented; review and merge pending.
Intended branch: `majid/s2-single-download`. I1 verified on localhost: 64 MiB
transferred over real TCP, all 256 piece hashes and whole-file SHA-256 matched.
Protocol failures, corruption retry, resume, deadlines, and cancellation tested.
Windows verification: 298 passed, 2 Linux TCP tests skipped.
Linux runtime results have not been independently verified here.
Gate S1 is not yet complete: H2.1 review/merge and routed topology/evidence
remain outstanding. Tracker discovery, multi-peer transfers, algorithm
integration, and full event hooks remain future Sprint 2 work.

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
