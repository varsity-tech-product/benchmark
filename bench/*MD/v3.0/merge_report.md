# PR #5 Post-Merge System Architecture

> Branch: ewan (selective merge from master PR #5)
> Date: 2026-03-16

---

## 1. Project Structure

```
bench/
├── config/                          # Global configuration
│   ├── benchmark_config.py          # Dataset repo, image names, date ranges
│   ├── llm_config.py                # Model IDs, SDK switches, OAuth config
│   ├── model_resolver.py            # DeepEval model resolution + OAuth wrapper
│   ├── pricing.py                   # Token cost tracking (OpenRouter pricing)
│   ├── prompt_config.py             # Prompt construction + layered injection
│   ├── conditions.py                # Experiment conditions (agent/baseline/pure_llm)
│   └── benchmark_dates.py           # Backtest date ranges
│
├── orchestrator/                    # Task orchestration layer
│   ├── orchestrator.py              # 5-phase task execution pipeline
│   ├── container_manager.py         # Docker container lifecycle management
│   ├── schemas.py                   # TaskResult, EnvironmentConfig, etc.
│   ├── simulation.py                # DeepEval conversation simulator config
│   ├── runners/                     # Parallel execution
│   │   ├── job_runner.py            # Single-task job execution
│   │   └── parallel_runner.py       # Multi-task parallel scheduling
│   └── agent_adapters/              # Agent SDK adapters
│       ├── base_adapter.py          # Abstract base (token tracking)
│       ├── anthropic_adapter.py     # Claude SDK / Direct API dual mode
│       ├── openai_adapter.py        # OpenAI SDK / Direct API dual mode
│       ├── google_adapter.py        # Google ADK adapter
│       └── generic_adapter.py       # OpenRouter generic adapter
│
├── mcp_servers/                     # Tool system
│   ├── core/
│   │   ├── tools.py                 # Core tool definitions + Trial system (4 tools)
│   │   ├── trial_manager.py         # TrialManager (backtest trial orchestration)
│   │   ├── tool_executor.py         # In-container JSON-RPC daemon
│   │   └── tool_wrappers.py         # Timeout + container routing wrappers
│   ├── distractors/
│   │   └── distractor_tools.py      # 10 distractor tools
│   ├── proxy/
│   │   └── mcp_proxy.py             # Tool call logging proxy
│   └── registry.py                  # Tool registration + per-task assembly
│
├── evaluation/                      # Evaluation system
│   ├── deepeval_metrics/            # LLM-as-Judge dimensions
│   │   ├── tutor_conv_geval.py      # 7D teaching quality evaluation
│   │   ├── result_judge.py          # QR result judgment (completeness + correctness)
│   │   ├── process_metrics.py       # QP aggregate (7 weighted dimensions)
│   │   ├── process_reasonableness.py # Process reasonableness + alignment
│   │   ├── code_process.py          # Code development process quality
│   │   ├── tool_usage.py            # Tool usage scoring (pure math)
│   │   └── _scoring_utils.py        # Scoring utility functions
│   ├── test_scripts/                # Eval scripts organized by category
│   │   ├── implementation/          # I01-I10 (10)
│   │   ├── end_to_end/             # E01-E05 (5)
│   │   ├── debug/                   # X01-X10 (10)
│   │   ├── data_analysis/           # D01-D11 (11)
│   │   ├── strategy/               # S01-S06 (6)
│   │   ├── backtest/               # B01-B06 (6)
│   │   ├── adversarial/            # A01-A17 (17)
│   │   └── common/                 # Shared check modules (6)
│   └── rubrics/                     # Scoring rubric definitions
│
├── tasks/layer2/                    # 65 task definitions (by category)
│   ├── implementation/  (10)        # I-series: LEAN C# algorithm implementation
│   ├── end_to_end/      (5)         # E-series: end-to-end quant workflows
│   ├── debug/           (10)        # X-series: code debugging
│   ├── data_analysis/   (11)        # D-series: data analysis
│   ├── strategy/        (6)         # S-series: strategy research
│   ├── backtest/        (6)         # B-series: backtest engine
│   └── adversarial/     (17)        # A-series: adversarial scenarios
│
├── reference/                       # Reference implementations (by series)
│   ├── Implementation/              # I-series
│   │   ├── algorithms/              # Reference .cs algorithm files
│   │   └── result/                  # Reference trades/signals JSON
│   ├── end_to_end/                  # E-series
│   │   ├── algorithms/              # E02/E04/E05 reference .cs
│   │   └── result/
│   ├── debug/                       # X-series
│   │   ├── algorithms/              # X07-X10 fixed .cs
│   │   └── result/
│   └── script/                      # Reference generation scripts
│       ├── generate_lean_reference.py
│       ├── generate_reference.py
│       ├── generate_reference_signals.py
│       └── reference_store.py
│
├── docker/                          # Docker image definitions
│   ├── Dockerfile                   # Base image (quant-tutor-env:v2.2)
│   ├── Dockerfile.lean              # LEAN image (quant-tutor-env:v2.2-lean)
│   ├── lean-config.json             # LEAN engine configuration
│   └── run_backtest.sh              # Backtest wrapper script
│
├── scripts/                         # Operational scripts
│   ├── data_manager.py              # HuggingFace data download management
│   └── prepare_i_series_data.py     # I-series data preparation
│
└── run_benchmark.py                 # CLI entry point
```

---

## 2. Docker Sandbox Architecture

### 2.1 Dual-Image System

| Image | Base | Purpose | Task Categories |
|-------|------|---------|-----------------|
| `quant-tutor-env:v2.2` | Python 3.11-slim | Data analysis, strategy research, Python debugging | B/D/S/A + E01/E03 + X01-X06 |
| `quant-tutor-env:v2.2-lean` | Inherits v2.2 + .NET 10.0 + LEAN engine | C# algorithm compilation and execution | I01-I10 + E02/E04/E05 + X07-X10 |

Each task JSON specifies its image via the `sandbox_image` field.

### 2.2 Container Resource Quotas

```python
_RESOURCE_PRESETS = {
    "lean": ("1g", "2"),        # LEAN image: 1 GB memory, 2 CPUs
    "_default": ("768m", "1"),   # Standard image: 768 MB memory, 1 CPU
}
```

Automatically matched by `container_manager.py` based on the image name.

### 2.3 Container Mount Points

```
/workspace       ← read-write, agent working directory
/data            ← read-only, task data files (universe.json, CSVs, etc.)
/docs            ← read-only, reference documentation (lean_algorithm_guide.md, etc.)
/student_code    ← read-only, X-series debug tasks' student code
/lean/Data       ← read-only, LEAN image only: LEAN-format market data
```

### 2.4 Dynamic Injection

After container creation, the following files are injected via `docker cp` (no image rebuild required):

- `tools.py` → `/opt/bench/mcp_servers/core/tools.py`
- `tool_executor.py` → `/opt/bench/tool_executor.py`
- `trial_manager.py` → `/opt/bench/mcp_servers/core/trial_manager.py`
- `run_backtest.sh` → `/usr/local/bin/run_backtest`

`tool_executor.py` runs as a JSON-RPC daemon inside the container, receiving tool call requests from the host. `trial_manager.py` manages the trial state for iterative LEAN backtest tasks (I-series).

### 2.5 run_backtest.sh Workflow

6-step process (supports `--params` and `--run-id` arguments):

1. **Clean & Copy**: Remove ALL existing .cs files from `Algorithm.CSharp/` (prevents multi-class DLL conflicts), then copy the new Algorithm.cs into place
2. **Auto-detect class name**: Grep for `class X : QCAlgorithm`; fallback to first `public class`; update `config.json` with `algorithm-type-name`, `close-automatically=true`, and `results-destination-folder` synced to `$RESULTS_DIR`
3. **Build**: `dotnet build Algorithm.CSharp` only (stdout/stderr → `build_log.txt` to avoid pipe deadlock)
4. **Inject parameters**: If `--params` provided, merge JSON into `config.json`'s `parameters` dict
5. **Run LEAN engine**: `timeout $LEAN_RUN_TIMEOUT dotnet $LAUNCHER_DLL` (default 120s); output → `log.txt`; exit code 124 = timeout, non-zero = runtime failure
6. **Extract results**: Search multiple directories (`$RESULTS_DIR`, `/workspace/results`, Launcher bin) for:
   - `*-order-events.json` → `orders.json`
   - `*-statistics.json` → `summary.json` (fallback: `*-summary.json`)
   - `*-trades.json` → `trades.json` (fallback: extract `ClosedTrades` from main result JSON)
   - `log.txt` (captured during run)

Key design decisions:
- **`.cs` cleanup**: LEAN's `Algorithm.CSharp/` ships with hundreds of example files; without cleanup, the DLL contains multiple QCAlgorithm subclasses causing "Algorithm type name not found" errors
- **`close-automatically=true`**: Without this flag, LEAN calls `Console.Read()` after completion, blocking indefinitely in non-interactive Docker mode
- **120s timeout**: Sufficient for .NET cold build + backtest after the `close-automatically` fix (previously 300s to accommodate the blocking issue)

---

## 3. Data Management

### 3.1 Data Source

All runtime data is downloaded from HuggingFace and cached in `bench/data/hf_cache/`.

```python
ensure_data(series="lean")    # LEAN tasks: I.tar.gz → hf_cache/lean/I/
ensure_data(series="normal")  # Normal tasks: BDS/, X/, A/ → hf_cache/normal/
```

### 3.2 hf_cache Directory Layout

```
hf_cache/
├── lean/                        # LEAN container tasks (v2.2-lean)
│   ├── I/                       # LEAN market data + universe.json
│   ├── E/                       # E-series LEAN data (BTC_UTC.csv, E04_compound_bug.cs)
│   └── X/                       # LEAN debug student code (.cs files, X07-X10)
├── normal/                      # Standard container tasks (v2.2)
│   ├── BDEX/                    # Frozen CSVs (AAPL, SPY, BTC, etc.)
│   ├── A/                       # Adversarial task data
│   └── X/                       # Python debug student code (.py files, X01-X06)
└── docs/                        # Shared reference documentation (13 .md files)
```

### 3.3 Data Path Mapping

| Series | DataPaths Field | Value | Container Mount |
|--------|----------------|-------|-----------------|
| lean | `lean_data` | `hf_cache/lean/I/` | `/lean/Data` |
| lean | `data_search_dirs` | `[lean/I/, lean/E/, lean/X/]` | `/data` (staged) |
| lean | `student_code` | `hf_cache/lean/X/` | `/student_code` |
| lean | `docs` | `hf_cache/docs/` | `/docs` |
| normal | `data_search_dirs` | `[normal/BDEX/, normal/A/]` | `/data` (staged) |
| normal | `student_code` | `hf_cache/normal/X/` | `/student_code` |
| normal | `docs` | `hf_cache/docs/` | `/docs` |

The `/data` mount is populated by `_create_staged_dirs()`, which copies only the files listed in each task's `data_files` from `data_search_dirs` — not the entire directory.

Debug tasks resolve `student_code` from their own series: LEAN debug tasks (X07-X10) use `lean/X/`, Python debug tasks (X01-X06) use `normal/X/`.

---

## 4. Trial Backtest System

### 4.1 Design Goal

Allow the agent to iteratively optimize LEAN algorithms within a fixed budget, simulating a real quantitative development workflow.

### 4.2 Components

| Component | File | Function |
|-----------|------|----------|
| TrialManager | `trial_manager.py` | Manages trial state, auto-selects best trial |
| run_lean_backtest | `tools.py` | Compile + run + record one trial |
| submit_trial | `tools.py` | Submit current code state as a trial |
| select_submission | `tools.py` | Select final submission by trial ID |
| get_trial_status | `tools.py` | View all trials' status and metrics |

### 4.3 Configuration

The `environment.max_backtest_trials` field in each task JSON controls the trial budget (0 = trial system disabled).

### 4.4 Execution Flow

1. Orchestrator injects `max_backtest_trials` into environment variable `QTB_MAX_BACKTEST_TRIALS`
2. Agent iterates through development using the 4 trial tools
3. Phase 3.25 (Trial Finalization): if the agent did not call `select_submission`, the best trial is auto-selected
4. `result.trial_metadata` records all trial states

### 4.5 Implementation Details

**Status Detection** (`run_lean_backtest` in `tools.py`):

Exit-code-based detection replaces fragile string matching:
- Exit code 2 → `compile_error`
- Exit code 3 or 124 → `runtime_error`
- Exit code 0 + `summary.json` exists → check trade count from `totalPerformance.tradeStatistics.totalNumberOfTrades` (fallback: `statistics."Total Orders"`); if > 0 → `success`, else → `empty_trades`
- No `summary.json` → `runtime_error`

**Trade Count Source**: Read from `summary.json` (not `trades.json`), because LEAN's `closedTrades` array in the main result JSON may be empty even when trades were actually executed.

**`--run-id` Support**: When `--run-id` is used, `run_backtest.sh` syncs the LEAN config's `results-destination-folder` to `/workspace/results/{run_id}/`. The `run_lean_backtest` tool resolves the correct subdirectory path for reading `summary.json`, with a fallback to glob `*-summary.json` filename patterns.

**Trial Snapshot** (`trial_manager.py`):

`snapshot_and_record()` copies results from both the base `results/` directory and any immediate subdirectories (created by `--run-id` runs) into the trial's flat `results/` directory. This ensures trial metrics are correctly captured regardless of whether `--run-id` was used.

---

## 5. Prompt Layered Injection System

### 5.1 Category Filtering

Tasks are classified via `_IEX_CATEGORIES = {"implementation", "end_to_end", "debug"}` to enable targeted prompt injection.

### 5.2 Prompt Segments

| Segment | Constant | Injection Condition | Injection Location |
|---------|----------|--------------------|--------------------|
| A | `_PROMPT_A_WAIT_OVERRIDE` | IEX + requires_code | build_tutor_context → inside AVAILABLE DOCUMENTATION |
| B | `_PROMPT_B_CODE_TASK_EXECUTION` | requires_code (all categories) | build_tutor_context → inside TOOL USAGE DIRECTIVE |
| C | `_PROMPT_C_BEYOND_SNIPPETS` | IEX + requires_code | build_tutor_context → inside CODE IN RESPONSES |
| D | `_PROMPT_D_IMPLEMENTATION_TRACKING` / `_ABSTRACT_PUSH` | IEX + requires_code | build_scenario → after TURN BUDGET |
| E | `_PROMPT_E_BACKTEST_TRIAL_SYSTEM` | max_backtest_trials > 0 | build_tutor_context → standalone section |

### 5.3 API

```python
segments = get_filtered_prompt_segments(
    category="implementation",  # task category
    requires_code=True,         # whether the task requires code
    max_backtest_trials=5,      # trial budget
)
# segments.a_wait_override → non-empty (implementation ∈ IEX and requires_code)
# segments.b_code_task_execution → non-empty (requires_code)
# segments.e_backtest_trial_system → non-empty (max_backtest_trials > 0)
```

Non-IEX categories (e.g., strategy, data_analysis) receive only Prompt B even when `requires_code=True`.

---

## 6. Tool System

### 6.1 Tool Categories

| Type | Count | Source | Description |
|------|-------|--------|-------------|
| Core tools | 7 | `tools.py` ESSENTIAL | shell_exec, file_write, file_read, file_list, search_docs, get_environment_info, send_message |
| Convenience tools | 5 | `tools.py` CONVENIENCE | plot_chart, compute_statistics, fetch_market_data, construct_signal, compare_backtest_results |
| Trial tools | 4 | `tools.py` CORE_TOOLS | run_lean_backtest, submit_trial, select_submission, get_trial_status |
| Distractor tools | 10 | `distractor_tools.py` | compute_var, fit_garch_model, optimize_portfolio, etc. |

### 6.2 Tool Assembly Logic

Each task receives 15 tool slots (`_TOTAL_TOOL_SLOTS = 15`):
- Core tools (specified by the task JSON's `core_mcp_tools`)
- Convenience tools (specified by the task JSON's `convenient_tool_names`)
- Distractor tools (randomly sampled from the global pool to fill remaining slots)

### 6.3 Timeout Tiers

| Tool Category | Timeout |
|--------------|---------|
| Default | 60s |
| Long timeout (plot_chart, compute_statistics) | 120s |
| Extra-long timeout (shell_exec, run_backtest, run_lean_backtest, submit_trial) | 600s |

---

## 7. Agent Adapters

### 7.1 Dual-Mode Architecture

The Anthropic and OpenAI adapters each support two runtime modes:

| Mode | Transport | Agent Loop | Observability |
|------|-----------|------------|---------------|
| **SDK mode** | Vendor Agent SDK manages full lifecycle | Black-box internal loop | Final output + MCPProxy tool logs only |
| **Direct API mode** | Vendor SDK built-in runner (Anthropic) / Manual while loop (OpenAI) | Per-iteration tool loop with full intermediate visibility | Per-turn token usage + MCPProxy tool logs |

In both modes the LLM autonomously decides all tool calls (what to call, how many times, when to stop). Anthropic Direct API mode delegates the tool_use → tool_result cycling to the SDK's `BetaToolRunner`; OpenAI Direct API mode implements a manual `while` loop around `chat.completions.create` with explicit tool_call → tool result cycling, providing full per-iteration control over history management and token tracking.

### 7.2 Configuration Switches

All switches live in `config/llm_config.py`.

**Anthropic adapter** — two independent switches:

| Switch | Controls | Values |
|--------|----------|--------|
| `ANTHROPIC_USE_SDK` | Transport mode | `False` = Direct API (BetaToolRunner); `True` = Claude Agent SDK (black-box) |
| `AGENT_USE_OAUTH` | Auth method (Direct API only) | `True` = OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`); `False` = API key (`ANTHROPIC_API_KEY`) |

Effective combinations:

| ANTHROPIC_USE_SDK | AGENT_USE_OAUTH | Result |
|---|---|---|
| False | True | OAuth + BetaToolRunner (**default**) |
| False | False | API key + BetaToolRunner |
| True | _(ignored)_ | API key + SDK black-box |

> SDK mode does not support OAuth — ClaudeSDKClient's stream-json protocol cannot read OAuth credentials.

**OpenAI adapter** — one switch:

| Switch | Controls | Values |
|--------|----------|--------|
| `OPENAI_USE_DIRECT_API` | Transport mode | `True` = Chat Completions API (while loop, **default**); `False` = Agents SDK Runner (black-box) |

OpenAI uses `OPENAI_API_KEY` (native) or `OPENROUTER_API_KEY` (via `base_url`); no additional auth switch needed.

### 7.3 Adapter Overview

| Adapter | SDK Mode | Direct API Mode | Default Model |
|---------|---------|-----------------|---------------|
| Anthropic | ClaudeSDKClient + in-process MCP server | Anthropic SDK `BetaToolRunner` (`client.beta.messages.tool_runner`) | `claude-haiku-4-5-20251001` |
| OpenAI | Runner.run_sync + FunctionTool | openai SDK → `chat.completions.create` | `gpt-5.2` |
| Google | Google ADK | — | `gemini-2.5-flash` |
| Generic | — | OpenRouter Chat Completions (single-round tool call) | `openai/gpt-5.2` |

### 7.4 Agent Loop Termination

The agent loop terminates when the LLM itself decides the task is complete:

- **Anthropic**: Manual `for message in runner:` iteration replaces `runner.until_done()`, enabling per-iteration token capture while preserving the same termination semantics; `max_iterations` parameter serves as safety cap.
- **OpenAI**: `finish_reason == "stop"` (no `tool_calls` present)

`max_agent_turns` / `max_iterations` serves only as a safety cap to prevent infinite tool-calling loops. Under normal operation the LLM emits `end_turn` / `stop` well before reaching this limit.

### 7.5 Tool Schema Conversion

Each adapter converts the benchmark's unified tool schema (returned by MCPProxy) into the vendor's API format:

| Adapter | Converter | Parameter Type Fidelity |
|---------|-----------|------------------------|
| Anthropic Direct API | `DynamicTool(BetaBuiltinFunctionTool)` + `_build_runner_tools()` | Full (string/integer/array/object) |
| Anthropic SDK | `_build_sdk_tools()` → `SdkMcpTool` | Flattened to string |
| OpenAI Direct API / SDK | `_format_tools_openai()` / `_build_agent_tools()` | Full |

### 7.6 Token Tracking and Pricing

All adapters accumulate `TokenRecord` instances (model, input_tokens, output_tokens, cost_usd) via `BaseAgentAdapter._token_records`.

- **Direct API mode**: Anthropic records usage per-iteration via `_record_message_usage()` inside the `for message in runner:` loop (one `TokenRecord` per intermediate API call, capturing input + cache_read + cache_creation tokens); OpenAI records per-turn from `response.usage`.
- **SDK mode**: Extracted from `raw_responses` (OpenAI) or `AssistantMessage.usage` (Anthropic). Falls back to character-count estimation (~3.5 chars/token) if the SDK does not expose usage.

`config/pricing.py` maintains hardcoded per-token prices (sourced from OpenRouter API) and supports bidirectional lookup between OpenRouter format (`anthropic/claude-sonnet-4.6`) and native SDK format (`claude-sonnet-4-6`) via a `_NATIVE_TO_OR` alias table.

### 7.7 Cross-Turn Context Management

Both Anthropic and OpenAI Direct API adapters maintain an `_input_history` instance variable that preserves the full message history — including `tool_use` and `tool_result` blocks — across `generate_response()` calls within the same task.

| Adapter | History Source | Mechanism |
|---------|---------------|-----------|
| Anthropic | `runner._params["messages"]` | Extracted after manual iteration completes; `compaction_control` may have already summarized mid-conversation |
| OpenAI SDK | `result.to_input_list()` | Extracted after `Runner.run_sync()` completes |
| OpenAI Direct API | `api_messages` (in-memory list) | Accumulated in the while loop; `_compact_history()` may replace with summary when threshold exceeded |

Supporting methods on both adapters:

- `set_task_context(ctx)`: Injects per-task dynamic context (scenario, persona instructions) and resets `_input_history` for the new task.
- `_get_full_system_prompt()`: Concatenates the base system prompt with the current `_task_context`.
- `reset()`: Clears both `_input_history` and `_task_context`.

The orchestrator calls `set_task_context()` before each task (via `hasattr` check) and restores the original system prompt in the `finally` block.

OAuth in Anthropic Direct API mode uses `Anthropic(auth_token=token)` with `client.api_key = None` to suppress the `X-Api-Key` header, ensuring only the `Authorization: Bearer` header is sent.

### 7.8 History Compaction

Long tutoring conversations suffer from O(n²) cumulative token consumption because the full message history is sent with every API call. Both adapters now implement history compaction to cap context growth:

| Adapter | Mechanism | Threshold | Implementation |
|---------|-----------|-----------|----------------|
| Anthropic | `compaction_control={"enabled": True}` (SDK-native) | 100K tokens (SDK default) | `BetaToolRunner` internally calls `_check_and_compact()` between iterations; replaces history with an LLM-generated summary |
| OpenAI Direct API | `_compact_history()` (custom) | 100K tokens (`COMPACTION_TOKEN_THRESHOLD`) | After each while-loop iteration, if `last_prompt_tokens > threshold`, sends full history + summary prompt to `chat.completions.create`, replaces `api_messages` with `[system, assistant_summary]` |

**Design notes:**

- Anthropic's server-side `context_management` (edit types: `clear_tool_uses_20250919`, `clear_thinking_20251015`, `compact_20260112`) requires a beta header not available via OAuth; only client-side `compaction_control` is used.
- OpenAI's `_compact_history()` mirrors the Anthropic pattern: the summary prompt preserves task overview, current state, important discoveries, and next steps.
- The compaction API call's token usage is tracked in `_token_records` for accurate cost reporting.
- OpenAI SDK mode (`Runner.run_sync`) manages its own context internally and is not affected.

### 7.9 Thinking / Reasoning Mode

**Anthropic Extended Thinking**

- Switches: `ANTHROPIC_ENABLE_THINKING` (default `True`), `ANTHROPIC_THINKING_BUDGET = 4096`
- Direct API mode: Injects `thinking={"type": "enabled", "budget_tokens": N}` into `BetaToolRunner`; extracts `BetaThinkingBlock` per iteration into `_thinking_trace`; strips thinking blocks from `_input_history` after extraction to prevent O(n²) input token growth
- SDK mode: Passes the same config via `ClaudeAgentOptions(thinking=...)`; SDK internally translates to `--max-thinking-tokens` CLI argument
- `max_tokens` raised to 16384 (constraint: `budget_tokens < max_tokens`)
- Thinking trace is captured by `get_thinking_trace()` in `pre_teardown_hook`, embedded in trace.md under the corresponding assistant turn as collapsible `<details>` blocks
- Lifecycle note: `set_task_context()` does NOT clear `_thinking_trace` (because the orchestrator's `finally` block runs before `pre_teardown_hook`); only `reset()` clears it

**OpenAI Reasoning Effort**

- Switches: `OPENAI_ENABLE_REASONING` (default `False`), `OPENAI_REASONING_EFFORT = "medium"`
- Supported levels: `"none"` / `"low"` / `"medium"` / `"high"`
- Direct API mode: Injects `reasoning_effort` parameter into `chat.completions.create()`
- SDK mode: Passes `ModelSettings(reasoning=Reasoning(effort=...))` to the Agents SDK
- API limitation: OpenAI does not expose reasoning text, only returns `reasoning_tokens` count; cannot save reasoning traces like Anthropic

**Compatibility Matrix**

| Provider | Transport | Auth | Thinking | Verification |
|----------|-----------|------|----------|--------------|
| Anthropic | Direct API | OAuth | ✅ | End-to-end tested |
| Anthropic | Direct API | API Key | ✅ | Same code path |
| Anthropic | SDK | API Key | ✅ | Parameter accepted (insufficient credit for actual reasoning) |
| Anthropic | SDK | OAuth | ❌ | SDK does not support OAuth |
| OpenAI | Direct API | OpenRouter | ✅ | End-to-end tested |
| OpenAI | Direct API | API Key | ✅ | Same code path |
| OpenAI | SDK | OpenRouter | ✅ | End-to-end tested |
| OpenAI | SDK | API Key | ✅ | Same code path |

**Files Involved**

- `bench/config/llm_config.py` — `ANTHROPIC_ENABLE_THINKING`, `ANTHROPIC_THINKING_BUDGET`, `OPENAI_ENABLE_REASONING`, `OPENAI_REASONING_EFFORT`
- `bench/orchestrator/agent_adapters/anthropic_adapter.py` — SDK mode thinking config injection; Direct API mode thinking extraction + history stripping + trace capture
- `bench/orchestrator/agent_adapters/openai_adapter.py` — Direct API `reasoning_effort` injection; SDK mode `Reasoning(effort=...)` injection
- `bench/orchestrator/agent_adapters/base_adapter.py` — `get_thinking_trace()` interface
- `bench/evaluation/trace_report.py` — `_format_thinking_block()`, `thinking_trace` parameter, per-turn grouped rendering
- `bench/orchestrator/runners/job_runner.py` — Captures thinking trace in hook, passes to trace report

**Content Blocks Capture (Web UI Support)**

- `anthropic_adapter.py` adds `_turn_content_blocks: dict[int, list[dict]]` to store structured content blocks per turn
- `_capture_content_blocks()` is called before thinking stripping, extracting the full ordered block sequence from `_input_history`: `thinking → tool_use → tool_result → ... → text`
  - First pass: collects `tool_result` blocks indexed by `tool_use_id`
  - Second pass: iterates assistant messages, pairs each `tool_use` with its corresponding `tool_result`
  - `tool_result.content` truncated to 800 chars (full data available in proxy tool_logs)
- `get_content_blocks()` public interface returns `{turn_index: [blocks]}` for Web UI inline rendering of the agent's reasoning-tool-response flow
- `reset()` clears `_turn_content_blocks`

**Known Limitations**

- OAuth only allows `claude-haiku-4-5-20251001`; sonnet-4.6 and opus-4.6 return 400
- OpenAI reasoning text is not accessible, only token consumption statistics are available
- Anthropic SDK mode thinking was not end-to-end content-verified due to insufficient API key credit (parameter acceptance confirmed)

---

## 8. Evaluation System

### 8.1 Three-Dimensional Scoring

| Dimension | Code | Description |
|-----------|------|-------------|
| Result Quality | QR | 30% programmatic + 30% code_eval + 40% LLM Judge for code tasks; 40% programmatic + 60% LLM Judge for non-code tasks |
| Process Quality | QP | 7 weighted dimensions (see below) |
| Teaching Quality | Tutor 7D | 7 conversational teaching dimensions |

### 8.2 QP Dimension Weights

```
tool_usage:              0.20
process_reasonableness:  0.20
step_efficiency:         0.15
code_process:            0.15
process_alignment:       0.10
role_adherence:          0.10
topic_adherence:         0.10
```

### 8.3 Shared Evaluation Modules

`evaluation/test_scripts/common/` provides 6 shared checkers:

- `implementation_check.py` — I-series implementation verification
- `debug_check.py` — X-series debug verification
- `backtest_engine_check.py` — B-series backtest engine verification
- `data_source_check.py` — Data source verification (all series)
- `safety_pattern_check.py` — Safety pattern checking
- `strategy_research_check.py` — Strategy research verification

---

## 9. Network Preflight

`run_benchmark.py` performs TCP connection tests before startup to verify all required API endpoints are reachable:

- Agent API endpoints (Anthropic / OpenAI / OpenRouter)
- Eval model endpoints (used by DeepEval evaluation models)

Can be skipped via the environment variable `QTB_SKIP_NETWORK_PREFLIGHT=1`.

---

## 10. Orchestrator Execution Pipeline

`orchestrator.run_single_task()` executes in 5 phases:

| Phase | Name | Core Operations |
|-------|------|-----------------|
| Phase 1 | Environment Setup | Data download → directory staging → container creation → executor startup → MCP proxy configuration |
| Phase 2 | Agent Conversation | DeepEval ConversationalSimulator drives multi-turn dialogue |
| Phase 3 | Evaluation | eval script → code_eval → result_judge → process_metrics → tutor_7d → overall score |
| Phase 3.25 | Trial Finalization | Auto-select best trial if agent did not manually select |
| Phase 5 | Teardown | Container destruction + staged directory cleanup |

---

## 11. Conversation Simulation & Termination

### 11.1 Architecture

`orchestrator/simulation.py` wraps DeepEval's `ConversationSimulator` to drive multi-turn tutoring dialogues. The simulator plays the student role; the agent under test plays the tutor.

| Component | Function | Responsibility |
|-----------|----------|----------------|
| `build_conversational_golden()` | Seed object | Constructs `ConversationalGolden` (scenario + expected_outcome + user_description) |
| `create_model_callback()` | Agent wrapper | Wraps the agent adapter as `Callable[[str], str]` with timeout and repeat detection |
| `run_conversation_simulation()` | Main loop | Calls `simulator.simulate()` with retry logic (up to 2 retries on 0-turn results) |

### 11.2 Stop Judge Configuration

DeepEval's `ConversationSimulator` invokes an LLM judge after each exchange to check the `expected_outcome` field and decide whether to terminate. `simulation.py` constructs the stop outcome differently by task category:

```python
if task.ground_truth.termination_criteria:
    if task.category.value in ("implementation", "end_to_end", "debug"):
        stop_outcome = (
            f"{expected_outcome}\n\n"
            f"Observable completion criteria:\n"
            f"{termination_criteria}"
        )
    else:
        stop_outcome = termination_criteria
else:
    stop_outcome = expected_outcome
```

Design rationale:
- **`expected_outcome`** describes the full task goal (e.g., 6 capability points), giving the judge a sense of how deep the conversation should go.
- **`termination_criteria`** describes deliverables observable from conversation text ("algorithm written, backtest executed, metrics discussed"), giving the judge a concrete "done" signal.
- I/E/X coding tasks concatenate both: outcome-only causes no termination (too vague); criteria-only causes premature termination (judge fires after 1–2 exchanges).
- A-series tasks use `termination_criteria` alone (positive conditions the LLM checker can verify).
- Tasks without `termination_criteria` fall back to `expected_outcome` only.

### 11.3 max_turns Semantics

The task JSON's `max_turns` field is passed to DeepEval as `max_user_simulations` — the maximum number of **student messages**. Total conversation turns = `max_turns × 2` (each student message elicits one tutor response). For example, `max_turns: 10` produces up to 20 conversation turns (10 exchanges).

The separate `agent_max_steps` field maps to `max_agent_turns` / `max_iterations` in the adapter (Section 7.4) and caps tool-calling iterations **within a single exchange**, not the conversation length.

### 11.4 Scenario Prompt (Student Behavior)

`build_scenario()` in `prompt_config.py` constructs the student simulator's behavioral instructions. For non-adversarial tasks:

| Directive | Effect |
|-----------|--------|
| **PACING** | Space learning goals across the conversation; do not ask all at once |
| **COVERAGE TRACKING** | Track which goals are covered; transition after 3 consecutive turns on the same goal; prioritize breadth past the halfway point |
| **ACTION EXPECTATION** | Goals involving outputs require the tutor to save results to a file, not just print or plot |
| **DEAD-END AVOIDANCE** | Spend at most 1 follow-up turn on setup/config tangents before redirecting to learning goals |
| **TURN BUDGET** | Informs the student of the total turn budget (`max_turns`); instructs efficiency |
| **IMPLEMENTATION TRACKING** (Prompt D, I/E/X only) | Do not accept verbal explanations as completion; push for concrete code and execution |
| **ABSTRACT PUSH** (Prompt D, I/E/X only) | If the tutor stays abstract without producing files or execution, redirect to implementation |

Adversarial tasks receive a different set: CONVERSATION CLOSURE (natural wrap-up after tutor addresses the concern) with no pacing, coverage tracking, or code-push directives.

`build_user_description()` injects the student persona (knowledge level, emotional profile, behavioral rules) plus hardcoded INTERACTION RULES (answer tutor's questions first, stay in character, persona persistence).

### 11.5 Safety Mechanisms

| Mechanism | Trigger | Behavior |
|-----------|---------|----------|
| Wall-clock timeout | `timeout_minutes` exceeded | 1st breach: warning injected into conversation; 2nd breach: `_SessionTimeoutError` raised, force-stops simulation |
| Repeat detection | 2 consecutive identical agent responses | Force-stop (`_MAX_REPEATS = 2`) |
| Simulation retry | `simulate()` returns 0 turns | Rebuild simulator instance and retry (up to `_MAX_SIMULATION_RETRIES = 2`) |
| Stop judge | Each exchange | DeepEval LLM judge checks `expected_outcome` to decide if conversation should end early |

---

## 12. I-Series: Reference Data & Behavioral Evaluation

### 12.1 Reference Data Generation

Two independent scripts produce ground-truth data for I-series evaluation:

**`generate_lean_reference.py`** — Runs reference C# algorithms in Docker containers:
- Reads reference `.cs` files from `reference/Implementation/algorithms/`
- Compiles and runs each algorithm via LEAN engine in Docker
- Parses LEAN output (order events → round-trip trades, performance metrics)
- Multi-run support: I06 (21 weight configs), I08 (2 configs), I09 (3 configs), I10 (~180 parameter grid)
- Output: `{task_id}_reference_trades{_run_id}.json`

**`generate_reference_signals.py`** — Pure Python/pandas signal computation (LEAN-independent):
- Computes deterministic reference signals per task (SMA, RSI, EMA crossover, z-score, composite)
- Output: `{task_id}_reference_signals.json`, `{task_id}_reference_summary.json`
- I05 also produces `I05_candidate_pairs.json` (60-day rolling correlation > 0.7)

Reference file layout:

```
reference/Implementation/
├── algorithms/                          # 10 reference C# algorithms (I01–I10)
│   ├── I01_implement_sma.cs
│   └── ...
└── result/                              # Ground-truth data
    ├── I0X_reference_trades.json        # Trade logs
    ├── I0X_reference_signals.json       # Daily signal directions per symbol
    ├── I0X_reference_summary.json       # Performance metrics
    ├── I05_candidate_pairs.json         # Pair trading candidates
    ├── I06_reference_sweep_results.json # 21-config weight sweep
    ├── I08_reference_comparison.json    # Equal vs insight weighting
    ├── I09_reference_comparison.json    # 3 risk-config comparison
    └── I10_reference_grid_results.json  # ~180 parameter grid
```

### 12.2 Behavioral Evaluation Framework

`evaluation/test_scripts/common/implementation_check.py` provides a 4-layer behavioral scoring system shared by all I-series eval scripts:

| Layer | Weight | What It Measures |
|-------|--------|------------------|
| Signal Agreement | 0.40 | Reference signal direction vs sign(agent position), per date/symbol |
| Position Overlap | 0.30 | Direction match (0.70) + size similarity (0.30) |
| Performance | 0.20 | Proximity of Sharpe, return%, drawdown%, trade count to reference |
| Trade Similarity | 0.10 | Relaxed 2-bar tolerance trade matching (entry/exit timing, direction, PnL correlation) |

When a layer's data is unavailable, its weight redistributes proportionally to the remaining layers.

Key helpers:
- `reconstruct_positions()` — Builds daily position series from orders/trades
- `compute_trial_efficiency()` — `(max_trials − used) / (max_trials − 1)`, reads `.backtest_runs.jsonl`
- `check_csharp_patterns()` — Regex scan of `.cs` files for required API usage
- `check_framework_architecture()` — Validates I07+ Algorithm Framework wiring (SetAlpha, SetPortfolioConstruction, SetExecution)

### 12.3 Per-Task Evaluation Specialization

Each I-series eval script (`I01_implement_sma.py` through `I10_parameter_optimization.py`) extends the shared framework with task-specific checks:

| Task | Additional Checks | Notable Weights |
|------|-------------------|-----------------|
| I01 | Code patterns (AddCryptoFuture, SMA, SetWarmUp, IsWarmingUp) | behavioral: 0.55, patterns: 0.10 |
| I02 | Universe coverage (≥80 symbols), universe summary produced | behavioral: 0.50, universe: 0.15 |
| I07 | Framework architecture (SetAlpha + PCM + Execution), AlphaModel class, Insight emission | architecture: 0.15, alpha_model: 0.10 |
| I10 | Grid completeness (≥150 combos), results structure, top-5 identification, Bayesian bonus | grid: 0.15, behavioral: 0.20 |

### 12.4 I-Series Task Configuration Summary

| Task | Difficulty | max_turns | Timeout | Trials | termination_criteria | Special Data |
|------|------------|-----------|---------|--------|---------------------|--------------|
| I01 | Easy | 10 | 15m | 5 | ✓ | — |
| I02 | Medium | 12 | 20m | 5 | ✓ | — |
| I03 | Medium | 30 | 20m | 5 | ✓ | — |
| I04 | Hard | 35 | 25m | 5 | ✓ | — |
| I05 | Hard | 35 | 25m | 5 | ✓ | I05_candidate_pairs.json |
| I06 | Hard | 40 | 45m | 5 | ✓ | — |
| I07 | Medium | 30 | 20m | 5 | ✓ | — |
| I08 | Hard | 36 | 35m | 5 | ✓ | — |
| I09 | Hard | 36 | 30m | 5 | ✓ | — |
| I10 | Hard | 40 | 60m | 3 | ✓ | — |

All I-series tasks share: `sandbox_image: quant-tutor-env:v2.2-lean`, `network_enabled: false`, `requires_code: true`, `data_files: ["universe.json"]` (I05 adds `I05_candidate_pairs.json`).

Core tools (9): shell_exec, file_write, file_read, file_list, get_environment_info, run_lean_backtest, submit_trial, select_submission, get_trial_status.

Convenient tools vary per task:

| Tasks | Convenient Tools |
|-------|-----------------|
| I01–I02 | search_docs, plot_chart |
| I03–I04 | search_docs, compute_indicator, plot_chart |
| I05 | search_docs, compute_statistics, plot_chart |
| I06 | search_docs, compute_indicator, analyze_backtest_results, plot_chart |
| I07–I10 | search_docs, compute_indicator, analyze_backtest_results |

### 12.5 Data Preparation Pipeline

`scripts/prepare_i_series_data.py` provides end-to-end data preparation:

1. Download raw Binance klines (via `download_binance_full_universe.py`)
2. Convert to LEAN format (via `convert_binance_to_lean.py`)
3. Generate `lean_universe.json` (flat symbol list for C# algorithms)
4. Upload to HuggingFace (`--upload`)
5. Verify data_manager roundtrip (`--verify`)

Unified benchmark window: `BENCH_START = "2022-01-01"`, `BENCH_END = "2025-12-31"` (`config/benchmark_dates.py`).

### 12.6 I-Series Modification History

**Resolved issues:**

| Issue | Root Cause | Fix | Files |
|-------|-----------|-----|-------|
| I01/I02 conversations drifted off-topic (24–30 turns) | max_turns too high | Reduced to 10/12 | I01, I02 task JSONs |
| Adding termination_criteria caused premature stop (4–6 turns) | `or` logic replaced expected_outcome entirely | Category-based concatenation (I/E/X concatenate; others replace) | simulation.py |
| termination_criteria phrased subjectively | "tutor has guided the student…" | Rewritten as objective deliverables | I01, I02 task JSONs |
| I02 expected_outcome contained unverifiable condition | "per-symbol trade logs should match ground-truth" not observable in conversation | Removed; eval script handles independently | I02 task JSON |
| Changes could affect A-series | simulation.py modification applied globally | Added `task.category.value in (...)` guard | simulation.py |

**Known issue — simple tasks exhaust max_turns:**

I01 core task completes at exchange 3 (turn 6) but runs to exchange 10 (turn 20). I02 completes at exchange 6 (turn 12) but runs to exchange 12 (turn 24). Root cause: scenario prompt lacks a TASK COMPLETION directive — after all learning goals are covered, the student continues inventing visualization requests and diagnostic refinements. Pending fix: add a completion directive and reduce max_turns for simple tasks.

---

## Appendix A. Internal Consistency Issues

The following inconsistencies were identified during review and require correction.

### A.1 Section 6.1: Tool category table does not match source code

**Issue:** Section 6.1 claims three source-code constants (`tools.py ESSENTIAL`, `tools.py CONVENIENCE`, `tools.py CORE_TOOLS`) with fixed tool counts (7 + 5 + 4 = 16). In reality:

- **No `ESSENTIAL_TOOLS` or `CONVENIENCE_TOOLS` constants exist.** All tools are registered in a single `CORE_TOOLS` dict in `tools.py` (19 entries total).
- **`send_message` is listed as a core tool but does not exist** anywhere in the codebase. The actual count of "basic" tools (shell_exec, file_write, file_read, file_list, search_docs, get_environment_info) is 6, not 7.
- **4 tools are missing from Section 6.1:** compute_indicator, run_backtest, analyze_backtest_results, evaluate_signal. These exist in `CORE_TOOLS` but appear in neither the "Core" nor "Convenience" rows.
- The per-task partitioning into core/convenient/distractor is determined entirely by each task JSON's `core_mcp_tools` and `ground_truth.convenient_tools` fields, not by source-code constants. Section 6.1 conflates source-code organization (which no longer has separate constants) with runtime assembly (which is task-driven).

**Correction needed:** Rewrite Section 6.1 to show the single `CORE_TOOLS` dict (19 tools), explain that per-task partitioning is driven by task JSON fields, and remove the nonexistent `send_message`.

### A.2 Section 2.5 / Section 4.5: Exit code 5 and budget enforcement undocumented

**Issue:** `run_backtest.sh` uses exit code 5 for "budget exhausted" (line 95 of the script) and tracks all runs via `.backtest_runs.jsonl` with an EXIT trap. Neither mechanism is mentioned in Section 2.5 (run_backtest.sh workflow) or Section 4.5 (status detection). Section 4.5 lists exit codes 0, 2, 3, 124 but omits 5.

**Correction needed:** Add budget enforcement as a pre-step in Section 2.5 (check `.backtest_runs.jsonl` count against `QTB_MAX_BACKTEST_TRIALS`; exit 5 if exceeded). Add exit code 5 → `budget_exhausted` to Section 4.5.

### A.3 Section 11.3 vs TURN BUDGET prompt: "turns" semantics ambiguity

**Issue:** Section 11.3 defines `max_turns` as the number of student messages (total conversation turns = `max_turns × 2`). However, the TURN BUDGET prompt in `prompt_config.py` tells the student: _"This session has approximately {max_turns} turns total"_ — presenting `max_turns` as if it were the total turn count. This means the student simulator believes it has fewer turns than it actually does (e.g., told "10 turns" but actually gets 10 exchanges = 20 turns).

**Impact:** Low — the student over-estimates urgency, which should help with efficiency. But the document should note this discrepancy for clarity.

### A.4 Section 10: Missing Phase 4

**Issue:** The orchestrator pipeline phases jump from 3.25 to 5 with no Phase 4 listed. This is technically correct (the `pre_teardown_hook` runs between Phase 3 and teardown, and Phase 4 was never formally assigned), but the numbering gap is confusing.

**Note:** No correction needed — acknowledge the gap is intentional.
