# Backtest Section (B-Series) Design Plan

> Version: v2.0 | Status: Draft | Section: Backtest Engineering

---

## 1. Section Philosophy

### 1.1 What B-Series Tests

B-series tests the agent's ability to teach **backtest system engineering** — the infrastructure side of quantitative trading, not the alpha/strategy side.

In production quant firms, the backtest system and the strategy are two completely decoupled concerns:

```
┌─────────────────────────────────────────────────────┐
│                    Data Layer                         │
│  Raw market data → cleaning → normalization →        │
│  sequential replay interface (bar-by-bar)            │
│  Guarantee: NO future data accessible to consumers   │
└──────────────────────┬──────────────────────────────┘
                       │ feeds bars one-at-a-time
                       ▼
┌─────────────────────────────────────────────────────┐
│                  Backtest Engine                      │
│  Event loop / bar replay → order matching →          │
│  fill simulation → position tracking →               │
│  PnL accounting → performance metrics                │
│  Guarantee: realistic execution simulation           │
└──────────────────────┬──────────────────────────────┘
                       │ strategy API (on_bar, on_fill)
                       ▼
┌─────────────────────────────────────────────────────┐
│                    Strategy                           │
│  Receives current bar → computes signals →           │
│  submits orders → knows NOTHING about future bars    │
│  Focus: alpha generation, trading methodology        │
└─────────────────────────────────────────────────────┘
```

**The core evaluation question**: Does the agent understand this separation and teach the student to build each layer correctly? A weak agent dumps everything into one monolithic script. A strong agent guides the student through proper layered architecture and explains *why* the decoupling matters (data integrity, strategy swappability, look-ahead prevention).

### 1.2 Position in the Quant Workflow Pipeline

B-series sits **downstream of S-series** in the quant workflow. S-series produces validated alpha ideas; B-series builds the engineering infrastructure to rigorously validate them.

```
D (Data)  →  S (Alpha Research)  →  B (Backtest Engine)  →  I / X / E
  │               │                       │
"Get and        "Given the data,         "Given the alpha idea,
 understand      discover and             build the system to
 the data"       formalize alpha"         validate it rigorously"
```

| Section | Focus | Relationship to B-series |
|---------|-------|--------------------------|
| **S-series** (Alpha Research) | Signal discovery, hypothesis testing, signal evaluation | S-series is the **upstream producer** — it generates alpha ideas that B-series validates. S-series does rough PnL checks; B-series does rigorous engine-based validation. |
| **D-series** (Data Analysis) | Data loading, cleaning, exploration | D-series prepares the student's data skills; B-series uses those skills in the data layer |
| **I-series** (Implementation) | Implementing specific financial computations | I-series implements components (indicators); B-series integrates them into an engine |
| **E-series** (End-to-End) | Complete system from scratch | E-series is a compressed version; B-series goes deep on the engine architecture |
| **X-series** (Debug) | Finding and fixing bugs | X-series could include debugging backtest engines in future |

### 1.2.1 The S→B Handoff (Conceptual Pairing)

Each B-task consumes a simple "trading idea" as input material. These ideas are conceptually the output of S-series research:

```
S02 (trend signal on BTC daily)      →  B02 (build engine, use MA crossover idea)
S03 (reversion signal on BTC daily)  →  B03 (build engine with look-ahead proof, use RSI idea)
S05 (cross-asset BTC/ETH signal)     →  B04 (build multi-asset engine, use ratio idea)
S04 (volume signal, multi-TF)        →  B05 (build engine with execution sim, use breakout idea)
S06 (composite signal)               →  B06 (build walk-forward framework, use parameterized idea)
```

This pairing is **conceptual, not enforced** — each task is independently executable. But the design ensures the benchmark covers the full quant workflow: research → validation. Both sections share the same Binance futures data.

### 1.3 Why Crypto Futures Data (Shared with S-Series)

Both S-series and B-series use **Binance USDT-M Futures kline data**, forming a continuous pipeline on the same data:

1. **Freely available, no API key**: Data is publicly hosted at `https://data.binance.vision/?prefix=data/futures/um/daily/klines/` — no licensing concerns
2. **24/7 market**: No market open/close, no holidays — simplifies some aspects but introduces others (continuous trading, no gap handling)
3. **Rich data structure**: Kline data includes OHLCV + quote volume + trade count + taker buy volume — S-series uses the extra fields for microstructure research, B-series uses them for realistic execution simulation
4. **Futures-specific mechanics**: Funding rates, mark price, liquidation — S-series can research carry signals from funding, B-series models funding in execution
5. **Multi-timeframe availability**: 1m, 5m, 15m, 1h, 4h, 1d — S-series does cross-timeframe signal research, B-series does multi-timeframe replay
6. **Pipeline continuity**: Same data in both sections means the alpha ideas researched in S-series can be directly validated in B-series

---

## 2. Data Preparation

### 2.1 Data Source

Binance USDT-M Futures historical klines:

```
Base URL: https://data.binance.vision/data/futures/um/daily/klines/
Pattern:  {SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{DATE}.zip
Example:  BTCUSDT/1d/BTCUSDT-1d-2023-01-01.zip
```

Each CSV contains columns:
```
open_time, open, high, low, close, volume, close_time,
quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore
```

### 2.2 Frozen Data Files (Shared with S-Series)

S-series and B-series share the same frozen datasets. This is intentional — the two sections form a continuous pipeline (research alpha on the data → build engine to validate it).

Prepare and freeze the following datasets (download + merge into single CSVs):

| File | Symbol | Interval | Period | Rows (approx) | S-Series | B-Series |
|------|--------|----------|--------|---------------|----------|----------|
| `BTCUSDT_1d_2021_2024.csv` | BTCUSDT | 1d | 2021-01-01 → 2024-12-31 | ~1,460 | S02, S03, S04, S06 | B02, B04, B05, B06 |
| `ETHUSDT_1d_2021_2024.csv` | ETHUSDT | 1d | 2021-01-01 → 2024-12-31 | ~1,460 | S05, S06 | B04 |
| `BTCUSDT_1h_2023_2024.csv` | BTCUSDT | 1h | 2023-01-01 → 2024-12-31 | ~17,520 | S04, S05 | B03, B05 |
| `BTCUSDT_5m_2024Q4.csv` | BTCUSDT | 5m | 2024-10-01 → 2024-12-31 | ~26,208 | S04 | B05 |
| `ETHUSDT_1h_2023_2024.csv` | ETHUSDT | 1h | 2023-01-01 → 2024-12-31 | ~17,520 | S05 | B04 |
| `BTCUSDT_funding_2021_2024.csv` | BTCUSDT | 8h | 2021-01-01 → 2024-12-31 | ~1,095 | S06 | B05 |

**Data preparation script**: `bench/scripts/download_binance_klines.py`
- Downloads daily zip files from Binance data vision
- Merges into single CSV per symbol/interval/period
- Validates: no gaps, monotonic timestamps, all columns present
- Outputs to `bench/data/futures/`

### 2.3 Column Standardization

All frozen CSVs will use standardized column names:

```python
COLUMNS = [
    "timestamp",      # Unix ms → converted to datetime index
    "open",           # float
    "high",           # float
    "low",            # float
    "close",          # float
    "volume",         # float (base asset volume)
    "quote_volume",   # float (quote asset volume, i.e. USDT)
    "trade_count",    # int
    "taker_buy_vol",  # float (taker buy base asset volume)
    "taker_buy_quote_vol",  # float (taker buy quote asset volume)
]
```

The `close_time` and `ignore` columns from Binance raw data are dropped during preparation.

---

## 3. Task Designs

### 3.0 Existing Task: B01 — Interpret Backtest Metrics

**Status**: Exists, keep as-is. B01 is the conceptual entry point — understanding what metrics mean. It uses stock data (AAPL/SPY) and `requires_code: false`. This remains valid as a standalone conceptual task before the student enters the engineering-focused B02–B06.

---

### 3.1 B02 — Basic Sequential Backtest Engine

**Difficulty**: medium
**Category**: backtest

**Core idea**: Given BTCUSDT daily data and a simple MA crossover trading idea, teach the student to build a properly structured backtest engine with clean separation between data feeding, engine logic, and strategy logic.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`
- Trading idea (in task description): "A dual moving average crossover — go long when the fast MA crosses above the slow MA, close when it crosses below."

**Description**: Guide a student to build a basic backtest system with proper architecture: a data replay module that feeds bars sequentially, a backtest engine that handles position tracking and PnL accounting, and a strategy module that implements an MA crossover — all as cleanly separated components.

**Expected outcome**: Student produces a working backtest system with at least three distinct components: (1) a data handler that loads BTCUSDT daily klines and replays bars one at a time without exposing future data, (2) an engine/runner that iterates through bars, manages positions, tracks PnL, and computes basic performance metrics, and (3) a strategy that implements the MA crossover logic and only has access to current and past bars. The system runs end-to-end and produces a performance summary (total return, number of trades).

**Required capabilities**:
1. Design a data handler that loads CSV data and provides a sequential bar replay interface
2. Build a backtest engine with position tracking and PnL computation
3. Implement a strategy class/function that is decoupled from the engine (receives bars, emits orders)
4. Run the complete system end-to-end and produce a performance summary
5. Explain why the three-layer separation matters (data integrity, strategy swappability, look-ahead prevention)

**Student openings**:
- **beginner_no_finance**: "I have some Bitcoin price data and I want to test a moving average strategy on it. I've never built a backtester before — where do I start?"
- **intermediate_developer**: "I need to build a backtest engine for crypto futures. I know Python well, but I want to make sure the architecture is right. How should I structure the components?"
- **advanced_quant**: "I'm building a backtest framework for USDT-M futures. I want a clean data replay layer that guarantees no look-ahead, an engine with proper position accounting, and a pluggable strategy interface. What's a good architecture?"

**Environment**:
```json
{
  "data_files": ["BTCUSDT_1d_2021_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["backtesting_101.md"],
  "network_enabled": false
}
```

**Convenient tools**: `fetch_market_data`, `plot_chart`

**Eval strategy**:
- **Structural check** (programmatic): Inspect workspace `.py` files for evidence of separation — at minimum, distinct classes or modules for data handling, engine, and strategy. Heuristic: strategy code should not import pandas or directly read CSV; data handler should not contain trading logic.
- **Execution check**: The system runs without errors and produces numeric output (total return, trade count).
- **Look-ahead check**: Strategy function/class should receive bars individually (via callback, iterator, or method argument), not access the full DataFrame.

**Notes**: This is the foundational B-series task. The architecture established here is extended by all subsequent B-tasks. The task's eval should reward structural clarity, not just correct final numbers.

---

### 3.2 B03 — Look-Ahead Prevention & Verification

**Difficulty**: medium
**Category**: backtest

**Core idea**: Given BTCUSDT hourly data and an RSI strategy idea, teach the student to build a backtest engine that architecturally prevents look-ahead bias AND includes a verification test that proves no future data leaks.

**Materials provided**:
- Data: `BTCUSDT_1h_2023_2024.csv`
- Trading idea: "RSI mean reversion — go long when RSI drops below 30, close when RSI crosses above 50."

**Description**: Guide a student to build a backtest engine that architecturally prevents look-ahead bias, and then write a verification test that proves the engine does not leak future data to the strategy. The student should understand common sources of look-ahead (centering, direct DataFrame access, indicator computation on full series) and how to guard against each.

**Expected outcome**: Student produces a backtest system that (1) feeds bars to the strategy one at a time through a controlled interface, (2) computes indicators incrementally or on the available-so-far slice only, and (3) includes a verification test — for example, a "spy strategy" that attempts to access future data and fails, or a known-answer test where injecting a future price spike should not affect past signals. The system runs on BTCUSDT hourly data with the RSI strategy and produces results.

**Required capabilities**:
1. Identify common sources of look-ahead bias (centering, full-series indicator computation, signal without shift)
2. Build a data replay interface that prevents strategies from accessing future bars
3. Compute RSI incrementally or on the available-so-far window only
4. Write a verification test that proves no look-ahead exists
5. Demonstrate the impact: show how results differ between a correct engine and a deliberately leaking one

**Student openings**:
- **beginner_no_finance**: "Someone told me my backtest results were 'too good to be true' because of something called look-ahead bias. What is that, and how do I make sure my code doesn't have it?"
- **intermediate_developer**: "I built a backtester but I'm not confident it's free of look-ahead bias. How can I structurally prevent it and write tests to verify?"
- **advanced_quant**: "I need to build a backtest engine with formal look-ahead prevention guarantees. I want an architecture where it's impossible for the strategy to access future data, plus a verification harness. What's the approach?"

**Environment**:
```json
{
  "data_files": ["BTCUSDT_1h_2023_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["backtesting_101.md"],
  "network_enabled": false
}
```

**Convenient tools**: `fetch_market_data`, `compute_indicator`, `plot_chart`

**Eval strategy**:
- **Verification test exists** (programmatic): Check that workspace contains a test script or test function that explicitly checks for look-ahead.
- **Look-ahead-free architecture**: Strategy code does not have access to the full dataset — uses an iterator, callback, or restricted view.
- **Incremental computation**: RSI is computed on available data only, not on the full series with retroactive access.
- **Impact demonstration**: Conversation or workspace shows a comparison between correct and leaking results.

---

### 3.3 B04 — Multi-Asset Synchronized Replay

**Difficulty**: hard
**Category**: backtest

**Core idea**: Given daily data for both BTCUSDT and ETHUSDT plus a spread/pairs trading idea, teach the student to build a backtest engine that handles synchronized multi-asset data replay with proper timestamp alignment.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`, `ETHUSDT_1d_2021_2024.csv`
- Trading idea: "BTC/ETH ratio mean reversion — when the BTC/ETH price ratio deviates more than 2 standard deviations from its 30-day mean, trade the convergence."

**Description**: Guide a student to extend a backtest engine to support multiple assets with synchronized bar replay. The engine must align timestamps across assets, handle missing bars (if one asset has a gap the other doesn't), and provide the strategy with a consistent multi-asset view at each timestep. The strategy implements a BTC/ETH ratio mean reversion approach.

**Expected outcome**: Student produces a backtest system where (1) the data layer loads and time-aligns BTCUSDT and ETHUSDT daily data, handling any timestamp mismatches, (2) the engine replays synchronized bar tuples (one bar per asset per timestep), (3) the strategy receives both current bars simultaneously and computes a ratio-based signal, and (4) the engine tracks positions in both assets with separate PnL accounting. The system runs end-to-end and reports per-asset and combined performance.

**Required capabilities**:
1. Load and time-align multiple asset data series (handle missing bars, different start/end dates)
2. Build a synchronized multi-asset replay mechanism (engine yields aligned bar tuples)
3. Track positions and PnL separately per asset
4. Implement a ratio-based mean reversion strategy that operates on synchronized data
5. Produce per-asset and portfolio-level performance metrics

**Student openings**:
- **beginner_no_finance**: "I have Bitcoin and Ethereum price data. I heard you can trade the ratio between them. But how do I even backtest something that involves two assets at the same time?"
- **intermediate_developer**: "I need to build a multi-asset backtester. My current engine handles one asset at a time. How should I restructure it to replay multiple assets in sync and track positions per asset?"
- **advanced_quant**: "I'm building a multi-asset backtest engine for crypto futures. I need synchronized bar replay with proper timestamp alignment, per-asset position tracking, and a clean multi-asset strategy API. What's the architecture?"

**Environment**:
```json
{
  "data_files": ["BTCUSDT_1d_2021_2024.csv", "ETHUSDT_1d_2021_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["backtesting_101.md", "statistical_tests.md"],
  "network_enabled": false
}
```

**Convenient tools**: `fetch_market_data`, `compute_statistics`, `plot_chart`

**Eval strategy**:
- **Multi-asset data alignment**: Programmatic check that the data handler merges/aligns timestamps correctly (no NaN rows from misalignment).
- **Synchronized replay**: Strategy receives both asset bars per timestep; no sequential single-asset processing.
- **Per-asset accounting**: Engine tracks positions, PnL, and trade log per asset independently.
- **End-to-end execution**: System runs and produces combined portfolio metrics.

---

### 3.4 B05 — Execution Simulation & Futures Mechanics

**Difficulty**: hard
**Category**: backtest

**Core idea**: Given BTCUSDT data at multiple timeframes and funding rate data, teach the student to build a backtest engine with realistic execution simulation: slippage modeling, maker/taker fee differentiation, and futures-specific funding rate accounting.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`, `BTCUSDT_1h_2023_2024.csv`, `BTCUSDT_5m_2024Q4.csv`, `BTCUSDT_funding_2021_2024.csv`
- Trading idea: "Momentum breakout — enter long when price breaks above the 20-bar high, exit when it drops below the 10-bar low."

**Description**: Guide a student to build a backtest engine with realistic execution simulation for crypto futures. The engine should model slippage (based on volume or fixed basis points), differentiate between maker and taker fees, account for funding rate payments on open positions, and optionally use higher-timeframe data to estimate fill quality. The strategy implements a momentum breakout system.

**Expected outcome**: Student produces a backtest system where (1) the engine has a configurable fill model — at minimum, fixed slippage in basis points; ideally, volume-aware slippage, (2) fees are modeled with separate maker/taker rates (e.g., Binance Futures: 0.02% maker, 0.04% taker), (3) funding rate payments are deducted/credited every 8 hours for open positions, (4) the strategy implements a breakout system, and (5) the system runs and reports gross vs net performance, showing the impact of each cost component separately.

**Required capabilities**:
1. Build a fill/execution simulator with configurable slippage (fixed bps or volume-based)
2. Model maker/taker fee differentiation (limit orders vs market orders)
3. Account for funding rate payments on open futures positions at 8-hour intervals
4. Break down performance into gross, net-of-fees, and net-of-fees-and-funding components
5. Explain why execution simulation matters and how it changes strategy viability assessment

**Student openings**:
- **beginner_no_finance**: "I built a simple backtester but someone told me I'm ignoring trading fees and slippage. What are those and how do I add them?"
- **intermediate_developer**: "I need to add realistic execution simulation to my futures backtester — slippage, maker/taker fees, and funding rates. How should I structure the fill model?"
- **advanced_quant**: "I'm building a futures backtest engine with a full execution simulator: volume-aware slippage, maker/taker fee differentiation, and 8-hour funding rate accounting. I want to decompose PnL into alpha, execution cost, and carry components. What's the architecture?"

**Environment**:
```json
{
  "data_files": [
    "BTCUSDT_1d_2021_2024.csv",
    "BTCUSDT_1h_2023_2024.csv",
    "BTCUSDT_5m_2024Q4.csv",
    "BTCUSDT_funding_2021_2024.csv"
  ],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["backtesting_101.md", "risk_metrics.md", "crypto_futures_basics.md"],
  "network_enabled": false
}
```

**Convenient tools**: `fetch_market_data`, `compute_indicator`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Slippage model exists**: Programmatic check that fill price ≠ bar close in trade log (slippage applied).
- **Fee model**: Check that PnL accounting includes fee deductions; ideally, maker/taker rates are configurable.
- **Funding rate**: Check that 8-hour funding payments appear in the PnL breakdown for held positions.
- **Gross vs net comparison**: Workspace or conversation shows performance with/without costs.

---

### 3.5 B06 — Walk-Forward Engine with Strategy Optimization

**Difficulty**: hard
**Category**: backtest

**Core idea**: Given BTCUSDT long-history daily data and a parameterized MA crossover idea, teach the student to build a walk-forward validation framework on top of the backtest engine — rolling train/test windows where the strategy is optimized in-sample and evaluated out-of-sample.

**Materials provided**:
- Data: `BTCUSDT_1d_2021_2024.csv`
- Trading idea: "Parameterized MA crossover — fast window and slow window are tunable. Find the best parameters via walk-forward optimization."

**Description**: Guide a student to build a walk-forward validation framework that wraps the backtest engine. The framework splits data into rolling train/test windows, optimizes strategy parameters (MA fast/slow windows) on each training period, then runs the optimized strategy on the subsequent test period. Aggregate out-of-sample results are reported separately from in-sample results, and the student should understand why this prevents overfitting.

**Expected outcome**: Student produces a walk-forward framework where (1) data is split into N rolling windows with configurable train/test sizes, (2) for each window, the strategy parameters are optimized on the training period by running multiple backtests with different parameter combinations, (3) the best parameters are applied to the test period, (4) out-of-sample test results are concatenated across all windows, and (5) the system reports both in-sample (aggregated training performance) and out-of-sample (aggregated test performance) metrics separately, demonstrating any overfitting gap.

**Required capabilities**:
1. Implement rolling-window data splitting with configurable train/test sizes
2. Build a parameter optimization loop that runs multiple backtests per training window
3. Apply optimal parameters to out-of-sample test periods
4. Aggregate and report in-sample vs out-of-sample performance separately
5. Explain why walk-forward validation prevents overfitting and why simple train/test splits are insufficient for time series

**Student openings**:
- **beginner_no_finance**: "I tried different moving average settings and found one that works amazingly well. But I'm worried I just got lucky. Is there a way to properly test if my parameters are actually good?"
- **intermediate_developer**: "I want to build a walk-forward optimization framework for my backtester. I understand the concept — rolling train/test windows — but I'm not sure how to structure it cleanly on top of my existing engine."
- **advanced_quant**: "I need a walk-forward validation harness for my futures backtest engine. Rolling windows, parameter grid search on each train fold, OOS evaluation on each test fold, with aggregated IS vs OOS reporting. What's the cleanest architecture?"

**Environment**:
```json
{
  "data_files": ["BTCUSDT_1d_2021_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"],
  "docs_available": ["backtesting_101.md", "risk_metrics.md", "moving_averages.md"],
  "network_enabled": false
}
```

**Convenient tools**: `fetch_market_data`, `compute_indicator`, `run_backtest`, `analyze_backtest_results`, `plot_chart`

**Eval strategy**:
- **Rolling windows**: Programmatic check that multiple train/test periods exist (not a single split).
- **Parameter variation**: Check that multiple parameter combinations are tested per window.
- **OOS separation**: In-sample and out-of-sample results are tracked and reported separately.
- **Overfitting gap**: System explicitly compares IS vs OOS performance.

---

## 4. Difficulty & Capability Progression

```
B01  Interpret Metrics              easy      Conceptual understanding of metrics
 │                                             (existing, stock data, no code)
 ▼
B02  Basic Sequential Engine        medium    Architecture: data / engine / strategy separation
 │                                             (1 asset, daily, MA crossover)
 ▼
B03  Look-Ahead Prevention          medium    Correctness: no future data leakage + verification
 │                                             (1 asset, hourly, RSI, verification tests)
 ▼
B04  Multi-Asset Sync Replay        hard      Scale: synchronized multi-asset replay
 │                                             (2 assets, daily, ratio strategy)
 ▼
B05  Execution Simulation           hard      Realism: slippage, fees, funding rates
 │                                             (1 asset, multi-timeframe, breakout)
 ▼
B06  Walk-Forward Validation        hard      Robustness: overfitting prevention framework
                                               (1 asset, daily, parameterized MA)
```

**Concept progression**:
- B02 establishes the fundamental three-layer architecture
- B03 adds correctness guarantees (the engine must be *provably* right)
- B04 adds engineering complexity (multiple data streams synchronized)
- B05 adds domain complexity (realistic futures execution mechanics)
- B06 adds methodological sophistication (validation framework wrapping the engine)

Each task builds on the concepts from earlier tasks, but is independently executable — the student does not need to literally reuse code from a previous task.

---

## 5. Evaluation Architecture

### 5.1 Programmatic Eval Focus (per-task eval scripts)

Unlike D-series (which checks data output correctness) or A-series (which checks safety refusal), B-series eval scripts primarily check **architectural quality**:

| Check Type | Method | What It Catches |
|------------|--------|-----------------|
| **Structural separation** | AST-parse workspace `.py` files; check for distinct classes/functions for data, engine, strategy | Monolithic scripts with no separation |
| **Look-ahead prevention** | Check that strategy code does not directly import pandas or access full DataFrames; check for iterator/callback patterns | Strategies that read the entire price series |
| **Execution correctness** | Run the produced system via `shell_exec`; verify output contains expected metrics (return, trade count, Sharpe) | Systems that don't actually execute |
| **Futures mechanics** | Parse trade logs for slippage, fees, funding entries (B05-specific) | Missing cost modeling |
| **Walk-forward structure** | Check for multiple train/test period outputs (B06-specific) | Single-split or full-sample-only backtests |

Important: programmatic B-series checks should be artifact-bound. Conversation-only mentions of "look-ahead", "slippage", or "walk-forward" should not earn major credit without code or output artifacts that implement those ideas.

### 5.2 Structural Separation Heuristics

The most important and novel eval dimension for B-series. Heuristics for programmatic checking:

```python
# Heuristic 1: Multiple .py files in workspace (separation into modules)
py_files = [f for f in os.listdir(workspace) if f.endswith('.py')]
has_multiple_files = len(py_files) >= 2

# Heuristic 2: Class-based separation (even in a single file)
# AST-parse for classes with names suggesting separation
import ast
for f in py_files:
    tree = ast.parse(open(f).read())
    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    # Look for patterns like: DataHandler, Engine/Backtester, Strategy
    # Or function-based: load_data, run_backtest, strategy_logic

# Heuristic 3: Strategy isolation
# The strategy class/function should NOT:
#   - import pandas (it receives processed bars, not raw DataFrames)
#   - open/read CSV files directly
#   - access a variable named 'df' or 'data' that contains the full series
```

These heuristics are imperfect but provide a meaningful programmatic signal. The LLM Result Judge provides the complementary qualitative assessment.

### 5.3 Result Judge Category Rubric

Add a `backtest` entry to `CATEGORY_RESULT_RUBRICS`:

```
Backtest tasks — evaluation focus:
1. Architecture Quality: Does the system have clean separation between data, engine, and strategy layers?
2. Correctness: Does the backtest engine produce plausible results? (positive trade count, reasonable return range)
3. Engineering Rigor: Are there safeguards against look-ahead? Is execution realistic?
4. Completeness: Does the system run end-to-end and produce the required outputs?
```

### 5.4 Process Reasonableness Criteria

Add to `CATEGORY_PROCESS_CRITERIA`:

```
backtest: "design architecture → implement data layer → implement engine → implement strategy →
           integrate and run → verify correctness → report results"
```

The expected process is architecture-first (discuss the design before coding), then bottom-up implementation (data layer → engine → strategy → integration).

For the hard tasks (B04-B06), add explicit score caps when the core engineering artifact is missing:
- No synchronized multi-asset replay or per-asset accounting artifact → cap near failing on B04
- No slippage/fee/funding implementation artifact or no gross-vs-net breakdown artifact → cap near failing on B05
- No rolling-window walk-forward artifact or no IS/OOS separation artifact → cap near failing on B06

---

## 6. Reference Documentation

### 6.1 Existing Docs (Reusable)

- `backtesting_101.md` — Already covers backtesting concepts, look-ahead bias, walk-forward. Core reference for all B-tasks.
- `risk_metrics.md` — Performance metrics (Sharpe, drawdown, etc.). Used by B01, B05, B06.
- `moving_averages.md` — MA/EMA computation. Used by B02, B06.
- `statistical_tests.md` — Cointegration, correlation. Used by B04.

### 6.2 New Docs Shared with S-Series

S-series introduces two new reference docs that B-series can optionally reference:
- `alpha_research_methodology.md` — Research process, hypothesis-driven development. Primarily for S-series, but relevant to B06 (walk-forward as overfitting prevention).
- `signal_evaluation.md` — IC, decay, turnover metrics. Primarily for S-series, but useful context for B-series when discussing what the engine needs to validate.

### 6.3 New Doc Required: `crypto_futures_basics.md`

A general-purpose reference covering crypto futures concepts. **NOT task-specific** (per v2.0 doc design principles).

Suggested structure:
```markdown
# Crypto Futures: A Practical Guide

## 1. What Are Perpetual Futures?
- No expiry date (unlike traditional futures)
- Funding rate mechanism to anchor to spot price

## 2. USDT-M vs Coin-M
- USDT-margined: settled in USDT, linear payoff
- Coin-margined: settled in the base asset, inverse payoff

## 3. Funding Rates
- Paid/received every 8 hours
- Positive rate: longs pay shorts
- Negative rate: shorts pay longs
- Formula: position_size × mark_price × funding_rate

## 4. Fee Structure
- Maker fee (limit orders): typically 0.02%
- Taker fee (market orders): typically 0.04%
- Fee = notional_value × fee_rate

## 5. Kline (Candlestick) Data
- OHLCV fields explained
- Quote volume vs base volume
- Taker buy volume interpretation

## 6. Slippage and Market Impact
- What causes slippage
- Modeling approaches (fixed bps, volume-based)

## 7. Python: Loading Binance Kline CSVs
- Parsing timestamps
- Column mapping
- Handling timezone (UTC)
```

This doc would be listed in `docs_available` for B05 (execution simulation). Other B-tasks can include it optionally.

---

## 7. Persona Considerations

### 7.1 All B-tasks Use Three Personas

Unlike adversarial tasks (which use targeted persona subsets), all B-tasks use the standard three personas. The engineering concepts scale naturally with persona level:

| Persona | What they focus on | Tutor should adapt by... |
|---------|-------------------|--------------------------|
| **beginner_no_finance** | "What is a backtester? Why can't I just look at the chart?" | Explaining concepts from first principles; using analogies; building step by step |
| **intermediate_developer** | "I know Python, I can build it — but is my architecture right?" | Focusing on design decisions; reviewing code structure; pointing out subtle issues |
| **advanced_quant** | "I want production-grade architecture with provable guarantees." | Discussing tradeoffs (event-driven vs vectorized); formal look-ahead prevention; edge cases |

### 7.2 Student Opening Design

Per v2.0 guidelines, openings must be restrained (one entry point only). For B-tasks, the natural entry point is the *starting question about architecture*, not the full system specification:

```
GOOD (beginner, B02):
  "I have some Bitcoin price data and I want to test a moving average strategy on it.
   I've never built a backtester before — where do I start?"

BAD (beginner, B02):
  "I want to build a three-layer backtest system with a data replay module, an engine
   with position tracking, and a strategy module, all as separate components."
```

---

## 8. Task Summary Table

| Task | Title | Difficulty | Data | Strategy Idea | Key Architecture Concept | New Eval Instances |
|------|-------|-----------|------|---------------|--------------------------|-------------------|
| B01 | Interpret Metrics | easy | AAPL, SPY | — | (conceptual) | 3 (existing) |
| B02 | Basic Sequential Engine | medium | BTCUSDT 1d | MA crossover | Data/Engine/Strategy separation | 3 |
| B03 | Look-Ahead Prevention | medium | BTCUSDT 1h | RSI mean reversion | Provable no-lookahead + verification | 3 |
| B04 | Multi-Asset Sync Replay | hard | BTCUSDT+ETHUSDT 1d | Ratio mean reversion | Synchronized multi-asset replay | 3 |
| B05 | Execution Simulation | hard | BTCUSDT multi-TF + funding | Momentum breakout | Slippage, fees, funding rate | 3 |
| B06 | Walk-Forward Validation | hard | BTCUSDT 1d | Parameterized MA | Rolling IS/OOS framework | 3 |

**Total new instances**: 5 tasks × 3 personas = **15 new evaluation instances**
**B-series total**: 6 tasks × 3 personas = **18 evaluation instances**

---

## 9. Implementation Checklist

### Phase 0: Shared Dependencies (with S-Series)
- [ ] Write `bench/scripts/download_binance_klines.py` (shared data download script)
- [ ] Download and freeze all data files listed in §2.2 (shared across S+B)
- [ ] Validate frozen CSVs (no gaps, correct columns, standardized names)
- [ ] Write `crypto_futures_basics.md` reference doc (shared)
- [ ] Ensure S-series new docs are written first (`alpha_research_methodology.md`, `signal_evaluation.md`) — B-series optionally references them
- [ ] Ensure S-series new tool is implemented first (`evaluate_signal`) — B-series doesn't use it, but shared sandbox needs it

### Phase 1: Task JSONs (bench/tasks/layer2/backtest/)
- [ ] B02_basic_sequential_engine.json
- [ ] B03_lookahead_prevention.json
- [ ] B04_multi_asset_sync.json
- [ ] B05_execution_simulation.json
- [ ] B06_walkforward_validation.json

### Phase 2: Eval Scripts (bench/evaluation/test_scripts/)
- [ ] B02_basic_sequential_engine.py — structural separation + execution check
- [ ] B03_lookahead_prevention.py — verification test existence + architecture check
- [ ] B04_multi_asset_sync.py — timestamp alignment + per-asset accounting
- [ ] B05_execution_simulation.py — slippage/fee/funding checks in trade log
- [ ] B06_walkforward_validation.py — rolling window + IS/OOS separation

### Phase 3: Scoring Integration
- [ ] Add `backtest` category to `CATEGORY_PROCESS_CRITERIA`
- [ ] Add `backtest` category to `CATEGORY_RESULT_RUBRICS`
- [ ] Add `backtest` category dimension weights to tutor 7D (adjust D5 code teaching weight upward — these are code-heavy tasks)
- [ ] Verify that `code_process` dimension works correctly for multi-file workspace outputs

### Phase 4: Reference Oracle
- [ ] Generate reference executions for all 15 instances (5 tasks × 3 personas)
- [ ] Validate reference `key_results` and `step_count` baselines

---

## 10. Cross-Reference: Full Pipeline View (S + B)

```
                     S-SERIES (Research)                    B-SERIES (Engineering)
                     ─────────────────                     ──────────────────────

                     S01  MA Crossover (easy)               B01  Interpret Metrics (easy)
                          [prescriptive, stock data]             [conceptual, stock data]

Data: BTCUSDT 1d    S02  Trend Research (med)          →   B02  Basic Engine (med)
                         explore trends → signal                build data/engine/strategy layers

Data: BTCUSDT 1d    S03  Mean-Reversion Research (med) →   B03  Look-Ahead Prevention (med)
                         explore reversion → signal             prove no future data leak

Data: BTCUSDT       S04  Volume/Microstructure (hard)  →   B05  Execution Simulation (hard)
      multi-TF           non-price features → signal            slippage, fees, funding

Data: BTC+ETH       S05  Cross-Asset Alpha (hard)      →   B04  Multi-Asset Sync (hard)
      multi-TF           cross-asset dynamics → signal          synchronized replay

Data: BTC+ETH       S06  Multi-Signal Combo (hard)     →   B06  Walk-Forward (hard)
      + funding          composite alpha → signal               rolling IS/OOS validation

                     ─────────────────                     ──────────────────────
                     Output: validated alpha idea           Output: production-grade engine
                     (IC, rough Sharpe, failure modes)      (no look-ahead, execution sim)
```
