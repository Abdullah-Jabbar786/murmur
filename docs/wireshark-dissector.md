# A3.4: MurmurTP Wireshark Lua dissector

`captures/murmurtp.lua` decodes the frozen v1 25-byte header and payload. It
registers UDP port 6882 and supports Decode As for custom ports. No system
plugin installation is required when loading the script with `-X lua_script:`.
The implementation uses Wireshark's documented
[Proto/dissector API](https://www.wireshark.org/docs/wsdg_html_chunked/lua_module_Proto.html),
[packet buffers](https://www.wireshark.org/docs/wsdg_html_chunked/lua_module_Tvb.html),
and [expert information](https://www.wireshark.org/docs/wsdg_html_chunked/lua_module_Tree.html).

## What it displays

| Display filter | Meaning |
|---|---|
| `murmurtp.flags` | Raw flags, with SYN/ACK/FIN/DATA/RST boolean subfields |
| `murmurtp.conn_id` | Connection ID |
| `murmurtp.seq`, `murmurtp.ack` | Packet sequence and next expected packet |
| `murmurtp.window` | Receive slots in packets, not bytes |
| `murmurtp.sack` | Raw 32-bit SACK bitmap |
| `murmurtp.sack_seq` | Repeated generated field: `cum_ack + 1 + bit` for each set bit |
| `murmurtp.timestamp` | Sender's low 32-bit millisecond clock; not measured RTT |
| `murmurtp.length`, `murmurtp.payload` | Declared payload length and captured payload bytes |
| `murmurtp.checksum`, `murmurtp.checksum_good` | Stored checksum and generated verification result |

The Internet checksum zeroes bytes 23 and 24 before comparing the calculated
numeric value with the stored value. The odd offset matters: checking the
checksum of the unmodified packet against zero is incorrect for this layout.
Validation matches the Python codec for empty, odd/even and 1200-byte payloads.

Expert warnings identify bad checksums, unknown/unexpected flags, control
payloads and SACK values outside the v1 non-wrapping sequence space. Length
errors and truncated captures are identified separately. Incomplete or
length-invalid packets do not get a misleading checksum-good/bad result.
Empty DATA packets remain valid window probes.

This is stateless inspection: it does not reassemble streams or infer packet
loss, retransmissions, congestion window or RTT from isolated headers.

## Verify in VS Code PowerShell

Wireshark/TShark with Lua support is required. This laptop already has TShark
4.6.8 with Lua 5.4.6 at `C:\Program Files\Wireshark\tshark.exe`.

```powershell
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
$env:MURMUR_REQUIRE_TSHARK = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_murmurtp_dissector.py -v -W error -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **16 passed**. Tests execute the actual Lua script through TShark.
The executable is found through `MURMUR_TSHARK`, PATH, or Windows Program Files.
If installed elsewhere, set `MURMUR_TSHARK` to its executable path. Without
TShark, local tests skip unless `MURMUR_REQUIRE_TSHARK=1`; CI installs TShark
and sets that variable so absence cannot silently skip acceptance.

For the full suite, this laptop's plotting dependency is in system Python.
Use its explicit path even when `.venv` is activated:

```powershell
$env:MURMUR_PLOT_PYTHON = "C:\Users\HP\AppData\Local\Programs\Python\Python314\python.exe"
.\.venv\Scripts\python.exe -m pytest -q -W error -p no:cacheprovider --basetemp="$testTemp-full"
```

## Generate and inspect the synthetic sample

```powershell
.\.venv\Scripts\python.exe -m captures.generate_sample --output results/mock-analysis/murmurtp-sample.pcapng
& "C:\Program Files\Wireshark\tshark.exe" -n -r results/mock-analysis/murmurtp-sample.pcapng -X lua_script:captures/murmurtp.lua -Y murmurtp -T fields -e frame.number -e murmurtp.flags -e murmurtp.seq -e murmurtp.ack -e murmurtp.sack_seq -e murmurtp.checksum_good
```

Expected: nine rows, every checksum correct. Packet 5 has bitmap `0x80000005`
with `cum_ack=1`, so the SACK sequence field lists **2,4,33**. Packet 6 is an
empty DATA probe. The sample illustrates handshake/data/SACK/FIN/RST fields;
it is not a complete bidirectional protocol exchange. Addresses are from the
documentation-only 192.0.2.0/24 range. Capture and packet comments explicitly
say SYNTHETIC. The generator emits Ethernet/IPv4/UDP pcapng using only stdlib
and the project header codec. No packet capture privileges or network access
are needed, and no packets are sent.

For the Wireshark GUI, run from the repository root:

```powershell
& "C:\Program Files\Wireshark\Wireshark.exe" -X lua_script:captures/murmurtp.lua -r results/mock-analysis/murmurtp-sample.pcapng
```

Filter on `murmurtp` and expand the Murmur Transport Protocol tree. Load the
script once per process; avoid also installing a second copy as a plugin.
For another UDP port, use Analyze > Decode As > MurmurTP, or TShark:

```text
-d udp.port==16882,murmurtp
```

The generated sample is ignored under `results/mock-analysis/`; commit the
generator, Lua script and tests. Real topology captures, five annotated
exchanges (M3.2), and A3.3's routed acceptance remain pending. Synthetic packets
are decoder acceptance fixtures, never network performance evidence.

## Recorded verification

18 November 2026, Windows/Python 3.14 and TShark 4.6.8 with Lua 5.4.6:
16 focused tests passed in 4.27s; full suite **575 passed, 2 existing Linux TCP
skips** in 107.12s with warnings treated as errors. All dissector tests ran.
The sample produced nine decoded packets and a SACK display filter selected
packet 5 with sequence values 2,4,33. Three new Python files pass Python 3.10
syntax checks; Git whitespace checks pass. Linux CI must still run after push.

## CI follow-up

The first GitHub run reported 17 failures: all 16 dissector cases were unable
to read the repository Lua script, plus a client-server swarm cleanup timeout.
The latter happened while joining node shutdown, not during data transfer.

CI now uses the explicit `ubuntu-24.04` runner and prints TShark's version.
The moving `ubuntu-latest` label is undergoing an
[Ubuntu 26.04 migration](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration/).
The supplied log establishes TShark's file-access denial, but contains no
AppArmor audit record proving the underlying policy. Pinning the supported
runner keeps package selection reproducible without broad file-permission
changes, running tests as root, or disabling security profiles.

Peer send/handshake waits and block-request wakeups now use a bounded helper
that preserves external cancellation if the inner operation finishes at the
same instant. This addresses a shutdown hazard in Python 3.10/3.11's legacy
`asyncio.wait_for` ([CPython issue 86296](https://github.com/python/cpython/issues/86296)).
The helper joins its child task on success, timeout, exception and cancellation.
Six deterministic regressions cover these cases at helper, session-send and
download-worker boundaries. No transfer/cleanup deadline was increased; Python
3.11 and all actual Lua acceptance tests remain enabled in CI.

Focused local follow-up: 32 passed (cancellation, keepalive, dissector and
whole-swarm tests) in 21.02s. Windows execution cannot verify the new Linux
runner's file access; both GitHub checks must pass on the updated PR before merge.

The broader local run exposed three assumptions about task ordering in older
tests. Slot replacement now waits for both policy messages to finish before
counting slots; the disconnect fixture waits for the first block to be stored
before simulating departure; bad-handshake fixtures consume the client greeting
before closing so TCP does not discard their reply via reset. Existing state,
hash, byte-count and timeout assertions remain intact. The focused peer suite
then passed all 60 cases in 6.88s. The helper also avoids an unnecessary event
loop yield when its child has already completed.

Final follow-up full Windows/Python 3.14 run: **581 passed, 2 existing Linux TCP
skips** in 115.63s, warnings treated as errors. The ten-leecher client-server
MurmurTP case passed in 7.98s, including its unchanged five-second cleanup limit.
Seven changed/new Python files pass Python 3.10 syntax checks. The updated
Ubuntu 24.04/Python 3.11 run is pending push and is not claimed as verified here.
