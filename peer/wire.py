"""Swarm Contract 1 codec; session ordering belongs to the future peer loop."""

from dataclasses import dataclass
import struct
from typing import Union

from core.transport import Connection

MAGIC = b"MURMUR01"
BLOCK_SIZE = 16 * 1024
_U32 = struct.Struct("!I")
_REQUEST = struct.Struct("!III")
_POSITION = struct.Struct("!II")


class WireError(ValueError):
    """An invalid message, frame, or truncated stream."""


@dataclass(frozen=True)
class Handshake:
    info_hash: bytes
    peer_id: bytes


@dataclass(frozen=True)
class Bitfield:
    pieces: frozenset[int]


@dataclass(frozen=True)
class Have:
    piece: int


@dataclass(frozen=True)
class Interested:
    pass


@dataclass(frozen=True)
class NotInterested:
    pass


@dataclass(frozen=True)
class Choke:
    pass


@dataclass(frozen=True)
class Unchoke:
    pass


@dataclass(frozen=True)
class Request:
    piece: int
    offset: int
    length: int


@dataclass(frozen=True)
class Piece:
    piece: int
    offset: int
    data: bytes


Message = Union[Handshake, Bitfield, Have, Interested, NotInterested,
                Choke, Unchoke, Request, Piece]
_TYPES = (Handshake, Bitfield, Have, Interested, NotInterested,
          Choke, Unchoke, Request, Piece)


def _integer(value: int, name: str, maximum: int = 0xFFFFFFFF) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise WireError(f"{name} must be an integer in [0, {maximum}]")


def _bytes(value: bytes, name: str) -> None:
    if not isinstance(value, bytes):
        raise WireError(f"{name} must be immutable bytes")


def _piece_index(piece: int, num_pieces: int) -> None:
    _integer(piece, "piece")
    if piece >= num_pieces:
        raise WireError("piece index is outside the torrent")


def _frame_limit(num_pieces: int) -> int:
    # The piece count comes from trusted local metadata, never from this stream.
    _integer(num_pieces, "num_pieces", 0x100000000)
    return max(61, 1 + (num_pieces + 7) // 8, 9 + BLOCK_SIZE)


def _check_length(length: int, num_pieces: int) -> None:
    if not 1 <= length <= _frame_limit(num_pieces):
        raise WireError("frame length is zero or exceeds the torrent frame limit")


def encode_message(message: Message, *, num_pieces: int) -> bytes:
    """Encode exactly one length-prefixed message, validating local fields."""
    _frame_limit(num_pieces)
    try:
        kind = _TYPES.index(type(message))
    except ValueError:
        raise WireError("unsupported message type") from None

    if isinstance(message, Handshake):
        _bytes(message.info_hash, "info_hash")
        _bytes(message.peer_id, "peer_id")
        payload = MAGIC + message.info_hash + message.peer_id
        if len(message.info_hash) != 32 or len(message.peer_id) != 20:
            raise WireError("handshake needs a 32-byte info_hash and 20-byte peer_id")
    elif isinstance(message, Bitfield):
        if not isinstance(message.pieces, frozenset):
            raise WireError("pieces must be a frozenset of piece indices")
        bits = bytearray((num_pieces + 7) // 8)
        for piece in message.pieces:
            _piece_index(piece, num_pieces)
            bits[piece // 8] |= 0x80 >> (piece % 8)
        payload = bytes(bits)
    elif isinstance(message, Have):
        _piece_index(message.piece, num_pieces)
        payload = _U32.pack(message.piece)
    elif isinstance(message, (Request, Piece)):
        _piece_index(message.piece, num_pieces)
        _integer(message.offset, "offset")
        if isinstance(message, Request):
            _integer(message.length, "length", BLOCK_SIZE)
            if message.length == 0:
                raise WireError("request length must be positive")
            payload = _REQUEST.pack(message.piece, message.offset, message.length)
        else:
            _bytes(message.data, "block data")
            if not 1 <= len(message.data) <= BLOCK_SIZE:
                raise WireError("block data must contain 1..16384 bytes")
            payload = _POSITION.pack(message.piece, message.offset) + message.data
    else:
        payload = b""
    return _U32.pack(1 + len(payload)) + bytes([kind]) + payload


def _decode_body(body: bytes, num_pieces: int) -> Message:
    kind, payload = body[0], body[1:]
    if kind == 0:
        if len(payload) != 60 or payload[:8] != MAGIC:
            raise WireError("invalid handshake size or magic")
        return Handshake(payload[8:40], payload[40:60])
    if kind == 1:
        if len(payload) != (num_pieces + 7) // 8:
            raise WireError("bitfield length does not match num_pieces")
        spare = (-num_pieces) % 8
        if spare and payload[-1] & ((1 << spare) - 1):
            raise WireError("bitfield has set padding bits")
        return Bitfield(frozenset(
            byte_index * 8 + bit
            for byte_index, value in enumerate(payload)
            for bit in range(8) if value & (0x80 >> bit)
        ))
    if kind == 2:
        if len(payload) != 4:
            raise WireError("HAVE needs one u32 piece index")
        piece = _U32.unpack(payload)[0]
        _piece_index(piece, num_pieces)
        return Have(piece)
    if 3 <= kind <= 6:
        if payload:
            raise WireError("control message payload must be empty")
        return _TYPES[kind]()
    if kind == 7:
        if len(payload) != 12:
            raise WireError("REQUEST needs three u32 fields")
        piece, offset, length = _REQUEST.unpack(payload)
        _piece_index(piece, num_pieces)
        if not 1 <= length <= BLOCK_SIZE:
            raise WireError("request length must be 1..16384")
        return Request(piece, offset, length)
    if kind == 8:
        if not 9 <= len(payload) <= 8 + BLOCK_SIZE:
            raise WireError("PIECE needs two u32 fields and 1..16384 data bytes")
        piece, offset = _POSITION.unpack(payload[:8])
        _piece_index(piece, num_pieces)
        return Piece(piece, offset, payload[8:])
    raise WireError(f"unknown message type: {kind}")


def decode_message(frame: bytes, *, num_pieces: int) -> Message:
    """Decode exactly one complete frame; reject missing or trailing bytes."""
    _bytes(frame, "frame")
    _frame_limit(num_pieces)
    if len(frame) < 4:
        raise WireError("truncated length prefix")
    length = _U32.unpack(frame[:4])[0]
    _check_length(length, num_pieces)
    if len(frame) != 4 + length:
        raise WireError("frame length does not match bytes supplied")
    return _decode_body(frame[4:], num_pieces)


async def _read_exact(connection: Connection, size: int, *, allow_eof: bool = False) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = await connection.recv(size - len(data))
        if not chunk:
            if allow_eof and not data:
                return b""
            raise WireError("EOF inside a message frame")
        data.extend(chunk)
    return bytes(data)


async def read_message(connection: Connection, *, num_pieces: int) -> Union[Message, None]:
    """Read one frame across arbitrary stream chunks; None means clean EOF.

    Use one reader per connection. After malformed input or cancellation, close
    the connection: a partially consumed frame cannot safely be resumed.
    The caller owns deadlines, session state, and connection cleanup.
    """
    _frame_limit(num_pieces)
    prefix = await _read_exact(connection, 4, allow_eof=True)
    if not prefix:
        return None
    length = _U32.unpack(prefix)[0]
    _check_length(length, num_pieces)  # Reject before reading/allocating the body.
    body = await _read_exact(connection, length)
    return _decode_body(body, num_pieces)
