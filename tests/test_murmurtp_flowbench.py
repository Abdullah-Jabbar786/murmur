"""Verified single-flow benchmark, failure paths, logs and real CLI processes."""

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from core.events import EventLog
from core.loopback import LoopbackRegistry
from core.transports import create_transport
from murmurtp.flowbench import (HEADER, MAGIC, RECEIPT, FlowError,
                               receive_stream, run_once, send_file)


ROOT = Path(__file__).resolve().parents[1]
DATA = bytes(range(251)) * 1000


def config(name):
    return dict(transport=name, murmurtp=dict(initial_rto=.05, min_rto=.01, max_rto=.2,
                probe_interval=.02, inactivity_timeout=5),
                loss_shim=dict(loss=.02, reorder=.02, duplicate=.01, reorder_delay=.003))


@pytest.mark.parametrize("name", ["loopback", "tcp", "murmurtp"])
async def test_verified_transfer_and_exact_payload_summaries(tmp_path, name):
    source = tmp_path / "source.bin"
    source.write_bytes(DATA)
    registry = LoopbackRegistry()
    server, client = (create_transport(config(name), registry=registry) for _ in range(2))
    ready = asyncio.get_running_loop().create_future()
    tasks = []
    with EventLog("receiver", str(tmp_path)) as recv_log, EventLog("sender", str(tmp_path)) as send_log:
        try:
            tasks.append(asyncio.create_task(run_once(server, ("127.0.0.1", 7151 if name == "loopback" else 0),
                timeout=10, event_log=recv_log, sample_interval=.02, on_listening=ready.set_result)))
            address = await asyncio.wait_for(ready, 2)
            tasks.append(asyncio.create_task(run_once(client, address, source=source,
                timeout=10, event_log=send_log, sample_interval=.02)))
            receiver, sender = await asyncio.gather(*tasks)
            for report in (receiver, sender):
                assert report["verified"] and report["bytes"] == len(DATA)
                assert report["sha256"] == hashlib.sha256(DATA).hexdigest()
                assert report["seconds"] > 0
                assert report["goodput_mbps"] == pytest.approx(len(DATA) * 8 / report["seconds"] / 1e6)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(server.close(), client.close())
    for role in ("sender", "receiver"):
        rows = [json.loads(line) for line in (tmp_path / f"events-{role}.jsonl").read_text().splitlines()]
        assert sum(row["type"] == "complete" for row in rows) == 1
        assert sum(row["type"] == "summary" for row in rows) == 1
        summary = rows[-1]
        assert summary["complete"] and summary["error"] is None and summary["hash_fails"] == 0
        assert summary["bytes_up"] == (len(DATA) if role == "sender" else 0)
        assert summary["bytes_down"] == (len(DATA) if role == "receiver" else 0)
        metrics = [row for row in rows if row["type"] == "transport"]
        assert metrics and all(row["proto"] == name for row in metrics)
        if name == "murmurtp":
            assert all(row["cwnd_bytes"] >= 1200 for row in metrics)
        if name == "tcp" and sys.platform != "linux":
            assert all(row["cwnd_bytes"] is None for row in metrics)
    if name == "murmurtp":
        assert not server._tasks and not client._tasks and not server._peers and not client._peers
    if name == "loopback":
        assert registry.lookup(address) is None


class MemoryConnection:
    def __init__(self, data):
        self.data, self.sent = bytearray(data), []

    async def recv(self, size):
        take = min(size, 7)  # Deliberately split every protocol boundary.
        data = bytes(self.data[:take])
        del self.data[:take]
        return data

    async def send(self, data):
        self.sent.append(data)


def progress():
    return SimpleNamespace(up=0, down=0)


@pytest.mark.parametrize("kind", ["magic", "limit", "empty", "short_header", "short_data", "hash"])
async def test_receiver_rejects_invalid_streams_without_success_receipt(kind):
    payload = b"payload"
    expected = hashlib.sha256(payload).digest()
    data = HEADER.pack(MAGIC, len(payload), expected) + payload
    limit = 100
    if kind == "magic":
        data = b"badmagic" + data[8:]
    elif kind == "limit":
        limit = 1
    elif kind == "empty":
        data = HEADER.pack(MAGIC, 0, expected)
    elif kind == "short_header":
        data = data[:10]
    elif kind == "short_data":
        data = data[:-1]
    else:
        data = data[:-1] + b"X"
    connection = MemoryConnection(data)
    with pytest.raises(FlowError):
        await receive_stream(connection, limit, progress())
    assert all(not message.startswith(RECEIPT) for message in connection.sent)


@pytest.mark.parametrize("kind", ["ready", "receipt", "empty", "changed"])
async def test_sender_requires_valid_receipt_and_unchanged_source(tmp_path, kind):
    source = tmp_path / "source.bin"
    source.write_bytes(b"" if kind == "empty" else b"payload")
    connection = MemoryConnection(b"\x00" if kind == "ready" else b"\x01" + b"X" * HEADER.size)
    if kind == "changed":
        send = connection.send

        async def mutate(data):
            await send(data)
            if data.startswith(MAGIC):
                source.write_bytes(b"changed")

        connection.send = mutate
    match = "source changed" if kind == "changed" else None
    with pytest.raises(FlowError, match=match):
        await send_file(connection, source, progress())


@pytest.mark.parametrize("cancel", [False, True])
async def test_accept_timeout_or_cancellation_closes_endpoint_and_logs_failure(tmp_path, cancel):
    registry = LoopbackRegistry()
    endpoint = create_transport({"transport": "loopback"}, registry=registry)
    ready = asyncio.Event()
    address = ("127.0.0.1", 7190)
    with EventLog("receiver", str(tmp_path)) as log:
        task = asyncio.create_task(run_once(endpoint, address, timeout=5 if cancel else .03,
                    event_log=log, on_listening=lambda address: ready.set()))
        await ready.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
            await task
    assert registry.lookup(address) is None
    rows = [json.loads(line) for line in Path(log.file_path).read_text().splitlines()]
    assert rows[-1]["type"] == "summary" and not rows[-1]["complete"]
    assert rows[-1]["bytes_up"] == rows[-1]["bytes_down"] == 0
    assert not any(row["type"] == "complete" for row in rows)


@pytest.mark.parametrize("options", [dict(timeout=0), dict(timeout=float("nan")),
    dict(sample_interval=-1), dict(max_bytes=0), dict(max_bytes=True)])
async def test_invalid_options_close_endpoint(options):
    endpoint = create_transport({"transport": "tcp"})
    with pytest.raises(ValueError):
        await run_once(endpoint, ("127.0.0.1", 7180), **options)
    with pytest.raises(RuntimeError):
        await endpoint.listen(("127.0.0.1", 7180))


async def test_hash_failure_logs_received_payload_and_no_completion(tmp_path):
    registry = LoopbackRegistry()
    server, client = (create_transport({"transport": "loopback"}, registry=registry) for _ in range(2))
    ready = asyncio.Event()
    with EventLog("receiver", str(tmp_path)) as log:
        task = asyncio.create_task(run_once(server, ("127.0.0.1", 7181), event_log=log,
                                            on_listening=lambda address: ready.set()))
        try:
            await ready.wait()
            connection = await client.connect(("127.0.0.1", 7181))
            await connection.send(HEADER.pack(MAGIC, 3, hashlib.sha256(b"abc").digest()))
            assert await connection.recv(1) == b"\x01"
            await connection.send(b"bad")
            with pytest.raises(FlowError, match="SHA-256"):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.close()
    rows = [json.loads(line) for line in Path(log.file_path).read_text().splitlines()]
    assert rows[-1]["hash_fails"] == 1 and rows[-1]["bytes_down"] == 3
    assert not rows[-1]["complete"] and rows[-1]["result"] is None
    assert not any(row["type"] == "complete" for row in rows)


async def test_cancel_during_payload_send_joins_worker_and_closes_endpoint(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(DATA)
    started, stopped = asyncio.Event(), asyncio.Event()
    connection = MemoryConnection(b"\x01")
    connection.peer_addr = ("127.0.0.1", 7182)
    connection.stats = lambda: {"proto": "loopback"}
    children = []

    async def send(data):
        if data.startswith(MAGIC):
            return
        children.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class Endpoint:
        closed = False

        async def connect(self, address):
            return connection

        async def close(self):
            self.closed = True

    connection.send = send
    endpoint = Endpoint()
    with EventLog("sender", str(tmp_path)) as log:
        task = asyncio.create_task(run_once(endpoint, connection.peer_addr, source=source,
                                           event_log=log, timeout=5))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set() and endpoint.closed and all(child.done() for child in children)
    rows = [json.loads(line) for line in Path(log.file_path).read_text().splitlines()]
    assert not rows[-1]["complete"] and rows[-1]["error"].startswith("CancelledError")


@pytest.mark.parametrize("name", ["tcp", "murmurtp"])
async def test_real_cli_sender_receiver(tmp_path, name):
    source, cfg = tmp_path / "source.bin", tmp_path / "config.json"
    source.write_bytes(DATA[:40000])
    cfg.write_text(json.dumps(config(name)))
    base = [sys.executable, "-m", "murmurtp.flowbench"]
    server = await asyncio.create_subprocess_exec(*base, "receive", "--port", "0",
        "--config", str(cfg), "--transport", name, "--run-dir", str(tmp_path / "receiver"), "--timeout", "15",
        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    client = None
    try:
        line = (await asyncio.wait_for(server.stdout.readline(), 5)).decode().strip()
        assert line.startswith("Listening on 127.0.0.1:"), line
        port = line.rsplit(":", 1)[1]
        client = await asyncio.create_subprocess_exec(*base, "send", "--port", port,
            "--config", str(cfg), "--transport", name, "--file", str(source), "--run-dir", str(tmp_path / "sender"),
            "--timeout", "15", cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        (out, err), (server_out, server_err) = await asyncio.wait_for(
            asyncio.gather(client.communicate(), server.communicate()), 20)
        assert client.returncode == server.returncode == 0, (err, server_err)
        assert json.loads(out)["sha256"] == json.loads(server_out)["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    finally:
        for process in (client, server):
            if process is not None:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
