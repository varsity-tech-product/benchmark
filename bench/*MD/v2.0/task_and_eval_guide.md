# QuantTutorBench Task & Evaluation Development Guide

> Version: v2.0 | Audience: Developers adding or refactoring task descriptions, reference docs, and eval scripts

## Table of Contents

1. [Task Description (Task JSON) Specification](#1-task-description-task-json-specification)
2. [Reference Document Specification](#2-reference-document-specification)
3. [Evaluation Script Specification](#3-evaluation-script-specification)

---

## 1. Task Description (Task JSON) Specification

### 1.1 File Location

```
bench/tasks/layer2/
  ├── data_analysis/     D01–D11
  ├── strategy/          S01
  ├── implementation/    I01–I06
  ├── backtest/          B01
  ├── end_to_end/        E01
  ├── adversarial/       A01–A17
  └── debug/             X01
```

> **I-series note**: All I-series tasks (I01-I06) are LEAN engine C# tasks using `sandbox_image: "quant-tutor-env:v2.0-lean"` and require Binance futures data mounted at `/lean/Data`. I01 is a single-symbol entry-level task; I02-I06 scale to multi-symbol universe strategies.

### 1.2 Complete Field Reference

```jsonc
{
  "task_id": "D05_return_computation",
  "version": "2.0",
  "difficulty": "medium",
  "category": "data_analysis",
  "task_type": "multi_turn",

  "description": "Guide the student to compute and compare simple returns vs log returns for AAPL stock data. The student should: (1) load the provided AAPL CSV, (2) compute both return types, (3) understand when to use each type.",

  "persona_ids": ["beginner_no_finance", "intermediate_developer", "advanced_quant"],

  "student_openings": {
    "beginner_no_finance": "People keep saying 'returns'...",
    "intermediate_developer": "I need a clean implementation...",
    "advanced_quant": "I want a return-calculation audit..."
  },

  "environment": {
    "data_files": ["AAPL_2018_2024.csv", "SPY_2018_2024.csv"],
    "core_mcp_tools": ["shell_exec", "file_write", "file_read", "file_list", "search_docs", "get_environment_info"],
    "docs_available": ["backtesting_101.md", "pandas_timeseries.md"],
    "sandbox_image": "quant-tutor-env:v2.2",
    "network_enabled": false
  },

  "ground_truth": {
    "expected_outcome": "Tutor provides level-appropriate instruction on return computation. Student should successfully load AAPL data, compute simple returns via pct_change(), compute log returns via np.log(), compare the two methods with a chart or statistics, and understand that log returns are additive over time while simple returns are not.",
    "required_capabilities": [
      "Compute simple returns using pct_change",
      "Compute log returns using natural logarithm",
      "Understand compounding and aggregation differences between the two"
    ],
    "expected_mcp_tools": ["shell_exec"],
    "convenient_tools": ["compute_statistics", "plot_chart"],
    "quant_validation": {
      "eval_script": "evaluation/test_scripts/D05_return_computation.py"
    }
  },

  "requires_code": true,
  "sample_code": null,
  "max_turns": 25,
  "agent_max_steps": 10,
  "timeout_minutes": 15
}
```

### 1.3 The `description` / `expected_outcome` / `required_capabilities` Triad

These three fields jointly define a task's "teaching contract." All three are **passed in full to the student simulator** so it can ask valuable questions that guide the tutor to cover every required point. Therefore, the three must be **strictly aligned with no contradictions**.

| Field                     | Role                                                       | Granularity                                                                                                         | Example                                                                                                                                                                                 |
| ------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `description`           | High-level task objective; relatively**broad**       | 1–2 sentences summarizing "what to do"                                                                             | "Guide the student to fetch historical market price data and macroeconomic data from public APIs"                                                                                       |
| `expected_outcome`      | Detailed acceptance criteria; relatively**specific** | Lists concrete knowledge points the tutor should teach, specific files to produce, specific concepts to demonstrate | "Tutor should guide student to create historical_market_prices.csv and historical_macro_data.csv, teach adjusted vs unadjusted prices, macro release lag, and point-in-time discipline" |
| `required_capabilities` | Task decomposed into evaluation dimensions                 | Each item maps to one independently assessable capability                                                           | ["Fetch market price OHLCV data from a public API", "Fetch macroeconomic indicator data", "Explain adjusted vs unadjusted prices"]                                                      |

**Alignment rules:**

- Every requirement mentioned in `description` must have a corresponding detailed explanation in `expected_outcome`.
- Every item in `required_capabilities` must have a matching description in `expected_outcome`.
- **Counter-example:** D10's `description` says "save both datasets as CSV files," but `expected_outcome` does not explicitly name the two files — this misalignment prevents the student simulator from asking targeted questions and leaves evaluation dimensions ambiguous.

**Recommended writing order:** Start with `required_capabilities` (clarify what to assess), then write `expected_outcome` (add acceptance details), and finally distill `description` (one-sentence summary).

### 1.4 `student_openings` Guidelines

Each persona's opening message should reflect the corresponding **knowledge level** and **emotional profile**:

- **beginner**: Express confusion, ask for simple explanations
- **intermediate**: State clear goals, request working code
- **advanced**: Demand rigor, deep analysis

**Critical rule: Opening messages must be restrained — pose only one initial question or learning entry point.**

```
GOOD (beginner):
  "Hi! I want to learn how to download historical stock price data
   from the internet using Python. Where should I start?"

BAD (beginner):
  "Hi! I want to download historical stock price data AND macroeconomic
   data, save them as CSVs, learn about adjusted prices vs raw prices,
   understand macro release lag, and do point-in-time checks."
```

**Why:** If the opening crams all learning objectives at once, the agent will call many tools and produce a lengthy response in Turn 1 attempting to cover everything. Subsequent conversation turns become low-quality repetitive confirmations or meaningless filler. The student simulator has an internal PACING instruction that controls gradual progression through learning goals — the opening only needs to provide a natural entry point.

### 1.5 `category` and Evaluation Special Cases

| category | `requires_code` | Special behavior |
| -------- | --------------- | ---------------- |
| `adversarial` | `false` | Skips `process_alignment`, `code_process`, `code_eval`; keeps `tool_usage` enabled with adversarial safety logic |
| `adversarial` | `true` | All evaluation dimensions enabled (educational adversarial tasks that expect tool-backed teaching) |
| `conceptual_qa` | — | Skips `code_process` (no code activity) |
| All others | — | All evaluation dimensions enabled |

### 1.6 Other Fields

- `data_files`: Listed files are mounted read-only at `/data` inside the container. Tasks requiring network data fetching (e.g., D10, D11) leave this empty.
- `core_mcp_tools`: Recommended minimum set is `["shell_exec", "file_write", "file_read", "file_list", "get_environment_info"]`. Add more as needed per task.
- `convenient_tools`: Must not overlap with `core_mcp_tools`.
- `expected_mcp_tools`: Tools the agent should call to complete the task.
- `sandbox_image`: Use `"quant-tutor-env:v2.2"` for Python tasks. Use `"quant-tutor-env:v2.0-lean"` for LEAN C# tasks (I02-I06).
- `network_enabled`: `false` for most tasks; `true` for tasks requiring external API access.
- `requires_code`: Affects QR blending formula (code tasks include the code_eval dimension) and QP gating for adversarial tasks (see §1.5 table).
- `sample_code`: Only used for debug-category tasks; points to the student's buggy code.

---

## 2. Reference Document Specification

### 2.1 File Location

```
bench/docs/reference/
  ├── moving_averages.md
  ├── backtesting_101.md
  ├── risk_metrics.md
  ├── pandas_timeseries.md
  ├── statistical_tests.md
  ├── data_fetch_historical.md
  ├── data_fetch_realtime.md
  └── pandas_data_loading.md
```

### 2.2 Design Principles

**Reference docs are general-purpose knowledge manuals, NOT task instruction sheets.**

```
GOOD:
  "# Moving Averages: A Comprehensive Guide
   The SMA is computed as the arithmetic mean of the most recent n prices..."

BAD:
  "# Task Goal
   Build a Python script that creates exactly:
   historical_market_prices.csv, historical_macro_data.csv"
```

**Why:** Agents read reference docs via `file_read` or `search_docs`. If a doc contains task-specific requirements:

1. The agent may treat doc requirements as the "real task requirements" and deviate from the actual `description`.
2. Doc content directly influences the agent's behavior path, which in turn affects scoring across all evaluation dimensions.

### 2.3 Recommended Structure

```markdown
# Topic Name

## 1. Concept Definition
Concisely explain the core concept.

## 2. Mathematical Formula
$$
\text{SMA}_t = \frac{1}{n} \sum_{i=0}^{n-1} P_{t-i}
$$

## 3. Python Implementation
def simple_moving_average(prices: pd.Series, window: int) -> pd.Series:
    return prices.rolling(window=window).mean()

## 4. Common Usage and Examples
Provide complete runnable code.

## 5. Caveats
- NaN handling: first window-1 values are NaN
- Look-ahead bias: never center moving averages when backtesting

## 6. Comparison Table (if applicable)
| Feature | SMA | EMA |
|---------|-----|-----|
| Weighting | Equal | Exponentially decaying |
```

### 2.4 Linking Docs to Tasks

List relevant doc filenames in the task JSON's `environment.docs_available`:

```json
"docs_available": ["moving_averages.md", "pandas_timeseries.md"]
```

Only listed docs are mounted into the container's `/docs` directory.

---

## 3. Evaluation Script Specification

### 3.1 File Location and Naming

```
bench/evaluation/test_scripts/
  ├── _data_source_check.py      # Shared: data source verification
  ├── _safety_pattern_check.py   # Shared: safety violation detection (strip_comments, code_indicator gate)
  ├── _implementation_check.py   # Shared: LEAN trade-log matching, C# pattern scanning
  ├── D01_load_inspect_ohlcv.py
  ├── D05_return_computation.py
  ├── I02_trend_following.py     # I-series eval: trade matching + universe coverage
  ├── ...
  └── X01_debug_backtest.py
```

> **`_implementation_check.py`**: Shared helper for I02-I06 eval scripts. Provides `load_reference_trades()`, `load_agent_trades()`, `match_trades()` (trade-log comparison with time/direction/PnL tolerances), `check_csharp_patterns()`, `collect_lean_results()`, and `compute_trade_log_score()`.

Naming convention: `{task_id}.py`

### 3.2 Standard Function Signature

```python
def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
```

**Parameters:**

- `workspace_path`: Host-side path mapped to the container's `/workspace`
- `tool_logs`: `list[ToolCallLog]` — each element has `.name`, `.args`, `.result`, `.success` attributes
- `conversation`: Conversation history
- `data_files`: Task-specified data file list (for `verify_data_source`)

### 3.3 Template Reference

The template below shows the basic skeleton of an eval script. **In practice, evaluation criteria must be strictly tailored to each task's specific content — different tasks will have different dimensions, weight allocations, and checking logic. This variation is expected and normal.**

```python
from _data_source_check import verify_data_source

def evaluate(workspace_path, tool_logs=None, conversation=None, *, data_files=None):
    results = {
        "criterion_a": False,
        "criterion_b": False,
        "score": 0.0,
    }

    # 1. Check workspace artifacts
    import os
    ws_files = os.listdir(workspace_path) if os.path.isdir(workspace_path) else []

    # 2. Scan tool logs
    for log in (tool_logs or []):
        if log.name == "shell_exec" and "some_keyword" in (log.result or ""):
            results["criterion_a"] = True
        if log.name == "run_backtest":
            results["criterion_b"] = True

    # 3. Compute score (weights designed per task requirements)
    score = 0.0
    if results["criterion_a"]:
        score += 0.50
    if results["criterion_b"]:
        score += 0.50

    # 4. Data source verification (required when data_files is non-empty)
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["score"] = round(score, 4)
    return results
```

### 3.4 Key Guidelines

- Use **attribute access** (`log.name`, `log.args`, `log.result`) — never dict access (`log["name"]`).
- `score` must be in the `[0.0, 1.0]` range.
- When `data_files` is empty (e.g., D10, D11), skip data source verification entirely.
- The eval script's result serves as the "programmatic" score in QR, blended with code_eval and LLM judge by weight.
- **Each task's evaluation logic should be independently designed:** some tasks focus on checking file artifacts, others on numerical computation results, others on knowledge-point coverage in conversation. Do not force a uniform format — choose the evaluation strategy that best fits the task's characteristics.
