"""Validate normalized per-run metrics and aggregate recorded repetitions."""

import argparse
import csv
import math
from pathlib import Path
import statistics


CONDITIONS = ("experiment", "variant", "subject", "loss_pct", "rtt_ms", "leechers", "file_bytes", "data_kind")
INPUT_FIELDS = ("run_id", *CONDITIONS, "seed", "warmup", "metric", "value")
OUTPUT_FIELDS = (*CONDITIONS, "metric", "unit", "runs", "n", "missing", "mean", "stddev", "min", "max")
METRICS = {
    "distribution_time_s": ("s", "Distribution time"),
    "completion_time_s": ("s", "Completion time"),
    "goodput_mbps": ("Mbit/s", "Goodput"),
    "retx": ("packets", "Retransmissions"),
    "wan_bytes": ("bytes", "WAN bytes"),
    "seed_upload_share": ("fraction", "Seed upload share"),
    "rtt_error_ms": ("ms", "RTT estimate error"),
    "jain_fairness": ("fraction", "Jain fairness"),
}


def number(value, label, *, integer=False, minimum=0, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if (not math.isfinite(result) or result < minimum
            or maximum is not None and result > maximum or integer and not result.is_integer()):
        raise ValueError(f"invalid {label}: {value!r}")
    return int(result) if integer else result


def conditions(row):
    experiment, variant, subject, kind = (row[key].strip() for key in ("experiment", "variant", "subject", "data_kind"))
    if experiment not in ("E1", "E2", "E3", "E4", "E5") or not variant or not subject:
        raise ValueError("experiment must be E1..E5; variant and subject must be nonempty")
    if kind not in ("mock", "measured"):
        raise ValueError("data_kind must explicitly be mock or measured")
    return (experiment, variant, subject, number(row["loss_pct"], "loss_pct", maximum=100),
            number(row["rtt_ms"], "rtt_ms"), number(row["leechers"], "leechers", integer=True),
            number(row["file_bytes"], "file_bytes", integer=True, minimum=1), kind)


def metric_value(metric, raw):
    if metric not in METRICS:
        raise ValueError(f"unknown metric: {metric!r}")
    if not raw.strip():
        return None
    value = number(raw, metric, integer=metric in ("retx", "wan_bytes"),
                   maximum=1 if metric in ("seed_upload_share", "jain_fairness") else None)
    if metric.endswith("time_s") and value == 0:
        raise ValueError(f"{metric} must be positive")
    return value


def read_csv(path, required):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("missing or duplicate CSV header")
        if set(reader.fieldnames) != set(required):
            raise ValueError("CSV columns must be: " + ",".join(required))
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {line}: wrong column count")
            yield line, row


def aggregate(path):
    cells, metadata, seen = {}, {}, set()
    for line, row in read_csv(path, INPUT_FIELDS):
        try:
            cell = conditions(row)
            run = row["run_id"].strip()
            if not run:
                raise ValueError("run_id must be nonempty")
            seed = number(row["seed"], "seed", integer=True)
            warmup = row["warmup"].strip().lower()
            if warmup not in ("true", "false"):
                raise ValueError("warmup must be true or false")
            identity = (tuple(value for key, value in zip(CONDITIONS, cell) if key != "subject"), seed, warmup)
            if run in metadata and metadata[run] != identity:
                raise ValueError(f"inconsistent metadata for run_id {run!r}")
            metadata[run] = identity
            metric = row["metric"].strip()
            value = metric_value(metric, row["value"])
            if (run, cell[2], metric) in seen:
                raise ValueError(f"duplicate metric {metric!r} for run {run!r}")
            seen.add((run, cell[2], metric))
            if warmup == "true":
                continue
            metrics = cells.setdefault(cell, {})
            metrics.setdefault(metric, {})[run] = value
        except ValueError as exc:
            raise ValueError(f"line {line}: {exc}") from exc
    if not cells:
        raise ValueError("no recorded runs (input empty or all warm-up)")
    result = []
    for cell, metrics in sorted(cells.items()):
        family = tuple(value for key, value in zip(CONDITIONS, cell) if key != "subject")
        # A missing peer/flow in one declared run is still a missing observation.
        runs = sum(identity[0] == family and identity[2] == "false" for identity in metadata.values())
        for metric, observations in sorted(metrics.items()):
            values = [v for v in observations.values() if v is not None]
            count = len(values)
            row = dict(zip(CONDITIONS, cell))
            row.update(metric=metric, unit=METRICS[metric][0], runs=runs, n=count,
                       missing=runs - count, mean=statistics.mean(values) if count else None,
                       stddev=statistics.stdev(values) if count > 1 else None,
                       min=min(values) if count else None, max=max(values) if count else None)
            result.append(row)
    return result


def write_summary(rows, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("output must differ from input")
        rows = aggregate(args.input)
        write_summary(rows, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"aggregate: {exc}\n")
    print(f"Wrote {len(rows)} summary rows to {args.output}")
    sparse = sum(row["n"] < 3 for row in rows)
    if sparse:
        print(f"{sparse} metric groups have fewer than 3 observed repetitions; inspect n/missing.")


if __name__ == "__main__":
    main()
