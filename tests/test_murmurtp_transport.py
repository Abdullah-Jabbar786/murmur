"""Public MurmurTransport contracts, selection, real swarm events and CLI."""

import argparse
import asyncio
import hashlib
import json
from contextlib import ExitStack

import pytest

from core.events import EventLog
from core.loopback import LoopbackRegistry, LoopbackTransport
from core.tcp import TcpTransport
from core.transport import Connection, Transport
from core.transports import create_transport, transport_port
from murmurtp.flow import FlowControlConfig
from murmurtp.transport import MurmurConnection, MurmurTransport
from peer.metadata import Metadata
from peer.node import _run_cli
from peer.storage import PieceStore
from peer.swarm import SwarmNode


FAST = dict(initial_rto=.03, min_rto=.01, max_rto=.1, clock_granularity=.001,
            probe_interval=.02, inactivity_timeout=5)
DATA = bytes(range(251)) * 300
META = Metadata("payload.bin", len(DATA), 32768, tuple(
    hashlib.sha256(DATA[i:i + 32768]).hexdigest() for i in range(0, len(DATA), 32768)))


@pytest.mark.parametrize("name,kind", [("tcp", TcpTransport), ("loopback", LoopbackTransport),
                                      ("murmurtp", MurmurTransport)])
async def test_factory_returns_contract_transport(name, kind):
    endpoint = create_transport({"transport": name})
    assert isinstance(endpoint, kind) and isinstance(endpoint, Transport)
    await endpoint.close()
    await endpoint.close()


@pytest.mark.parametrize("config", [[], {"transport": "udp"}, {"transport": None},
    {"transport": "murmurtp", "murmurtp": []}, {"transport": "murmurtp", "loss_shim": []},
    {"transport": "murmurtp", "murmurtp": {"typo": 1}},
    {"transport": "murmurtp", "murmurtp": {"probe_interval": 0}},
    {"transport": "murmurtp", "loss_shim": {"loss": 2}},
    {"transport": "murmurtp", "seed": True}])
def test_factory_rejects_invalid_configuration(config):
    with pytest.raises(ValueError):
        create_transport(config)


def test_transport_ports_and_explicit_ephemeral_override():
    assert transport_port({}) == 6881
    assert transport_port({"transport": "murmurtp"}) == 6882
    assert transport_port({"transport": "murmurtp", "peer_port_udp": 7777}) == 7777
    assert transport_port({"transport": "murmurtp"}, 0) == 0
    for config in ({"transport": "loopback"}, {"peer_port_tcp": True},
                   {"transport": "murmurtp", "peer_port_udp": 65536}):
        with pytest.raises(ValueError):
            transport_port(config)


async def read_exact(connection, length):
    data = bytearray()
    while len(data) < length:
        chunk = await connection.recv(min(127, length - len(data)))
        assert chunk
        data.extend(chunk)
    return bytes(data)


async def test_public_duplex_stream_stats_and_graceful_close():
    config = FlowControlConfig(receive_buffer=2400, **FAST)
    server, client = MurmurTransport(config=config), MurmurTransport(config=config)
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        assert isinstance(outgoing, Connection) and isinstance(incoming, MurmurConnection)
        initial = outgoing.stats()
        assert initial["proto"] == "murmurtp" and initial["stage"] == 7
        assert initial["cwnd_bytes"] == 12000 and initial["rtt_ms"] is None
        assert initial == outgoing.stats()  # Synchronous snapshot has no side effects.
        result = await asyncio.wait_for(asyncio.gather(outgoing.send(DATA), incoming.send(DATA[::-1]),
            read_exact(incoming, len(DATA)), read_exact(outgoing, len(DATA))), 10)
        assert result[2:] == [DATA, DATA[::-1]]
        assert outgoing.stats()["rtt_ms"] is not None
        assert outgoing.stats()["bytes_in_flight"] == 0
        await asyncio.wait_for(asyncio.gather(outgoing.close(), incoming.close()), 3)
        assert await incoming.recv() == await outgoing.recv() == b""
        await outgoing.close()
        assert outgoing.stats()["bytes_sent"] == outgoing.stats()["bytes_received"] == len(DATA)
    finally:
        await asyncio.gather(server.close(), client.close())
        assert not server._tasks and not client._tasks


async def test_public_close_after_abort_is_idempotent_but_io_still_fails():
    server, client = MurmurTransport(config=FlowControlConfig(**FAST)), MurmurTransport(config=FlowControlConfig(**FAST))
    try:
        await server.listen(("127.0.0.1", 0))
        outgoing = await client.connect(server.local_addr)
        incoming = await server.accept()
        outgoing._abort(ConnectionAbortedError("test cancellation"))
        await outgoing.close()
        await outgoing.close()
        with pytest.raises(ConnectionAbortedError):
            await outgoing.send(b"not delivered")
        with pytest.raises(ConnectionAbortedError):
            await outgoing.recv()
        with pytest.raises(ConnectionResetError):
            await asyncio.wait_for(incoming.recv(), 2)
        await incoming.close()
    finally:
        await asyncio.gather(server.close(), client.close())


@pytest.mark.parametrize("transport,mode", [("loopback", "swarm"), ("murmurtp", "swarm"),
                                          ("murmurtp", "client_server")])
async def test_same_swarm_over_public_transport_with_loss_and_events(tmp_path, transport, mode):
    nodes, logs = [], []
    registry = LoopbackRegistry()
    with ExitStack() as resources:
        try:
            for index in range(3):
                path = tmp_path / f"data{index}.bin"
                if index == 0:
                    path.write_bytes(DATA)
                store = resources.enter_context(PieceStore(path, META, mode="seed" if index == 0 else "create"))
                log = resources.enter_context(EventLog("127.0.0.1", str(tmp_path / f"logs{index}")))
                logs.append(log)
                endpoint = create_transport({"transport": transport, "murmurtp": FAST,
                    "loss_shim": {"loss": .05, "reorder": .05, "duplicate": .01,
                                  "reorder_delay": .003}, "seed": index + 7}, registry=registry)
                node = SwarmNode(store, transport=endpoint, is_seed=index == 0, mode=mode,
                    peer_id=index.to_bytes(20, "big"), event_log=log, sample_interval=.02,
                    seed_address=nodes[0].address if index and mode == "client_server" else None)
                nodes.append(node)
                await node.start(("127.0.0.1", 7000 + index if transport == "loopback" else 0))
            # Chain requires relay uploads; client-server leaves both use origin.
            await nodes[2].connect(nodes[1 if mode == "swarm" else 0].address)
            await nodes[1].connect(nodes[0].address)
            await asyncio.gather(*(node.wait_complete(15) for node in nodes[1:]))
            async def accounted():
                while sum(node.bytes_sent for node in nodes) != 2 * len(DATA):
                    await asyncio.sleep(.001)
            await asyncio.wait_for(accounted(), 3)
            for node in nodes:
                assert node.store.path.read_bytes() == DATA
                assert not node.errors and node.hash_failures == 0
                node._events.sample()
            if mode == "swarm":
                assert nodes[1].bytes_sent == len(DATA)
            else:
                assert nodes[1].bytes_sent == nodes[2].bytes_sent == 0
        finally:
            await asyncio.wait_for(asyncio.gather(*(node.close() for node in nodes)), 6)
        for index, node in enumerate(nodes):
            assert not node._tasks and not node._links and not node._claims
            assert not node._events._connections and node._events._task.done()
            with open(logs[index].file_path, encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream]
            metrics = [row for row in rows if row["type"] == "transport"]
            assert metrics and all(row["proto"] == transport for row in metrics)
            assert sum(row["type"] == "summary" for row in rows) == 1
            assert rows[-1]["bytes_down"] == node.bytes_received
            assert rows[-1]["bytes_up"] == node.bytes_sent
            final_by_connection = {row["connection_id"]: row["retx"] for row in metrics}
            assert rows[-1]["retx"] == sum(final_by_connection.values())
            if transport == "murmurtp":
                assert any(row["rtt_ms"] is not None for row in metrics)
                assert all(row["cwnd_bytes"] >= 1200 and row["retx"] >= 0 for row in metrics)
                assert not node.transport._tasks and not node.transport._peers


@pytest.mark.parametrize("name", ["tcp", "murmurtp"])
async def test_cli_config_selects_real_transport_and_cleans_up(tmp_path, monkeypatch, name):
    source, metadata, config = tmp_path / "source.bin", tmp_path / "meta.json", tmp_path / "config.json"
    source.write_bytes(DATA)
    metadata.write_bytes(META.canonical_bytes())
    config.write_text(json.dumps({"transport": name, "murmurtp": FAST}), encoding="utf-8")
    inspected = []
    original_start = SwarmNode.start

    async def start(node, address):
        inspected.append(node)
        await original_start(node, address)
        raise asyncio.CancelledError

    monkeypatch.setattr(SwarmNode, "start", start)
    args = argparse.Namespace(command="swarm", metadata=str(metadata), file=str(source),
        config=str(config), distribution_mode="swarm", peer=[], origin_seed=None,
        mode="seed", host="127.0.0.1", port=0, io_timeout=5, total_timeout=5, run_dir=str(tmp_path / "logs"))
    with pytest.raises(asyncio.CancelledError):
        await _run_cli(args)
    node = inspected[0]
    assert isinstance(node.transport, TcpTransport if name == "tcp" else MurmurTransport)
    assert node.store.closed and node._events._task.done()
    if name == "murmurtp":
        assert node.transport._socket_closed.done()


async def test_cli_invalid_transport_creates_no_output(tmp_path):
    metadata, config, destination = tmp_path / "meta.json", tmp_path / "config.json", tmp_path / "out.bin"
    metadata.write_bytes(META.canonical_bytes())
    config.write_text('{"transport":"loopback"}', encoding="utf-8")
    args = argparse.Namespace(command="swarm", metadata=str(metadata), file=str(destination),
        config=str(config), distribution_mode="swarm", peer=[], origin_seed=None,
        mode="create", host="127.0.0.1", port=None, io_timeout=5, total_timeout=5, run_dir=None)
    with pytest.raises(ValueError, match="in-process"):
        await _run_cli(args)
    assert not destination.exists()


@pytest.mark.parametrize("name", ["tcp", "murmurtp"])
async def test_cli_download_uses_configured_transport_and_writes_summary(tmp_path, name):
    source, metadata, config = tmp_path / "source.bin", tmp_path / "meta.json", tmp_path / "config.json"
    destination, run_dir = tmp_path / "out.bin", tmp_path / "run"
    source.write_bytes(DATA)
    metadata.write_bytes(META.canonical_bytes())
    settings = {"transport": name, "murmurtp": FAST}
    config.write_text(json.dumps(settings), encoding="utf-8")
    task = None
    with PieceStore(source, META, mode="seed") as store:
        seed = SwarmNode(store, transport=create_transport(settings), is_seed=True)
        try:
            await seed.start(("127.0.0.1", 0))
            args = argparse.Namespace(command="swarm", metadata=str(metadata), file=str(destination),
                config=str(config), distribution_mode="swarm", peer=[seed.address], origin_seed=None,
                mode="create", host="127.0.0.1", port=0, io_timeout=5, total_timeout=10, run_dir=str(run_dir))
            task = asyncio.create_task(_run_cli(args))
            log_path = run_dir / "events-127.0.0.1.jsonl"

            async def completed():
                while True:
                    if task.done():
                        await task
                        pytest.fail("CLI exited before completion")
                    if log_path.exists():
                        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                        if any(row["type"] == "complete" for row in rows):
                            return
                    await asyncio.sleep(.005)

            await asyncio.wait_for(completed(), 12)
            assert destination.read_bytes() == DATA
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            assert rows[-1]["type"] == "summary" and rows[-1]["complete"]
            assert rows[-1]["bytes_down"] == len(DATA)
            assert all(row["proto"] == name for row in rows if row["type"] == "transport")
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await seed.close()
