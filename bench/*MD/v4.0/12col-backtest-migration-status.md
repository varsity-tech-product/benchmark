# 12-Col Backtest System Migration Status

**Date**: 2026-04-12
**Branch**: `rick-12col-backtest`

## What Changed

The benchmark LEAN backtest system was migrated from 6-col LEAN-native OHLCV data to 12-col custom data with per-side taker breakdown. The harness auto-injects a `_Kline12Col` BaseData class so AI students still write `AddCrypto()` as before.

### Data Format

| Column | Index | Field |
|--------|-------|-------|
| 0 | timestamp | open_time (ms) |
| 1-5 | OHLCV | open, high, low, close, volume |
| 6-7 | taker volumes | taker_buy_volume, taker_sell_volume |
| 8-9 | taker quote vols | taker_buy_quote_volume, taker_sell_quote_volume |
| 10-11 | taker trades | taker_buy_trades, taker_sell_trades |

### Data Coverage

- **Symbols**: 635 (all with data in 2021-2025)
- **Resolutions**: 1m, 5m, 1h, 1d
- **Date range**: 2021-01-01 to 2025-12-31
- **Size**: 22 GB on disk, 20.8 GB compressed on HuggingFace
- **Source**: ClickHouse (`54.238.27.155:9000`, database `binance_hub`)
- **Validated**: Cross-checked against ClickHouse across 6 symbols × 3 timeframes, 1m exact, higher timeframes <5e-6 noise

### Harness Injection Pipeline

Student writes:
```csharp
var crypto = AddCrypto("BTCUSDT", Resolution.Hour, Market.Binance);
```

Harness auto-transforms to:
```csharp
// _Kline12Col class injected (BaseData with GetSource/Reader for 12-col zips)
_Kline12Col.CustomDataRoot = GetParameter("custom-data-root", "/data/custom/binance");
_Kline12Col.ResolutionFolder = "hour";
var crypto = AddData<_Kline12Col>("BTCUSDT", Resolution.Hour);
// SymbolProperties reflection hack injected
// SetAccountCurrency, fee model, TradingDaysPerYear injected
```

### Parity Validation

Tested against remote backtest service (18.182.20.120:8600):
- EMA(10/30) on BTCUSDT 2021-2025: **3,089/3,089 fills match exactly**
- Bollinger(20): **3,425/3,425 fills match exactly**
- KlineDump: **43,820 bars** delivered correctly

---

## Completed

- [x] `inject_strategy.py` — `_Kline12Col` class injection + `AddCrypto`→`AddData` transform
- [x] `run_backtest.sh` — legacy standard mode removed; 12-col custom mode only
- [x] `benchmark_dates.py` — `BENCH_START` expanded to `2021-01-01`
- [x] `benchmark_config.py` — `DATASET_REVISION` updated to `85449fc` (includes 12-col archive)
- [x] `tools.py` — agent guidance updated (no data path details, 2021-2025)
- [x] `container_manager.py` — `custom_data_dir` mount at `/data/custom/`
- [x] `orchestrator.py` — passes `custom_data` path to container
- [x] `data_manager.py` — runtime no longer downloads `I.tar.gz`; uses tracked LEAN metadata/task fixtures + `custom_binance_12col.tar.gz`
- [x] `generate_lean_reference.py` — mounts custom data plus metadata-only `/lean/Data`
- [x] `bench/runtime_assets/lean/` — tracked LEAN metadata and LEAN task fixtures extracted out of the legacy `I/E/X` bundle
- [x] `convert_binance_to_lean.py` — defaults to `--security-type crypto`
- [x] `generate_symbol_properties.py` — outputs `crypto` type (was `cryptofuture`)
- [x] Evaluation scripts — accept `AddCrypto` pattern (students write AddCrypto, harness transforms)
- [x] HuggingFace upload tooling — uploader now targets `custom_binance_12col.tar.gz` instead of `I.tar.gz`
- [x] `fetch_and_aggregate.py` — two-pass aggregation for ClickHouse float parity
- [x] Parity test strategies — 5 strategies tested against remote service
- [x] E2E verification — student-style `AddCrypto` strategy compiles and runs
- [x] Lean runtime audit — 17 LEAN tasks, 0 direct-missing files, 0 shared runtime gaps

## TODO

### Must Do (before merging to master)

- [ ] **Regenerate all I-series reference results (I01-I10)**
  Reference algorithms need updating to work with the new 12-col data. The harness injection handles `AddCrypto`→`AddData` automatically, but the reference algorithms in the HF dataset (submodule) need to be re-run with the new data and injection pipeline.
  - Files: `bench/reference/Implementation/algorithms/I01-I10_*.cs`
  - Runner: `bench/reference_generator/generate_lean_reference.py`
  - Output: `bench/data/reference/I*_reference_trades.json`

- [ ] **Regenerate X-series reference results (X07-X10)**
  Same as I-series — debug task fixed algorithms need re-running.
  - Files: `bench/reference/debug/algorithms/X07-X10_*.cs`

- [ ] **Update reference.tar.gz on HuggingFace**
  After regenerating references, re-upload `reference.tar.gz` and update `DATASET_REVISION`.

- [ ] **Rebuild Docker image (optional but recommended)**
  The current `quant-tutor-env:v2.2-lean` image has the old `run_backtest.sh` and `inject_strategy.py` baked in. Currently we mount updated versions at runtime, which works but is fragile. Rebuild the image with the new scripts.

### Should Do

- [x] **Remove runtime dependency on `I.tar.gz`**
  The benchmark runtime now uses tracked assets under `bench/runtime_assets/lean/`:
  - `metadata/` → mounted at `/lean/Data` (`symbol-properties/`, `market-hours/`, `universe.json`)
  - `data/` → staged task fixtures (`BTC_UTC.csv`, `I05_candidate_pairs.json`, `E04_compound_bug.cs`, `universe.json`)
  - `student_code/` → LEAN debug fixtures (`warmup_bug.cs`, `order_type_bug.cs`, `alpha_conflict.cs`, `universe_stale.cs`)
  The runtime no longer downloads or reads `I.tar.gz`.

- [ ] **Delete legacy `I.tar.gz` from HuggingFace (optional but recommended)**
  The codebase no longer depends on `I.tar.gz`, but the dataset repo may still contain it from older revisions.
  - Use: `python bench/scripts/upload_lean_to_hf.py --delete-legacy-i-tar`
  - Do this after confirming no external workflow still consumes the old archive.

- [ ] **Test with actual benchmark run (full orchestrator pipeline)**
  The E2E test used Docker directly. Should also test through the full orchestrator → container_manager → MCP tools → agent pipeline.

### Nice to Have

- [ ] **Add `AddCryptoFuture` → `AddData` transform**
  Currently only transforms `AddCrypto`. Some legacy strategies or student code might use `AddCryptoFuture` — add a parallel transform for it.

- [ ] **Support multi-resolution strategies**
  Current injection uses the first `AddCrypto` call's resolution for `_Kline12Col.ResolutionFolder`. Strategies that subscribe at multiple resolutions (e.g., I04 uses hourly + daily) would need per-subscription resolution handling.

- [ ] **Remove `_BenchFeeModel` naming workaround**
  Renamed from `_BacktestFeeModel` to avoid conflict with LEAN project's built-in class. The root cause is that benchmark compiles against the full `Algorithm.CSharp.csproj`. A cleaner fix: use the standalone compilation path (like the remote service's Roslyn compiler).

---

## Architecture Diagram

```
Student writes:    AddCrypto("BTCUSDT", Resolution.Hour, Market.Binance)
                           │
                   inject_strategy.py
                           │
                   ┌───────┴───────────────────────────────┐
                   │ Step 0: _Kline12Col class injection    │
                   │         AddCrypto → AddData transform  │
                   │         SymbolProperties hack          │
                   │ Step 1: SetAccountCurrency("USDT")     │
                   │ Step 2: _BenchFeeModel injection       │
                   │ Step 3: TradingDaysPerYear = 365       │
                   └───────┬───────────────────────────────┘
                           │
                   dotnet build + LEAN engine
                           │
                   ┌───────┴───────────────────┐
                   │ _Kline12Col.GetSource()    │
                   │ reads from:                │
                   │ /data/custom/binance/      │
                   │   hour/btcusdt/            │
                   │     20210101_trade.zip     │
                   │     ...                    │
                   └───────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `bench/scripts/inject_strategy.py` | Core injection — _Kline12Col + AddCrypto transform |
| `bench/docker/run_backtest.sh` | Docker entrypoint — runs injection + compile + LEAN |
| `bench/runtime_assets/lean/` | Tracked LEAN metadata + LEAN task fixtures used by 12-col runtime |
| `bench/scripts/fetch_and_aggregate.py` | Canonical data generator (Binance aggTrades → 12-col) |
| `bench/scripts/download_clickhouse_12col.py` | Convenience downloader (ClickHouse → 12-col, not in git) |
| `bench/config/benchmark_dates.py` | Date range: 2021-01-01 to 2025-12-31 |
| `bench/config/benchmark_config.py` | HF dataset revision: `85449fc` |
| `bench/scripts/data_manager.py` | Runtime asset resolution + 12-col archive download |
| `bench/scripts/parity/` | Parity test strategies and results |
