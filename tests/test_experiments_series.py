"""M3.3 checkpoint recovery; routed transfers have separate Ubuntu acceptance."""
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from experiments import run, series
from experiments.run import ROOT


@pytest.mark.parametrize("failure,status", [(KeyboardInterrupt(), "interrupted"),
                                             (TimeoutError("deadline"), "timed_out"),
                                             (subprocess.TimeoutExpired("tc", 30), "timed_out"),
                                             (ValueError("hash mismatch"), "failed")])
def test_resume_retries_only_unverified_trial(tmp_path, failure, status):
    directory = tmp_path / "experiment"
    config = {"transport": "tcp", "file_size": 1048576}
    state = series.create(directory, config, 60, True, "revision-one", series=True)
    calls = []

    def transfer(path, settings, timeout, capture):
        assert settings == config and capture
        calls.append((path.relative_to(directory).as_posix(), timeout))
        with (path / "work.log").open("x") as log:
            log.write("trial started")
        if len(calls) == 3:
            raise failure
        return {"verified_leechers": 10}

    with pytest.raises(type(failure)):
        series.execute(directory, state, "revision-one", transfer)
    recovered = series.load(directory)
    assert recovered["status"] == status
    assert recovered["measurements"] == ["recorded-1/attempt-1"]
    series.execute(directory, recovered, "revision-two", transfer, timeout=120)
    state = series.load(directory)
    assert state["status"] == "complete"
    assert state["measurements"] == ["recorded-1/attempt-1", "recorded-2/attempt-2",
                                     "recorded-3/attempt-1"]
    assert calls == [("warmup/attempt-1", 60), ("recorded-1/attempt-1", 60),
                     ("recorded-2/attempt-1", 60), ("recorded-2/attempt-2", 120),
                     ("recorded-3/attempt-1", 120)]
    assert state["trials"][2]["attempts"][0]["status"] == status
    assert state["trials"][2]["attempts"][1]["git_sha"] == "revision-two"
    series.execute(directory, state, "revision-two", transfer)
    assert len(calls) == 5


def test_one_shot_preserves_layout_and_older_runs(tmp_path):
    directory = tmp_path / "single"
    state = series.create(directory, {}, 42, False, "revision", series=False)
    called = []
    series.execute(directory, state, "revision", lambda path, *args: called.append(path))
    assert called == [directory] and state["measurements"] == ["."]
    with pytest.raises(FileExistsError):
        series.create(directory, {}, 42, False, "revision", series=False)
    with pytest.raises(ValueError, match="No M3.3 checkpoint"):
        series.load(tmp_path)


def test_routed_timeout_tears_down_before_checkpoint(tmp_path, monkeypatch):
    directory = tmp_path / "single"
    config = {"transport": "tcp", "wan_loss_pct": 0, "file_size": 8 * 1024**2}
    state = series.create(directory, config, 2, False, "revision", series=False)
    commands = []

    def lab(args, timeout):
        assert timeout > 0
        assert series.load(directory)["status"] == "running"
        commands.append(args[1])

    def stall(path, size, timeout, peers, tracker_ip, settings, prefix, *, capture):
        assert path == directory and timeout == 2 and settings == config
        raise TimeoutError("Timed out waiting for 10.0.2.11")

    monkeypatch.setattr(run, "lab_command", lab)
    monkeypatch.setattr(run, "run_swarm", stall)
    with pytest.raises(TimeoutError):
        series.execute(directory, state, "revision", run.routed_trial)
    assert commands == ["infra/topology.sh", "infra/netem.sh", "infra/teardown.sh"]
    assert series.load(directory)["status"] == "timed_out"
    assert not state["measurements"]


def test_interruption_during_shutdown_still_reaps_all_peers(monkeypatch):
    # Supply the POSIX signal primitives on a Windows test host too.
    kill_group = Mock()
    monkeypatch.setattr(run.os, "killpg", kill_group, raising=False)
    monkeypatch.setattr(run.signal, "SIGKILL", 9, raising=False)
    first, second = Mock(pid=100), Mock(pid=101)
    first.poll.return_value = second.poll.return_value = None
    first.wait.side_effect = [KeyboardInterrupt(), 0]
    with pytest.raises(KeyboardInterrupt):
        run.stop({"a1": first, "b1": second})
    assert [call.args for call in kill_group.call_args_list][-2:] == [(100, 9), (101, 9)]
    second.wait.assert_called_once_with()


def test_kill_and_restart(tmp_path):
    # On Linux run the same parent-death and cleanup path as the real CLI.
    # On Windows an uncatchable kill leaves the last atomic checkpoint running.
    directory = tmp_path / "killed"
    series.create(directory, {}, 60, False, "revision", series=True)
    script = """
import signal, sys, time
from pathlib import Path
from experiments import series
from experiments.run import supervise_linux_runner
directory = Path(sys.argv[1])
def interrupt(*args):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, interrupt)
if sys.platform == "linux":
    supervise_linux_runner()
def transfer(path, *args):
    if path.parent.name == "recorded-1":
        (directory / "ready").touch()
        try:
            time.sleep(60)
        finally:
            (directory / "cleaned").touch()
series.execute(directory, series.load(directory), "revision", transfer)
"""
    proc = subprocess.Popen([sys.executable, "-c", script, str(directory)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 15
        while not (directory / "ready").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(.025)
    finally:
        proc.kill()
        proc.wait(timeout=5)
    if sys.platform == "linux":
        deadline = time.monotonic() + 10
        while series.load(directory)["status"] != "interrupted":
            assert time.monotonic() < deadline
            time.sleep(.025)
        assert (directory / "cleaned").exists()
    finished = []
    series.execute(directory, series.load(directory), "revision",
                   lambda path, *args: finished.append(path.parent.name))
    assert finished == ["recorded-1", "recorded-2", "recorded-3"]
    state = series.load(directory)
    assert state["trials"][1]["attempts"][0]["status"] == "interrupted"
    assert state["measurements"][0] == "recorded-1/attempt-2"
