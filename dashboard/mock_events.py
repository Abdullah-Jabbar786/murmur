"""Generate a deterministic Contract 3 fixture for local dashboard development."""
import argparse
import json
import random
from contextlib import ExitStack
from pathlib import Path


def generate(folder):
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.glob("events-*.jsonl")):
        raise ValueError("Use an empty output directory to keep runs separate.")
    rng = random.Random(1)
    seed = "10.0.3.11"
    clients = [f"10.0.{lan}.{i}" for lan in (1, 2) for i in range(11, 16)]
    count, chunk = 256, 262144
    with ExitStack() as stack:
        streams = {}

        def emit(t, node, kind, **fields):
            if node not in streams:
                streams[node] = stack.enter_context((folder / f"events-{node}.jsonl").open("x", encoding="utf8"))
            print(json.dumps(dict(t=float(t), node=node, type=kind, **fields)), file=streams[node])

        emit(0, "harness", "marker", label="Synthetic fixture")
        for node in [seed, *clients]:
            emit(0, node, "join", mode="swarm", origin_seed=node == seed, initial_complete=node == seed,
                 piece_count=count, file_bytes=count*chunk, piece_bytes=chunk)
        for piece in range(count):
            emit(0, seed, "have", piece=piece, initial=True)
        emit(0, seed, "complete", initial=True)
        uploads = {}
        for index, node in enumerate(clients):
            order = rng.sample(range(count), count)
            start = index * 8
            emit(start, seed, "unchoke", peer=node)
            for batch in range(32):
                t = start + batch + 1
                for piece in order[batch*8:(batch+1)*8]:
                    emit(t-0.5, node, "piece_select", piece=piece, peer=seed)
                    emit(t, node, "have", piece=piece, initial=False)
                uploads[t] = uploads.get(t, 0) + 8*chunk*8
                emit(t, node, "rate", down_bps=8*chunk*8, up_bps=0)
                emit(t, node, "transport", proto="murmurtp", connection_id=1, peer=seed,
                     cwnd_bytes=(10+batch)*1200, rtt_ms=20, retx=0, bytes_in_flight=1200)
            emit(t, node, "complete", initial=False)
            emit(t, node, "summary", complete=True, bytes_down=count*chunk, bytes_up=0, retx=0, hash_fails=0)
            emit(t, seed, "choke", peer=node)
        for t, up in sorted(uploads.items()):
            emit(t, seed, "rate", down_bps=0, up_bps=up)
        emit(max(uploads), seed, "summary", complete=True, bytes_down=0, bytes_up=len(clients)*count*chunk,
             retx=0, hash_fails=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("mock_run"))
    generate(parser.parse_args().output)
