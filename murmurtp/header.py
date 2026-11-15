"""MurmurTP v1 datagram codec; see docs/murmurtp-spec.md, section 2.

Length and checksum are derived from the payload. Connection state, sequence
arithmetic, and legal flag combinations belong to the future connection layer.
"""

from dataclasses import dataclass
import struct
from typing import Tuple

SYN, ACK, FIN, DATA, RST = 0x01, 0x02, 0x04, 0x08, 0x10
KNOWN_FLAGS = SYN | ACK | FIN | DATA | RST
MSS = 1200
_HEADER = struct.Struct("!B H I I H I I H H")
HEADER_SIZE = _HEADER.size  # 25 bytes, no alignment padding.
_CHECKSUM_OFFSET = 23


class PacketError(ValueError):
    """An invalid field, malformed datagram, or checksum mismatch."""


@dataclass(frozen=True)
class Header:
    """Caller-controlled fields; encode_packet computes length and checksum."""

    flags: int
    conn_id: int = 0
    seq_num: int = 0
    cum_ack: int = 0
    window: int = 0
    sack_mask: int = 0
    timestamp: int = 0


def internet_checksum(data: bytes) -> int:
    """RFC 1071 one's-complement checksum, with an odd final byte padded low.

    Padding is conceptual: no extra byte is placed on the wire.
    """
    if not isinstance(data, bytes):
        raise TypeError("checksum input must be bytes")
    total = (sum(data[0::2]) << 8) + sum(data[1::2])
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _validate(header: Header) -> None:
    for name, bits in (("flags", 8), ("conn_id", 16), ("seq_num", 32),
                       ("cum_ack", 32), ("window", 16), ("sack_mask", 32),
                       ("timestamp", 32)):
        value = getattr(header, name)
        if (not isinstance(value, int) or isinstance(value, bool)
                or not 0 <= value < (1 << bits)):
            raise PacketError(f"{name} must be an unsigned {bits}-bit integer")
    if header.flags & ~KNOWN_FLAGS:
        raise PacketError("Unknown flag bits")


def encode_packet(header: Header, payload: bytes = b"") -> bytes:
    """Encode exactly one UDP payload, including its 25-byte MurmurTP header."""
    if not isinstance(header, Header):
        raise TypeError("header must be a Header")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    _validate(header)
    if len(payload) > MSS:
        raise PacketError(f"Payload exceeds MSS ({MSS} bytes)")
    raw = _HEADER.pack(header.flags, header.conn_id, header.seq_num,
                       header.cum_ack, header.window, header.sack_mask,
                       header.timestamp, len(payload), 0) + payload
    checksum = internet_checksum(raw)
    return raw[:_CHECKSUM_OFFSET] + struct.pack("!H", checksum) + raw[HEADER_SIZE:]


def decode_packet(datagram: bytes) -> Tuple[Header, bytes]:
    """Validate one whole datagram; malformed network bytes raise PacketError."""
    if not isinstance(datagram, bytes):
        raise TypeError("datagram must be bytes")
    if not HEADER_SIZE <= len(datagram) <= HEADER_SIZE + MSS:
        raise PacketError("Invalid datagram size")
    fields = _HEADER.unpack_from(datagram)
    length, checksum = fields[-2:]
    if length > MSS or len(datagram) != HEADER_SIZE + length:
        raise PacketError("Payload length does not match datagram")
    # The checksum sits at an ODD offset. Follow the frozen spec: checksum
    # the zeroed field and compare its numeric, big-endian stored value.
    # Checking checksum(datagram) == 0 would assume an even-aligned field.
    zeroed = datagram[:_CHECKSUM_OFFSET] + b"\0\0" + datagram[HEADER_SIZE:]
    if internet_checksum(zeroed) != checksum:
        raise PacketError("Checksum mismatch")
    header = Header(*fields[:-2])
    _validate(header)
    return header, datagram[HEADER_SIZE:]
