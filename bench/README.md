# QuantTutorBench

A two-axis benchmark for evaluating quantitative finance tutoring agents. Measures both **Quant Agent expertise** (70%) and **Tutoring quality** (30%) in a sandboxed, tool-augmented environment.

Based on the design document: `design_2026_2_12_updated.md`

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       Orchestration Host                        │
│                                                                 │
│  run_benchmark.py  ──►  BenchmarkOrchestrator                   │
│       │                       │                                 │
│       │                  ┌────┴────────────────────────┐        │
│       │                  │  Per-Task Lifecycle (5 phases)│        │
│       │                  │  1. RESET   → Docker + Proxy │        │
│       │                  │  2. INTERACT → Simulator      │        │
│       │                  │  3. CAPTURE  → Trace logs     │        │
│       │                  │  4. EVALUATE → 3-axis scoring │        │
│       │                  │  5. TEARDOWN → Cleanup        │        │
│       │                  └───────────────────────────────┘        │
│       │                                                         │
│  ┌────┴─────────┐   ┌──────────────────┐   ┌────────────────┐  │
│  │ Agent Adapters│   │ Docker Container  │   │  DeepEval      │  │
│  │  - generic    │   │  /workspace (RW)  │   │  - Simulator   │  │
│  │  - openai     │   │  /data     (RO)   │   │  - GEval       │  │
│  │  - anthropic  │   │  /docs     (RO)   │   │  - MCP Metrics │  │
│  │  - google     │   │  /student_code    │   │  - Conv.GEval  │  │
│  └──────────────┘   │  --network none    │   └────────────────┘  │
│                     │  CPU:2 / RAM:4GB   │                       │
│                     └──────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
bench/
├── run_benchmark.py              # CLI entry point
├── requirements.txt              # Python dependencies
├── design_2026_2_12_updated.md   # Design document (NeurIPS 2026)
│
├── config/                       # Central configuration
│   ├── llm_config.py             #   Model names per SDK (native + OpenRouter)
│   ├── prompt_config.py          #   System prompts + dynamic prompt builders
│   └── conditions.py             #   2x2 test condition matrix
│
├── orchestrator/                 # Core orchestration layer
│   ├── orchestrator.py           #   BenchmarkOrchestrator (5-phase lifecycle)
│   ├── schemas.py                #   Pydantic models (QuantTutorTask, StudentPersona, etc.)
│   ├── container_manager.py      #   Docker container lifecycle + local fallback
│   ├── simulator_config.py       #   DeepEval ConversationSimulator integration
│   ├── trace_assembler.py        #   Combines proxy logs + dialogue → TestCase
│   └── agent_adapters/           #   SDK-specific agent wrappers
│       ├── base_adapter.py       #     Abstract base class
│       ├── generic_adapter.py    #     OpenAI-compatible API (OpenRouter)
│       ├── openai_adapter.py     #     OpenAI Agents SDK (native)
│       ├── anthropic_adapter.py  #     Claude Agent SDK (native)
│       ├── google_adapter.py     #     Google ADK (native)
│       └── prompts.py            #     Re-exports from config/prompt_config.py
│
├── mcp_servers/                  # MCP tool infrastructure
│   ├── registry.py               #   Tool registration + proxy creation per task
│   ├── core/                     #   14 core tool implementations
│   │   ├── tools.py              #     All tool functions (lazy env var reads)
│   │   └── tool_wrappers.py      #     Docker exec wrappers for code execution tools
│   ├── proxy/                    #   Transparent logging proxy
│   │   ├── mcp_proxy.py          #     MCPProxy class (log + forward)
│   │   └── log_converter.py      #     Convert logs to DeepEval format
│   └── distractors/              #   15 distractor tool schemas (no real impl)
│       └── distractor_tools.py
│
├── evaluation/                   # Evaluation pipeline
│   ├── scoring.py                #   Score aggregation + KPI computation
│   ├── rubrics/                  #   7D tutoring rubrics (persona-specific)
│   │   ├── rubric_beginner.json
│   │   ├── rubric_intermediate.json
│   │   └── rubric_advanced.json
│   ├── deepeval_metrics/         #   DeepEval metric configurations
│   │   ├── tutor_conv_geval.py   #     7D ConversationalGEval (3x shuffled judge)
│   │   ├── mcp_metrics.py        #     Tool precision/recall + capability checks
│   │   ├── process_metrics.py    #     DeepEval process-level metrics
│   │   └── quant_geval.py        #     Layer 1 GEval scoring
│   └── test_scripts/             #   Per-task eval scripts (quant result scoring)
│       ├── _implementation_check.py   #   Shared: LEAN trade-log matching + C# pattern checks
│       ├── D01_load_inspect_ohlcv.py
│       ├── S01_ma_crossover.py
│       ├── I02_trend_following.py
│       └── ...
│
├── tasks/                        # Task definitions (JSON)
│   ├── layer1/                   #   Single-turn knowledge items
│   │   ├── conceptual_qa/        #     Financial concept Q&A
│   │   ├── strategy_explanation/ #     Strategy explanation
│   │   ├── code_generation/      #     Python code generation
│   │   ├── code_debugging/       #     Code debugging
│   │   ├── data_interpretation/  #     Data interpretation (TAT-QA)
│   │   └── multi_step_reasoning/ #     Multi-step reasoning (FinQA/ConvFinQA)
│   └── layer2/                   #   Multi-turn tutoring scenarios
│       ├── data_analysis/        #     D01-D06: data exploration tasks
│       ├── strategy/             #     S01-S07: strategy design tasks
│       ├── implementation/       #     I01-I06: code implementation tasks
│       ├── backtest/             #     B01-B05: backtest & analysis tasks
│       ├── debug/                #     X01-X06: code debugging tasks
│       ├── end_to_end/           #     E01-E05: full workflow tasks
│       └── adversarial/          #     A01-A06: safety & boundary tasks
│
├── personas/                     # Student persona definitions
│   ├── beginner_no_finance.json
│   ├── intermediate_developer.json
│   └── advanced_quant.json
│
├── student_code/                 # Pre-planted buggy code for debug tasks
│   ├── ma_offbyone.py            #   rolling(19) → rolling(20) off-by-one
│   ├── returns_diff.py           #   diff() vs pct_change() confusion
│   ├── lookahead.py              #   Look-ahead bias (no signal shift)
│   ├── timezone_merge.py         #   Timezone mismatch in data merge
│   ├── position_bug.py           #   Position signal 1/0 instead of 1/0/-1
│   └── overfit_single.py         #   Over-parameterized strategy (12 params)
│
├── data/                         # Frozen market data
│   ├── frozen/                   #   Static CSV files (AAPL, SPY, etc.)
│   ├── universe.json             #   Binance futures symbol universe (3 tiers + funding)
│   └── reference/                #   Ground-truth trade logs for I-series eval
│
├── docs/reference/               # Agent's reference library
│   ├── moving_averages.md
│   ├── backtesting_101.md
│   ├── risk_metrics.md
│   ├── pandas_timeseries.md
│   ├── statistical_tests.md
│   └── lean_algorithm_guide.md   #   LEAN C# algorithm guide (12 sections)
│
├── docker/                       # Sandbox environment
│   ├── Dockerfile                #   Python 3.11 + pandas/numpy/scipy/etc.
│   ├── Dockerfile.lean           #   LEAN engine image (.NET 8.0 + QuantConnect)
│   ├── lean-config.json          #   LEAN pre-configured for Binance futures
│   └── run_backtest.sh           #   Wrapper: compile + run LEAN backtest
│
├── layer1/                       # Layer 1 runner infrastructure
│   ├── data_loader.py            #   Load synthesized single-turn items
│   └── runner.py                 #   Layer1Runner (batch eval with GEval)
│
├── scripts/                      # Utility scripts
│   ├── synthesize_tasks.py       #   Generate Layer 1 items from datasets
│   ├── download_frozen_data.py   #   Download static market data
│   ├── data_manager.py           #   HuggingFace dataset download + caching
│   ├── download_binance_full_universe.py  # Full-universe Binance kline downloader
│   └── convert_binance_to_lean.py #  Convert Binance CSVs → LEAN TradeBar format
│
├── reference/                    # Reference algorithms
│   ├── lean_algorithms/          #   C# reference implementations (I02-I06)
│   └── generate_lean_reference.py #  Generate ground-truth trade logs from ref algos
│
└── results/                      # Output directory (auto-created)
    └── traces/                   #   Per-task conversation traces
```

---

## Two-Layer Evaluation

### Layer 1: Core Capabilities (single-turn, ~40 items currently)

Tests foundational quant knowledge via single-turn Q&A. Items sourced from FiQA, TAT-QA, FinQA, ConvFinQA, StackExchange, Reddit, CFPB, FINRA, SEC.

| Category             | Source Datasets                        | Eval Method         |
|----------------------|----------------------------------------|---------------------|
| Conceptual Q&A       | FiQA, StackExchange, Reddit, CFPB, etc. | DeepEval GEval    |
| Strategy Explanation | Custom-synthesized                     | DeepEval GEval      |
| Code Generation      | Custom-synthesized                     | GEval + execution   |
| Code Debugging       | Custom-synthesized                     | GEval + execution   |
| Data Interpretation  | TAT-QA                                | DeepEval GEval      |
| Multi-step Reasoning | FinQA, ConvFinQA                       | DeepEval GEval      |

### Layer 2: Tutoring Skills (multi-turn, 12 tasks × 3 personas = 36 instances)

Tests interactive tutoring via multi-turn dialogue with tool use in a sandboxed environment.

| Category       | Tasks  | Key Challenge                              |
|----------------|--------|--------------------------------------------|
| Data Analysis  | D01    | Load/inspect OHLCV, explain data columns   |
| Strategy       | S01    | Design MA crossover strategy               |
| Implementation | I01    | Implement SMA trend filter (LEAN C#, single symbol) |
| Implementation | I02    | Trend-following strategy (LEAN C#, ~100 symbols) |
| Implementation | I03    | Mean-reversion strategy (LEAN C#, RSI + stop-loss) |
| Implementation | I04    | Multi-timeframe strategy (LEAN C#, consolidators) |
| Implementation | I05    | Cross-asset pairs trading (LEAN C#, correlation) |
| Implementation | I06    | Multi-signal parameter sweep (LEAN C#, 21 combos) |
| Backtest       | B01    | Interpret backtest metrics                 |
| Debug          | X01    | Fix off-by-one bug in MA calculation       |
| End-to-End     | E01    | Build complete MA system from scratch      |
| Adversarial    | A01    | Refuse investment advice appropriately     |

Each task is evaluated with 3 student personas: `beginner_no_finance`, `intermediate_developer`, `advanced_quant`.

---

## Scoring Architecture

```
Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score

Quant Agent Score = 0.50 × Result Sub-score + 0.50 × Process Sub-score
  Result Sub-score  = λ × Layer1 + (1-λ) × Layer2   (λ = 0.40)
  Process Sub-score = Tool precision/recall + DeepEval process metrics

Tutor Score = mean of 7D rubric scores (each 0-1)
  D1: Level Detection       D5: Code Teaching
  D2: Language Adaptation   D6: Empathetic Response
  D3: Scaffolding           D7: Safety & Boundaries
  D4: Domain Accuracy
```

### Benchmark-Level KPIs

| KPI | Description |
|-----|-------------|
| OAS | Overall Agent Score (weighted average across all tasks) |
| QAI | Quant Agent Index (average quant scores) |
| TEI | Tutoring Effectiveness Index (average tutor rubric scores) |
| AS  | Adaptiveness Score (tutor score variance across personas) |
| TMS | Tool Mastery Score (average tool precision × recall) |

---

## 2x2 Test Condition Matrix

```
              │  Tools Enabled      │  No Tools            │
──────────────┼─────────────────────┼──────────────────────│
Tutor prompt  │  agent              │  pure_llm            │
Baseline      │  baseline           │  pure_llm_baseline   │
```

- **agent**: Full agent with SDK + tools + tutor system prompt
- **baseline**: SDK + tools + "dump-the-answer" prompt (no teaching)
- **pure_llm**: No tools, tutor prompt only (pure LLM reasoning)
- **pure_llm_baseline**: No tools, no teaching (lowest effort baseline)

---

## Agent Adapters

| Adapter    | SDK                  | Model (default)       | Tool Calling |
|------------|----------------------|-----------------------|--------------|
| `generic`  | OpenAI-compatible API | `google/gemini-3-flash-preview` (via OpenRouter) | OpenAI function calling |
| `openai`   | OpenAI Agents SDK    | `gpt-4o`              | Native FunctionTool |
| `anthropic`| Claude Agent SDK     | `claude-sonnet-4-6`   | SdkMcpTool + ClaudeSDKClient |
| `google`   | Google ADK           | `gemini-2.5-flash`    | Native tools |

All adapters implement `BaseAgentAdapter.generate_response(messages, available_tools, tool_callback)`.

---

## Docker Sandbox Environment

Code execution tools (`shell_exec`, `run_backtest`, `plot_chart`) run inside an isolated Docker container.

### Build the image

```bash
cd bench
docker build -t quant-tutor-env:v2.2 docker/
```

### LEAN engine image (I02-I06)

```bash
docker build -t quant-tutor-env:v2.0-lean -f docker/Dockerfile.lean .
```

LEAN-based tasks (I02-I06) use a separate Docker image with .NET 8.0 and the QuantConnect LEAN engine. Market data is mounted read-only from a HuggingFace dataset cache via `/lean/Data`.

### Container specs

**Standard image (`quant-tutor-env:v2.2`)**:
- **Base**: Python 3.11-slim
- **Packages**: pandas, numpy, matplotlib, scipy, statsmodels, tabulate
- **User**: Non-root `sandbox` user
- **Network**: `--network none` (no internet)
- **Resources**: CPU 2 / RAM 4GB
- **Mounts**: `/workspace` (RW), `/data` (RO), `/docs` (RO), `/student_code` (RO)

**LEAN image (`quant-tutor-env:v2.0-lean`)**:
- **Base**: quant-tutor-env:v2.2 + .NET SDK 8.0 + LEAN engine
- **Mounts**: All standard mounts + `/lean/Data` (RO, Binance futures OHLCV)
- **Usage**: C# algorithm compilation + LEAN backtesting (I02-I06)

### Information access control

Each task specifies `data_files` and `docs_available` in its JSON. The orchestrator creates **staged directories** (symlinks to only the allowed files) and sets environment variables:

```
QTB_DATA_DIR    → staged data dir (only task-allowed CSVs)
QTB_DOCS_DIR    → staged docs dir (only task-allowed docs)
QTB_WORKSPACE_DIR → container workspace path
```

Tool functions use lazy environment variable reads (`_data_dir()` instead of module-level constants), so each task gets its own filtered file access.

---

## Quick Start

### Prerequisites

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables (in project root .env)
OPENROUTER_API_KEY=sk-or-...
# Optional: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY

# 3. Build Docker image (optional, for sandboxed execution)
docker build -t quant-tutor-env:v2.2 docker/
```

### CLI Commands

```bash
cd bench

# List all available tasks
python run_benchmark.py list-tasks

# Run end-to-end validation (no LLM calls for eval)
python run_benchmark.py test-e2e

# Run a single task (quick test)
python run_benchmark.py run-single \
  --task D01_load_inspect_ohlcv \
  --persona beginner_no_finance \
  --max-turns 5

# Run a single task with Docker sandbox
python run_benchmark.py run-single \
  --task D01_load_inspect_ohlcv \
  --persona beginner_no_finance \
  --max-turns 8 --docker

# Run full benchmark (Layer 1 + Layer 2)
python run_benchmark.py run --agent generic --condition agent

# Run Layer 2 only with a specific agent SDK
python run_benchmark.py run --agent openai --condition agent --layer 2

# Run Layer 1 only (single-turn knowledge eval)
python run_benchmark.py run-layer1 --max-items 10

# Run with custom eval/simulator models
python run_benchmark.py run-single \
  --task S01_ma_crossover \
  --persona intermediate_developer \
  --eval-model "anthropic/claude-sonnet-4.6" \
  --simulator-model "openai/gpt-4o"
```

---

## Implementation Status vs Design Document

### Fully Implemented

| Design Doc Section | Implementation | Status |
|--------------------|----------------|--------|
| Two-axis framework (Quant 70% + Tutor 30%) | `evaluation/scoring.py` | Done |
| 5-phase lifecycle (Reset/Interact/Capture/Evaluate/Teardown) | `orchestrator/orchestrator.py` | Done |
| DeepEval ConversationSimulator integration | `orchestrator/simulator_config.py` | Done |
| 7D persona-aware tutoring rubric (3x shuffled judge) | `evaluation/deepeval_metrics/tutor_conv_geval.py` + `evaluation/rubrics/` | Done |
| MCP Proxy Layer (transparent tool call logging) | `mcp_servers/proxy/mcp_proxy.py` | Done |
| 14 core MCP tools + 15 distractor tools | `mcp_servers/core/tools.py` + `mcp_servers/distractors/` | Done |
| Tool precision/recall + capability checks | `evaluation/deepeval_metrics/mcp_metrics.py` | Done |
| Docker sandbox (code execution isolation) | `docker/Dockerfile` + `orchestrator/container_manager.py` + `mcp_servers/core/tool_wrappers.py` | Done |
| Information access control (staged directories) | `orchestrator/orchestrator.py` (`_create_staged_dirs`) | Done |
| 3 student personas with behavioral rules | `personas/*.json` | Done |
| Per-task eval scripts (quant result scoring) | `evaluation/test_scripts/*.py` | Done |
| Pydantic task/persona schemas | `orchestrator/schemas.py` | Done |
| 6 pre-planted buggy code files for debug tasks | `student_code/*.py` | Done |
| 5 reference documents for agent library | `docs/reference/*.md` | Done |
| pass@k and pass^k metrics | `evaluation/scoring.py` | Done |
| Confidence intervals on aggregate metrics | `evaluation/scoring.py` | Done |
| Cost estimation per task | `orchestrator/orchestrator.py` | Done |

### Extended Beyond Design Document

| Extension | Description | Files |
|-----------|-------------|-------|
| **Multi-SDK adapter system** | 4 native SDK adapters (OpenAI, Anthropic, Google, Generic) with unified interface, beyond the design doc's single adapter pattern | `orchestrator/agent_adapters/` |
| **2x2 test condition matrix** | Systematic ablation: agent / baseline / pure_llm / pure_llm_baseline. Design doc mentioned baseline but not the full matrix | `config/conditions.py` |
| **Layer 1 + Layer 2 blending** | Combined scoring formula (Result_Sub = 0.40 × L1 + 0.60 × L2) with unified CLI. Design doc described layers separately | `evaluation/scoring.py`, `run_benchmark.py` |
| **Dynamic tutor prompt injection** | Per-task/persona context injected into agent system prompt at runtime, restored after each task | `config/prompt_config.py`, `orchestrator/orchestrator.py` |
| **DeepEval model routing** | `resolve_deepeval_model()` handles mixed API keys (OpenAI + OpenRouter), auto-routes non-OpenAI judge models through OpenRouter | `config/llm_config.py` |
| **Central model configuration** | All model names in one file with per-SDK native + OpenRouter mappings | `config/llm_config.py` |
| **Docker exec wrappers** | Factory pattern for code execution tools: `make_shell_exec`, `make_run_backtest`, `make_plot_chart` with host/container mode | `mcp_servers/core/tool_wrappers.py` |
| **Lazy environment variable reads** | Tools read env vars at call time (not import time), enabling per-task file access control without module reloading | `mcp_servers/core/tools.py` |
| **Layer 1 data pipeline** | Synthesized from 6 financial datasets (FiQA, TAT-QA, FinQA, ConvFinQA, StackExchange, Reddit, CFPB, FINRA, SEC) | `layer1/`, `scripts/synthesize_tasks.py` |
| **Trace assembler** | Combines MCP proxy logs + ConversationalTestCase into enriched test cases for DeepEval | `orchestrator/trace_assembler.py` |
| **Manual conversation fallback** | When DeepEval is unavailable, uses simple student simulation with persona-aware responses | `orchestrator/simulator_config.py` |
| **Comprehensive E2E test suite** | 8-step validation: schemas, personas, Layer 1 data, MCP tools, scoring, combined scoring, DeepEval, LLM connectivity | `run_benchmark.py` (`cmd_test_e2e`) |

### Not Yet Implemented (from Design Doc)

| Design Doc Feature | Section | Notes |
|--------------------|---------|-------|
| Full 41 base tasks (currently 12 Layer 2 tasks) | §5.3 | 12 tasks implemented (D01, S01, I01-I06, B01, X01, E01, A01); remaining 29 to be added |
| ~2000 Layer 1 items | §5.0 | ~40 items currently; pipeline ready for scale-up |
| docker-compose.yml | §7.1 | Dockerfile done; compose file not needed for current flow |
| Human annotation validation | §8 | ≥50 tasks with 3 human raters for rubric calibration |
| Private hold-out test set | §8 | Contamination prevention strategy |
| Reference agent release | §8 | Baseline quant tutor agent |

---

## Successfully Tested

### E2E Validation (`test-e2e`)

All 8 tests pass:
- Task schema validation (all Layer 1 + Layer 2 JSONs)
- Persona schema validation (3 personas)
- Layer 1 data loading
- MCP tool registry (14 core + 15 distractor tools)
- Scoring pipeline (task-level + combined Layer 1/2)
- DeepEval availability
- LLM connectivity via OpenRouter

### Single Task End-to-End (`run-single`)

Successfully completed with the following verified:
- DeepEval ConversationSimulator generates multi-turn dialogue
- All 21 judge evaluations (7 dimensions × 3 shuffled runs) complete without error
- Tool precision/recall and capability checks compute correctly
- Per-task eval script runs and produces quant result score
- Full scoring pipeline (Quant Agent Score + Tutor Score → Overall) works
- Results saved to JSON with full conversation traces

### Docker Sandbox

- Image builds successfully (`quant-tutor-env:v2.2`, ~834MB)
- All required packages verified inside container (pandas, numpy, scipy, statsmodels, matplotlib, tabulate)
- Sandbox user permissions validated (read/write workspace, read-only data/docs)
- Bind mount bidirectionality confirmed (host ↔ container)

### DeepEval Model Routing

- `resolve_deepeval_model()` correctly handles 4 cases:
  - OpenRouter base URL → model name as-is
  - Native OpenAI + OpenAI model → strip "openai/" prefix
  - Non-provider-prefixed model → pass through
  - Non-OpenAI model on native API → create GPTModel via OpenRouter

### Agent Adapter Tool Calling

- `GenericLLMAdapter` confirmed capable of tool calling via OpenAI function calling format
- Proxy tool format → adapter `_format_tools()` compatibility verified
- Tool callback chain: adapter → proxy.call_tool() → tool function → result

---

## Known Limitations

1. **GenericLLMAdapter**: Only supports 1 round of tool calls per `generate_response()` invocation. Multi-step tool chains require multiple conversation turns. This makes it a pseudo-agent baseline rather than a true agent.
2. **Max turns**: The `--max-turns` default is 5 for single task runs. Tests show multi-step tasks (S01, I01, X01) benefit significantly from `--max-turns 10` when using the OpenAI SDK adapter.
3. **Layer 1 scale**: Currently ~40 items. The pipeline supports scaling to ~2000 items as described in the design doc.
4. **Layer 2 tasks**: 12 of 41 planned base tasks are implemented. Each has full eval scripts, rubrics, and persona support. I02-I06 require the LEAN Docker image and populated reference trade logs.
5. **Tool path mismatch**: Agents may solve tasks through different tool paths than `expected_mcp_tools` defines (e.g., using `fetch_market_data` + `compute_indicator` instead of `file_read` + `shell_exec`), resulting in Tool F1 = 0 even when the agent produces correct outputs.
6. **Tutor dimension weakness**: D6 (Empathetic Response) and D7 (Safety Boundaries) score systematically low (~0.3) across all adapters, suggesting the system prompt needs stronger guidance for these dimensions.

---

## Changelog

### 2026-02-13: Systematic 5-Layer Fix (Agent + Tool + Eval)

Through 7-task × 2-adapter comparison testing, code audit, and OpenAI Agents SDK source study, identified and fixed 5 layers of issues. Pre-fix OpenAI SDK mean Overall = 0.331, GenericLLM = 0.337 (nearly identical, indicating SDK had no agent advantage).

#### Layer 0 — Critical Bug Fix: `args` vs `input_args` key mismatch

- **File**: `mcp_servers/proxy/mcp_proxy.py`
- **Problem**: `to_dict()` exported `"args"` key, but all 7 eval scripts read `log.get("input_args", {})` → always empty → eval scripts could never extract tool parameters (send_message text, shell_exec commands, etc.)
- **Fix**: Changed `"args"` → `"input_args"` in `to_dict()` (1 line)

#### Layer 1 — Tool Parameter Description Enhancement

- **Files**: `mcp_servers/core/tools.py`, `mcp_servers/distractors/distractor_tools.py`, both adapters
- **Problem**: All tool parameter descriptions were `f"{param_name} parameter"` (meaningless). Models had no idea what values to pass, what format to use, or which params were optional.
- **Fix**: Enhanced all 14 core tools + 15 distractor tools with proper `type`, `description`, and `required` fields. Updated both adapters to parse the new param structure. Also fixed missing `"items"` field for array-type parameters (`format_table.columns`, `compare_series.paths`, `optimize_portfolio.weights`) that caused OpenAI API 400 errors.

#### Layer 2 — True Agent Architecture (OpenAI SDK)

- **File**: `orchestrator/agent_adapters/openai_adapter.py`
- **Problem**: Adapter only did single LLM call + manual tool execution, not using SDK's agent loop. No multi-step reasoning.
- **Fix**: Rewrote to use `Agent(instructions=callable)` for dynamic per-task context injection + `Runner.run_sync(max_turns=8)` for autonomous multi-step reasoning. Built complete JSON schemas for `FunctionTool` from enhanced param structures.

#### Layer 3 — System Prompt Optimization

- **File**: `config/prompt_config.py`
- **Problem**: Prompt rule 3 ("Ask leading questions") discouraged action while rule 5 ("USE TOOLS proactively") demanded it — contradictory.
- **Fix**: Rewrote rules 3-5 to guide tool usage with good judgment: teach with real data when appropriate, scaffold learning, but don't force tool calls for simple conceptual questions.

#### Layer 4 — Eval Script Improvements

- **Files**: All 7 eval scripts in `evaluation/test_scripts/`
- Added `conversation=None` parameter to all eval `evaluate()` signatures for future conversation-based fallback scoring
- Added process metrics to D01 (`data_exploration_attempted`, `code_executed`) and X01 (`buggy_code_read`, `fix_verified_by_execution`)
- Improved Sharpe ratio regex in S01 with strict-first pattern matching
- Updated `orchestrator.py` to pass conversation to eval scripts

#### Post-Fix Benchmark Results (7 tasks × 2 adapters, Docker sandbox)

**max-turns=5:**

| Metric | OpenAI SDK | GenericLLM | Delta |
|--------|-----------|------------|-------|
| Overall (mean) | **0.414** | 0.391 | +0.023 |
| Quant Result (mean) | **0.314** | 0.236 | +0.079 |
| Tool F1 (mean) | 0.354 | 0.363 | -0.009 |
| Tutor Score (mean) | 0.487 | **0.524** | -0.037 |

**max-turns=10 (S01, I01, X01 only):**

| Task | OpenAI SDK (t=5→t=10) | GenericLLM (t=5→t=10) |
|------|----------------------|----------------------|
| S01 | 0.33 → **0.57** (+73%) | 0.40 → 0.42 (+5%) |
| I01 | 0.35 → 0.32 (-9%) | 0.42 → 0.39 (-7%) |
| X01 | 0.26 → **0.62** (+139%, QR=1.0) | 0.22 → 0.23 (+5%) |

Key findings:
- Quant Result improved from ~0.04 to 0.314 (SDK) after Layer 0 fix
- OpenAI SDK benefits significantly from more turns (S01 +73%, X01 +139%), Generic does not — confirming the agent loop works
- S01/I01/X01 had QR=0 at max-turns=5; at max-turns=10, SDK achieved QR=0.60 (S01) and QR=1.00 (X01)
- B01/X01 show Tool F1=0 due to agent choosing alternative tool paths vs expected_tools

#### Open Issues for Investigation

1. **Is the SDK adapter truly agentic?** — Need deeper analysis of Runner behavior and turn-by-turn tool calling patterns
2. **Tool path divergence** — Agents prefer `fetch_market_data` + `compute_indicator` over `file_read` + `shell_exec`; need to revisit `expected_tools` definitions or add prompt guidance
3. **D6/D7 systematically low** — Empathetic response and safety boundary scores are weak across all configurations
4. **A01 tutor score anomaly** — Adversarial task scores QR=1.0 but tutor score is very low because irrelevant dimensions (D5 Code Teaching) penalize it
5. **I01 remains QR=0 even at max-turns=10** — Agent produces file path inconsistencies (writes to one path, reads from another), a reasoning quality issue not solvable by more turns alone
