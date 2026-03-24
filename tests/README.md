# Tests — Infrastructure Validation

## Test Files

### `test_lean_backtest.py` — LEAN Backtest Pipeline (I-series)

Validates the Docker-based LEAN backtest infrastructure end-to-end, **without any LLM simulation**. Runs the reference C# algorithms through the same pipeline that agents use during evaluation.

**What it tests:**
- Docker image (`quant-tutor-env:v2.2-lean`) starts and has .NET 10.0
- LEAN market data (635 daily symbols, 671 universe) is mounted correctly
- C# algorithms compile via `dotnet build` inside the container
- LEAN engine runs backtests to completion
- Result files (summary, orders, trades, logs) are produced
- Trade counts, net profit, and Sharpe match expected values
- Dataset coherence (universe vs available data, missing file types)

**Run:**
```bash
pytest tests/test_lean_backtest.py -v -s              # all 9 tests (17 min)
pytest tests/test_lean_backtest.py -v -s -k I01       # single task (~3 min)
pytest tests/test_lean_backtest.py -v -s -k "smoke"   # Docker check only
```

### `test_lean_eval_helpers.py` — Eval Fallback Regressions

Regression tests for the LEAN eval-helper layer. These are fast, local tests that
exercise the file-discovery and fallback logic used by the I-series scoring code.

**What it tests:**
- `result.json` is sufficient when `trades.json` is missing
- Order-pairing fallback uses clean symbols, real `fillPrice`, and numeric timestamps
- I02/I05 order-paired trades fully match the reference trade counts and timings
- Native LEAN `closedTrades` keep clean symbol values
- `_implementation_check.py` remains behaviorally identical to the shared common helper

### `test_lean_golden_eval.py` — Golden Score Sanity

Fast sanity tests over the saved `tests/results/I01-I07` workspaces.

**What it tests:**
- Golden workspaces achieve strong behavioral scores through the real eval stack
- Expected layers are available for each task
- The benchmark does not regress back into “files readable but scores meaningless”

**Run:**
```bash
pytest tests/test_lean_eval_helpers.py -q
pytest tests/test_lean_golden_eval.py -q
pytest tests/test_lean_backtest.py tests/test_lean_eval_helpers.py tests/test_lean_golden_eval.py -q   # full validation
```

**Prerequisites:**
- Docker running with `quant-tutor-env:v2.2-lean` image built
- LEAN data cached at `bench/data/hf_cache/lean/I/` (run `ensure_data(series="lean")` first)

**Results (2026-03-24, post coherence fixes + fallback repairs):**

| Test | Trades | trades.json | Orders | Net Profit | Status | Notes |
|------|:---:|:---:|:---:|:---:|:---:|-------|
| I01 (SMA single symbol) | 85 | 85 | 340 | 32.910% | PASS | exact trade + profit match |
| I02 (trend following, ~671 symbols) | 0* | - | 9,305 | -** | PASS | eval reconstructs 1,763 trades and performance from `result.json` + order events |
| I03 (RSI mean reversion) | 662 | 662 | 2,778 | -102.952% | PASS | exact trade + profit match |
| I04 (multi-timeframe composite) | 4,026 | 4,026 | 16,104 | -17.194% | PASS | exact trade + profit match |
| I05 (cross-asset correlation) | 0* | - | 9,178 | -** | PASS | eval reconstructs 2,294 trades and performance from `result.json` + order events |
| I07 (alpha model framework) | 466 | 466 | 1,004 | -34.090% | PASS | profit matches, trades differ across LEAN builds |
| Docker smoke | - | - | - | - | PASS | .NET 10.0.200 |
| Data mount check | - | - | - | - | PASS | 635 daily, 671 universe |
| Dataset coherence | - | - | - | - | PASS | 36 missing daily, 0 quote.zip, sidecar DBs present |

**Latest full validation:**
```bash
pytest tests/test_lean_backtest.py tests/test_lean_eval_helpers.py tests/test_lean_golden_eval.py -q
# 15 passed in 1013.87s (0:16:53)
```

\* LEAN's `TradeBuilder` reports 0 closed trades for multi-symbol CryptoFuture strategies in this build.
\** Top-level `summary.json` statistics are empty for those runs; the eval layer recovers trades from order events and performance from `result.json`.

**Coherence fixes applied (this session):**

1. **Eval readers hardened** — `load_agent_trades()` now searches 5 sources: `trades.json` → `*-trades.json` → closedTrades in summary → closedTrades in main JSON → FIFO order pairing. `collect_lean_results()` searches 3 patterns. Previously each was hardcoded to one file, causing behavioral score = 0.0 for correct implementations.

2. **`run_backtest.sh` fixed** — Now copies main LEAN JSON to `result.json`, extracts `closedTrades` to `trades.json` when available, tries `-summary.json` before `-statistics.json`, includes DLL copy step.

3. **Docker image rebuilt** — All fixes baked in. Runtime injection from `container_manager.py` is now a safety net, not a requirement.

4. **Trade/order semantics separated** — `tools.py` and `trial_manager.py` now report `total_trades` and `total_orders` as distinct fields instead of falling back from one to the other.

5. **Test assertions tightened** — Net profit asserted (was print-only), exit-code override narrowed to code 4 only (was 3+4), `Total Orders` removed from trade-count fallback.

6. **Order-pairing repaired** — I02/I05 fallback trades now use `symbolValue`/`fillPrice`, strip LEAN suffixes (e.g. `ADAUSDT 18R` → `ADAUSDT`), and keep numeric timestamps instead of stringifying epoch values.

7. **Trade matching hardened** — `match_trades()` now requires symbol equality, preventing cross-symbol false matches.

8. **`result.json` fallback completed** — Eval helpers now prefer deterministic `result.json` when reconstructing closed trades and recover I02/I05 performance metrics from `runtimeStatistics` + equity/drawdown charts when `summary.json` is empty.

9. **Single helper source of truth** — `_implementation_check.py` is now a compatibility wrapper over `common/implementation_check.py`, eliminating drift between the two copies.

10. **Golden performance references added** — I01 / I03 / I04 / I07 now have reference summary files, so performance is scored for all saved I-series goldens.

11. **Golden sanity suite added** — `test_lean_golden_eval.py` now locks the current behavioral-score baseline into CI-friendly regression tests.

**Known remaining issues:**

1. **LEAN TradeBuilder quirk** — Multi-symbol CryptoFuture strategies (I02, I05) report `totalNumberOfTrades: 0`. The eval layer now handles this via order-pairing fallback and reconstructs performance from `result.json`.

2. **Sharpe variance across LEAN builds** — Different LEAN commits produce different Sharpe ratios for identical trades. Trade counts and net profit are exact matches. Tests use 0.2 absolute tolerance for Sharpe.

3. **Dataset gap** — Universe has 671 symbols but only 635 have daily trade data. No quote or margin_interest data. 1,342+ quote.zip and 1,342+ margin_interest requests fail per multi-symbol backtest. Handled gracefully by LEAN.

---

### `test_parsers.py` — Schema Validation (broken)

Unit tests for the data pipeline parser schemas. **Currently not runnable** — the module-level import of `lib.schemas` pulls in `aiohttp` which is not installed in the test venv.

```bash
pytest tests/test_parsers.py -v  # fails at collection: ModuleNotFoundError: aiohttp
```

---

### `results/` — Persistent Backtest Output & Analysis

All 6 I-series backtest results saved for manual inspection. Not used by pytest (which uses `tmp_path`).

```
results/
  ANALYSIS.md          # Full analysis: per-task findings, reference comparison,
                       # infrastructure issues, Sharpe variance table, fixes applied
  I01/                 # SMA(20) BTCUSDT — 85 trades, exact match
  I02/                 # Dual SMA 671 symbols — 9,305 orders
  I03/                 # RSI(14) mean reversion — 662 trades, exact match
  I04/                 # Multi-timeframe composite — 4,026 trades, exact match
  I05/                 # Cross-asset correlation — 9,178 orders
  I07/                 # Alpha model framework — 466 trades, profit matches
```

I02 and I05 deserve special note:
- `trades.json` is still absent because LEAN emitted `0` closed trades
- The eval layer now reconstructs the trade list from `orders.json` and performance from `result.json`
- Current saved goldens score through the real eval helpers:
  I02 composite `0.597`
  I05 composite `0.971`

I01 / I03 / I04 / I07 now also have reference summary baselines, so their performance layers are scored rather than treated as unavailable.

Each task directory contains:
```
{TASK}/
  Algorithm.cs                                    # C# strategy source (reference algo)
  run_output.txt                                  # Full stdout from run_backtest.sh
  results/
    result.json                                    # Main LEAN output (Orders, closedTrades, charts)
    trades.json                                    # Extracted closedTrades (when >0)
    summary.json                                   # Copied from *-summary.json
    orders.json                                    # Copied from *-order-events.json
    {Namespace.ClassName}.json                      # Full LEAN output (original name)
    {Namespace.ClassName}-summary.json             # LEAN statistics (original name)
    {Namespace.ClassName}-order-events.json         # Order events (original name)
    log.txt                                         # LEAN engine trace log
```

---

## What's NOT Tested Yet

The following areas need infrastructure validation tests (no LLM required):

- [ ] **Non-LEAN backtests** — Python-based backtest tasks (B-series) using `run_backtest` tool
- [ ] **MCP tool execution** — Tool executor daemon, proxy logging, distractor injection
- [ ] **Container lifecycle** — Start/stop/cleanup, executor handshake, timeout enforcement
- [ ] **Data pipeline** — HuggingFace download, staged directory creation, file filtering
- [ ] **Eval scripts against known results** — Run I01-I10 eval scripts on saved workspace, verify scores > 0
- [ ] **Web dashboard** — FastAPI server startup, SSE events, result browsing API
- [ ] **Agent adapters** — SDK connectivity (requires API keys, may be integration-only)
- [ ] **I08/I09/I10** — Multi-run tasks (parameter sweeps, risk configs, grid search)
- [ ] **`test_parsers.py` import fix** — Decouple `lib.schemas` from `aiohttp`
