# I-Series Remote Data Refresh And Validation — 2026-03-25

## Summary

This document records the end-to-end refresh of the LEAN I-series dataset on HuggingFace, the resulting benchmark-universe contract change, and the validation runs executed against the new remote data.

This is intended to be handed to another agent or engineer for independent verification.

## What Changed

### Code changes

- Commit [`d215d47`](../bench/config/benchmark_config.py): `Freeze LEAN benchmark universe by coverage`
  - Added [freeze_benchmark_universe.py](/home/rick/Desktop/benchmark/bench/scripts/freeze_benchmark_universe.py)
  - Added [lean_data_audit.py](/home/rick/Desktop/benchmark/bench/scripts/lean_data_audit.py)
  - Changed downloader failure semantics in [download_binance_full_universe.py](/home/rick/Desktop/benchmark/bench/scripts/download_binance_full_universe.py)
  - Changed preflight verification in [prepare_i_series_data.py](/home/rick/Desktop/benchmark/bench/scripts/prepare_i_series_data.py)
  - Updated LEAN coherence tests in [test_lean_backtest.py](/home/rick/Desktop/benchmark/tests/test_lean_backtest.py)
  - Updated [tests/README.md](/home/rick/Desktop/benchmark/tests/README.md)

- Commit [`813a45a`](../bench/scripts/upload_lean_to_hf.py): `Publish frozen LEAN archive to HuggingFace`
  - Changed [upload_lean_to_hf.py](/home/rick/Desktop/benchmark/bench/scripts/upload_lean_to_hf.py) to publish the canonical `I.tar.gz` archive that `data_manager.ensure_data(series="lean")` actually consumes
  - Added frozen-universe metadata upload:
    - `raw/i-series/universe.json`
    - `raw/i-series/universe_structured.json`
    - `raw/i-series/benchmark_universe_coverage.json`
  - Updated [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py) to pin the final validated revision

### Data-contract changes

- Tier 1 universe was frozen by actual trade-data coverage:
  - old tier1 size: `671`
  - new tier1 size: `635`
- Tier 2 and tier 3 remained unchanged:
  - tier2: `20`
  - tier3: `5`
- `quote` and `margin_interest` were explicitly de-scoped from the benchmark contract.

### Final pinned HuggingFace revision

- Final validated dataset revision:
  - `c97f2cc969216d0dc85c55c8b4de62ef5715ba9d`

### Final remote dataset shape checked

Verified on `Varsity-Tech/quant-tutor-bench-data` at revision `c97f2cc969216d0dc85c55c8b4de62ef5715ba9d`:

- `I.tar.gz`: present
- `raw/i-series/universe.json`: present
- `raw/i-series/universe_structured.json`: present
- `raw/i-series/benchmark_universe_coverage.json`: present
- `lean/universe.json`: still present as a legacy remote path

Important note:

- The canonical consumer path for future LEAN runs is `I.tar.gz` via `data_manager.ensure_data(series="lean")`.
- The legacy exploded `lean/` tree still exists remotely and was not deleted in this session.

## What Was Verified

### 1. Local contract / pipeline checks

Commands run:

```bash
python3 bench/scripts/freeze_benchmark_universe.py --input bench/data/universe.json --raw-dir bench/data/raw/i-series --lean-dir bench/data/lean --output bench/data/universe.json --flat-output bench/data/lean_universe.json --report-output bench/data/benchmark_universe_coverage.json
python3 bench/scripts/lean_data_audit.py
python3 bench/scripts/prepare_i_series_data.py --skip-download --skip-convert --verify
pytest tests/test_lean_backtest.py -q -k "dataset_coherence or lean_data_mounted or docker_lean_smoke"
pytest tests/test_lean_backtest.py -q -k "test_lean_backtest and (I02 or I05)"
```

Observed:

- `lean_data_audit.py`:
  - `direct_missing_count = 0`
  - `shared_runtime_gap_count = 0`
  - `daily_trade_symbols = 635`
- `prepare_i_series_data.py --verify` passed
- Fast LEAN tests passed
- Real `I02` and `I05` LEAN backtests passed against the frozen local contract

### 2. Fresh remote archive sanity check

A clean-cache verification was performed by downloading and extracting `I.tar.gz` into a fresh workspace-local temporary directory, not the existing local HF cache.

Verified from the extracted archive:

- `I/universe.json` contains `635` symbols
- `I/I05_candidate_pairs.json` exists
- `E/BTC_UTC.csv` exists
- `E/E04_compound_bug.cs` exists
- `X/warmup_bug.cs` exists
- `X/order_type_bug.cs` exists
- `X/alpha_conflict.cs` exists
- `X/universe_stale.cs` exists
- `I/cryptofuture/binance/daily` contains `635` `*_trade.zip` files

Fresh remote extract used for reruns:

- [tmp_remote_verify2](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2)

### 3. Fresh remote I-series reruns

All reruns below were forced onto the fresh remote extract at:

- [extract/I](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/extract/I)

They did not rely on the pre-existing local HF cache.

## Results

### Core I-series rerun summary

Source:

- [i_series_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i_series_rerun_summary.json)

Results:

| Task | Exit | Orders | Trades | Net Profit |
| --- | ---: | ---: | ---: | ---: |
| I01 | 0 | 340 | 85 | 32.91 |
| I02 | 0 | 9305 | 0 | null |
| I03 | 0 | 2778 | 662 | -102.952 |
| I04 | 0 | 16104 | 4026 | -17.194 |
| I05 | 0 | 9362 | 0 | null |
| I07 | 0 | 1004 | 466 | -34.09 |

Notes:

- `I02` and `I05` still produce `0` native LEAN closed trades in this build. This remained true across the earlier pinned-vs-latest LEAN comparison.
- `I05` now reproducibly yields `9362` orders on the refreshed remote dataset, not the previously observed `9178`. The old baseline should therefore not be treated as authoritative.

### I06 / I08 / I09 rerun summary

Source:

- [i_series_extended_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i_series_extended_rerun_summary.json)
- [I06_reference_sweep_results.json](/home/rick/Desktop/benchmark/bench/reference/Implementation/result/I06_reference_sweep_results.json)
- [I08_reference_comparison.json](/home/rick/Desktop/benchmark/bench/reference/Implementation/result/I08_reference_comparison.json)
- [I09_reference_comparison.json](/home/rick/Desktop/benchmark/bench/reference/Implementation/result/I09_reference_comparison.json)

Results:

- `I06`
  - runs: `19/19 success`
  - best configuration: `t02_r05_c03`
  - best metrics:
    - `total_trades = 14565`
    - `sharpe_ratio = 0.3793`
    - `total_return = 24037.488%`
    - `max_drawdown = 39.100%`
    - `win_rate = 26%`

- `I08`
  - runs: `2`
  - `insight_weighting`: `no_trades`
  - `equal_weighting`: `success`, `63 trades`, `7.967% total_return`

- `I09`
  - runs: `3/3 success`
  - `norisk`: `395 trades`, `-76.644% total_return`
  - `builtin`: `24797 trades`, `49.510% total_return`
  - `custom`: `33970 trades`, `118.399% total_return`

### I10 rerun summary

Sources:

- [i10_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i10_rerun_summary.json)
- [I10_reference_grid_results.json](/home/rick/Desktop/benchmark/bench/reference/Implementation/result/I10_reference_grid_results.json)

Results:

- total configurations attempted: `250`
- total configurations tested: `250`
- status counts:
  - `success = 250`
- nonzero-trade runs: `250`

Best configuration:

- `run_id = f15_s20_t0005`
- parameters:
  - `fast_period = 15`
  - `slow_period = 20`
  - `signal_threshold = 0.005`
- metrics:
  - `total_trades = 4611`
  - `sharpe_ratio = 0.553`
  - `total_return = 148.564%`
  - `max_drawdown = 34.500%`
  - `win_rate = 40%`

## Current Assessment

### What is confirmed

- The new remote HF revision is consumable by a clean-cache client.
- The canonical `I.tar.gz` path now contains the frozen `635`-symbol trade-data universe.
- The archive also contains the extra clean-cache task files required by `I05`, `E`, and `X`.
- `I01` through `I10` were rerun against fresh remote data, with:
  - core tasks rerun directly and summarized
  - `I06`, `I08`, `I09`, and `I10` multi-run outputs regenerated successfully

### What is not yet claimed

- This document does **not** claim that all refreshed results are semantically identical to the previously stored baselines.
- In particular, `I05` changed from the older `9178`-order world to a reproducible `9362`-order world under the refreshed remote dataset.
- The old stored baselines should therefore be treated as historical artifacts, not ground truth.

### Recommended next validation pass for Claude Code

Ask Claude Code to verify:

1. The remote revision `c97f2cc969216d0dc85c55c8b4de62ef5715ba9d` is the revision pinned in [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py).
2. A clean-cache download of `I.tar.gz` yields:
   - `635` symbols in `I/universe.json`
   - `I/I05_candidate_pairs.json`
   - `E/` and `X/` files listed above
3. `bench/scripts/lean_data_audit.py` reports zero contract gaps against the refreshed local contract.
4. The rerun summaries listed above exist and match the values in this document.
5. `I05`’s changed order count (`9362`) is reflected in the fresh remote rerun and is no longer judged against the old `9178` baseline.

## File Index

- Contract / metadata
  - [benchmark_config.py](/home/rick/Desktop/benchmark/bench/config/benchmark_config.py)
  - [universe.json](/home/rick/Desktop/benchmark/bench/data/universe.json)
  - [lean_universe.json](/home/rick/Desktop/benchmark/bench/data/lean_universe.json)
  - [benchmark_universe_coverage.json](/home/rick/Desktop/benchmark/bench/data/benchmark_universe_coverage.json)

- Scripts changed
  - [freeze_benchmark_universe.py](/home/rick/Desktop/benchmark/bench/scripts/freeze_benchmark_universe.py)
  - [lean_data_audit.py](/home/rick/Desktop/benchmark/bench/scripts/lean_data_audit.py)
  - [download_binance_full_universe.py](/home/rick/Desktop/benchmark/bench/scripts/download_binance_full_universe.py)
  - [prepare_i_series_data.py](/home/rick/Desktop/benchmark/bench/scripts/prepare_i_series_data.py)
  - [upload_lean_to_hf.py](/home/rick/Desktop/benchmark/bench/scripts/upload_lean_to_hf.py)

- Test / validation
  - [test_lean_backtest.py](/home/rick/Desktop/benchmark/tests/test_lean_backtest.py)
  - [tests/README.md](/home/rick/Desktop/benchmark/tests/README.md)

- Fresh remote rerun summaries
  - [i_series_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i_series_rerun_summary.json)
  - [i_series_extended_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i_series_extended_rerun_summary.json)
  - [i10_rerun_summary.json](/home/rick/Desktop/benchmark/bench/data/tmp_remote_verify2/i10_rerun_summary.json)

- Generated reference outputs
  - [Implementation/result](/home/rick/Desktop/benchmark/bench/reference/Implementation/result)
