import csv
import hashlib
import json
from pathlib import Path

import pytest

from experiments import payloads, run, series, sweep


def test_matrix_matches_plan():
    assert set(sweep.CELLS.values()) == {
        (exp, proto, loss, rtt)
        for proto in ("tcp", "murmurtp")
        for exp, loss, rtt in [("E1", 0, 20), ("E1", 2, 20), ("E1", 5, 20),
                               ("E2", 0, 20), ("E2", 0, 100), ("E2", 0, 200)]
    }


@pytest.mark.parametrize("config,delay", [({}, "10"), ({"wan_rtt_ms": 100}, "50"),
                                         ({"wan_rtt_ms": 200}, "100")])
def test_shaping_uses_half_target_rtt(tmp_path, monkeypatch, config, delay):
    commands = []
    monkeypatch.setattr(run, "lab_command", lambda args, timeout: commands.append(args))
    monkeypatch.setattr(run, "run_swarm", lambda *args, **kwargs: {})
    run.routed_trial(tmp_path, dict(config, file_size=32, transport="tcp", wan_loss_pct=0), 30, False)
    assert commands[1] == ["bash", "infra/netem.sh", "apply", "--loss", "0", "--delay", delay]


@pytest.mark.parametrize("compact", [False, True])
def test_sweep_resume_export_and_regeneration(tmp_path, monkeypatch, compact):
    peers = run.lab_peers()
    root = tmp_path / "sweep"
    calls, plots = [], []
    monkeypatch.setattr(sweep, "plot_summary", lambda path, output, **kwargs: plots.append(kwargs))

    def transfer(path, config, timeout, capture):
        calls.append(path)
        if len(calls) == 2:
            raise TimeoutError
        assert config["wan_rtt_ms"] == 100
        for i, (name, address) in enumerate(peers.items()):
            seed = name == "seed"
            events = [dict(type="summary", complete=True, bytes_down=0 if seed else 1000,
                           hash_fails=0, retx=None if seed else 4),
                      dict(type="complete", initial=seed, t=10+i)]
            (path / f"events-{address}.jsonl").write_text(
                "".join(json.dumps(dict(event, node=address))+"\n" for event in events), encoding="utf-8")
        return write_payloads(path, peers, b"x" * 1000)

    args = (root, "E2-tcp-rtt100", 1000, 100, False, "sha", transfer, peers)
    with pytest.raises(TimeoutError):
        sweep.execute(*args, keep_seed_only=compact)
    if not compact:
        marker = root / "run.json"
        legacy = json.loads(marker.read_text())
        del legacy["keep_seed_only"]
        marker.write_text(json.dumps(legacy))
        with pytest.raises(ValueError, match="settings"):
            sweep.execute(*args, keep_seed_only=True)
    sweep.execute(*args, keep_seed_only=compact)
    assert len(calls) == 5
    assert calls[2].name == "attempt-2"
    with (root / "summary.csv").open() as stream:
        rows = {(r["subject"], r["metric"]): r for r in csv.DictReader(stream)}
    assert float(rows["all", "distribution_time_s"]["mean"]) == 20
    assert rows["all", "retx"]["missing"] == "3"
    assert float(rows["10.0.1.11", "goodput_mbps"]["mean"]) == pytest.approx(.008 / 11)
    assert all(r["runs"] == "3" for r in rows.values())
    assert len(plots) == 11
    with (root / "metrics.csv").open() as stream:
        raw = list(csv.DictReader(stream))
    assert len({r["run_id"] for r in raw if r["warmup"] == "true"}) == 1
    assert any("recorded-1/attempt-2" in r["run_id"] for r in raw)
    assert len(list(calls[-1].rglob("payload.bin"))) == (1 if compact else 11)
    sweep.execute(*args, keep_seed_only=compact)
    assert len(calls) == 5
    with pytest.raises(ValueError, match="settings"):
        sweep.execute(root, None, *args[2:])
    (calls[-1] / "events-10.0.1.11.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid final summary"):
        sweep.execute(*args, keep_seed_only=compact)


def write_payloads(directory, peers, content=b"verified test payload"):
    for name in peers:
        path = directory / name / "payload.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    return dict(file_bytes=len(content), verified_leechers=len(peers)-1,
                sha256=hashlib.sha256(content).hexdigest())


def test_compact_sweep_interrupted_cleanup_reuses_verified_trial(tmp_path, monkeypatch):
    peers = run.lab_peers()
    calls = []
    def trial(directory, *args):
        calls.append(directory)
        (directory / "r1-wan0.pcap").write_bytes(b"capture")
        return write_payloads(directory, peers)

    monkeypatch.setattr(sweep, "report", lambda *args: None)
    args = (tmp_path / "pilot", "E2-tcp-rtt100", 21, 60, True, "sha", trial, peers)
    unlink = Path.unlink
    def interrupt(path):
        if path.parent.name == "a2":
            raise KeyboardInterrupt
        unlink(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(KeyboardInterrupt):
            sweep.execute(*args, keep_seed_only=True)
    attempt = series.load(calls[0].parents[1])["trials"][0]["attempts"][0]
    assert attempt["status"] == "passed" and "retained_payload" not in attempt
    assert not (calls[0] / "a1/payload.bin").exists()
    with pytest.raises(ValueError, match="settings"):
        sweep.execute(*args)
    sweep.execute(*args, keep_seed_only=True)
    sweep.execute(*args, keep_seed_only=True)
    assert len(calls) == 4
    for path in calls:
        assert list(path.rglob("payload.bin")) == [path / "seed/payload.bin"]
        assert (path / "r1-wan0.pcap").read_bytes() == b"capture"
    for trial_state in series.load(calls[0].parents[1])["trials"]:
        assert trial_state["attempts"][0]["retained_payload"] == "seed/payload.bin"


@pytest.mark.parametrize("changed", ["seed", "b5"])
def test_changed_file_preserves_all_remaining_payloads(tmp_path, changed):
    peers = run.lab_peers()
    result = write_payloads(tmp_path, peers)
    (tmp_path / changed / "payload.bin").write_bytes(b"x" * result["file_bytes"])
    with pytest.raises(ValueError, match="differs"):
        payloads.keep_seed(tmp_path, peers, result)
    assert len(list(tmp_path.rglob("payload.bin"))) == 11


def test_failed_compact_trial_preserves_payloads(tmp_path):
    peers = run.lab_peers()
    def failed(directory, *args):
        write_payloads(directory, peers)
        raise ValueError("capture failed")
    with pytest.raises(ValueError, match="capture failed"):
        sweep.execute(tmp_path / "failed", "E1-tcp-loss0", 21, 60, True, "sha",
                      failed, peers, keep_seed_only=True)
    assert len(list(tmp_path.rglob("payload.bin"))) == 11
