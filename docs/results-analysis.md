# A2.4: aggregate normalized metrics and plot mock results

`experiments/aggregate.py` validates per-run CSV metrics and produces a summary
table. `experiments/plot.py` renders one experiment/metric family to PNG using
Matplotlib's noninteractive Agg backend. `experiments/mock_results.csv` is a
synthetic acceptance fixture, not network evidence. Every fixture row and
summary group retains `data_kind=mock`; generated mock plots are prominently
labeled **MOCK DATA — NOT EXPERIMENT RESULTS**.

This implements A2.4's table-and-PNG acceptance. It does not run E1..E5, convert
raw JSONL, operate a dashboard or build the Ubuntu topology. The future harness
must export normalized metrics from verified runs into this schema. Flowbench
time-series plots and routed experiment analysis remain separate work.

## Run on this Windows laptop

The existing system Python has Matplotlib 3.10.9. The project virtual environment
has pytest but currently lacks Matplotlib. No package installation was performed
for this step. Use the existing plotting interpreter for the PNG subprocess:

```powershell
$env:MURMUR_PLOT_PYTHON = (Get-Command python).Source
$testTemp = Join-Path $env:TEMP ("murmur-tests-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m pytest tests/test_experiments_analysis.py -v -p no:cacheprovider --basetemp="$testTemp"
```

Expected: **30 passed**, including a real PNG rendering check. This environment
variable selects only the plotting interpreter used by the tests; it does not
change or install packages. On a machine where the project's requirements are
already installed in `.venv`, the variable is unnecessary: tests default to
their own Python interpreter. Matplotlib is now listed in `requirements.txt`
for fresh setups and CI.

Generate the sample artifacts:

```powershell
.\.venv\Scripts\python.exe -m experiments.aggregate experiments/mock_results.csv --output results/mock-analysis/summary.csv
python -m experiments.plot results/mock-analysis/summary.csv --output results/mock-analysis/distribution-time.png
```

The CSV has **30 summary rows**: two variants, three loss settings and five
metrics. Each cell contains three recorded runs; its warm-up is discarded.
The three TCP retransmission groups deliberately contain unknown values, so the
CLI reports fewer than three observed repetitions for those groups. Their mean
is blank, not zero. Expected distribution-time means/sample SD:

| Synthetic variant | 0% loss | 2% loss | 5% loss |
|---|---|---|---|
| tcp-swarm | 60 ± 2 s | 72 ± 2 s | 90 ± 2 s |
| murmurtp-swarm | 68 ± 2 s | 72 ± 2 s | 78 ± 2 s |

These numbers are invented to exercise analysis and chart layout. They must not
be quoted as project performance. Generated artifacts under `results/mock-analysis/`
are ignored by Git; source scripts and the mock fixture are committed instead.

## Input schema

UTF-8 CSV, one metric per run and subject, with these exact column names (order
may vary):

```text
run_id,experiment,variant,subject,loss_pct,rtt_ms,leechers,file_bytes,data_kind,seed,warmup,metric,value
```

| Column | Meaning |
|---|---|
| `run_id` | Globally unique run identifier; repeated across that run's metric/subject rows |
| `experiment` | E1, E2, E3, E4 or E5 |
| `variant` | Compared configuration, e.g. tcp-swarm, murmurtp-swarm or tcp-client-server |
| `subject` | `all` for run-level metrics, or one stable peer/flow identifier |
| `loss_pct`, `rtt_ms` | Configured loss percentage (0..100) and nonnegative RTT in milliseconds |
| `leechers`, `file_bytes` | Nonnegative integer peer count and positive integer file size |
| `data_kind` | Explicitly `mock` or `measured` |
| `seed` | Nonnegative integer random seed; repeats may use different seeds |
| `warmup` | Explicit `true` or `false` |
| `metric`, `value` | Metric below and finite nonnegative numeric value, or an empty value for unknown |

Duplicate run/subject/metric rows and inconsistent run metadata are errors.
Rows in different configurations, subjects or data kinds never aggregate
together. Repetitions with matched seeds across variants should be supplied by
the harness; the analyzer does not invent or automatically pair missing runs.

Supported metrics and units:

| Metric | Unit / exporter responsibility |
|---|---|
| `distribution_time_s` | Positive seconds from shared MURMUR_T0 to last leecher completion |
| `completion_time_s` | Positive seconds to a specified peer/flow completion |
| `goodput_mbps` | File payload goodput in decimal Mbit/s; use subject to identify the peer/flow |
| `retx` | Nonnegative integer packet retransmissions from summaries, or unknown |
| `wan_bytes` | Nonnegative integer interface-counter delta, or unknown |
| `seed_upload_share` | Seed payload upload / total payload upload, in [0,1] |
| `rtt_error_ms` | Absolute SRTT error against configured/known RTT, in milliseconds |
| `jain_fairness` | Jain index in [0,1], calculated over steady-state flow throughput |

These values are inputs, not inferred from arbitrary log fields. Do not derive
steady-state fairness from whole-file completion times. Mark failed or missing
measurements empty instead of pretending that they completed in zero seconds.
Run validity, synchronized clocks, warm-up classification, file verification and
counter extraction remain responsibilities of the exporter/harness.

## Output and statistics

The output preserves configuration and subject fields and adds `metric`, `unit`,
`runs`, `n`, `missing`, `mean`, `stddev`, `min`, `max`.

- Warm-up rows are validated but excluded from every statistic.
- `runs` counts recorded run IDs declared in that configuration, including runs
  declared only through another subject. `n` counts observed values for this metric.
- `missing = runs - n`; empty metric values, missing metric rows and missing peers
  in a declared run count as missing. A run completely absent from the entire CSV
  cannot be detected without the future harness's run inventory.
- Standard deviation uses the sample formula (n-1), not population SD.
- When n=1, SD is blank. When n=0, mean/SD/min/max are all blank.
- Unknown TCP metrics stay unknown. A missing group is never replaced with a
  zero-valued result. Groups with fewer than three observations are reported.

## Plot selection

Defaults are E1, distribution_time_s, subject=all, x=loss_pct. Alternatives:

```powershell
python -m experiments.plot results/mock-analysis/summary.csv --metric goodput_mbps --output results/mock-analysis/goodput.png
```

Use `--experiment E2 --x rtt_ms` or `--experiment E3 --x leechers` with compatible
input summaries. `--subject peer-id` selects one peer/flow. All conditions except
variant and the x-axis must match. Mixed mock/measured data or mixed fixed
conditions are rejected rather than merged into a misleading curve.

Points show means, bars show sample SD (not confidence intervals), and labels
show n. Missing values make gaps; all-missing selections are rejected. Figures
include configured conditions, file size, units and data-kind labels. The code
closes each figure after saving and requires an output path ending in `.png`.

## Validation scope

Tests cover known arithmetic, warm-up exclusion, missing and singleton metrics,
subject separation, absent peers, duplicate/inconsistent runs, numeric bounds,
invalid headers, incompatible plot groups, CLI failures and actual PNG output.
The sample PNG was also rendered and visually inspected. Python 3.10 syntax
checks cover all four new Python files. No real experiment results are claimed.

Windows verification on 18 November 2026: all 30 analysis tests passed with
warnings treated as errors. The broader suite returned 551 passed, 2 skipped
and 2 failures: the existing 64 MiB Stage 3 transfer and MurmurTP client-server
whole-swarm acceptance exceeded their deadlines. No transport code or timeout
thresholds were changed in this step. A full green regression is still required
before merging; these results do not establish a clean full-suite run.
Both failures reproduced in a two-test rerun (183.33s): the Stage 3 transfer hit
120s and the client-server swarm hit 60s, with connection inactivity errors.
Their underlying cause has not been established in this analysis task.

Follow-up: see [MurmurTP integration](murmurtp-integration.md#idle-peer-timeout-follow-up-18-november-2026)
for the idle-peer keepalive fix and subsequent successful runs of both original
acceptance cases. The earlier failed runs above remain part of the test record.
The follow-up full Windows suite passed: **559 passed, 2 Linux TCP skips** in
197.16s with warnings treated as errors. No analysis test or transport acceptance
deadline was weakened.
