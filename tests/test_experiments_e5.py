"""Competing-flow measurement, synchronized launch and checkpointed reporting."""
import asyncio
import csv
import json
from contextlib import ExitStack, contextmanager

import pytest

from core.events import EventLog
from core.transports import create_transport
from experiments import e5, run, sweep
from murmurtp.flowbench import run_once


def fixture_trial(directory, config, timeout, capture):
    size = config["file_size"]
    for i, proto in enumerate(config["pairing"].split("-"), 1):
        path = directory / f"flow-{i}"
        path.mkdir()
        start, end, rate = (0, 10, 100) if i == 1 else (2, 20, 20)
        for side, role in (("send", "sender"), ("receive", "receiver")):
            rows = [dict(type="join", proto=proto)]
            if side == "receive":
                rows.append(dict(type="marker", label="payload_start", t=start))
                rows.extend(dict(type="rate", bytes_down=(t-start)*rate, t=t) for t in range(start+1, end))
                rows.append(dict(type="marker", label="payload_end", t=end))
            rows.append(dict(type="summary", complete=True, hash_fails=0, bytes_up=size, bytes_down=size,
                             result=dict(role=role, verified=True, bytes=size, sha256="digest")))
            (path / f"events-flow-{side}.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
        if proto == "tcp":
            (path / "ss.log").write_text("t=1\n cubic rtt:20/1 mss:1460 cwnd:10\n")
    return e5.measure(directory, config, "digest")


def test_common_interval_excludes_time_after_first_receiver_finishes(tmp_path):
    config = dict(file_size=1000, pairing="murmurtp-tcp")
    result = fixture_trial(tmp_path, config, 10, True)
    assert (result["overlap_start_s"], result["overlap_end_s"]) == (2, 10)
    assert (result["steady_start_s"], result["steady_end_s"]) == pytest.approx((3.6, 8.4))
    assert [f["goodput_mbps"] for f in result["flows"]] == pytest.approx([.0008, .00016])
    assert result["jain_fairness"] == pytest.approx(120**2 / (2*(100**2 + 20**2)))
    with pytest.raises(ValueError, match="unverified"):
        e5.measure(tmp_path, config, "other")
    (tmp_path / "flow-2/ss.log").write_text("t=1\n reno rtt:20/1 mss:1460 cwnd:10\n")
    with pytest.raises(ValueError, match="cubic"):
        e5.measure(tmp_path, config, "digest")


@pytest.mark.parametrize("change", ["starvation", "no-overlap", "undersampled", "decreasing"])
def test_receiver_measurement_edge_cases(tmp_path, change):
    config = dict(file_size=1000, pairing="tcp-tcp")
    fixture_trial(tmp_path, config, 10, True)
    path = tmp_path / "flow-2/events-flow-receive.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if change == "no-overlap" and "t" in row:
            row["t"] += 10
        if row["type"] == "rate":
            if change == "decreasing" and row["t"] == 8:
                row["bytes_down"] = 1
            if change == "starvation":
                row["bytes_down"] = 0
    if change == "undersampled":
        rows = [r for r in rows if r["type"] != "rate"]
    path.write_text("".join(json.dumps(r)+"\n" for r in rows))
    if change == "starvation":
        assert e5.measure(tmp_path, config, "digest")["jain_fairness"] == .5
    else:
        with pytest.raises(ValueError, match="overlap|counter"):
            e5.measure(tmp_path, config, "digest")


def test_sweep_reports_only_recorded_trials_and_resumes(tmp_path):
    pytest.importorskip("matplotlib")
    output = tmp_path / "sweep"
    args = (output, None, 1000, 10, True, "git-sha")
    sweep.execute(*args, fixture_trial, {}, fairness=True)
    with (output / "summary.csv").open() as stream:
        summary = list(csv.DictReader(stream))
    assert len(summary) == 9 and all(r["n"] == "3" and r["missing"] == "0" for r in summary)
    assert {r["variant"] for r in summary} == set(e5.PAIRINGS)
    with (output / "receiver-samples.csv").open() as stream:
        counters = list(csv.DictReader(stream))
    assert all(r["attempt"].startswith("recorded-") for r in counters)
    assert len({(r["cell"], r["attempt"], r["flow"]) for r in counters}) == 18
    assert (output / "E5-fairness.png").read_bytes().startswith(b"\x89PNG")
    sweep.execute(*args, lambda *a: pytest.fail("repeated a verified trial"), {}, fairness=True)


async def test_tcp_and_murmurtp_wait_for_same_payload_gate(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(bytes(range(251))*400)
    gate = asyncio.Event()
    tasks, ready_events, logs = [], [], []

    async def wait_for_gate(ready):
        ready.set()
        await gate.wait()

    with ExitStack() as stack:
        try:
            for proto in ("tcp", "murmurtp"):
                directory = tmp_path / proto
                log = stack.enter_context(EventLog("receive", str(directory)))
                logs.append(directory / "events-receive.jsonl")
                receiver, sender = (create_transport(dict(transport=proto)) for _ in range(2))
                listening = asyncio.get_running_loop().create_future()
                tasks.append(asyncio.create_task(run_once(receiver, ("127.0.0.1", 0), timeout=10,
                    event_log=log, on_listening=listening.set_result)))
                address = await asyncio.wait_for(listening, 3)
                ready = asyncio.Event()
                ready_events.append(ready)
                tasks.append(asyncio.create_task(run_once(sender, address, source=source, timeout=10,
                    before_payload=lambda event=ready: wait_for_gate(event))))
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready_events)), 5)
            assert all("payload_start" not in path.read_text() for path in logs)
            gate.set()
            for result in await asyncio.gather(*tasks):
                assert result["verified"] and result["bytes"] == source.stat().st_size
            for path in logs:
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                assert [r["label"] for r in rows if r["type"] == "marker"] == ["payload_start", "payload_end"]
                assert rows[-1]["bytes_down"] == source.stat().st_size
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("failed", [False, True])
def test_launcher_both_namespaces_captures_and_cleanup(tmp_path, monkeypatch, failed):
    launched, sampled, closed, commands = [], [], [], []
    gate = tmp_path / "start-payload"

    def launch(path, name, args, env):
        assert not gate.exists()
        launched.append((name, args[2], args[args.index("--port")+1], env["MURMUR_T0"]))
        log = "Listening on " if name == "receiver" else "Ready to send payload"
        (path / f"{name}.log").write_text("" if failed and name == "sender" else log)
        class Process:
            def poll(self):
                if failed and name == "sender":
                    return 1
                return 0 if len(sampled) == 2 else None
        return Process()

    def lab(args, timeout, stdout=None):
        commands.append(args)
        if stdout is not None:
            assert gate.exists()
            sampled.append((args[2], args[-1]))

    @contextmanager
    def capture(path, port, protocol):
        assert protocol == "tcp"
        try:
            yield
        finally:
            closed.append(port)

    monkeypatch.setattr(run, "launch", launch)
    monkeypatch.setattr(run, "lab_command", lab)
    monkeypatch.setattr(run, "stop", lambda processes: closed.append(len(processes)))
    monkeypatch.setattr(run, "capture_wan", capture)
    monkeypatch.setattr(e5, "measure", lambda *args: {"jain_fairness": 1})
    args = (tmp_path, dict(file_size=100, seed=0, pairing="tcp-tcp"), 5, True)
    if failed:
        with pytest.raises(RuntimeError, match="readiness"):
            run.fairness_trial(*args)
        assert not gate.exists()
    else:
        run.fairness_trial(*args)
        assert sampled == [("a1", "10.0.2.11:6901"), ("a2", "10.0.2.12:6902")]
    assert [r[:3] for r in launched] == [("receiver", "b1", "6901"), ("receiver", "b2", "6902"),
                                          ("sender", "a1", "6901"), ("sender", "a2", "6902")]
    assert len({r[3] for r in launched}) == 1
    assert closed == [4, 6902, 6901]
    assert commands[-1] == ["bash", "infra/teardown.sh"]
