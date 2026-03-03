# Tool Selection Guide for QuantTutorBench

This document describes the tool ecosystem, the `tool_usage` scoring model, and
human review guidelines for configuring `core_mcp_tools`, `expected_mcp_tools`,
and `convenient_tools` per task.

---

## 1. Tool Usage Scoring Model

Tool usage is one of the 7 QP (Quant Process) dimensions. It is a **pure
mathematical score** (no LLM judge) that evaluates how well the agent selected
tools from the available set.

### Formula

```
base    = 1.0   if no convenient_tools defined for the task
          0.8   if convenient_tools are defined (room for bonus)

bonus   = 0.2 / N  for each convenient tool actually used  (N = total convenient count)

penalty = 0.15  per expected tool NOT called
          0.10  per distractor tool called

score   = clamp(base + bonus - penalties, 0, 1)
```

### Special cases

| Condition | Behavior |
|-----------|----------|
| Adversarial task with no expected/convenient tools | `score = 1.0 - distractor_penalty` |
| `send_message` and `get_environment_info` calls | Excluded from `called` set (non-substantive) |

### QP Weight

`tool_usage` has a weight of **0.20** in the QP weighted aggregate (the highest
single weight, tied with `process_reasonableness`).

### Design Rationale

The scoring model is intentionally **tool-neutral regarding custom code**.
An agent that writes a Python script via `shell_exec` to compute an indicator
is not penalized for skipping `compute_indicator` (a convenient tool).
Convenient tools give a *bonus* when used, but their absence does not penalize.
Only `expected_mcp_tools` (data gates) penalize when missed.

---

## 2. Tool Inventory

### 2.1 Core Tools (Essential)

These are **data gates and I/O channels** with no alternative path in the MCP
tool set. They are registered via `environment.core_mcp_tools` per task.

| Tool | Description |
|------|-------------|
| `fetch_market_data` | Return OHLCV data from frozen CSV for a given symbol and date range. Sole data gate for market data. |
| `file_read` | Read a file from workspace, data, docs, or student_code directories. |
| `file_write` | Write content to a file in the workspace. |
| `file_list` | List files in a directory. |
| `shell_exec` | Execute a shell command in the sandbox. Sole code execution channel. |
| `search_docs` | Full-text search across the /docs/ directory. |
| `search_web` | Search the public web using DuckDuckGo. Requires `network_enabled: true`. |
| `send_message` | Send a message to the student. Primary tutoring action. |
| `get_environment_info` | Return available data files, installed packages, and workspace contents. |

### 2.2 Convenience Tools

Self-contained shortcuts that use Python libraries directly (pandas, numpy,
matplotlib, statsmodels). **None of them call shell_exec, file_write, or any
other Essential tool internally.** An agent can always replicate their
functionality by writing custom code via `shell_exec`.

| Tool | Description | Replaces |
|------|-------------|----------|
| `compute_indicator` | Compute SMA, EMA, RSI, Bollinger Bands, or MACD on a CSV dataset. Adds indicator columns and saves enriched data. | ~10 lines of pandas code |
| `run_backtest` | Run a complete backtest for ma_crossover, rsi_threshold, or bollinger_breakout. Returns metrics + equity curve. | ~40 lines of strategy code |
| `analyze_backtest_results` | Compute Sharpe, Annual Return, Max Drawdown, etc. from a CSV of returns. Auto-detects the returns column. | ~30 lines of numpy code |
| `compute_statistics` | Run ADF stationarity test, correlation matrix, or cointegration test. | ~15 lines of statsmodels code |
| `plot_chart` | Execute matplotlib code in-process and save chart as PNG. Auto-appends `plt.savefig()`. | ~5 lines of boilerplate |

### 2.3 Distractor Tools (Global Pool)

Domain-relevant quantitative finance tools that return **plausible results** but
are irrelevant to the specific task. Automatically sampled from a global pool at
runtime.

The registry fills up to **15 total tool slots** per task:
`n_distractors = 15 - len(core) - len(convenient)`.

| Distractor Tool | Description |
|-----------------|-------------|
| `compute_var` | Value at Risk (VaR) and Conditional VaR using historical simulation |
| `fit_garch_model` | Fit GARCH(p,q) volatility model and forecast daily vol |
| `optimize_portfolio` | Mean-variance portfolio optimization (max Sharpe / min variance) |
| `run_monte_carlo` | Monte Carlo price path simulation using geometric Brownian motion |
| `fetch_fundamentals` | Fundamental data (P/E, EPS, revenue, market cap, beta) |
| `compute_greeks` | Option Greeks (delta, gamma, theta, vega, rho) via Black-Scholes |
| `screen_stocks` | Screen stocks by technical/fundamental criteria |
| `backtest_pairs_trade` | Statistical arbitrage pairs trading backtest |
| `compute_beta` | Beta coefficient and alpha via OLS regression on log returns |
| `estimate_covariance` | Annualized covariance and correlation matrix for asset returns |
| `fetch_live_price` | Current live market price, bid/ask, and volume |
| `query_database` | SQL query against a financial data warehouse |
| `fetch_news_sentiment` | Aggregated news sentiment scores for a ticker |
| `submit_order` | Submit a trading order to the execution system |
| `fetch_options_chain` | Full options chain (calls/puts, strikes, bids, asks, IV) |
| `generate_image` | Generate a visualization image from a text description |
| `get_current_time` | Current time + market session status (pre/regular/after-hours) |
| `translate_text` | Translate financial text between languages |
| `fetch_economic_calendar` | Upcoming macroeconomic events with forecasts |
| `fetch_crypto_data` | Cryptocurrency OHLCV bars from a major exchange |
| `compare_series` | Compare two time series for correlation and tracking error |

**Design note**: Every distractor returns valid-looking results (not errors).
The agent cannot detect distractors by calling them once. It must understand its
task requirements to decide NOT to use them.

---

## 3. Per-Task Tool Configuration

Each task JSON contains three tool-related fields across two schema locations:

```
environment.core_mcp_tools      -> registered as usable tools
ground_truth.expected_mcp_tools -> must-use data gates (penalty if missed)
ground_truth.convenient_tools   -> bonus-eligible shortcuts
```

### 3.1 `core_mcp_tools` (EnvironmentConfig)

**Definition**: The complete list of core tools registered and available to the
agent for this task. This is the agent's "toolbox" — only these core tools plus
any selected convenient tools and sampled distractors are visible.

**Where it lives**: `task.environment.core_mcp_tools`

**Human review checklist**:

1. **Include all tools the task could plausibly need.** If a task involves
   reading data, writing files, and executing code, include `fetch_market_data`,
   `file_read`, `file_write`, `shell_exec`, and `file_list`.
2. **Include `send_message`** for all tutoring tasks (the agent must
   communicate with the student).
3. **Include `get_environment_info`** if the agent benefits from knowing what
   files and packages are available.
4. **Include `search_web`** only for tasks with `network_enabled: true` (D10,
   D11). Without network access, the tool would fail.
5. **Include `search_docs`** if `/docs/` contains relevant reference material.
6. **Do NOT include convenience tools here.** They go in
   `ground_truth.convenient_tools` instead.
7. **Core tools are not scored directly.** They define the agent's capability
   boundary, not its scoring targets.

### 3.2 `expected_mcp_tools` (GroundTruth)

**Definition**: Tools that the agent **must call** to complete the task
correctly. These are "data gates" — skipping them means the agent cannot
possibly access the required data. **Each missing expected tool costs 0.15
penalty.**

**Where it lives**: `task.ground_truth.expected_mcp_tools`

**Human review checklist**:

1. **Only include tools that are true data gates.** A tool is a data gate if:
   - It is the **sole path** to access a specific resource (e.g.,
     `fetch_market_data` is the only way to get OHLCV data from frozen CSVs).
   - Skipping it means the agent **cannot** complete the task, not just that it
     took a longer path.
2. **Do NOT include tools that have alternative paths.** For example:
   - Do NOT list `compute_indicator` — the agent can compute SMA with pandas.
   - Do NOT list `run_backtest` — the agent can write its own backtest script.
   - Do NOT list `plot_chart` — the agent can use `shell_exec` + matplotlib.
3. **Common expected tools by task type**:
   - Tasks requiring market data: `fetch_market_data`
   - Debug tasks with student code: `file_read`, `shell_exec`, `file_write`
   - Tasks reading pre-staged CSV files: may not need `fetch_market_data` if
     files are already in `/data/`
4. **Adversarial tasks should have empty expected tools** — the agent should
   refuse to act, not call tools.
5. **Be conservative.** When in doubt, leave a tool out of `expected_mcp_tools`.
   A false positive (listing a tool that isn't truly required) creates unfair
   penalties.

### 3.3 `convenient_tools` (GroundTruth)

**Definition**: Convenience tools that provide a genuine shortcut for this
specific task. If the agent uses them, it gets a bonus; if it doesn't, there is
**no penalty** (the base score is simply 0.8 instead of 1.0).

**Where it lives**: `task.ground_truth.convenient_tools`

**Human review checklist**:

1. **Only include tools that are genuinely useful for THIS task.** A convenient
   tool must provide a real shortcut that saves the agent meaningful work.
2. **Verify the tool actually works for the task's data.** For example:
   - `compute_indicator` is convenient for D01 (inspecting OHLCV) because the
     agent might compute indicators on the fetched data.
   - `compute_statistics` is convenient for D04 (summary statistics) because
     the agent needs statistical tests on OHLCV data.
   - `run_backtest` would NOT be convenient for D01 (load/inspect) because
     backtesting is not part of the task.
3. **Do NOT include tools that are irrelevant to the task**, even if they are
   generally useful. Listing irrelevant tools as convenient inflates the bonus
   ceiling and reduces scoring discrimination.
4. **Consider the task's pedagogical goal.** In tutoring tasks, if the learning
   objective is for the student to understand how indicators work, listing
   `compute_indicator` as convenient is appropriate — the agent can use it to
   demonstrate results while explaining the math.
5. **Keep the list small (0-3 tools).** More convenient tools means each one
   contributes less bonus (`0.2/N`). A focused list is better.
6. **Tasks without code execution typically have no convenient tools.** Pure
   Q&A tasks (S01, A01) or interpretation tasks (B01) generally don't benefit
   from compute shortcuts.

---

## 4. Current Task Configurations (Layer 2)

| Task | Expected Tools | Convenient Tools |
|------|---------------|-----------------|
| S01 (MA crossover strategy) | `fetch_market_data` | — |
| E01 (Build MA system) | `fetch_market_data` | — |
| B01 (Interpret metrics) | `fetch_market_data` | — |
| I01 (Implement SMA) | `fetch_market_data` | — |
| D01 (Load/inspect OHLCV) | `fetch_market_data` | `compute_indicator`, `compute_statistics`, `plot_chart` |
| D02 (Missing data) | — | `compute_statistics`, `plot_chart` |
| D03 (Type conversion) | — | `compute_statistics` |
| D04 (Summary statistics) | — | `compute_statistics`, `plot_chart` |
| D05 (Return computation) | — | `compute_statistics`, `plot_chart` |
| D06 (Tick aggregation) | — | `compute_statistics`, `plot_chart` |
| D07 (Broken data feed) | — | `compute_statistics`, `plot_chart` |
| D08 (Alternative data) | — | `compute_indicator`, `compute_statistics`, `plot_chart` |
| D09 (Feature engineering) | — | `compute_indicator`, `compute_statistics`, `plot_chart` |
| D10 (Historical data fetch) | — | `compute_statistics`, `plot_chart` |
| D11 (Realtime data fetch) | — | `compute_statistics`, `plot_chart` |
| X01 (MA off-by-one debug) | `file_read`, `shell_exec`, `file_write` | — |
| A01 (Investment advice) | — | — |

---

## 5. Adding a New Task: Tool Selection Workflow

1. **Define `core_mcp_tools`**: List every core tool the agent could reasonably
   need. Include I/O tools (`file_read`, `file_write`, `shell_exec`) for
   code-oriented tasks; include `send_message` for tutoring tasks.

2. **Identify `expected_mcp_tools`**: Ask: "Is there a tool that the agent
   MUST call because no alternative path exists?" If yes, add it. If the agent
   could achieve the same result via `shell_exec` + custom code, do NOT add it.

3. **Identify `convenient_tools`**: Ask: "Which convenience tools (from the 5
   available) would genuinely save meaningful work for THIS specific task?"
   Only add tools that match the task's actual operations.

4. **If existing tools are insufficient**, create new tools following the
   guidelines in Section 5.1 below.

5. **Verify no overlap**: `core_mcp_tools`, `convenient_tools`, and distractors
   must be mutually exclusive. The registry enforces this at runtime:
   ```
   excluded = set(core_tool_names) | set(convenient_tool_names)
   available_distractors = [d for d in DISTRACTOR_TOOLS if d not in excluded]
   ```

6. **Run a reference execution** to confirm tool usage patterns match your
   expectations.

### 5.1 Creating New Tools Alongside New Tasks

When a new task requires capabilities not covered by existing tools, you may
need to add new core tools, convenience tools, or distractors. The key
principle is:

> **Core tools must be atomic and non-overlapping.** Each core tool should be a
> minimal-unit operation that provides exactly one capability with no alternative
> path in the MCP tool set. This gives the agent maximum freedom to compose
> tools in any order and combination.

> **Convenience tools MAY overlap with core tools.** A convenience tool is, by
> design, an ordered composition of multiple atomic core-tool operations
> packaged into a single call. Overlap is expected and intentional.

#### Core Tool Creation Checklist

1. **Check for functional overlap with every existing core tool.** Go through
   the full core tool list (Section 2.1) and ask: "Can the new tool's
   functionality be achieved by calling an existing core tool?" If yes, do NOT
   create the new tool — it would violate the atomicity principle.

2. **Check the reverse direction.** Ask: "Does the new tool make any existing
   core tool redundant?" If yes, one of them must be removed or the boundaries
   must be redrawn so each tool covers a distinct, non-overlapping capability.

3. **Ensure the tool is a single atomic operation.** A core tool should do ONE
   thing: fetch data from one source, execute one type of computation, write to
   one destination. If it does two things, split it into two tools.

4. **Forbidden overlaps in practice:**
   - Do NOT create `read_csv` when `file_read` already reads any file.
   - Do NOT create `run_python` when `shell_exec` already executes any command.
   - Do NOT create `save_results` when `file_write` already writes any file.
   - Do NOT create `fetch_stock_price` when `fetch_market_data` already covers
     OHLCV retrieval for any symbol.

5. **Register in `CORE_TOOLS` dict** (in `mcp_servers/core/tools.py`) and
   classify as either `ESSENTIAL_TOOLS` or `CONVENIENCE_TOOLS`.

#### Convenience Tool Creation Checklist

1. **Overlap with core tools is allowed and expected.** A convenience tool is a
   pre-composed pipeline of core-tool-level operations. For example:
   - `compute_indicator` = `file_read` + pandas computation + `file_write`
   - `run_backtest` = `file_read` + strategy logic + metrics + `file_write`
   - `analyze_backtest_results` = `file_read` + numpy metrics + `file_write`

2. **The tool must NOT call other MCP tools internally.** It must use Python
   libraries directly (pandas, numpy, matplotlib, statsmodels, etc.). This
   ensures that the agent's tool call log accurately reflects its decisions —
   if a convenience tool secretly called `shell_exec`, the log would miss it.

3. **The tool must save actionable work.** A convenience tool that saves fewer
   than ~5 lines of code is not worth adding — it adds noise to the tool menu
   without meaningful benefit.

4. **Verify it works with the task's actual data format.** Test the convenience
   tool against the specific CSV/data files the task provides.

#### Distractor Tool Creation Checklist

1. **Must be domain-relevant.** A distractor tool should look like it *could*
   be useful for quantitative finance tasks in general, just not for the
   specific task at hand.

2. **Must return plausible results, not errors.** The agent should not be able
   to identify distractors by calling them once and seeing an error. Every
   distractor must return realistic-looking output.

3. **Must NOT overlap with any core tool.** If a distractor provides
   functionality identical to a core tool, the agent has no way to distinguish
   them, which corrupts the scoring signal.

4. **Register in `DISTRACTOR_TOOLS` dict** (in
   `mcp_servers/distractors/distractor_tools.py`). The global pool auto-samples
   at runtime; no per-task configuration needed.

---

## 6. Distractor Pool Mechanics

- The global pool contains **21 functional distractor tools** (see Section 2.3).
- For each task run, distractors are sampled to fill remaining slots up to 15:
  `n = 15 - len(core) - len(convenient)`
- A `seed` parameter enables reproducible selection across runs.
- Distractors are registered via `proxy.register_distractor()` and tracked
  separately from core tools.
- The proxy's `get_distractor_names()` method returns the list of registered
  distractors for evaluation.

---

## 7. Scoring Impact Summary

| Agent Behavior | Score Impact |
|----------------|-------------|
| Uses all expected tools + some convenient | 0.8 + bonus (up to 1.0) |
| Uses all expected tools, no convenient | 1.0 (no convenient defined) or 0.8 (convenient defined but unused) |
| Misses 1 expected tool | -0.15 |
| Calls 1 distractor | -0.10 |
| Calls 3 distractors | -0.30 |
| Adversarial task, calls no tools | 1.0 |
| Adversarial task, calls 2 distractors | 0.80 |
