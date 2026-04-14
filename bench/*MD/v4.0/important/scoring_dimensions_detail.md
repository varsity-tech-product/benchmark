# QR | QP 评分子维度详细报告

> 2026-04-09 | 基于代码调研的完整评分细则

---

## 一、QR（Quant Result）— 结果质量

### 1.1 总体结构

QR 由三个成分通过 **Divergence Dampening** 动态融合：

```
QR = w_prog × Programmatic + w_code × Code_Eval + w_judge × Result_Judge

权重随 divergence 动态调整：
  有 code_eval 时:
    标准:    prog 30% + code 30% + judge 40%   (dampening factor ≈ 1.0)
    全抑制:  prog 10% + code 30% + judge 60%   (dampening factor ≈ 0.0)

  无 code_eval 时:
    标准:    prog 40% + judge 60%              (dampening factor ≈ 1.0)
    全抑制:  prog 15% + judge 85%              (dampening factor ≈ 0.0)

  dampening_factor = 1 / (1 + exp(10 × (|prog - judge| - 0.40)))
```

特殊情况：
- `eval_script` 返回 `None`（信号不足）→ 完全 defer 给 LLM judge
- `requires_code = false` → code_eval 强制跳过（防止 ICC 不稳定）

---

### 1.2 成分 A：Programmatic Eval（test_scripts）

**性质**：100% 确定性，人工编写，每个任务专用

**每个任务有独立的 eval script**，输出 `_checklist`：

```python
_checklist = [
    {"item": "backtest_executed",       "weight": 0.20, "passed": True},
    {"item": "sharpe_present",          "weight": 0.20, "passed": True},
    {"item": "return_present",          "weight": 0.20, "passed": True},
    {"item": "drawdown_present",        "weight": 0.20, "passed": True},
    {"item": "interpretation_present",  "weight": 0.20, "passed": True},
]
score = sum(c["weight"] for c in _checklist if c["passed"])
```

**check item 的判定方式**（纯程序化，无 LLM）：
- 关键词 + 正则匹配（"sharpe" 后跟数字）
- 工具调用日志检查（是否调了 `run_backtest`）
- 文件存在性检查（workspace 中是否有特定文件）
- 数值范围检查（某指标是否在合理范围内）

**data_source_cap**：如果任务指定了 `data_files`，检查 agent 是否使用了正确的数据源。未使用 → `score *= max(0.25, fraction)`

---

### 1.3 成分 B：Code Eval（code_eval.py）

**性质**：100% 确定性，三层分析

| Layer | 权重 | 评估内容 | 方法 |
|-------|------|---------|------|
| **A — Static Analysis** | 15% | .py 文件语法正确性、危险模式检测 | AST 解析，无 LLM |
| **B — Execution Result** | 35% | shell_exec 的执行成功率 | 解析 tool_logs 中 shell_exec 的 exit code 和 stderr |
| **C — Output Verification** | 50% | 数值输出与 reference 对比 | 数学比对 key_results，无 reference 时 hard zero (0.0) |

Layer B 细节：
- 取每个脚本的**最后一次**执行结果（反映迭代调试后的最终状态）
- `success_rate = successful_scripts / total_scripts`
- 未测试的脚本（写了但没跑）列入 `untested` 列表

Layer C 细节：
- 比较 agent workspace 中的 key_results 与 reference 的 key_results
- 数值容差：相对误差 < 5% 算正确
- 无 reference 时不跳过，直接 score=0.0（hard zero 设计）

**LEAN 任务特殊路径**（`_evaluate_lean_code`）：
- Layer A：编译状态（compile_error → 0.0, success → 1.0）
- Layer B：执行状态映射（compile_error=0.0, runtime_error=0.1, empty_trades=0.5, success=1.0）
- Layer C：同 Python 路径

---

### 1.4 成分 C：LLM Result Judge（result_judge.py）

**性质**：LLM 评估，10 档整数评分（1-10）

**两个子维度**：

| 子维度 | 权重 | 含义 | 评分标准 |
|--------|------|------|---------|
| **completeness** | 0.55 | 是否产出了所有期望的输出 | 10=全部产出含细节，5=核心产出但有缺失，1=几乎无产出 |
| **correctness** | 0.45 | 输出是否可用且格式正确 | 10=全部可运行/可用，5=核心可用但部分格式有问题，1=全部不可用 |

**Result Judge 得到的输入**：
- 任务描述 + 类别
- expected_outcome（如有）
- reference 的 key_results、workspace_files、trace_summary（如有）
- Agent 的 workspace_files、key_tool_outputs、summary

**6 条评估 guidelines**（纠偏系统性误判）：
1. 子集变异是正常的（不同时间段算出不同值不是矛盾）
2. 区分正确性和质量（方法正确但收益差不扣分）
3. 接受替代方法
4. 中间输出是正常的
5. 未执行的代码不算数（[FAIL] 的工具输出不得分）
6. Debug 任务：修 bug ≠ 策略盈利

**有/无 reference 两套 rubric**：有 reference 时与 reference 对比，无 reference 时按任务要求独立评判。

---

## 二、QP（Quant Process）— 行为质量

### 2.1 总体结构

```
QP = weighted_avg(7 dimensions, renormalized if any dim is None/skipped)
```

| 维度 | 权重 | 评分方式 | 评分粒度 |
|------|------|---------|---------|
| tool_usage | 0.20 | **纯数学**（无 LLM） | 连续 [0,1] |
| process_reasonableness | 0.20 | **LLM judged** | 5 档（0.0/0.25/0.5/0.75/1.0） |
| step_efficiency | 0.15 | **混合**（程序化 + LLM） | 混合 |
| code_process | 0.15 | **混合**（程序化 + LLM） | 混合 |
| process_alignment | 0.10 | **LLM judged** | 5 档 |
| role_adherence | 0.10 | **LLM judged** | 10 档（1-10 整数） |
| topic_adherence | 0.10 | **LLM judged** | 10 档（1-10 整数） |

**renormalization**：如果某维度返回 `score=None`（如无代码活动时 code_process 不适用），从聚合中排除并重新归一化剩余权重。

---

### 2.2 tool_usage（0.20）— 纯数学

**公式**：
```
score = 0.60 × selection_score + 0.40 × effectiveness

selection_score = clamp(base + bonus - penalties, 0, 1)
  base = 0.8（有 convenient tools 时）或 1.0（无 convenient tools 时）
  bonus = 0.2 × (called_convenient / total_convenient)  per convenient tool used
  penalty_expected = 0.15 × count(missing expected tools)
  penalty_distractor = 0.10 × count(called distractor tools)

effectiveness = effective_expected_calls / total_expected_tools
  effective = tool call 返回了有效结果（非 Error、非 Traceback、非空）
```

**输入**：tool_logs + expected_mcp_tools + convenient_tools + distractor_names

**完全确定性**——相同 tool_logs 永远产出相同分数。

---

### 2.3 step_efficiency（0.15）— 混合

**三个子维度**：

| 子维度 | 权重 | 评分方式 |
|--------|------|---------|
| **action_economy** | 0.40 | 有 reference → 程序化（步数比阈值）；无 reference → **hard zero (0.0)** |
| **redundancy_avoidance** | 0.30 | LLM judged，5 档 |
| **logical_sequencing** | 0.30 | LLM judged，5 档 |

**action_economy 阈值**（有 reference 时）：
```
ratio = agent_steps / reference_steps
ratio ≤ 1.3  → 1.0   (在自然方差范围内)
ratio ≤ 1.6  → 0.75  (略多)
ratio ≤ 2.2  → 0.5   (明显偏多)
ratio ≤ 3.0  → 0.25  (显著偏多)
ratio > 3.0  → 0.0   (过度冗余)
```

**LLM 评估的输入**：agent 的工具调用 trace（name + args + result 摘要）+ reference trace（如有）

**LLM prompt 要求**：只能输出 {0.0, 0.25, 0.5, 0.75, 1.0}，代码中 clamp 到最近的 5 档值。

---

### 2.4 process_reasonableness（0.20）— LLM judged

**三个子维度**（全部 LLM judged，5 档 clamp）：

| 子维度 | 权重 | 含义 |
|--------|------|------|
| **problem_decomposition** | 0.30 | agent 是否把任务分解为合理的子步骤 |
| **execution_soundness** | 0.40 | 工具调用序列是否在方法论上合理 |
| **error_handling** | 0.30 | 遇到错误时的恢复策略是否合理 |

**评估输入**：任务描述 + 类别 + 工具调用 trace

**类别容差**（`_ALIGNMENT_TOLERANCE`）：
```
data_analysis: 0.8  (分析路径相对固定)
strategy: 0.7       (策略有多种合理路径)
debug: 0.6
implementation: 0.5 (实现方式差异最大)
backtest: 0.5
```

---

### 2.5 code_process（0.15）— 混合（50% 程序化 + 50% LLM）

**前提**：仅在检测到代码活动时评估（file_write .py/.cs 或 code shell_exec）。无代码活动 → `score=None`，从 QP 聚合中排除。

**程序化子维度**（各 1/4 权重，从 tool_logs 计算）：

| 子维度 | 含义 | 判定方式 |
|--------|------|---------|
| **iterative_refinement** | 写代码后是否测试 | file_write(.py) 后是否有对应 shell_exec；全测=1.0，全不测=0.0 |
| **test_before_deliver** | 最终代码是否验证通过 | 最后一次代码执行是否 success；无执行=None |
| **error_recovery** | 失败后是否修复成功 | 有失败的脚本中，后续是否有成功执行；recovered/failed |
| **code_evolution** | 多次改写是否有实质变化 | 同一文件多次 file_write 时内容差异 >5%；写一次=1.0 |

**LLM 子维度**（各 1/3 权重，5 档 clamp）：

| 子维度 | 含义 |
|--------|------|
| **debugging_competence** | 面对错误时的诊断和修复能力 |
| **incremental_development** | 是否渐进式开发（vs 一次写完大文件） |
| **code_explanation_quality** | 代码解释给学生的质量 |

**Combined**：`score = 0.5 × programmatic_avg + 0.5 × llm_avg`

---

### 2.6 process_alignment（0.10）— LLM judged

**前提**：有 reference trace 时评估。无 reference → hard zero (0.0)，但**不排除**出 QP 聚合（计入权重）。

**三个子维度**（全部 LLM judged，5 档 clamp）：

| 子维度 | 权重 | 含义 |
|--------|------|------|
| **coverage** | 0.40 | agent 的步骤覆盖了 reference 的多少关键环节 |
| **depth** | 0.35 | 在覆盖的环节上是否达到了 reference 的深度 |
| **soundness_delta** | 0.25 | agent 的过程质量与 reference 的差距 |

---

### 2.7 role_adherence（0.10）— LLM judged

**两个子维度**（10 档整数评分，1-10）：

| 子维度 | 含义 | 评分标准 |
|--------|------|---------|
| **persona_consistency** | 是否维持 tutor 角色 | 10=每次回复都有教育框架；5=有时教有时只执行；1=纯机械执行 |
| **boundary_maintenance** | 是否在角色边界内 | 10=始终在 tutor 边界内；5=偶尔越界；1=完全脱离角色 |

**关键 prompt 指导**：工具使用和代码执行是 tutor 的核心活动，不是角色偏离。只在 agent 完全放弃教学语境时扣分。

**最终分数**：`(persona_consistency + boundary_maintenance) / 2`，归一化到 0-1

---

### 2.8 topic_adherence（0.10）— LLM judged

**两个子维度**（10 档整数评分，1-10）：

| 子维度 | 含义 | 评分标准 |
|--------|------|---------|
| **topic_relevance** | 对话内容是否与任务和领域相关 | 10=全部内容直接服务任务；5=一半相关一半跑题；1=完全跑题 |
| **task_focus** | 是否聚焦在任务目标上 | 10=始终朝任务目标推进；5=部分工作不贡献于任务；1=完全偏离任务 |

**关键 prompt 指导**：工具输出（数据表、代码执行结果）在服务任务时是 ON-TOPIC。环境检查（ls、版本查询）是中性动作不算跑题。只有真正无关的内容（讨论烹饪等）才算跑题。

**输入**：对话内容 + 任务描述 + 24 个量化金融相关话题列表

**最终分数**：`(topic_relevance + task_focus) / 2`，归一化到 0-1

---

## 三、跨维度总览

### 3.1 评分方式分类

| 评分方式 | 维度/子维度 | 特点 |
|---------|-----------|------|
| **纯程序化（确定性 100%）** | Programmatic eval, Code eval (A/B/C), tool_usage, action_economy (有 ref), iterative_refinement, test_before_deliver, error_recovery, code_evolution | 相同输入 → 相同输出。跨 judge 完全一致 |
| **LLM 5 档（受限离散）** | step_efficiency (redundancy, sequencing), process_reasonableness (3 sub), code_process (3 sub), process_alignment (3 sub) | LLM 只能输出 {0.0, 0.25, 0.5, 0.75, 1.0}，代码 clamp 强制 |
| **LLM 10 档（整数离散）** | Result judge (completeness, correctness), role_adherence (2 sub), topic_adherence (2 sub) | LLM 输出 1-10 整数，/10 归一化 |

### 3.2 数据源依赖

| 数据源 | 采集方 | 使用者 |
|--------|-------|--------|
| **conversation** | simulation.py 自动记录 | Result judge, role_adherence, topic_adherence, Tutor 7D |
| **tool_logs** | MCP Proxy 自动拦截 | tool_usage, step_efficiency, process_reasonableness, code_process, process_alignment |
| **workspace_files** | Docker 容器扫描 | Code eval (Layer A/C), result judge |
| **reference** | reference_store 加载 | Code eval (Layer C), result judge, step_efficiency (action_economy), process_alignment |
| **task metadata** | task JSON | 所有维度（category, expected_outcome, expected_mcp_tools 等） |

### 3.3 QP 维度权重与实际 LLM 调用次数

当前配置（单模型 eval）：

| QP 维度 | LLM 调用数 | 说明 |
|---------|-----------|------|
| tool_usage | **0** | 纯数学 |
| step_efficiency | **1** | 1 次 LLM（action_economy 程序化） |
| process_reasonableness | **1** | 1 次 LLM |
| code_process | **1**（如适用） | 1 次 LLM（程序化部分无 LLM） |
| process_alignment | **1**（如适用） | 1 次 LLM |
| role_adherence | **1** | 1 次 LLM |
| topic_adherence | **1** | 1 次 LLM |
| **总计** | **5-6** | 全部并行执行 |


---

## 附录：LLM Judge 完整 Prompt 源码

> 以下为各 LLM judge 维度的完整 prompt 构建函数/模板源码，确保评分标准的完全透明和可审计。

### A.1 QR Result Judge

**Source**: `evaluation/deepeval_metrics/result_judge.py`

```python
def _build_result_judge_prompt(
    task_description: str,
    category: str,
    *,
    agent_key_outputs: str,
    agent_workspace_files: list[str],
    agent_summary: str,
    reference: dict | None,
    expected_outcome: str | None = None,
) -> str:
    """Build the result quality evaluation prompt."""

    header = """You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification."""

    task_section = f"""
TASK: {task_description}
CATEGORY: {category}"""

    if expected_outcome:
        task_section += f"""

EXPECTED OUTCOME (acceptance criteria):
{expected_outcome}

Evaluate the agent's outputs against the EXPECTED OUTCOME above.
Items not mentioned in EXPECTED OUTCOME should not be penalized if missing."""

    # Reference section
    ref_section = ""
    if reference:
        ref_key_results = reference.get("key_results", {})
        ref_workspace = reference.get("workspace_files", [])
        ref_trace = reference.get("trace_summary", [])
        ref_output = ""
        if isinstance(ref_trace, list):
            ref_output = "\n".join(
                f"  {i+1}. {s}" for i, s in enumerate(ref_trace[-8:])
            )
        else:
            ref_output = str(ref_trace)[:500]

        ref_section = f"""
REFERENCE RESULT (expert baseline):
- Key metrics: {_json.dumps(ref_key_results, indent=2, default=str)}
- Files produced: {', '.join(ref_workspace) if ref_workspace else '(none)'}
- Execution trace (last steps):
{ref_output}"""

    # Agent section
    agent_files_str = (
        ", ".join(agent_workspace_files) if agent_workspace_files else "(none)"
    )
    agent_section = f"""
AGENT RESULT:
- Files produced: {agent_files_str}
- Key tool outputs:
{agent_key_outputs}
- Agent's explanation (summary):
{agent_summary}"""

    # Evaluation guidelines (reduce systematic LLM mis-judgments)
    guidelines = """
IMPORTANT EVALUATION GUIDELINES:
1. SUBSET VARIATION IS EXPECTED: The same metric computed over different
   time periods, data subsets, parameter choices, or model specifications
   will naturally produce different values. This is NOT inconsistency.
   Only flag contradictions when identical inputs yield conflicting outputs.
2. SEPARATE CORRECTNESS FROM QUALITY: A correctly implemented analysis
   may produce unfavorable results (poor strategy returns, weak model fit,
   insignificant test statistics). Judge whether the implementation is
   methodologically sound and executes without errors — not whether the
   results are impressive or match expectations.
3. ACCEPT ALTERNATIVE METHODS: There are often multiple valid approaches
   to the same analytical task. If the agent uses a different method than
   what you might expect but arrives at defensible results, this is not
   an error. Evaluate whether the chosen approach is reasonable for the
   stated objective.
4. INTERMEDIATE OUTPUT IS NORMAL: Exploratory analysis, debugging output,
   and iterative refinement are part of a sound analytical workflow.
   Do not penalize intermediate or superseded results as long as the
   final output is coherent.
5. CODE MUST BE EXECUTED TO COUNT: Code that was written but never
   successfully executed (shown as [FAIL] in tool outputs, or absent from
   tool outputs entirely) should NOT receive credit for completeness or
   correctness. Plans, drafts, and unexecuted code are preparation — not
   results. Only credit outputs from tools marked [OK]. If the agent
   wrote multiple code iterations, only the executed versions matter.
"""
    # Debug-specific guideline: separate "fix works" from "strategy profits"
    if category == "debug":
        guidelines += """6. DEBUG TASKS: For debugging tasks, "fix" means resolving the identified
   bug so the code behaves as architecturally intended (e.g., trades execute
   instead of canceling, warm-up period is respected, correct order type is
   used). The fix does NOT need to produce profitable results — a correctly
   fixed strategy may still lose money due to market conditions. Judge
   whether the bug was resolved, not whether the strategy is profitable.
"""

    # Dimensions — numerical accuracy is handled separately by programmatic
    # code_eval (Layer C), so the judge focuses on completeness + correctness.
    if reference:
        dimensions = """
EVALUATE these TWO dimensions (integer 1-10):

1. COMPLETENESS (1-10):
   Did the agent produce ALL expected outputs compared to the reference?
   - 10: All reference outputs present with full detail (files, metrics, visualizations)
   -  9: All key outputs present; one very minor element has slightly less detail
   -  8: All key outputs present but one minor output missing (e.g. a secondary chart)
   -  7: Most outputs present; 1-2 minor items missing but all core deliverables exist
   -  6: Core outputs present; a few secondary items missing
   -  5: Core outputs partially present; several items missing
   -  4: Some outputs present but notable gaps in core deliverables
   -  3: Only partial outputs; several key items missing
   -  2: Minimal outputs; most key items missing
   -  1: No meaningful outputs produced

2. CORRECTNESS (1-10):
   Are the outputs usable and in the expected format?
   - 10: All outputs are runnable/usable, formats match expectations, results fully actionable
   -  9: All outputs usable; one trivial format issue (e.g. minor label mismatch)
   -  8: Outputs mostly usable; minor format issues (e.g. missing column headers)
   -  7: Outputs functional but with some format or labeling inconsistencies
   -  6: Core outputs usable but several have format or quality issues
   -  5: Core outputs present but some are unusable or in wrong format
   -  4: Several outputs have broken formatting or are partially unusable
   -  3: Most outputs are broken, unrunnable, or in unexpected format
   -  2: Nearly all outputs are unusable
   -  1: Outputs are entirely unusable or missing

Return ONLY a JSON object (no markdown, no extra text):
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}"""
    else:
        # No reference — evaluate on standalone merit
        dimensions = """
EVALUATE these TWO dimensions (no reference baseline available, integer 1-10):

1. COMPLETENESS (1-10):
   Given the task requirements, did the agent produce all expected outputs?
   - 10: Task fully addressed — all requested outputs present with full detail
   -  9: All key requirements met; one very minor element slightly abbreviated
   -  8: All key requirements met; one minor item missing
   -  7: Most requirements met; 1-2 minor items missing
   -  6: Core requirements met; a few secondary items missing
   -  5: Core requirements partially met; several items missing
   -  4: Some requirements addressed but notable gaps
   -  3: Only partial work completed; several key items missing
   -  2: Minimal work; most requirements unmet
   -  1: Task barely attempted

2. CORRECTNESS (1-10):
   Are the outputs usable and in the expected format?
   - 10: All outputs are runnable/usable, formats match expectations, results fully actionable
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
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}"""

    prompt = (
        header + task_section + ref_section + agent_section + guidelines + dimensions
    )

    # Safety cap: if prompt exceeds budget, trim agent_key_outputs
    _TOTAL_PROMPT_CAP = 40000
    if len(prompt) > _TOTAL_PROMPT_CAP:
        overshoot = len(prompt) - _TOTAL_PROMPT_CAP
        trimmed_outputs = agent_key_outputs[
            : max(200, len(agent_key_outputs) - overshoot)
        ]
        agent_section_trimmed = f"""
AGENT RESULT:
- Files produced: {agent_files_str}
- Key tool outputs (trimmed):
{trimmed_outputs}
- Agent's explanation (summary):
{agent_summary}"""
        prompt = (
            header
            + task_section
            + ref_section
            + agent_section_trimmed
            + guidelines
            + dimensions
        )

    return prompt

```

### A.2 QP Step Efficiency (LLM 部分)

**Source**: `evaluation/deepeval_metrics/process_metrics.py`

```python
def _build_step_efficiency_prompt(
    task: str,
    agent_trace: str,
    *,
    has_reference: bool,
    ref_step_count: int,
    ref_trace_summary: str,
    agent_steps: int,
    action_economy_precomputed: float | None,
) -> str:
    """Build the step efficiency evaluation prompt.

    When reference is available and Action Economy is pre-computed,
    the LLM only judges Redundancy Avoidance and Logical Sequencing.
    When no reference, the LLM judges all three dimensions.
    """
    header = """You are evaluating the STEP EFFICIENCY of a tool-augmented tutoring agent.

CONTEXT: This agent teaches quantitative finance using tools that fetch market data,
execute code, compute indicators, create charts, and run backtests. Tool calls that
serve teaching purposes (demonstrate with real data, verify code, compute metrics,
create visualizations) are pedagogically valuable.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score."""

    task_section = f"\nTASK: {task}"

    # Reference section (only when available)
    ref_section = ""
    if has_reference:
        ref_section = f"""
REFERENCE EXECUTION (expert baseline):
- Substantive steps: {ref_step_count}
- Trace:
{ref_trace_summary}"""

    agent_section = f"""
AGENT EXECUTION:
- Substantive steps: {agent_steps}
- Trace:
{agent_trace}"""

    tool_tier_note = """
NOTE ON TOOL TIERS:
The agent had access to convenience tools (compute_indicator, run_backtest, etc.) that
bundle multi-step operations into one call.
- Using convenience tools is efficient and should be recognized positively.
- Building equivalent functionality with shell_exec + file_write is equally valid.
  Judge the step count relative to the approach taken, not against the shortcut."""

    if action_economy_precomputed is not None:
        # Reference available — LLM judges 2 dimensions only
        ratio = agent_steps / ref_step_count if ref_step_count > 0 else 0
        dimensions = f"""
ACTION ECONOMY (pre-computed): {action_economy_precomputed} (ratio: {ratio:.2f})
This score is already calculated. Do NOT re-evaluate it.

Evaluate the following TWO dimensions:

1. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: Same tool called with identical arguments, fetching data never used,
   re-computing values already obtained, calling tools after the answer is known.
   Acceptable: Retrying after an error with different parameters, fetching different
   data for comparison, progressive refinement.
   - 1.0:  No redundant calls
   - 0.75: Minor redundancy (1-2 repeated calls but with some purpose)
   - 0.5:  Some redundancy (repeated calls or unused data fetches)
   - 0.25: Significant redundancy
   - 0.0:  Pervasive waste

2. LOGICAL SEQUENCING (0.0-1.0):
   Evaluate whether actions follow a logical data-dependency order.
   Good: fetch data → compute indicator → analyze → visualize
   Bad: visualize before data exists, compute before dependencies ready,
   backtracking to fix ordering errors.
   - 1.0:  Perfect logical flow
   - 0.75: Minor sequencing issues (one action slightly out of order)
   - 0.5:  Some out-of-order actions
   - 0.25: Significant ordering problems
   - 0.0:  Chaotic/random ordering

Return ONLY a JSON object (no markdown, no extra text):
{{"redundancy_avoidance": <float>, "logical_sequencing": <float>, "reason": "<brief explanation>"}}"""
    else:
        # No reference — LLM judges all 3 dimensions
        dimensions = """
Evaluate the following THREE dimensions:

1. ACTION ECONOMY (0.0-1.0):
   Given the task complexity, did the agent use a reasonable number of steps?
   - 1.0:  Minimal steps, every call essential
   - 0.75: Mostly efficient, 1-2 extra calls
   - 0.5:  Moderate excess steps
   - 0.25: Many unnecessary steps
   - 0.0:  Excessively verbose

2. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: Same tool called with identical arguments, fetching data never used,
   re-computing values already obtained, calling tools after the answer is known.
   Acceptable: Retrying after an error with different parameters, fetching different
   data for comparison, progressive refinement.
   - 1.0:  No redundant calls
   - 0.75: Minor redundancy (1-2 repeated calls but with some purpose)
   - 0.5:  Some redundancy (repeated calls or unused data fetches)
   - 0.25: Significant redundancy
   - 0.0:  Pervasive waste

3. LOGICAL SEQUENCING (0.0-1.0):
   Evaluate whether actions follow a logical data-dependency order.
   Good: fetch data → compute indicator → analyze → visualize
   Bad: visualize before data exists, compute before dependencies ready,
   backtracking to fix ordering errors.
   - 1.0:  Perfect logical flow
   - 0.75: Minor sequencing issues (one action slightly out of order)
   - 0.5:  Some out-of-order actions
   - 0.25: Significant ordering problems
   - 0.0:  Chaotic/random ordering

Return ONLY a JSON object (no markdown, no extra text):
{{"action_economy": <float>, "redundancy_avoidance": <float>, "logical_sequencing": <float>, "reason": "<brief explanation>"}}"""

    return (
        header
        + task_section
        + ref_section
        + agent_section
        + tool_tier_note
        + dimensions
    )

```

### A.3 QP Process Reasonableness

**Source**: `evaluation/deepeval_metrics/process_reasonableness.py`

```python
def _build_process_reasonableness_prompt(
    task: str,
    category: str,
    agent_trace: str,
    *,
    is_code_task: bool = False,
) -> str:
    """Build the process reasonableness evaluation prompt."""
    category_criteria = CATEGORY_PROCESS_CRITERIA.get(
        category, CATEGORY_PROCESS_CRITERIA["conceptual_qa"]
    )

    # For code tasks, narrow Error Handling to non-code errors only
    # (code-specific debugging is evaluated separately by Code Process).
    if is_code_task:
        error_handling_desc = """3. ERROR HANDLING (0.0-1.0):
   Focus on NON-CODE error handling: wrong data paths, missing files,
   tool call failures, invalid parameters, data format issues.
   Code-specific debugging (Python tracebacks, syntax errors, logic bugs)
   is evaluated separately — do NOT consider those here.
   - 1.0:  Excellent handling of non-code errors (or no such errors occurred)
   - 0.75: Good recovery from most non-code issues
   - 0.5:  Recovered from some non-code errors but missed others
   - 0.25: Poor handling of non-code failures
   - 0.0:  No error handling or made non-code errors worse"""
    else:
        error_handling_desc = """3. ERROR HANDLING (0.0-1.0):
   When errors occurred, did the agent correctly diagnose the root cause?
   Did it fix the actual problem rather than suppressing symptoms?
   Did it avoid repeating the same failing action?
   - 1.0:  Excellent error diagnosis and recovery (or no errors occurred)
   - 0.75: Good recovery, minor diagnostic gaps
   - 0.5:  Recovered from some errors but missed others
   - 0.25: Poor error handling, repeated failing actions
   - 0.0:  No error handling or made errors worse"""

    return f"""You are evaluating the PROCESS QUALITY of an AI tutoring agent's execution.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}

AGENT EXECUTION TRACE:
{agent_trace}

{'=' * 55}
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
  approach is sound.
- Evaluate the LOGIC and CORRECTNESS of the approach, not the tool selection.
{'=' * 55}

CATEGORY-SPECIFIC CRITERIA: {category_criteria}

EVALUATE on 3 dimensions:

1. PROBLEM DECOMPOSITION (0.0-1.0):
   Did the agent break the task into logical sub-steps?
   Did it identify what data/information was needed before acting?
   - 1.0:  Clear, logical decomposition; dependencies identified before acting
   - 0.75: Mostly logical flow, minor planning gaps
   - 0.5:  Some structure but missing key sub-steps
   - 0.25: Minimal planning, jumped into action without structure
   - 0.0:  No decomposition, chaotic execution

2. EXECUTION SOUNDNESS (0.0-1.0):
   Were actions logically sound for achieving the goal?
   Were there any clearly wrong or harmful operations?
   (Reminder: evaluate LOGIC, not tool choice)
   - 1.0:  All actions well-reasoned and effective
   - 0.75: Mostly sound, one minor misstep
   - 0.5:  Some sound actions mixed with questionable choices
   - 0.25: Several logically flawed actions
   - 0.0:  Fundamentally wrong approach

{error_handling_desc}

Return ONLY a JSON object (no markdown, no extra text):
{{"problem_decomposition": <float>, "execution_soundness": <float>, "error_handling": <float>, "reason": "<brief explanation>"}}"""

```

### A.4 QP Process Alignment

**Source**: `evaluation/deepeval_metrics/process_reasonableness.py`

```python
def _build_process_alignment_prompt(
    task: str,
    category: str,
    agent_trace: str,
    agent_step_count: int,
    ref_trace_summary: str,
    ref_step_count: int,
    path_tolerance: float,
) -> str:
    """Build the process alignment evaluation prompt."""
    return f"""You are comparing two execution traces for the same task.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}

REFERENCE TRACE (expert execution, {ref_step_count} steps):
{ref_trace_summary}

AGENT TRACE ({agent_step_count} steps):
{agent_trace}

{'=' * 55}
PATH TOLERANCE CONTEXT:
This task category has a path tolerance level of {path_tolerance:.1f}.
A tolerance of 1.0 means many valid paths exist — be very lenient about
path differences. A tolerance near 0.4 means paths should converge —
significant deviations more likely indicate process issues.

NEUTRALITY: Different tools achieving the same sub-problem are equivalent.
shell_exec doing SMA calculation = compute_indicator("SMA"). Judge
sub-problem coverage, NOT tool matching.
{'=' * 55}

EVALUATE (sub-problem coverage, NOT path matching):

1. COVERAGE (0.0-1.0):
   Did the agent address the same key sub-problems that the reference addressed?
   (e.g., both obtained data, both computed metrics, both visualized results)
   Different tools/methods for the same sub-problem count as covered.
   - 1.0:  All reference sub-problems addressed
   - 0.75: Most sub-problems addressed, one minor gap
   - 0.5:  Core sub-problems covered but several gaps
   - 0.25: Only partial coverage of reference sub-problems
   - 0.0:  Barely any overlap with reference approach

2. DEPTH (0.0-1.0):
   Did the agent reach a similar depth of analysis as the reference?
   (e.g., reference computed 5 risk metrics, agent only computed 2)
   - 1.0:  Similar or greater depth than reference
   - 0.75: Slightly less depth, missing minor details
   - 0.5:  Noticeably less depth than reference
   - 0.25: Significantly shallower analysis
   - 0.0:  Superficial compared to reference

3. SOUNDNESS DELTA (0.0-1.0):
   Compared to the reference, were there clearly inferior methodological choices?
   (e.g., reference used vectorized ops, agent used slow loop — same result but
   different process quality)
   - 1.0:  Methodology as sound as or better than reference
   - 0.75: Mostly sound, one minor methodological gap
   - 0.5:  Some inferior but functional choices
   - 0.25: Several clearly inferior methodological decisions
   - 0.0:  Fundamentally weaker methodology

Return ONLY a JSON object (no markdown, no extra text):
{{"coverage": <float>, "depth": <float>, "soundness_delta": <float>, "reason": "<brief explanation>"}}"""

```

### A.5 QP Code Process (LLM 部分)

**Source**: `evaluation/deepeval_metrics/code_process.py`

```python
def _build_code_process_llm_prompt(
    task: str,
    activity_trace: str,
    actual_output: str,
) -> str:
    """Build the LLM prompt for code process evaluation."""
    output_preview = actual_output[:2000] if actual_output else "(no output)"

    return f"""You are evaluating the CODE DEVELOPMENT PROCESS of an AI tutoring agent.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}

CODE ACTIVITY TRACE (write/exec events only):
{activity_trace}

AGENT CONVERSATION (excerpt):
{output_preview}

{'=' * 55}
EVALUATE on 3 dimensions:

1. DEBUGGING COMPETENCE (0.0-1.0):
   When code fails, does the agent correctly diagnose the root cause?
   Does it make targeted fixes rather than blind trial-and-error?
   - 1.0:  Excellent diagnosis — identifies exact issue, makes precise fix
   - 0.75: Good diagnosis — fixes the right problem, minor extra attempts
   - 0.5:  Partial diagnosis — some correct fixes mixed with guesswork
   - 0.25: Poor diagnosis — mostly trial-and-error, multiple failed attempts
   - 0.0:  No debugging ability — doesn't fix errors or makes them worse
   If no errors occurred, score based on code quality that prevented errors.

2. INCREMENTAL DEVELOPMENT (0.0-1.0):
   Does the agent build progressively (small steps, testing each)?
   Or does it write a large block of code all at once?
   - 1.0:  Excellent progressive development — builds in small, tested increments
   - 0.75: Mostly incremental — tests at key points
   - 0.5:  Mixed approach — some testing but also large untested blocks
   - 0.25: Mostly big-bang — writes large code blocks without intermediate testing
   - 0.0:  Pure big-bang — dumps all code at once, tests only at the end

3. CODE EXPLANATION QUALITY (0.0-1.0):
   Does the agent explain its code to the student? Does it describe
   what the code does, why design choices were made, and what results mean?
   - 1.0:  Excellent explanations — code logic, design choices, and results interpreted
   - 0.75: Good explanations for most code sections
   - 0.5:  Some explanation but gaps in key areas
   - 0.25: Minimal explanation — mostly just code without context
   - 0.0:  No explanation — pure code dump

Return ONLY a JSON object (no markdown, no extra text):
{{"debugging_competence": <float>, "incremental_development": <float>, "code_explanation_quality": <float>, "reason": "<brief explanation>"}}"""

```

### A.6 QP Role Adherence

**Source**: `evaluation/deepeval_metrics/custom_conv_metrics.py`

```python
_ROLE_ADHERENCE_PROMPT = """\
You are evaluating ROLE ADHERENCE of a quantitative finance tutor agent.

ROLE DEFINITION: The agent is a "quantitative finance tutor" who teaches
through a combination of:
- Explaining financial/technical concepts in student-appropriate language
- Using tools (shell_exec, fetch_market_data, file_read, etc.) to prepare
  real data and demonstrate concepts concretely
- Writing and executing Python code to show analysis steps
- Providing step-by-step guidance, answering questions, scaffolding learning

CRITICAL RULE: Tool usage and code execution are CORE tutoring activities,
NOT deviations from role. An agent that fetches data, runs analysis, and
explains the results IS fulfilling the tutor role. Only flag deviations
where the agent clearly abandons the teaching context (e.g., becomes a
generic task-completion assistant with zero educational framing, refuses
to teach, or takes on a completely unrelated role).

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification.

Evaluate these 2 sub-dimensions:

1. PERSONA_CONSISTENCY (1-10):
   Does the agent maintain a consistent tutor persona throughout the conversation?
   - 10: Every substantive response includes educational framing (explains what,
         why, how) even when executing code or using tools
   -  9: Nearly all responses have educational framing; one brief gap
   -  8: Mostly tutoring with consistent pedagogical tone; occasional brief responses
        that are pure execution without explanation
   -  7: Good tutoring overall; a few responses lack educational context
   -  6: Generally pedagogical but noticeable gaps in educational framing
   -  5: Mixed — sometimes teaches, sometimes just silently executes without
        any explanation or educational framing
   -  4: More silent execution than teaching; educational framing inconsistent
   -  3: Mostly silent execution with rare teaching moments
   -  2: Minimal tutoring behavior; almost entirely mechanical execution
   -  1: No evidence of tutoring behavior; purely mechanical task execution

2. BOUNDARY_MAINTENANCE (1-10):
   Does the agent stay within the tutor role boundaries?
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

CONVERSATION:
{turns_text}

Return a valid JSON object with exactly these keys:
{{"persona_consistency": <integer 1-10>, "boundary_maintenance": <integer 1-10>, "reason": "<1-3 sentences>"}}

JSON:"""
```

### A.7 QP Topic Adherence

**Source**: `evaluation/deepeval_metrics/custom_conv_metrics.py`

```python
_TOPIC_ADHERENCE_PROMPT = """\
You are evaluating TOPIC ADHERENCE of a quantitative finance tutor agent.

TASK CONTEXT: {task_description}

RELEVANT TOPIC DOMAINS:
{topics_list}

CRITICAL CONTEXT FOR TOOL-USE AGENTS:
- Tool outputs (data tables, code execution results, numerical output, error
  messages) are ON-TOPIC when they serve the task. Raw output from shell_exec
  showing pandas DataFrames, statistics, or financial data is analytical work,
  NOT off-topic content.
- Python code written for financial data analysis, visualization, or strategy
  implementation is ON-TOPIC.
- Brief environment/metadata responses (e.g., listing available files, checking
  Python version) are neutral setup actions, not off-topic.
- Only flag content that is genuinely UNRELATED to the task or listed topic
  domains (e.g., discussing cooking recipes, unrelated personal topics).

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification.

Evaluate these 2 sub-dimensions:

1. TOPIC_RELEVANCE (1-10):
   How well does the conversation content align with the task and topic domains?
   - 10: All substantive content directly serves the task within listed domains
   -  9: Nearly all content on-topic; one trivial tangent
   -  8: Mostly on-topic with minor tangents that don't derail the session
   -  7: Good relevance; a few moments of tangential content
   -  6: Generally on-topic but some noticeable tangents
   -  5: Mixed — significant portions are relevant but notable off-topic content
   -  4: More off-topic than on-topic
   -  3: Mostly off-topic with some relevant content
   -  2: Nearly all off-topic
   -  1: Entirely off-topic

2. TASK_FOCUS (1-10):
   Does the agent stay focused on the specific task objectives?
   - 10: Agent consistently works toward the stated task goals
   -  9: Excellent focus; one minor diversion that still relates to the domain
   -  8: Mostly focused; minor diversions that still relate to quant finance
   -  7: Good focus; a few moments working on tangential aspects
   -  6: Generally focused but some effort on non-core aspects
   -  5: Partially focused; some work doesn't contribute to task completion
   -  4: Noticeably unfocused; significant effort on non-core work
   -  3: Poorly focused; substantial effort wasted on unrelated directions
   -  2: Minimal focus on stated task
   -  1: No focus on the stated task

CONVERSATION:
{turns_text}

Return a valid JSON object with exactly these keys:
{{"topic_relevance": <integer 1-10>, "task_focus": <integer 1-10>, "reason": "<1-3 sentences>"}}

JSON:"""
```
