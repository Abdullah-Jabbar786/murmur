"""Piece integrity, retry behavior, disk lifecycle, and wire integration."""

import hashlib
import random

import pytest

from peer.metadata import Metadata, PIECE_LENGTH
from peer.storage import PieceHashError, PieceStore, StorageError
from peer.wire import BLOCK_SIZE, decode_message, encode_message


def metadata_for(data, piece_length=PIECE_LENGTH):
    return Metadata("payload.bin", len(data), piece_length, tuple(
        hashlib.sha256(data[i:i + piece_length]).hexdigest()
        for i in range(0, len(data), piece_length)))


def payload(size):
    return (bytes(range(256)) * ((size + 255) // 256))[:size]


def fill_piece(store, data, piece):
    results = []
    for offset, size in store.missing_blocks(piece):
        start = piece * store.metadata.piece_length + offset
        results.append(store.write_block(piece, offset, data[start:start + size]))
    return results


def test_preallocation_and_default_sixteen_block_assembly(tmp_path):
    data = payload(PIECE_LENGTH)
    path = tmp_path / "download.bin"
    with PieceStore(path, metadata_for(data)) as store:
        assert path.stat().st_size == len(data)
        assert not store.complete and store.left == len(data)
        assert store.owned_pieces == store.started_pieces == frozenset()
        assert store.missing_blocks(0) == tuple((i * BLOCK_SIZE, BLOCK_SIZE) for i in range(16))
        assert fill_piece(store, data, 0) == [False] * 15 + [True]
        assert store.complete and store.left == 0 and store.bytes_verified == len(data)
        assert store.owned_pieces == frozenset({0}) and store.started_pieces == frozenset()
        assert store.missing_blocks(0) == ()
        assert store.read_block(0, 0, BLOCK_SIZE) == data[:BLOCK_SIZE]
        assert decode_message(encode_message(store.bitfield, num_pieces=1), num_pieces=1) == store.bitfield
    assert path.read_bytes() == data


@pytest.mark.parametrize("seed", [0, 1, 7, 42])
def test_interleaved_out_of_order_blocks_and_duplicates(tmp_path, seed):
    data = payload(2 * PIECE_LENGTH + BLOCK_SIZE + 7)
    with PieceStore(tmp_path / "download.bin", metadata_for(data)) as store:
        blocks = [(piece, offset, length) for piece in range(3)
                  for offset, length in store.missing_blocks(piece)]
        random.Random(seed).shuffle(blocks)
        completed = []
        for piece, offset, length in blocks:
            start = piece * PIECE_LENGTH + offset
            block = data[start:start + length]
            if store.write_block(piece, offset, block):
                completed.append(piece)
            assert not store.write_block(piece, offset, block)
        assert sorted(completed) == [0, 1, 2]
        assert store.complete and store.left == 0
        assert store.bytes_verified == len(data)
    assert (tmp_path / "download.bin").read_bytes() == data


def test_corrupt_block_discards_piece_and_can_be_requested_again(tmp_path):
    data = payload(PIECE_LENGTH * 2)
    with PieceStore(tmp_path / "download.bin", metadata_for(data)) as store:
        fill_piece(store, data, 1)
        corrupt = bytearray(data[:BLOCK_SIZE])
        corrupt[123] ^= 0x80
        assert not store.write_block(0, 0, bytes(corrupt))
        assert store.started_pieces == frozenset({0})
        with pytest.raises(StorageError, match="unverified"):
            store.read_block(0, 0, BLOCK_SIZE)
        with pytest.raises(PieceHashError) as error:
            fill_piece(store, data, 0)
        assert error.value.piece == 0
        assert store.owned_pieces == frozenset({1})
        assert store.started_pieces == frozenset()
        assert len(store.missing_blocks(0)) == 16
        assert store.left == PIECE_LENGTH
        fill_piece(store, data, 0)
        assert store.complete
        assert store.read_block(0, 0, BLOCK_SIZE) == data[:BLOCK_SIZE]


def test_duplicate_different_bytes_never_replace_accepted_blocks(tmp_path):
    data = payload(PIECE_LENGTH)
    with PieceStore(tmp_path / "download.bin", metadata_for(data)) as store:
        store.write_block(0, 0, data[:BLOCK_SIZE])
        snapshot = store.started_pieces
        assert not store.write_block(0, 0, b"x" * BLOCK_SIZE)
        assert len(store.missing_blocks(0)) == 15
        fill_piece(store, data, 0)
        assert snapshot == frozenset({0})  # Snapshot does not change with store state.
        assert not store.write_block(0, 0, b"x" * BLOCK_SIZE)
        assert store.read_block(0, 0, BLOCK_SIZE) == data[:BLOCK_SIZE]
        assert store.bytes_verified == len(data)


@pytest.mark.parametrize("size,piece_length", [(0, 262144), (1, 262144), (16385, 262144),
                                              (262145, 262144), (30000, 17000), (17, 8)])
def test_empty_and_short_piece_boundaries(tmp_path, size, piece_length):
    data = payload(size)
    with PieceStore(tmp_path / "download.bin", metadata_for(data, piece_length)) as store:
        for piece in range(store.metadata.num_pieces):
            fill_piece(store, data, piece)
            assert store.read_block(piece, store.piece_size(piece) - 1, 1) == data[
                (piece * piece_length + store.piece_size(piece) - 1):
                (piece * piece_length + store.piece_size(piece))]
        assert store.complete and store.left == 0
    assert (tmp_path / "download.bin").read_bytes() == data


def test_invalid_writes_leave_disk_and_state_unchanged(tmp_path):
    data = payload(BLOCK_SIZE + 7)
    path = tmp_path / "download.bin"
    with PieceStore(path, metadata_for(data)) as store:
        before = path.read_bytes()
        for piece, offset, block in [
            (-1, 0, bytes(BLOCK_SIZE)), (1, 0, bytes(BLOCK_SIZE)),
            (True, 0, bytes(BLOCK_SIZE)), (0, -1, bytes(BLOCK_SIZE)),
            (0, True, bytes(BLOCK_SIZE)), (0, 1, bytes(BLOCK_SIZE)),
            (0, 0, b""), (0, 0, bytes(BLOCK_SIZE - 1)), (0, 0, bytes(BLOCK_SIZE + 1)),
            (0, 0, bytearray(BLOCK_SIZE)), (0, BLOCK_SIZE, bytes(8)),
            (0, 2 * BLOCK_SIZE, b"x"),
        ]:
            with pytest.raises(StorageError):
                store.write_block(piece, offset, block)
        assert store.owned_pieces == store.started_pieces == frozenset()
        assert path.read_bytes() == before


def test_seed_reads_require_verified_content_and_valid_ranges(tmp_path):
    data = payload(PIECE_LENGTH)
    path = tmp_path / "seed.bin"
    path.write_bytes(data)
    with PieceStore(path, metadata_for(data), mode="seed") as store:
        assert store.complete and store.left == 0
        assert store.read_block(0, 3, 11) == data[3:14]
        for piece, offset, length in [(1, 0, 1), (True, 0, 1), (0, -1, 1),
                                     (0, True, 1), (0, 0, 0), (0, 0, True),
                                     (0, 0, BLOCK_SIZE + 1), (0, PIECE_LENGTH, 1)]:
            with pytest.raises(StorageError):
                store.read_block(piece, offset, length)
        with pytest.raises(StorageError, match="read-only"):
            store.write_block(0, 0, bytes(BLOCK_SIZE))
    assert path.read_bytes() == data


def test_existing_files_preserved_and_bad_seed_rejected(tmp_path):
    path = tmp_path / "existing.bin"
    path.write_bytes(b"original")
    meta = metadata_for(b"different")
    with pytest.raises(FileExistsError):
        PieceStore(path, meta)
    for mode in ("seed", "resume"):
        with pytest.raises(StorageError, match="length"):
            PieceStore(path, meta, mode=mode)
    assert path.read_bytes() == b"original"
    with pytest.raises(PieceHashError):
        PieceStore(path, metadata_for(b"badbytes"), mode="seed")
    assert path.read_bytes() == b"original"
    # Failed constructors must close their handles, including on Windows.
    path.rename(tmp_path / "renamed.bin")


def test_resume_rehashes_and_discards_partial_history(tmp_path):
    data = payload(2 * PIECE_LENGTH)
    path = tmp_path / "download.bin"
    meta = metadata_for(data)
    with PieceStore(path, meta) as store:
        fill_piece(store, data, 0)
        store.write_block(1, 0, data[PIECE_LENGTH:PIECE_LENGTH + BLOCK_SIZE])
    with PieceStore(path, meta, mode="resume") as store:
        assert store.owned_pieces == frozenset({0})
        assert store.started_pieces == frozenset()
        assert store.left == PIECE_LENGTH and len(store.missing_blocks(1)) == 16
        fill_piece(store, data, 1)
    with PieceStore(path, meta, mode="seed") as store:
        assert store.complete


def test_verification_read_error_leaves_retryable_piece(tmp_path, monkeypatch):
    data = payload(BLOCK_SIZE)
    with PieceStore(tmp_path / "download.bin", metadata_for(data)) as store:
        verify = store._verify

        def fail(piece):
            raise OSError("simulated disk read failure")

        monkeypatch.setattr(store, "_verify", fail)
        with pytest.raises(OSError, match="simulated"):
            store.write_block(0, 0, data)
        assert store.missing_blocks(0) == ((0, BLOCK_SIZE),)
        assert not store.complete
        monkeypatch.setattr(store, "_verify", verify)
        assert store.write_block(0, 0, data)


def test_close_is_idempotent_and_operations_fail_cleanly(tmp_path):
    store = PieceStore(tmp_path / "download.bin", metadata_for(b"a"))
    store.close()
    store.close()
    assert store.closed
    for operation in (lambda: store.write_block(0, 0, b"a"),
                      lambda: store.read_block(0, 0, 1),
                      lambda: store.missing_blocks(0), lambda: store.__enter__()):
        with pytest.raises(StorageError, match="closed"):
            operation()


def test_invalid_mode_or_metadata_creates_nothing(tmp_path):
    path = tmp_path / "download.bin"
    with pytest.raises(StorageError, match="mode"):
        PieceStore(path, metadata_for(b"a"), mode="overwrite")
    with pytest.raises(StorageError, match="Metadata"):
        PieceStore(path, {})
    assert not path.exists()
