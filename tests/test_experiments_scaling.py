import csv
import json
import socket
import sys

import pytest

from core.config import load_config
from experiments import run, scaling, sweep


def test_e3_sizes_modes_and_placement():
    assert set(scaling.CELLS.values()) == {(mode, n) for mode in ("swarm", "client_server")
                                           for n in (2, 5, 10, 20)}
    for n in scaling.COUNTS:
        peers = scaling.lab_peers(n)
        assert len(peers) == len(set(peers.values())) == n + 1
        assert sum(name.startswith("a") for name in peers) == (n + 1) // 2
        assert sum(name.startswith("b") for name in peers) == n // 2


def test_e3_measurement_brackets_transfers_and_tears_down(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(run, "lab_command", lambda args, timeout: commands.append(args[1:]))
    def read(args, **kwargs):
        assert args == ["ip", "-n", "murmur-r1", "-j", "-s", "link", "show", "dev", "wan0"]
        commands.append("read")
        k = commands.count("read")
        return json.dumps([{"stats64": {"rx": {"bytes": 7*k}, "tx": {"bytes": 40*k}}}])
    monkeypatch.setattr(run.subprocess, "check_output", read)
    def transfer(directory, size, timeout, peers, tracker, config, prefix, *, capture):
        assert peers == scaling.lab_peers(5) and config["mode"] == "client_server"
        commands.append("transfer")
        return {"seed_upload_bytes": 80, "leecher_upload_bytes": 0}
    monkeypatch.setattr(run, "run_swarm", transfer)
    config = dict(file_size=16, transport="tcp", mode="client_server", wan_loss_pct=0, leechers=5)
    result = run.routed_trial(tmp_path, config, 60, True)
    assert commands[0] == ["infra/topology.sh", "--leechers-per-lan", "3"]
    assert commands[2:] == ["read", "transfer", "read", ["infra/teardown.sh"]]
    assert list(scaling.metrics(result, config["mode"])) == [
        ("all", "seed_upload_share", 1), ("all", "wan_bytes", 47)]
    result["leecher_upload_bytes"] = 1
    with pytest.raises(ValueError, match="upload"):
        list(scaling.metrics(result, "client_server"))
    with pytest.raises(ValueError, match="decreased"):
        scaling.wan_delta(result["wan_after"], result["wan_before"])


def test_e3_pilot_sweep_and_report(tmp_path, monkeypatch):
    plots = []
    monkeypatch.setattr(sweep, "plot_summary", lambda *args, **kwargs: plots.append(kwargs))
    path = tmp_path / "e3"
    def trial(directory, config, timeout, capture):
        assert config["leechers"] == 5 and config["transport"] == "tcp"
        server = config["mode"] == "client_server"
        for name, address in scaling.lab_peers(5).items():
            seed = name == "seed"
            events = [dict(type="complete", initial=seed, t=10),
                      dict(type="summary", complete=True, hash_fails=0, bytes_down=0 if seed else 16,
                           retx=2)]
            (directory / f"events-{address}.jsonl").write_text(
                "".join(json.dumps(dict(e, node=address)) + "\n" for e in events))
        return dict(verified_leechers=5, seed_upload_bytes=80 if server else 32,
                    leecher_upload_bytes=0 if server else 48, wan_before=dict(rx=0, tx=20),
                    wan_after=dict(rx=5, tx=115 if server else 145))
    args = (path, None, 16, 60, True, "sha", trial, scaling.lab_peers())
    sweep.execute(*args, e3=True, count=5)
    sweep.execute(*args[:6], lambda *a: pytest.fail("verified trial repeated"), args[-1], e3=True, count=5)
    with (path / "summary.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert all(r["runs"] == r["n"] == "3" and r["leechers"] == "5" for r in rows)
    assert {r["variant"]: float(r["mean"]) for r in rows if r["metric"] == "seed_upload_share"} == {
        "tcp-client-server": 1, "tcp-swarm": .4}
    with (path / "wan-savings.csv").open() as stream:
        saved, = csv.DictReader(stream)
    assert float(saved["saved_bytes"]) == -30 and float(saved["saved_pct"]) == -30
    assert len(plots) == 6 and all(p["x"] == "leechers" for p in plots)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX subprocess shutdown")
def test_direct_seed_e3_runner_on_local_tcp(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.2", 0))
        port = sock.getsockname()[1]
    config = load_config(overrides=dict(transport="tcp", mode="client_server", leechers=2,
                                        peer_port_tcp=port))
    size = 256 * 1024 + 17
    result = run.run_swarm(tmp_path, size, 60, {"seed": "127.0.0.2", "a1": "127.0.0.3",
                           "b1": "127.0.0.4"}, None, config, lambda name: [])
    assert result["verified_leechers"] == 2 and result["leecher_upload_bytes"] == 0
    assert result["seed_upload_bytes"] == 2 * size
