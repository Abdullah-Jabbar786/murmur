"""E4 measurements and sweep acceptance with deterministic local fixtures."""
import csv
import json
from contextlib import contextmanager

import pytest

from experiments import e4, run, sweep


def fixture_trial(path, config, timeout, capture):
    proto = config["transport"]
    algorithm = config.get("tcp_congestion_control")
    if proto == "tcp":
        assert algorithm in ("reno", "cubic")
        (path / "ss.log").write_text(
            f"t=0.0\nt=1.5\n  {algorithm} rtt:24.5/2.0 mss:1400 cwnd:12\n"
            f"t=4\n  {algorithm} rtt:25/2 mss:1400 cwnd:8\n")
    for side, role in (("send", "sender"), ("receive", "receiver")):
        size = config["file_size"]
        result = dict(role=role, verified=True, sha256="digest", bytes=size, seconds=4)
        events = [dict(type="transport", proto=proto, t=t, cwnd_bytes=10000, rtt_ms=24.5)
                  for t in (0, 1.5, 4, 7)]
        events += [dict(type="complete", t=5), dict(type="summary", complete=True, hash_fails=0,
                   bytes_up=size if side == "send" else 0, bytes_down=size if side == "receive" else 0,
                   result=result)]
        (path / f"events-flow-{side}.jsonl").write_text("".join(json.dumps(r)+"\n" for r in events))
    (path / "ping.log").write_text("[101.6] 64 bytes from 10.0.2.11: icmp_seq=2 ttl=62 time=21.5 ms\n")
    return e4.measure(path, config, "digest", 100)


@pytest.mark.parametrize("variant", e4.VARIANTS)
def test_measure_pairing_units_and_verification(tmp_path, variant):
    config = dict(file_size=1000, transport="murmurtp" if variant == "murmurtp" else "tcp",
                  tcp_congestion_control=variant)
    result = fixture_trial(tmp_path, config, 60, False)
    assert result["samples"] == 2 and result["paired_samples"] == 1
    assert result["rtt_error_ms"] == 3 and result["goodput_mbps"] == .002
    with (tmp_path / "trace.csv").open() as stream:
        first, last = list(csv.DictReader(stream))
    assert float(first["time_s"]) == .5 and last["rtt_error_ms"] == ""
    assert int(first["cwnd_bytes"]) == (10000 if variant == "murmurtp" else 16800)
    with pytest.raises(ValueError, match="unverified"):
        e4.measure(tmp_path, config, "other digest", 100)
    (tmp_path / "ping.log").write_text("")
    with pytest.raises(ValueError, match="paired"):
        e4.measure(tmp_path, config, "digest", 100)


def test_ss_wrong_algorithm():
    with pytest.raises(ValueError, match="confirm reno"):
        list(e4.ss_samples("t=1\n cubic rtt:20/2 mss:1460 cwnd:6\n", "reno"))


def test_matrix_report_and_resume(tmp_path, monkeypatch):
    plots = []
    monkeypatch.setattr(sweep, "plot_summary", lambda *a, **k: plots.append(a[1]))
    monkeypatch.setattr(e4, "plot_traces", lambda directory, cells: plots.append(len(cells)))
    output = tmp_path / "full"
    args = (output, None, 1000, 60, False, "sha")
    sweep.execute(*args, fixture_trial, {}, flow=True)
    assert json.loads((output / "E4-reno-loss0/run.json").read_text())["config"]["mode"] == "single_flow"
    with (output / "metrics.csv").open() as stream:
        raw = list(csv.DictReader(stream))
    assert len({r["run_id"] for r in raw if r["warmup"] == "false"}) == 36
    assert len({r["run_id"] for r in raw if r["warmup"] == "true"}) == 12
    with (output / "summary.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 24 and all(r["n"] == "3" and r["missing"] == "0" for r in rows)
    assert {r["variant"] for r in rows} == set(e4.VARIANTS)
    assert set(float(r["loss_pct"]) for r in rows) == set(e4.LOSSES)
    assert len(plots) == 3 and 12 in plots
    with (output / "timeseries.csv").open() as stream:
        traces = list(csv.DictReader(stream))
    assert len(traces) == 72
    assert all(r["attempt"].startswith("recorded-") for r in traces)
    sweep.execute(*args, lambda *a: pytest.fail("repeated verified trial"), {}, flow=True)
    with pytest.raises(ValueError, match="settings"):
        sweep.execute(*args, fixture_trial, {}, flow=True, flow_loss=0)


def test_render_pilot_figures(tmp_path):
    pytest.importorskip("matplotlib")
    path = tmp_path / "pilot"
    sweep.execute(path, None, 1000, 60, False, "sha", fixture_trial, {}, flow=True, flow_loss=0)
    assert {p.name for p in path.glob("*.png")} == {
        "E4-goodput_mbps-all.png", "E4-rtt_error_ms-all.png", "E4-traces-loss0.png"}
    for image in path.glob("*.png"):
        assert image.read_bytes().startswith(bytes.fromhex("89504e470d0a1a0a"))


def test_routed_failure_tears_down(tmp_path, monkeypatch):
    commands = []
    def lab(args, timeout):
        commands.append(args)
        if "infra/netem.sh" in args:
            raise RuntimeError("failed to apply WAN profile")
    monkeypatch.setattr(run, "lab_command", lab)
    with pytest.raises(RuntimeError, match="WAN profile"):
        run.flow_trial(tmp_path, dict(file_size=100, seed=0, transport="tcp", wan_loss_pct=2), 60, False)
    assert commands[-1] == ["bash", "infra/teardown.sh"]


def test_flow_trial_orchestrates_one_routed_tcp_connection(tmp_path, monkeypatch):
    launched, commands, cleanup = {}, [], []
    sampled = False

    class Process:
        def __init__(self, name):
            self.name = name

        @property
        def returncode(self):
            return 0 if sampled and self.name != "ping" else None

        def poll(self):
            return self.returncode

    def launch(path, name, args, env):
        launched[name] = args
        if name == "receiver":
            (path / "receiver.log").write_bytes(b"Listening on 10.0.2.11:6881\n")
        return Process(name)

    def lab(args, timeout, stdout=None):
        nonlocal sampled
        commands.append(args)
        if stdout is not None:
            assert args[2:] == ["a1", "ss", "-Hnti", "state", "established", "dst", "10.0.2.11:6881"]
            sampled = True

    @contextmanager
    def capture(path, port, protocol):
        assert port == 6881 and protocol == "tcp"
        yield
        cleanup.append("capture")

    monkeypatch.setattr(run, "launch", launch)
    monkeypatch.setattr(run, "lab_command", lab)
    monkeypatch.setattr(run, "stop", lambda processes: cleanup.extend(processes))
    monkeypatch.setattr(run, "capture_wan", capture)
    monkeypatch.setattr(e4, "measure", lambda *args: dict(samples=1, goodput_mbps=1))
    config = dict(file_size=100, seed=0, transport="tcp", tcp_congestion_control="reno", wan_loss_pct=5)
    run.flow_trial(tmp_path, config, 5, True)
    assert commands[1][2:] == ["apply", "--delay", "10", "--loss", "5", "--rate", "10"]
    assert launched["receiver"][2] == "b1" and launched["sender"][2] == launched["ping"][2] == "a1"
    assert "--file" in launched["sender"] and "--file" not in launched["receiver"]
    assert cleanup == ["receiver", "ping", "sender", "capture"]
    assert commands[-1] == ["bash", "infra/teardown.sh"]
