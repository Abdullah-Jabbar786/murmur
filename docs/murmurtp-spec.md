# MurmurTP Specification (v1.0)

**Protocol:** Murmur Transport Protocol (MurmurTP)  
**Layer:** Transport layer carried inside UDP datagrams (port `6882/UDP`)  
**Design Lead:** Abdullah (24K-0859)  
**Status:** Frozen specification for Sprint 0 / Gate S0  

---

## 1. Overview

MurmurTP is a custom connection-oriented, reliable, congestion-controlled transport protocol operating over UDP. It provides an in-order reliable byte stream to the application layer (implementing `core/transport.py:Transport`), using:
- **Packet-numbered sequence numbers** (Decision D11).
- **Cumulative ACK + 32-packet SACK bitmap** (Selective Repeat).
- **Packet-counted flow control window** (Decision D10).
- **Jacobson/Karels RTO estimator** with **Karn's algorithm** and exponential backoff.
- **Reno-style AIMD Congestion Control** with Fast Retransmit.

---

## 2. Packet Format & Header Layout

Every MurmurTP datagram consists of a **fixed 25-byte header** followed by variable payload (0 to 1200 bytes).

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Flags     |         Connection ID         |               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+               +
|                        Sequence Number                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Cumulative ACK                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|        Receive Window         |          SACK Bitmap          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          SACK Bitmap (cont)   |           Timestamp           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|       Timestamp (cont)        |         Payload Length        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|           Checksum            |            Payload ...        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+                               +
|                           ...                                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 2.1 Field Descriptions (Big-Endian Network Byte Order)

| Offset | Field | Size | Description |
|---|---|---|---|
| 0 | `flags` | 1 byte | Control bitmask: `SYN=0x01`, `ACK=0x02`, `FIN=0x04`, `DATA=0x08`, `RST=0x10`. |
| 1 | `conn_id` | 2 bytes | Connection identifier (0 during initial SYN; assigned by listener in SYN-ACK). |
| 3 | `seq_num` | 4 bytes | 32-bit packet sequence number (starts at 0 for DATA). |
| 7 | `cum_ack` | 4 bytes | Next expected in-order packet sequence number. |
| 11 | `window` | 2 bytes | Advertised receive window in **packets** (0 to 65,535 packets). |
| 13 | `sack_mask` | 4 bytes | 32-bit SACK bitmap. Bit $i$ set means packet `cum_ack + 1 + i` was received. |
| 17 | `timestamp` | 4 bytes | Sender millisecond clock (low 32 bits). Used for pcap debugging/tracing. |
| 21 | `length` | 2 bytes | Length of the payload in bytes (0 to 1200). |
| 23 | `checksum` | 2 bytes | 16-bit Internet Checksum (RFC 1071) over header (with checksum=0) + payload. |
| 25 | `payload` | 0–1200 B | Application byte data (Maximum Segment Size = 1200 bytes). |

Total IP packet size with 1200 B payload: $25\text{ B (MurmurTP)} + 8\text{ B (UDP)} + 20\text{ B (IP)} = 1253\text{ B}$ (no Ethernet MTU fragmentation).

---

## 3. Connection Lifecycle & State Machine

```
       CLIENT                                          SERVER
         |                                               |
  (CLOSED)                                        (LISTEN)
         |                                               |
         | ----- SYN (seq=0, conn_id=0) ---------------> |  Generate Conn ID
         | <---- SYN-ACK (seq=0, ack=1, conn_id=X) ----- |
 (ESTAB) |                                               | (ESTAB)
         | ----- ACK (seq=1, ack=1, conn_id=X) --------> |
         |                                               |
         | <==== Bidirectional DATA + ACK / SACK ======> |
         |                                               |
         | ----- FIN (seq=N, conn_id=X) ---------------> |
         | <---- ACK (ack=N+1, conn_id=X) -------------- |
         | <---- FIN (seq=M, conn_id=X) ---------------- |
         | ----- ACK (ack=M+1, conn_id=X) -------------> |
  (TIME_WAIT: 2 * RTO)                                (CLOSED)
  (CLOSED)
```

- **Handshake:** Client retries SYN up to 5 times with exponential backoff before reporting `ConnectionRefusedError`.
- **Teardown:** Each direction closes gracefully with FIN/ACK. Sender enters `TIME_WAIT` for $2 \times \text{RTO}$ to absorb in-flight duplicates.
- **Abort:** An unexpected packet or unrecoverable error sends `RST` to terminate immediately.
- **Inactivity Timeout:** A connection with no progress for 30 seconds is dropped.

---

## 4. Reliability & Selective Repeat

### 4.1 Sequence Numbers & ACKs
- DATA packets are numbered sequentially: $0, 1, 2, 3, \dots$
- `cum_ack = K` confirms receipt of all packets up to and including $K-1$. Packet $K$ is the oldest missing packet.
- SACK bit $i \in [0, 31]$ set indicates packet $K + 1 + i$ has been received out-of-order and is buffered in the receiver's reorder queue.

### 4.2 Loss Detection & Fast Retransmit
A packet $P$ is flagged for retransmission when either:
1. **SACK Gap Rule (Fast Retransmit):** At least 3 packets with sequence numbers greater than $P$ have been acknowledged via SACK.
2. **RTO Timer Expiry:** The retransmission timer for the unacknowledged in-flight packet fires.

Only missing packets are retransmitted (Selective Repeat).

---

## 5. Flow & Congestion Control

### 5.1 Flow Control (Receive Window)
- The receiver advertises its free buffer capacity in `window` (counted in packets).
- If the receive buffer is full, `window = 0` is advertised. The sender pauses transmission and sends periodic zero-window probes every 1 second.
- Effective send window:
  $$\text{effective\_window} = \min(\text{cwnd}, \text{rwnd})$$

### 5.2 RTT Estimation & RTO Calculation (Jacobson/Karels)
The sender maintains a local map: `send_time[seq] = timestamp_seconds`.
- **Karn's Rule:** RTT samples are only measured for packets that were **not** retransmitted.
- Updates on sample $R$:
  $$\text{RTTVAR} = 0.75 \times \text{RTTVAR} + 0.25 \times |\text{SRTT} - R|$$
  $$\text{SRTT} = 0.875 \times \text{SRTT} + 0.125 \times R$$
  $$\text{RTO} = \text{SRTT} + \max(10\text{ ms}, 4 \times \text{RTTVAR})$$
- Initial $\text{RTO} = 1.0\text{ s}$, clamped to $[200\text{ ms}, 5.0\text{ s}]$.
- On timeout: $\text{RTO} = \min(2 \times \text{RTO}, 5.0\text{ s})$.

### 5.3 Reno Congestion Control (AIMD)
Internally tracked in packets, reported in bytes ($\text{cwnd\_bytes} = \text{cwnd} \times 1200$).
- Initial: $\text{cwnd} = 10\text{ packets}$, $\text{ssthresh} = \infty$.
- **Slow Start:** $\text{cwnd} \leftarrow \text{cwnd} + 1$ per newly ACKed packet until $\text{cwnd} \ge \text{ssthresh}$.
- **Congestion Avoidance:** $\text{cwnd} \leftarrow \text{cwnd} + \frac{1}{\text{cwnd}}$ per ACK ($\approx +1$ packet per RTT).
- **Fast Recovery (SACK loss):**
  $$\text{ssthresh} \leftarrow \max(2, \lfloor \text{cwnd} / 2 \rfloor)$$
  $$\text{cwnd} \leftarrow \text{ssthresh}$$
  Immediately retransmit the missing packet.
- **Timeout Loss:**
  $$\text{ssthresh} \leftarrow \max(2, \lfloor \text{cwnd} / 2 \rfloor)$$
  $$\text{cwnd} \leftarrow 1$$

---

## 6. Contract 2 Integration

MurmurTP exposes `MurmurTransport` satisfying `core/transport.py`:
- `stats()` returns:
  ```python
  {
      "proto": "murmurtp",
      "cwnd_bytes": int(cwnd * 1200),
      "rtt_ms": round(srtt * 1000, 2),
      "retx": total_retransmissions,
      "bytes_in_flight": in_flight_bytes,
  }
  ```
Swapping between kernel TCP and MurmurTP is controlled exclusively by `"transport": "tcp" | "murmurtp"` in `config/default.json`.
