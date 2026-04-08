# QuantTutorBench Specification v1.0

> **Status**: Draft
> **Date**: 2026-04-08
> **Scope**: This document defines the benchmark protocol. Any conforming agent
> can be evaluated — no dependency on the reference harness is required.

---

## 1. Overview

QuantTutorBench evaluates an LLM agent's ability to (1) produce correct
quantitative analysis results, (2) follow sound analytical processes, and
(3) effectively teach domain concepts to students of varying proficiency.

The agent under test acts as a **quantitative finance tutor**. It converses
with a simulated student while operating a sandboxed toolset (data analysis,
backtesting, code execution). Evaluation is post-hoc, based on observable
outputs only — no access to internal chain-of-thought is needed.

**Key design properties**:
- Agent ↔ Environment closed loop (actions change sandbox state)
- Benchmark specification is decoupled from the reference implementation
- Third-party agents interact through a single `respond()` interface
- Evaluation uses only observable behavior: conversation text, tool call
  logs, and workspace files

---

## 2. Task Schema

Each task is a JSON file conforming to the following schema. Tasks live in
`tasks/layer2/{category}/{task_id}.json`.

### 2.1 Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Unique identifier, e.g. `S01_ma_crossover` |
| `version` | string | no | Schema version (default `"1.0"`) |
| `difficulty` | enum | yes | `"easy"` \| `"medium"` \| `"hard"` |
| `category` | enum | yes | See §2.2 |
| `task_type` | enum | no | `"multi_turn"` (default) \| `"single_turn"` |
| `description` | string | yes | Task description shown to the agent |
| `persona_ids` | string[] | yes | Eligible persona IDs for this task |
| `student_openings` | object | yes | `{persona_id: opening_message}` |
| `environment` | object | no | Sandbox configuration (§2.3) |
| `ground_truth` | object | no | Evaluation targets (§2.4, **hidden from agent**) |
| `requires_code` | bool | no | Whether the task expects code output |
| `requires_tool` | bool | no | Whether the task requires tool use |
| `max_turns` | int | no | Max conversation turns (default 30) |
| `agent_max_steps` | int | no | Max tool-call steps per turn (default 10) |
| `timeout_minutes` | int | no | Wall-clock timeout (default 15) |

### 2.2 Categories

| Category | Count | Description |
|----------|-------|-------------|
| `data_analysis` | 11 | Load, inspect, transform financial data |
| `strategy` | 6 | Design and evaluate trading strategies |
| `implementation` | 10 | Implement strategies in LEAN C# |
| `backtest` | 6 | Run and interpret backtests |
| `debug` | 10 | Find and fix bugs in existing code |
| `end_to_end` | 5 | Full pipeline from data to backtest |
| `adversarial` | 17 | Safety boundary and robustness tests |

### 2.3 Environment Configuration

```json
{
  "data_files": ["AAPL_2018_2024.csv"],
  "core_mcp_tools": ["shell_exec", "file_read", "file_write", ...],
  "docs_available": ["indicators.md", "backtest_guide.md"],
  "sandbox_image": "quant-tutor-env:v2.2",
  "max_backtest_trials": 0,
  "network_enabled": false
}
```

- `sandbox_image`: `"quant-tutor-env:v2.2"` for Python tasks,
  `"quant-tutor-lean:v1.0"` for LEAN C# tasks
- `max_backtest_trials`: Trial budget for I-series tasks (0 = no trial system)
- `network_enabled`: Whether outbound internet is available inside sandbox

### 2.4 Ground Truth (Hidden)

These fields are **never** exposed to the agent. They drive evaluation and
student termination logic.

```json
{
  "expected_outcome": "Student understands SMA crossover ...",
  "termination_criteria": "(1) Computed SMA-20 and SMA-50 (2) ...",
  "required_capabilities": ["data_loading", "indicator_computation"],
  "expected_mcp_tools": ["fetch_market_data", "compute_indicator"],
  "convenient_tools": ["plot_chart"],
  "quant_validation": {"eval_script": "strategy/S01_ma_crossover.py"}
}
```

---

## 3. Environment Interface (Gym)

The benchmark is a **gym environment**. The agent controls the loop;
the environment provides tools and a simulated student.

```python
from bench.gym import QuantTutorEnv

env = QuantTutorEnv(use_docker=True)
obs = env.reset("S01_ma_crossover")

while not obs.done:
    # Agent decides: call tools, think, or reply
    result = env.call_tool("fetch_market_data", symbol="AAPL")
    result = env.call_tool("compute_indicator", data_path="AAPL.csv", indicator="SMA")

    obs = env.send_message("I've computed SMA-20 and SMA-50 for AAPL...")

scores = env.evaluate()
env.close()
```

### 3.1 API Reference

| Method | Returns | Effect |
|--------|---------|--------|
| `env.reset(task_id, persona_id?)` | `Observation` | Creates sandbox, returns student opening + tools |
| `env.call_tool(name, **kwargs)` | `str` | Executes tool in sandbox. **Does not advance conversation.** |
| `env.send_message(text)` | `Observation` | Sends agent reply → student responds → TC checked |
| `env.evaluate()` | `Scores` | Runs post-hoc evaluation on completed conversation |
| `env.close()` | `None` | Destroys sandbox, releases resources |

### 3.2 Observation

```python
@dataclass
class Observation:
    student_message: str       # Student's latest message
    available_tools: list[dict]  # Tool schemas agent can call
    done: bool                 # True when conversation should end
    turn: int                  # Current turn number
    max_turns: int             # Hard turn cap
    info: dict                 # Extra metadata (termination reason, TC coverage)
```

### 3.3 Scores

```python
@dataclass
class Scores:
    overall: float             # OAS (the headline number)
    quant_result: float        # QR sub-score
    quant_process: float       # QP sub-score
    quant_agent: float         # 0.50*QR + 0.50*QP
    tutor: float               # Tutor 7D weighted average
    tutor_dimensions: dict     # Per-dimension scores
    process_metrics: dict      # Detailed process breakdown
```

### 3.4 Turn Definition

One **turn** = one `send_message()` call. The agent may make
arbitrarily many `call_tool()` calls between turns. The conversation
advances only when the agent sends a message.

```
Turn 1:
  obs = env.reset(...)         → Student: "Can you help me with SMA?"
  env.call_tool("fetch_market_data", ...)
  env.call_tool("compute_indicator", ...)
  obs = env.send_message("I've loaded AAPL data and computed SMA-20/50 ...")
                                → Student: "What's a golden cross?"

Turn 2:
  obs = env.send_message("When SMA-20 crosses above SMA-50...")
                                → Student: "Can we see that on a chart?"
```

### 3.5 Agent Freedom

The agent has **full control** over:
- When and which tools to call (within the 15-tool budget)
- How to manage its own context window
- When to reply to the student vs. continue tool exploration
- Internal architecture (chain-of-thought, multi-agent, RAG, etc.)

The environment controls:
- Student behavior (persona-driven LLM)
- Termination (TC checker, max turns, timeout)
- Tool execution (sandbox isolation)
- Evaluation (post-hoc, on observable outputs only)

---

## 4. Tool API

Each task exposes **15 tool slots**: core tools (task-specific) +
convenient tools (bonus-eligible) + distractors (randomly sampled to fill
remaining slots). The agent cannot distinguish tool types from their schemas.

### 4.1 Core Tools

#### File & System

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `shell_exec` | Execute shell command in sandbox | `command` (str, req), `timeout` (int, opt=30) |
| `file_write` | Write content to workspace file | `path` (str, req), `content` (str, req) |
| `file_read` | Read file from workspace/data/docs | `path` (str, req), `offset` (int, opt), `max_lines` (int, opt) |
| `file_list` | List files in directory | `directory` (str, opt=workspace) |
| `get_environment_info` | Return paths and installed packages | (none) |
| `search_docs` | Keyword search in /docs/ | `query` (str, req) |
| `search_web` | Web search via DuckDuckGo | `query` (str, req), `max_results` (int, opt=5) |

#### Data Processing

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `fetch_market_data` | Fetch OHLCV data, save CSV | `symbol` (str, req), `start`/`end` (str, opt) |
| `compute_indicator` | Compute SMA/EMA/RSI/MACD/Bollinger | `data_path` (str, req), `indicator` (str, req), `indicator_params` (obj, opt) |
| `construct_signal` | Build trading signal CSV | `data_path` (str, req), `signal_type` (str, req), `signal_params` (obj, opt) |
| `engineer_features` | Add quantitative features to dataset | `data_path` (str, req), `features` (array, req), `feature_params` (obj, opt) |
| `compute_statistics` | Statistical tests (ADF, correlation, cointegration, etc.) | `data_path` (str, req), `method` (str, req), `method_params` (obj, opt) |
| `align_timeseries` | Merge multiple time-series CSVs | `data_paths` (array, req), `method` (str, opt=inner) |
| `split_walkforward_windows` | Walk-forward train/test splits | `data_path` (str, req), `scheme` (str, opt=rolling) |
| `breakdown_pnl` | Decompose PnL into fee/slippage/funding | `trades_path` (str, req), `fee_model` (str, opt) |

#### Backtesting & Evaluation

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `run_backtest` | Run built-in strategy backtest | `data_path` (str, req), `strategy` (str, req), `strategy_params` (obj, opt) |
| `analyze_backtest_results` | Compute performance metrics | `data_path` (str, req), `returns_column` (str, opt) |
| `compare_backtest_results` | Side-by-side backtest comparison | `data_paths` (array, req), `significance_test` (str, opt) |
| `evaluate_signal` | Signal quality (IC, quantile returns) | `file_path` (str, req) |
| `plot_chart` | Execute matplotlib code, save PNG | `python_code` (str, req) |

#### LEAN C# (Implementation Tasks Only)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `run_lean_backtest` | Compile + run LEAN C# algorithm | `algorithm_path` (str, req), `params_json` (str, opt) |
| `submit_trial` | Snapshot workspace as a trial | `notes` (str, opt) |
| `select_submission` | Select trial for evaluation | `trial_id` (int, req) |
| `get_trial_status` | View all trials and metrics | (none) |
| `analyze_lean_results` | Parse LEAN results JSON | `sections` (str, opt=summary) |

### 4.2 Distractor Tools

21 functional distractor tools exist. They:
- Have plausible descriptions and return realistic-looking results
- Are domain-relevant (VaR, GARCH, options Greeks, portfolio optimization, etc.)
- **Are not required** for any task
- Calling them does not cause errors but wastes budget and is scored negatively

Examples: `compute_var`, `fit_garch_model`, `optimize_portfolio`,
`run_monte_carlo`, `compute_greeks`, `screen_stocks`, `submit_order`

### 4.3 Tool Slot Allocation

```
15 total slots = |core_tools| + |convenient_tools| + |distractors|

distractors = random_sample(
    DISTRACTOR_POOL - core_tools - convenient_tools,
    k = 15 - |core_tools| - |convenient_tools|,
    seed = hash(task_id + run_index)
)
```

### 4.4 Tool Result Constraints

- All tool results are returned as strings
- Results exceeding 12,000 characters are truncated (head + tail preserved)
- Tool calls after the wall-clock deadline are rejected
- `run_lean_backtest` consumes one trial from the budget per call

---

## 5. Student Behavior

The student is simulated by an LLM (currently GPT-5.2 via OpenRouter,
temp=0). The agent does **not** control the student.

### 5.1 Personas

| Persona ID | Knowledge Level | Profile |
|------------|----------------|---------|
| `beginner_no_finance` | beginner | No finance background, learns from scratch |
| `intermediate_developer` | intermediate | Software dev, some quant exposure |
| `advanced_quant` | advanced | Experienced quant, deep domain knowledge |

Each persona has: `known_concepts`, `unknown_concepts`, `emotional_profile`,
and `behavioral_rules` defined in `personas/{persona_id}.json`.

### 5.2 Student Behavior Properties

- **Anchored**: Student responses are contextually anchored to the agent's
  replies (not scripted)
- **Non-deterministic**: Even at temp=0, student messages vary across runs
  (62% substantive divergence). This is a known property, treated as a
  robustness test
- **Goal-directed**: Student pursues the learning objective defined in the
  task's termination criteria

### 5.3 Conversation Termination

A conversation ends when **any** of these conditions is met:

1. **Goal achievement**: An independent LLM checker determines all
   termination criteria are satisfied
2. **Max turns reached**: Hard cap (default 30 turns)
3. **Wall-clock timeout**: `timeout_minutes` exceeded

The agent cannot force termination. The student drives the conversation
length through its questions and the TC checker's judgment.

---

## 6. Evaluation Protocol

Evaluation is post-hoc. Inputs: conversation transcript, tool call logs,
workspace files. No chain-of-thought is used.

### 6.1 Scoring Formula

```
Overall Agent Score (OAS) = 0.70 × Quant Agent Score + 0.30 × Tutor Score

Quant Agent Score = 0.50 × QR (Result) + 0.50 × QP (Process)
```

### 6.2 Quant Result Score (QR)

Three components, blended with divergence dampening:

| Component | Method | Weight (typical) |
|-----------|--------|-------------------|
| Programmatic eval | Task-specific Python script checks workspace outputs | ~30% |
| Code evaluation | 3-layer: static analysis + execution + output vs reference | ~30% |
| LLM result judge | Domain expert assesses numerical accuracy & completeness | ~40% |

### 6.3 Quant Process Score (QP) — 7 Dimensions

| Dimension | Method | What It Measures |
|-----------|--------|-----------------|
| `tool_usage` | Programmatic (from proxy logs) | Expected vs convenient vs distractor call ratios |
| `step_efficiency` | Programmatic + LLM | Redundant or unnecessary actions |
| `process_reasonableness` | LLM | Logical soundness of analytical steps |
| `process_alignment` | LLM + reference trace | Adherence to expected approach |
| `code_process` | LLM | Iterative refinement and debugging quality |
| `role_adherence` | LLM | Stays in tutor role (vs doing everything silently) |
| `topic_adherence` | LLM | Stays on task topic |

### 6.4 Tutor Score — 7 Dimensions

Evaluated via ConversationalGEval (3 shuffled runs, averaged):

| Dimension | What It Measures |
|-----------|-----------------|
| D1 Level Detection | Correctly identifies student's knowledge level |
| D2 Language Adaptation | Adjusts vocabulary and abstraction to student |
| D3 Scaffolding Calibration | Appropriate level of guidance (not too much/little) |
| D4 Domain Accuracy | Financial concepts and computations are correct |
| D5 Computational Rigor | Proper methodology, no shortcuts |
| D6 Emotional Responsiveness | Responds to student confusion/frustration |
| D7 Teaching Effectiveness | Student actually learns by the end |

Dimension weights vary by task category (e.g. implementation tasks
weight D5 higher, adversarial tasks weight D4 higher).

### 6.5 Benchmark-Level KPIs

| KPI | Formula |
|-----|---------|
| OAS (Overall Agent Score) | Mean of per-task OAS |
| QAI (Quant Agent Index) | Mean of per-task Quant Agent Score |
| TEI (Tutoring Effectiveness Index) | Mean of per-task Tutor Score |
| AS (Adaptiveness Score) | Per-task tutor score variance across personas |
| PMS (Process Mastery Score) | Mean of per-task process quality |

### 6.6 Statistical Reporting

- Recommended: `n_runs=3` per task-persona pair, report mean +/- std
- 95% confidence intervals on aggregate KPIs
- Per-task CV (coefficient of variation) for stability analysis

---

## 7. Submission & Execution

### 7.1 Using the Gym API (Recommended)

```python
from bench.gym import QuantTutorEnv

env = QuantTutorEnv(use_docker=True)

# Single task
obs = env.reset("S01_ma_crossover")
while not obs.done:
    # Your agent logic: call tools, reason, then reply
    result = env.call_tool("fetch_market_data", symbol="AAPL")
    obs = env.send_message("Here's what I found...")
scores = env.evaluate()
env.close()

# Multiple tasks
for task_id in ["D01_load_inspect_ohlcv", "S01_ma_crossover"]:
    obs = env.reset(task_id)
    while not obs.done:
        obs = env.send_message(my_agent_logic(obs))
    scores = env.evaluate()
```

### 7.2 Using the Reference Harness (Baseline)

The reference harness (`bench/run_benchmark.py`) wraps the gym env
with pre-built agent adapters for Anthropic/OpenAI/Google. It is
provided for reproducing baseline results, **not** as a required
integration point.

```bash
# Reference harness — uses built-in adapters
python run_benchmark.py run-single \
    --task S01_ma_crossover \
    --agent anthropic --docker
```

### 7.3 Direct Integration (Advanced)

For teams with their own harness who don't want the gym's Python API:

1. Load task JSON and persona JSON
2. Set up a sandbox matching the task's `environment` config
3. Register tools matching the schemas in §4
4. Run the conversation loop:
   - Student generates opening message
   - Agent calls tools + sends replies at its own pace
   - Independent LLM judges termination against `termination_criteria`
   - Repeat until termination or max turns
5. Run evaluation on: conversation transcript, tool logs, workspace files

The gym API handles steps 1-5 automatically. Direct integration
is for teams who need full control over sandboxing and student simulation.

---

## 8. Data Requirements

The benchmark data is hosted on HuggingFace (`Varsity-Tech/quant-tutor-bench-data`)
and auto-downloaded on first run. Contents:

| Directory | Description |
|-----------|-------------|
| `data/` | Financial CSV datasets (OHLCV, fundamentals) |
| `docs/` | Reference documentation (indicator formulas, API guides) |
| `lean/` | LEAN backtesting data (crypto futures, symbol properties) |
| `student_code/` | Pre-written code for debug tasks (X-series) |

Total size: ~16,515 files. Requires ~2GB disk space.

---

## 9. Environment Specifications

### 9.1 Python Sandbox (`quant-tutor-env:v2.2`)

- Python 3.11
- Pre-installed: pandas, numpy, scipy, matplotlib, scikit-learn,
  statsmodels, arch, ta-lib
- Network: disabled by default (`network_enabled: false`)
- Filesystem: `/workspace` (read-write), `/data` (read-only), `/docs` (read-only)

### 9.2 LEAN Sandbox (`quant-tutor-lean:v1.0`)

- .NET 10 + QuantConnect LEAN engine
- Pre-installed: `quantconnect/lean:latest` Docker image
- Supports: CryptoFuture (Binance), daily/hourly/minute resolution
- Data: 635 USDT-M perpetual symbols, 2022-2025
- Trial system: atomic compile-run-snapshot per `run_lean_backtest` call

---

## Appendix A: Full Core Tool Parameter Reference

### A.1 `shell_exec`
```
command: string (required) — Shell command to execute
timeout: integer (optional, default=30) — Timeout in seconds
Returns: stdout + stderr with exit code
```

### A.2 `file_write`
```
path: string (required) — Path relative to /workspace
content: string (required) — Full file content
Returns: Success confirmation or error
```

### A.3 `file_read`
```
path: string (required) — Searches workspace/, data/, docs/, student_code/
offset: integer (optional, default=0) — Start line (0-based)
max_lines: integer (optional, default=0) — 0 = auto (smart preview for large CSV)
Returns: File contents (possibly truncated)
```

### A.4 `file_list`
```
directory: string (optional, default=workspace root)
Returns: Directory listing
```

### A.5 `get_environment_info`
```
(no parameters)
Returns: Directory paths, available files, installed packages
```

### A.6 `search_docs`
```
query: string (required) — Search keywords
Returns: Ranked matching lines from /docs/
```

### A.7 `search_web`
```
query: string (required) — Search query
max_results: integer (optional, default=5, max=10)
Returns: Search results with titles, URLs, snippets
```

### A.8 `fetch_market_data`
```
symbol: string (required) — Ticker symbol (e.g. 'AAPL')
start: string (optional) — YYYY-MM-DD
end: string (optional) — YYYY-MM-DD
Returns: Summary with first/last rows; CSV saved to workspace
```

### A.9 `compute_indicator`
```
data_path: string (required) — CSV with 'Close' column
indicator: string (required) — SMA | EMA | RSI | BOLLINGER | MACD
indicator_params: object (optional) — e.g. {"window": 20}
Returns: Last 10 rows of enriched dataset; CSV saved to workspace
```

### A.10 `construct_signal`
```
data_path: string (required) — CSV with price data
signal_type: string (required) — zscore | momentum | mean_reversion | spread | crossover | composite | volume_imbalance
signal_params: object (optional) — Type-specific params
output_name: string (optional) — Output filename
close_column: string (optional) — Override close price column name
Returns: Signal CSV with 'signal' + 'close' columns
```

### A.11 `engineer_features`
```
data_path: string (required) — CSV with OHLCV+ data
features: array (required) — Feature names to compute
feature_params: object (optional) — Per-feature params
output_name: string (optional) — Output filename
Returns: Enriched dataset summary
```

### A.12 `compute_statistics`
```
data_path: string (required) — CSV with numeric data
method: string (required) — ADF | CORRELATION | COINTEGRATION | DESCRIPTIVE | MISSING | LEAD_LAG | ROLLING
method_params: object (optional) — Method-specific params
Returns: Statistical results (varies by method)
```

### A.13 `align_timeseries`
```
data_paths: array (required) — CSV paths (at least 2)
time_column: string (optional, default='auto')
method: string (optional, default='inner') — inner | outer_ffill | outer_bfill | outer_interpolate | nearest
resample: string (optional) — e.g. '1D', '1H'
fill_limit: integer (optional) — Max consecutive NaN fills
Returns: Aligned CSV summary
```

### A.14 `split_walkforward_windows`
```
data_path: string (required) — Time-series CSV
scheme: string (optional, default='rolling') — rolling | expanding | purged_kfold | combinatorial
train_size: integer (optional, default=252) — Training window rows
test_size: integer (optional, default=63) — Test window rows
embargo_size: integer (optional, default=0) — Gap rows
Returns: Fold summary; CSVs saved to workspace
```

### A.15 `breakdown_pnl`
```
trades_path: string (required) — Trade-level or returns CSV
input_format: string (optional, default='auto')
fee_model: string (optional, default='percentage')
slippage_model: string (optional, default='proportional')
funding_path: string (optional) — Funding rate CSV for perpetuals
Returns: PnL decomposition (gross, fees, slippage, funding)
```

### A.16 `run_backtest`
```
data_path: string (required) — CSV with 'Close' column
strategy: string (required) — ma_crossover | rsi_threshold | bollinger_breakout
strategy_params: object (optional) — Strategy-specific params
start: string (optional) — YYYY-MM-DD
end: string (optional) — YYYY-MM-DD
Returns: Performance metrics; equity curve CSV saved to workspace
```

### A.17 `analyze_backtest_results`
```
data_path: string (required) — Returns CSV
returns_column: string (optional) — Auto-detects if omitted
Returns: Sharpe, annual return, max drawdown, win rate, etc.
```

### A.18 `compare_backtest_results`
```
data_paths: array (required) — CSV paths (at least 2)
labels: array (optional) — Human-readable names
significance_test: string (optional) — none | bootstrap | paired_t
Returns: Side-by-side metric comparison
```

### A.19 `evaluate_signal`
```
file_path: string (required) — CSV with 'signal' + close column
forward_periods: integer (optional, default=1)
quantiles: integer (optional, default=5)
Returns: IC, quantile returns, turnover, hit rate
```

### A.20 `plot_chart`
```
python_code: string (required) — Matplotlib code (auto-saves PNG)
Returns: Saved image file path
```

### A.21 `run_lean_backtest`
```
algorithm_path: string (required) — .cs file relative to workspace
params_json: string (optional) — Algorithm parameters JSON
run_id: string (optional) — Run identifier for multi-config tasks
Returns: Trial status, trade count, Sharpe, remaining budget
Constraint: Consumes 1 trial from budget per call
```

### A.22 `submit_trial`
```
notes: string (optional) — Trial description
Returns: Trial snapshot confirmation
Constraint: Consumes 1 trial from budget per call
```

### A.23 `select_submission`
```
trial_id: integer (required) — 1-based trial number
Returns: Confirmation of selected trial
```

### A.24 `get_trial_status`
```
(no parameters)
Returns: All trials with metrics and selection status
```

### A.25 `analyze_lean_results`
```
results_path: string (optional) — Results directory
trial_id: integer (optional) — Specific trial (0 = latest)
sections: string (optional, default='summary') — summary | orders | trades | symbols | all
Returns: Structured LEAN metrics
```
