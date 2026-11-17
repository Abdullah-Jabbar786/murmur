"""Offline A2.4 statistics, missing-data rules and real PNG CLI generation."""

import csv
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from experiments.aggregate import INPUT_FIELDS, OUTPUT_FIELDS, aggregate, write_summary
from experiments.plot import load_plot_rows

ROOT = Path(__file__).resolve().parents[1]
PLOT_PYTHON = os.environ.get("MURMUR_PLOT_PYTHON", sys.executable)


def row(run="r1", value=10, **changes):
    result = dict(run_id=run, experiment="E1", variant="tcp-swarm", subject="all",
                  loss_pct=0, rtt_ms=20, leechers=10, file_bytes=67108864,
                  data_kind="mock", seed=42, warmup="false", metric="distribution_time_s", value=value)
    result.update(changes)
    return result


def csv_file(tmp_path, rows, fields=INPUT_FIELDS, name="input.csv"):
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_known_statistics_exclude_warmup_and_use_sample_sd(tmp_path):
    rows = aggregate(csv_file(tmp_path, [row("warm", 999, warmup="true"),
        row("r1", 10), row("r2", 20, seed=43), row("r3", 30, seed=44)]))
    assert len(rows) == 1
    result = rows[0]
    assert (result["mean"], result["stddev"], result["min"], result["max"]) == (20, 10, 10, 30)
    assert (result["runs"], result["n"], result["missing"]) == (3, 3, 0)


def test_missing_values_and_absent_metric_rows_are_not_zero(tmp_path):
    path = csv_file(tmp_path, [row("r1", "", metric="retx"), row("r2", 4, metric="retx"),
                               row("r3", 20)])
    by_metric = {r["metric"]: r for r in aggregate(path)}
    assert by_metric["retx"]["mean"] == 4 and by_metric["retx"]["n"] == 1
    assert by_metric["retx"]["missing"] == 2 and by_metric["retx"]["stddev"] is None
    output = tmp_path / "summary.csv"
    write_summary(list(by_metric.values()), output)
    with output.open(newline="", encoding="utf-8") as stream:
        saved = list(csv.DictReader(stream))
    assert all(item["stddev"] == "" for item in saved)


def test_all_missing_metric_preserved_in_table(tmp_path):
    result = aggregate(csv_file(tmp_path, [row("r1", "", metric="retx"), row("r2", "", metric="retx")]))[0]
    assert result["n"] == 0 and result["missing"] == 2
    assert all(result[key] is None for key in ("mean", "stddev", "min", "max"))


def test_peers_and_configurations_are_separate_groups(tmp_path):
    rows = [row("r1", 10, subject="peer1"), row("r1", 20, subject="peer2"),
            row("r2", 30, loss_pct=5), row("r3", 40, file_bytes=1024),
            row("r4", 50, data_kind="measured")]
    result = aggregate(csv_file(tmp_path, rows))
    assert len(result) == 5 and all(item["n"] == 1 for item in result)


def test_peer_missing_from_declared_run_counts_as_missing(tmp_path):
    rows = aggregate(csv_file(tmp_path, [row("r1", 10, subject="peer1"),
        row("r1", 20, subject="peer2"), row("r2", 12, subject="peer1")]))
    peer2 = next(item for item in rows if item["subject"] == "peer2")
    assert (peer2["runs"], peer2["n"], peer2["missing"]) == (2, 1, 1)


@pytest.mark.parametrize("changes", [dict(value="nan"), dict(value="inf"), dict(value=-1),
    dict(value=0), dict(metric="unknown"), dict(metric="jain_fairness", value=1.1),
    dict(metric="retx", value=1.5), dict(loss_pct=101), dict(rtt_ms=-1), dict(file_bytes=0),
    dict(leechers=2.5), dict(warmup="yes"), dict(data_kind=""), dict(experiment="E9"), dict(run_id="")])
def test_invalid_rows_have_line_numbers(tmp_path, changes):
    with pytest.raises(ValueError, match="line 2"):
        aggregate(csv_file(tmp_path, [row(**changes)]))


def test_duplicate_or_inconsistent_run_is_rejected(tmp_path):
    for rows in ([row(), row()], [row(), row(metric="retx", value=0, seed=9)]):
        with pytest.raises(ValueError, match="line 3"):
            aggregate(csv_file(tmp_path, rows))


def test_empty_warmup_only_and_duplicate_header_rejected(tmp_path):
    for rows in ([], [row(warmup="true")]):
        with pytest.raises(ValueError, match="no recorded"):
            aggregate(csv_file(tmp_path, rows))
    path = tmp_path / "bad.csv"
    path.write_text("run_id,run_id\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        aggregate(path)


def test_mock_fixture_has_three_recorded_runs_and_unknown_tcp_retx():
    rows = aggregate(ROOT / "experiments/mock_results.csv")
    assert len(rows) == 30 and all(item["runs"] == 3 and item["data_kind"] == "mock" for item in rows)
    times = [item for item in rows if item["metric"] == "distribution_time_s"]
    assert all(item["n"] == 3 and item["stddev"] == 2 for item in times)
    unknown = [item for item in rows if item["variant"] == "tcp-swarm" and item["metric"] == "retx"]
    assert len(unknown) == 3 and all(item["mean"] is None and item["n"] == 0 for item in unknown)


@pytest.mark.parametrize("changes", [dict(data_kind="measured"), dict(rtt_ms=100), dict(file_bytes=1024)])
def test_plot_refuses_incomparable_conditions(tmp_path, changes):
    data = aggregate(csv_file(tmp_path, [row(), row("r2", 12, variant="murmurtp-swarm", **changes)]))
    output = tmp_path / "summary.csv"
    write_summary(data, output)
    with pytest.raises(ValueError, match="mixes"):
        load_plot_rows(output, "E1", "distribution_time_s", "loss_pct")


def test_plot_rejects_invalid_summary_and_all_missing(tmp_path):
    summary = aggregate(csv_file(tmp_path, [row()]))
    summary[0]["n"] = 2
    path = csv_file(tmp_path, summary, OUTPUT_FIELDS, "summary.csv")
    with pytest.raises(ValueError):
        load_plot_rows(path, "E1", "distribution_time_s", "loss_pct")
    missing = aggregate(csv_file(tmp_path, [row(value="", metric="retx")]))
    write_summary(missing, path)
    with pytest.raises(ValueError, match="no observed"):
        load_plot_rows(path, "E1", "retx", "loss_pct")


def test_plot_subject_filter_keeps_peer_metrics_separate(tmp_path):
    summary = aggregate(csv_file(tmp_path, [row(subject="peer1"), row(subject="peer2", value=20)]))
    output = tmp_path / "summary.csv"
    write_summary(summary, output)
    rows = load_plot_rows(output, "E1", "distribution_time_s", "loss_pct", subject="peer2")
    assert len(rows) == 1 and rows[0]["mean"] == 20


def test_cli_table_and_real_png(tmp_path):
    summary, png = tmp_path / "summary.csv", tmp_path / "distribution.png"
    env = dict(os.environ, MPLCONFIGDIR=str(tmp_path / "mpl-cache"))
    command = [sys.executable, "-m", "experiments.aggregate", str(ROOT / "experiments/mock_results.csv"),
               "--output", str(summary)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "30 summary rows" in result.stdout
    result = subprocess.run([PLOT_PYTHON, "-m", "experiments.plot", str(summary), "--output", str(png)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    raw = png.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (1440, 880)
    assert b"MOCK DATA" in raw and b"sample standard deviation" in raw


def test_cli_error_does_not_overwrite_input_or_create_output(tmp_path):
    source = csv_file(tmp_path, [row(value="NaN")])
    original = source.read_bytes()
    output = tmp_path / "not-created.csv"
    result = subprocess.run([sys.executable, "-m", "experiments.aggregate", str(source), "--output", str(output)],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "line 2" in result.stderr and not output.exists()
    result = subprocess.run([sys.executable, "-m", "experiments.aggregate", str(source), "--output", str(source)],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and source.read_bytes() == original
