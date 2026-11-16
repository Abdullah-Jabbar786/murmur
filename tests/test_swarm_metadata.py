"""Metadata identity, independent hashes, repeatable data, and CLI behavior."""

import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from core.config import load_config
from peer import metadata as module
from peer.metadata import (
    DEFAULT_SIZE, PIECE_LENGTH, Metadata, MetadataError, build_metadata,
    generate_dataset, load_metadata, parse_size,
)
from peer.wire import Handshake, decode_message, encode_message


def test_canonical_bytes_and_info_hash_vector(tmp_path):
    metadata = Metadata("empty.bin", 0, 262144, ())
    expected = b'{"length":0,"name":"empty.bin","piece_length":262144,"pieces":[]}'
    assert metadata.canonical_bytes() == expected
    assert metadata.info_hash == hashlib.sha256(expected).digest()
    assert metadata.info_hash_hex == hashlib.sha256(expected).hexdigest()
    path = tmp_path / "metadata.json"
    # Ordering and presentation whitespace do not change torrent identity.
    path.write_text(json.dumps(metadata.to_dict(), indent=2), encoding="utf-8")
    assert load_metadata(path) == metadata
    assert load_metadata(path).info_hash == metadata.info_hash
    handshake = Handshake(metadata.info_hash, b"p" * 20)
    assert decode_message(encode_message(handshake, num_pieces=0), num_pieces=0) == handshake


def test_unicode_name_is_utf8_and_identity_includes_name():
    metadata = Metadata("caf\u00e9.bin", 0, 1, ())
    assert b"caf\xc3\xa9.bin" in metadata.canonical_bytes()
    assert metadata.info_hash != Metadata("other.bin", 0, 1, ()).info_hash


@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 16, 17])
def test_existing_file_hashes_and_short_final_piece(tmp_path, size):
    payload = bytes(range(size))
    path = tmp_path / "data.bin"
    path.write_bytes(payload)
    metadata = build_metadata(path, piece_length=8)
    assert metadata.length == size
    assert metadata.num_pieces == (size + 7) // 8
    assert metadata.pieces == tuple(
        hashlib.sha256(payload[i:i + 8]).hexdigest() for i in range(0, size, 8))
    assert path.read_bytes() == payload


def test_seeded_generator_known_vector_and_global_rng_isolation(tmp_path):
    state = random.getstate()
    metadata = generate_dataset(tmp_path / "data.bin", tmp_path / "metadata.json", size=16)
    expected = bytes.fromhex("9d79b1a37f31801cd11a6706fb40d6bd")
    assert (tmp_path / "data.bin").read_bytes() == expected
    assert metadata.pieces == (hashlib.sha256(expected).hexdigest(),)
    assert random.getstate() == state
    assert (tmp_path / "metadata.json").read_bytes() == metadata.canonical_bytes()
    assert load_metadata(tmp_path / "metadata.json") == metadata


def test_default_64_mib_acceptance_repeatable_256_hashes(tmp_path):
    first = generate_dataset(tmp_path / "one/data.bin", tmp_path / "one/metadata.json")
    second = generate_dataset(tmp_path / "two/data.bin", tmp_path / "two/metadata.json")
    assert DEFAULT_SIZE == load_config()["file_size"] == 64 * 1024 * 1024
    assert PIECE_LENGTH == load_config()["piece_size"] == 256 * 1024
    assert first == second
    assert first.length == DEFAULT_SIZE and first.num_pieces == 256
    assert (tmp_path / "one/data.bin").stat().st_size == DEFAULT_SIZE
    assert (tmp_path / "one/metadata.json").read_bytes() == (tmp_path / "two/metadata.json").read_bytes()
    with (tmp_path / "one/data.bin").open("rb") as left, (tmp_path / "two/data.bin").open("rb") as right:
        for expected_hash in first.pieces:
            block = left.read(PIECE_LENGTH)
            assert block == right.read(PIECE_LENGTH)
            assert hashlib.sha256(block).hexdigest() == expected_hash
        assert left.read(1) == right.read(1) == b""


def test_piece_length_does_not_change_data_and_seed_does(tmp_path):
    for folder, seed, piece_length in (("one", 42, 8), ("two", 42, 13), ("three", 43, 8)):
        generate_dataset(tmp_path / folder / "data.bin", tmp_path / folder / "meta.json",
                         size=65543, seed=seed, piece_length=piece_length)
    assert (tmp_path / "one/data.bin").read_bytes() == (tmp_path / "two/data.bin").read_bytes()
    assert (tmp_path / "one/data.bin").read_bytes() != (tmp_path / "three/data.bin").read_bytes()
    assert load_metadata(tmp_path / "one/meta.json").info_hash != load_metadata(tmp_path / "two/meta.json").info_hash


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("name", ".."), ("name", "../escape"), ("name", "dir\\file"),
    ("name", "C:file"), ("name", "bad\0name"), ("name", "\ud800"), ("name", 1),
    ("length", -1), ("length", True), ("length", 1.5), ("length", 2**32 + 1),
    ("piece_length", 0), ("piece_length", True), ("piece_length", 2**32),
    ("pieces", []), ("pieces", ("a" * 64,)),
])
def test_invalid_metadata_values(field, value):
    values = dict(name="data.bin", length=0, piece_length=1, pieces=())
    values[field] = value
    with pytest.raises(MetadataError):
        Metadata(**values)


@pytest.mark.parametrize("digest", ["0" * 63, "0" * 65, "G" * 64, "A" * 64, 1])
def test_invalid_piece_hash(digest):
    with pytest.raises(MetadataError, match="SHA-256"):
        Metadata("data.bin", 1, 1, (digest,))


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b"[]", b"null", b"{}",
    b'{"name":"a","name":"b","length":0,"piece_length":1,"pieces":[]}',
    b'{"name":"a","length":0,"piece_length":1,"pieces":[],"info_hash":"x"}',
    b'{"name":"a","length":0,"piece_length":1,"pieces":""}',
    b'{"name":"a","length":NaN,"piece_length":1,"pieces":[]}',
])
def test_malformed_json_metadata_rejected(tmp_path, raw):
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)
    with pytest.raises(MetadataError):
        load_metadata(path)


@pytest.mark.parametrize("existing", ["data.bin", "meta.json"])
def test_never_overwrites_existing_outputs(tmp_path, existing):
    preserved = tmp_path / existing
    preserved.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        generate_dataset(tmp_path / "data.bin", tmp_path / "meta.json", size=10)
    assert preserved.read_bytes() == b"keep me"
    assert set(p.name for p in tmp_path.iterdir()) == {existing}


def test_failure_cleans_up_only_created_outputs(tmp_path, monkeypatch):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("simulated hash read failure")

    monkeypatch.setattr(module, "build_metadata", fail)
    with pytest.raises(OSError, match="simulated"):
        generate_dataset(tmp_path / "data.bin", tmp_path / "meta.json", size=10)
    assert [p.name for p in tmp_path.iterdir()] == ["keep.txt"]


def test_invalid_generation_options_create_nothing(tmp_path):
    for options in ({"size": -1}, {"size": True}, {"seed": -1}, {"seed": 1.5},
                    {"piece_length": 0}, {"piece_length": 2**32},
                    {"size": 2**32 + 1, "piece_length": 1}):
        with pytest.raises(MetadataError):
            generate_dataset(tmp_path / "data.bin", tmp_path / "meta.json", **options)
    with pytest.raises(MetadataError, match="different"):
        generate_dataset(tmp_path / "data.bin", tmp_path / "./data.bin", size=0)
    assert list(tmp_path.iterdir()) == []


def test_empty_generated_file(tmp_path):
    metadata = generate_dataset(tmp_path / "empty.bin", tmp_path / "meta.json", size=0)
    assert metadata.length == metadata.num_pieces == 0
    assert metadata.pieces == ()
    assert (tmp_path / "empty.bin").read_bytes() == b""


@pytest.mark.parametrize("value,expected", [("0", 0), ("123B", 123), ("2KiB", 2048),
                                          ("8mib", 8 * 1024**2), ("1GiB", 1024**3)])
def test_size_units(value, expected):
    assert parse_size(value) == expected


def test_cli_generation_and_existing_output_error(tmp_path):
    command = [sys.executable, "-m", "peer.metadata", "--size", "8KiB", "--seed", "7",
               "--output", str(tmp_path / "data.bin"),
               "--metadata", str(tmp_path / "metadata.json")]
    cwd = Path(__file__).resolve().parents[1]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    metadata = load_metadata(tmp_path / "metadata.json")
    assert metadata.length == 8192
    assert metadata.info_hash_hex in result.stdout
    before = (tmp_path / "data.bin").read_bytes()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "metadata:" in result.stderr
    assert "Traceback" not in result.stderr
    assert (tmp_path / "data.bin").read_bytes() == before
    assert load_metadata(tmp_path / "metadata.json") == metadata
    result = subprocess.run(command + ["--size", "1.5MiB"], cwd=cwd,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
