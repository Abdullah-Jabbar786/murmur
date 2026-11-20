"""Run and report the M4.1 routed swarm experiments under the harness lock."""
import csv
import json
import math
from pathlib import Path
from uuid import uuid4

from core.config import load_config
from experiments import series
from experiments.aggregate import INPUT_FIELDS, aggregate, write_summary
from experiments.plot import plot_summary


CELLS = {}
for protocol in ("tcp", "murmurtp"):
    for loss in (0, 2, 5):
        CELLS[f"E1-{protocol}-loss{loss}"] = ("E1", protocol, loss, 20)
    for rtt in (20, 100, 200):
        CELLS[f"E2-{protocol}-rtt{rtt}"] = ("E2", protocol, 0, rtt)


def trial_metrics(directory, peers, size):
    """Read the final summary and common-clock completion from each verified peer."""
    times, retransmissions = {}, []
    for name, address in peers.items():
        summaries, completions = [], []
        with (directory / f"events-{address}.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["node"] != address:
                    raise ValueError(f"unexpected node in {directory}: {row['node']}")
                if row["type"] == "summary":
                    summaries.append(row)
                if row["type"] == "complete" and row.get("initial") is False:
                    completions.append(row["t"])
        if (len(summaries) != 1 or summaries[0]["complete"] is not True
                or summaries[0]["hash_fails"] != 0
                or summaries[0]["bytes_down"] != (0 if name == "seed" else size)):
            raise ValueError(f"{directory}: invalid final summary for {address}")
        retx = summaries[0]["retx"]
        if retx is not None and (type(retx) is not int or retx < 0):
            raise ValueError(f"{directory}: invalid retransmission count for {address}")
        retransmissions.append(retx)
        if name != "seed":
            if len(completions) != 1 or not math.isfinite(completions[0]) or completions[0] <= 0:
                raise ValueError(f"{directory}: invalid completion time for {address}")
            times[address] = completions[0]
    for address, seconds in times.items():
        yield address, "completion_time_s", seconds
        yield address, "goodput_mbps", 8 * size / seconds / 1e6
    last = max(times.values())
    yield "all", "distribution_time_s", last
    yield "all", "goodput_mbps", len(times) * 8 * size / last / 1e6
    yield "all", "retx", None if None in retransmissions else sum(retransmissions)


def report(directory, cells, peers, sweep_id):
    rows = []
    for cell in cells:
        experiment, transport, loss, rtt = CELLS[cell]
        state = series.load(directory / cell)
        size = state["config"]["file_size"]
        for trial in state["trials"]:
            attempt = trial["attempts"][-1]
            if attempt["status"] != "passed" or attempt["result"]["verified_leechers"] != len(peers) - 1:
                raise ValueError(f"{cell}/{trial['name']}: missing verified trial")
            path = directory / cell / attempt["directory"]
            for subject, metric, value in trial_metrics(path, peers, size):
                rows.append(dict(run_id=f"{sweep_id}/{cell}/{attempt['directory']}",
                                 experiment=experiment, variant=f"{transport}-swarm", subject=subject,
                                 loss_pct=loss, rtt_ms=rtt, leechers=len(peers)-1, file_bytes=size,
                                 data_kind="measured", seed=state["config"]["seed"],
                                 warmup=str(trial["name"] == "warmup").lower(),
                                 metric=metric, value=value))
    raw = directory / "metrics.csv"
    with raw.with_suffix(".tmp").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    raw.with_suffix(".tmp").replace(raw)
    summary = directory / "summary.csv"
    stats = aggregate(raw)
    write_summary(stats, summary)
    for experiment in sorted({CELLS[c][0] for c in cells}):
        x = "loss_pct" if experiment == "E1" else "rtt_ms"
        plots = [("distribution_time_s", "all")]
        if experiment == "E1":
            plots.append(("goodput_mbps", "all"))
            if any(r["experiment"] == "E1" and r["metric"] == "retx" and r["n"] for r in stats):
                plots.append(("retx", "all"))
        else:
            plots.extend(("goodput_mbps", ip) for name, ip in peers.items() if name != "seed")
        for metric, subject in plots:
            plot_summary(summary, directory / f"{experiment}-{metric}-{subject}.png",
                         experiment=experiment, metric=metric, x=x, subject=subject)
    print(f"Saved {raw}, {summary} and plots in {directory}", flush=True)


def execute(directory: Path, cell, size, timeout, capture, git_sha, run_trial, peers):
    cells = [cell] if cell else list(CELLS)
    settings = dict(version="M4.1", cells=cells, file_size=size, capture=capture, git_sha=git_sha)
    if directory.exists():
        marker = directory / "run.json"
        if not marker.is_file():
            raise ValueError(f"expected a sweep checkpoint at {marker}; use a fresh directory")
        state = json.loads(marker.read_text(encoding="utf-8"))
        if any(state.get(k) != v for k, v in settings.items()) or not state.get("sweep_id"):
            raise ValueError("sweep settings or Git revision differ; use the original command or a new directory")
    else:
        directory.mkdir(parents=True)
        state = dict(settings, sweep_id=uuid4().hex)
        series.save(directory, state)
    for name in cells:
        _, protocol, loss, rtt = CELLS[name]
        config = load_config(overrides=dict(mode="swarm", transport=protocol, file_size=size,
                                            wan_loss_pct=loss, wan_rtt_ms=rtt, seed=0))
        path = directory / name
        print(f"Cell {name} in {path}", flush=True)
        if path.exists():
            checkpoint = series.load(path)
            if (not checkpoint["series"] or checkpoint["git_sha"] != git_sha
                    or checkpoint["capture"] != capture or checkpoint["config"] != config):
                raise ValueError(f"unexpected cell configuration in {path}")
        else:
            checkpoint = series.create(path, config, timeout, capture, git_sha, series=True)
        series.execute(path, checkpoint, git_sha, run_trial, timeout=timeout)
    report(directory, cells, peers, state["sweep_id"])
