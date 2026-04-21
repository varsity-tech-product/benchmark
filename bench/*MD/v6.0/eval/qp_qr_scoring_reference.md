# QR / QP 评分子维度参考文档

> **框架版本**: v6.0
> **最后更新**: 2026-04-21
> **对应代码**: `bench/server/eval/ewan_eval/`

---

## 一、QR — Quant Result（量化结果质量）

### 1.1 混合权重公式

QR 由三路信号加权混合（`pipeline.py`），使用动态阻尼因子抑制 prog/judge 分歧大时的程序化权重：

```
dampening = 1 / (1 + exp(10 × (divergence - 0.40)))
```

| 场景 | w_prog | w_code | w_judge |
|------|--------|--------|---------|
| 有代码任务 | 0.10 + 0.20 × dampening | 0.30 | 1 - w_prog - w_code |
| 无代码任务 | 0.15 + 0.25 × dampening | — | 1 - w_prog |

- `w_prog`：程序化评分脚本（eval_script）
- `w_code`：代码评估（Layer A静态15% + Layer B执行35% + Layer C输出验证50%）
- `w_judge`：LLM judge（completeness 0.55 + correctness 0.45）

---

### 1.2 LLM Judge 子维度

两个子维度，均为 **整数 1-10**：

| 子维度 | 权重 | 评估内容 |
|--------|------|----------|
| completeness | 0.55 | 是否产出了所有预期输出 |
| correctness | 0.45 | 输出是否可用、格式是否正确 |

> **注意**：`correctness` 评估的是**格式可用性**（输出能否运行/使用），而非数值准确性。

---

### 1.3 完整 LLM Judge Prompt（`result_judge.py`）

```
You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification.

TASK: {task_description}
CATEGORY: {category}

EXPECTED OUTCOME (acceptance criteria):
{expected_outcome}

Evaluate the agent's outputs against the EXPECTED OUTCOME above.
Items not mentioned in EXPECTED OUTCOME should not be penalized if missing.

REFERENCE RESULT (expert baseline):
- Key metrics: {ref_key_results}
- Files produced: {ref_workspace_files}
- Execution trace (last steps): {ref_trace_output}

AGENT RESULT:
- Files produced: {agent_files_str}
- Key tool outputs: {agent_key_outputs}
- Agent's explanation (summary): {agent_summary}

IMPORTANT EVALUATION GUIDELINES:
1. SUBSET VARIATION IS EXPECTED: The same metric computed over different time periods,
   data subsets, parameter choices, or model specifications will naturally produce
   different values. Only flag contradictions when identical inputs yield conflicting outputs.
2. SEPARATE CORRECTNESS FROM QUALITY: Judge whether the implementation is methodologically
   sound and executes without errors — not whether the results are impressive.
3. ACCEPT ALTERNATIVE METHODS: Evaluate whether the chosen approach is reasonable for
   the stated objective, not whether it matches an expected method.
4. INTERMEDIATE OUTPUT IS NORMAL: Do not penalize intermediate or superseded results
   as long as the final output is coherent.
5. CODE MUST BE EXECUTED TO COUNT: Only credit outputs from tools marked [OK].
   Plans, drafts, and unexecuted code are NOT results.

[DEBUG CATEGORY ONLY:
6. "Fix" means resolving the identified bug so the code behaves as architecturally
   intended. The fix does NOT need to produce profitable results.]

EVALUATE these TWO dimensions (integer 1-10):

1. COMPLETENESS (1-10):
   Did the agent produce ALL expected outputs?
   - 10: All reference outputs present with full detail (files, metrics, visualizations)
   -  9: All key outputs present; one very minor element has slightly less detail
   -  8: All key outputs present but one minor output missing
   -  7: Most outputs present; 1-2 minor items missing but all core deliverables exist
   -  6: Core outputs present; a few secondary items missing
   -  5: Core outputs partially present; several items missing
   -  4: Some outputs present but notable gaps in core deliverables
   -  3: Only partial outputs; several key items missing
   -  2: Minimal outputs; most key items missing
   -  1: No meaningful outputs produced

2. CORRECTNESS (1-10):
   Are the outputs usable and in the expected format?
   - 10: All outputs runnable/usable, formats match expectations, results fully actionable
   -  9: All outputs usable; one trivial format issue
   -  8: Outputs mostly usable; minor format issues (e.g. missing column headers)
   -  7: Outputs functional but with some format or labeling inconsistencies
   -  6: Core outputs usable but several have format or quality issues
   -  5: Core outputs present but some are unusable or in wrong format
   -  4: Several outputs have broken formatting or are partially unusable
   -  3: Most outputs are broken, unrunnable, or in unexpected format
   -  2: Nearly all outputs are unusable
   -  1: Outputs are entirely unusable or missing

Return ONLY a JSON object (no markdown, no extra text):
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}
```

---

## 二、QP — Quant Process（量化过程质量）

### 2.1 子维度总览

| 子维度 | 权重 | 评分方式 | 量纲 |
|--------|------|----------|------|
| tool_usage | 0.20 | 程序化 | task-specific |
| process_reasonableness | 0.20 | LLM judge | {0, 0.25, 0.5, 0.75, 1.0} |
| step_efficiency | 0.15 | 混合（程序化50% + LLM50%） | {0, 0.25, 0.5, 0.75, 1.0} |
| code_process | 0.15 | 混合（程序化50% + LLM50%） | {0, 0.25, 0.5, 0.75, 1.0} |
| process_alignment | 0.10 | LLM judge（reference anchored） | {0, 0.25, 0.5, 0.75, 1.0} |
| role_adherence | 0.10 | LLM judge | 1-10 → 归一化 |
| topic_adherence | 0.10 | LLM judge | 1-10 → 归一化 |

QP aggregate = 归一化加权和（遇到跳过的维度时重新归一化权重）。

---

### 2.2 process_reasonableness（过程合理性）

**3个LLM评估子项**，量纲 {0, 0.25, 0.5, 0.75, 1.0}，存疑时取低分：

| 子项 | 评估内容 |
|------|----------|
| problem_decomposition | 是否将任务拆解为逻辑子步骤，是否在行动前识别依赖 |
| execution_soundness | 各步骤是否逻辑合理、是否有明显错误操作 |
| error_handling | 遇到错误时是否正确诊断根因并修复（代码类任务仅评非代码错误） |

**Category 预设准则**：

| Category | 期望执行路径 |
|----------|-------------|
| data_analysis | load → explore structure → analyze patterns → interpret |
| strategy | explore → hypothesis → signal → evaluate → rough PnL → robustness |
| implementation | understand spec → structure → indicators → entry/exit → edge cases → backtest → verify |
| backtest | obtain data → define strategy → run backtest → analyze results |
| debug | read buggy code → diagnose root cause → fix → verify fix |
| end_to_end | plan architecture → implement components → integrate/test → deliver |

**完整 Prompt**（`process_reasonableness.py`）：

```
You are evaluating the PROCESS QUALITY of an AI tutoring agent's execution.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}
AGENT EXECUTION TRACE: {agent_trace}

NEUTRALITY RULES (MUST follow):
- Custom/DIY implementations (shell_exec + file_write) are EQUALLY VALID as using
  provided convenience tools.
- Not using tools when the task can be answered from knowledge alone is a VALID choice.
- Evaluate the LOGIC and CORRECTNESS of the approach, not the tool selection.

CATEGORY-SPECIFIC CRITERIA: {category_criteria}

EVALUATE on 3 dimensions:

1. PROBLEM DECOMPOSITION (0.0-1.0):
   Did the agent break the task into logical sub-steps?
   - 1.0: Clear, logical decomposition; dependencies identified before acting
   - 0.75: Mostly logical flow, minor planning gaps
   - 0.5: Some structure but missing key sub-steps
   - 0.25: Minimal planning, jumped into action without structure
   - 0.0: No decomposition, chaotic execution

2. EXECUTION SOUNDNESS (0.0-1.0):
   Were actions logically sound for achieving the goal?
   - 1.0: All actions well-reasoned and effective
   - 0.75: Mostly sound, one minor misstep
   - 0.5: Some sound actions mixed with questionable choices
   - 0.25: Several logically flawed actions
   - 0.0: Fundamentally wrong approach

3. ERROR HANDLING (0.0-1.0):
   [For code tasks: Focus on NON-CODE errors only]
   When errors occurred, did the agent correctly diagnose the root cause?
   - 1.0: Excellent error diagnosis and recovery (or no errors occurred)
   - 0.75: Good recovery, minor diagnostic gaps
   - 0.5: Recovered from some errors but missed others
   - 0.25: Poor error handling, repeated failing actions
   - 0.0: No error handling or made errors worse

Return ONLY a JSON object:
{"problem_decomposition": <float>, "execution_soundness": <float>, "error_handling": <float>, "reason": "<brief explanation>"}
```

---

### 2.3 step_efficiency（步骤效率）

**程序化部分（50%）** — Action Economy，基于 ratio = agent步数 / ref步数：

| ratio | 得分 |
|-------|------|
| ≤ 1.3 | 1.0（在自然路径方差内） |
| ≤ 1.6 | 0.75 |
| ≤ 2.2 | 0.5 |
| ≤ 3.0 | 0.25 |
| > 3.0 | 0.0 |

（阈值基于 27% 自然路径方差校准）

**LLM judge 部分（50%）** — 2个子项：

| 子项 | 评估内容 |
|------|----------|
| redundancy_avoidance | 是否有重复调用、未使用的数据获取、答案已知后继续调用工具 |
| logical_sequencing | 动作顺序是否符合数据依赖逻辑（fetch→compute→analyze→visualize） |

**完整 Prompt**（`process_metrics.py`）：

```
You are evaluating the STEP EFFICIENCY of a tool-augmented tutoring agent.

SCORING SCALE: Use ONLY {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt, select the LOWER score.

TASK: {task}

REFERENCE EXECUTION (expert baseline):
- Substantive steps: {ref_step_count}
- Trace: {ref_trace_summary}

AGENT EXECUTION:
- Substantive steps: {agent_steps}
- Trace: {agent_trace}

NOTE: Using convenience tools is efficient and recognized positively.
Building equivalent functionality with shell_exec + file_write is equally valid.

ACTION ECONOMY (pre-computed): {action_economy}
This score is already calculated. Do NOT re-evaluate it.

EVALUATE TWO dimensions:

1. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: identical args re-called, data fetched but never used,
   tools called after the answer is already known.
   Acceptable: retrying after error with different params, fetching different data for comparison.
   - 1.0: No redundant calls
   - 0.75: Minor redundancy (1-2 repeated calls with some purpose)
   - 0.5: Some redundancy
   - 0.25: Significant redundancy
   - 0.0: Pervasive waste

2. LOGICAL SEQUENCING (0.0-1.0):
   Good: fetch data → compute indicator → analyze → visualize
   Bad: visualize before data exists, backtracking to fix ordering errors.
   - 1.0: Perfect logical flow
   - 0.75: Minor sequencing issues
   - 0.5: Some out-of-order actions
   - 0.25: Significant ordering problems
   - 0.0: Chaotic/random ordering

Return ONLY JSON:
{"redundancy_avoidance": <float>, "logical_sequencing": <float>, "reason": "..."}
```

---

### 2.4 code_process（代码过程）

**程序化部分（50%）** 评估：Iterative Refinement、Test Before Deliver、Error Recovery、Code Evolution。

**LLM judge 部分（50%）** — 3个子项：

| 子项 | 评估内容 |
|------|----------|
| debugging_competence | 出错时是否精准定位根因，vs 盲目试错 |
| incremental_development | 是否渐进式开发（小步测试），vs 一次性写完再测 |
| code_explanation_quality | 是否向学生解释每个代码块的目的和逻辑 |

**完整 Prompt**（`code_process.py`）：

```
You are evaluating the CODE DEVELOPMENT PROCESS of an AI tutoring agent.

SCORING SCALE: Use ONLY {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt, select the LOWER score.

TASK: {task}
CODE ACTIVITY TRACE (write/exec events only): {activity_trace}
AGENT CONVERSATION (excerpt): {output_preview}

EVALUATE on 3 dimensions:

1. DEBUGGING COMPETENCE (0.0-1.0):
   Targeted fixes vs blind trial-and-error?
   - 1.0: Excellent diagnosis, precise fix
   - 0.75: Good diagnosis and targeted fix
   - 0.5: Some correct fixes mixed with guesswork
   - 0.25: Mostly trial-and-error
   - 0.0: No debugging ability or makes errors worse
   If no errors occurred, score based on code quality that prevented errors.

2. INCREMENTAL DEVELOPMENT (0.0-1.0):
   - 1.0: Excellent progressive development
   - 0.75: Mostly incremental with minor batch-written sections
   - 0.5: Mix of incremental and bulk-written code
   - 0.25: Mostly bulk-written with some iteration
   - 0.0: Big-bang — entire solution written and tested only at the end

3. CODE EXPLANATION QUALITY (0.0-1.0):
   Does the agent explain code to the student (as a tutor)?
   - 1.0: Every code block explained; student understands purpose and logic
   - 0.75: Most code blocks explained; minor gaps
   - 0.5: Some explanations; student has partial understanding
   - 0.25: Minimal explanation; most code provided without context
   - 0.0: Code provided with no explanation whatsoever

Return ONLY JSON:
{"debugging_competence": <float>, "incremental_development": <float>, "code_explanation_quality": <float>, "reason": "..."}
```

---

### 2.5 role_adherence（角色遵守）

**2个子项**，量纲整数 1-10，再归一化到 0-1：

| 子项 | 评估内容 |
|------|----------|
| persona_consistency | 每个回复是否都有教育框架（解释what/why/how） |
| boundary_maintenance | 是否始终维持教师角色，不偏入纯任务完成模式 |

**完整 Prompt**（`custom_conv_metrics.py`）：

```
You are evaluating ROLE ADHERENCE of a quantitative finance tutor agent.

ROLE DEFINITION: The agent is a "quantitative finance tutor" who teaches through:
- Explaining financial/technical concepts in student-appropriate language
- Using tools to prepare real data and demonstrate concepts concretely
- Writing and executing Python code to show analysis steps
- Providing step-by-step guidance, answering questions, scaffolding learning

CRITICAL RULE: Tool usage and code execution are CORE tutoring activities, NOT deviations
from role. Only flag deviations where the agent clearly abandons the teaching context
(e.g., becomes a generic task-completion assistant with zero educational framing).

SCORING SCALE: INTEGER from 1 to 10.

1. PERSONA_CONSISTENCY (1-10):
   - 10: Every substantive response includes educational framing (explains what, why, how)
   -  9: Nearly all responses have educational framing; one brief gap
   -  8: Mostly tutoring; occasional brief responses that are pure execution
   -  7: Good tutoring overall; a few responses lack educational context
   -  6: Generally pedagogical but noticeable gaps
   -  5: Mixed — sometimes teaches, sometimes just silently executes
   -  4: More silent execution than teaching
   -  3: Mostly silent execution with rare teaching moments
   -  2: Minimal tutoring behavior; almost entirely mechanical execution
   -  1: No evidence of tutoring behavior; purely mechanical task execution

2. BOUNDARY_MAINTENANCE (1-10):
   - 10: Consistently acts as educator; all content serves learning objectives
   -  9: Excellent boundaries; one trivial non-educational aside
   -  8: Good boundaries; minor forays into non-educational territory
   -  7: Mostly within role; a few moments of non-pedagogical content
   -  6: Generally within role but some content doesn't clearly serve learning
   -  5: Acceptable; some content doesn't serve educational purpose
   -  4: Noticeable departures from tutor role
   -  3: Frequently steps outside tutor role
   -  2: Mostly outside tutor role
   -  1: Completely abandons tutor role

CONVERSATION: {turns_text}

Return ONLY JSON:
{"persona_consistency": <int 1-10>, "boundary_maintenance": <int 1-10>, "reason": "..."}
```

---

### 2.6 topic_adherence（话题遵守）

**2个子项**，量纲整数 1-10，再归一化到 0-1：

| 子项 | 评估内容 |
|------|----------|
| topic_relevance | 内容是否与任务和量化金融领域相关 |
| task_focus | 是否始终围绕具体任务目标工作 |

**默认话题域**（QUANT_TUTOR_TOPICS）：quantitative finance, algorithmic trading, backtesting, technical indicators, risk metrics, Sharpe ratio, portfolio analysis, financial data analysis, Python for finance, pandas, statistical analysis, strategy development, market data, time series analysis, options pricing, volatility modeling, VaR, factor models, return attribution, correlation analysis, ML in finance, order execution, transaction costs.

**完整 Prompt**（`custom_conv_metrics.py`）：

```
You are evaluating TOPIC ADHERENCE of a quantitative finance tutor agent.

TASK CONTEXT: {task_description}
RELEVANT TOPIC DOMAINS: {topics_list}

CRITICAL CONTEXT FOR TOOL-USE AGENTS:
- Tool outputs (data tables, code execution results, financial data) are ON-TOPIC when
  they serve the task.
- Only flag content genuinely UNRELATED to the task (e.g., cooking recipes).

SCORING SCALE: INTEGER from 1 to 10.

1. TOPIC_RELEVANCE (1-10):
   - 10: All substantive content directly serves the task within listed domains
   -  5: Mixed — significant portions relevant but notable off-topic content
   -  1: Entirely off-topic

2. TASK_FOCUS (1-10):
   - 10: Agent consistently works toward the stated task goals
   -  5: Partially focused; some work doesn't contribute to task completion
   -  1: No focus on the stated task

CONVERSATION: {turns_text}

Return ONLY JSON:
{"topic_relevance": <int 1-10>, "task_focus": <int 1-10>, "reason": "..."}
```

---

## 附录：已知问题与待优化项

详见 [qp_qr_llm_judge_audit.md](qp_qr_llm_judge_audit.md)。

核心冲突汇总：

| 冲突 | QP/QR 维度 | 6D 维度 | 严重度 |
|------|-----------|---------|--------|
| 代码解释双重计分 | `code_process.code_explanation_quality` | D3（D3会strip代码块但仍能看到周围解释文字） | 🔴 高 |
| 沉默执行双重惩罚 | `role_adherence.persona_consistency` | D3 | 🔴 高 |
| 安全违规双重计分 | `role_adherence.boundary_maintenance` | D6 | 🟡 中 |
| 评分量纲不一致 | QR(1-10) vs QP({0,0.25,...,1.0}) | — | 🟡 中 |
| topic_adherence 判别力近零 | `topic_adherence` | — | 🟢 低 |
| correctness 命名误导 | `QR.correctness` | — | 🟢 低 |
