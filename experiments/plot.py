"""Render one experiment metric as a labeled PNG with sample-SD error bars."""

import argparse
import math
from pathlib import Path

from experiments.aggregate import CONDITIONS, METRICS, OUTPUT_FIELDS, conditions, number, read_csv

AXES = {"loss_pct": "Loss (%)", "rtt_ms": "Configured RTT (ms)", "leechers": "Leechers"}


def load_plot_rows(path, experiment, metric, x, subject="all"):
    if x not in AXES or metric not in METRICS:
        raise ValueError("invalid x axis or metric")
    result, seen = [], set()
    for line, raw in read_csv(path, OUTPUT_FIELDS):
        if raw["experiment"] != experiment or raw["metric"] != metric or raw["subject"] != subject:
            continue
        try:
            row = dict(zip(CONDITIONS, conditions(raw)))
            if raw["unit"] != METRICS[metric][0]:
                raise ValueError("metric unit does not match schema")
            runs = number(raw["runs"], "runs", integer=True, minimum=1)
            count = number(raw["n"], "n", integer=True, maximum=runs)
            missing = number(raw["missing"], "missing", integer=True)
            if count + missing != runs:
                raise ValueError("n + missing must equal runs")
            for key in ("mean", "min", "max"):
                row[key] = number(raw[key], key) if count else None
                if not count and raw[key].strip():
                    raise ValueError("empty group must have empty statistics")
            row["stddev"] = number(raw["stddev"], "stddev") if count > 1 else None
            if count <= 1 and raw["stddev"].strip():
                raise ValueError("sample SD is undefined for fewer than two observations")
            if count and not row["min"] <= row["mean"] <= row["max"]:
                raise ValueError("mean must lie between min and max")
            row["n"], row["runs"] = count, runs
            identity = tuple(row[key] for key in CONDITIONS)
            if identity in seen:
                raise ValueError("duplicate summary group")
            seen.add(identity)
            result.append(row)
        except ValueError as exc:
            raise ValueError(f"line {line}: {exc}") from exc
    if not result or not any(row["n"] for row in result):
        raise ValueError("no observed data for selected experiment/metric")
    fixed = [key for key in CONDITIONS if key not in ("variant", x)]
    if any(len({row[key] for row in result}) != 1 for key in fixed):
        raise ValueError("plot mixes data kinds or fixed conditions; provide one comparable experiment cell family")
    return sorted(result, key=lambda row: (row["variant"], row[x]))


def plot_summary(source, output, *, experiment="E1", metric="distribution_time_s", x="loss_pct", subject="all"):
    rows = load_plot_rows(source, experiment, metric, x, subject)
    output = Path(output)
    if output.suffix.lower() != ".png" or output.resolve() == Path(source).resolve():
        raise ValueError("output must be a separate .png file")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5.5), layout="constrained")
    try:
        for index, variant in enumerate(sorted({row["variant"] for row in rows})):
            series = [row for row in rows if row["variant"] == variant]
            color = f"C{index % 10}"
            by_x = {row[x]: row for row in series}
            x_values = sorted({row[x] for row in rows})
            label = variant if any(row["n"] for row in series) else variant + " (no observations)"
            ax.plot(x_values, [by_x[v]["mean"] if v in by_x and by_x[v]["n"] else math.nan for v in x_values],
                    marker="o", linewidth=2, label=label, color=color)
            for row in series:
                if row["stddev"] is not None:
                    ax.errorbar(row[x], row["mean"], yerr=row["stddev"], capsize=4, color=color)
                if row["n"]:
                    ax.annotate(f"n={row['n']}", (row[x], row["mean"]),
                                xytext=(5, 8 if index % 2 == 0 else -18),
                                textcoords="offset points", fontsize=8, color=color)
        title, unit = METRICS[metric][1], METRICS[metric][0]
        kind = rows[0]["data_kind"]
        fig.suptitle("MOCK DATA — NOT EXPERIMENT RESULTS" if kind == "mock" else "MEASURED DATA",
                     fontsize=12, fontweight="bold", color="#a12a20" if kind == "mock" else "#243847")
        ax.set(title=f"{experiment}: {title}", xlabel=AXES[x], ylabel=f"{title} ({unit})")
        ax.set_xticks(sorted({row[x] for row in rows}))
        ax.grid(axis="y", alpha=.25)
        ax.legend(frameon=False)
        ax.margins(y=.2)
        fixed = ", ".join(f"{AXES[key]}={rows[0][key]:g}" for key in AXES if key != x)
        fig.supxlabel(f"{fixed}; file={rows[0]['file_bytes']:,} bytes; subject={subject}\n"
                      "Points: mean; bars: sample SD (not confidence intervals). Missing values remain gaps.",
                      fontsize=9)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=160, metadata={"Title": f"{kind.upper()} DATA: {experiment} {title}",
                                             "Description": "Mean and sample standard deviation; not confidence intervals."})
    finally:
        plt.close(fig)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment", default="E1", choices=[f"E{i}" for i in range(1, 6)])
    parser.add_argument("--metric", default="distribution_time_s", choices=METRICS)
    parser.add_argument("--x", default="loss_pct", choices=AXES)
    parser.add_argument("--subject", default="all", help="all, or one peer/flow identifier")
    args = parser.parse_args()
    try:
        output = plot_summary(args.input, args.output, experiment=args.experiment, metric=args.metric,
                              x=args.x, subject=args.subject)
    except ImportError:
        parser.exit(1, "plot: Matplotlib is required in this Python environment; install project requirements.\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"plot: {exc}\n")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
