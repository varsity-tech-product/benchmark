# QuantTutorBench v3.0: A Dual-Layer, Dual-Axis Benchmark for Quantitative Finance Tutoring Agents

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

## 2. Dual-Layer Task Architecture

QuantTutorBench v3.0 adopts a **dual-layer evaluation architecture** covering the full assessment spectrum from single-turn QA to multi-turn tutoring.

### 2.1 Layer 2 — Multi-Turn Tutoring Scenarios (65 Tasks)

The agent acts as a quantitative finance tutor, guiding students through complete quant tasks via multi-turn dialogue (up to 30 turns). Each task is equipped with a Docker sandbox, curated toolset, and data files.

| Series | Category | Tasks | Difficulty |
|--------|----------|-------|------------|
| **D** | Data Analysis | 11 | 4E / 4M / 3H |
| **S** | Strategy Research | 6 | 1E / 2M / 3H |
| **B** | Backtesting | 6 | 1E / 2M / 3H |
| **I** | LEAN Implementation | 10 | 1E / 3M / 6H |
| **X** | Code Debugging | 10 | 2E / 2M / 6H |
| **E** | End-to-End Workflow | 5 | 0E / 3M / 2H |
| **A** | Adversarial | 17 | 1E / 10M / 6H |
| **Total** | | **65** | |

Each task is configured with 3 student personas (beginner_no_finance / intermediate_developer / advanced_quant), yielding **195 evaluation instances**.

### 2.2 Layer 1 — Single-Turn Capability Tests (37 Tasks)

Single-turn QA format, directly assessing the agent's quantitative finance knowledge and reasoning.

| Category | Tasks | Evaluation |
|----------|-------|------------|
| Conceptual QA | 16 | GEval rubric (no tools) |
| Code Debugging | 4 | Code execution (60%) + GEval (40%) |
| Code Generation | 4 | Code execution (60%) + GEval (40%) |
| Data Interpretation | 3 | GEval rubric (table/document) |
| Multi-step Reasoning | 6 | GEval rubric (numerical chains) |
| Strategy Explanation | 4 | GEval rubric (strategy depth) |
| **Total** | **37** | |

**Overall scale: 102 tasks, 232 evaluation instances.**

---

## 3. Layer 2 Task Sets

### 3.1 D-Series — Data Analysis (11 Tasks)

Covers the full quantitative finance data workflow: loading, cleaning, exploration, feature engineering.

| Task | Diff. | Description | Data |
|------|-------|-------------|------|
| D01 | Easy | Load and explore OHLCV stock data | AAPL, SPY |
| D02 | Easy | Detect and handle missing values | AAPL_dirty |
| D03 | Easy | Data type conversion and schema validation | AAPL_dirty |
| D04 | Easy | Compute and interpret summary statistics | AAPL, SPY |
| D05 | Medium | Simple returns vs log returns | AAPL, SPY |
| D06 | Medium | Aggregate tick data into OHLCV bars | tick_data_sample |
| D07 | Hard | Diagnose multiple data-feed issues | AAPL_dirty |
| D08 | Hard | Align alternative data with price data | AAPL + sentiment |
| D09 | Medium | Build a feature-engineering pipeline | AAPL, SPY |
| D10 | Easy | Fetch historical data from public APIs | Network-enabled |
| D11 | Medium | Collect realtime market data stream | Network-enabled |

### 3.2 S-Series — Strategy Research (6 Tasks)

Covers the complete strategy research pipeline from simple construction to composite alpha synthesis.

| Task | Diff. | Description | Data |
|------|-------|-------------|------|
| S01 | Easy | Build and test an MA crossover strategy | AAPL, SPY |
| S02 | Medium | BTC trend-following alpha research | BTCUSDT 1d |
| S03 | Medium | BTC mean-reversion alpha research | BTCUSDT 1d |
| S04 | Hard | Volume/microstructure alpha research | BTCUSDT multi-TF |
| S05 | Hard | BTC-ETH cross-asset alpha research | BTC+ETH multi-TF |
| S06 | Hard | Multi-signal composite alpha synthesis | BTC+ETH+Funding |

### 3.3 B-Series — Backtesting (6 Tasks)

Covers the full chain from metric interpretation to complete backtest engine architecture.

| Task | Diff. | Description |
|------|-------|-------------|
| B01 | Easy | Interpret basic backtest metrics |
| B02 | Medium | Build a basic sequential backtest engine |
| B03 | Medium | Build a look-ahead-proof backtest engine |
| B04 | Hard | Multi-asset synchronized backtest engine |
| B05 | Hard | Execution simulation backtest engine (slippage/fees/funding) |
| B06 | Hard | Walk-forward validation framework |

### 3.4 I-Series — LEAN Algorithm Implementation (10 Tasks)

Tests the ability to implement quantitative strategies on the QuantConnect LEAN engine, from single-asset to full-universe.

| Task | Diff. | Description |
|------|-------|-------------|
| I01 | Easy | Single-symbol SMA strategy |
| I02 | Medium | Full-universe dual MA crossover strategy |
| I03 | Medium | Full-universe RSI mean-reversion strategy |
| I04 | Hard | Multi-timeframe strategy (Consolidate) |
| I05 | Hard | Pairs trading strategy |
| I06 | Hard | Multi-signal parameter sweep |
| I07 | Medium | Alpha Model framework migration |
| I08 | Hard | Multi-alpha composition system |
| I09 | Hard | Risk management model comparison |
| I10 | Hard | Systematic parameter optimization |

### 3.5 X-Series — Code Debugging (10 Tasks)

Tests the agent's ability to locate and fix common bugs in quantitative code.

| Task | Diff. | Description |
|------|-------|-------------|
| X01 | Easy | Off-by-one error in moving average calculation |
| X02 | Easy | Look-ahead bias in SMA crossover strategy |
| X03 | Medium | Missing short signal in Bollinger Band strategy |
| X04 | Medium | Returns calculation bug (diff vs pct_change) |
| X05 | Hard | Timezone misalignment in crypto-stock correlation |
| X06 | Hard | Overfitting diagnosis in 12-parameter strategy |
| X07 | Hard | Missing WarmUp period in LEAN EMA strategy |
| X08 | Hard | Order type bug in LEAN momentum strategy |
| X09 | Hard | Conflicting alpha model insights in LEAN |
| X10 | Hard | Survivorship bias in LEAN universe filter |

### 3.6 E-Series — End-to-End Workflow (5 Tasks)

Tests the agent's ability to complete a full quantitative research cycle.

| Task | Diff. | Description |
|------|-------|-------------|
| E01 | Medium | Build a complete MA trading system from scratch |
| E02 | Medium | Python prototype to LEAN C# implementation |
| E03 | Medium | Rigorous time-series momentum signal validation |
| E04 | Hard | LEAN multi-bug systematic debugging |
| E05 | Hard | Complete quant research cycle (data to LEAN) |

### 3.7 A-Series — Adversarial (17 Tasks)

Tests the agent's safety, compliance, and judgment when facing inappropriate requests.

| Sub-category | Tasks | Description |
|--------------|-------|-------------|
| **Teaching Boundaries** | A01-A04 | Investment advice refusal, balancing education & code delivery, misleading metric correction, emotional support |
| **Compliance & Legal** | A05-A06, A09-A12 | Spoofing, wash trading, insider trading, pump & dump, window dressing |
| **Cognitive Biases** | A07-A08 | Data mining fallacy, impossible backtest results |
| **Security Attacks** | A13-A17 | Path traversal, obfuscated malicious code, credential exfiltration, CSV prompt injection, fork bomb |

---

## 4. Tool System

### 4.1 Core Tools (16)

| Tool | Function |
|------|----------|
| `shell_exec` | Execute shell commands in container (supports Python scripts) |
| `file_write` / `file_read` / `file_list` | Workspace file I/O and directory browsing |
| `get_environment_info` | Get environment paths and available files |
| `fetch_market_data` | Fetch OHLCV data from frozen CSV |
| `compute_indicator` | Compute technical indicators (SMA/EMA/RSI/BOLL/MACD) |
| `run_backtest` | Run built-in strategy backtests (3 strategies) |
| `analyze_backtest_results` | Backtest performance analysis (Sharpe/drawdown/Sortino/Calmar) |
| `evaluate_signal` | Signal quality evaluation (IC/quantile returns/turnover) |
| `compute_statistics` | Statistical tests (ADF/correlation/cointegration/Lead-Lag) |
| `plot_chart` | Execute matplotlib code to generate charts |
| `search_docs` / `search_web` | Documentation and web search |
| `construct_signal` | 7 signal types (zscore/momentum/mean_reversion/spread/crossover/composite/volume_imbalance) |
| `engineer_features` | 11 feature types (VWAP ratio/volume zscore/realized vol/ATR/OBV/...) |

### 4.2 Advanced Quant Tools (4)

| Tool | Function |
|------|----------|
| `compare_backtest_results` | Multi-backtest comparison (Bootstrap CI / paired t-test) |
| `align_timeseries` | Merge N CSVs on common time axis |
| `breakdown_pnl` | Fee/slippage/funding PnL decomposition |
| `split_walkforward_windows` | Walk-forward window splitting |

### 4.3 Convenient Tools and Distractor Tools

- **Convenient tools**: Non-essential but workflow-simplifying tools (configured per task). Using them earns a bonus (+0.05/n); not using them incurs no penalty
- **Distractor tools** (10 functional): Task-irrelevant tools randomly sampled from a global pool. Calling a distractor incurs a -0.10 penalty per tool
- Each task has a fixed **15 tool slots** = core + convenient + distractors

---

## 5. Sandbox and Execution Environment

### 5.1 Full-Container Sandbox (Docker v2.2)

**All core tools** are routed through the Docker container for complete isolation:

```
Agent <-> MCP Proxy <-> tool_executor.py (in-container JSON-RPC daemon) <-> tools.py
```

| Feature | Configuration |
|---------|---------------|
| Base Image | `quant-tutor-env:v2.2` (Python 3.11) |
| Dependencies | pandas, numpy, scipy, matplotlib, scikit-learn, arch, statsmodels |
| Mount Points | `/workspace`(RW), `/data`(RO), `/docs`(RO), `/student_code`(RO) |
| Resource Limits | Standard 768MB/1CPU, LEAN 1GB/2CPU |
| Network | Isolated by default (--network none), per-task opt-in |
| Protocol | stdin/stdout JSON-lines, thread-safe (Lock) |
| Timeout | 600s per tool call (signal.alarm) |

### 5.2 Five-Phase Execution Lifecycle

```
Phase 1: RESET
  |-- Download HF dataset, create staged directories (hardlink/copy)
  |-- Create Docker sandbox container (resource + network isolation)
  |-- Start in-container tool_executor daemon
  |-- Configure MCP Proxy (core + convenient + distractor tools = 15)
  +-- Inject task/persona dynamic context into agent system prompt

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
  |-- 4e. QP seven-dimension process evaluation
  +-- 4f. Tutor 7D dual-channel evaluation (3x shuffle, multi-model)

Phase 5: TEARDOWN
  |-- Destroy Docker container
  |-- Clean up staged temporary directories
  +-- Aggregate token costs
```

---

## 6. Evaluation System

### 6.1 QR — Quality of Result (Three-Source Fusion)

| Signal Source | Method | Focus |
|---------------|--------|-------|
| **Programmatic Eval Script** | Per-task hand-written checklist | Deterministic: file existence, numeric ranges, keyword checks |
| **Code Execution Eval** | Three-layer code analysis | Does the code run? Are the results correct? |
| **LLM Result Judge** | Multi-model LLM assessment | Completeness (55%) + Correctness (45%) |

**Code Execution Eval — Three-Layer Structure:**
- **Layer A — Static Analysis (20%)**: AST-parses .py files from workspace; checks syntax and structure
- **Layer B — Execution Verification (40%)**: Parses shell_exec results; tracks last execution per script; detects untested files
- **Layer C — Output Verification (40%)**: Compares outputs against Reference baseline data using relative error thresholds

**QR Fusion Formula:**
```
Code tasks:     QR = w_prog x Programmatic + 0.30 x Code_Eval + w_judge x LLM_Judge
Non-code tasks: QR = 0.40 x Programmatic + 0.60 x LLM_Judge
```
where `w_prog` and `w_judge` are dynamically adjusted by a dampening factor (divergence between Programmatic and Judge scores).

### 6.2 QP — Quality of Process (Seven Weighted Dimensions)

| Dimension | Weight | Method | Description |
|-----------|--------|--------|-------------|
| **tool_usage** | 20% | Pure math | Selection & effectiveness of core/convenient/distractor tools |
| **process_reasonableness** | 20% | LLM-judged | Problem decomposition, execution soundness, error handling |
| **step_efficiency** | 15% | Hybrid | Action economy, redundancy avoidance, logical sequencing |
| **code_process** | 15% | Hybrid | Iterative refinement, test-before-deliver, debugging competence |
| **process_alignment** | 10% | LLM-judged | Alignment with Reference execution trace (skipped for adversarial) |
| **role_adherence** | 10% | LLM-judged | Maintains tutor role throughout |
| **topic_adherence** | 10% | LLM-judged | Stays on quantitative finance topics |

**Key design principle:** Custom Python code via shell_exec is treated as equivalent to calling a dedicated tool (no tool-choice bias).

### 6.3 Tutor 7D — Tutoring Quality

Seven dimensions evaluated per persona using ConversationalGEval (1-10 scale, normalized to 0-1):

| Dimension | Assessment |
|-----------|------------|
| D1 Level Detection | Accurately identifies student knowledge level |
| D2 Language Adaptation | Terminology matches student proficiency |
| D3 Scaffolding | Progressively guides learning |
| D4 Domain Accuracy | Quantitative finance knowledge correctness |
| D5 Code Teaching | Code teaching quality and explanation clarity |
| D6 Empathetic Response | Understands student emotions, responds appropriately |
| D7 Safety & Boundaries | Refuses inappropriate requests, maintains clear boundaries |

**Dual-channel input mechanism:**
- Original conversation channel: Pure text exchange (for D1, D2, D3, D6)
- Enriched conversation channel: Appends tool activity summaries (for D4, D5, D7)

Each dimension is evaluated 3 times with randomized dimension ordering (shuffle judge), averaged to reduce LLM positional bias.

### 6.4 Data Source Verification

All 15 evaluation scripts (D01-D09, S01, I01, B01, X01, E01, A01) include a **data source verification** step that checks whether the agent actually accessed the specified data files. Scores are capped to `0.25x` when verification fails.

---

## 7. Multi-Agent SDK Support

QuantTutorBench supports 5 agent SDK adapters for cross-platform agent capability comparison:

| Adapter | SDK | Key Feature |
|---------|-----|-------------|
| **generic** | OpenRouter | Universal LLM fallback (any model) |
| **openai** | OpenAI Agents SDK | FunctionTool native integration |
| **anthropic** | Anthropic Python SDK | BetaToolRunner + Extended Thinking |
| **google** | Google AI Dev Kit (ADK) | Native ADK tool calls |
| **baseline** | None | Direct answer output (control baseline) |

All adapters support per-call token extraction for precise cost accounting.

---

## 8. OAuth Authentication

QuantTutorBench supports OAuth token authentication for Anthropic models, enabling **Claude Max** usage without API keys:

| Component | Detail |
|-----------|--------|
| Token Resolution | Two-tier fallback: macOS Keychain (`Claude Code-credentials`) then `CLAUDE_CODE_OAUTH_TOKEN` env var |
| Evaluation Models | `EVAL_USE_OAUTH = True` — all LLM-as-Judge evaluators use OAuth by default |
| Agent Models | `AGENT_USE_OAUTH` — configurable per deployment |
| Beta Header | `oauth-2025-04-20` (Anthropic beta protocol) |
| Transport | Requires `ANTHROPIC_USE_SDK = False` (BetaToolRunner mode) |

**Two independent switches:**
- **Transport** (`ANTHROPIC_USE_SDK`): Claude Agent SDK (black-box loop, API key only) vs BetaToolRunner (visible tool loop, OAuth compatible)
- **Auth** (`AGENT_USE_OAUTH`): OAuth token vs API key (only applies to BetaToolRunner mode)

This eliminates API key costs for evaluation runs while preserving full functionality.

---

## 9. Extended Thinking Visualization

QuantTutorBench captures and visualizes **Anthropic Extended Thinking** (chain-of-thought) traces, providing full transparency into the agent's reasoning process.

### 9.1 Backend Capture

| Feature | Implementation |
|---------|----------------|
| Thinking Extraction | `_extract_thinking()` captures `thinking` content blocks per iteration |
| Context Management | `clear_thinking_20251015` — auto-strips thinking from message history after capture (saves input tokens) |
| Thinking Budget | Configurable via `ANTHROPIC_THINKING_BUDGET` (default 4096 tokens) |
| Per-Turn Blocks | `get_content_blocks()` returns structured `{type: "thinking"/"tool_use"/"tool_result"/"text"}` per turn |

### 9.2 Frontend Rendering

**Live streaming mode:**
- Animated thinking indicator ("Tutor is thinking...") with bouncing dot animation during generation
- Thinking text updates in real-time as tokens stream

**Conversation display (live and replay):**
- Collapsible `<details>`-style thinking blocks with toggle headers
- Thinking content rendered in monospace `<pre>` with word-wrap
- Integrated into content block rendering pipeline alongside tool_use, tool_result, and text blocks

**Trace report (saved):**
- Full untruncated thinking text preserved in `trace.md` per iteration
- Indexed by turn and iteration number

### 9.3 OpenAI Reasoning Support

OpenAI's `reasoning_effort` parameter is supported via `OPENAI_ENABLE_REASONING` and `OPENAI_REASONING_EFFORT` config. Reasoning effort levels: `none`, `low`, `medium`, `high`. Note: OpenAI does not expose reasoning text to the client.

---

## 10. Web Dashboard

A real-time monitoring and result browsing system built on FastAPI + SSE (Server-Sent Events). This is a fully new component in v3.0.

### 10.1 Core Modules

| Module | Feature |
|--------|---------|
| **Task Browser** | Browse all tasks by category with metadata and configuration |
| **Single Run** | Select agent/model/task/persona, launch single-task evaluation |
| **Group Run** | Batch run by category with parallel workers |
| **Live Monitor** | SSE push: conversation messages, tool call status, inline chart display |
| **Result Viewer** | Browse saved results: full conversation replay, tool call details, score reports |

### 10.2 Inline Chart Display

Charts generated by the agent via `plot_chart` are **displayed inline** in the conversation:
- **Live mode**: Served via `/api/files/live/{filename}` from the active workspace
- **Replay mode**: Loaded from `agent_files/` directory of saved results
- **Dedup logic**: Pre-scans text blocks for markdown image references, filters duplicates from tool card images

### 10.3 SSE Architecture

- Broadcast + replay buffer (max 1000 events) for client reconnect resilience
- Sequence numbering for crash recovery
- 10s heartbeat timeout on idle
- No-cache middleware for JS/CSS hot-reloading

---

## 11. Reference Baseline System

Generates "gold-standard" execution traces for each task x persona combination.

| Component | Function |
|-----------|----------|
| **Generation CLI** | Execute oracle run, promote best result to reference |
| **Storage Format** | trace_summary, step_count, key_results, workspace_files, full_trace |
| **Evaluation Integration** | Layer C numerical comparison, Step Efficiency ratio, Process Alignment trace matching, Result Judge output comparison |

Dimensions gracefully degrade to standalone assessment when no reference is available.

---

## 12. Cost Tracking and Result Persistence

### 12.1 Token-Level Cost Tracking

End-to-end cost tracking from agent execution to evaluation:

| Cost Category | Source |
|---------------|--------|
| **Agent cost** | SDK adapter extracts usage from API responses (input/output tokens) |
| **Simulator cost** | ConversationSimulator student message generation |
| **Evaluation cost** | Per-model cost from each LLM-as-Judge evaluator |

### 12.2 Result Persistence

| File | Content | Generated When |
|------|---------|----------------|
| `scores.md` | Full score report (QR/QP/7D breakdown) | `--save-result` |
| `trace.md` | Full execution trace (conversation + tool calls, untruncated) | `--save-result` |
| `cost.md` | Token usage and cost breakdown (Agent/Simulator/Eval) | `--save-result` |
| `agent_files/` | Copy of agent-produced workspace files (CSV, PNG, etc.) | `--save-result` |
| `run_state.json` | Reproducible execution state snapshot | `--runonly` |

**Two-phase execution mode:**
- `--runonly`: Run agent interaction only, save run_state.json (skip evaluation)
- `--evalonly`: Load run_state.json, run evaluation pipeline only (reproduce scores)

---

## 13. CLI Commands

Main entry: `python -m run_benchmark`

| Subcommand | Function |
|------------|----------|
| `run` | Full benchmark (both layers) |
| `run-single` | Single task x persona run |
| `run-group` | Batch run by category |
| `run-layer2` | All Layer 2 tasks (65) |
| `run-layer1` | All Layer 1 tasks (37) |
| `list-tasks` | List all tasks with metadata |
| `validate-tasks` | Validate task JSONs against schema |
| `test-e2e` | End-to-end validation test |

---

## 14. Scoring Formula Summary

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

## 15. Improvements over v2.0

| Area | v2.0 | v3.0 | Change |
|------|------|------|--------|
| **Layer 2 Tasks** | 33 tasks (4 categories: D/S/B/I) | 65 tasks (7 categories) | +X (debugging), +E (end-to-end), +A (adversarial) |
| **Layer 2 Instances** | 99 (33 x 3 personas) | 195 (65 x 3 personas) | ~2x increase |
| **Total Tasks** | 70 (33 L2 + 37 L1) | 102 (65 L2 + 37 L1) | +32 new Layer 2 tasks |
| **Code Debugging** | No dedicated series | X-series: 10 tasks (off-by-one, look-ahead, LEAN bugs) | New capability axis |
| **End-to-End** | No dedicated series | E-series: 5 tasks (full quant research cycles) | New capability axis |
| **Adversarial** | No dedicated series | A-series: 17 tasks (teaching/compliance/security) | Systematic safety & ethics evaluation |
| **Core Tools** | 12 | 16 + 4 advanced | +construct_signal, engineer_features, align_timeseries, etc. |
| **Tool System** | Core + distractors | Core + convenient + distractors (15 slots) | Convenient tool bonus mechanism |
| **Data Source Verification** | None | 15 eval scripts with shared `verify_data_source()` | Catches agents that skip required data |
| **OAuth Authentication** | None | macOS Keychain + env var, eval + agent modes | Claude Max usage without API keys |
| **Extended Thinking** | None | Anthropic thinking capture + live visualization + trace export | Full reasoning transparency |
| **Web Dashboard** | None | FastAPI + SSE: live monitor, task browser, result viewer, inline charts | Complete visual management interface |
| **Inline Chart Display** | None | Live + replay modes with dedup logic | Agent-generated charts visible in conversation |
| **OpenAI Reasoning** | None | `reasoning_effort` parameter support | Configurable reasoning depth |
