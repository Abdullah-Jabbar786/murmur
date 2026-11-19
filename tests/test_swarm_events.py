"""H2.4 event schemas, transfer accounting, periodic metrics, and cleanup."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from core.events import EventLog
from core.loopback import LoopbackRegistry, LoopbackTransport
from core.tcp import TcpTransport
from peer.metadata import Metadata
from peer.node import _run_cli
from peer.storage import PieceStore
from peer.swarm import SwarmNode
from peer.telemetry import PeerEvents

DATA = b"a" * 32768 + b"b" * 32768 + b"tail"
META = Metadata("payload.bin", len(DATA), 32768, tuple(
    hashlib.sha256(DATA[i:i + 32768]).hexdigest() for i in range(0, len(DATA), 32768)))


def records(log):
    with open(log.file_path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def check_schema(rows):
    for row in rows:
        assert isinstance(row["t"], float) and math.isfinite(row["t"])
        assert row["node"] == "127.0.0.1"
        kind = row["type"]
        if kind in ("have", "hash_fail", "piece_select"):
            assert type(row["piece"]) is int and row["piece"] >= 0
            if kind == "piece_select":
                assert type(row["availability"]) is int and row["availability"] >= 1
                assert type(row["started"]) is bool
                assert isinstance(row["peer"], str)
        elif kind == "rate":
            assert all(type(row[key]) is int and row[key] >= 0 for key in ("up_bps", "down_bps"))
        elif kind == "transport":
            assert row["proto"] in ("tcp", "loopback")
            assert isinstance(row["peer"], str)
            for key in ("cwnd_bytes", "rtt_ms", "retx", "bytes_in_flight"):
                assert row[key] is None or row[key] >= 0
        elif kind in ("choke", "unchoke"):
            assert isinstance(row["peer"], str)
        elif kind == "summary":
            assert all(type(row[key]) is int and row[key] >= 0
                       for key in ("bytes_down", "bytes_up", "hash_fails"))
            assert row["retx"] is None or row["retx"] >= 0
        else:
            assert kind in ("join", "complete")


async def eventually(condition, timeout=3):
    async def wait():
        while not condition():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout)


@asynccontextmanager
async def pair(tmp_path, transport="loopback", mode="swarm"):
    nodes, stores, logs = [], [], []
    registry = LoopbackRegistry()
    try:
        for index in range(2):
            store = PieceStore(tmp_path / f"data{index}.bin", META)
            stores.append(store)
            if index == 0:
                for piece in range(3):
                    for offset, size in store.missing_blocks(piece):
                        start = piece * META.piece_length + offset
                        store.write_block(piece, offset, DATA[start:start + size])
            log = EventLog("127.0.0.1", str(tmp_path / f"logs{index}"))
            logs.append(log)
            endpoint = LoopbackTransport(registry) if transport == "loopback" else TcpTransport()
            node = SwarmNode(store, transport=endpoint, is_seed=index == 0, mode=mode,
                             seed_address=nodes[0].address if index and mode == "client_server" else None,
                             event_log=log, sample_interval=0.02)
            nodes.append(node)
            await node.start(("127.0.0.1", 7000 + index if transport == "loopback" else 0))
        yield nodes, logs
    finally:
        await asyncio.gather(*(node.close() for node in nodes))
        for store in stores:
            store.close()
        for log in logs:
            log.close()


@pytest.mark.parametrize("transport,mode", [("loopback", "swarm"), ("tcp", "swarm"),
                                          ("loopback", "client_server")])
async def test_transfer_emits_contract_events_and_exact_summary(tmp_path, transport, mode):
    async with pair(tmp_path, transport, mode) as ((seed, leecher), logs):
        await leecher.connect(seed.address)
        await leecher.wait_complete(5)
        await eventually(lambda: seed.bytes_sent == len(DATA))
        await eventually(lambda: any(row["type"] == "choke" for row in records(logs[0])))
        await eventually(lambda: len([r for r in records(logs[1]) if r["type"] == "rate"]) >= 3)
        # Readers can consume complete JSONL lines before the logger is closed.
        assert any(r["type"] == "complete" for r in records(logs[1]))
        await asyncio.gather(seed.close(), leecher.close())
        await leecher.close()
        for index, log in enumerate(logs):
            rows = records(log)
            check_schema(rows)
            assert rows[0]["type"] == "join" and rows[-1]["type"] == "summary"
            assert sum(r["type"] == "summary" for r in rows) == 1
            assert sum(r["type"] == "complete" for r in rows) == 1
            assert {r["piece"] for r in rows if r["type"] == "have"} == {0, 1, 2}
            assert all(r["initial"] == (index == 0) for r in rows if r["type"] == "have")
            assert any(r["type"] == "transport" for r in rows)
            summary = rows[-1]
            assert summary["bytes_down"] == (0 if index == 0 else len(DATA))
            assert summary["bytes_up"] == (len(DATA) if index == 0 else 0)
            assert summary["hash_fails"] == 0 and summary["complete"]
            if transport == "loopback":
                assert summary["retx"] == 0
        seed_events = records(logs[0])
        assert any(r["type"] == "unchoke" for r in seed_events)


async def test_corruption_logs_hash_failure_before_verified_have(tmp_path, monkeypatch):
    async with pair(tmp_path) as ((seed, leecher), logs):
        original, corrupted = seed.store.read_block, []
        def read(piece, offset, length):
            data = original(piece, offset, length)
            if not corrupted:
                corrupted.append(piece)
                return bytes([data[0] ^ 1]) + data[1:]
            return data
        monkeypatch.setattr(seed.store, "read_block", read)
        await leecher.connect(seed.address)
        await leecher.wait_complete(5)
        await leecher.close()
        rows = records(logs[1])
        failure = next(i for i, r in enumerate(rows) if r["type"] == "hash_fail")
        verified = next(i for i, r in enumerate(rows)
                        if r["type"] == "have" and r["piece"] == corrupted[0])
        assert failure < verified
        assert rows[-1]["hash_fails"] == 1
        assert rows[-1]["bytes_down"] == len(DATA) + META.piece_length
        assert sum(r["type"] == "have" and r["piece"] == corrupted[0] for r in rows) == 1


async def test_default_sampler_runs_once_a_second_and_seed_logs_idle_rates(tmp_path):
    with PieceStore(tmp_path / "empty.bin", Metadata("empty.bin", 0, 32768, ())) as store:
        with EventLog("127.0.0.1", str(tmp_path / "events")) as log:
            node = SwarmNode(store, event_log=log)
            try:
                await node.start(("127.0.0.1", 0))
                assert node._events.interval == 1
                await eventually(lambda: len([r for r in records(log) if r["type"] == "rate"]) >= 2)
                assert all(r["up_bps"] == r["down_bps"] == 0
                           for r in records(log) if r["type"] == "rate")
            finally:
                await node.close()


@pytest.mark.parametrize("unknown", [False, True])
async def test_rate_math_and_cumulative_retx_not_double_counted(tmp_path, unknown):
    now = [0.0]
    node = SimpleNamespace(mode="swarm", is_seed=False, bytes_received=0, bytes_sent=0,
                           hash_failures=0, store=SimpleNamespace(owned_pieces=(), complete=False,
                               metadata=SimpleNamespace(length=0, piece_length=262144, num_pieces=0)),
                           _complete=asyncio.Event())
    class Connection:
        peer_addr = ("127.0.0.1", 9000)
        retx = 1
        def stats(self):
            return dict(proto="tcp", retx=self.retx, cwnd_bytes=None, rtt_ms=None, bytes_in_flight=None)
    with EventLog("127.0.0.1", str(tmp_path)) as log:
        events = PeerEvents(node, log, clock=lambda: now[0])
        events.start()
        first, second = Connection(), Connection()
        events.opened(first)
        node.bytes_received, node.bytes_sent, now[0] = 100, 50, 2.0
        events.sample()
        rate = [r for r in records(log) if r["type"] == "rate"][-1]
        assert (rate["down_bps"], rate["up_bps"]) == (400, 200)
        first.retx = 3
        now[0] = 4.0
        events.sample()
        events.closed(first)
        events.closed(first)  # Closing twice cannot add the counter twice.
        second.retx = None if unknown else 5
        events.opened(second)
        events.sample()
        events.closed(second)
        await events.finish()
        await events.finish()
        rows = records(log)
        assert rows[-1]["retx"] == (None if unknown else 8)
        assert sum(r["type"] == "summary" for r in rows) == 1
        assert len({r["connection_id"] for r in rows if r["type"] == "transport"}) == 2
        check_schema(rows)


async def test_partial_shutdown_logs_summary_without_false_complete(tmp_path):
    with PieceStore(tmp_path / "partial.bin", META) as store:
        with EventLog("127.0.0.1", str(tmp_path / "logs")) as log:
            node = SwarmNode(store, event_log=log)
            await node.start(("127.0.0.1", 0))
            waiter = asyncio.create_task(node.wait_complete(5))
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            await node.close()
            await node.close()
            rows = records(log)
            assert not any(r["type"] == "complete" for r in rows)
            assert rows[-1]["type"] == "summary" and not rows[-1]["complete"]
            assert sum(r["type"] == "summary" for r in rows) == 1
            assert not store.closed


@pytest.mark.parametrize("phase", ["startup", "periodic"])
async def test_logging_failure_is_reported_and_socket_cleanup_still_runs(tmp_path, phase):
    with PieceStore(tmp_path / "data.bin", META) as store:
        log = EventLog("127.0.0.1", str(tmp_path / "logs"))
        node = SwarmNode(store, event_log=log, sample_interval=0.01)
        if phase == "startup":
            log.close()
            with pytest.raises(ValueError, match="closed"):
                await node.start(("127.0.0.1", 0))
        else:
            await node.start(("127.0.0.1", 0))
            log.close()
            await eventually(lambda: node._events.error is not None)
        with pytest.raises(ValueError, match="closed"):
            await node.wait_complete(1)
        with pytest.raises(ValueError, match="closed"):
            await node.close()
        assert node.transport.local_addr is None
        assert not store.closed


async def test_cli_cancellation_writes_summary_and_closes_log(tmp_path, capsys):
    source, metadata_path, run_dir = tmp_path / "source.bin", tmp_path / "metadata.json", tmp_path / "run"
    source.write_bytes(DATA)
    metadata_path.write_bytes(META.canonical_bytes())
    args = argparse.Namespace(command="swarm", metadata=str(metadata_path), config=None,
                              distribution_mode="swarm", peer=[], mode="seed", origin_seed=None,
                              file=str(source), io_timeout=30, host="127.0.0.1", port=0,
                              total_timeout=30, run_dir=str(run_dir))
    task = asyncio.create_task(_run_cli(args))
    path = run_dir / "events-127.0.0.1.jsonl"
    try:
        await eventually(lambda: path.exists() and '"complete"' in path.read_text(encoding="utf-8"))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["type"] == "summary"
    assert sum(r["type"] == "summary" for r in rows) == 1
    path.rename(run_dir / "closed.jsonl")  # Windows must have released the file handle.
    assert "Peer listening on" in capsys.readouterr().out
