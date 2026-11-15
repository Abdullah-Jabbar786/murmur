# MurmurTP header codec (A1.2)

`murmurtp/header.py` implements the frozen v1 wire layout from
`murmurtp-spec.md`: a 25-byte big-endian header and 0–1200 payload bytes.

```python
from murmurtp.header import ACK, DATA, Header, encode_packet, decode_packet

header = Header(flags=DATA | ACK, conn_id=7, seq_num=0,
                cum_ack=0, window=64, sack_mask=0, timestamp=1234)
datagram = encode_packet(header, b"hello")
received_header, payload = decode_packet(datagram)
assert received_header == header
assert payload == b"hello"
assert len(datagram) == 30  # 25-byte header + 5-byte payload
```

`Header` contains the seven caller-controlled fields; `encode_packet` derives
the length and checksum. `decode_packet` returns `(Header, payload)` only after
validation. `PacketError` (a `ValueError`) identifies truncated, oversized,
length-mismatched, checksum-invalid packets or unsupported flag bits. Invalid
outgoing integer fields are rejected instead of being silently truncated.
Inputs for payload/datagram/checksum must be `bytes`.

Checksum computation follows [RFC 1071](https://www.rfc-editor.org/rfc/rfc1071.txt):
zero the checksum field, checksum the entire header plus payload as one byte
sequence, and store the resulting integer in network byte order. Odd total
lengths use conceptual zero padding; padding is never sent. The checksum field
starts at the odd offset 23, so decoding recomputes with that field zeroed and
compares the stored value. Do not use a whole-packet checksum-equals-zero check
or checksum header and payload separately without accounting for alignment.

The codec validates field widths and known flag bits. Whether a flag combination
is legal in a particular connection state is reserved for the connection layer.
No handshake, retransmission, or sequence-number wrap behavior is added here.

Run `python -m pytest tests/test_murmurtp_header.py -v -p no:cacheprovider`.
The 16 test cases cover a fixed independent wire vector, checksum vectors,
empty/odd/even/MSS-sized payloads, every single-bit mutation of a sample packet,
malformed lengths/flags, integer bounds, and deterministic Hypothesis properties.
The properties each exercise 150 examples with the example database disabled.
