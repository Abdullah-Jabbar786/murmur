"""Receiver goodput and Jain fairness for two competing routed bulk flows."""
from bisect import bisect_left
import csv
import math

from experiments.e4 import ss_samples, verified_flow_logs
from experiments.series import load


PAIRINGS = ("murmurtp-tcp", "murmurtp-murmurtp", "tcp-tcp")


def receiver_curve(rows, size):
    """Include exact payload boundaries and only samples within them."""
    start, = [r["t"] for r in rows if r["type"] == "marker" and r.get("label") == "payload_start"]
    end, = [r["t"] for r in rows if r["type"] == "marker" and r.get("label") == "payload_end"]
    points = [(start, 0)]
    points.extend((r["t"], r["bytes_down"]) for r in rows if r["type"] == "rate" and start < r["t"] < end)
    points.append((end, size))
    for (t0, b0), (t1, b1) in zip(points, points[1:]):
        if not math.isfinite(t1 - t0) or t1 <= t0 or not 0 <= b0 <= b1 <= size:
            raise ValueError("invalid receiver counter or timestamp")
    return points


def bytes_at(curve, t):
    right = bisect_left(curve, (t,))
    t0, b0 = curve[right - 1]
    t1, b1 = curve[right]
    return b0 + (b1 - b0) * ((t - t0) / (t1 - t0))


def measure(directory, config, digest):
    size = config["file_size"]
    curves = []
    for i, transport in enumerate(config["pairing"].split("-"), 1):
        path = directory / f"flow-{i}"
        logs = verified_flow_logs(path, size, digest)
        for rows in logs.values():
            if [r.get("proto") for r in rows if r["type"] == "join"] != [transport]:
                raise ValueError(f"unexpected transport in {path}")
        if transport == "tcp" and not list(ss_samples((path / "ss.log").read_text(), "cubic")):
            raise ValueError(f"no sender CUBIC evidence in {path}")
        curves.append(receiver_curve(logs["receive"], size))
    overlap_start = max(curve[0][0] for curve in curves)
    overlap_end = min(curve[-1][0] for curve in curves)
    trim = (overlap_end - overlap_start) * 0.2
    start, end = overlap_start + trim, overlap_end - trim
    if end <= start:
        raise ValueError("receiver payload intervals did not overlap")
    flows = []
    for curve in curves:
        if sum(start <= t <= end for t, _ in curve) < 2:
            raise ValueError("too few overlapping receiver samples for steady-state fairness")
        before, after = bytes_at(curve, start), bytes_at(curve, end)
        goodput = (after - before) * 8 / (end - start) / 1e6
        flows.append(dict(bytes_start=before, bytes_end=after, goodput_mbps=goodput))
    x, y = (f["goodput_mbps"] for f in flows)
    if x + y == 0:
        raise ValueError("neither flow delivered payload in the measurement interval")
    with (directory / "samples.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(("flow", "t_s", "received_bytes"))
        for i, curve in enumerate(curves, 1):
            writer.writerows((i, t, b) for t, b in curve)
    return dict(file_bytes=size, sha256=digest, overlap_start_s=overlap_start, overlap_end_s=overlap_end,
                steady_start_s=start, steady_end_s=end, flows=flows,
                jain_fairness=(x + y) ** 2 / (2 * (x ** 2 + y ** 2)))


def report(directory, cells, summary):
    """Export the recorded counters and plot per-pair means and sample SD."""
    from matplotlib.figure import Figure

    with (directory / "receiver-samples.csv").open("w", newline="", encoding="utf-8") as dest:
        writer = csv.writer(dest)
        writer.writerow(("cell", "attempt", "flow", "t_s", "received_bytes"))
        for name in cells:
            for attempt in load(directory / name)["measurements"]:
                with (directory / name / attempt / "samples.csv").open(newline="", encoding="utf-8") as src:
                    for row in csv.DictReader(src):
                        writer.writerow((name, attempt, row["flow"], row["t_s"], row["received_bytes"]))
    lookup = {(r["variant"], r["subject"], r["metric"]): r for r in summary}
    fig = Figure(figsize=(10, 5), layout="constrained")
    fairness, rates = fig.subplots(1, 2)
    for axis, subject, metric, offset in [(fairness, "all", "jain_fairness", 0),
                                          (rates, "flow-1", "goodput_mbps", -.18),
                                          (rates, "flow-2", "goodput_mbps", .18)]:
        rows = [lookup[p, subject, metric] for p in PAIRINGS]
        axis.bar([i+offset for i in range(3)], [r["mean"] for r in rows],
                 yerr=[r["stddev"] for r in rows], width=.34, capsize=3, label=subject)
        axis.set_xticks(range(3), [p.replace("-", "\n+ ") for p in PAIRINGS])
    fairness.set(ylabel="Jain fairness index", ylim=(0, 1.06))
    rates.set_ylabel("Receiver goodput (Mbit/s)")
    rates.legend()
    fig.suptitle("E5 shared 10 Mbit/s WAN: recorded-trial means and sample SD")
    fig.savefig(directory / "E5-fairness.png", dpi=160)
