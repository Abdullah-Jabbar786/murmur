"""M2.2 runner and tracker discovery checks on local TCP sockets."""
import argparse
import asyncio
import json
import socket
import sys
import threading
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

from core.config import load_config
from dashboard.live import RunLog
from experiments import run
from peer.discovery import serve_discovered
from peer.metadata import generate_dataset
from peer.node import _run_cli
from tracker.client import AnnounceResult, TrackerError
from tracker.server import create_app


def test_lab_inventory():
    peers = run.lab_peers()
    assert peers.pop("seed") == "10.0.3.11"
    assert peers == {f"{lan}{i}": f"10.0.{subnet}.{10 + i}"
                     for lan, subnet in (("a", 1), ("b", 2)) for i in range(1, 6)}


async def test_stop_waits_for_registration_in_flight():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Tracker:
        async def announce(self, peer, *, left, event):
            if event == "started":
                entered.set()
                await release.wait()
            calls.append(event)
            return AnnounceResult((), 1, 0)

    node = SimpleNamespace(peer_id=bytes(20), address=("127.0.0.1", 6881),
                           store=SimpleNamespace(complete=True, left=0),
                           wait_complete=asyncio.sleep)
    task = asyncio.create_task(serve_discovered(node, Tracker(), 30))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert calls == ["started", "stopped"]


async def test_discovery_refresh_recovers_after_tracker_error():
    refreshed = asyncio.Event()
    calls = []

    class Tracker:
        announce_interval = 0.01

        async def announce(self, peer, *, left, event):
            calls.append(event)
            if len(calls) == 1:
                raise TrackerError("tracker starting")
            if event == "":
                refreshed.set()
            return AnnounceResult((), 1, 0)

    node = SimpleNamespace(peer_id=bytes(20), address=("127.0.0.1", 6881),
                           store=SimpleNamespace(complete=True, left=0),
                           wait_complete=asyncio.sleep)
    task = asyncio.create_task(serve_discovered(node, Tracker(), 0))
    try:
        await asyncio.wait_for(refreshed.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == ["started", "started", "", "stopped"]


@pytest.mark.parametrize("transport,loss", [("tcp", 0), ("murmurtp", 0), ("murmurtp", 0.05)])
async def test_ten_leechers_discover_and_verify(tmp_path, transport, loss, monkeypatch):
    # Exercise several tracker refreshes during a short local transfer.
    monkeypatch.setattr("tracker.client.TrackerClient.announce_interval", 1)
    config = tmp_path / "config.json"
    settings = load_config(overrides={"transport": transport})
    if loss:
        settings.update(loss_shim={"loss": loss}, murmurtp={"initial_rto": .1, "min_rto": .04,
                        "max_rto": .5}, piece_size=32 * 1024)
    config.write_text(json.dumps(settings), encoding="utf-8")
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    peers = {name: f"127.0.0.{i}" for i, name in enumerate(run.lab_peers(), 1)}
    size = 4 * settings["piece_size"] + 37
    metadata = tmp_path / "metadata.json"
    seed = tmp_path / "seed" / "payload.bin"
    tasks = []
    log = RunLog(tmp_path)
    try:
        generate_dataset(seed, metadata, size=size, piece_length=settings["piece_size"])
        for name, ip in peers.items():
            args = argparse.Namespace(command="swarm", metadata=metadata, config=config,
                                      distribution_mode="swarm", peer=[], origin_seed=None,
                                      mode="seed" if name == "seed" else "create",
                                      tracker_url=f"http://127.0.0.1:{server.server_port}/announce",
                                      port=0, host=ip, file=tmp_path / name / "payload.bin",
                                      run_dir=tmp_path, io_timeout=10, total_timeout=60)
            tasks.append(asyncio.create_task(_run_cli(args)))
        async def finished():
            while True:
                for task in tasks:
                    if task.done():
                        task.result()
                        pytest.fail("peer exited before the swarm finished")
                # File scans and reads can block for hundreds of milliseconds
                # on Windows. Keep monitoring off the peers' UDP event loop.
                await asyncio.to_thread(log.refresh)
                completed = {r["node"] for r in log.rows if r.get("event") == "completed"}
                if completed == set(peers.values()) - {peers["seed"]}:
                    return
                await asyncio.sleep(0.05)
        await asyncio.wait_for(finished(), 65)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.shutdown()
        server.server_close()
        worker.join()
    expected_hash = run.sha256(seed)
    summaries = run.verify(tmp_path, peers, size, expected_hash, log, transport)
    assert len(summaries) == 11
    if loss:
        assert sum(summary["retx"] for summary in summaries.values()) > 0
    assert not app.extensions["murmur_tracker"]._peers
    assert {row["node"] for row in log.rows if row.get("event") == "started"} == set(peers.values())
    with (tmp_path / "b5" / "payload.bin").open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(ValueError, match="b5: file differs"):
        run.verify(tmp_path, peers, size, expected_hash, log, transport)


def test_wait_reports_missing_peer_and_child_exit(tmp_path):
    log = RunLog(tmp_path)
    with pytest.raises(TimeoutError, match="10.0.2.11"):
        run.wait_for_downloads(log, {}, {"10.0.2.11"}, 0)
    class Failed:
        returncode = 3

        def poll(self):
            return self.returncode
    with pytest.raises(RuntimeError, match=r"a1 exited with 3; see a1.log"):
        run.wait_for_downloads(log, {"a1": Failed()}, {"10.0.1.11"}, 30)


@pytest.mark.skipif(sys.platform != "linux", reason="runner uses Linux process signals")
@pytest.mark.parametrize("transport", ["tcp", "murmurtp"])
def test_runner_supervises_real_subprocesses_on_local_sockets(tmp_path, transport):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = load_config(overrides={"tracker_url": f"http://127.0.0.1:{port}/announce",
                                    "tracker_port": port, "transport": transport,
                                    "peer_port_tcp": 0, "peer_port_udp": 0})
    run.run_swarm(tmp_path, 256 * 1024 + 7, 60,
                  {"seed": "127.0.0.2", "a1": "127.0.0.3", "b1": "127.0.0.4"},
                  "127.0.0.1", config, lambda name: [])
