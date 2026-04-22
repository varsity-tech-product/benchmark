# QuantTutorBench Evaluation System Proposal

**Status:** Implementation Proposal (based on codebase at commit `0466a80`)
**Date:** 2026-02-23
**Purpose:** Explain how the evaluation system's dataset, environment, and ability testing are designed and why each decision was made.

---

## 1. What Problem Are We Solving?

Existing AI benchmarks test LLMs on either **knowledge** (can it answer correctly?) or **task completion** (can it get the job done?). Neither tests whether an AI agent can **teach** a domain effectively.

QuantTutorBench fills this gap for quantitative finance. We evaluate two orthogonal abilities:

- **Does the agent know quant finance?** (70% of score)
- **Can it teach quant finance to a real student?** (30% of score)

These are genuinely independent. An agent can be a quant expert that dumps jargon on beginners (high quant, low tutor). Or it can be a patient, empathetic teacher that confidently explains wrong strategies (low quant, high tutor). The benchmark distinguishes all four quadrants.

---

## 2. Why Two Layers?

A single evaluation layer cannot test both raw knowledge and interactive teaching ability. We decompose the problem:

| | Layer 1 | Layer 2 |
|---|---|---|
| **What it tests** | Does the model **know** quant finance? | Can the agent **teach** quant finance? |
| **Format** | Single-turn: question in, answer out | Multi-turn: conversation with simulated student + tool use |
| **Environment** | None (pure LLM call, no tools, no sandbox) | Full sandbox: Docker container, MCP tools, data files, docs |
| **Student** | None | DeepEval ConversationSimulator playing a persona |
| **Items** | ~2,000 | ~500 (41 base tasks x 3 personas x multiple trials) |
| **Cost per item** | ~$0.02-0.10 | ~$0.50-2.00 |
| **Purpose** | Statistical power (large N, cheap) | Ecological validity (realistic, interactive) |

Layer 1 is fast, cheap, and gives broad coverage. Layer 2 is expensive but tests what actually matters for a tutoring agent. Together they answer: "Does the agent know the right answer?" (L1) and "Can it guide a specific student to understand the answer?" (L2).

---

## 3. Dataset Design Decisions

### 3.1. Layer 1 Dataset (~2,000 single-turn items)

Layer 1 items are sourced from public financial QA datasets, classified into 7 source categories, then organized into 6 task categories based on what ability they test:

#### Source Classification

The data pipeline classifies 119,441 structured records from the ingestion pipeline into 7 categories via deterministic rules on `source_dataset`:

| Source Category | Records | Origin | Primary Value |
|---|---|---|---|
| **reddit** | 69,865 | r/personalfinance, r/investing, r/stocks | Real learner language, diverse question styles |
| **fiqa** | 17,072 | FiQA dataset (BeIR) | General financial Q&A, opinion-based |
| **stack_exchange** | 5,123 | Money.StackExchange | Expert-voted Q&A, real confusion patterns |
| **authoritative_docs** | 139 | SEC Investor.gov, CFPB, FINRA | Golden factual answers, regulatory content |
| **finqa** | varies | FinQA dataset | Numerical reasoning over financial reports |
| **tatqa** | varies | TAT-QA dataset | Tabular + textual reasoning |
| **convfinqa** | varies | ConvFinQA dataset | Multi-turn numerical reasoning |

Each record is enriched via the TSR synthesis pipeline (Learner Profile, Tutoring Strategy, Synthetic Response) using multi-model random selection for diversity.

#### Task Categories (What Each Tests)

| Task Category | Count | Ability Tested | Eval Method | Why This Category Exists |
|---|---|---|---|---|
| **Conceptual Q&A** | 500 | Domain knowledge recall and explanation | GEval with domain rubric | Tests whether the model can explain "what is a Sharpe ratio?" correctly |
| **Strategy Explanation** | 300 | Multi-concept reasoning about quant strategies | GEval with strategy rubric | Tests whether the model understands how strategies work, not just definitions |
| **Code Generation** | 500 | Writing correct quant Python code | 60% code execution + 40% GEval code quality | Tests practical implementation ability, not just verbal knowledge |
| **Code Debugging** | 300 | Finding and fixing bugs in quant code | 60% code execution + 40% GEval code quality | Tests ability to identify subtle quant-specific bugs (lookahead bias, off-by-one) |
| **Data Interpretation** | 200 | Reading financial tables and computing values | GEval with data interpretation rubric | Tests numerical reasoning grounded in real financial data |
| **Multi-step Reasoning** | 200 | Chaining calculation steps with conversational context | GEval with multi-step rubric | Tests whether the model can carry context across follow-up questions |

**Key design decision:** Layer 1 tests only raw LLM ability. No tools, no conversation, no persona adaptation. This is intentional -- it serves as the **control baseline** for measuring what the agent framework adds beyond the bare model. If a full agent (Layer 2) doesn't score meaningfully higher than the bare LLM (Layer 1), the agent scaffolding isn't adding value.

### 3.2. Layer 2 Dataset (~500 multi-turn scenarios)

Layer 2 tasks are hand-crafted scenarios organized by quant workflow stage. Each task is paired with 3 student personas, creating ~120 evaluation instances from 41 base tasks.

#### Task Categories (Mapped to Quant Workflow)

| Category | Count | Quant Ability Tested | Tutor Ability Tested | Why This Category Exists |
|---|---|---|---|---|
| **Data** (D01-D06) | 6 | Data acquisition, cleaning, validation | Guide exploration, explain OHLCV, handle confusion | Tests the first thing any quant student encounters: "how do I get and understand data?" |
| **Strategy** (S01-S07) | 7 | Strategy design, conceptual reasoning | Explain concepts and trade-offs at the right level | Tests whether the agent can teach strategy concepts (MA crossover, pairs trading) not just describe them |
| **Implementation** (I01-I06) | 6 | Writing correct Python code | Guide student to write code, not dump it for them | Tests scaffolded code teaching: hints before answers, explaining why not just what |
| **Backtest** (B01-B05) | 5 | Running and analyzing backtests | Help interpret results, be honest about overfitting | Tests whether the agent can ground teaching in real data and results |
| **Debug** (X01-X06) | 6 | Finding and fixing quant-specific bugs | Guide student to find bugs themselves | Tests Socratic debugging: leading questions vs. just pointing at the bug |
| **End-to-End** (E01-E05) | 5 | Full multi-step quant workflow | Scaffold an entire learning journey | Tests sustained tutoring across many turns with shifting context |
| **Adversarial** (A01-A06) | 6 | Varies | Safety boundaries, empathy, intellectual honesty | Tests edge cases: "should I invest my savings?", "just give me the code", "my Sharpe of 5.0 is great!" |

#### Difficulty Distribution

| Level | Count | Design Target | Rationale |
|---|---|---|---|
| Easy | 9 | Best agent scores ~85% | Establishes that all agents can handle basics; saturates quickly |
| Medium | 14 | Best agent scores ~60% | Multi-step reasoning, adaptive scaffolding needed |
| Hard | 18 | Best agent scores ~40% | Intentionally heavy -- adversarial students, subtle methodology bugs, multi-asset workflows; prevents ceiling effects as models improve |

#### Student Personas (Why 3?)

Each Layer 2 task is run with 3 personas. The quant evaluation is identical (same correct answer), but the **tutoring rubric adapts**:

| Persona | Knowledge Level | What Good Teaching Looks Like | What Bad Teaching Looks Like |
|---|---|---|---|
| `beginner_no_finance` | Knows basic Python, no finance knowledge, anxious about math | Simple language, analogies, step-by-step, define all terms | Jargon dumping, skipping steps, assuming knowledge |
| `intermediate_developer` | Strong Python/pandas, some finance exposure | Skip basics, focus on implementation, challenge assumptions | Over-explaining, being patronizing |
| `advanced_quant` | Expert in both code and finance | Precise terminology, discuss edge cases, debate trade-offs | Hand-holding, excessive scaffolding |

**Critical design:** The agent does NOT receive the persona definition. It must **detect the student's level through conversation** -- like a real tutor meeting a new student. This is what dimension D1 (Level Detection) measures.

---

## 4. Environment Design Decisions

### 4.1. Why a Sandbox Environment?

An LLM benchmark is "question in, text out." An agent benchmark must include a **live environment** because agents:
- **Observe** (read data, see errors, receive student messages)
- **Act** (call tools, execute code, fetch data)
- **Decide** next steps based on what changed

This means the evaluation cannot be "question -> answer -> grade." It must include tools, state changes, and multi-step workflows.

### 4.2. Per-Task Isolation

Each task gets its own isolated environment:

```
Docker Container (or local temp dir)
  /workspace/      (agent's working directory, read-write)
  /data/           (staged data files for THIS task only, read-only)
  /docs/           (staged reference docs for THIS task only, read-only)
  /student_code/   (pre-planted buggy code for debug tasks, read-only)
```

**Why staged directories?** Each task JSON specifies which data files and docs are available. The orchestrator creates temp directories with symlinks to only those files. This prevents cross-task data leakage and tests whether the agent can work with a constrained environment.

### 4.3. MCP Tools (What the Agent Can Do)

14 core tools are available, mapped to stages of the quant workflow:

| Tool | Workflow Stage | What It Does |
|---|---|---|
| `shell_exec` | Implementation, backtesting | Execute Python scripts in the sandbox |
| `file_write` / `file_read` / `file_list` | Workflow management | Workspace file operations |
| `fetch_market_data` | Data acquisition | Returns OHLCV from frozen CSVs (no live APIs) |
| `compute_indicator` | Strategy design | SMA, EMA, RSI, Bollinger, MACD computation |
| `run_backtest` | Backtesting | Execute backtest script, return structured metrics |
| `compute_statistics` | Statistical analysis | ADF test, cointegration, correlation matrix |
| `plot_chart` | Visualization | Execute matplotlib code, return image |
| `format_table` | Teaching | Format data as markdown table |
| `search_docs` | Information retrieval | Full-text search across reference docs |
| `compare_series` | Multi-strategy analysis | Compare return series on metrics |
| `get_environment_info` | Environment discovery | List available data, packages, workspace contents |

**Why frozen data?** All market data is pre-downloaded static CSV. No live API dependencies. This ensures reproducibility -- every benchmark run sees identical data. (ABC checklist T.6: "Freeze environments.")

### 4.4. Distractor Tools (Testing Tool Selection)

Each task randomly samples 5-10 distractor tools from a pool of 15. The agent should NOT call them:

| Distractor | Why It's a Trap |
|---|---|
| `fetch_live_price` | All data is static; tests if agent uses provided frozen data |
| `train_ml_model` | Overkill for non-ML tasks; tests for over-engineering |
| `submit_order` | This is a backtest, not live trading |
| `search_web` | No network access; tests environment awareness |
| `send_email` | Completely irrelevant to tutoring or quant |

The mix of "plausibly relevant" (portfolio optimization for a single-strategy task) and "obviously irrelevant" (send email) tests different levels of tool selection judgment. Inspired by MCP-Bench's 10-distractor design.

### 4.5. MCP Proxy Layer (How We Observe Everything)

All tool calls route through a transparent proxy that logs:
- Tool name, arguments, result, timestamp, duration, success/failure, turn index

This is the key to evaluating **closed-source** agents: we don't need to see the agent's internal reasoning. We control the environment, so every action is observable. Same pattern as TAU-bench (controls API layer), MCP-Bench (controls MCP servers), and SWE-bench (controls Docker).

### 4.6. Three-LLM Architecture (Information Boundaries)

| | Student Simulator | Agent Under Test | Judge |
|---|---|---|---|
| **Sees** | Persona definition, scenario, learning objective | Student messages, tool schemas, tool results, docs | Full transcript, student persona, rubric |
| **Does NOT see** | Rubric, ground truth, agent internal state | Student persona, rubric, ground truth, eval scripts | Ground truth answers (only rubric criteria) |

The agent must **infer** the student's level from conversation. The judge evaluates **observable behavior** (what the agent said, what tools it called), not internal reasoning. This makes the benchmark valid for any agent architecture, open or closed source.

---

## 5. Ability Testing: What Each Evaluation Component Measures

### 5.1. The 2x2 Condition Matrix

Before discussing specific metrics, the benchmark runs each agent under 4 conditions to isolate what matters:

| | Tools ON | Tools OFF |
|---|---|---|
| **Tutor prompt** | `agent` (full evaluation) | `pure_llm` (tutor prompt, no tools) |
| **Baseline prompt** | `baseline` (tools, but "dump the answer" prompt) | `pure_llm_baseline` (bare model) |

This answers:
- **Does the tutor prompt help?** Compare `agent` vs `baseline` (same tools, different prompt)
- **Do tools help?** Compare `agent` vs `pure_llm` (same prompt, different tools)
- **Does the full agent add value over a bare LLM?** Compare `agent` vs `pure_llm_baseline`

### 5.2. Layer 1 Abilities (Single-Turn, Pure LLM)

Layer 1 tests **6 abilities** via single-turn evaluation, each with a category-specific GEval rubric:

| Ability | Test Method | What a High Score Means | What a Low Score Means |
|---|---|---|---|
| **Domain Knowledge** (Conceptual Q&A) | GEval compares to reference answer | Model correctly explains financial concepts | Model hallucinates or gives superficial answers |
| **Strategic Reasoning** (Strategy Explanation) | GEval with strategy rubric | Model understands how quant strategies work end-to-end | Model can define terms but can't connect them |
| **Code Writing** (Code Generation) | Execute extracted Python + GEval | Model writes correct, clean quant code | Code has bugs, uses wrong libraries, or doesn't run |
| **Bug Finding** (Code Debugging) | Execute fixed code + GEval | Model identifies the root cause and fixes it | Model patches symptoms or introduces new bugs |
| **Data Reasoning** (Data Interpretation) | GEval with data rubric | Model correctly reads tables and computes values | Model misreads data or makes calculation errors |
| **Multi-step Reasoning** | GEval with multi-step rubric | Model chains reasoning steps correctly across context | Model loses context or makes logical jumps |

### 5.3. Layer 2 Abilities (Multi-Turn, Full Agent)

Layer 2 evaluates along **three axes**, each testing different abilities:

#### Axis A: Quant Result (Did the Agent Solve the Problem?)

Per-task custom eval scripts check the agent's actual output:

| What It Checks | Example |
|---|---|
| Code correctness | Strategy code in workspace produces valid output |
| Numerical accuracy | Sharpe ratio is within expected range |
| Strategy validity | MA crossover actually uses two different window sizes |
| Task completion | Expected output files exist and are non-empty |
| Bug identification | For debug tasks: the specific bug was identified and fixed |

These are **deterministic, code-based checks** -- no LLM judge involved. The agent's code is executed in the sandbox and results are verified programmatically.

#### Axis B: Quant Process (Did the Agent Work Correctly?)

Two sub-components, averaged 50/50:

**Manual tool precision/recall:**
- Precision = correct tools called / total tools called (penalizes distractor calls)
- Recall = correct tools called / expected tools (penalizes missing steps)
- Capability completion = did tool outputs satisfy each required capability?

**DeepEval process metrics (7 metrics):**

| Metric | Ability Tested |
|---|---|
| `ToolCorrectnessMetric` | Did the agent select the right tools? |
| `ArgumentCorrectnessMetric` | Were tool arguments valid and well-formed? |
| `StepEfficiencyMetric` | Did the agent solve the problem efficiently (not too many steps)? |
| `MultiTurnMCPMetric` | Was tool usage contextually appropriate at each conversation turn? |
| `RoleAdherenceMetric` | Did the agent stay in the "tutor" role throughout? |
| `KnowledgeRetentionMetric` | Did the agent remember information from earlier in the conversation? |
| `TopicAdherenceMetric` | Did the agent stay focused on quant finance? |

#### Axis C: Tutor Quality (Did the Agent Teach Well?)

This is what makes QuantTutorBench unique. 7 dimensions, each scored 1-10 by DeepEval `ConversationalGEval`, with persona-aware rubrics. The judge runs **3 times with shuffled dimension order** and scores are averaged for stability.

| Dimension | Ability Tested | Why It Needs Multi-Turn | Example of High Score | Example of Low Score |
|---|---|---|---|---|
| **D1: Level Detection** | Infer the student's knowledge level from conversation | Requires observing student responses over turns | "I notice you mentioned Python but asked what OHLCV means -- let me start with the data basics" | Treats every student identically |
| **D2: Language Adaptation** | Adjust vocabulary and complexity to match the student | Requires sensing if the student understood previous explanations | Defines "Sharpe ratio" with a batting average analogy for a beginner; uses "risk-adjusted return" directly with an expert | Jargon with beginners, over-explaining with experts |
| **D3: Scaffolding Calibration** | Provide the right amount of help -- hints, not answers | Requires withholding information strategically across turns | Gives the beginner step-by-step guidance to discover the answer; gives the expert a direct peer discussion | Dumps the full solution immediately regardless of level |
| **D4: Domain Accuracy** | Financial content is factually correct | Testable in L1 too, but here under conversational pressure | Correctly explains annualized Sharpe, mentions caveats about assumption of normality | Confuses Sharpe and Sortino, or gives wrong formula |
| **D5: Code Teaching** | Teach code pedagogically, not just generate it | Requires back-and-forth about code, not just dumping | Guides student through the code line by line, explains WHY each step matters | Dumps 50 lines of uncommented code |
| **D6: Empathetic Response** | Handle student emotions: confusion, frustration, excitement | Requires the student to EXPRESS an emotion first | "I can see this is frustrating -- many students find time series tricky at first" | Ignores "this is too hard" and continues with formulas |
| **D7: Safety Boundaries** | Refuse harmful advice, add disclaimers, promote independence | Tested via adversarial tasks where the student pushes for advice | Deflects "should I invest my savings?" into a teaching moment about decision frameworks | Gives direct investment advice |

---

## 6. Scoring Architecture

### 6.1. Per-Task Score

```
Task Score = 0.70 x Quant Score + 0.30 x Tutor Score

  Quant Score = 0.50 x Result (eval scripts) + 0.50 x Process (tool metrics + DeepEval)
  Tutor Score = mean(D1, D2, D3, D4, D5, D6, D7)
```

The 70/30 split reflects that a quant tutor must first be **correct** before it can be pedagogically effective. A charming teacher who confidently explains wrong strategies is worse than a dry one who gets it right.

### 6.2. Cross-Layer Blending

When both layers are evaluated, the Result sub-score blends them:

```
Result Sub-score = 0.40 x Layer1_mean + 0.60 x Layer2_Result
```

Layer 2 gets more weight (0.60) because it tests the agent solving problems interactively with tools, which is harder and more realistic than single-turn Q&A.

### 6.3. Benchmark-Level KPIs

| KPI | What It Measures | How It's Computed |
|---|---|---|
| **OAS** (Overall Agent Score) | Single headline number | Weighted mean of all task scores |
| **QAI** (Quant Agent Index) | Domain expertise axis | Mean of quant scores across tasks |
| **TEI** (Tutoring Effectiveness Index) | Teaching quality axis | Mean of tutor rubric scores across tasks |
| **AS** (Adaptiveness Score) | Does the agent adapt to different students? | Per-task stdev of tutor scores across persona variants. Higher = agent adapts more |
| **TMS** (Tool Mastery Score) | Does the agent use tools correctly? | Mean of (precision x recall) per task |
| **pass@k** | Reliability: does it pass in k tries? | Fraction of tasks scoring above 0.5 threshold |
| **pass^k** | Consistency: does it pass EVERY time? | 1.0 if all k trials pass, else 0.0 |
| **Difficulty Curve** | Does performance degrade on harder tasks? | Mean score by difficulty level (should be easy > medium > hard) |

---

## 7. Why Each Design Decision Was Made

| Decision | Rationale |
|---|---|
| **Two layers, not one** | Single-turn Q&A cannot test conversation management, persona adaptation, or scaffolding. Multi-turn is expensive, so we use L1 for statistical power and L2 for depth. |
| **70% quant / 30% tutor weighting** | A tutor must first be correct. Wrong-but-friendly is worse than right-but-dry. |
| **3 personas per task** | Tests adaptiveness -- same quant answer, different teaching approach. Minimum 3 levels (beginner/intermediate/advanced) to detect adaptation. |
| **Agent doesn't see persona** | Forces the agent to detect level through conversation, like a real tutor. This is the entire point of D1. |
| **Frozen static data, no live APIs** | Reproducibility. Every run sees identical data. (ABC checklist T.6) |
| **Docker sandbox isolation** | Agents execute code -- must sandbox. Eval scripts live outside the agent workspace to prevent tampering. (ABC checklist T.5) |
| **Distractor tools** | Tests tool selection judgment, not just tool calling ability. Inspired by MCP-Bench. |
| **3x shuffled judge runs** | LLM judges are sensitive to dimension ordering. Averaging across shuffled orders improves stability. |
| **Observable-behavior evaluation only** | Internal chain-of-thought may be unfaithful (Anthropic 2025, OpenAI 2025). We evaluate what the agent DOES, not what it claims to think. |
| **Custom eval scripts + DeepEval** | Quant correctness requires domain-specific checks (is the Sharpe in range?). Tutoring quality requires LLM-as-Judge. Neither alone is sufficient. |
| **2x2 condition matrix** | Isolates the contribution of tools and tutor prompt separately. Without this, we can't tell if the agent's value comes from its tools or its prompt. |
| **pass@k AND pass^k** | pass@k measures capability (did it ever succeed?). pass^k measures reliability (does it always succeed?). Both matter for a tutoring agent. |
| **Hard tasks skew heavy (18/41)** | Easy tasks will saturate quickly as models improve. Hard tasks (adversarial students, subtle methodology bugs, multi-asset workflows) prevent ceiling effects. |

---

## 8. Current Implementation Status

### Implemented

- Full Layer 1 runner with 6-category GEval evaluation
- Full Layer 2 orchestrator with 5-phase lifecycle (RESET -> INTERACT -> CAPTURE -> EVALUATE -> TEARDOWN)
- 4 agent adapters (Anthropic, OpenAI, Google, Generic/OpenRouter)
- MCP proxy layer with 14 core tools and 15 distractor tools
- 7D tutor rubric with persona-aware ConversationalGEval (3x shuffled)
- 7 DeepEval process metrics
- Per-task custom eval scripts (7 implemented)
- 3 student personas with behavioral rules
- Combined scoring pipeline (L1+L2 blending, all KPIs)
- CLI with `run`, `run-single`, `run-layer1`, `list-tasks`, `validate-tasks`, `test-e2e`
- 2x2 condition matrix (agent/baseline/pure_llm/pure_llm_baseline)

### Layer 1 Task Coverage

| Category | Target | Status |
|---|---|---|
| Conceptual Q&A | 500 | Partially covered (from FiQA, Money.SE, Reddit, SEC/CFPB/FINRA) |
| Data Interpretation | 200 | Partially covered (from FinQA, TAT-QA) |
| Multi-step Reasoning | 200 | Partially covered (from ConvFinQA) |
| Strategy Explanation | 300 | Not started |
| Code Generation | 500 | Not started |
| Code Debugging | 300 | Not started |

### Layer 2 Task Coverage

| Category | Target | Implemented |
|---|---|---|
| Data | 6 | 1 (D01) |
| Strategy | 7 | 1 (S01) |
| Implementation | 6 | 1 (I01) |
| Backtest | 5 | 1 (B01) |
| Debug | 6 | 1 (X01) |
| End-to-End | 5 | 1 (E01) |
| Adversarial | 6 | 1 (A01) |

### Key Gaps

1. **Code tasks (800 items, 40% of Layer 1)** -- Requires curated Python quant coding problems with unit test suites. Cannot be sourced from existing datasets.
2. **Strategy Explanation (300 items)** -- Needs dedicated curation or synthesis pipeline.
3. **Remaining 34 Layer 2 tasks** -- 7 implemented, 34 remaining. Each requires hand-crafted task JSON, eval script, and student openings per persona.
4. **IRT calibration** -- Item Response Theory parameters for the Durability and Efficiency evaluation dimensions.
5. **Human annotation** -- Target: 50+ tasks with 3 human raters to validate 7D rubric inter-annotator agreement.
