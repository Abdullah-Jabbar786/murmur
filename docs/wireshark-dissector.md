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
