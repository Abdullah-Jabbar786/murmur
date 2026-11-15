# AI-use disclosure

Fill this in as we go, in line with the course policy.

| Date | Who | Tool | What it was used for | How we checked or changed the output |
|---|---|---|---|---|
| 2026-11-15 | Abdullah (assisted; review pending) | OpenAI Codex | A1.1 TCP transport, behavior tests, usage documentation | Checked asyncio and Linux TCP_INFO documentation. Windows/Python 3.14: `python -m pytest -q -W error` gave 27 passed, 2 Linux-only skips; Python 3.10 syntax check passed. Linux Reno/CUBIC runtime checks and teammate review pending. |
| 2026-11-15 | Abdullah (assisted; review pending) | OpenAI Codex | A1.2 25-byte header codec, checksum validation, property tests, documentation | Compared with frozen wire spec and RFC 1071; independent wire/checksum vectors and malformed-packet tests. Windows/Python 3.14 full suite: 43 passed, 2 existing Linux TCP skips, warnings treated as errors; Python 3.10 syntax check passed. A1.1 merge confirmed at `14677e3`; A1.2 review pending. |
| 2026-11-15 | Abdullah (assisted; review pending) | OpenAI Codex | A1.3 seeded UDP loss shim, tests, usage documentation | Checked asyncio UDP/timer documentation. Verified loss rates over 10,000 sends per seed, deterministic impairment traces, cancellation, byte preservation, and real UDP exchange. Windows/Python 3.14 full suite: 52 passed, 2 existing Linux TCP skips with warnings treated as errors; Python 3.10 syntax check passed. A1.2 merge confirmed at `0760813`; A1.3 review pending. |
| 2026-11-15 | Abdullah (assisted; review pending) | OpenAI Codex | A1.4 rarest-first selector, availability counting, tests, integration notes | Checked master plan section 7.3. Verified filtering, partial-piece priority, seeded ties, changing availability, and 150 randomized swarm snapshots. Windows/Python 3.14 full suite: 66 passed, 2 existing Linux TCP skips with warnings treated as errors; Python 3.10 syntax check passed. A1.3 merge confirmed at `ce0c221`; A1.4 review pending. |
