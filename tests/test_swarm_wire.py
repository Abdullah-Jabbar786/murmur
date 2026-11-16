"""Contract vectors, malformed input, stream boundaries, and codec properties."""

import asyncio

from hypothesis import given, settings, strategies as st
import pytest

from core.loopback import LoopbackRegistry, LoopbackTransport
from peer.wire import (
    BLOCK_SIZE, Bitfield, Choke, Handshake, Have, Interested, NotInterested,
    Piece, Request, Unchoke, WireError, decode_message, encode_message, read_message,
)


def frame(kind, payload=b""):
    return (1 + len(payload)).to_bytes(4, "big") + bytes([kind]) + payload


VECTORS = [
    (Handshake(bytes(range(32)), b"p" * 20),
     b"\0\0\0\x3d\0MURMUR01" + bytes(range(32)) + b"p" * 20),
    (Bitfield(frozenset({0, 7, 8, 9})), bytes.fromhex("00000003 01 81c0")),
    (Have(9), bytes.fromhex("00000005 02 00000009")),
    (Interested(), bytes.fromhex("00000001 03")),
    (NotInterested(), bytes.fromhex("00000001 04")),
    (Choke(), bytes.fromhex("00000001 05")),
    (Unchoke(), bytes.fromhex("00000001 06")),
    (Request(9, 0x00014000, 16384),
     bytes.fromhex("0000000d 07 00000009 00014000 00004000")),
    (Piece(9, 0x00014000, b"abc"),
     bytes.fromhex("0000000c 08 00000009 00014000 616263")),
]


@pytest.mark.parametrize("message,expected", VECTORS)
def test_independent_contract_vectors(message, expected):
    assert encode_message(message, num_pieces=10) == expected
    assert decode_message(expected, num_pieces=10) == message


def test_every_truncation_and_trailing_bytes_rejected():
    for _, raw in VECTORS:
        for end in range(len(raw)):
            with pytest.raises(WireError):
                decode_message(raw[:end], num_pieces=10)
        with pytest.raises(WireError):
            decode_message(raw + b"x", num_pieces=10)


@pytest.mark.parametrize("raw", [
    b"\0\0\0\0", b"\xff\xff\xff\xff", frame(9), frame(255),
    frame(0, b"BADMAGIC" + bytes(52)), frame(0, b"MURMUR01" + bytes(51)),
    frame(0, b"MURMUR01" + bytes(53)),
    frame(1), frame(1, b"\0"), frame(1, bytes(3)), frame(1, b"\0\x01"),
    frame(2, bytes(3)), frame(2, bytes(5)), frame(2, (10).to_bytes(4, "big")),
    *(frame(kind, b"x") for kind in range(3, 7)),
    frame(7, bytes(11)), frame(7, bytes(13)), frame(7, bytes(12)),
    frame(7, bytes(8) + (BLOCK_SIZE + 1).to_bytes(4, "big")),
    frame(7, (10).to_bytes(4, "big") + bytes(4) + (1).to_bytes(4, "big")),
    frame(8, bytes(7)), frame(8, bytes(8)), frame(8, bytes(9 + BLOCK_SIZE)),
    frame(8, (10).to_bytes(4, "big") + bytes(4) + b"x"),
])
def test_malformed_frames_rejected(raw):
    with pytest.raises(WireError):
        decode_message(raw, num_pieces=10)


def test_invalid_local_values_rejected():
    invalid = [object(), Handshake(bytes(31), bytes(21)),
               Handshake(bytearray(32), bytes(20)), Bitfield({0}),
               Bitfield(frozenset({10})), Bitfield(frozenset({True})),
               Have(-1), Have(True), Have(1.5), Have(10),
               Request(0, -1, 1), Request(0, 2**32, 1), Request(0, True, 1),
               Request(0, 0, 0), Request(0, 0, True), Request(0, 0, 16385),
               Piece(0, 0, b""), Piece(0, 0, bytes(16385)),
               Piece(0, 0, bytearray(b"x")), Piece(0, 0, "x")]
    for message in invalid:
        with pytest.raises(WireError):
            encode_message(message, num_pieces=10)
    for count in (-1, True, 1.5, 2**32 + 1):
        with pytest.raises(WireError, match="num_pieces"):
            encode_message(Interested(), num_pieces=count)
        with pytest.raises(WireError, match="num_pieces"):
            decode_message(frame(3), num_pieces=count)
    with pytest.raises(WireError, match="bytes"):
        decode_message(bytearray(frame(3)), num_pieces=10)


@pytest.mark.parametrize("size", [1, BLOCK_SIZE])
def test_block_length_boundaries_and_u32_fields(size):
    for message in (Request(0xFFFFFFFF, 0xFFFFFFFF, size),
                    Piece(0xFFFFFFFF, 0xFFFFFFFF, b"x" * size)):
        raw = encode_message(message, num_pieces=2**32)
        assert decode_message(raw, num_pieces=2**32) == message


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 256])
def test_full_and_empty_bitfields(count):
    for pieces in (frozenset(), frozenset(range(count))):
        message = Bitfield(pieces)
        raw = encode_message(message, num_pieces=count)
        assert len(raw) == 5 + (count + 7) // 8
        assert decode_message(raw, num_pieces=count) == message
    if count == 0:
        with pytest.raises(WireError):
            decode_message(frame(2, bytes(4)), num_pieces=0)


class FragmentedConnection:
    """Contract-compatible recv stub that records requested sizes."""

    def __init__(self, raw, chunk_size):
        self.raw = raw
        self.chunk_size = chunk_size
        self.calls = []

    async def recv(self, max_bytes):
        self.calls.append(max_bytes)
        size = min(max_bytes, self.chunk_size)
        result, self.raw = self.raw[:size], self.raw[size:]
        return result


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 7, 65536])
async def test_fragmented_and_concatenated_frames(chunk_size):
    conn = FragmentedConnection(b"".join(raw for _, raw in VECTORS), chunk_size)
    for message, _ in VECTORS:
        assert await read_message(conn, num_pieces=10) == message
    assert await read_message(conn, num_pieces=10) is None


async def test_mid_frame_eof_is_not_clean_eof():
    for _, raw in VECTORS:
        for end in range(1, len(raw)):
            with pytest.raises(WireError, match="EOF"):
                await read_message(FragmentedConnection(raw[:end], 2), num_pieces=10)


async def test_oversized_length_rejected_before_reading_body():
    for size in (0, 9 + BLOCK_SIZE + 1, 0xFFFFFFFF):
        conn = FragmentedConnection(size.to_bytes(4, "big") + b"body", 65536)
        with pytest.raises(WireError, match="length"):
            await read_message(conn, num_pieces=10)
        assert conn.calls == [4]
        assert conn.raw == b"body"


async def test_loopback_transport_exchange():
    registry = LoopbackRegistry()
    server, client = LoopbackTransport(registry), LoopbackTransport(registry)
    await server.listen(("127.0.0.1", 6881))
    sender = await client.connect(("127.0.0.1", 6881))
    receiver = await server.accept()
    try:
        raw = b"".join(encoded for _, encoded in VECTORS)
        for offset in range(0, len(raw), 3):
            await sender.send(raw[offset:offset + 3])
        await sender.close()
        for message, _ in VECTORS:
            assert await asyncio.wait_for(read_message(receiver, num_pieces=10), 1) == message
        assert await asyncio.wait_for(read_message(receiver, num_pieces=10), 1) is None
    finally:
        await sender.close()
        await receiver.close()
        await client.close()
        await server.close()


property_test = settings(max_examples=150, derandomize=True, database=None, deadline=None)


@property_test
@given(st.one_of(
    st.builds(Handshake, st.binary(min_size=32, max_size=32),
              st.binary(min_size=20, max_size=20)),
    st.builds(Bitfield, st.frozensets(st.integers(0, 255))),
    st.builds(Have, st.integers(0, 255)),
    st.sampled_from([Interested(), NotInterested(), Choke(), Unchoke()]),
    st.builds(Request, st.integers(0, 255), st.integers(0, 2**32 - 1),
              st.integers(1, BLOCK_SIZE)),
    st.builds(Piece, st.integers(0, 255), st.integers(0, 2**32 - 1),
              st.binary(min_size=1, max_size=BLOCK_SIZE)),
))
def test_round_trip_property(message):
    raw = encode_message(message, num_pieces=256)
    assert decode_message(raw, num_pieces=256) == message


@property_test
@given(st.integers(0, 255), st.binary(max_size=100))
def test_arbitrary_payloads_fail_cleanly(kind, payload):
    raw = frame(kind, payload)
    try:
        message = decode_message(raw, num_pieces=256)
    except WireError:
        return
    assert encode_message(message, num_pieces=256) == raw
