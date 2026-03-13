# End-to-End Section (E-Series) Design Plan

> Version: v2.2 | Status: **Complete and Operationally Validated** — all code, evals, and reference data generated; live suite runs complete end-to-end | Section: Workflow Orchestration

---

## 1. Section Philosophy

### 1.1 What E-Series Tests

E-series tests the agent's ability to act as a **workflow orchestrator** — guiding a student through a complete multi-stage quant research pipeline in a single session. Where every other section isolates a single skill (D: data, S: research, B: engine, I: implementation, X: debugging), E-series combines 2-3 of these skills into a coherent end-to-end workflow.

The critical distinction: **E-series does not introduce any new technical skill.** Every individual step in an E-task has already been tested in isolation by an earlier section. What is new is the *integration* — maintaining context across stages, making principled transitions between tools and environments, and producing a coherent final artifact that spans the full pipeline.

```
┌─────────────────────────────────────────────────────────┐
│              Given: Open-ended Research Brief              │
│  "Build a momentum strategy" or "Debug this algorithm     │
│   and verify the fix." No step-by-step instructions —     │
│   agent must decompose into stages and orchestrate.       │
└──────────────────────┬──────────────────────────────────┘
                       │ agent decomposes & orchestrates
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Multi-Stage Pipeline                          │
│  Stage 1: Explore / hypothesize     (D-series skills)     │
│  Stage 2: Define / formalize signal (S-series skills)     │
│  Stage 3: Backtest / evaluate       (B-series skills)     │
│  Stage 4: Implement in LEAN         (I-series skills)     │
│  Stage 5: Debug / iterate           (X-series skills)     │
│                                                           │
│  Each stage feeds into the next. Agent must maintain       │
│  context, carry artifacts forward, and transition cleanly. │
└──────────────────────┬──────────────────────────────────┘
                       │ agent produces integrated output
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Output: Complete Research Artifact             │
│  Working code + metrics + comparison + discussion.         │
│  Evaluated on pipeline completion (did all stages          │
│  happen?) AND correctness (are the outputs right?).       │
└─────────────────────────────────────────────────────────┘
```

**The core evaluation question**: Given an open-ended research objective, can the agent decompose it into stages, execute each stage using appropriate tools, carry artifacts forward between stages, and produce a coherent end-to-end result — all while maintaining the tutoring dialogue with the student?

### 1.2 Why Integration Testing Matters

Individual section scores can be misleading. An agent that scores well on S-series (research) and I-series (implementation) separately might fail when asked to do both in sequence because:

1. **Context loss**: The signal definition from the research phase doesn't carry forward cleanly into the implementation phase.
2. **Tool transitions**: Moving from Python pandas to LEAN C# requires re-framing the problem, not just switching languages.
3. **Coherence failures**: The Python backtest and the LEAN backtest should produce broadly similar results. An agent that doesn't notice a large discrepancy hasn't truly "done research to implementation."
4. **Scope management**: Without step-by-step prompts, the agent must decide when to stop exploring and start implementing — a planning skill not tested by any individual section.

E-series is the **only section** that tests these integration skills.

### 1.3 Position in the Quant Workflow Pipeline

E-series sits at the **right end** of the pipeline, consuming skills from all upstream sections:

```
D (Data)  →  S (Strategy Research)  →  B (Backtest Engine)  →  I (Implementation)  →  X (Debug)  →  E (End-to-End)
  │               │                       │                       │                     │              │
"Get and        "Discover and            "Build the system       "Write the          "Find and fix   "Do it all
 understand      formalize the            to validate             algorithm from       bugs in         in one
 the data"       alpha idea"              strategies"             scratch"             algorithms"     session"
```

| Section | Relationship to E-series |
|---------|--------------------------|
| **D-series** (Data) | E03/E05 begin with data exploration (descriptive stats, distributions) |
| **S-series** (Strategy Research) | E02/E03/E05 include signal formalization and evaluation |
| **B-series** (Backtest Engine) | E02/E03/E05 include Python prototyping / backtesting |
| **I-series** (Implementation) | E02/E04/E05 include LEAN C# implementation |
| **X-series** (Debug) | E04 is a compound debugging task |

### 1.4 Three Pipeline Patterns

E-series tasks fall into three structural patterns based on which sandboxes they use:

```
Pattern A: Python Only                  Pattern B: LEAN Only                Pattern C: Python + LEAN
──────────────────────                  ────────────────────                ────────────────────────
quant-tutor-env:v2.2                    quant-tutor-env:v2.2-lean           quant-tutor-env:v2.2-lean
                                                                            (includes Python)

  Data → Signal → Backtest               Buggy Code → Diagnose               Research → Prototype →
  → Evaluate → Report                    → Fix → Verify                      Implement → Compare

  Exercises: D + S + B                    Exercises: I + X                    Exercises: S + B + I  or
  Example: E03                            Example: E04                        D + S + B + I
                                                                              Examples: E02, E05
```

**Pattern A (E03)**: Pure Python research — explore, define, backtest, evaluate. No LEAN involved. Tests the D→S→B pipeline in Python alone.

**Pattern B (E04)**: LEAN-only debugging — start from broken C#, systematically diagnose compound bugs, fix, and verify. Tests the I→X pipeline using the LEAN engine.

**Pattern C (E02, E05)**: Dual-sandbox workflow — research in Python, implement in LEAN C#, compare results. Tests the S→B→I pipeline (E02) or the full D→S→B→I pipeline (E05). Uses `quant-tutor-env:v2.2-lean` which includes both Python and the LEAN engine.

### 1.5 No New Skills, Only Integration

A strict design constraint: **every individual check in an E-task eval must correspond to a skill already tested by D/S/B/I/X.** If an E-task eval requires a check that no upstream section tests, the check belongs in an upstream section instead.

This ensures E-series measures **orchestration overhead** — the cost of combining skills — not new technical capabilities. A perfect score on all upstream sections should, in theory, guarantee a perfect E-series score. In practice, the integration cost is substantial.

### 1.6 The Two Integration Skills E-Series Uniquely Tests

While E-series re-uses upstream skills, the *combination* surfaces two capabilities that no upstream section tests in isolation:

**Pipeline planning**: The agent must decompose an open-ended brief into ordered stages and communicate a plan before executing. A weak agent dives into code immediately; a strong agent outlines "Step 1: explore, Step 2: define signal, Step 3: backtest, ..." before writing a single line. E-series evals detect this via `pipeline_structure_present`.

**Artifact continuity**: When transitioning between stages (especially Python → LEAN), the agent must carry forward the *same* signal definition, parameters, and logic — not re-invent them. A common failure mode: the Python prototype uses BB(20, 2.0) but the LEAN implementation quietly switches to SMA crossover. E-series evals detect this via `signal_consistency_between_stages` (E02, E05).

### 1.7 Current Operational Status (2026-03-13)

E-series is now **operationally validated** in live runs. The full E01–E05 suite executes end-to-end, saves artifacts, and completes without infrastructure-level blockers. In practice:

1. **Harness usability**: The benchmark runner, sandbox, evaluator stack, and result persistence all work for the full E-series.
2. **Provider/runtime stability**: OpenRouter-routed runs with `gpt-4o-mini` complete cleanly for the suite; no sandbox/network or tracing blocker was observed during the E-series usability run.
3. **Model-quality caveat**: Operational validity does **not** imply strong end-to-end reasoning. The dominant failure mode in the live run was that the agent stayed in explanatory tutoring mode and did not complete the full multi-stage workflow with runnable artifacts. Tasks therefore completed with low `QR` even though the pipeline itself worked.

The practical conclusion is: **E-series can work today**, and its current bottleneck is model workflow execution quality rather than benchmark infrastructure.

---

## 2. Difficulty Calibration

### 2.1 Tool Usage Expectations

E-tasks are inherently longer than single-section tasks because they span multiple pipeline stages. Expected tool call ranges:

| Task | Pattern | Tool Calls | Max Turns | Timeout |
|------|---------|------------|-----------|---------|
| E01 | A | 8-15 | 40 | 25 min |
| E02 | C | 10-18 | 40 | 30 min |
| E03 | A | 8-15 | 35 | 25 min |
| E04 | B | 8-16 | 40 | 30 min |
| E05 | C | 12-20 | 45 | 35 min |

### 2.2 Difficulty Progression

| Task | Difficulty | Sections Combined | Reasoning |
|------|-----------|-------------------|-----------|
| E01 | medium | S + B | Two-stage Python-only. Already exists. |
| E02 | medium | S + B + I | Three-stage cross-language, but single asset and well-defined strategy. |
| E03 | medium | D + S + B | Three-stage Python-only. Rigorous methodology (IS/OOS) adds conceptual difficulty, but no LEAN complexity. |
| E04 | hard | I + X | Compound debugging — three interacting bugs in a single LEAN algorithm. Requires systematic isolation of each bug. |
| E05 | hard | D + S + B + I | **Four-stage**, full pipeline, cross-language. Hardest task in the benchmark. |

### 2.3 Gate Design

Each E-task has **gates** — hard caps on the maximum score if critical pipeline stages are missing. Gates prevent high scores from chatting about the topic without actually executing the pipeline.

| Gate Condition | Typical Cap | Rationale |
|----------------|------------|-----------|
| No LEAN backtest (E02/E04/E05) | 0.30-0.35 | Implementation is the core cross-language skill |
| No Python metrics (E02/E03/E05) | 0.25-0.40 | Research phase must produce quantitative output |
| No signal artifact (E03/E05) | 0.25-0.30 | Can't validate what wasn't formalized |
| No train/test split (E03) | 0.25 | Methodology is the point of E03 |
| <2 bugs fixed (E04) | 0.35 | Compound debugging must find most bugs |

---

## 3. Task Designs

### 3.0 Existing Task: E01 — Build MA Crossover System

**Status**: **Exists**. Task JSON, eval script implemented and tested.

**Pattern**: A (Python only)

**Sections combined**: S + B — Define a moving average crossover strategy, then build and backtest it in Python.

**Sandbox**: `quant-tutor-env:v2.2`

**Summary**: Guide a student through building a complete MA crossover trading system from scratch — data loading, indicator computation, signal generation, backtesting, and result interpretation.

**Files**: `E01_build_ma_system.json` (task), `E01_build_ma_system.py` (eval).

---

### 3.1 E02 — Research to Implementation (S + B + I)

**Difficulty**: medium
**Category**: end_to_end
**Pattern**: C (Python + LEAN)
**Sandbox**: `quant-tutor-env:v2.2-lean`

**Scenario**: Student researches a Bollinger Band mean-reversion signal on BTC in Python, backtests it to get preliminary metrics, then implements the same strategy as a LEAN C# algorithm and compares results across backends.

**Why this tests integration**: The cross-language transition (Python → C#) is the hardest part. The agent must ensure the LEAN implementation matches the Python prototype's logic — same BB parameters, same entry/exit conditions, same position sizing. Discrepancies between Python and LEAN results should be noticed and discussed.

**Pipeline stages**:
```
1. Define BB signal parameters (period=20, std=2.0)
2. Python backtest on BTC data → extract Sharpe/return/drawdown
3. Implement equivalent strategy in LEAN C# → compile & run
4. Compare Python vs LEAN results → discuss discrepancies
```

**Student openings**:
- **beginner_no_finance**: "I've been reading about Bollinger Bands and mean reversion. I want to try building a strategy that buys BTC when it drops below the lower band. Can you help me prototype it in Python first, and then maybe implement it in C# for the LEAN engine?"
- **intermediate_developer**: "I want to prototype a BB(20,2) mean-reversion strategy on BTC in Python, get baseline metrics, then implement the same logic in LEAN C#. I want to compare the Python and LEAN backtests to make sure they agree. Where do we start?"
- **advanced_quant**: "I'm building a Bollinger Band mean-reversion strategy for BTCUSDT. The workflow is: prototype in Python to validate the signal, then port to LEAN C# for production-grade backtest with proper order execution. I need the two backends to produce comparable results. Let's architect this systematically."

**Data**: `BTC_UTC.csv`, `universe.json`
**Docs**: `moving_averages.md`, `backtesting_101.md`, `lean_algorithm_guide.md`, `risk_metrics.md`

**Eval checklist**:

| Check | Weight | Method |
|-------|--------|--------|
| `pipeline_structure_present` | 0.05 | Conversation contains ordered plan (step/phase numbering, "first...then...finally") |
| `signal_defined_in_python` | 0.15 | Python code has BB / rolling std signal definition |
| `python_backtest_produces_metrics` | 0.15 | Python output includes Sharpe/return/drawdown numbers |
| `lean_backtest_completed` | 0.20 | LEAN summary.json exists in workspace/results/ |
| `lean_trade_log_produced` | 0.05 | LEAN trades with >0 entries |
| `behavioral_score` | 0.20 | `compute_behavioral_score("E02", workspace_path)` |
| `signal_consistency` | 0.05 | Same parameters (BB period/std) appear in both Python code and C# code |
| `comparison_discussed` | 0.10 | Mentions "python vs lean" / "discrepancy" / "comparison" |
| `code_is_modular` | 0.05 | >=2 Python files or >=3 function defs |

**Gates**:
- No LEAN backtest → cap 0.30
- No Python metrics → cap 0.40

**Imports**: `_strategy_research_check` (collect_artifact_text, has_signal_definition, has_metric_numbers, conversation_text, has_any, workspace_has_csv_columns), `_implementation_check` (compute_behavioral_score, collect_lean_results, load_agent_trades, check_csharp_patterns), `_data_source_check` (verify_data_source)

**Reference algorithm**: `E02_bb_reversion.cs` — BB(20, 2.0) on BTCUSDT, daily, 2022-2025. Buy when price < lower band, sell when price > middle band. SetHoldings(1.0).

---

### 3.2 E03 — Strategy Validation (D + S + B)

**Difficulty**: medium
**Category**: end_to_end
**Pattern**: A (Python only)
**Sandbox**: `quant-tutor-env:v2.2`

**Scenario**: Student validates a time-series momentum signal on SPY with rigorous train/test methodology. Tests whether the agent can guide proper in-sample / out-of-sample separation and overfitting assessment.

**Why this tests integration**: The D→S→B pipeline requires moving cleanly from exploration (what does the data look like?) to formalization (define the signal) to evaluation (is the signal real?). The key integration skill is maintaining methodological discipline across stages — the agent must ensure the train/test split happens *before* evaluating the signal's true quality, not after cherry-picking parameters.

**Pipeline stages**:
```
1. Explore data: descriptive stats, return distributions, autocorrelation
2. Define momentum signal: pct_change(lookback), ranking, thresholds
3. Evaluate signal quality: IC, quantile analysis, hit rate
4. Train/test split backtest: IS period for development, OOS for validation
5. Compare IS vs OOS metrics → assess overfitting
6. Discuss robustness: regime sensitivity, parameter sensitivity, degradation
```

**Student openings**:
- **beginner_no_finance**: "I want to build a momentum strategy on SPY. I've heard that stocks that went up recently tend to keep going up. But how do I know if this actually works, or if I'm just fooling myself with past data? Can you help me test this properly?"
- **intermediate_developer**: "I'm building a time-series momentum signal on SPY — something like ROCP(20) or trailing returns. I want to do this rigorously with proper train/test separation to avoid overfitting. Can you walk me through the full validation workflow?"
- **advanced_quant**: "I need to validate a time-series momentum factor on SPY with proper IS/OOS methodology. The pipeline should be: EDA, signal formalization, IC analysis, walk-forward or fixed-split backtest, and robustness assessment. I want to quantify how much the signal degrades out-of-sample."

**Data**: `SPY_2018_2024.csv`, `AAPL_2018_2024.csv`
**Docs**: `alpha_research_methodology.md`, `signal_evaluation.md`, `backtesting_101.md`, `statistical_tests.md`

**Eval checklist**:

| Check | Weight | Method |
|-------|--------|--------|
| `pipeline_structure_present` | 0.05 | Conversation contains ordered plan (step/phase numbering, "first...then...finally") |
| `exploratory_analysis_performed` | 0.10 | Evidence of descriptive stats, autocorrelation, distribution |
| `signal_formalized` | 0.15 | Signal definition artifact (momentum, pct_change, lookback) |
| `signal_evaluated` | 0.15 | IC metrics or quantile analysis present |
| `train_test_split_implemented` | 0.20 | Code splits data by date into distinct train/test periods |
| `is_oos_metrics_separated` | 0.20 | Both in-sample AND out-of-sample metrics reported separately |
| `robustness_discussed` | 0.10 | Conversation mentions overfitting gap, regime sensitivity, degradation |
| `visualization_produced` | 0.05 | Chart files (.png/.svg) in workspace |

**Gates**:
- No train/test split → cap 0.25
- No signal artifact → cap 0.30
- No OOS metrics → cap 0.40

**Imports**: `_strategy_research_check` (collect_artifact_text, has_signal_definition, has_metric_numbers, conversation_text, has_any, workspace_has_csv_columns), `_data_source_check` (verify_data_source)

**No reference data needed** — E03 is Python-only with purely checklist-based evaluation. No LEAN backtest, no behavioral scoring.

---

### 3.3 E04 — Production Debugging (I + X)

**Difficulty**: hard
**Category**: end_to_end
**Pattern**: B (LEAN only)
**Sandbox**: `quant-tutor-env:v2.2-lean`

**Scenario**: Student has a LEAN C# EMA crossover algorithm with **three interacting bugs**. The agent must guide systematic diagnosis of each bug, explain root causes, apply fixes, and verify the corrected algorithm produces reasonable results.

**Why this tests integration**: Compound debugging is qualitatively harder than single-bug debugging (X-series). The three bugs interact — Bug 1 (wrong signal direction) causes losing trades, Bug 3 (2x leverage) amplifies those losses, and Bug 2 (missing warm-up) adds noise on top. Fixing only one bug shows partial improvement, which can mislead the student into thinking the fix is complete. The agent must systematically isolate all three.

**The three bugs**:

1. **Logic bug**: `_emaFast < _emaSlow` instead of `_emaFast > _emaSlow` — enters on bearish crossover instead of bullish. Still produces trades but with wrong timing.
2. **Missing warm-up**: No `SetWarmUp()` and no `if (IsWarmingUp) return;` — EMA values garbage in first ~50 bars, causing spurious early trades.
3. **Excessive leverage**: `SetHoldings(_btc, 2.0m)` — 200% position. Amplifies losses from Bug 1's wrong-direction entries.

**Bug interaction**: Wrong signals + unreliable indicators + 2x leverage = systematic losses. Fixing only one bug shows partial improvement — student must find all three.

**Buggy code location**: `bench/data/frozen/E04_compound_bug.cs` — mounted at `/data/E04_compound_bug.cs` read-only via `data_files`. Student opening directs agent to examine this file.

**Pipeline stages**:
```
1. Read buggy code → identify suspicious patterns
2. Run buggy algorithm → observe poor performance
3. Diagnose Bug 1 (signal direction) → explain bearish vs bullish crossover
4. Diagnose Bug 2 (missing warm-up) → explain EMA initialization
5. Diagnose Bug 3 (excessive leverage) → explain position sizing risk
6. Apply all three fixes → compile and run
7. Verify improved performance → compare before/after
```

**Student openings**:
- **beginner_no_finance**: "I wrote an EMA crossover strategy in LEAN C# but it's losing a lot of money. I thought EMA crossovers were supposed to be a decent baseline strategy. The code compiles and runs fine, but the results are terrible. Can you help me figure out what's wrong?"
- **intermediate_developer**: "My LEAN EMA(20/50) crossover algorithm is producing large systematic losses. The code runs without errors but performance is terrible. I suspect there might be multiple issues interacting with each other. Can you help me systematically debug it?"
- **advanced_quant**: "I have a LEAN EMA crossover algo that's hemorrhaging capital. Initial analysis suggests compound issues — the signal timing looks wrong, there may be initialization problems, and the position sizing seems aggressive. I need to systematically isolate and fix each bug. The buggy code is at /data/E04_compound_bug.cs."

**Data**: `E04_compound_bug.cs`, `universe.json`
**Docs**: `lean_algorithm_guide.md`, `risk_metrics.md`

**Eval checklist**:

| Check | Weight | Method |
|-------|--------|--------|
| `logic_bug_fixed` | 0.20 | `check_fix_applied`: fix=`_emaFast > _emaSlow` or `fastValue > slowValue`, bug=`_emaFast < _emaSlow` |
| `warmup_added` | 0.15 | `check_fix_applied`: pattern `SetWarmUp\s*\(` |
| `warmup_guard_added` | 0.10 | `check_fix_applied`: pattern `IsWarmingUp` |
| `position_size_fixed` | 0.10 | `check_fix_applied`: fix=`SetHoldings.*[01]\.\d`, bug=`SetHoldings.*2\.0` |
| `root_causes_explained` | 0.10 | `check_root_cause_explained`: keywords for all 3 bugs |
| `backtest_completed` | 0.10 | LEAN summary.json exists after fix |
| `trades_produced` | 0.10 | Trade count > 0 after fix |
| `behavioral_score` | 0.15 | `compute_behavioral_score("E04", workspace_path)` |

**Gates**:
- <2 bugs fixed → cap 0.35
- No backtest after fix → cap 0.40

**Imports**: `_debug_check` (check_fix_applied, check_root_cause_explained, check_fix_verified), `_implementation_check` (compute_behavioral_score, collect_lean_results, check_csharp_patterns), `_data_source_check` (verify_data_source)

**Reference algorithm**: `E04_compound_fixed.cs` — EMA(20/50) crossover on BTCUSDT, SetWarmUp(50), IsWarmingUp guard, SetHoldings(1.0), daily, 2022-2025.

---

### 3.4 E05 — Full Quant Workflow (D + S + B + I)

**Difficulty**: hard
**Category**: end_to_end
**Pattern**: C (Python + LEAN)
**Sandbox**: `quant-tutor-env:v2.2-lean`

**Scenario**: Complete quant research cycle. Student explores BTC data, designs a momentum strategy, prototypes in Python, implements in LEAN, evaluates performance, and discusses risks. **This is the hardest task in the benchmark.**

**Why this tests integration**: E05 is the maximum-integration test — four pipeline stages across two languages. The agent must:
1. Guide meaningful data exploration (not rote describe commands)
2. Formalize a signal from the exploration (not invent one from thin air)
3. Prototype and evaluate in Python (producing quantitative metrics)
4. Implement in LEAN C# (translating the Python prototype faithfully)
5. Compare results across backends (noticing and explaining discrepancies)
6. Discuss risks (connecting back to the exploration and evaluation)

Each stage must build on the previous one. An agent that skips steps or doesn't carry artifacts forward will score poorly.

**Pipeline stages**:
```
1. Explore data: BTC price patterns, volatility, return distributions
2. Form hypothesis: "Momentum (recent winners keep winning) captures crypto trends"
3. Formalize signal: ROCP(20) on ~20 symbols, long top-5
4. Python prototype backtest → extract Sharpe/return/drawdown
5. Validation: train/test split or OOS evaluation
6. LEAN C# implementation → compile & run → extract trade log
7. Compare Python vs LEAN results → discuss discrepancies
8. Discuss risks: regime dependence, crowding, transaction costs
```

**Student openings**:
- **beginner_no_finance**: "I want to do a complete quant research project on crypto. I've got BTC data and want to go from exploring the data all the way to having a working strategy running on the LEAN engine. I know some Python and I'm willing to learn C#. Can you guide me through the whole process?"
- **intermediate_developer**: "I want to build a momentum strategy for crypto futures. The full pipeline: explore the BTC data, define a momentum signal, prototype and validate in Python, then implement in LEAN C# for production-grade backtest. I want to do this rigorously with proper methodology."
- **advanced_quant**: "I'm running a complete quant research cycle on crypto momentum. Pipeline: EDA on BTC, formalize a cross-sectional ROCP signal on ~20 liquid perps, prototype backtest in Python with IS/OOS validation, port to LEAN C# for production execution, and compare backends. Let's architect this end-to-end."

**Data**: `BTC_UTC.csv`, `universe.json`
**Docs**: `alpha_research_methodology.md`, `signal_evaluation.md`, `backtesting_101.md`, `lean_algorithm_guide.md`, `risk_metrics.md`

**Eval checklist**:

| Check | Weight | Method |
|-------|--------|--------|
| `pipeline_structure_present` | 0.05 | Conversation contains ordered plan (step/phase numbering, "first...then...finally") |
| `data_exploration_performed` | 0.07 | Descriptive statistics, return analysis evidence |
| `signal_formalized` | 0.10 | Signal definition artifact (momentum, ranking, pct_change) |
| `signal_evaluated` | 0.08 | IC or quantile metrics present |
| `python_backtest_metrics` | 0.10 | Python backtest produces Sharpe/return/drawdown |
| `validation_performed` | 0.10 | Train/test split or OOS evaluation present |
| `lean_backtest_completed` | 0.15 | LEAN summary.json exists |
| `lean_trade_log_produced` | 0.05 | LEAN trades with >0 entries |
| `behavioral_score` | 0.15 | `compute_behavioral_score("E05", workspace_path)` |
| `signal_consistency` | 0.08 | Same signal type (momentum/ROCP) and parameters in Python code and C# code |
| `comparison_discussed` | 0.07 | Python-vs-LEAN comparison evidence |

**Gates**:
- No LEAN backtest → cap 0.35
- No Python metrics → cap 0.40
- No signal artifact → cap 0.30

**Runtime safety**: E05 is the longest task (35 min timeout). To prevent agents from burning time on repeated LEAN runs, the eval counts LEAN compilation attempts. More than 3 LEAN runs without a successful backtest indicates thrashing, not progress.

**Imports**: `_strategy_research_check` (collect_artifact_text, has_signal_definition, has_metric_numbers, conversation_text, has_any, workspace_has_csv_columns), `_implementation_check` (compute_behavioral_score, collect_lean_results, load_agent_trades, check_csharp_patterns), `_data_source_check` (verify_data_source)

**Reference algorithm**: `E05_momentum_topn.cs` — ROCP(20) momentum on ~20 tier2 symbols, long top-5, daily rebalancing, 2022-2025.

---

## 4. Evaluation Approach

### 4.1 Checklist + Behavioral Scoring

E-series evals combine two approaches:

**Checklist scoring** (all tasks): Boolean checks for pipeline stage completion. "Did the agent produce a signal definition?" "Did the LEAN backtest complete?" Each check has a weight; the score is the weighted sum of passed checks.

**Behavioral scoring** (E02, E04, E05 only): For tasks with LEAN output, `compute_behavioral_score()` compares the agent's trade log against the reference implementation across four layers (signal agreement, position overlap, performance proximity, trade similarity). This measures not just "did it run?" but "did it produce the right results?"

### 4.2 Gate Logic

Gates implement hard caps on the maximum achievable score when critical pipeline stages are missing. The gate check happens after the weighted sum and overrides the score if the condition is met:

```python
# Example gate logic:
if not results["lean_backtest_completed"]:
    score = min(score, 0.30)  # Cap at 30%
```

Gates serve two purposes:
1. **Prevent prose-only scoring**: An agent that discusses momentum strategies eloquently but never runs code shouldn't score above 30%.
2. **Enforce pipeline completion**: The integration test loses its meaning if critical stages are skipped.

### 4.3 Data Source Verification

All E-series evals import `_data_source_check.verify_data_source()` to verify that the agent actually accessed the task's data files rather than fabricating synthetic data. If data files weren't accessed, the score is discounted proportionally.

### 4.4 Integration-Specific Checks

Two checks are unique to E-series — they test planning and artifact continuity, the two capabilities that only emerge when skills are combined:

**`pipeline_structure_present`** (E02, E03, E05 — weight 0.05):

Detects whether the agent communicated a structured plan before diving into execution. Looks for ordered step language in the assistant's conversation turns:

```python
# Detection: any of these patterns in assistant conversation text
plan_patterns = [
    r"step\s*[123]",
    r"phase\s*[123]",
    r"first.*then.*finally",
    r"pipeline.*:.*\n.*\d\.",
    r"1\.\s.*\n.*2\.\s.*\n.*3\.\s",
]
```

This is intentionally lightweight (0.05 weight) — planning is valuable but not the core deliverable.

**`signal_consistency_between_stages`** (E02, E05 — weight 0.05-0.08):

Detects whether the Python and LEAN implementations use the same signal logic. For E02, checks that Bollinger Band parameters (period, std multiplier) appear in both Python and C# artifacts. For E05, checks that the momentum signal type (ROCP, pct_change) carries across.

```python
# Example: extract BB period from both artifacts
python_text = collect_artifact_text(workspace, tool_logs)  # includes .py files
cs_text = collect_csharp_text(workspace)                    # includes .cs files
# Check: both mention the same period (e.g., "20") near "bollinger" or "rolling"
```

This prevents the common failure mode where an agent prototypes strategy A in Python, then implements strategy B in C# — producing two unrelated backtests that happen to both succeed.

---

## 5. Reference Data Strategy

### 5.1 Which Tasks Need Reference Data

| Task | Reference Algo | Reference Data | Behavioral Scoring |
|------|---------------|----------------|-------------------|
| E01 | None | None | No (checklist only) |
| E02 | `E02_bb_reversion.cs` | trades + signals + summary | Yes |
| E03 | None | None | No (checklist only) |
| E04 | `E04_compound_fixed.cs` | trades + signals + summary | Yes |
| E05 | `E05_momentum_topn.cs` | trades + signals + summary | Yes |

### 5.2 Reference Algorithm Specifications

**E02 — Bollinger Band Mean Reversion**:
- Asset: BTCUSDT perpetual, daily
- Parameters: BB(20, 2.0) — 20-period SMA, 2.0 standard deviation bands
- Entry: close < lower band → long (SetHoldings 1.0)
- Exit: close > middle band (SMA) → flatten
- Period: 2022-01-01 to 2025-12-31

**E04 — EMA Crossover (Corrected)**:
- Asset: BTCUSDT perpetual, daily
- Parameters: EMA(20) fast, EMA(50) slow
- Entry: fast > slow → long (SetHoldings 1.0)
- Exit: fast < slow → flatten
- Warm-up: SetWarmUp(50, Resolution.Daily) + IsWarmingUp guard
- Period: 2022-01-01 to 2025-12-31

**E05 — Momentum Top-N**:
- Universe: ~20 tier2 symbols from universe.json
- Parameters: ROCP(20) — 20-day rate of change percentage
- Signal: rank symbols by ROCP, long top-5
- Sizing: equal-weight across top-5 positions
- Rebalancing: daily
- Period: 2022-01-01 to 2025-12-31

### 5.3 Generation

Reference data is generated by extending `bench/reference/generate_lean_reference.py` with entries for E02, E04, and E05 in `TASK_ALGO_MAP` and `class_name_map`. All three are single-run tasks (no parameter sweep). Generation command:

```bash
python bench/reference/generate_lean_reference.py --task E02
python bench/reference/generate_lean_reference.py --task E04
python bench/reference/generate_lean_reference.py --task E05
```

---

## 6. Orchestrator Integration

### 6.1 No Orchestrator Changes Required

The existing orchestrator handles E-series tasks without modification:

- **LEAN data**: Auto-mounted when `"lean" in sandbox_image` (orchestrator line 159). Works for `end_to_end` category.
- **Buggy code (E04)**: Delivered via `data_files` → mounted at `/data/` read-only. The student opening tells the agent where to find the file.
- **student_code_dir**: NOT needed. E-series is `end_to_end` category, not `debug`. The orchestrator only mounts `student_code/` for `category == "debug"`.
- **sample_code field**: Used by the prompt system to inject a code location hint. E04 sets `sample_code: "data/E04_compound_bug.cs"` to hint the agent where the buggy code lives.

### 6.2 Sandbox Assignments

| Task | Sandbox | Reason |
|------|---------|--------|
| E01 | `quant-tutor-env:v2.2` | Python-only |
| E02 | `quant-tutor-env:v2.2-lean` | Python + LEAN |
| E03 | `quant-tutor-env:v2.2` | Python-only |
| E04 | `quant-tutor-env:v2.2-lean` | LEAN debugging |
| E05 | `quant-tutor-env:v2.2-lean` | Python + LEAN |

---

## 7. File Inventory

### 7.1 New Files

| # | File | Type | Status |
|---|------|------|--------|
| 1 | `bench/*MD/v2.0/end_to_end_section_plan.md` | Design doc (this file) | Done |
| 2 | `bench/data/frozen/E04_compound_bug.cs` | Buggy student code | Done |
| 3 | `bench/tasks/layer2/end_to_end/E02_research_to_implementation.json` | Task definition | Done |
| 4 | `bench/tasks/layer2/end_to_end/E03_strategy_validation.json` | Task definition | Done |
| 5 | `bench/tasks/layer2/end_to_end/E04_production_debugging.json` | Task definition | Done |
| 6 | `bench/tasks/layer2/end_to_end/E05_full_quant_workflow.json` | Task definition | Done |
| 7 | `bench/evaluation/test_scripts/E02_research_to_implementation.py` | Eval script (9 checks) | Done |
| 8 | `bench/evaluation/test_scripts/E03_strategy_validation.py` | Eval script (8 checks) | Done |
| 9 | `bench/evaluation/test_scripts/E04_production_debugging.py` | Eval script (8 checks) | Done |
| 10 | `bench/evaluation/test_scripts/E05_full_quant_workflow.py` | Eval script (11 checks) | Done |
| 11 | `bench/reference/lean_algorithms/E02_bb_reversion.cs` | Reference algo | Done |
| 12 | `bench/reference/lean_algorithms/E04_compound_fixed.cs` | Reference algo | Done |
| 13 | `bench/reference/lean_algorithms/E05_momentum_topn.cs` | Reference algo | Done |

### 7.2 Modified Files

| File | Change | Status |
|------|--------|--------|
| `bench/reference/generate_lean_reference.py` | Add E02/E04/E05 to TASK_ALGO_MAP and class_name_map | Done |
| `bench/reference/generate_reference_signals.py` | Add `_signals_e02/e04/e05()` + register in SIGNAL_GENERATORS | Done |

### 7.3 Generated Files (from reference generation)

| File | Source | Status |
|------|--------|--------|
| `bench/data/reference/E02_reference_trades.json` | LEAN backtest | ✅ 26 trades, +24.9% return |
| `bench/data/reference/E02_reference_signals.json` | Signal extraction | ✅ 1,442 signals |
| `bench/data/reference/E02_reference_summary.json` | Performance summary | ✅ |
| `bench/data/reference/E04_reference_trades.json` | LEAN backtest | ✅ 12 trades, +77.1% return |
| `bench/data/reference/E04_reference_signals.json` | Signal extraction | ✅ 1,461 signals |
| `bench/data/reference/E04_reference_summary.json` | Performance summary | ✅ |
| `bench/data/reference/E05_reference_trades.json` | LEAN backtest | ✅ 1,159 trades, +104.8% return |
| `bench/data/reference/E05_reference_signals.json` | Signal extraction | ✅ 27,455 signals |
| `bench/data/reference/E05_reference_summary.json` | Performance summary | ✅ |

---

## 8. Verification Checklist

1. **Schema validation**: Load each task JSON through `json.load()` — verify parsing ✅ All 4 parse correctly
2. **Checklist weight sums**: Verify all weights sum to 1.0 per eval script ✅ E02=1.000, E03=1.000, E04=1.000, E05=1.000
3. **Data file existence**: All `data_files` entries exist in `bench/data/frozen/` or are auto-staged
4. **Docs existence**: All `docs_available` entries exist in `bench/docs/reference/`
5. **Eval script dry-run**: Each script handles empty workspace gracefully (score=0) ✅ All return score=0.0
6. **E04 buggy code compiles**: Compile in LEAN Docker → runs but produces bad results (no crash)
7. **Reference generation**: Run for E02/E04/E05 → verify trade counts reasonable ✅ E02=26, E04=12, E05=1159
8. **Behavioral scoring**: Reference files load correctly for E02/E04/E05 task IDs ✅ All reference data generated

## 9. Implementation Notes

### 9.1 Filename Deviations from Design

- `E02_bb_reversion_fixed.cs` → `E02_bb_reversion.cs` (dropped `_fixed` — it's a reference, not a fix)
- `E05_momentum_fixed.cs` → `E05_momentum_topn.cs` (clearer name describing the strategy)
- `E04_compound_fixed.cs` kept as-is (emphasizes it's the corrected version of the buggy code)

### 9.2 Additional Modification

`generate_reference_signals.py` was also modified (not originally listed in Section 7.2). Three new signal generators (`_signals_e02`, `_signals_e04`, `_signals_e05`) were added and registered in `SIGNAL_GENERATORS`.

### 9.3 Reference Data Results

Reference data generated successfully for all three LEAN-backed tasks:

| Task | Trades | Return | Sharpe | Signals | Strategy |
|------|--------|--------|--------|---------|----------|
| E02 | 26 | +24.9% | — | 1,442 | BB(20,2) mean-reversion, BTCUSDT daily |
| E04 | 12 | +77.1% | — | 1,461 | EMA(20/50) crossover (fixed), BTCUSDT daily |
| E05 | 1,159 | +104.8% | — | 27,455 | ROCP(20) top-5 momentum, 20 tier2 symbols daily |

Trade counts are consistent with strategy design:
- E02/E04: Single-symbol daily strategies → low trade counts expected
- E05: 20-symbol daily rebalancing → ~1K trades over 4 years is reasonable
