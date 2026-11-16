# Client-server baseline (H2.3)

`MASTER_PLAN.md` section 7.4 defines `"mode": "client_server"` for the E3
comparison: leechers connect only to the origin seed and upload no file data.
This mode is now available in `SwarmNode` and the `peer.node swarm` command.
Default configuration remains `"mode": "swarm"`.

## Select the behavior

| Setting | Purpose |
|---|---|
| JSON `"mode": "swarm"` | Peers may share verified pieces with neighbors |
| JSON `"mode": "client_server"` | Only the designated origin seed uploads payload |
| `--config path.json` | Load a JSON config for the swarm command; currently its distribution `mode` is used |
| `--distribution-mode swarm` or `client_server` | Override the config's distribution mode |
| `--mode create`, `resume`, or `seed` | Existing file-opening option; separate from distribution mode |
| `--origin-seed IPv4:port` | Sole permitted destination for a client-server leecher |

Without `--config`, the swarm command reads `config/default.json`. An explicitly
supplied config must exist and be a JSON object. Invalid modes and missing or
conflicting origin-seed options are rejected before creating a download file.
Other config fields, including transport selection, are not wired into this CLI
by H2.3; the CLI still uses TCP. The original `seed` and `download` commands
remain the H2.1 baseline and do not read these new swarm options.

The origin seed uses `--mode seed`, which verifies an existing complete file.
Client-server `create` and `resume` nodes are leechers for their entire lifetime,
even if the resumed file was already complete. Completion does not promote
them into uploading seeds. Swarm mode retains its previous sharing behavior.

## Optional three-terminal demo

Generate a fresh dataset once from the project root:

```powershell
.\.venv\Scripts\python.exe -m peer.metadata --size 8MiB --seed 42 --output testdata/h23/payload.bin --metadata testdata/h23/metadata.json
```

Reuse these files if they already exist. In terminal 1, start the origin:

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --distribution-mode client_server --mode seed --metadata testdata/h23/metadata.json --file testdata/h23/payload.bin --port 6881
```

In terminal 2, start leecher A:

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --distribution-mode client_server --origin-seed 127.0.0.1:6881 --metadata testdata/h23/metadata.json --file testdata/h23/a.bin --port 6882
```

In terminal 3, start leecher B, also connected directly to the origin:

```powershell
.\.venv\Scripts\python.exe -m peer.node swarm --distribution-mode client_server --origin-seed 127.0.0.1:6881 --metadata testdata/h23/metadata.json --file testdata/h23/b.bin --port 6883
```

Both leechers should print `Verified 32 pieces; client-server uploads disabled`.
They remain running until Ctrl+C but do not accept or serve peer downloads.
On shutdown each leecher's `sent` payload counter should be **0**. These counters
exclude handshake, HAVE, CHOKE, and other control traffic: zero payload upload
does not mean zero network bytes sent.

After both finish, compare all three files in another terminal:

```powershell
Get-FileHash testdata/h23/payload.bin, testdata/h23/a.bin, testdata/h23/b.bin -Algorithm SHA256
```

All hashes must match. Outputs stay under Git-ignored `testdata/`. Use a new
output filename for a fresh run, or `--mode resume` to continue an existing
download. Default create mode never overwrites files.

To exercise JSON selection instead of the explicit distribution flag:

```powershell
'{"mode":"client_server"}' | Set-Content -Encoding ascii testdata/h23/client-server.json
```

Replace `--distribution-mode client_server` in a command with
`--config testdata/h23/client-server.json`. A CLI distribution flag takes
precedence if both are supplied.

## Enforcement and Python API

```python
origin = SwarmNode(seed_store, mode="client_server", is_seed=True)
await origin.start(("127.0.0.1", 6881))

leecher = SwarmNode(download_store, mode="client_server",
                    seed_address=origin.address)
await leecher.start(("127.0.0.1", 6882))
await leecher.connect(origin.address)
await leecher.wait_complete(timeout=30)
# Caller closes both nodes before closing their stores.
```

Import `SwarmNode` from `peer.swarm`. A caller-supplied LoopbackTransport works
with the same options. The origin role is explicit and requires every piece
to be verified. Client-server seeds accept connections rather than initiating
them. Leechers reject other outbound addresses before calling the transport,
and immediately close inbound connections.

Upload policy is enforced at both unchoking and block-serving boundaries.
Even an INTERESTED or REQUEST message from the configured origin receives
CHOKE, never a PIECE from the leecher. `set_upload_allowed(..., True)` cannot
override the restriction. Distribution mode and origin role are read-only
properties for the lifetime of a node; create a new node to change modes.

The origin is identified by the configured numeric IPv4 endpoint, with the
normal info_hash handshake check. This is controlled-lab routing policy, not
cryptographic peer authentication. Automatic tracker discovery and reconnect
scheduling remain separate work. E3 timing/bandwidth experiments and Linux
impairment evidence have not been performed by these correctness tests.

## Tests

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_swarm_client_server.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **20 passed**. Tests start their own endpoints. They verify two
leechers downloading on Loopback and TCP with zero payload upload, rejection
of other peers, origin-seed upload probes against partial and completed
leechers, immutable roles, completed resume behavior, invalid settings, JSON
selection, CLI override, and unchanged upload behavior when explicitly choosing
swarm mode.
