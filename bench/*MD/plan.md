# QuantTutorBench Evaluation System Reform Plan

## Goal

Reform the QP (Quality Process) and QR (Quality Result) evaluation system to:
1. Remove dependency on fixed MCP tool paths (agents can use any tool combination)
2. Add real code execution testing to QR
3. Replace 4 tool-bound QP metrics with Process Reasonableness approach
4. Optimize Step Efficiency with 3 sub-dimensions + reference anchoring
5. Build Reference Execution infrastructure as scoring anchor
6. Redesign tool set based on empirical agent tool-selection research

---

## Research Foundation & Design Principles

This reform is informed by Amplifying.ai's "Claude Code Picks" study (2026),
which analyzed 2,430 open-ended prompts across 3 models, 4 project types,
and 20 tool categories. Key findings that directly apply to QuantTutorBench:

### Finding 1: Build vs. Buy Is Systemic, Not Accidental

Custom/DIY implementations appeared in 12% of all picks and ranked #1 across
12 of 20 categories. In some domains the rate was extreme: Feature Flags 69%,
Python Authentication 100%. This validates our observation that agents prefer
`write_file` + `shell_exec` over `run_backtest` + `analyze_backtest_results`.
**This is not agent misbehavior — it is a systemic trait of LLM agents.**

### Finding 2: Near-Monopoly vs. Competitive Market

Tool categories split into three tiers of selection consistency:

| Tier | Pick Rate | Agent Behavior | Our Tool Mapping |
|------|-----------|---------------|------------------|
| Near-Monopoly | >75% | Agent almost always selects this tool | `fetch_market_data` (data gate) |
| Strong Default | 50-75% | Preferred but alternatives exist | `compute_indicator` |
| Competitive | <50% | Agent freely builds custom solutions | `run_backtest`, `analyze_backtest_results` |

**Implication**: Only Near-Monopoly tools should be part of the core tool set.
Competitive tools should be offered as optional convenience, never required.

### Finding 3: Run-to-Run Consistency Is Limited

- 73% perfect agreement across 3 runs of the same prompt
- 76% stability across 5 different phrasings of the same question
- Category-level consistency ranges from 40% (Real-time) to 93% (CI/CD)

**Implication**: Even the same agent on the same task produces different tool
paths ~27% of the time. Any evaluation based on exact tool-path matching has
a ~75% theoretical ceiling. Result-based evaluation approaches ~100%.

### Finding 4: Model Personality Affects Tool Preference

| Model | Custom/DIY Rate | Behavior |
|-------|-----------------|----------|
| Sonnet 4.5 | 9.5% | Conservative — favors established tools |
| Opus 4.5 | 10.5% | Balanced |
| Opus 4.6 | 11.4% | Forward-looking — higher DIY preference |

**Implication**: Multi-model judge averaging is essential not just for noise
reduction, but to cancel out systematic bias in "build vs. buy" evaluation.
LLM judge prompts must include explicit anti-bias neutrality clauses.

### Finding 5: Recency Gradient Causes Tool Preference Shifts

Prisma dropped from 79% (Sonnet 4.5) to 0% (Opus 4.6) in ORM picks.
Celery dropped from 100% to 0% in Python background jobs.
These are cliff-edge switches, not gradual transitions.

**Implication**: Any hardcoded `expected_tools` list will break when the
agent-under-test model is updated. Scoring must be tool-choice-agnostic.

### Design Principles (derived from research)

1. **Work with agent behavior, not against it.** Build vs. Buy is intrinsic.
   Tool design should provide tools agents "must use" (data gates), not tools
   agents "could but might not" use (convenience wrappers).

2. **Evaluate results, not paths.** 73% run-to-run consistency means path-based
   scoring has an inherent ~25% noise floor. Result-based scoring does not.

3. **Neutralize judge bias with multi-model averaging + explicit anti-bias
   prompt clauses.** Different models systematically favor different approaches.

4. **Set per-category path tolerance.** Data-loading tasks have Near-Monopoly
   consistency (~93%); implementation tasks have Competitive consistency (~40-50%).
   Evaluation strictness should match.

---

## Final Scoring Architecture

```
Task Score = 0.70 x Quant Agent Score + 0.30 x Tutor Score   (unchanged)

Quant Agent Score = 0.50 x QR + 0.50 x QP

QR (reformed):
  0.30 x Programmatic Check     (existing eval scripts, relaxed regex)
  0.30 x Code Execution QR      (NEW: static analysis + log-based exec check + output verification)
  0.40 x LLM-as-Judge Result QR (NEW: reference-anchored result evaluation)

QP (reformed):
  0.20 x tool_usage             (NEW: mathematical, replaces tool_correctness + argument_correctness)
  0.20 x process_reasonableness (NEW: replaces mcp_use + multi_turn_mcp, evaluates logic quality)
  0.15 x process_alignment      (NEW: sub-problem coverage vs reference)
  0.15 x step_efficiency        (OPTIMIZED: 3 sub-dimensions + reference anchoring)
  0.10 x code_process_quality   (NEW: iterative refinement, test-before-deliver, error recovery)
  0.10 x role_adherence         (unchanged)
  0.10 x topic_adherence        (unchanged)
```

### LLM Judge Scoring Principle: 5-Point Ordinal Scale

All LLM-judged dimensions (process_reasonableness, step_efficiency sub-dimensions,
code_process_quality, result_judge) use a **5-point ordinal scale** instead of
continuous 0-1 floats. This prevents LLM judges from clustering scores around
0.7-0.8 (a well-documented bias).

**Mandatory format for every LLM-judged dimension:**
```
Select ONE score from: {0.0, 0.25, 0.5, 0.75, 1.0}

- 1.0  — [explicit behavioral criteria for this dimension]
- 0.75 — [explicit behavioral criteria]
- 0.5  — [explicit behavioral criteria]
- 0.25 — [explicit behavioral criteria]
- 0.0  — [explicit behavioral criteria]

You MUST select one of these five values. Do not output intermediate values.
When in doubt between two levels, select the LOWER one.
```

Each dimension's 5-level criteria are defined in the relevant phase (Phases 2-5).
This technique is derived from DeepEval's ToolCorrectnessMetric and
StepEfficiencyMetric, which achieve high discriminability through discrete
ordinal scales + adversarial "when in doubt, lower" bias.

---

## Phase 0A: Tool Set Redesign

### What
Reclassify the 14 core tools into Essential vs. Convenience tiers based on
the Near-Monopoly / Competitive framework from the Amplifying.ai research.
Essential tools are the only data-access gates — agents cannot bypass them.
Convenience tools are optional efficiency shortcuts that agents may ignore
in favor of custom implementations via `shell_exec` + `file_write`.

### Files to Modify
- `bench/mcp_servers/core/tools.py` — rewrite `run_backtest` + `plot_chart`, remove `format_table` + `compare_series`, add tier constants
- `bench/mcp_servers/core/tool_wrappers.py` — remove `make_run_backtest` and `make_plot_chart` (no longer wrap shell_exec)
- `bench/mcp_servers/registry.py` — update `CODE_EXEC_TOOLS` (remove `run_backtest`, `plot_chart`), expose tier metadata
- `bench/mcp_servers/distractors/distractor_tools.py` — complete redesign (see below)
- `bench/tasks/layer2/**/*.json` — update `expected_mcp_tools` to minimal essential only, update `core_mcp_tools` to remove deleted tools

### Tool Independence Principle

**Critical rule: Convenience tools MUST NOT internally call Essential tools.**

Each convenience tool is a self-contained operation that directly uses Python
libraries (pandas, numpy, matplotlib, statsmodels) — never by invoking
`shell_exec` or `file_write` underneath. This ensures:

1. **Two clean, independent paths exist for every task:**
   - **Convenience path**: `fetch_market_data` → `compute_indicator` → `run_backtest` → `analyze_backtest_results` → `plot_chart` (no `shell_exec` needed)
   - **DIY path**: `fetch_market_data` → `file_write` + `shell_exec` (agent writes all code)
   - **Hybrid path**: mix of both (e.g., `compute_indicator` for SMA, then `shell_exec` for custom backtest logic)

2. **No hidden nesting** — calling `run_backtest` does not secretly log a
   `shell_exec` call in tool_logs. The tool tiers remain truly orthogonal.

3. **`expected_tools` per task = only the unavoidable data gates** — if the
   agent CAN complete the entire task using convenience tools alone, then
   `shell_exec` and `file_write` are NOT in expected_tools for that task.

### Tool Audit & Actions

| Tool | Tier | Current State | Action |
|------|------|---------------|--------|
| `shell_exec` | Essential | OK | Keep as-is |
| `file_write` | Essential | OK | Keep as-is |
| `file_read` | Essential | OK | Keep as-is |
| `file_list` | Essential | OK | Keep as-is |
| `fetch_market_data` | Essential | OK — sole data gate | Keep as-is |
| `search_docs` | Essential | OK | Keep as-is |
| `get_environment_info` | Essential | OK | Keep as-is |
| `compute_indicator` | Convenience | **Genuine** — 1 call = ~10 lines pandas code. Self-contained (uses pandas directly). | Keep as-is |
| `analyze_backtest_results` | Convenience | **Genuine** — computes 8 metrics from returns CSV. Self-contained (uses numpy/pandas directly). | Keep as-is |
| `compute_statistics` | Convenience | **Genuine** — runs ADF/correlation/cointegration. Self-contained (uses statsmodels directly). | Keep as-is |
| `plot_chart` | Convenience | **Broken** — internally calls `shell_exec`. Must rewrite to use `exec()` + matplotlib directly. | **REWRITE: remove shell_exec dependency** |
| `run_backtest` | Convenience | **Broken** — just wraps `shell_exec("python {script}")`. Not a real shortcut. | **REWRITE: accept strategy params, use pandas directly** |
| `format_table` | Convenience | **Trivial** — CSV→markdown, near-zero value | **REMOVE** |
| `compare_series` | Convenience | **Redundant** — overlaps with `analyze_backtest_results` | **REMOVE** |

### Tool Rewrites

**`run_backtest` — rewrite as self-contained strategy executor:**

```python
def run_backtest(
    data_path: str,
    strategy: str,        # "ma_crossover", "rsi_threshold", "bollinger_breakout"
    params: dict,         # {"fast_window": 20, "slow_window": 50}
    start: str = "",
    end: str = "",
) -> str:
    """Run a complete backtest given strategy type and parameters.

    Self-contained: uses pandas/numpy directly. Does NOT call shell_exec.
    Agent does NOT need to write a script — just provide strategy + params.

    Returns: structured JSON with equity curve summary, trade log,
    and performance metrics (Sharpe, return, drawdown, win rate).
    """
    import numpy as np
    import pandas as pd

    df = pd.read_csv(_resolve_path(data_path), parse_dates=["Date"])
    # ... apply built-in strategy logic ...
    # ... compute equity curve, metrics ...
    # ... save results to workspace as CSV/JSON ...
    return json.dumps(metrics, indent=2)
```

**`plot_chart` — rewrite to use exec() directly, not shell_exec:**

```python
def plot_chart(python_code: str) -> str:
    """Execute matplotlib Python code and save chart as PNG.

    Self-contained: uses exec() directly. Does NOT call shell_exec.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chart_path = os.path.join(_workspace_dir(), f"chart_{int(time.time())}.png")
    code_with_save = python_code + f"\nplt.savefig('{chart_path}', ...)\nplt.close()"
    exec(code_with_save, {"plt": plt, "pd": __import__('pandas'), ...})
    return f"Chart saved to {chart_path}" if os.path.isfile(chart_path) else "Error"
```

### Tool Tier Classification (Post-Audit)

```python
# bench/mcp_servers/core/tools.py

ESSENTIAL_TOOLS = {
    # Data gates and I/O channels — no alternative exists.
    "fetch_market_data",   # sole data gate (frozen CSVs)
    "file_read",           # sole file reading channel
    "file_write",          # sole file writing channel
    "shell_exec",          # sole code execution channel (DIY path only)
    "file_list",           # sole directory listing
    "search_docs",         # sole documentation access
    "get_environment_info",# sole environment introspection
}

CONVENIENCE_TOOLS = {
    # Self-contained shortcuts. Each uses Python libraries directly.
    # None of them call shell_exec, file_write, or any other Essential tool.
    "compute_indicator",          # pandas rolling/ewm — 1 call = ~10 lines
    "run_backtest",               # built-in strategies — 1 call = ~30 lines (REWRITTEN)
    "analyze_backtest_results",   # numpy metrics — 1 call = ~30 lines
    "compute_statistics",         # statsmodels tests — 1 call = ~15 lines
    "plot_chart",                 # matplotlib exec — 1 call = ~5 lines (REWRITTEN)
}
# Removed: format_table (trivial), compare_series (redundant)
```

### `expected_tools` Per Task: Minimal Essential Only

`expected_tools` in each task JSON should list ONLY the essential tools the
agent cannot avoid, given that convenience tools exist.

**Example — S01_ma_crossover (SMA crossover strategy):**

```json
// OLD (includes convenience tools + DIY tools — conflates both paths):
"expected_mcp_tools": [
    "fetch_market_data", "shell_exec", "file_write", "plot_chart",
    "compute_indicator", "run_backtest", "analyze_backtest_results"
]

// NEW (only the unavoidable data gate):
"expected_mcp_tools": ["fetch_market_data"]
```

Why only `fetch_market_data`? Because:
- **Convenience path**: `fetch_market_data` → `compute_indicator` → `run_backtest` → `analyze_backtest_results` → `plot_chart` — no `shell_exec` or `file_write` needed
- **DIY path**: `fetch_market_data` → `file_write` + `shell_exec` — `compute_indicator` etc. not needed
- The ONLY tool both paths share is `fetch_market_data` (data gate)

**Example — X01_ma_offbyone (debug an existing script):**

```json
// NEW: agent must read the buggy file + needs shell_exec to test fixes
"expected_mcp_tools": ["file_read", "shell_exec", "file_write"]
```

Debug tasks require reading the buggy code, modifying it, and running it.
Convenience tools don't help here — no built-in "debug" shortcut exists.

**Example — A01_investment_advice (adversarial — refuse bad advice):**

```json
// NEW: agent may not need ANY tools if it answers from knowledge
"expected_mcp_tools": []
```

The agent should refuse the investment advice request. If it chooses to
demonstrate why the student's claim is wrong, it might use `fetch_market_data`,
but this is optional — a correct refusal with explanation is sufficient.

### expected_tools Audit (All 7 Layer 2 Tasks)

| Task | Category | Old expected_tools | New expected_tools | Rationale |
|------|----------|-------------------|-------------------|-----------|
| D01 | data_analysis | fetch, shell, file_write, ... | `["fetch_market_data"]` | Data loading is the only gate; analysis can be done via convenience tools |
| S01 | strategy | fetch, shell, file_write, compute_indicator, run_backtest, ... | `["fetch_market_data"]` | Entire workflow possible via convenience path |
| X01 | debug | fetch, shell, file_read, compute_indicator | `["file_read", "shell_exec", "file_write"]` | Must read buggy code, edit it, run it |
| B01 | backtest | shell, run_backtest, fetch, analyze, ... | `["fetch_market_data"]` | Convenience tools cover the full workflow |
| I01 | implementation | fetch, shell, compute_indicator, file_write | `["fetch_market_data"]` | Can implement SMA via compute_indicator; or DIY |
| E01 | end_to_end | fetch, shell, file_write, ... | `["fetch_market_data"]` | Complex but convenience tools cover it |
| A01 | adversarial | shell, run_backtest, fetch, analyze, plot | `[]` | Agent should refuse; tools optional for demonstration |

### Agent Prompt Verification

Verified: the agent-facing prompts in `config/prompt_config.py` do NOT
contain language that forces or names specific convenience tools. The system
prompt uses soft guidance ("use tools proactively", "choose the tool whose
capabilities best match each sub-task") and never lists tool names.
**No prompt modifications are needed after tool tiering.**

The real tool guidance comes from tool availability + tool descriptions
(injected via MCP schema), which is the intended design — agents discover
tools by reading descriptions, not by prompt instruction.

### Evaluation Implications
- Using an Essential tool is neutral (mandatory, no bonus, no penalty).
- Using a Convenience tool is rewarded via mathematical bonus in tool_usage
  score (fewer total steps to achieve the same result).
- NOT using a Convenience tool is never penalized — the agent built the
  equivalent functionality through Essential tools, which is equally valid.
- Distractor tools are heavily penalized in tool_usage score if called.

### Distractor Tool Redesign

Current distractors are too obvious — descriptions contain self-defeating hints
like "Requires network access", "Not related to financial analysis", "Not needed
for historical data analysis". Agents skip them trivially by reading descriptions.

**New design principle:** Distractors must be **functional, domain-relevant, but
task-irrelevant.** They return plausible-looking results (not error messages).
The agent must understand the task's actual requirements to decide not to use them.

**Redesigned distractor pool:**

```python
# bench/mcp_servers/distractors/distractor_tools.py

DISTRACTOR_TOOLS = {
    "compute_var": {
        "description": "Compute Value at Risk (VaR) for a returns series at a given confidence level",
        "func": _compute_var,        # returns valid VaR number
        # Deceptive: sounds quant-relevant, but SMA/backtest tasks don't need VaR
    },
    "fit_garch_model": {
        "description": "Fit a GARCH(1,1) model to a returns series and forecast volatility",
        "func": _fit_garch,          # returns valid GARCH params
        # Deceptive: volatility forecasting is quant work, but not needed for most tasks
    },
    "optimize_portfolio": {
        "description": "Run mean-variance optimization to find optimal portfolio weights",
        "func": _optimize_portfolio,  # returns valid weights
        # Deceptive: single-asset tasks don't need portfolio optimization
    },
    "run_monte_carlo": {
        "description": "Run Monte Carlo simulation for price path generation",
        "func": _run_monte_carlo,    # returns simulated paths
        # Deceptive: tasks use historical data, not simulated paths
    },
    "fetch_fundamentals": {
        "description": "Fetch fundamental data (P/E, EPS, revenue, market cap) for a symbol",
        "func": _fetch_fundamentals, # returns realistic-looking fundamental data
        # Deceptive: technical analysis tasks don't need fundamentals
    },
    "compute_greeks": {
        "description": "Compute option Greeks (delta, gamma, vega, theta) for a given position",
        "func": _compute_greeks,     # returns valid Greeks
        # Deceptive: equity strategy tasks don't need options Greeks
    },
    "screen_stocks": {
        "description": "Screen stocks by technical or fundamental criteria across a universe",
        "func": _screen_stocks,      # returns a filtered list
        # Deceptive: tasks focus on a specific given symbol, not stock screening
    },
    "backtest_pairs_trade": {
        "description": "Backtest a statistical arbitrage pairs trading strategy on two correlated series",
        "func": _backtest_pairs,     # returns pairs trade metrics
        # Deceptive: MA crossover task is not pairs trading
    },
    "compute_beta": {
        "description": "Compute the beta coefficient of an asset relative to a benchmark index",
        "func": _compute_beta,       # returns valid beta
        # Deceptive: relevant to quant analysis but not to the specific task
    },
    "estimate_covariance": {
        "description": "Estimate the covariance matrix for a set of asset returns using shrinkage methods",
        "func": _estimate_cov,       # returns valid covariance matrix
        # Deceptive: multi-asset concept, irrelevant to single-asset tasks
    },
}
```

**Key differences from current distractors:**
1. **No self-defeating hints** in descriptions — no "requires network", "not available"
2. **Return plausible results**, not error messages — agent cannot detect by calling once
3. **All domain-relevant** — every distractor is a legitimate quant finance tool
4. **Task-irrelevant** — agent must reason about its actual task to skip them
5. **Higher deception quality** — `compute_var` and `compute_beta` look useful for
   any quant task, but the specific task at hand doesn't need them

### Test Command (Phase 0A)
```bash
# Validate tier classification and distractor redesign
cd /Users/richsion/Desktop/benchmark/bench && \
python -c "
from mcp_servers.core.tools import ESSENTIAL_TOOLS, CONVENIENCE_TOOLS, CORE_TOOLS
from mcp_servers.distractors.distractor_tools import DISTRACTOR_TOOLS
print(f'Essential: {len(ESSENTIAL_TOOLS)} tools')
print(f'Convenience: {len(CONVENIENCE_TOOLS)} tools')
print(f'Core total: {len(CORE_TOOLS)} tools')
print(f'Distractors: {len(DISTRACTOR_TOOLS)} tools')
assert ESSENTIAL_TOOLS | CONVENIENCE_TOOLS == set(CORE_TOOLS.keys()) - {'send_message'}, 'Tier mismatch'
# Verify no distractor has 'error' key (old design) — should have 'func' key
for name, info in DISTRACTOR_TOOLS.items():
    assert 'func' in info, f'{name} missing func (still old error-based design?)'
    assert 'not available' not in info['description'].lower(), f'{name} has self-defeating description'
print('All checks passed')
"
```

---

## Phase 0B: Reference Execution Infrastructure

### What
Create the infrastructure for generating and storing reference executions.
A strong model runs each task once with a detailed prompt. Its trace, results,
and conversation are recorded as the scoring anchor.

### Files to Create
- `bench/reference/generate_reference.py` — CLI to run oracle model on each task
- `bench/reference/reference_store.py` — load/save reference data per task
- `bench/reference/refs/` — directory storing per-task JSON reference files

### Reference JSON Schema (per task)
```json
{
  "task_id": "S01_ma_crossover",
  "oracle_model": "openai/gpt-5.2",
  "generated_at": "2026-02-27T...",
  "trace_summary": [
    {"step": 1, "action": "fetch AAPL data 2020-2024", "tool": "fetch_market_data", "result_summary": "1008 rows"},
    {"step": 2, "action": "compute SMA(20) and SMA(50)", "tool": "compute_indicator", "result_summary": "..."}
  ],
  "step_count": 5,
  "key_results": {
    "sharpe_ratio": 1.23,
    "annual_return": 0.156,
    "max_drawdown": -0.089,
    "total_trades": 44
  },
  "workspace_files": ["strategy.py", "backtest_analysis.json", "equity.png"],
  "full_trace": [ ... raw tool_logs ... ],
  "conversation": [ ... full conversation turns ... ]
}
```

### Implementation
- `generate_reference.py` reuses the existing `BenchmarkOrchestrator.run_single_task()` flow
- After execution, captures `proxy.get_logs()`, workspace file listing, and key numerical outputs
- Summarizes trace into `trace_summary` (action + tool + result per step)
- Extracts key_results from workspace files (parse backtest_analysis.json, etc.)
- Saves to `bench/reference/refs/{task_id}.json`

### Test Command (Phase 0B)
```bash
# Generate reference for one task to validate infrastructure
cd /Users/richsion/Desktop/benchmark/bench && \
python reference/generate_reference.py \
  --task S01_ma_crossover \
  --agent openai \
  --docker \
  --max-turns 15

# Verify reference file was created
cat bench/reference/refs/S01_ma_crossover.json | python -m json.tool | head -30
```

---

## Phase 1: Code Execution QR (new QR component)

### What
Add code quality evaluation to QR. Three layers:
- Layer A: Static Analysis (AST parse, syntax check) — 20% of Code QR
- Layer B: Execution Result Analysis (extract from tool_logs, no re-execution) — 40% of Code QR
- Layer C: Output Verification (compare agent outputs to reference) — 40% of Code QR

### Files to Create
- `bench/evaluation/code_eval.py` — all three layers in one module

### Files to Modify
- `bench/orchestrator/orchestrator.py` — call code_eval in Phase 4
- `bench/evaluation/scoring.py` — integrate code_eval score into QR

### Key Design Decisions

**Layer A: Static Analysis** (no execution needed)
```python
def evaluate_code_static(workspace_path, tool_logs):
    # 1. Collect all .py files from workspace + file_write args in tool_logs
    # 2. ast.parse() each file — check syntax validity
    # 3. Count functions, classes, imports — basic structure metrics
    # 4. Flag dangerous patterns (bare except, exec(), eval())
    # Returns: {"has_code": bool, "syntax_valid": bool, "structure_score": float}
```

**Layer B: Execution Result Analysis** (from tool_logs, NO re-execution)

The agent already executes code via `shell_exec` during the task. The tool logs
capture stdout, stderr, exit code, and success status for every `shell_exec` call.
Layer B analyzes these recorded results instead of re-running scripts.

```python
def evaluate_code_execution(tool_logs, workspace_path):
    # 1. Extract all shell_exec calls from tool_logs
    # 2. Identify code execution calls (python *.py, python -c '...', etc.)
    # 3. For each execution, parse the recorded result:
    #    - exit_code == 0 and no [stderr] → Success (1.0)
    #    - SyntaxError in stderr → 0.0
    #    - ImportError in stderr → 0.3 (environment issue)
    #    - RuntimeError/TypeError/ValueError → 0.1 (logic error)
    #    - Timeout → 0.0
    # 4. Score = weighted average of final execution results per script
    #    (if same script executed multiple times, use LAST execution — reflects
    #     iterative debugging, which is evaluated separately in code_process)
    # 5. Edge case: agent wrote .py files via file_write but never executed them
    #    → flag as "untested code", score contribution = 0.0 for those files
    # Returns: {"exec_calls_found": int, "success_rate": float, "untested_files": [...]}
```

**Why not re-execute?**
- Agent already ran the code; results are in tool_logs. Re-executing is redundant.
- Removes dependency on container being alive during evaluation.
- "Wrote code but never ran it" is itself a signal — penalized here and in
  code_process_quality's "test-before-deliver" metric (Phase 5).

**Layer C: Output Verification** (needs reference data)
```python
def evaluate_code_output(workspace_path, reference, tool_logs):
    # 1. Extract numerical outputs from workspace (backtest_analysis.json, CSV files)
    #    AND from shell_exec stdout in tool_logs
    # 2. Compare to reference["key_results"]
    # 3. Per-metric relative error → score mapping:
    #    <5% error → 1.0, 5-15% → 0.75, 15-30% → 0.5, >30% → 0.25
    # 4. Check artifact completeness: how many of reference["workspace_files"] exist?
    # Returns: {"numerical_accuracy": float, "output_completeness": float, "score": float}
```

### Integration into orchestrator.py

In `run_single_task()`, after existing eval scripts run (no container dependency):

```python
# Phase 4 addition: Code Execution QR
from evaluation.code_eval import evaluate_code_combined
code_eval_result = evaluate_code_combined(
    workspace_path=workspace_path,
    tool_logs=proxy.to_dict(),
    reference=reference_store.load(task.task_id),  # None if no reference yet
)
# code_eval_result["score"] feeds into QR alongside existing eval script score
```

### Integration into scoring.py

Modify `compute_task_score()` to accept the new code_eval_score:

```python
# OLD: quant_result_score = eval_script_score
# NEW:
quant_result_score = (
    0.30 * programmatic_score      # existing eval script
  + 0.30 * code_eval_score         # new code execution QR
  + 0.40 * llm_result_judge_score  # new LLM judge (Phase 3)
)
```

### Test Command (Phase 1)
```bash
# Test code eval on a single task
cd /Users/richsion/Desktop/benchmark/bench && \
python run_benchmark.py run-single \
  --task I01_implement_sma \
  --persona beginner_no_finance \
  --agent openai \
  --docker \
  --max-turns 10 2>&1 | tee /tmp/qtb_phase1_I01.log

# Run 3 tasks to validate code eval integration
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase1 && \
for task in I01_implement_sma S01_ma_crossover X01_ma_offbyone; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase1/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
echo "Waiting for all tasks..."
wait
echo "Done. Check logs in /tmp/qtb_phase1/"

# Quick validation: grep for new code_eval scores in logs
grep -r "code_eval\|Code Execution\|static_analysis\|execution_test" /tmp/qtb_phase1/
```

---

## Phase 2: Step Efficiency Optimization

### What
Replace the current 5-level patched prompt with a 3-sub-dimension evaluation:
- Action Economy (0.4 weight): step count ratio vs reference
- Redundancy Avoidance (0.3 weight): detect wasted/repeated calls
- Logical Sequencing (0.3 weight): evaluate action order

### Files to Modify
- `bench/evaluation/deepeval_metrics/process_metrics.py`
  - Replace `_patch_step_efficiency_template()` (lines 729-771)
  - Modify `_build_trace_dict()` (lines 774-804) to include reference info
  - Modify `_async_eval_step_efficiency()` to accept reference_trace parameter

### Changes Detail

**1. New prompt** (replaces lines 740-765):
- Adds reference step count and trace summary to context
- Defines 3 explicit sub-dimensions with scoring criteria
- Action Economy uses quantitative ratio (agent_steps / reference_steps)
- Redundancy Avoidance lists explicit red flags and acceptable patterns
- Logical Sequencing evaluates data dependency ordering
- Returns JSON with 3 sub-scores + weighted overall

**2. Action Economy ratio with research-calibrated tolerance band**:

The Amplifying.ai study found 73% run-to-run consistency, meaning ~27% of
path variation is inherent noise. The ratio thresholds are widened accordingly:

```python
ratio = agent_substantive_steps / reference_step_count

# Thresholds calibrated to 27% natural path variance:
if ratio <= 1.3:   action_economy = 1.0    # within natural variance
elif ratio <= 1.6:  action_economy = 0.75   # slightly above variance
elif ratio <= 2.2:  action_economy = 0.5    # noticeably more steps
elif ratio <= 3.0:  action_economy = 0.25   # significantly more steps
else:               action_economy = 0.0    # excessively verbose

# "Substantive steps" excludes benign reads (file_list, get_environment_info)
# and counts only state-changing or information-producing tool calls.
```

**3. Convenience tool efficiency bonus in prompt**:

The prompt explicitly instructs the judge to recognize that Convenience tools
reduce step count but their absence is not a penalty:

```
NOTE ON TOOL TIERS:
The agent had access to convenience tools (compute_indicator, run_backtest,
analyze_backtest_results, etc.) that bundle multi-step operations into one call.
- If the agent USED these tools, it likely completed the task in fewer steps.
  This is efficient and should be reflected in the Action Economy score.
- If the agent BUILT equivalent functionality using shell_exec + file_write,
  this is equally valid. Judge the step count relative to the complexity of
  the custom implementation, not against the convenience-tool shortcut.
```

**4. Pass reference trace into step_efficiency evaluation**:
- `evaluate_all_process_metrics()` gets new parameter: `reference_trace`
- `_build_process_tasks_for_model()` passes it to `_async_eval_step_efficiency()`
- `_async_eval_step_efficiency()` injects reference summary into prompt
- If no reference available, falls back to current prompt (without ratio comparison)

**5. Parse structured response**:
- Current: expects `{"score": float, "reason": str}`
- New: expects `{"action_economy": float, "redundancy_avoidance": float,
  "logical_sequencing": float, "overall": float, "reason": str}`
- Overall = 0.4 * action_economy + 0.3 * redundancy + 0.3 * sequencing
- Fallback: if response only has "score", use it directly (backward compat)

### Test Command (Phase 2)
```bash
# Run same 3 code tasks + 2 non-code tasks to validate step efficiency
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase2 && \
for task in I01_implement_sma S01_ma_crossover D01_load_inspect_ohlcv B01_interpret_metrics X01_ma_offbyone; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase2/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
echo "Waiting..."
wait
echo "Done."

# Compare step_efficiency scores: should now show 3 sub-dimensions
grep -A3 "step_efficiency" /tmp/qtb_phase2/*.log
```

---

## Phase 3: QR LLM-as-Judge with Reference

### What
Add LLM-as-Judge evaluation to QR, using reference execution results as anchor.

### Files to Create
- `bench/evaluation/deepeval_metrics/result_judge.py` — LLM judge for result quality

### Files to Modify
- `bench/orchestrator/orchestrator.py` — call result_judge in Phase 4
- `bench/evaluation/scoring.py` — integrate into QR formula

### Prompt Design
```
You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

TASK: {task_description}
TASK CATEGORY: {category}

REFERENCE RESULT (expert baseline):
- Key metrics: {ref_key_results}
- Files produced: {ref_workspace_files}
- Output summary: {ref_output_summary}

AGENT RESULT:
- Key metrics: {agent_key_results}
- Files produced: {agent_workspace_files}
- Output summary: {agent_output_summary}

EVALUATE:
1. Numerical Accuracy (0-1): Are quantitative results close to reference?
2. Completeness (0-1): Did agent produce all expected outputs?
3. Correctness (0-1): Even if numbers differ, is the methodology sound?

{category_specific_rubric}  -- reuse from quant_geval.py CATEGORY_CONFIGS

Return JSON: {"numerical_accuracy": float, "completeness": float,
              "correctness": float, "overall": float, "reason": "..."}
```

The `category_specific_rubric` is pulled from the existing `CATEGORY_CONFIGS` in
`quant_geval.py` (CODE_QUALITY_CRITERIA for implementation tasks, DATA_INTERPRETATION_CRITERIA
for data analysis tasks, etc.).

### Integration
```python
# scoring.py — new QR formula
quant_result_score = (
    0.30 * programmatic_score      # existing eval script (relaxed)
  + 0.30 * code_eval_score         # Phase 1: code execution QR
  + 0.40 * llm_result_judge_score  # this phase: LLM judge
)
```

When reference is unavailable (e.g., first run before Phase 0 completes),
`llm_result_judge_score` falls back to standalone evaluation without reference
comparison (just evaluates the agent's result on its own merits using the
category rubric).

### Test Command (Phase 3)
```bash
# Full 7-task run with all QR components active
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase3 && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase3/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait
echo "Done."

# Verify all 3 QR components are scored
grep -E "programmatic_score|code_eval|llm_result_judge|Quant Result" /tmp/qtb_phase3/*.log
```

---

## Phase 4: QP Process Reasonableness + Process Alignment

### What
Replace the 4 tool-bound metrics (tool_correctness, argument_correctness, mcp_use,
multi_turn_mcp) with 2 new metrics:
- **process_reasonableness**: evaluates execution logic independent of tool choice
- **process_alignment**: compares sub-problem coverage to reference trace

### Files to Create
- `bench/evaluation/deepeval_metrics/process_reasonableness.py`

### Files to Modify
- `bench/evaluation/deepeval_metrics/process_metrics.py`
  - Remove `_async_eval_tool_correctness()`
  - Remove `_async_eval_argument_correctness()`
  - Remove `_async_eval_mcp_use()`
  - Remove `_async_eval_multi_turn_mcp()`
  - Add `_async_eval_process_reasonableness()`
  - Add `_async_eval_process_alignment()`
  - Update `_build_process_tasks_for_model()` to use new metrics
  - Update `evaluate_all_process_metrics()` signature (remove expected_tool_names dependency)
- `bench/orchestrator/orchestrator.py` — stop passing expected_tool_names, pass reference
- `bench/orchestrator/schemas.py` — expected_mcp_tools field becomes optional/deprecated

### Process Reasonableness Prompt

The prompt includes explicit anti-bias neutrality clauses derived from the
Amplifying.ai finding that different LLM judges have systematically different
attitudes toward Build-vs-Buy (9.5% to 11.4% Custom/DIY rate variance).

```
You are evaluating the PROCESS QUALITY of an AI tutoring agent's execution.

TASK: {task_description}
TASK CATEGORY: {category}

AGENT EXECUTION TRACE:
{full_trace}

═══════════════════════════════════════════════════
NEUTRALITY RULES (MUST follow):
- Custom/DIY implementations (writing code from scratch using shell_exec +
  file_write) are EQUALLY VALID as using provided convenience tools.
- Do NOT penalize the agent for choosing to build functionality manually
  when a higher-level tool was available. Both paths are legitimate.
- An agent that writes its own SMA calculation via shell_exec is not
  inferior to one that calls compute_indicator("SMA"). Judge only whether
  the calculation logic is correct.
- Not using tools when the task can be answered from knowledge alone is a
  VALID choice. Do not penalize the absence of tool calls if the agent's
  textual explanation is correct and complete.
- Evaluate the LOGIC and CORRECTNESS of the approach, not the tool selection.
═══════════════════════════════════════════════════

EVALUATE ON 4 DIMENSIONS:

1. Problem Decomposition (0-1):
   Did the agent break the task into logical sub-steps?
   Did it identify what data/information was needed before acting?

2. Execution Soundness (0-1):
   Were actions logically sound for achieving the goal?
   Were there any clearly wrong or harmful operations?
   (Reminder: there is no "correct tool" — evaluate LOGIC, not tool choice)

3. Error Handling (0-1):
   When errors occurred, did the agent correctly diagnose the root cause?
   Did it fix the actual problem rather than suppressing the symptom?
   Did it avoid repeating the same failing action?

4. Pedagogical Integration (0-1):
   Did the agent explain its process to the student while executing?
   Were intermediate results shared and interpreted for learning purposes?

{category_specific_process_criteria}

Return JSON: {"problem_decomposition": float, "execution_soundness": float,
              "error_handling": float, "pedagogical_integration": float,
              "overall": float, "reason": "..."}
```

Category-specific criteria examples:
- **backtest**: "Should follow data→strategy→execution→analysis flow"
- **debugging**: "Should follow read→diagnose→fix→verify flow"
- **data_analysis**: "Should follow load→explore→analyze→interpret flow"
- **implementation**: "Should follow design→code→test→iterate flow"

### Process Alignment Prompt

Includes per-category path tolerance derived from the Amplifying.ai
consistency data (40-93% by category). Categories with Competitive-level
tool consistency get more lenient alignment scoring.

```
You are comparing two execution traces for the same task.

TASK: {task_description}
TASK CATEGORY: {category}

REFERENCE TRACE (expert execution, {ref_step_count} steps):
{reference_trace_summary}

AGENT TRACE ({agent_step_count} steps):
{agent_trace_summary}

═══════════════════════════════════════════════════
PATH TOLERANCE CONTEXT:
This task category has a path tolerance level of {path_tolerance}.
A tolerance of 1.0 means many valid paths exist — be very lenient about
path differences. A tolerance of 0.4 means paths should converge —
significant deviations are more likely to indicate process issues.
═══════════════════════════════════════════════════

EVALUATE (NOT path matching — sub-problem coverage):

1. Coverage (0-1): Did the agent address the same key sub-problems that
   the reference addressed? (e.g., both obtained data, both computed metrics)
   Different tools/methods for the same sub-problem count as covered.

2. Depth (0-1): Did the agent reach a similar depth of analysis?
   (e.g., reference computed 5 risk metrics, agent only computed 2)

3. Soundness Delta (0-1): Compared to the reference, were there any clearly
   inferior methodological choices? (e.g., reference used vectorized ops,
   agent used a slow loop — result is same but process quality differs)

Return JSON: {"coverage": float, "depth": float, "soundness_delta": float,
              "overall": float, "reason": "..."}
```

### Per-Category Path Tolerance Configuration

Based on the Near-Monopoly / Competitive classification. Categories where
agents have strong natural tool convergence get stricter path evaluation;
categories with high path variance get more lenient evaluation.

```python
# bench/evaluation/deepeval_metrics/process_reasonableness.py

CATEGORY_PATH_TOLERANCE = {
    # Near-Monopoly tier: agents converge on similar approaches
    "data_analysis":    0.9,   # data loading has clear patterns
    "conceptual_qa":    1.0,   # may not use tools at all — maximum tolerance

    # Strong Default tier: preferred path exists but alternatives work
    "strategy":         0.7,   # strategy explanation has moderate variance
    "debug":            0.6,   # debug flow has recognizable patterns

    # Competitive tier: agents diverge significantly — lenient scoring
    "implementation":   0.5,   # many valid implementation approaches
    "backtest":         0.5,   # run_backtest vs shell_exec equally valid
    "end_to_end":       0.4,   # complex tasks, maximum path divergence

    # Special case
    "adversarial":      1.0,   # no tools expected — skip alignment entirely
}

# Tolerance affects process_alignment weight dynamically:
# effective_weight = base_weight (0.20) * tolerance
# Freed weight redistributed to process_reasonableness.
# Example: backtest task (tolerance=0.5) → alignment weight = 0.10,
#          extra 0.10 goes to process_reasonableness (0.25 → 0.35)
```

### Test Command (Phase 4)
```bash
# Run all 7 tasks with reformed QP (no more tool_correctness/mcp_use)
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase4 && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase4/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait
echo "Done."

# Verify old metrics are gone, new metrics are present
grep -E "process_reasonableness|process_alignment|tool_correctness|mcp_use" /tmp/qtb_phase4/*.log
# Expected: process_reasonableness and process_alignment present
# Expected: tool_correctness and mcp_use absent
```

---

## Phase 5: Code Process Quality (QP)

### What
Add programmatic + LLM-judged code process evaluation to QP.

### Files to Create
- `bench/evaluation/deepeval_metrics/code_process.py`

### Files to Modify
- `bench/evaluation/deepeval_metrics/process_metrics.py` — integrate code_process into QP

### Programmatic Metrics (no LLM needed)
Extract from tool_logs:

```python
def evaluate_code_process_programmatic(tool_logs):
    # 1. Iterative Refinement: file_write → exec → [error] → file_write → exec → [success]
    #    Score = productive_iterations / total_iterations

    # 2. Test Before Deliver: last turn has successful exec before final response?
    #    Score = 1.0 if yes, 0.0 if no

    # 3. Error Recovery: after failure, does next exec of same script succeed?
    #    Score = recovered_count / failure_count

    # 4. Code Evolution: if same file written N times, are changes substantive?
    #    Score based on diff analysis (avoid trivial rewrites)
```

### LLM-Judged Code Process
Build a `code_activity_trace` from tool_logs showing WRITE/EXEC pairs with diff summaries,
then ask LLM to evaluate:
1. Debugging Competence (0-1)
2. Incremental Development (0-1)
3. Code Explanation Quality (0-1) — cross-reference with conversation turns

### Combined Code Process Score
```python
code_process_score = (
    0.50 * programmatic_code_process  # metrics 1-4 averaged
  + 0.50 * llm_code_process_judge    # metrics 1-3 averaged
)
```

For tasks with no code (conceptual Q&A, adversarial), this metric is skipped
and its weight (0.15) is redistributed to process_reasonableness and step_efficiency.

### Test Command (Phase 5)
```bash
# Run code-heavy tasks to validate code process metrics
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase5 && \
for task in I01_implement_sma S01_ma_crossover X01_ma_offbyone E01_build_ma_system; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase5/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait

# Check code process metrics appear
grep -E "code_process|iterative_refinement|test_before_deliver|error_recovery" /tmp/qtb_phase5/*.log
```

---

## Phase 6: Scoring Integration + Cleanup

### What
Wire everything together. Update scoring.py with final formula.
Remove deprecated code. Update orchestrator flow.

### Files to Modify

**`bench/evaluation/scoring.py`**:
- `compute_task_score()`: accept new score components
- New QR formula: 0.30 programmatic + 0.30 code_eval + 0.40 llm_result_judge
- New QP formula: use reformed metrics
- `compute_benchmark_kpis()`: replace TMS (Tool Mastery Score) with new aggregate
- Handle graceful fallback when reference is unavailable

**`bench/orchestrator/orchestrator.py`**:
- Phase 4 eval flow: call all new evaluators in correct order
- Pass container_manager and reference_data to evaluators
- Remove expected_mcp_tools from eval call chain
- Ensure code_eval runs before container teardown (Phase 5)

**`bench/orchestrator/schemas.py`**:
- Make `expected_mcp_tools` optional (backward compat, deprecated)
- Add reference_id field to GroundTruth

**`bench/evaluation/deepeval_metrics/process_metrics.py`**:
- Final cleanup: remove dead code for old metrics
- Update `evaluate_all_process_metrics()` signature
- Update aggregate_process_score calculation with new weights
- Remove knowledge_retention from metric list (or rewrite prompt)

**`bench/evaluation/deepeval_metrics/mcp_metrics.py`**:
- `compute_tool_precision_recall()` — keep as optional diagnostic, remove from scoring

**`bench/run_benchmark.py`**:
- `cmd_run_single()`: display new metric names in output
- Update `_QP_METRICS` list (line 387-396) with new metric names

### Updated Display Format
```
--- Process Metrics (QP) ---
  Metric                     Score    Status
  ------------------------- --------  ------
  process_reasonableness      0.8200    PASS
    problem_decomposition     0.9000
    execution_soundness       0.8500
    error_handling            0.7000
    pedagogical_integration   0.7500
  process_alignment           0.7800    PASS
    coverage                  0.8500
    depth                     0.7500
    soundness_delta           0.7500
  step_efficiency             0.8500    PASS
    action_economy            0.9000
    redundancy_avoidance      0.8000
    logical_sequencing        0.8500
  code_process_quality        0.7200    PASS
  role_adherence              0.9500    PASS
  topic_adherence             0.9000    PASS
  -------------------------  --------  ------
  AGGREGATE                   0.8200
```

### Test Command (Phase 6 — Full Integration)
```bash
# Full benchmark run with all reforms active
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase6 && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase6/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait
echo "All tasks done."

# Validate complete reformed output
for f in /tmp/qtb_phase6/*.log; do
  echo "=== $(basename $f) ==="
  grep -E "Quant Result|Quant Process|Overall|process_reasonableness|process_alignment|step_efficiency|code_process|role_adherence|topic_adherence|code_eval|llm_result_judge|AGGREGATE" "$f"
  echo ""
done

# Run full benchmark (not just single tasks) to validate scoring aggregation
cd /Users/richsion/Desktop/benchmark/bench && \
python run_benchmark.py run \
  --agent openai \
  --docker \
  --max-turns 10 \
  --layer 2 \
  --trials 1 > /tmp/qtb_phase6/full_run.log 2>&1

# Check final KPIs
grep -E "OAS|QAI|TEI|Overall Agent" /tmp/qtb_phase6/full_run.log
```

---

## Phase 7: Relaxation of Existing Eval Scripts

### What
Relax the regex patterns in existing per-task eval scripts to be implementation-agnostic.

### Files to Modify
All 7 scripts in `bench/evaluation/test_scripts/`:

| Script | Current Problem | Fix |
|--------|----------------|-----|
| `I01_implement_sma.py` | Requires `.rolling().mean()` pattern | Accept any SMA: rolling, ta.sma, manual loop, numpy |
| `X01_ma_offbyone.py` | Requires `rolling(20)` literal | Check output correctness, not code pattern |
| `S01_ma_crossover.py` | Sharpe range [-0.5, 3.0] too wide | Tighten with reference comparison (Phase 0 data) |
| `E01_build_ma_system.py` | `len(py_files) >= 2` = modular | Remove, this is QP not QR |
| `D01_load_inspect_ohlcv.py` | Keyword matching ("mean", "std") | Check actual data loaded, not keyword presence |
| `A01_investment_advice.py` | 6 regex bad-advice patterns | Add LLM judge for safety (more robust than regex) |
| `B01_interpret_metrics.py` | Check keyword presence | Verify actual metric values present |

Principle: eval scripts should check WHAT was produced (results), not HOW it was produced
(code patterns). Code pattern checking moves to code_eval.py (Phase 1) and
code_process.py (Phase 5).

### Test Command (Phase 7)
```bash
# Re-run all tasks with relaxed eval scripts
cd /Users/richsion/Desktop/benchmark/bench && \
mkdir -p /tmp/qtb_phase7 && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_phase7/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait

# Compare scores with Phase 6 to ensure relaxation didn't break scoring
for short in D01 S01 X01 B01 I01 E01 A01; do
  echo "=== $short ==="
  echo "Phase 6:" && grep "Quant Result" /tmp/qtb_phase6/$short.log
  echo "Phase 7:" && grep "Quant Result" /tmp/qtb_phase7/$short.log
  echo ""
done
```

---

## Final Validation

### A/B Comparison Test
Run the same agent on all tasks with both old and new evaluation:

```bash
# OLD evaluation (current system, for baseline)
cd /Users/richsion/Desktop/benchmark/bench && \
git stash && \
mkdir -p /tmp/qtb_old && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_old/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait
git stash pop

# NEW evaluation (reformed system)
mkdir -p /tmp/qtb_new && \
for task in D01_load_inspect_ohlcv S01_ma_crossover X01_ma_offbyone B01_interpret_metrics I01_implement_sma E01_build_ma_system A01_investment_advice; do
  short=$(echo $task | cut -d_ -f1)
  python run_benchmark.py run-single \
    --task $task \
    --persona beginner_no_finance \
    --agent openai \
    --docker \
    --max-turns 10 > /tmp/qtb_new/$short.log 2>&1 &
  echo "Started $short (PID $!)"
done
wait

# Side-by-side comparison
echo "Task    Old_QR  New_QR  Old_QP  New_QP  Old_Total  New_Total"
for short in D01 S01 X01 B01 I01 E01 A01; do
  old_qr=$(grep "Quant Result" /tmp/qtb_old/$short.log | awk '{print $NF}')
  new_qr=$(grep "Quant Result" /tmp/qtb_new/$short.log | awk '{print $NF}')
  old_qp=$(grep "Quant Process" /tmp/qtb_old/$short.log | awk '{print $NF}')
  new_qp=$(grep "Quant Process" /tmp/qtb_new/$short.log | awk '{print $NF}')
  old_t=$(grep "Overall:" /tmp/qtb_old/$short.log | awk '{print $NF}')
  new_t=$(grep "Overall:" /tmp/qtb_new/$short.log | awk '{print $NF}')
  printf "%-7s %-7s %-7s %-7s %-7s %-10s %-10s\n" "$short" "$old_qr" "$new_qr" "$old_qp" "$new_qp" "$old_t" "$new_t"
done
```

---

## File Change Summary

### New Files (6)
| File | Phase | Purpose |
|------|-------|---------|
| `bench/reference/generate_reference.py` | 0 | Oracle execution CLI |
| `bench/reference/reference_store.py` | 0 | Reference data load/save |
| `bench/evaluation/code_eval.py` | 1 | 3-layer code execution QR |
| `bench/evaluation/deepeval_metrics/result_judge.py` | 3 | LLM-as-Judge for QR |
| `bench/evaluation/deepeval_metrics/process_reasonableness.py` | 4 | New QP metrics |
| `bench/evaluation/deepeval_metrics/code_process.py` | 5 | Code process quality |

### Modified Files (7)
| File | Phases | Changes |
|------|--------|---------|
| `bench/evaluation/deepeval_metrics/process_metrics.py` | 2,4,6 | Replace prompt, remove old metrics, add new |
| `bench/orchestrator/orchestrator.py` | 1,3,4,6 | Wire new evaluators, pass container_manager |
| `bench/evaluation/scoring.py` | 1,3,6 | New QR/QP formulas |
| `bench/orchestrator/schemas.py` | 6 | Deprecate expected_mcp_tools |
| `bench/run_benchmark.py` | 6 | Update display format |
| `bench/evaluation/test_scripts/*.py` (7 files) | 7 | Relax regex patterns |
| `bench/evaluation/deepeval_metrics/mcp_metrics.py` | 6 | Demote to diagnostic only |

### Directories to Create
```
bench/reference/
bench/reference/refs/
```
