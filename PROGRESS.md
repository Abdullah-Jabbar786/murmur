# Weekly progress

One short line per person per week.

16 Nov 2026: Sprint 0 complete per team confirmation. A1.1 merged in PR #7
(`14677e3`), A1.2 in PR #8 (`0760813`), and A1.3 in PR #9 (`ce0c221`).
A1.4 merged in PR #10 (`d3cca91`), A1.5 in PR #11 (`1d152f5`).
A1.6 stop-and-wait UDP stage merged in PR #12 (`1bbcd4a`); acceptance:
1 MiB at 10% loss in both directions, SHA-256 verified.
H1.1 peer wire codec merged in PR #13 (`d38b41d`).
H1.2 deterministic file generator and metadata tool merged in PR #14 (`de760e8`).
H1.3 tracker server and async client implemented; review and merge pending.
Intended branch: `majid/s1-tracker`. Verified peer discovery over real localhost
HTTP, lifecycle updates, 90-second expiry boundaries, and client error handling.
Windows verification: 257 passed, 2 Linux TCP tests skipped.
Linux runtime results have not been independently verified here.
Gate S1 is not yet complete: tracker review/merge, storage, single TCP download,
and routed topology/evidence remain outstanding in this checkout.

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
