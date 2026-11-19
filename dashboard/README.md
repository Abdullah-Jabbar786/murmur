# Murmur dashboard

From the repository root, start the local review server in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m dashboard.server --demo --port 18765
```

Open **http://127.0.0.1:18765/** in Chrome. Choose TCP or MurmurTP and click
**Run transfer**. A seed serves an 8 MiB file, split into 256 pieces, to one
leecher, which also serves a second leecher. These are real SwarmNode instances
using ephemeral localhost ports and separate disk-backed files. A successful
run ends with two verified downloads. Switch protocols and run again to compare.
The view retains the most recent run until another starts. Stop the server with
Ctrl+C to release its temporary files.

## Watch your own run

Start peers with the existing `peer.node swarm --run-dir` option. Use separate
subdirectories per peer on localhost, or a common directory for distinct IPs.
Set the same `MURMUR_T0` for all writers when comparing times across nodes.
Point the server at the parent of those directories:

```powershell
.\.venv\Scripts\python.exe -m dashboard.server results/my-run --port 18765
```

New swarm joins report the piece count, piece size and file length. For older
logs supply `--metadata path/to/metadata.json`, or place the dataset's
`metadata.json` in the run directory. A fresh directory per run keeps the
measurements comparable. Watch mode is read-only and can open completed runs.
To inspect a deterministic synthetic fixture, generate it into an empty folder
with `python -m dashboard.mock_events --output results/raw_tmp/mock-review`
and point the dashboard at that folder.

## Reading the screen

- Select a peer above the piece map or click a peer name in the table. The grid
  follows verified pieces, selections and hash failures for that peer. Optional
  metrics display a dash until supplied.
- The rings show selected-peer verification, swarm-wide leecher verification,
  and the fraction of leechers complete. Verified goodput counts newly verified
  leecher bytes over the shared interval from the first leecher joining to the
  last completion (or latest event). Initial seed and resume bytes are excluded.
- Retransmissions use the latest cumulative counter per connection, replaced
  by the final summary when available. Upload slots show how many destinations
  were last signaled unchoked out of those signaled by the selected peer.
- The chart selector switches between measured congestion window per connection
  and the selected peer's download/upload rates. Windows kernel TCP may omit
  its window and RTT. The chart retains the latest 500 samples per series.
- The event feed shows application events. The topology graphic depicts the
  planned Ubuntu lab, and the packet anatomy describes the MurmurTP wire format.
  The local review uses localhost. WAN savings require measurements across runs.

The frontend uses the supplied HTML design and bundled Inter and JetBrains Mono
fonts. Font licenses accompany the assets. All dashboard assets are local.

## Event API

`GET /api/state` returns run information, dataset dimensions, an event page,
a revision and a cursor. Poll once per second using
`GET /api/events?since=<cursor>&revision=<revision>`. Drain additional pages
when `more` is true. Each event carries a source path relative to the run;
this distinguishes localhost peers with the same IP. A changed revision
indicates a fresh snapshot. The reader tracks file offsets, defers an
unfinished last line, and counts malformed complete rows. It keeps parsed
events for the current run in memory to serve new viewers.
