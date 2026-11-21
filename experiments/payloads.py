"""Retention of verified payloads after a sweep trial has been checkpointed."""
import hashlib


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def keep_seed(directory, peers, result):
    size, expected_hash = result["file_bytes"], result["sha256"]
    if "seed" not in peers or result["verified_leechers"] != len(peers) - 1:
        raise ValueError("incomplete verification record for payload retention")
    # Check the retained seed and every remaining duplicate before unlinking any.
    # An interrupted cleanup can have already removed some leecher copies.
    duplicates = []
    for name in peers:
        path = directory / name / "payload.bin"
        if path.resolve() != path.absolute() or not name.isalnum():
            raise ValueError(f"unexpected payload path: {path}")
        if name != "seed" and not path.exists():
            continue
        if not path.is_file() or path.stat().st_size != size or sha256(path) != expected_hash:
            raise ValueError(f"payload differs from verification checkpoint: {path}")
        if name != "seed":
            duplicates.append(path)
    for path in duplicates:
        path.unlink()
    print(f"Retained verified seed payload in {directory}", flush=True)
