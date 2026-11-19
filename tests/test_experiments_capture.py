"""Capture supervision and acceptance checks without a privileged VM."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from experiments import run


@pytest.fixture
def tcpdump(monkeypatch):
    state = SimpleNamespace(ready=True, returncode=None, saved="2 packets", read_error="",
                            stopped=False, read=False)
    state.poll = lambda: state.returncode

    def launch(args, *, cwd, stdout, stderr, start_new_session):
        assert args == ["bash", "infra/capture.sh", "6881"]
        assert start_new_session
        if state.ready:
            stderr.write(b"tcpdump: listening on wan0, link-type EN10MB\n")
            stderr.flush()
        state.log = stderr
        return state

    def stop(processes):
        assert processes == {"capture": state}
        state.log.write(b"2 packets captured\n2 packets received by filter\n")
        state.stopped = True
        if state.returncode is None:
            state.returncode = 0

    def read(args, *, stdin, capture_output, text):
        assert state.stopped and stdin.name.endswith("r1-wan0.pcap")
        assert args[-1] == "tcp port 6881"
        state.read = True
        return SimpleNamespace(returncode=int(bool(state.read_error)),
                               stdout=state.saved, stderr=state.read_error)

    monkeypatch.setattr(run.subprocess, "Popen", launch)
    monkeypatch.setattr(run.subprocess, "run", read)
    monkeypatch.setattr(run, "stop", stop)
    return state


@pytest.mark.parametrize("saved, read_error, error", [
    ("2 packets", "", None), ("1 packet", "", "counts disagree"),
    ("0 packets", "", "counts disagree"), ("", "truncated dump", "truncated dump"),
])
def test_checks_closed_file_against_live_count(tmp_path, tcpdump, saved, read_error, error):
    tcpdump.saved, tcpdump.read_error = saved, read_error
    with pytest.raises(ValueError, match=error) if error else nullcontext():
        with run.capture_wan(tmp_path, 6881):
            assert not tcpdump.stopped
    assert tcpdump.stopped and tcpdump.read


def test_capture_startup_failure_prevents_workload(tmp_path, tcpdump):
    tcpdump.ready = False
    tcpdump.returncode = 1
    with pytest.raises(RuntimeError, match="did not start"):
        with run.capture_wan(tmp_path, 6881):
            pytest.fail("workload started without capture")
    assert tcpdump.stopped and not tcpdump.read


def test_workload_interrupt_reaps_capture(tmp_path, tcpdump):
    with pytest.raises(KeyboardInterrupt):
        with run.capture_wan(tmp_path, 6881):
            raise KeyboardInterrupt
    assert tcpdump.stopped and not tcpdump.read


def test_early_capture_exit_fails_run(tmp_path, tcpdump):
    with pytest.raises(RuntimeError, match="exited before swarm shutdown"):
        with run.capture_wan(tmp_path, 6881):
            tcpdump.returncode = 0
    assert tcpdump.stopped and not tcpdump.read
