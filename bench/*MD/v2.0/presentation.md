# QuantTutorBench v2.0: A Two-Axis Benchmark for Quantitative Finance Tutoring Agents

> Technical Presentation | March 2026

---

## 1. Scoring Architecture Overview

QuantTutorBench evaluates agents along **two axes**: quantitative finance expertise (70%) and tutoring pedagogy (30%), using a multi-layer composite scoring system.

```
                     +-------------------------------+
                     |   Overall Agent Score (OAS)    |
                     |  = 0.70 x QAI + 0.30 x TEI    |
                     +-------+--------------+--------+
                             |              |
               +-------------+              +--------------+
               v                                           v
    +---------------------+                  +---------------------+
    | Quant Agent Index    |                  | Tutoring Effect.    |
    | (QAI) -- 70%         |                  | Index (TEI) -- 30%  |
    | = 0.50xQR + 0.50xQP |                  | = mean(D1..D7)      |
    +----+------------+----+                  +----------+----------+
         |            |                                  |
         v            v                                  v
   +----------+ +----------+              +--------------------------+
   | QR Result| | QP Process|             | 7D Rubric (per persona)  |
   | 3-Source  | | 7 Weighted|             | D1: Level Detection      |
   | Fusion   | | Dimensions|             | D2: Language Adaptation  |
   +----------+ +----------+              | D3: Scaffolding          |
                                          | D4: Domain Accuracy      |
                                          | D5: Code Teaching        |
                                          | D6: Empathetic Response  |
                                          | D7: Safety & Boundaries  |
                                          +--------------------------+
```

---

## 2. Key Updates from v1.0

### 2.1 QR (Quant Result): Three-Source Fusion Scoring

v1.0 used only hand-written eval scripts for programmatic scoring. v2.0 decomposes QR into three independent signal sources with weighted fusion:

| Signal Source | Evaluation Method | Focus |
|---------------|-------------------|-------|
| **Programmatic Eval Script** | Per-task hand-written checklist | Deterministic: file existence, numeric ranges, keyword checks |
| **Code Execution Eval** | Three-layer code analysis | Does the code run? Are the results correct? |
| **LLM Result Judge** | Multi-model LLM assessment | Completeness (55%) + Correctness (45%) |

**Code Execution Eval -- Three-Layer Structure:**
- **Layer A -- Static Analysis (15%)**: AST-parses .py files from workspace; checks syntax, function count
- **Layer B -- Execution Verification (35%)**: Parses shell_exec tool results; checks actual execution success rate, detects untested files
- **Layer C -- Output Verification (50%)**: Compares code execution outputs against Reference baseline data using relative error thresholds

**QR Fusion Formula** (code tasks):

```
QR = w_prog x Programmatic + 0.30 x Code_Eval + w_judge x LLM_Judge
```

where `w_prog` and `w_judge` are dynamically adjusted by a dampening factor (divergence between Programmatic and Judge scores), preventing over-weighting when two independent signal sources conflict.

### 2.2 QP (Quant Process): Seven-Dimension Restructuring

v1.0 used 8 tool-bound metrics (tool_correctness, argument_correctness, mcp_use, etc.), which introduced tool-choice bias -- an agent that writes equivalent Python code via shell_exec instead of calling a dedicated tool would be unfairly penalized.

v2.0 removes all 4 tool-bound metrics, replacing them with **tool-agnostic process quality assessment**:

| Dimension | Weight | Method | Description |
|-----------|--------|--------|-------------|
| **tool_usage** | 20% | Pure math | Selection & effectiveness of core/convenient/distractor tools |
| **process_reasonableness** | 20% | LLM-judged | Problem decomposition, execution soundness, error handling |
| **step_efficiency** | 15% | Hybrid | Action economy, redundancy avoidance, logical sequencing |
| **code_process** | 15% | Hybrid | Iterative refinement, test-before-deliver, debugging competence |
| **process_alignment** | 10% | LLM-judged | Alignment with Reference execution trace |
| **role_adherence** | 10% | LLM-judged | Maintains tutor role throughout |
| **topic_adherence** | 10% | LLM-judged | Stays on quantitative finance topics |

**Key design principle:** Custom Python code via shell_exec is treated as equivalent to calling a dedicated tool (no tool-choice bias).

### 2.3 Tutor 7D: Dual-Channel Conversation Input

v2.0 introduces a **dual-channel conversation input** mechanism for the 7D Tutor evaluation:

- **Original conversation**: Pure student-tutor text exchange, used for D1 (Level Detection), D2 (Language Adaptation), D3 (Scaffolding), D6 (Empathetic Response) -- these dimensions assess only teaching interaction quality and do not need tool execution details
- **Enriched conversation**: Appends `[Tool Activity]` summaries after assistant replies, used for D4 (Domain Accuracy), D5 (Code Teaching), D7 (Safety Boundaries) -- these dimensions need to verify the correctness of code/tool execution

Each dimension is evaluated 3 times with randomized dimension ordering (shuffle judge), averaged to reduce LLM-as-Judge positional bias.

### 2.4 Reference Baseline System (Infrastructure Built, Pending Full Integration)

v2.0 introduces Reference (baseline execution) infrastructure:

- `bench/reference/generate_reference.py`: CLI tool that executes a "gold-standard run" for each task x persona
- `bench/reference/reference_store.py`: Save/load/list reference data per combination
- Reference data includes: trace_summary, step_count, key_results, workspace_files, full_trace

**Reference integration points in evaluation:**
- Code Eval Layer C: Compares code outputs against reference key_results numerically
- Step Efficiency: action_economy uses `agent_steps / reference_steps` ratio thresholds
- Process Alignment: Evaluates trace alignment against reference execution
- Result Judge: Compares output files and numerical results when reference is available

> **Current status**: Reference generation infrastructure is complete, but reference data has not yet been generated for all D/S/B/I tasks. Current evaluation runs in no-reference mode (affected dimensions gracefully degrade to standalone assessment).

### 2.5 Full-Container Sandbox (Docker v2.2)

v1.0 routed only shell_exec into the container. v2.0 routes **all core tools** through the container:

```
Agent <-> MCP Proxy <-> tool_executor.py (in-container JSON-RPC daemon) <-> tools.py
```

- In-container `tool_executor.py` daemon communicates via stdin/stdout JSON-lines protocol
- All core tool calls serialized through `call_tool_in_container()` (thread-safe)
- Per-call timeout via signal.alarm()
- Resource limits: Standard image 768MB / 1 CPU; LEAN image 1GB / 2 CPU

### 2.6 Result Persistence System

v2.0 introduces a complete execution result persistence mechanism:

| File | Content | Generated When |
|------|---------|----------------|
| `scores.md` | Full score report (QR/QP/7D breakdown) | `--save-result` |
| `trace.md` | Full execution trace (conversation + tool calls) | `--save-result` |
| `cost.md` | Token usage and cost breakdown (Agent/Simulator/Eval) | `--save-result` |
| `agent_files/` | Copy of agent-produced workspace files (CSV, PNG, etc.) | `--save-result` |
| `run_state.json` | Reproducible execution state snapshot | `--runonly` |

**Two-phase execution mode:**
- `--runonly`: Run agent interaction only, save run_state.json (skip evaluation)
- `--evalonly`: Load run_state.json, run evaluation pipeline only (reproduce scores)

### 2.7 Token-Level Cost Tracking

v2.0 implements end-to-end token-level cost tracking from agent to evaluation:

- **Agent cost**: Each adapter extracts usage from API responses (input/output tokens)
- **Simulator cost**: DeepEval ConversationSimulator student message generation cost
- **Evaluation cost**: Per-model cost from each LLM-as-Judge evaluator (Tutor 7D, Process Metrics, Result Judge)

---

## 3. Tool System

### 3.1 Core Tools (12)

Each task selects a subset from the following core tools:

| Tool | Function |
|------|----------|
| `shell_exec` | Execute shell commands in the container (supports Python scripts) |
| `file_write` | Write files to workspace |
| `file_read` | Read files from workspace/data/docs directories |
| `file_list` | List directory contents |
| `get_environment_info` | Get environment paths and available files |
| `fetch_market_data` | Fetch OHLCV data from frozen CSV |
| `compute_indicator` | Compute technical indicators (SMA/EMA/RSI/BOLL/MACD) |
| `run_backtest` | Run built-in strategy backtests |
| `compute_statistics` | Statistical tests (ADF/correlation/cointegration/descriptive) |
| `plot_chart` | Execute matplotlib code to generate charts |
| `analyze_backtest_results` | Analyze backtest returns and compute performance metrics |
| `evaluate_signal` | Evaluate signal quality (IC/quantile/PnL) |

Additionally, `search_web` and `search_docs` are available for web/documentation search.

### 3.2 Convenient Tools and Distractor Tools

- **Convenient tools**: Non-essential but workflow-simplifying tools (e.g., plot_chart, compute_indicator). Using them earns a bonus (+0.05/n); not using them incurs no penalty
- **Distractor tools** (10 total): Task-irrelevant tools (compute_var, fit_garch_model, optimize_portfolio, run_monte_carlo, fetch_fundamentals, compute_greeks, screen_stocks, backtest_pairs_trade, compute_beta, estimate_covariance, fetch_live_price, query_database, fetch_news_sentiment) randomly sampled from a global pool to fill 15 total tool slots. Calling a distractor incurs a -0.10 penalty per tool

---

## 4. Verified Task Sets

### 4.1 D-Series -- Data Analysis (11 Tasks)

The D-series covers the complete quantitative finance data workflow lifecycle: from data loading and cleaning to feature engineering.

| Task ID | Difficulty | Description | Core Assessment | Data Files |
|---------|------------|-------------|-----------------|------------|
| **D01** | Easy | Load and explore OHLCV stock data | pandas loading, column meanings, basic EDA | AAPL, SPY (2018-2024) |
| **D02** | Easy | Detect and handle missing values in OHLCV data | Distinguish market-closure gaps from data-feed issues | AAPL_dirty |
| **D03** | Easy | Data type conversion and schema validation | datetime parsing, numeric coercion, timezone handling | AAPL_dirty |
| **D04** | Easy | Compute and interpret summary statistics | describe(), distribution diagnostics, volume analysis | AAPL, SPY (2018-2024) |
| **D05** | Medium | Compute simple returns vs log returns | Compounding, aggregation properties, use-case comparison | AAPL, SPY (2018-2024) |
| **D06** | Medium | Aggregate tick data into OHLCV bars | resample, timestamp handling, microstructure QC | tick_data_sample |
| **D07** | Hard | Diagnose multiple data-feed issues | Anomaly detection, root-cause mapping, remediation checklist | AAPL_dirty |
| **D08** | Hard | Align alternative data with price data | Frequency alignment, look-ahead safeguards, information content testing | AAPL + sentiment_data |
| **D09** | Medium | Build a feature-engineering pipeline | Multicollinearity detection, look-ahead leakage prevention | AAPL, SPY (2018-2024) |
| **D10** | Easy | Fetch historical data from public APIs | API calls, adjusted prices, data validation | None (network-enabled) |
| **D11** | Medium | Collect realtime market data stream | Streaming/polling, timestamped storage, microstructure | None (network-enabled) |

**Difficulty distribution**: Easy x4 / Medium x4 / Hard x3

**Design progression**: D01-D04 (basic data operations) -> D05-D06 (returns & aggregation) -> D07-D08 (data quality diagnosis & multi-source fusion) -> D09 (feature engineering) -> D10-D11 (live data streams)

### 4.2 S-Series -- Strategy Research (6 Tasks)

The S-series covers the complete strategy research pipeline from simple strategy construction to composite alpha signal synthesis.

| Task ID | Difficulty | Description | Core Assessment | Data Files |
|---------|------------|-------------|-----------------|------------|
| **S01** | Easy | Build and test an MA crossover strategy | MA strategy design, backtest execution, visualization | AAPL, SPY (2018-2024) |
| **S02** | Medium | BTC trend-following alpha research | Hypothesis formation, signal construction, IC/quantile/PnL evaluation | BTCUSDT 1d (2021-2024) |
| **S03** | Medium | BTC mean-reversion alpha research | Reversion signal construction, trending-regime failure analysis | BTCUSDT 1d (2021-2024) |
| **S04** | Hard | Volume/microstructure alpha research | Non-price features (volume/taker imbalance), cross-timeframe analysis | BTCUSDT 1d/1h/5m |
| **S05** | Hard | BTC-ETH cross-asset alpha research | Rolling correlation, cointegration, lead-lag, dollar-neutral strategy | BTCUSDT + ETHUSDT 1d/1h |
| **S06** | Hard | Multi-signal composite alpha synthesis | Signal correlation analysis, combination methods, IC IR improvement | BTCUSDT + ETHUSDT + Funding |

**Difficulty distribution**: Easy x1 / Medium x2 / Hard x3

**Design progression**: S01 (introductory strategy) -> S02-S03 (single-factor alpha methodology) -> S04-S05 (multi-dimensional alpha discovery) -> S06 (multi-signal combination & portfolioization)

### 4.3 B-Series -- Backtesting (6 Tasks)

The B-series covers the full chain from backtest metric interpretation to complete backtest engine architecture.

| Task ID | Difficulty | Description | Core Assessment | Data Files |
|---------|------------|-------------|-----------------|------------|
| **B01** | Easy | Interpret basic backtest metrics | Sharpe/return/drawdown meaning, overfitting recognition | AAPL, SPY (2018-2024) |
| **B02** | Medium | Build a basic sequential backtest engine | Data replay / engine / strategy three-layer separation, look-ahead prevention | BTCUSDT 1d (2021-2024) |
| **B03** | Medium | Build a look-ahead-proof backtest engine | Bar-by-bar replay interface, incremental RSI, bias verification test | BTCUSDT 1h (2023-2024) |
| **B04** | Hard | Multi-asset synchronized backtest engine | Time alignment, ratio signal, per-asset independent PnL accounting | BTCUSDT + ETHUSDT 1d |
| **B05** | Hard | Execution simulation backtest engine | Slippage/fee/funding-rate modeling, gross vs net performance | BTCUSDT 1d/1h/5m + Funding |
| **B06** | Hard | Walk-forward validation framework | Rolling train/test windows, parameter optimization, IS vs OOS comparison | BTCUSDT 1d (2021-2024) |

**Difficulty distribution**: Easy x1 / Medium x2 / Hard x3

**Design progression**: B01 (metric understanding) -> B02-B03 (engine fundamentals & look-ahead prevention) -> B04-B05 (multi-asset & execution simulation) -> B06 (validation framework)

### 4.4 I-Series -- LEAN Algorithm Implementation (10 Tasks)

The I-series tests the ability to implement quantitative strategies on the QuantConnect LEAN engine, scaling from single-asset to full-universe.

| Task ID | Difficulty | Description | Core Assessment | Data Files |
|---------|------------|-------------|-----------------|------------|
| **I01** | Easy | Implement single-symbol SMA strategy | AddCryptoFuture, SMA indicator, WarmUp handling | universe.json |
| **I02** | Medium | Full-universe dual MA crossover strategy | ~100 symbol dynamic subscription, per-symbol indicator state | universe.json |
| **I03** | Medium | Full-universe RSI mean-reversion strategy | Per-symbol state machine, stop-loss logic, risk-based sizing | universe.json |
| **I04** | Hard | Multi-timeframe strategy | Consolidate() for 4h bars, dual-timeframe signal coordination | universe.json |
| **I05** | Hard | Pairs trading strategy | Candidate pair loading, spread z-score, multi-leg position management | universe.json + pairs.json |
| **I06** | Hard | Multi-signal parameter sweep | Three-signal composite, funding data asymmetry, 21 backtest runs | universe.json |
| **I07** | Medium | Alpha Model framework migration | AlphaModel/Insight, SetAlpha pipeline wiring | universe.json |
| **I08** | Hard | Multi-alpha composition system | Three AlphaModels + AddAlpha, InsightWeighting vs EqualWeighting | universe.json |
| **I09** | Hard | Risk management model comparison | Custom RiskManagementModel, three-config drawdown comparison | universe.json |
| **I10** | Hard | Systematic parameter optimization | GetParameter(), ~180 parameter grid search | universe.json |

**Difficulty distribution**: Easy x1 / Medium x3 / Hard x6

**Design progression**: I01 (single-symbol intro) -> I02-I03 (full-universe scaling) -> I04-I06 (advanced strategy structures) -> I07-I10 (LEAN Algorithm Framework patterns)

---

## 5. Five-Phase Execution Lifecycle

```
Phase 1: RESET
  |-- 1a. Download HF dataset, create staged directories (hardlink/copy)
  |-- 1b. Create Docker sandbox container (resource + network isolation)
  |-- 1b.5 Start in-container tool_executor daemon
  |-- 1c. Configure MCP Proxy (core + convenient + distractor tools = 15)
  +-- 1d. Inject task/persona dynamic context into agent system prompt

Phase 2: INTERACT
  +-- DeepEval ConversationSimulator plays the student role
      |-- Agent responds via SDK adapter + MCP Proxy
      +-- Multi-turn dialogue continues to max_turns or goal achievement

Phase 3: CAPTURE
  |-- Record workspace file list
  |-- Execute pre_teardown_hook (save proxy logs + workspace copy)
  +-- Save sandbox metadata

Phase 4: EVALUATE (skippable via --runonly)
  |-- 4a. Eval Script programmatic assessment + data source verification
  |-- 4b. Code Eval three-layer analysis
  |-- 4c. LLM Result Judge multi-model assessment
  |-- 4d. QR three-source fusion
  |-- 4e. QP seven-dimension process evaluation (incl. tool_usage math scoring)
  +-- 4f. Tutor 7D dual-channel evaluation (3x shuffle, multi-model)

Phase 5: TEARDOWN
  |-- Destroy Docker container
  |-- Clean up staged temporary directories
  +-- Aggregate token costs
```

---

## 6. Scoring Formula Summary

```
OAS = 0.70 x QAI + 0.30 x TEI

QAI = 0.50 x QR + 0.50 x QP

QR  = w_prog x Programmatic + 0.30 x Code_Eval + w_judge x LLM_Judge
      (w_prog, w_judge dynamically adjusted by dampening factor)

QP  = 0.20 x tool_usage + 0.20 x process_reasonableness + 0.15 x step_efficiency
    + 0.15 x code_process + 0.10 x process_alignment + 0.10 x role_adherence
    + 0.10 x topic_adherence

TEI = mean(D1, D2, D3, D4, D5, D6, D7)
```

**Benchmark-Level KPIs:**

| KPI | Computation |
|-----|-------------|
| OAS (Overall Agent Score) | Mean of all task OAS values |
| QAI (Quant Agent Index) | Mean of all task QAI values |
| TEI (Tutoring Effectiveness Index) | Mean of all task TEI values |
| PMS (Process Mastery Score) | Mean of all task QP values |
| AS (Adaptiveness Score) | Mean of per-task tutor score variance across personas |

---

## 7. Task Scale Overview

| Series | Category | Tasks | x3 Personas = Instances | Difficulty Distribution |
|--------|----------|-------|------------------------|------------------------|
| **D** | Data Analysis | 11 | 33 | 4E / 4M / 3H |
| **S** | Strategy | 6 | 18 | 1E / 2M / 3H |
| **B** | Backtest | 6 | 18 | 1E / 2M / 3H |
| **I** | Implementation (LEAN) | 10 | 30 | 1E / 3M / 6H |
| **Total** | | **33** | **99** | 7E / 11M / 15H |

Each task is configured with 3 student personas (beginner_no_finance / intermediate_developer / advanced_quant), yielding **99 evaluation instances**, each executing the full five-phase lifecycle.
