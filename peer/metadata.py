"""Deterministic test data and SHA-256 metadata (MASTER_PLAN section 7.2)."""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Union

DEFAULT_SIZE = 64 * 1024 * 1024
PIECE_LENGTH = 256 * 1024
_GENERATOR_CHUNK = 64 * 1024
PathLike = Union[str, Path]


class MetadataError(ValueError):
    """Invalid metadata or generation parameters."""


def _integer(value: int, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise MetadataError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Metadata:
    name: str
    length: int
    piece_length: int
    pieces: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.name, str) or not self.name
                or self.name in (".", "..")
                or any(c in self.name for c in '/\\:*?"<>|')
                or any(ord(c) < 32 for c in self.name)):
            raise MetadataError("name must be a filename, not a path")
        try:
            self.name.encode("utf-8")
        except UnicodeError as exc:
            raise MetadataError("name must be valid UTF-8") from exc
        _integer(self.length, "length")
        _integer(self.piece_length, "piece_length", 1)
        if self.piece_length > 0xFFFFFFFF:
            raise MetadataError("piece_length must fit a u32 offset")
        if self.num_pieces > 0x100000000:
            raise MetadataError("piece indices must fit u32")
        if not isinstance(self.pieces, tuple) or len(self.pieces) != self.num_pieces:
            raise MetadataError("pieces must contain one hash per piece")
        if any(not isinstance(h, str) or re.fullmatch(r"[0-9a-f]{64}", h) is None
               for h in self.pieces):
            raise MetadataError("piece hashes must be lowercase SHA-256 hex strings")

    @property
    def num_pieces(self) -> int:
        return (self.length + self.piece_length - 1) // self.piece_length

    def to_dict(self) -> dict:
        return {"name": self.name, "length": self.length,
                "piece_length": self.piece_length, "pieces": list(self.pieces)}

    def canonical_bytes(self) -> bytes:
        """Sorted keys, compact separators, UTF-8, no BOM or final newline."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    @property
    def info_hash(self) -> bytes:
        """32 binary bytes for the peer handshake; not included in the JSON."""
        return hashlib.sha256(self.canonical_bytes()).digest()

    @property
    def info_hash_hex(self) -> str:
        return self.info_hash.hex()


def build_metadata(path: PathLike, *, piece_length: int = PIECE_LENGTH) -> Metadata:
    """Hash an existing file in piece order with at most 1 MiB read buffers."""
    path = Path(path)
    _integer(piece_length, "piece_length", 1)
    if piece_length > 0xFFFFFFFF:
        raise MetadataError("piece_length must fit a u32 offset")
    hashes = []
    total = 0
    with path.open("rb") as stream:
        while True:
            digest, remaining = hashlib.sha256(), piece_length
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                remaining -= len(chunk)
            if remaining == piece_length:
                break
            hashes.append(digest.hexdigest())
            if remaining:
                break
    return Metadata(path.name, total, piece_length, tuple(hashes))


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_metadata(path: PathLike) -> Metadata:
    """Load strict metadata; harmless JSON whitespace/key order is normalized."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"),
                           object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MetadataError("invalid UTF-8 metadata JSON") from exc
    if (not isinstance(value, dict)
            or set(value) != {"name", "length", "piece_length", "pieces"}
            or not isinstance(value["pieces"], list)):
        raise MetadataError("metadata needs exactly name, length, piece_length, pieces")
    return Metadata(value["name"], value["length"], value["piece_length"],
                    tuple(value["pieces"]))


def generate_dataset(
    data_path: PathLike, metadata_path: PathLike, *, size: int = DEFAULT_SIZE,
    seed: int = 42, piece_length: int = PIECE_LENGTH,
) -> Metadata:
    """Create both files exclusively; remove our partial outputs on failure.

    A private Random(seed) emits randbytes in fixed 64 KiB chunks, with a final
    short chunk. Piece length affects hashing only, not the generated bytes.
    This is reproducible test data, not cryptographic randomness.
    """
    _integer(size, "size")
    _integer(seed, "seed")
    _integer(piece_length, "piece_length", 1)
    if piece_length > 0xFFFFFFFF or (size + piece_length - 1) // piece_length > 2**32:
        raise MetadataError("piece length or piece count exceeds wire field limits")
    data_path, metadata_path = Path(data_path), Path(metadata_path)
    # Validate the filename before creating anything, even for an empty file.
    Metadata(data_path.name, 0, piece_length, ())
    if data_path.resolve() == metadata_path.resolve():
        raise MetadataError("data and metadata paths must be different")
    rng = random.Random(seed)
    created = []
    try:
        with ExitStack() as stack:
            streams = []
            for path in (data_path, metadata_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                streams.append(stack.enter_context(path.open("xb")))
                created.append(path)
            data_stream, metadata_stream = streams
            remaining = size
            while remaining:
                count = min(remaining, _GENERATOR_CHUNK)
                data_stream.write(rng.randbytes(count))
                remaining -= count
            data_stream.close()
            metadata = build_metadata(data_path, piece_length=piece_length)
            metadata_stream.write(metadata.canonical_bytes())
        return metadata
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def parse_size(value: str) -> int:
    """Accept integer bytes, or binary KiB/MiB/GiB suffixes."""
    match = re.fullmatch(r"([0-9]+)(B|KiB|MiB|GiB)?", value, re.IGNORECASE)
    if match is None:
        raise argparse.ArgumentTypeError("use bytes or an integer KiB/MiB/GiB size")
    powers = {"b": 0, "kib": 1, "mib": 2, "gib": 3}
    return int(match[1]) * 1024 ** powers[(match[2] or "b").lower()]


def main(argv: Union[list[str], None] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("testdata/payload.bin"))
    parser.add_argument("--metadata", type=Path, default=Path("testdata/metadata.json"))
    parser.add_argument("--size", type=parse_size, default=DEFAULT_SIZE,
                        help="bytes or KiB/MiB/GiB (default: 64MiB)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--piece-length", type=int, default=PIECE_LENGTH,
                        help="bytes per piece (default: 262144)")
    args = parser.parse_args(argv)
    try:
        metadata = generate_dataset(args.output, args.metadata, size=args.size,
                                    seed=args.seed, piece_length=args.piece_length)
    except (OSError, MetadataError) as exc:
        parser.exit(1, f"metadata: {exc}\n")
    print(f"File: {args.output} ({metadata.length} bytes)")
    print(f"Metadata: {args.metadata}")
    print(f"Pieces: {metadata.num_pieces} ({metadata.piece_length} bytes per full piece)")
    print(f"info_hash: {metadata.info_hash_hex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
