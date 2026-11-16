"""Disk-backed piece assembly and verification for a single peer."""

import hashlib
from pathlib import Path
from typing import Union

from peer.metadata import Metadata
from peer.wire import BLOCK_SIZE, Bitfield


class StorageError(ValueError):
    """Invalid storage operation or block boundaries."""


class PieceHashError(StorageError):
    def __init__(self, piece: int) -> None:
        self.piece = piece
        super().__init__(f"SHA-256 mismatch for piece {piece}")


class PieceStore:
    """One file, used serially by its owning peer.

    create: exclusively create and pre-size a new download.
    resume: open an existing file and rehash it; discard partial-block history.
    seed: open read-only and require every piece to verify.

    Calls are synchronous. Do not concurrently access this instance or modify
    its file externally. The peer loop owns scheduling and event emission.
    """

    def __init__(self, path: Union[str, Path], metadata: Metadata, *, mode: str = "create") -> None:
        if mode not in ("create", "resume", "seed"):
            raise StorageError("mode must be create, resume, or seed")
        if not isinstance(metadata, Metadata):
            raise StorageError("metadata must be validated Metadata")
        self.path, self.metadata, self.mode = Path(path), metadata, mode
        self._owned: set[int] = set()
        self._received: dict[int, set[int]] = {}
        self._bytes_verified = 0
        if mode == "create":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open({"create": "x+b", "resume": "r+b", "seed": "rb"}[mode])
        try:
            if mode == "create":
                self._file.truncate(metadata.length)
            else:
                self._file.seek(0, 2)
                if self._file.tell() != metadata.length:
                    raise StorageError("existing file length does not match metadata")
                for piece in range(metadata.num_pieces):
                    if self._verify(piece):
                        self._mark_owned(piece)
                    elif mode == "seed":
                        raise PieceHashError(piece)
        except BaseException:
            self._file.close()
            if mode == "create":
                self.path.unlink(missing_ok=True)
            raise

    @property
    def owned_pieces(self) -> frozenset[int]:
        return frozenset(self._owned)

    @property
    def started_pieces(self) -> frozenset[int]:
        return frozenset(self._received)

    @property
    def bitfield(self) -> Bitfield:
        return Bitfield(self.owned_pieces)

    @property
    def bytes_verified(self) -> int:
        return self._bytes_verified

    @property
    def left(self) -> int:
        return self.metadata.length - self._bytes_verified

    @property
    def complete(self) -> bool:
        return len(self._owned) == self.metadata.num_pieces

    @property
    def closed(self) -> bool:
        return self._file.closed

    def _check_open(self) -> None:
        if self.closed:
            raise StorageError("piece store is closed")

    def piece_size(self, piece: int) -> int:
        if type(piece) is not int or not 0 <= piece < self.metadata.num_pieces:
            raise StorageError("piece index is outside the torrent")
        return min(self.metadata.piece_length,
                   self.metadata.length - piece * self.metadata.piece_length)

    def missing_blocks(self, piece: int) -> tuple[tuple[int, int], ...]:
        """Planned (offset, length) requests; only the last block may be short."""
        self._check_open()
        size = self.piece_size(piece)
        if piece in self._owned:
            return ()
        received = self._received.get(piece, set())
        return tuple((offset, min(BLOCK_SIZE, size - offset))
                     for offset in range(0, size, BLOCK_SIZE) if offset not in received)

    def write_block(self, piece: int, offset: int, data: bytes) -> bool:
        """Return True only when this block newly completes a verified piece.

        Duplicate offsets are ignored (first accepted block wins). On hash
        failure all blocks of that piece become missing and PieceHashError is
        raised. Unverified disk bytes are never served or advertised.
        """
        self._check_open()
        if self.mode == "seed":
            raise StorageError("seed store is read-only")
        size = self.piece_size(piece)
        if (type(offset) is not int or not 0 <= offset < size
                or offset % BLOCK_SIZE != 0):
            raise StorageError("block offset must be a valid 16 KiB boundary")
        if not isinstance(data, bytes) or len(data) != min(BLOCK_SIZE, size - offset):
            raise StorageError("block must contain exactly the planned immutable bytes")
        if piece in self._owned or offset in self._received.get(piece, set()):
            return False
        self._file.seek(piece * self.metadata.piece_length + offset)
        if self._file.write(data) != len(data):
            raise OSError("short block write")
        self._file.flush()
        received = self._received.setdefault(piece, set())
        received.add(offset)
        if len(received) != (size + BLOCK_SIZE - 1) // BLOCK_SIZE:
            return False
        # A failed verification/read must leave a retryable piece, not a full
        # received set whose duplicates would prevent verification on retry.
        del self._received[piece]
        if not self._verify(piece):
            raise PieceHashError(piece)
        self._mark_owned(piece)
        return True

    def read_block(self, piece: int, offset: int, length: int) -> bytes:
        """Serve at most 16 KiB from a verified piece, within its boundaries."""
        self._check_open()
        size = self.piece_size(piece)
        if (type(offset) is not int or offset < 0 or type(length) is not int
                or not 1 <= length <= BLOCK_SIZE or offset + length > size):
            raise StorageError("invalid block read range")
        if piece not in self._owned:
            raise StorageError("cannot serve an unverified piece")
        self._file.seek(piece * self.metadata.piece_length + offset)
        data = self._file.read(length)
        if len(data) != length:
            raise OSError("verified file was truncated")
        return data

    def _verify(self, piece: int) -> bool:
        remaining = self.piece_size(piece)
        self._file.seek(piece * self.metadata.piece_length)
        digest = hashlib.sha256()
        while remaining:
            data = self._file.read(min(remaining, 1024 * 1024))
            if not data:
                raise OSError("file ended inside a piece")
            digest.update(data)
            remaining -= len(data)
        return digest.hexdigest() == self.metadata.pieces[piece]

    def _mark_owned(self, piece: int) -> None:
        self._owned.add(piece)
        self._bytes_verified += self.piece_size(piece)

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "PieceStore":
        self._check_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
