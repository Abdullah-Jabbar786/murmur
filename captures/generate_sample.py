"""Write a SYNTHETIC Ethernet/IPv4/UDP pcapng for testing the Lua dissector."""

import argparse
from pathlib import Path
import struct

from murmurtp.header import ACK, DATA, FIN, RST, SYN, Header, encode_packet, internet_checksum


def sample_packets():
    """Illustrative headers, including a SACK gap; not a measured conversation."""
    return [encode_packet(header, payload) for header, payload in (
        (Header(SYN), b""),
        (Header(SYN | ACK, 7, cum_ack=1, window=64), b""),
        (Header(ACK, 7, seq_num=1, cum_ack=1, window=64), b""),
        (Header(DATA, 7, timestamp=1234), b"synthetic MurmurTP payload"),
        (Header(ACK, 7, cum_ack=1, window=63, sack_mask=0x80000005), b""),
        (Header(DATA, 7, seq_num=2), b""),  # Empty DATA is a window probe.
        (Header(FIN, 7, seq_num=2), b""),
        (Header(ACK, 7, cum_ack=3, window=64), b""),
        (Header(RST, 7), b""),
    )]


def ethernet_frame(payload, port=6882):
    """IPv4 UDP checksum zero means omitted; IP and MurmurTP checksums are set."""
    udp = struct.pack("!HHHH", 40000, port, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                     bytes([192, 0, 2, 1]), bytes([192, 0, 2, 2]))
    ip = ip[:10] + struct.pack("!H", internet_checksum(ip)) + ip[12:]
    return bytes.fromhex("0200000000020200000000010800") + ip + udp


def _padded(data):
    return data + b"\0" * (-len(data) % 4)


def _comment(text):
    raw = text.encode("utf-8")
    return struct.pack("<HH", 1, len(raw)) + _padded(raw) + b"\0" * 4


def _block(kind, body):
    body = _padded(body)
    size = 12 + len(body)
    return struct.pack("<II", kind, size) + body + struct.pack("<I", size)


def write_capture(path, packets, *, port=6882, truncate=0):
    """Write deterministic fixtures; truncate removes captured tail bytes only."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        stream.write(_block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
                            + _comment("SYNTHETIC MurmurTP decoder fixture; NOT network evidence")))
        stream.write(_block(1, struct.pack("<HHI", 1, 0, 65535)))
        for index, payload in enumerate(packets):
            frame = ethernet_frame(payload, port)
            captured = frame[:-truncate] if truncate else frame
            body = struct.pack("<IIIII", 0, 0, index * 1000, len(captured), len(frame))
            stream.write(_block(6, body + _padded(captured) + _comment("SYNTHETIC packet")))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = write_capture(args.output, sample_packets())
    print(f"Wrote 9 SYNTHETIC packets to {path}; not experiment evidence")


if __name__ == "__main__":
    main()
