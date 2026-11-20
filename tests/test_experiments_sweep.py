import csv
import json

import pytest

from experiments import run, sweep


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


def test_sweep_resume_export_and_regeneration(tmp_path, monkeypatch):
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
        return {"verified_leechers": 10}

    args = (root, "E2-tcp-rtt100", 1000, 100, False, "sha", transfer, peers)
    with pytest.raises(TimeoutError):
        sweep.execute(*args)
    sweep.execute(*args)
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
    sweep.execute(*args)
    assert len(calls) == 5
    with pytest.raises(ValueError, match="settings"):
        sweep.execute(root, None, *args[2:])
    (calls[-1] / "events-10.0.1.11.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid final summary"):
        sweep.execute(*args)
