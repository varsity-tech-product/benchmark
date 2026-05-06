# QuantAgentBench Specification v2.0

> **Status**: Draft (post-#122 redefinition)
> **Date**: 2026-05-02
> **Working name**: QuantAgentBench (final marketing name pending — see #122 TBD-4)
> **Scope**: This document defines the benchmark protocol. Any conforming agent
> can be evaluated — no dependency on the reference harness is required.

---

## 1. Overview

QuantAgentBench evaluates an LLM agent's ability to (1) produce correct
quantitative analysis results and (2) follow sound analytical processes
while operating across a spectrum of task types and difficulties (simple
Q&A → analysis → code → long-horizon execution).

The agent under test acts as a **quant agent**. It converses with a
simulated user (NPC) and operates a sandboxed toolset (data analysis,
backtesting, code execution). Evaluation is post-hoc, based on observable
outputs only — no access to internal chain-of-thought is needed.

**Key design properties**:
- Agent ↔ Environment closed loop (actions change sandbox state)
- Benchmark specification is decoupled from the reference implementation
- Third-party agents interact through a single `respond()` interface
- Evaluation uses only observable behavior of the agent: conversation text,
  tool call logs, and workspace files
- The simulated user is a scenario NPC; its replies do not enter the
  scoring pipeline

---

## 2. Task Schema

Each task is a JSON file conforming to the following schema. Tasks live in
`tasks/layer2/{category}/{task_id}.json`.

### 2.1 Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Unique identifier, e.g. `S01_ma_crossover` |
| `version` | string | no | Schema version (default `"2.0"`) |
| `difficulty` | enum | yes | `"easy"` \| `"medium"` \| `"hard"` |
| `category` | enum | yes | See §2.2 |
| `task_type` | enum | no | `"multi_turn"` (default) \| `"single_turn"` |
| `description` | string | yes | Task description shown to the agent |
| `persona_id` | string | yes | The single persona used for this task |
| `student_opening` | string | yes | Opening message from the NPC |
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

The diversity of task types and difficulties across these categories is the
benchmark's measure of "level adaptation". There is no separate adaptation
score — cross-category pass rate carries that signal.

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
termination logic.

```json
{
  "expected_outcome": "Agent produces a working SMA crossover backtest with metrics and a limitations discussion.",
  "termination_criteria": {
    "required_artifacts": ["backtest_results.csv", "sma_crossover.py"],
    "required_tool_chain": ["compute_indicator", "run_backtest"],
    "required_content_topics": ["whipsaw risk", "lag"]
  },
  "required_capabilities": ["data_loading", "indicator_computation"],
  "expected_mcp_tools": ["fetch_market_data", "compute_indicator"],
  "convenient_tools": ["plot_chart"],
  "quant_validation": {"eval_script": "strategy/S01_ma_crossover.py"}
}
```

`termination_criteria` is structured (agent-trace based, see §5.3). Each
sub-field is checked programmatically except `required_content_topics`,
which is checked by the QR judge against the agent's chat output.

---

## 3. Environment Interface (Gym)

The benchmark is a **gym environment**. The agent controls the loop;
the environment provides tools and a simulated NPC user.

```python
from bench.gym import QuantAgentEnv

env = QuantAgentEnv(use_docker=True)
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
| `env.reset(task_id)` | `Observation` | Creates sandbox, returns NPC opening + tools |
| `env.call_tool(name, **kwargs)` | `str` | Executes tool in sandbox. **Does not advance conversation.** |
| `env.send_message(text, attachments?, reasoning?)` | `Observation` | Sends agent reply → NPC responds → TC checked. `reasoning` is an optional private rationale recorded for trace analysis (never shown to the NPC, never enters scoring). |
| `env.evaluate()` | `Scores` | Runs post-hoc evaluation on completed conversation |
| `env.close()` | `None` | Destroys sandbox, releases resources |

### 3.2 Observation

```python
@dataclass
class Observation:
    npc_message: str             # NPC's latest message (scenario context only)
    available_tools: list[dict]  # Tool schemas agent can call
    done: bool                   # True when conversation should end
    turn: int                    # Current turn number
    max_turns: int               # Hard turn cap
    info: dict                   # Extra metadata (termination reason, TC coverage)
```

### 3.3 Scores

```python
@dataclass
class Scores:
    quant_result: float        # QR sub-score, 0-100
    quant_process: float       # QP sub-score, 0-100
    task_score: float          # 0.6 * QR + 0.4 * QP
    task_pass: bool            # normalized task_score >= 0.50
    process_metrics: dict      # Detailed process breakdown
```

The dataclass is per-task. Benchmark-level KPIs (pass rate over the task
suite) are computed by aggregating over `task_pass` across all tasks (§6.5).

REST clients consume the v1 envelope returned by
`GET /session/{sid}/scores` (see `docs/architecture.md` Public Reads). The
public top level is `schema_version`, `score_id`, `score_status`,
`task_score`, `task_pass`; per-track scores and process metric breakdowns
live under the opaque `detail` blob. Completed scored responses populate
`task_pass` from `task_pass_threshold_v1`.

### 3.4 Turn Definition

One **turn** = one `send_message()` call. The agent may make
arbitrarily many `call_tool()` calls between turns. The conversation
advances only when the agent sends a message.

```
Turn 1:
  obs = env.reset(...)         → NPC: "Can you help me with SMA?"
  env.call_tool("fetch_market_data", ...)
  env.call_tool("compute_indicator", ...)
  obs = env.send_message("I've loaded AAPL data and computed SMA-20/50 ...")
                                → NPC: "What's a golden cross?"

Turn 2:
  obs = env.send_message("When SMA-20 crosses above SMA-50...")
                                → NPC: "Can we see that on a chart?"
```

### 3.5 Agent Freedom

The agent has **full control** over:
- When and which tools to call (within the 15-tool budget)
- How to manage its own context window
- When to reply to the NPC vs. continue tool exploration
- Internal architecture (chain-of-thought, multi-agent, RAG, etc.)

The environment controls:
- NPC behavior (persona-driven LLM; NPC replies do not enter scoring)
- Termination (agent-trace TC, max turns, timeout)
- Tool execution (sandbox isolation)
- Evaluation (post-hoc, on agent-only observable signals)

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

## 5. NPC Behavior

The simulated user is an NPC: it drives scenario context (asks questions,
provides framing) but its replies do not enter the scoring pipeline. The
agent is scored purely on what it produces and does.

The NPC is implemented by an LLM (currently GPT-5.2 via OpenRouter, temp=0).
The agent does **not** control the NPC.

### 5.1 Personas

Each task is associated with a single `persona_id` chosen for narrative fit.
The current persona set:

| Persona ID | Profile |
|------------|---------|
| `developer_crossover` | Software developer who can write code but is new to quant finance |
| `double_novice` | Beginner in both finance and programming |
| `finance_veteran` | Experienced financial professional, automating manual workflows |
| `fullstack_practitioner` | Generalist with both finance and engineering background |

Each persona has: `known_concepts`, `unknown_concepts`, `emotional_profile`,
and `behavioral_rules` defined in `personas/{persona_id}.json`. These shape
the NPC's questions but do not enter scoring.

### 5.2 NPC Behavior Properties

- **Anchored**: NPC responses are contextually anchored to the agent's
  replies (not scripted)
- **Non-deterministic**: Even at temp=0, NPC messages vary across runs.
  This is acceptable because NPC replies do not enter scoring (only agent
  outputs do); NPC variance manifests as task-difficulty variance, not
  scoring variance
- **Goal-directed**: NPC drives toward the scenario goal embedded in the
  task's `student_opening` and persona profile

### 5.3 Conversation Termination

A conversation ends when **any** of these conditions is met:

1. **Goal achievement** (agent-trace based): all of the following are
   satisfied —
   - All `required_artifacts` exist in the workspace (programmatic check)
   - All `required_tool_chain` tools have been invoked (programmatic check)
   - All `required_content_topics` are covered in the agent's chat output
     (checked by the QR judge over the agent transcript)
2. **Max turns reached**: hard cap (default 30 turns)
3. **Wall-clock timeout**: `timeout_minutes` exceeded

The agent cannot force termination. The TC check runs after each
`send_message()` call; an independent post-hoc TC re-check runs at scoring
time over the completed bundle.

---

## 6. Evaluation Protocol

Evaluation is post-hoc. Inputs (agent-only signals): conversation transcript
(agent's chat replies), tool call logs, workspace files. NPC replies and
internal chain-of-thought are not used.

### 6.1 Scoring Formula

```
Per-task subscores:
  QR_score = LLM_judge(workspace_files, agent_chat) ∈ [0, 100]
  QP_score = LLM_judge(tool_call_log, agent_chat)   ∈ [0, 100]

Per-task aggregate:
  task_score = 0.6 × QR_score + 0.4 × QP_score
  task_pass  = (normalized task_score ≥ 0.50)      # task_pass_threshold_v1

Per-run-set aggregate (n_runs = 3):
  task_pass_majority = sum(task_pass across runs) ≥ 2

Benchmark headline:
  pass_rate = count(task_pass_majority) / N_tasks
```

QR/QP weight (0.6/0.4) and `task_pass_threshold_v1` are frozen for the v2.0
score contract.

### 6.2 Quant Result Score (QR)

Three components, blended:

| Component | Method | Weight (typical) |
|-----------|--------|-------------------|
| Programmatic eval | Task-specific Python script checks workspace outputs | ~30% |
| Code evaluation | 3-layer: static analysis + execution + reference distribution match | ~30% |
| LLM result judge | Domain expert assesses numerical accuracy & completeness | ~40% |

**Reference distribution match** (Layer-3 of code evaluation): reference
results are treated as a **tolerance band**, not a single ground truth.
Three task-type cases:

- **Single-config tasks** (I01–I05, I07–I08, S/D/E/B series): a single
  reference run produces a metric set (Sharpe, return, max DD, turnover,
  trade count). Agent's metrics are scored against ±X% tolerance bands;
  same-sign / same-regime deviations get partial credit; reverse-sign or
  pathological values get 0.
- **Sweep tasks** (I06, I10): reference is the full sweep grid plus the
  best config. Agent is scored on whether its own best config lands in
  the top-K percentile of the reference grid.
- **Comparison tasks** (I09, three risk modes): each scenario gets an
  independent tolerance band; final score is per-scenario score aggregated.

Tolerance bands per metric per task type are defined in
`bench/data/reference/<task_id>/distribution.json`.

### 6.3 Quant Process Score (QP) — 5 Dimensions

| Dimension | Method | What It Measures |
|-----------|--------|-----------------|
| `tool_usage` | Programmatic (from tool log) | Expected vs convenient vs distractor call ratios |
| `step_efficiency` | Programmatic + LLM | Redundant or unnecessary actions |
| `process_reasonableness` | LLM | Logical soundness of analytical steps |
| `process_alignment` | LLM + reference trace | Adherence to expected approach |
| `code_process` | LLM | Iterative refinement and debugging quality |

### 6.4 (Reserved)

The Tutor 7D scoring system from v1.0 has been removed. Pedagogical
dimensions (D1 Level Detection, D2 Language Adaptation, D3 Scaffolding,
D6 Emotional Responsiveness, D7 Teaching Effectiveness) are not part of
this benchmark; agent capability across task type/difficulty is captured
by §6.5 pass rate. Domain Accuracy and Computational Rigor (former D4/D5)
are subsumed by QR.

### 6.5 Benchmark-Level KPIs

| KPI | Formula |
|-----|---------|
| `pass_rate` (headline) | `count(task_pass_majority) / N_tasks` |
| `pass_rate_by_category` | Same, grouped by task category |
| `task_score_mean` (diagnostic) | Mean of `task_score` across all tasks |
| `task_score_std` (diagnostic) | Std-dev of `task_score` |

**Pass Threshold**: `task_pass_threshold_v1` sets the normalized runtime
threshold to `0.50`. Calibration source:
`jv_20260429_stage3_combined` plus
`bench/experiments/judge_validation/human_labels.json` and
`bench/experiments/judge_validation/human_review_sample_map.json`. Human pass
is raw score `3` on the 1-5 validation rubric, which maps to normalized
score `0.50`.

LLM dependencies in the scoring path: 2 (QR judge, QP judge). The NPC
LLM (§5) sits in conversation runtime only; its outputs do not enter
scoring.

### 6.6 Statistical Reporting

- Required: `n_runs = 3` per task, majority pass aggregation
- Diagnostic: per-task mean ± std of `task_score`
- 95% confidence intervals on `pass_rate` (Wilson interval)

---

## 7. Submission & Execution

### 7.1 Using the Gym API (Recommended)

```python
from bench.gym import QuantAgentEnv

env = QuantAgentEnv(use_docker=True)

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

### 7.2 Using the API Batch Driver (Baseline)

The baseline driver (`bench/scripts/baseline_run.py`) creates run-token
sessions through the server client API, delegates agent execution to
`bench/client/runner.py`, exports Bundle v1 alpha artifacts, and writes the
tracked aggregate summary for paper-facing baseline data.

```bash
export QTB_BASELINE_SERVER=http://127.0.0.1:8000
export QTB_CLIENT_API_KEY=<client-api-key>
export OPENROUTER_API_KEY=<openrouter-api-key>

python bench/scripts/baseline_run.py run \
    --tasks L2_ADV_01_investment_advice \
    --agents claude_haiku_4_5 \
    --conditions agent \
    --server-results-root bench/results/server
```

Full run guidance lives in `docs/baseline_run_v1.md`.

### 7.3 Direct Integration (Advanced)

For teams with their own harness using direct task orchestration:

1. Load task JSON and persona JSON
2. Set up a sandbox matching the task's `environment` config
3. Register tools matching the schemas in §4
4. Run the conversation loop:
   - NPC generates opening message
   - Agent calls tools + sends replies at its own pace
   - After each agent message, run the agent-trace TC check (§5.3)
   - Repeat until termination, max turns, or timeout
5. Run evaluation (QR + QP judges) on: agent's chat replies, tool logs,
   workspace files. NPC replies and internal CoT are excluded.

The gym API handles steps 1-5 automatically. Direct integration
is for teams who need full control over sandboxing and NPC simulation.

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
| `reference/` | Per-task reference distribution (`<task_id>/distribution.json`) |

Total size: ~16,515 files. Requires ~2GB disk space.

The HuggingFace dataset name retains its v1.0 path (`quant-tutor-bench-data`)
for backward compatibility; future major dataset versions may rename.

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
