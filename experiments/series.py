"""Checkpoint verified trials of a single experiment configuration."""
from datetime import datetime, timezone
import json
import os
import subprocess
from tempfile import NamedTemporaryFile


SERIES_TRIALS = ["warmup", "recorded-1", "recorded-2", "recorded-3"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(directory, state):
    # The harness lock serializes writers. Replace only after the entire JSON
    # document is on disk, so a killed process never publishes half a checkpoint.
    with NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as temp:
        json.dump(state, temp, indent=2, allow_nan=False)
        temp.write("\n")
        temp.flush()
        os.fsync(temp.fileno())
    os.replace(temp.name, directory / "run.json")


def create(directory, config, timeout, capture, git_sha, *, series):
    directory.mkdir(parents=True, exist_ok=False)
    names = SERIES_TRIALS if series else ["recorded-1"]
    state = dict(version=1, series=series, git_sha=git_sha, config=config, timeout=timeout,
                 capture=capture, created=now(), status="pending", measurements=[],
                 trials=[dict(name=name, attempts=[]) for name in names])
    save(directory, state)
    return state


def load(directory):
    path = directory / "run.json"
    if not path.is_file():
        raise ValueError(f"No M3.3 checkpoint at {path}. Older recordings are read-only.")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError(f"Unsupported checkpoint in {path}")
    names = SERIES_TRIALS if state.get("series") else ["recorded-1"]
    if [trial.get("name") for trial in state.get("trials", [])] != names:
        raise ValueError(f"Invalid trial list in {path}")
    return state


def execute(directory, state, git_sha, run_trial, *, timeout=None):
    """Checkpoint after verification and teardown. Retry in a fresh directory."""
    deadline = state["timeout"] if timeout is None else timeout
    for trial in state["trials"]:
        name, attempts = trial["name"], trial["attempts"]
        if attempts and attempts[-1]["status"] == "passed":
            print(f"Skipping verified {name}", flush=True)
            continue
        if attempts and attempts[-1]["status"] == "running":
            attempts[-1].update(status="interrupted", error="Runner stopped before checkpoint")
        # Preserve the existing one-shot layout on its first attempt.
        relative = "." if not state["series"] and not attempts else f"{name}/attempt-{len(attempts)+1}"
        attempt = dict(directory=relative, git_sha=git_sha, timeout=deadline,
                       started=now(), status="running")
        attempts.append(attempt)
        state["status"] = "running"
        save(directory, state)
        output = directory / relative
        print(f"Starting {name} in {output}", flush=True)
        try:
            if output != directory:
                output.mkdir(parents=True, exist_ok=False)
            result = run_trial(output, state["config"], deadline, state["capture"])
        except (KeyboardInterrupt, Exception) as exc:
            if isinstance(exc, KeyboardInterrupt):
                status = "interrupted"
            elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
                status = "timed_out"
            else:
                status = "failed"
            attempt.update(status=status, ended=now(), error=str(exc) or status)
            state["status"] = status
            save(directory, state)
            raise
        attempt.update(status="passed", ended=now(), result=result)
        if name != "warmup":
            state["measurements"].append(relative)
        state["status"] = "complete" if name == state["trials"][-1]["name"] else "running"
        save(directory, state)
        print(f"Checkpointed {name}", flush=True)
