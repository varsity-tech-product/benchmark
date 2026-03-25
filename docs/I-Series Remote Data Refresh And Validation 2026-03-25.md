# I-Series Current Baseline And Legacy Archive — 2026-03-25

## Scope

This document describes the **current active I-series baseline** only.

It does not treat the older pre-refresh reference files as active benchmark inputs.
Those files have been moved into a legacy subdirectory so they do not confuse future reviews.

## Current Source Of Truth

### Remote dataset

- HuggingFace dataset: `Varsity-Tech/quant-tutor-bench-data`
- Current pinned revision: `67c0df0b9d85afa7c1a33e7ea5ed8be143bf3297`
- Canonical consumer path:
  - `I.tar.gz`

### Local code pin

- [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py)
  - `DATASET_REVISION = "67c0df0b9d85afa7c1a33e7ea5ed8be143bf3297"`

### Active benchmark reference directory

- [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference)

This is the directory the evaluation stack actually reads via:

- [implementation_check.py](/home/rick/Desktop/benchmark/bench/evaluation/test_scripts/common/implementation_check.py)

## Legacy Archive

The older pre-refresh active reference files were moved out of the way to:

- [legacy_2026-03-25_pre_remote_refresh](/home/rick/Desktop/benchmark/bench/data/reference/legacy_2026-03-25_pre_remote_refresh)

That folder currently contains `39` legacy I-series reference artifacts that should be treated as historical provenance only.

## Active Reference Inventory

The active top-level [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference) now contains:

- `I01-I07`
  - `*_reference_trades.json`
  - `*_reference_summary.json`
  - `*_reference_signals.json`
- `I06`
  - default `I06_reference_trades.json`
  - sweep result file
  - per-config trade files
- `I08`
  - comparison file
  - per-config trade files
  - summary + signals
- `I09`
  - comparison file
  - per-config trade files
  - summary + signals
- `I10`
  - grid result file
  - per-config trade files
  - summary + signals

## Current Active Baseline Results

### Single-run tasks

These are the current active trade references in [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference):

| Task | `generated_at` | `lean_image` | trade_count |
| --- | --- | --- | ---: |
| I01 | `2026-03-25T07:16:42.060227+00:00` | `quant-tutor-env:v2.2-lean` | 85 |
| I02 | `2026-03-25T07:17:08.925785+00:00` | `quant-tutor-env:v2.2-lean` | 1763 |
| I03 | `2026-03-25T07:16:52.690030+00:00` | `quant-tutor-env:v2.2-lean` | 662 |
| I04 | `2026-03-25T07:17:02.308076+00:00` | `quant-tutor-env:v2.2-lean` | 4026 |
| I05 | `2026-03-25T07:17:16.135631+00:00` | `quant-tutor-env:v2.2-lean` | 2340 |
| I06 | `2026-03-25T07:17:42.618675+00:00` | `quant-tutor-env:v2.2-lean` | 13719 |
| I07 | `2026-03-25T07:17:23.301186+00:00` | `quant-tutor-env:v2.2-lean` | 179 |

### Current summary metrics

These are the current active summary metrics in [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference):

| Task | total_return_pct | sharpe_ratio | total_trades |
| --- | ---: | ---: | ---: |
| I01 | 32.91 | 0.168 | 85 |
| I02 | 20980.082 | 0.6076 | 1763 |
| I03 | -102.952 | -0.335 | 662 |
| I04 | -17.194 | -0.07 | 4026 |
| I05 | -309669.019 | -0.6031 | 2340 |
| I06 | 27604.073 | 0.2776 | 13719 |
| I07 | -34.09 | -0.059 | 179 |
| I08 | 7.967 | 0.02 | 63 |
| I09 | 49.51 | 0.307 | 24797 |
| I10 | 148.564 | 0.553 | 4611 |

## Multi-run Task Outcomes

### I06

- sweep result file:
  - [I06_reference_sweep_results.json](/home/rick/Desktop/benchmark/bench/data/reference/I06_reference_sweep_results.json)
- best configuration:
  - `run_id = t02_r05_c03`
  - `trend_weight = 0.2`
  - `reversion_weight = 0.5`
  - `carry_weight = 0.3`
  - `total_trades = 14565`
  - `sharpe_ratio = 0.3793`
  - `total_return = 24037.488%`

### I08

- comparison file:
  - [I08_reference_comparison.json](/home/rick/Desktop/benchmark/bench/data/reference/I08_reference_comparison.json)
- runs:
  - `insight_weighting`: `no_trades`
  - `equal_weighting`: `63 trades`, `7.967% total_return`

### I09

- comparison file:
  - [I09_reference_comparison.json](/home/rick/Desktop/benchmark/bench/data/reference/I09_reference_comparison.json)
- runs:
  - `norisk`: `395 trades`, `-76.644%`
  - `builtin`: `24797 trades`, `49.510%`
  - `custom`: `33970 trades`, `118.399%`

### I10

- grid file:
  - [I10_reference_grid_results.json](/home/rick/Desktop/benchmark/bench/data/reference/I10_reference_grid_results.json)
- total configurations tested:
  - `250`
- best configuration:
  - `run_id = f15_s20_t0005`
  - `fast_period = 15`
  - `slow_period = 20`
  - `signal_threshold = 0.005`
  - `total_trades = 4611`
  - `sharpe_ratio = 0.553`
  - `total_return = 148.564%`
  - `max_drawdown = 34.500%`

## Data Contract Status

- frozen trade universe:
  - `635` daily symbols
- hourly coverage:
  - `20`
- 4hour coverage:
  - `20`
- minute coverage:
  - `5`
- 5minute coverage:
  - `5`
- `quote` / `margin_interest`:
  - de-scoped from the contract

Coverage report:

- [benchmark_universe_coverage.json](/home/rick/Desktop/benchmark/bench/data/benchmark_universe_coverage.json)

## Validation Notes

- The remote exploded `lean/` tree was deleted. Future clean-cache consumers should rely on `I.tar.gz` only.
- The active benchmark path is now the refreshed [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference) root, not the legacy subdirectory.
- Any review should ignore the legacy folder unless it is explicitly doing provenance / drift analysis.
- `I05` and `I07` still need semantic scrutiny, but their active artifacts are now clearly separated from the pre-refresh ones.

## Files To Hand To Claude Code

- Active baseline:
  - [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference)
- Legacy baseline:
  - [legacy_2026-03-25_pre_remote_refresh](/home/rick/Desktop/benchmark/bench/data/reference/legacy_2026-03-25_pre_remote_refresh)
- Remote pin:
  - [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py)
- Upload/publish path:
  - [upload_lean_to_hf.py](/home/rick/Desktop/benchmark/bench/scripts/upload_lean_to_hf.py)
