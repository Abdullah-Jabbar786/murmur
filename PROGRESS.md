# Weekly progress

One short line per person per week.

16 Nov 2026: Sprint 0 complete per team confirmation. A1.1 merged in PR #7
(`14677e3`), A1.2 in PR #8 (`0760813`), and A1.3 in PR #9 (`ce0c221`).
A1.4 merged in PR #10 (`d3cca91`), A1.5 in PR #11 (`1d152f5`).
A1.6 stop-and-wait UDP stage merged in PR #12 (`1bbcd4a`); acceptance:
1 MiB at 10% loss in both directions, SHA-256 verified.
H1.1 peer wire codec merged in PR #13 (`d38b41d`).
H1.2 deterministic file generator and metadata tool implemented; review and
merge pending. Intended branch: `majid/s1-metadata-tool`. Acceptance: two
identical 64 MiB datasets, 256 independently checked SHA-256 piece hashes,
and matching canonical JSON/info_hash.
Windows verification: 218 passed, 2 Linux TCP tests skipped.
Linux runtime results have not been independently verified here.
Gate S1 is not yet complete: metadata review/merge, tracker, storage, single TCP
download, and routed topology/evidence remain outstanding in this checkout.

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
