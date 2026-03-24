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

**Prerequisites:**
- Docker running with `quant-tutor-env:v2.2-lean` image built
- LEAN data cached at `bench/data/hf_cache/lean/I/` (run `ensure_data(series="lean")` first)

**Results (2026-03-24, post coherence fixes):**

| Test | Trades | trades.json | Orders | Net Profit | Status | Notes |
|------|:---:|:---:|:---:|:---:|:---:|-------|
| I01 (SMA single symbol) | 85 | 85 | 340 | 32.910% | PASS | exact trade + profit match |
| I02 (trend following, ~671 symbols) | 0* | - | 9,305 | -** | PASS | eval uses order-pairing fallback (1,763 trades) |
| I03 (RSI mean reversion) | 662 | 662 | 2,778 | -102.952% | PASS | exact trade + profit match |
| I04 (multi-timeframe composite) | 4,026 | 4,026 | 16,104 | -17.194% | PASS | exact trade + profit match |
| I05 (cross-asset correlation) | 0* | - | 9,178 | -** | PASS | eval uses order-pairing fallback |
| I07 (alpha model framework) | 466 | 466 | 1,004 | -34.090% | PASS | profit matches, trades differ across LEAN builds |
| Docker smoke | - | - | - | - | PASS | .NET 10.0.200 |
| Data mount check | - | - | - | - | PASS | 635 daily, 671 universe |
| Dataset coherence | - | - | - | - | PASS | 36 missing daily, 0 quote.zip, sidecar DBs present |

\* LEAN's `TradeBuilder` reports 0 closed trades for multi-symbol CryptoFuture strategies in this build. The eval readers fall back to FIFO order-pairing.

**Coherence fixes applied (this session):**

1. **Eval readers hardened** — `load_agent_trades()` now searches 5 sources: `trades.json` → `*-trades.json` → closedTrades in summary → closedTrades in main JSON → FIFO order pairing. `collect_lean_results()` searches 3 patterns. Previously each was hardcoded to one file, causing behavioral score = 0.0 for correct implementations.

2. **`run_backtest.sh` fixed** — Now copies main LEAN JSON to `result.json`, extracts `closedTrades` to `trades.json` when available, tries `-summary.json` before `-statistics.json`, includes DLL copy step.

3. **Docker image rebuilt** — All fixes baked in. Runtime injection from `container_manager.py` is now a safety net, not a requirement.

4. **Trade/order semantics separated** — `tools.py` and `trial_manager.py` now report `total_trades` and `total_orders` as distinct fields instead of falling back from one to the other.

5. **Test assertions tightened** — Net profit asserted (was print-only), exit-code override narrowed to code 4 only (was 3+4), `Total Orders` removed from trade-count fallback.

**Known remaining issues:**

1. **LEAN TradeBuilder quirk** — Multi-symbol CryptoFuture strategies (I02, I05) report `totalNumberOfTrades: 0`. The eval reader now handles this via order-pairing fallback.

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
