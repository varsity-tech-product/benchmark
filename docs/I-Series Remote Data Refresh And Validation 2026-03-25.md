# I-Series Current Baseline And Legacy Archive — 2026-03-25

## Scope

This document describes the **current active I-series baseline** only.

It does not treat the older pre-refresh reference files as active benchmark inputs.
Those files have been moved into a legacy subdirectory so they do not confuse future reviews.

## Current Source Of Truth

### Remote dataset

- HuggingFace dataset: `Varsity-Tech/quant-tutor-bench-data`
- Current pinned revision: `daa6bb96a89f29bceab22a13fd5b25768305f627`
- Canonical consumer path:
  - `I.tar.gz`

### Local code pin

- [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py)
  - `DATASET_REVISION = "daa6bb96a89f29bceab22a13fd5b25768305f627"`

### Active benchmark reference directory

- [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference)

This is the directory the evaluation stack actually reads via:

- [implementation_check.py](/home/rick/Desktop/benchmark/bench/evaluation/test_scripts/common/implementation_check.py)

## Legacy Archive

The older pre-refresh active reference files were moved out of the way to:

- [legacy_2026-03-25_pre_remote_refresh](/home/rick/Desktop/benchmark/bench/data/reference/legacy_2026-03-25_pre_remote_refresh)

That folder currently contains `39` legacy I-series reference artifacts that should be treated as historical provenance only.

## Data Quality Fix — Scientific Notation (2026-03-25)

### Problem

4 tokens (`1000SATSUSDT`, `1000WHYUSDT`, `DOGSUSDT`, `NEIROUSDT`) had prices
that dropped below ~$0.0001 during the 2022-2025 backtest window, causing
Python's CSV writer to emit scientific notation (e.g., `6.68e-05`). LEAN's
CryptoFuture data parser does not handle scientific notation, producing fill
prices inflated by ~100,000x.

This caused **I02, I05, and I06** references to be corrupted by phantom trades
at impossible prices (e.g., $122,705 for a token actually worth $0.00008).

### Fix

- `bench/scripts/convert_binance_to_lean.py`: Added `_fmt_price()` helper that
  forces fixed-point decimal output (`f"{v:.10f}"` with trailing-zero stripping),
  applied to all OHLC price fields.
- All 635 daily data files regenerated with fixed formatting.
- New `I.tar.gz` uploaded to HuggingFace (revision `daa6bb96`).
- All I-series references regenerated against the fixed data.

### Signal generation fix

- `bench/reference/script/generate_reference_signals.py`: Fixed `BENCH_ROOT`
  path (was `SCRIPT_DIR.parent` → `bench/reference/`, corrected to
  `SCRIPT_DIR.parent.parent` → `bench/`). Signals were previously empty (0)
  for all tasks due to `universe.json` not found at the wrong path.

### Impact

| Task | Old Return | New Return | Change |
| --- | ---: | ---: | --- |
| I01 | +32.91% | +32.91% | unchanged |
| I02 | +20,980% | **-56.52%** | was corrupted (phantom 1000WHYUSDT trade) |
| I03 | -102.95% | -102.95% | unchanged |
| I04 | -17.19% | -17.19% | unchanged |
| I05 | -309,669% | **-10.64%** | was corrupted (phantom 1000SATSUSDT trade) |
| I06 | +27,604% | **-59.10%** | was corrupted (multiple affected tokens) |
| I07 | -34.09% | -34.09% | unchanged |
| I08 | +7.97% | +2.49% | minor (data rounding change) |
| I09 | +49.51% | +49.51% | unchanged (top-20 only) |
| I10 | +148.56% | +148.56% | unchanged (top-20 only) |

## Active Reference Inventory

The active top-level [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference) now contains:

- `I01-I07`
  - `*_reference_trades.json`
  - `*_reference_summary.json`
  - `*_reference_signals.json`
- `I06`
  - default `I06_reference_trades.json` (config `t04_r03_c03`)
  - sweep result file
  - per-config trade files (19 configs)
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
  - per-config trade files (250 configs)
  - summary + signals

## Current Active Baseline Results

### Single-run tasks

These are the current active trade references in [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference):

| Task | `generated_at` | `lean_image` | trade_count |
| --- | --- | --- | ---: |
| I01 | `2026-03-25T08:33:24.761622+00:00` | `quant-tutor-env:v2.2-lean` | 85 |
| I02 | `2026-03-25T08:28:57.631482+00:00` | `quant-tutor-env:v2.2-lean` | 1762 |
| I03 | `2026-03-25T08:33:48.696995+00:00` | `quant-tutor-env:v2.2-lean` | 662 |
| I04 | `2026-03-25T08:33:57.216883+00:00` | `quant-tutor-env:v2.2-lean` | 4026 |
| I05 | `2026-03-25T08:28:40.883224+00:00` | `quant-tutor-env:v2.2-lean` | 3336 |
| I06 | `2026-03-25T08:35:56.812024+00:00` | `quant-tutor-env:v2.2-lean` | 14174 |
| I07 | `2026-03-25T08:33:37.985115+00:00` | `quant-tutor-env:v2.2-lean` | 179 |

### Current summary metrics

These are the current active summary metrics in [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference):

| Task | total_return_pct | sharpe_ratio | total_trades |
| --- | ---: | ---: | ---: |
| I01 | 32.91 | 0.168 | 85 |
| I02 | -56.52 | -0.236 | 1762 |
| I03 | -102.952 | -0.335 | 662 |
| I04 | -17.194 | -0.07 | 4026 |
| I05 | -10.636 | -0.627 | 3336 |
| I06 | -59.103 | -0.832 | 14174 |
| I07 | -34.09 | -0.059 | 179 |
| I08 | 2.492 | -0.003 | 69 |
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
  - `total_trades = 14871`
  - `sharpe_ratio = -0.654`
  - `total_return = -45.380%`

### I08

- comparison file:
  - [I08_reference_comparison.json](/home/rick/Desktop/benchmark/bench/data/reference/I08_reference_comparison.json)
- runs:
  - `insight_weighting`: `no_trades`
  - `equal_weighting`: `69 trades`, `2.492% total_return`

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
- All CSV data files verified: 0 scientific notation lines across all 635 daily files.
- All reference signals now populated (previously empty due to path bug).

## Files To Hand To Claude Code

- Active baseline:
  - [bench/data/reference](/home/rick/Desktop/benchmark/bench/data/reference)
- Legacy baseline:
  - [legacy_2026-03-25_pre_remote_refresh](/home/rick/Desktop/benchmark/bench/data/reference/legacy_2026-03-25_pre_remote_refresh)
- Remote pin:
  - [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py)
- Upload/publish path:
  - [upload_lean_to_hf.py](/home/rick/Desktop/benchmark/bench/scripts/upload_lean_to_hf.py)
