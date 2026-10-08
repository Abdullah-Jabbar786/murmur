# MurmurTP specification

A reliable, congestion-controlled transport carried inside UDP datagrams.

## Header (about 25 bytes)

| Field | Bytes |
|---|---|
| Flags (SYN, ACK, FIN, DATA) | 1 |
| Connection ID | 2 |
| Sequence number | 4 |
| Cumulative ACK | 4 |
| Receive window | 2 |
| SACK bitmap (32 packets) | 4 |
| Timestamp | 4 |
| Payload length | 2 |
| Checksum | 2 |

MSS: 1200 bytes of payload.

## State machine

(to be drawn)

## Reliability, flow control, congestion control

(to be written)
