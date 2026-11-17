"""One seed and ten leechers: same acceptance on Loopback and MurmurTP."""

import asyncio
import hashlib
import json
import time

import pytest

from core.events import EventLog
from core.loopback import LoopbackRegistry, LoopbackTransport
from murmurtp.api import MurmurTransport
from murmurtp.flow import FlowControlConfig
from peer.metadata import generate_dataset
from peer.storage import PieceStore
from peer.swarm import SwarmNode

LEECHER_COUNT = 10
PAYLOAD_SIZE = 2 * 1024 * 1024 + 37
TRANSFER_TIMEOUT = 20.0
CLEANUP_TIMEOUT = 5.0


def verify_file(path, metadata):
    """Independent disk verification, including the short tail and exact EOF."""
    whole = hashlib.sha256()
    with path.open("rb") as stream:
        for index, expected in enumerate(metadata.pieces):
            expected_size = min(metadata.piece_length,
                                metadata.length - index * metadata.piece_length)
            piece = stream.read(metadata.piece_length)
            assert len(piece) == expected_size
            assert hashlib.sha256(piece).hexdigest() == expected
            whole.update(piece)
        assert stream.read(1) == b""
    assert path.stat().st_size == metadata.length
    return whole.hexdigest()


@pytest.mark.parametrize("mode", ["swarm", "client_server"])
@pytest.mark.parametrize("transport", ["loopback", "murmurtp"])
async def test_one_seed_ten_leechers_complete_and_release_resources(tmp_path, mode, record_property, transport):
    started = time.monotonic()
    transfer_timeout = TRANSFER_TIMEOUT if transport == "loopback" else 60
    metadata = generate_dataset(tmp_path / "seed/payload.bin", tmp_path / "metadata.json",
                                size=PAYLOAD_SIZE, seed=2026)
    expected_hash = verify_file(tmp_path / "seed/payload.bin", metadata)
    assert metadata.num_pieces == 9
    assert metadata.length % metadata.piece_length == 37
    registry = LoopbackRegistry()
    nodes, stores, logs, addresses = [], [], [], []
    try:
        for index in range(LEECHER_COUNT + 1):
            path = tmp_path / ("seed/payload.bin" if index == 0 else f"leecher-{index}/payload.bin")
            store = PieceStore(path, metadata, mode="seed" if index == 0 else "create")
            stores.append(store)
            address = (f"10.0.1.{index + 1}", 6881) if transport == "loopback" else ("127.0.0.1", 0)
            log = EventLog(address[0], str(tmp_path / "events" / str(index)))
            logs.append(log)
            endpoint = (LoopbackTransport(registry) if transport == "loopback" else
                        MurmurTransport(config=FlowControlConfig(initial_rto=.05, min_rto=.01,
                            max_rto=.2, clock_granularity=.001, probe_interval=.02, inactivity_timeout=10)))
            node = SwarmNode(store, transport=endpoint,
                             peer_id=index.to_bytes(20, "big"), is_seed=index == 0,
                             mode=mode, event_log=log,
                             seed_address=addresses[0] if index and mode == "client_server" else None)
            nodes.append(node)
            await node.start(address)
            addresses.append(node.address)

        async def transfer():
            if mode == "swarm":
                # Build the nonseed mesh before introducing data. Leaves have
                # no direct seed connection, and several peers have two sources.
                edges = [(3, 1), (4, 1), (5, 2), (6, 2), (7, 3), (8, 4),
                         (9, 5), (10, 6), (4, 2), (5, 1), (7, 8), (9, 10)]
                await asyncio.gather(*(nodes[child].connect(addresses[parent])
                                       for child, parent in edges))
                await asyncio.gather(nodes[1].connect(addresses[0]), nodes[2].connect(addresses[0]))
            else:
                await asyncio.gather(*(node.connect(addresses[0]) for node in nodes[1:]))
            await asyncio.gather(*(node.wait_complete(transfer_timeout) for node in nodes[1:]))
            # Send completion may be scheduled just after the receiver verifies.
            # Also allow workers to release their final piece reservations.
            while (sum(node.bytes_sent for node in nodes) != LEECHER_COUNT * PAYLOAD_SIZE
                   or any(node._claims for node in nodes)):
                await asyncio.sleep(0.001)

        try:
            await asyncio.wait_for(transfer(), transfer_timeout)
        except asyncio.TimeoutError:
            pytest.fail("whole-swarm transfer exceeded deadline: " + repr([
                {"peer": index, "left": node.store.left, "errors": list(node.errors)}
                for index, node in enumerate(nodes)]))

        for node in nodes:
            assert node.store.complete and node.store.left == 0
            assert node.store.owned_pieces == frozenset(range(metadata.num_pieces))
            assert node.hash_failures == 0 and not node.errors
            assert verify_file(node.store.path, metadata) == expected_hash
        assert all(node.bytes_received == PAYLOAD_SIZE for node in nodes[1:])
        assert nodes[0].bytes_received == 0
        if mode == "swarm":
            assert nodes[0].peer_id not in set().union(*(node.peer_ids for node in nodes[3:]))
            assert sum(node.bytes_sent for node in nodes[1:]) >= 8 * PAYLOAD_SIZE
            assert 0 < nodes[0].bytes_sent <= 2 * PAYLOAD_SIZE
        else:
            assert nodes[0].bytes_sent == LEECHER_COUNT * PAYLOAD_SIZE
            assert all(node.bytes_sent == 0 for node in nodes[1:])
            assert all(node.peer_ids == frozenset({nodes[0].peer_id}) for node in nodes[1:])
    finally:
        try:
            await asyncio.wait_for(asyncio.gather(*(node.close() for node in nodes)), CLEANUP_TIMEOUT)
        finally:
            for store in stores:
                store.close()
            for log in logs:
                log.close()

    for index, node in enumerate(nodes):
        if transport == "loopback":
            assert registry.lookup(addresses[index]) is None
        else:
            assert not node.transport._tasks and not node.transport._peers
            assert node.transport._socket_closed.done()
        assert not node._links and not node._claims and not node._tasks
        assert node._accept_task.done() and node._events._task.done()
        assert node._policy_task.done()
        assert node._events._connections == {}
        assert stores[index].closed
        with open(logs[index].file_path, encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream]
        assert events[0]["type"] == "join" and events[-1]["type"] == "summary"
        assert sum(e["type"] == "summary" for e in events) == 1
        assert sum(e["type"] == "complete" for e in events) == 1
        assert {e["piece"] for e in events if e["type"] == "have"} == set(range(metadata.num_pieces))
        summary = events[-1]
        assert summary["complete"] and summary["hash_fails"] == 0
        assert summary["retx"] >= 0
        if transport == "loopback":
            assert summary["retx"] == 0
        else:
            metrics = [e for e in events if e["type"] == "transport"]
            assert metrics and all(e["proto"] == "murmurtp" for e in metrics)
            assert any(e["rtt_ms"] is not None for e in metrics)
        assert summary["bytes_down"] == node.bytes_received
        assert summary["bytes_up"] == node.bytes_sent
    elapsed = time.monotonic() - started
    record_property("swarm_elapsed_seconds", round(elapsed, 3))
    budget = 30 if transport == "loopback" else 70
    assert elapsed < budget, f"whole-swarm case took {elapsed:.2f}s (budget: <{budget}s)"
