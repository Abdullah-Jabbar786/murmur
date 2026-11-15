# AI-use disclosure

Fill this in as we go, in line with the course policy.

| Date | Who | Tool | What it was used for | How we checked or changed the output |
|---|---|---|---|---|
| 2026-11-15 | Abdullah (assisted; review pending) | OpenAI Codex | A1.1 TCP transport, behavior tests, usage documentation | Checked asyncio and Linux TCP_INFO documentation. Windows/Python 3.14: `python -m pytest -q -W error` gave 27 passed, 2 Linux-only skips; Python 3.10 syntax check passed. Linux Reno/CUBIC runtime checks and teammate review pending. |
