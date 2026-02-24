# QuantTutorBench — Framework Preview

> A two-axis benchmark for evaluating quantitative finance tutoring agents.
> Measures **Quant Agent expertise (70%)** and **Tutoring quality (30%)** in a sandboxed, tool-augmented environment.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       Orchestration Host                        │
│                                                                 │
│  run_benchmark.py  ──►  BenchmarkOrchestrator                   │
│       │                       │                                 │
│       │                  ┌────┴────────────────────────┐        │
│       │                  │  Per-Task Lifecycle (5 phases)│       │
│       │                  │  1. RESET   → Docker + Proxy │       │
│       │                  │  2. INTERACT → Simulator      │       │
│       │                  │  3. CAPTURE  → Trace logs     │       │
│       │                  │  4. EVALUATE → 3-axis scoring │       │
│       │                  │  5. TEARDOWN → Cleanup        │       │
│       │                  └───────────────────────────────┘       │
│       │                                                         │
│  ┌────┴─────────┐   ┌──────────────────┐   ┌────────────────┐  │
│  │ Agent Adapters│   │ Docker Sandbox    │   │  DeepEval      │  │
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

## 2. Two-Layer Evaluation Structure

### Layer 1 — Core Capabilities (single-turn, ~40 items)

Tests foundational quant knowledge via single-turn Q&A, sourced from FiQA, TAT-QA, FinQA, ConvFinQA, StackExchange, Reddit, CFPB, FINRA, SEC.

| Category             | Eval Method                       |
| -------------------- | --------------------------------- |
| Conceptual Q&A       | DeepEval GEval (domain rubric)    |
| Strategy Explanation | DeepEval GEval (strategy rubric)  |
| Code Generation      | 60% execution + 40% GEval         |
| Code Debugging       | 60% execution + 40% GEval         |
| Data Interpretation  | DeepEval GEval (data rubric)      |
| Multi-step Reasoning | DeepEval GEval (reasoning rubric) |

GEval scoring scale: **1-10** (normalized to 0-1), with category-specific rubrics defining what each score level means.

### Layer 2 — Tutoring Skills (multi-turn, 7 tasks x 3 personas = 21 instances)

Tests interactive tutoring via multi-turn dialogue with tool use in a sandboxed Docker environment.

| Category       | Task ID | Key Challenge                          | Difficulty |
| -------------- | ------- | -------------------------------------- | ---------- |
| Data Analysis  | D01     | Load/inspect OHLCV, explain columns    | Easy       |
| Strategy       | S01     | Design MA crossover strategy           | Easy       |
| Implementation | I01     | Implement SMA in pandas                | Medium     |
| Backtest       | B01     | Interpret backtest metrics             | Medium     |
| Debug          | X01     | Fix off-by-one bug in MA calculation   | Easy       |
| End-to-End     | E01     | Build complete MA system from scratch  | Hard       |
| Adversarial    | A01     | Refuse investment advice appropriately | Medium     |

Each task runs with **3 student personas**, producing distinct dialogue traces per persona.

---

## 3. Student Personas

| Persona ID                 | Level        | Background                              | Emotional Profile      |
| -------------------------- | ------------ | --------------------------------------- | ---------------------- |
| `beginner_no_finance`    | Beginner     | Basic Python, no financial knowledge    | Curious & anxious      |
| `intermediate_developer` | Intermediate | Proficient Python/pandas, basic finance | Pragmatic & impatient  |
| `advanced_quant`         | Advanced     | Deep stats, Python, trading strategy    | Analytical & skeptical |

Each persona defines:

- **known_concepts** / **unknown_concepts** — drives what the simulated student should already understand
- **emotional_profile** — shapes response tone
- **behavioral_rules** — specific behavioral patterns (e.g., beginner asks "what does OHLCV mean?"; advanced challenges assumptions)

---

## 4. Scoring Architecture

### 4.1 Task-Level Formula

```
Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score

Quant Agent Score = 0.50 × Result Sub-score + 0.50 × Process Sub-score
  Result Sub-score = λ × Layer1 + (1-λ) × Layer2   (λ = 0.40)
  Process Sub-score = mean of DeepEval process metrics (Layer 2)

Tutor Score = mean of 7D rubric scores (each 0-1, Layer 2)
```

### 4.2 Quant Result Sub-score

**Layer 1**: Category-specific GEval (code tasks blend 60% execution + 40% GEval code quality).

**Layer 2**: Per-task eval scripts validate agent outputs (e.g., data loaded? Sharpe ratio computed? Bug fixed?).

### 4.3 Quant Process Sub-score (8 DeepEval Metrics)

| Metric               | Type        | What It Measures                          |
| -------------------- | ----------- | ----------------------------------------- |
| Tool Correctness     | Single-turn | Did the agent select the right tools?     |
| Argument Correctness | Single-turn | Were tool call arguments correct?         |
| MCP Use Quality      | Single-turn | LLM-judged tool selection quality         |
| Step Efficiency      | Single-turn | Reasonable number of steps?               |
| Multi-Turn MCP Use   | Multi-turn  | Contextual tool usage across conversation |
| Role Adherence       | Multi-turn  | Does agent stay in tutor role?            |
| Knowledge Retention  | Multi-turn  | Does agent remember earlier context?      |
| Topic Adherence      | Multi-turn  | Does agent stay on quant finance topics?  |

Aggregate: `Process Sub-score = mean(all metric scores)`

### 4.4 Tutor Score — 7D Rubric

Each dimension scored **1-10** (normalized to 0-1), with **persona-specific rubrics** (beginner/intermediate/advanced):

| Dim | Name                    | Focus                                               |
| --- | ----------------------- | --------------------------------------------------- |
| D1  | Level Detection         | Identifies and adapts to learner's level            |
| D2  | Language Adaptation     | Uses appropriate language for learner type          |
| D3  | Scaffolding Calibration | Provides appropriate step-by-step guidance          |
| D4  | Domain Accuracy         | Factually correct financial/technical information   |
| D5  | Code Teaching           | Correct, well-explained, appropriately complex code |
| D6  | Empathetic Response     | Responds to emotional cues, provides encouragement  |
| D7  | Safety & Boundaries     | No investment advice, includes risk disclaimers     |

**Judge stability**: Each dimension is evaluated **3 times** with shuffled dimension order; final score = mean of 3 runs.

```
Tutor Score = mean(D1, D2, D3, D4, D5, D6, D7)
```

---

## 5. Benchmark-Level KPIs

| KPI | Name                         | Formula                                                      |
| --- | ---------------------------- | ------------------------------------------------------------ |
| OAS | Overall Agent Score          | `0.70 × QAI + 0.30 × TEI` (across all tasks)             |
| QAI | Quant Agent Index            | `0.50 × Result_Sub + 0.50 × Process_Sub`                 |
| TEI | Tutoring Effectiveness Index | `mean(D1..D7)` across all tasks                            |
| AS  | Adaptiveness Score           | Tutor score std-dev across personas (higher = more adaptive) |
| TMS | Tool Mastery Score           | `mean(precision × recall)` across tasks                   |

Additional metrics:

- **pass@1, pass@3**: Did the agent pass (score >= 0.5) in k trials?
- **pass^3**: Did the agent pass every single trial?
- **95% Confidence Intervals**: On OAS, QAI, TEI
- **Cost Estimation**: Total USD + average per task

---

## 6. 2x2 Test Condition Matrix

```
              │  Tools Enabled        │  No Tools              │
──────────────┼───────────────────────┼────────────────────────│
Tutor prompt  │  agent                │  pure_llm              │
Baseline      │  baseline             │  pure_llm_baseline     │
```

| Condition             | Tools | Prompt   | Purpose                                   |
| --------------------- | ----- | -------- | ----------------------------------------- |
| `agent`             | Yes   | Tutor    | Full agent: SDK + tools + teaching prompt |
| `baseline`          | Yes   | Baseline | SDK + tools + "dump-the-answer" prompt    |
| `pure_llm`          | No    | Tutor    | Pure LLM reasoning + teaching prompt      |
| `pure_llm_baseline` | No    | Baseline | Lowest effort baseline                    |

---

## 7. Testing Flow (5-Phase Lifecycle)

```
Phase 1: RESET
  ├─ Create staged directories (symlinks to task-allowed data/docs only)
  ├─ Launch Docker sandbox (--network none, CPU:2, RAM:4GB)
  ├─ Set env vars: QTB_DATA_DIR, QTB_DOCS_DIR, QTB_WORKSPACE_DIR
  ├─ Create MCP proxy with task-specific core tools + sampled distractors
  └─ Inject dynamic context into agent system prompt (task + persona info)

Phase 2: INTERACT
  ├─ DeepEval ConversationSimulator plays student role (persona-driven)
  ├─ Agent responds via SDK adapter (with tool calling if enabled)
  ├─ Multi-turn dialogue continues up to max_turns (default 5-30)
  └─ Fallback: manual student simulation if DeepEval unavailable

Phase 3: CAPTURE
  └─ MCP Proxy logs every tool call: name, args, result, timing, success/failure

Phase 4: EVALUATE (post-hoc)
  ├─ Quant Result Score: per-task eval scripts validate outputs
  ├─ Quant Process Score: 8 DeepEval process metrics (tool use, role, topic, etc.)
  ├─ Tutor Score: 7D ConversationalGEval (3x shuffled judge runs)
  └─ Aggregate: Task Score = 0.70 × Quant + 0.30 × Tutor

Phase 5: TEARDOWN
  └─ Destroy container + clean staged directories + save results JSON
```

---

## 8. Score Summary Visual

```
                     ┌─────────────────────────────────┐
                     │     Overall Agent Score (OAS)    │
                     │  = 0.70 × QAI + 0.30 × TEI      │
                     └──────────┬────────────┬──────────┘
                                │            │
              ┌─────────────────┘            └─────────────────┐
              ▼                                                ▼
   ┌──────────────────────┐                     ┌──────────────────────┐
   │  Quant Agent Index   │                     │  Tutoring Effect.    │
   │  (QAI) — 70%         │                     │  Index (TEI) — 30%   │
   │  = 0.50×Res+0.50×Pro │                     │  = mean(D1..D7)      │
   └────┬────────────┬────┘                     └──────────┬───────────┘
        │            │                                     │
        ▼            ▼                                     ▼
  ┌──────────┐ ┌──────────┐               ┌────────────────────────────┐
  │ Result   │ │ Process  │               │  7D Rubric (per persona)   │
  │ Sub-score│ │ Sub-score│               │  D1: Level Detection       │
  │ λ=0.40  │ │ 8 metrics│               │  D2: Language Adaptation   │
  │ L1 + L2 │ │          │               │  D3: Scaffolding           │
  └────┬─┬──┘ └──────────┘               │  D4: Domain Accuracy       │
       │ │                                │  D5: Code Teaching         │
       ▼ ▼                                │  D6: Empathetic Response   │
  ┌─────┐ ┌─────┐                        │  D7: Safety & Boundaries   │
  │ L1  │ │ L2  │                        └────────────────────────────┘
  │ 40% │ │ 60% │
  └─────┘ └─────┘
```
