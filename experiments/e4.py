"""Measurements from one routed bulk flow and its independent RTT probes."""
from bisect import bisect_left
import csv
import json
import math
import re
import statistics


LOSSES = (0, 1, 2, 5)
VARIANTS = ("reno", "cubic", "murmurtp")
TRACE_FIELDS = ("time_s", "cwnd_bytes", "srtt_ms", "probe_rtt_ms", "rtt_error_ms")


def ss_samples(text, algorithm):
    """Read sender socket snapshots. Linux reports cwnd in segments."""
    for block in re.split(r"(?m)^t=", text)[1:]:
        stamp, _, details = block.partition("\n")
        if not details.strip():
            continue  # Before connect or after close.
        if not re.search(rf"\b{re.escape(algorithm)}\b", details):
            raise ValueError(f"ss did not confirm {algorithm} on the sender")
        mss = re.search(r"\bmss:(\d+)", details)
        cwnd = re.search(r"\bcwnd:(\d+)", details)
        rtt = re.search(r"\brtt:([\d.]+)/", details)
        if not (mss and cwnd and rtt):
            raise ValueError("missing mss, cwnd or rtt in sender ss.log")
        yield float(stamp), int(mss[1]) * int(cwnd[1]), float(rtt[1])


def measure(directory, config, digest, epoch):
    size = config["file_size"]
    logs = {}
    for side, role in (("send", "sender"), ("receive", "receiver")):
        path = directory / f"events-flow-{side}.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not rows or rows[-1].get("type") != "summary":
            raise ValueError(f"missing {role} summary")
        summary = rows[-1]
        result = summary.get("result") or {}
        counter = "bytes_up" if side == "send" else "bytes_down"
        if (not summary.get("complete") or summary.get("hash_fails") != 0 or summary[counter] != size
                or result.get("role") != role or result.get("verified") is not True
                or result.get("sha256") != digest or result.get("bytes") != size):
            raise ValueError(f"unverified {role} result")
        logs[side] = rows
    receiver = logs["receive"][-1]["result"]
    finish, = [row["t"] for row in logs["receive"] if row["type"] == "complete"]
    duration = receiver["seconds"]
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid receiver duration")
    start = finish - duration
    if config["transport"] == "tcp":
        samples = ss_samples((directory / "ss.log").read_text(), config["tcp_congestion_control"])
    else:
        samples = ((r["t"], r["cwnd_bytes"], r["rtt_ms"]) for r in logs["send"]
                   if r["type"] == "transport" and r.get("proto") == "murmurtp")
    ping = (directory / "ping.log").read_text()
    probes = sorted((float(t)-epoch, float(rtt)) for t, rtt in
                    re.findall(r"(?m)^\[([\d.]+)\].*?\btime=([\d.]+) ms", ping))
    stamps = [probe[0] for probe in probes]
    errors = []
    count = 0
    with (directory / "trace.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(TRACE_FIELDS)
        for t, cwnd, srtt in samples:
            if not start <= t <= finish or cwnd is None or srtt is None:
                continue
            if cwnd <= 0 or not math.isfinite(srtt) or srtt <= 0:
                continue
            index = bisect_left(stamps, t)
            nearby = probes[max(0, index-1):index+1]
            reference = error = None
            if nearby:
                when, value = min(nearby, key=lambda probe: abs(probe[0]-t))
                if abs(when-t) <= 1:
                    reference, error = value, abs(srtt-value)
                    errors.append(error)
            writer.writerow((t-start, cwnd, srtt, reference, error))
            count += 1
    if not errors:
        raise ValueError("no sender samples paired with a measured ping RTT during transfer; see trial logs")
    return dict(file_bytes=size, sha256=digest, goodput_mbps=size*8/duration/1e6,
                rtt_error_ms=statistics.mean(errors), samples=count, paired_samples=len(errors))


def plot_traces(directory, cells):
    """Keep a fixed trial selection for comparisons across all transports."""
    from matplotlib.figure import Figure
    from experiments import series

    for loss in sorted({int(cell.split("loss")[1]) for cell in cells}):
        fig = Figure(figsize=(9, 7), layout="constrained")
        window, error = fig.subplots(2, 1, sharex=True)
        for variant in VARIANTS:
            cell = directory / f"E4-{variant}-loss{loss}"
            attempt = series.load(cell)["measurements"][0]
            with (cell / attempt / "trace.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            t = [float(r["time_s"]) for r in rows]
            window.plot(t, [int(r["cwnd_bytes"])/1024 for r in rows], label=variant)
            error.plot(t, [float(r["rtt_error_ms"] or "nan") for r in rows], label=variant)
        fig.suptitle(f"E4 single flow at {loss}% WAN loss per direction (recorded trial 1)")
        window.set_ylabel("Congestion window (KiB)")
        error.set(xlabel="Seconds from receiver payload interval start", ylabel="Absolute RTT error (ms)")
        for axis in (window, error):
            axis.legend()
            axis.grid(axis="y", alpha=.25)
        fig.savefig(directory / f"E4-traces-loss{loss}.png", dpi=160)


def export_traces(directory, cells):
    """Collect every verified recorded trace in the portable results bundle."""
    from experiments import series

    output = directory / "timeseries.csv"
    temp = output.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf-8") as dest:
        writer = csv.writer(dest)
        writer.writerow(("cell", "attempt", *TRACE_FIELDS))
        for name in cells:
            cell = directory / name
            for attempt in series.load(cell)["measurements"]:
                with (cell / attempt / "trace.csv").open(newline="", encoding="utf-8") as source:
                    reader = csv.reader(source)
                    if next(reader, []) != list(TRACE_FIELDS):
                        raise ValueError(f"unexpected trace header in {cell / attempt}")
                    writer.writerows((name, attempt, *row) for row in reader)
    temp.replace(output)
