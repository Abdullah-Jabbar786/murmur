"""Independent wire vectors, malformed datagrams, and bounded property tests."""

from dataclasses import replace

from hypothesis import given, settings, strategies as st
import pytest

from murmurtp.header import (
    ACK, DATA, FIN, SYN, RST, HEADER_SIZE, MSS, Header, PacketError,
    decode_packet, encode_packet, internet_checksum,
)


def reference_checksum(data):
    """Word-by-word oracle independent of the codec's separate byte sums."""
    total = 0
    for offset in range(0, len(data), 2):
        total += int.from_bytes(data[offset:offset + 2].ljust(2, b"\0"), "big")
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def resign(raw):
    raw[23:25] = b"\0\0"
    raw[23:25] = reference_checksum(raw).to_bytes(2, "big")
    return bytes(raw)


def test_exact_wire_vector():
    header = Header(DATA | ACK, 0x1234, 0x01020304, 0x05060708,
                    0x1112, 0x21222324, 0x31323334)
    # Explicit offsets and independent checksum, not an encode/decode-only test.
    expected = bytes.fromhex(
        "0a 1234 01020304 05060708 1112 21222324 31323334 0003 2fad aabbcc")
    assert HEADER_SIZE == 25
    assert (SYN, ACK, FIN, DATA, RST) == (1, 2, 4, 8, 16)
    assert encode_packet(header, bytes.fromhex("aabbcc")) == expected
    assert decode_packet(expected) == (header, bytes.fromhex("aabbcc"))


@pytest.mark.parametrize("raw,expected", [
    (b"", 0xFFFF), (b"\xff\xff", 0), (b"\x01\x02\x03", 0xFBFD),
    (bytes.fromhex("0001f203f4f5f6f7"), 0x220D),
])
def test_checksum_known_values(raw, expected):
    assert internet_checksum(raw) == expected


@pytest.mark.parametrize("size", [0, 1, 2, 3, 1199, 1200])
def test_payload_boundaries_and_odd_header_alignment(size):
    header = Header(DATA | ACK, 65535, 0xFFFFFFFF, 0xFFFFFFFF,
                    65535, 0xFFFFFFFF, 0xFFFFFFFF)
    payload = bytes(i % 256 for i in range(size))
    packet = encode_packet(header, payload)
    zeroed = packet[:23] + b"\0\0" + packet[25:]
    assert int.from_bytes(packet[23:25], "big") == reference_checksum(zeroed)
    assert int.from_bytes(packet[21:23], "big") == size
    assert len(packet) == HEADER_SIZE + size
    assert decode_packet(packet) == (header, payload)


def test_every_single_bit_corruption_is_rejected():
    packet = encode_packet(Header(DATA | ACK, 12, 34, 56, 78, 90, 123), b"abcde")
    for bit in range(len(packet) * 8):
        bad = bytearray(packet)
        bad[bit // 8] ^= 1 << (bit % 8)
        with pytest.raises(PacketError):
            decode_packet(bytes(bad))


def test_truncation_trailing_bytes_oversize_and_unknown_flags():
    packet = encode_packet(Header(DATA), b"abc")
    for size in range(len(packet)):
        with pytest.raises(PacketError):
            decode_packet(packet[:size])
    for invalid in (packet + b"x", b"\0" * (HEADER_SIZE + MSS + 1)):
        with pytest.raises(PacketError):
            decode_packet(invalid)
    # Recompute the checksum so these exercise structural validation itself.
    bad = bytearray(packet)
    bad[21:23] = (MSS + 1).to_bytes(2, "big")
    with pytest.raises(PacketError, match="length"):
        decode_packet(resign(bad))
    bad = bytearray(packet)
    bad[0] = 0x80
    with pytest.raises(PacketError, match="flag"):
        decode_packet(resign(bad))


def test_invalid_encoder_fields_and_payload():
    for name, maximum in (("flags", 255), ("conn_id", 65535), ("window", 65535),
                          ("seq_num", 0xFFFFFFFF), ("cum_ack", 0xFFFFFFFF),
                          ("sack_mask", 0xFFFFFFFF), ("timestamp", 0xFFFFFFFF)):
        for value in (-1, maximum + 1, 1.5, True):
            with pytest.raises(PacketError, match=name):
                encode_packet(replace(Header(ACK), **{name: value}))
    with pytest.raises(PacketError, match="flag"):
        encode_packet(Header(0x20))
    with pytest.raises(PacketError, match="MSS"):
        encode_packet(Header(DATA), b"x" * (MSS + 1))


property_test = settings(max_examples=150, derandomize=True, database=None, deadline=None)
headers = st.builds(Header, flags=st.integers(0, 31),
                    conn_id=st.integers(0, 65535), window=st.integers(0, 65535),
                    **{field: st.integers(0, 0xFFFFFFFF) for field in
                       ("seq_num", "cum_ack", "sack_mask", "timestamp")})


@property_test
@given(headers, st.binary(max_size=MSS))
def test_round_trip_property(header, payload):
    packet = encode_packet(header, payload)
    assert decode_packet(packet) == (header, payload)
    assert int.from_bytes(packet[23:25], "big") == reference_checksum(
        packet[:23] + b"\0\0" + payload)


@property_test
@given(st.binary(max_size=1500))
def test_arbitrary_datagrams_fail_cleanly(raw):
    try:
        header, payload = decode_packet(raw)
    except PacketError:
        return
    assert encode_packet(header, payload) == raw
