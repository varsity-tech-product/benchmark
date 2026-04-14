# Tutor 评分体系增强方案：基于文献调研的四项改进

> 版本：v1.0 | 日期：2026-04-14
> 基于论文调研：TutorBench (2510.02663), MathTutorBench (2502.18940), EduBench (2505.16160)
> 对标文档：stage_report.md, judge_comparison_analysis.md
> 状态：实现规格文档（可直接用于开发）

---

## 〇、背景与改进目标

### 0.1 当前 Tutor 评分的核心矛盾

| 指标 | Tutor | QP | QR |
|------|-------|----|----|
| Cohen's d (区分度) | **1.69** | 1.11 | 0.24 |
| Cross-judge Pearson r (可靠性) | **0.676** | 0.806 | 0.872 |
| 程序化评分占比 | **0%** | ~50% | ~90% |

**矛盾**：Tutor 是系统最大区分力来源（d=1.69），但也是最大可靠性短板（r=0.676）。stage_report 已明确指出："可复现性与程序化评分占比成正比"。

### 0.2 三篇论文的核心启发

| 论文 | 核心方法论 | 对我们的启发 |
|------|-----------|------------|
| **TutorBench** (Scale AI, 2025) | 每个样本 3-39 条 sample-specific rubric，pass/fail 二分判定，negative-critical 权重 -5 | 为 Tutor 引入程序化锚点；强制惩罚"直接给答案" |
| **MathTutorBench** (ETH/EMNLP 2025 Oral) | Pedagogical reward model；"解题≠教学"实证 | 验证 QR-Tutor 独立性设计的正确性；长对话退化分析 |
| **EduBench** (2025) | 12 维评估，教师+学生双视角 | 双视角评估思路；小模型可达大模型水平 |

### 0.3 四项改进的定位

```
方案一 ──→ 引入程序化锚点 ──→ 提升 Tutor cross-judge r
方案二 ──→ 修改现有 rubric ──→ 覆盖缺失维度（最小成本）
方案三 ──→ 对话长度退化分析 ──→ 新分析维度（score_report 增强）
方案四 ──→ Human Calibration ──→ 提供有效性证据（论文关键实验）
```

四个方案独立实施、渐进叠加，不互相依赖。

---

## 方案一：Task-Specific Behavioral Checklist

> **目标**：为 Tutor 引入半程序化锚点，预期将 cross-judge r 从 0.676 提升至 0.75+
> **优先级**：P0 | **工程成本**：中 | **论文价值**：高（新方法论贡献）

### 1.1 设计原理

TutorBench 的核心洞察：**通用维度 1-10 打分的 judge 间一致性，远低于 sample-specific rubric 的 pass/fail 判定**。

| 评估方式 | TutorBench 报告的一致性 | 原因 |
|---------|---------------------|------|
| 通用维度 1-10 打分 | — (未使用) | 10 个档位间的边界模糊，judge 偏好不同 |
| Sample-specific pass/fail | **0.78** (> 人类间 0.75) | 每条 rubric 是可验证的具体行为，二分判定歧义小 |

我们的策略：**保留 D1-D7 ConversationalGEval 作为通用评估，叠加 task-specific checklist 作为锚定信号**。

### 1.2 Checklist 数据格式

在每个 task JSON 中增加 `tutor_checklist` 字段：

```json
{
  "task_id": "D01_load_inspect_ohlcv",
  "category": "data_analysis",
  "tutor_checklist": {
    "version": "1.0",
    "description": "Task-specific behavioral checks for tutor quality evaluation",
    "items": [
      {
        "id": "TC01",
        "description": "When the student asks what OHLCV stands for, tutor explains each letter (Open, High, Low, Close, Volume) with clear definitions",
        "type": "critical",
        "weight": 5,
        "tag": "domain_accuracy",
        "persona_scope": ["beginner", "intermediate"],
        "trigger": "student_asks_definition",
        "notes": "Not applicable if student never asks; mark N/A"
      },
      {
        "id": "TC02",
        "description": "Tutor does NOT directly provide the complete data loading code without first asking the student to attempt it or guiding them through the logic",
        "type": "negative_critical",
        "weight": -5,
        "tag": "autonomy_preservation",
        "persona_scope": ["intermediate", "advanced"],
        "trigger": "code_teaching_moment",
        "notes": "For beginner persona, providing code with explanation is acceptable (score N/A)"
      },
      {
        "id": "TC03",
        "description": "When student expresses a misconception about data (e.g., confuses adjusted close with close, misunderstands volume units), tutor identifies the specific error and corrects it",
        "type": "critical",
        "weight": 5,
        "tag": "error_diagnosis",
        "persona_scope": ["beginner", "intermediate", "advanced"],
        "trigger": "student_misconception",
        "notes": "Only applicable if student expresses a misconception"
      },
      {
        "id": "TC04",
        "description": "Tutor uses at least one analogy or real-world example to explain a financial concept",
        "type": "non_critical",
        "weight": 1,
        "tag": "language_adaptation",
        "persona_scope": ["beginner"],
        "trigger": "concept_introduction",
        "notes": "For intermediate/advanced, analogies are nice but not expected"
      },
      {
        "id": "TC05",
        "description": "Tutor checks student understanding at least once during the conversation (e.g., 'Does this make sense?', 'Can you explain back to me...?')",
        "type": "non_critical",
        "weight": 1,
        "tag": "formative_assessment",
        "persona_scope": ["beginner", "intermediate"],
        "trigger": "any",
        "notes": "For advanced persona, this is optional"
      }
    ]
  }
}
```

### 1.3 字段定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，格式 TC{nn} |
| `description` | string | 可验证的具体行为描述（必须可 pass/fail 判定） |
| `type` | enum | `critical` (权重 +5), `non_critical` (权重 +1), `negative_critical` (权重 -5) |
| `weight` | int | 评分权重，critical=5, non_critical=1, negative_critical=-5 |
| `tag` | string | 对应的能力标签（见 1.4） |
| `persona_scope` | list[str] | 适用的 persona 级别，不在列表中的 persona 标记为 N/A |
| `trigger` | string | 触发条件描述，用于指导 judge 判断是否适用 |
| `notes` | string | 补充说明，特别是 N/A 条件 |

### 1.4 能力标签体系（Tag Taxonomy）

参考 TutorBench 的 8 个教学技能标签，结合我们的领域特点：

| Tag | 来源 | 对应 D1-D7 | 说明 |
|-----|------|-----------|------|
| `error_diagnosis` | TutorBench "Identifying Core Misconceptions" | D4 扩展 | **新增能力**：识别学生的概念性错误 |
| `autonomy_preservation` | TutorBench "Active Learning Support" | D3 扩展 | **新增能力**：不过度帮助，保护学生自主性 |
| `formative_assessment` | EduBench, 教育学理论 | D1/D3 交叉 | **新增能力**：主动检查学生理解 |
| `domain_accuracy` | 现有 D4 | D4 | 事实准确性 |
| `language_adaptation` | 现有 D2 | D2 | 语言适配 |
| `scaffolding` | 现有 D3 | D3 | 脚手架策略 |
| `code_teaching` | 现有 D5 | D5 | 代码教学 |
| `emotional_support` | 现有 D6 | D6 | 情感支持 |
| `safety_boundary` | 现有 D7 | D7 | 安全边界 |

### 1.5 评分算法

#### 1.5.1 Checklist 单项评分

LLM-judge 对每条 checklist 做三分类判定：

```python
class ChecklistVerdict(Enum):
    PASS = "pass"       # 行为符合描述
    FAIL = "fail"       # 行为违反描述
    NOT_APPLICABLE = "na"  # 触发条件未出现，无法判定
```

#### 1.5.2 Checklist 聚合公式

参考 TutorBench 的 ARR_w（Weighted Average Rubric Rating）：

```python
def compute_checklist_score(verdicts: list[dict]) -> float | None:
    """
    计算 checklist 聚合分。

    公式：
        score = sum(weight_i * pass_i for applicable items)
                / sum(abs(weight_i) for applicable items with positive weight)

    negative_critical 项：
        - PASS (行为未出现) → 0 分（不加不减）
        - FAIL (违规行为出现) → weight × 1（扣分）

    Returns None if all items are N/A.
    """
    applicable = [v for v in verdicts if v["verdict"] != "na"]
    if not applicable:
        return None  # 全部 N/A，不贡献 checklist 分数

    numerator = 0.0
    denominator = 0.0

    for v in applicable:
        w = v["weight"]
        if v["type"] == "negative_critical":
            # negative_critical: FAIL = 扣分, PASS = 不扣分
            if v["verdict"] == "fail":
                numerator += w  # w 是负数，所以是扣分
        else:
            # critical / non_critical: PASS = 得分, FAIL = 不得分
            if v["verdict"] == "pass":
                numerator += w
            denominator += w  # 只有正权重项贡献分母

    # 防止 negative_critical 扣到负数
    denominator = max(denominator, 1.0)
    return max(0.0, numerator / denominator)
```

#### 1.5.3 与 D1-D7 的整合

```python
# 新的 Tutor 总分计算
CHECKLIST_BLEND_ALPHA = 0.60  # D1-D7 占比
CHECKLIST_BLEND_BETA = 0.40   # Checklist 占比

def compute_tutor_score_v2(
    d1_d7_scores: dict[str, float],  # 现有 ConversationalGEval 分数
    checklist_score: float | None,    # Checklist 聚合分
    category: str,
    requires_code: bool,
) -> float:
    """带 checklist 锚点的 Tutor 总分。"""
    # 现有 D1-D7 加权平均（保持不变）
    d1_d7_weighted = compute_tutor_score(d1_d7_scores, category, requires_code)

    if checklist_score is None:
        # 所有 checklist 项都 N/A，退回到纯 D1-D7
        return d1_d7_weighted

    return CHECKLIST_BLEND_ALPHA * d1_d7_weighted + CHECKLIST_BLEND_BETA * checklist_score
```

### 1.6 Judge Prompt 模板

```python
CHECKLIST_JUDGE_PROMPT = """You are evaluating a tutor's teaching behavior in a quantitative finance tutoring conversation.

For each checklist item below, determine whether the tutor's behavior PASSES or FAILS the criterion, or whether the criterion is NOT APPLICABLE (the triggering condition never occurred in the conversation).

IMPORTANT RULES:
1. Each item has a "trigger" field describing when it applies. If the trigger condition never occurred in the conversation, mark as "N/A".
2. For "negative_critical" items: PASS means the tutor did NOT exhibit the undesirable behavior. FAIL means the tutor DID exhibit it.
3. Base your judgment ONLY on observable behavior in the conversation. Do not infer intent.
4. For each verdict, provide a one-sentence justification quoting or referencing specific parts of the conversation.

Student persona: {persona_level}
Task: {task_id} — {task_description}

Checklist items:
{checklist_items_json}

Conversation:
{conversation}

Respond in JSON format:
{{
  "verdicts": [
    {{
      "id": "TC01",
      "verdict": "pass" | "fail" | "na",
      "justification": "..."
    }},
    ...
  ]
}}"""
```

### 1.7 Checklist 编写规范

每个 task 需要 **5-15 条** checklist。编写时遵循以下原则（来自 TutorBench §2.3）：

| 原则 | 说明 | 反例 |
|------|------|------|
| **Self-contained** | 每条 rubric 可独立判定，不依赖其他条目的结果 | ❌ "如果 TC01 为 pass，则检查..." |
| **Mutually exclusive** | 不同条目不评同一个行为的同一个方面 | ❌ TC01 和 TC02 都检查"是否解释了 Sharpe ratio" |
| **Verifiable** | 描述可观测的行为，不推测意图 | ❌ "tutor 理解了学生的困惑"（不可观测） |
| **Binary** | 结果必须是 pass/fail/na，不允许 "partially pass" | ❌ "tutor 部分解释了..." |
| **Persona-aware** | 标明适用的 persona，避免对 advanced 学生强求 beginner 标准 | ❌ 对 advanced 要求"使用类比解释基础概念" |

### 1.8 Checklist 模板（按任务类别）

#### Data Analysis 类任务模板

```json
{
  "items": [
    {"tag": "error_diagnosis",        "type": "critical",          "template": "When student misinterprets {metric}, tutor identifies and corrects the misconception"},
    {"tag": "autonomy_preservation",  "type": "negative_critical", "template": "Tutor does NOT provide complete {analysis_code} without prompting student to attempt first"},
    {"tag": "domain_accuracy",        "type": "critical",          "template": "Tutor correctly explains {financial_concept} including its formula and interpretation"},
    {"tag": "formative_assessment",   "type": "non_critical",      "template": "Tutor asks student to interpret {result} before providing the correct interpretation"},
    {"tag": "language_adaptation",    "type": "non_critical",      "template": "Tutor uses analogy or real-world example when introducing {concept}"}
  ]
}
```

#### Implementation 类任务模板

```json
{
  "items": [
    {"tag": "autonomy_preservation",  "type": "negative_critical", "template": "Tutor does NOT provide complete strategy implementation code in a single block without guided development"},
    {"tag": "error_diagnosis",        "type": "critical",          "template": "When student's code has {bug_type}, tutor identifies the root cause rather than just fixing the symptom"},
    {"tag": "code_teaching",          "type": "critical",          "template": "Tutor explains the purpose of {key_code_pattern} before or after introducing it"},
    {"tag": "scaffolding",            "type": "non_critical",      "template": "Tutor breaks implementation into testable increments (e.g., signal generation → position sizing → execution)"},
    {"tag": "formative_assessment",   "type": "non_critical",      "template": "Tutor asks student to predict what code output will be before running it"}
  ]
}
```

#### Debug 类任务模板

```json
{
  "items": [
    {"tag": "autonomy_preservation",  "type": "negative_critical", "template": "Tutor does NOT directly reveal the bug location without guiding student through diagnosis"},
    {"tag": "error_diagnosis",        "type": "critical",          "template": "Tutor helps student understand WHY {bug} occurs, not just HOW to fix it"},
    {"tag": "scaffolding",            "type": "critical",          "template": "Tutor guides student through a systematic debugging methodology (hypothesis → test → verify)"},
    {"tag": "code_teaching",          "type": "non_critical",      "template": "Tutor teaches a transferable debugging technique (e.g., print debugging, bisection, assertion)"},
    {"tag": "domain_accuracy",        "type": "critical",          "template": "Tutor correctly identifies {specific_bug} as the root cause"}
  ]
}
```

### 1.9 实施计划

| 步骤 | 内容 | 产出 | 工时估算 |
|------|------|------|---------|
| 1 | 为 8 个 ICC 任务编写 checklist | 8 × ~10 条 = ~80 条 | 4h |
| 2 | 实现 `checklist_eval.py` | 评估模块 | 4h |
| 3 | 在 ICC 数据上跑 checklist + D1-D7 对比 | cross-judge r 对比报告 | 2h |
| 4 | 验证 r 提升后，为全部 65 任务编写 checklist | 65 × ~10 条 = ~650 条 | 16h |
| 5 | 整合到 `tutor_conv_geval.py` 和 `score_report.py` | 生产代码 | 4h |
| **合计** | | | **~30h** |

### 1.10 预期效果与验证标准

| 指标 | 当前值 | 目标值 | 验证方法 |
|------|--------|--------|---------|
| Cross-judge Pearson r (Tutor) | 0.676 | ≥ 0.75 | 在 ICC 数据上对比 r_old vs r_new |
| Cohen's d (Tutor) | 1.69 | ≥ 1.50 (不大幅下降) | 确认区分度未因锚定而压缩 |
| Checklist judge 一致性 | — | ≥ 0.80 (pass/fail F1) | 与 human calibration (方案四) 交叉验证 |

### 1.11 与 score_report 的集成

在 Tutor 7D Breakdown section 后新增：

```markdown
### Tutor Behavioral Checklist

| Item | Tag | Type | Weight | Verdict | Justification |
|------|-----|------|--------|---------|---------------|
| TC01 | error_diagnosis | critical | +5 | ✅ PASS | "In turn 4, tutor correctly identified..." |
| TC02 | autonomy_preservation | neg_critical | -5 | ✅ PASS | "Tutor guided student through steps..." |
| TC03 | domain_accuracy | critical | +5 | ❌ FAIL | "Tutor stated Sharpe = mean/std but..." |
| TC04 | language_adaptation | non_critical | +1 | ⬜ N/A | "Student did not ask about this concept" |

**Checklist Score**: 0.750 (5/10 applicable items passed, weighted)
**Tutor Score (blended)**: 0.60 × D1-D7 (0.645) + 0.40 × Checklist (0.750) = **0.687**
```

---

## 方案二：D3/D4 Rubric 修改

> **目标**：在不增加维度、不改代码的前提下，通过修改 rubric JSON 覆盖 Autonomy Preservation 和 Error Diagnosis
> **优先级**：P1 | **工程成本**：极低（改 3 个 JSON 文件） | **论文价值**：低

### 2.1 D3 Scaffolding Calibration — 增加 Autonomy Preservation 标准

#### 2.1.1 Beginner 级别修改

**当前 Score 1**:
> "Agent dumps a complete solution with no explanation of individual steps. No scaffolding at all. (Note: using tools to prepare data before explaining is NOT dumping — dumping means presenting results without any pedagogical structure.)"

**修改为**:
> "Agent dumps a complete solution with no explanation of individual steps. No scaffolding at all. Agent provides complete code blocks or full answers without any attempt to guide the student through the reasoning process. (Note: using tools to prepare data before explaining is NOT dumping — dumping means presenting results without any pedagogical structure.)"

**当前 Score 7**:
> "Agent provides clear scaffolding with a logical sequence. Each step is preceded by context ('Now we will...') and followed by a comprehension check ('Does this make sense?'). When tools are used, the results are explained step by step."

**修改为**:
> "Agent provides clear scaffolding with a logical sequence. Each step is preceded by context ('Now we will...') and followed by a comprehension check ('Does this make sense?'). When tools are used, the results are explained step by step. Agent uses guiding questions to help the student think through problems rather than immediately providing complete solutions."

**当前 Score 9**:
> "Agent creates explicit learning milestones. Periodically summarizes progress ('So far we have covered X, Y, Z — next we will look at W'). Each new concept is connected to the overall learning goal. When tools are used, their results are woven into the teaching narrative rather than presented as raw output."

**修改为**:
> "Agent creates explicit learning milestones. Periodically summarizes progress ('So far we have covered X, Y, Z — next we will look at W'). Each new concept is connected to the overall learning goal. When tools are used, their results are woven into the teaching narrative rather than presented as raw output. Agent actively protects student autonomy — asks the student to attempt solutions before providing them, and adjusts the level of guidance based on whether the student is making progress independently."

#### 2.1.2 Intermediate 级别修改

**当前 Score 1** 增加:
> "...or Agent provides complete implementation code without giving the developer a chance to write any code themselves."

**当前 Score 8** 增加:
> "Agent respects the developer's ability to code independently — provides conceptual guidance on quant-specific patterns while letting the student handle the implementation, intervening only when the student is stuck."

#### 2.1.3 Advanced 级别修改

**当前 Score 9-10** 增加:
> "Agent guides the student toward developing their own problem-solving methodology — e.g., teaching systematic debugging approaches, how to evaluate strategy robustness, or how to decompose unfamiliar quant problems — rather than only addressing the immediate question. This metacognitive scaffolding helps the advanced learner become self-sufficient."

### 2.2 D4 Domain Accuracy — 扩展为包含 Error Diagnosis

#### 2.2.1 所有级别的 Criteria 修改

**当前**:
> "The agent provides factually correct information about financial concepts, formulas, and trading mechanics. Regardless of the level of simplification or depth used, the core facts must be accurate. No misleading statements, incorrect formulas, or factual errors."

**修改为**:
> "The agent provides factually correct information about financial concepts, formulas, and trading mechanics. Regardless of the level of simplification or depth used, the core facts must be accurate. No misleading statements, incorrect formulas, or factual errors. **Additionally, when the student expresses misconceptions or makes factual errors, the agent should identify and address them accurately.**"

#### 2.2.2 Scoring Guidance 修改（所有级别通用）

**当前 Score 7**:
> "Agent is accurate across all core concepts. Simplified explanations preserve the essential truth without introducing errors."

**修改为**:
> "Agent is accurate across all core concepts. Simplified explanations preserve the essential truth without introducing errors. When the student expresses a misconception, the agent identifies the specific error and provides a correct explanation."

**当前 Score 9**:
> "Agent is highly accurate with excellent attention to detail. Correctly handles edge cases and boundary conditions..."

**修改为**:
> "Agent is highly accurate with excellent attention to detail. Correctly handles edge cases and boundary conditions... When the student expresses misconceptions, the agent not only corrects the surface error but diagnoses the underlying conceptual gap — explaining *why* the student's reasoning led to the wrong conclusion."

**当前 Score 10** 增加:
> "...When student misconceptions arise, the agent treats them as teaching opportunities — diagnosing the root cause, connecting the correction to the broader conceptual framework, and verifying the student has understood the correction."

### 2.3 实施步骤

```bash
# 需要修改的文件：
bench/evaluation/rubrics/rubric_beginner.json      # D3 + D4
bench/evaluation/rubrics/rubric_intermediate.json   # D3 + D4
bench/evaluation/rubrics/rubric_advanced.json       # D3 + D4 + metacognitive scaffolding
```

### 2.4 修改前后对比验证

在 ICC 数据上对比修改前后的 Tutor 分数分布变化：

| 验证项 | 预期结果 |
|--------|---------|
| 对"直接给答案"行为的 D3 分数 | 修改后应下降 |
| 对"识别学生错误"行为的 D4 分数 | 修改后高质量对话应上升 |
| 整体 Cohen's d | 预期微增（因为区分了"引导式教学" vs "灌输式教学"） |
| Cross-judge r | 预期不变（仍是 1-10 主观打分） |

---

## 方案三：对话长度退化分析

> **目标**：检测 tutor 是否在长对话中教学质量退化（MathTutorBench 核心发现）
> **优先级**：P2 | **工程成本**：低 | **论文价值**：中（interesting finding section）

### 3.1 背景

MathTutorBench 的关键发现：

> "Tutoring becomes more challenging in longer dialogs, where simpler questioning strategies (e.g., basic Socratic prompting) begin to fail."

你们的数据中 B02 的高 CV (18.6%) 和 S03 的分数波动可能部分来自对话长度差异。如果能证明"Sonnet 在长对话中退化更少"，这是一个有力的区分度来源。

### 3.2 分析方法

#### 3.2.1 对话分段评估

将每次对话按轮次均分为前后两段，分别独立评估 D1-D7：

```python
def split_conversation_halves(turns: list[Turn]) -> tuple[list[Turn], list[Turn]]:
    """将对话按轮次均分为前后两段。"""
    mid = len(turns) // 2
    return turns[:mid], turns[mid:]

# 分别评估
first_half_scores = evaluate_tutor_7d(first_half_turns, ...)
second_half_scores = evaluate_tutor_7d(second_half_turns, ...)

# 计算退化指标
degradation = {
    dim: second_half_scores[dim] - first_half_scores[dim]
    for dim in DIMENSIONS
}
# degradation < 0 意味着后半段教学质量下降
```

#### 3.2.2 退化指数 (Degradation Index)

```python
def compute_degradation_index(
    first_half: dict[str, float],
    second_half: dict[str, float],
) -> float:
    """
    退化指数 = mean(second_half - first_half) across all dimensions.
    负值表示退化，正值表示对话后期教学改善。
    """
    deltas = [second_half[d] - first_half[d] for d in DIMENSIONS if d in second_half]
    return sum(deltas) / len(deltas) if deltas else 0.0
```

### 3.3 报告输出

在 score_report 的 Tutor section 增加：

```markdown
### Tutor Degradation Analysis

| Dimension | First Half | Second Half | Δ | Trend |
|-----------|-----------|------------|---|-------|
| D1_level_detection | 0.72 | 0.68 | -0.04 | ↓ slight |
| D2_language_adaptation | 0.65 | 0.63 | -0.02 | → stable |
| D3_scaffolding | 0.70 | 0.55 | -0.15 | ↓↓ notable |
| ... | | | | |
| **Degradation Index** | | | **-0.06** | |

Interpretation: Agent shows notable scaffolding degradation in longer conversations,
suggesting difficulty maintaining structured guidance as complexity accumulates.
```

### 3.4 聚合分析（跨任务）

在全量运行完成后，生成：

1. **散点图**：X = 对话轮次数, Y = Tutor 总分（按 agent 着色）
2. **Agent 对比**：Sonnet degradation index vs Haiku degradation index
3. **维度热力图**：哪些维度在长对话中最容易退化

### 3.5 实施步骤

| 步骤 | 内容 | 修改文件 |
|------|------|---------|
| 1 | 实现 `split_conversation_halves` | `tutor_conv_geval.py` |
| 2 | 增加 degradation 计算逻辑 | `tutor_conv_geval.py` |
| 3 | 在 score_report 增加 degradation section | `score_report.py` |
| 4 | 在 ICC 数据上验证 | — |

**代码量**：约 80-100 行新增代码

### 3.6 成本控制

分段评估会将 Tutor 评估的 LLM 调用量翻倍。两种控制策略：

- **策略 A（推荐）**：只在 ICC 实验和论文撰写阶段启用，不在 full run 中常规使用
- **策略 B**：只对超过 N 轮的长对话启用（如 N=15），短对话跳过

```python
DEGRADATION_ANALYSIS_ENABLED = False  # 默认关闭
DEGRADATION_MIN_TURNS = 15            # 至少 15 轮才做分段分析
```

---

## 方案四：Human Calibration 实验

> **目标**：提供 Tutor 评分有效性的硬证据，回答 reviewer 核心质疑："你的 LLM judge 可靠吗？"
> **优先级**：P0 | **工程成本**：中（需要领域专家） | **论文价值**：极高

### 4.1 为什么这是 P0

stage_report §8.2 已列出 P0 项："Human calibration (30-50 samples)"。三篇论文给出了这项实验的黄金标准：

| 论文 | 人类一致性数据 | 论文说服力 |
|------|-------------|-----------|
| TutorBench | LLM-judge 0.78 > 人类间 0.75, F1=0.82 | **极强**——这是论文最被引用的结论 |
| MathTutorBench | Reward model 区分专家/新手 | 中等 |
| EduBench | 人类验证 model 评估 | 中等 |

**没有这个实验，reviewer 可以合理质疑所有基于 Tutor 分数的结论。**

### 4.2 实验设计

#### 4.2.1 样本选择

```
总样本量：40 个对话（覆盖分数分布）
├── 高分段 (Tutor ≥ 0.70): 12 个
├── 中分段 (0.45 ≤ Tutor < 0.70): 16 个
├── 低分段 (Tutor < 0.45): 12 个
└── 覆盖：至少 5 个 task category, 2 个 agent, 3 个 persona level
```

**选样标准**：
- 排除 Tutor=0.0 的 API 超时异常值
- 优先选择两个 judge 分歧大（Δ > 0.15）的对话——这些是最需要人类校准的
- 确保 sonnet agent 和 haiku agent 各占一半

#### 4.2.2 评分方式

每个对话由 **3 位独立评分员** 评估，评分员构成：

| 角色 | 人数 | 负责内容 | 资格要求 |
|------|------|---------|---------|
| 量化金融专家 | 1-2 人 | D4 Domain Accuracy, D5 Code Teaching, D7 Safety | 量化分析/交易经验 3+ 年 |
| 教育学/教学设计专家 | 1-2 人 | D1 Level Detection, D2 Language, D3 Scaffolding, D6 Empathy | 教学设计/教育技术背景 |
| 混合背景 | 1 人 | 全部维度 | 有教学经验的量化从业者 |

#### 4.2.3 评分内容

每位评分员需完成：

**Part A：D1-D7 维度评分（1-10 整数）**
- 使用与 LLM-judge 完全相同的 rubric（从 `rubric_{level}.json` 导出）
- 每个维度独立评分，附一句 justification

**Part B：Checklist 判定（如果方案一已实施）**
- 对每条 task-specific checklist 做 pass/fail/na 判定
- 附一句 justification

**Part C：整体质量排序**
- 从同一 task 的 3 个对话中选出"最好的教学"和"最差的教学"
- 这提供了 **相对排序** 信号，比绝对分数更稳定

### 4.3 评分工具

为评分员提供标准化的评分界面（可以是简单的 spreadsheet）：

```
| 对话 ID | D1 (1-10) | D1 理由 | D2 (1-10) | D2 理由 | ... | D7 (1-10) | D7 理由 |
|---------|-----------|---------|-----------|---------|-----|-----------|---------|
| conv_001 | | | | | | | |
| conv_002 | | | | | | | |
```

**评分前培训**：
1. 10 分钟 rubric 讲解（使用 tutor_rubric_expert_review.md）
2. 2 个校准样本（全体评分员一起评，对齐标准）
3. 正式评分（独立完成）

### 4.4 分析指标

#### 4.4.1 核心指标（论文必须报告）

```python
# 1. LLM-judge vs Human 一致性
llm_human_agreement = compute_agreement(llm_scores, human_majority_scores)
# TutorBench 基线: 0.78
# 我们的目标: ≥ 0.75

# 2. 人类间一致性
human_inter_agreement = compute_pairwise_agreement(human_scores_list)
# TutorBench 基线: 0.75
# 报告实际值

# 3. LLM-judge vs Human 排序一致性 (Kendall τ)
rank_agreement = compute_kendall_tau(llm_ranks, human_ranks)
# 目标: τ ≥ 0.60

# 4. 如果实施方案一：Checklist F1
checklist_f1 = compute_f1(llm_checklist_verdicts, human_checklist_verdicts)
# TutorBench 基线: 0.82
# 目标: ≥ 0.80
```

#### 4.4.2 补充指标（附录报告）

```python
# 5. 逐维度一致性
per_dim_agreement = {
    dim: compute_agreement(llm_dim_scores, human_dim_scores)
    for dim in DIMENSIONS
}
# 目的：识别哪些维度的 LLM-judge 最不可靠

# 6. Cohen's Kappa (考虑随机一致的影响)
kappa = compute_cohens_kappa(llm_scores_binned, human_scores_binned)

# 7. Krippendorff's Alpha (多评分员)
alpha = compute_krippendorff_alpha(all_rater_scores)
```

### 4.5 Agreement 计算方法

参考 TutorBench §3.7，使用以下方法：

```python
def compute_agreement(scores_a: list[float], scores_b: list[float], threshold: float = 1.0) -> float:
    """
    计算两组分数的 agreement rate。

    TutorBench 定义：如果两个分数的差异 ≤ threshold，则认为 agree。
    对于 1-10 scale，threshold=1 意味着允许 1 分误差。
    对于 pass/fail，直接比较是否相同。
    """
    assert len(scores_a) == len(scores_b)
    agreements = sum(1 for a, b in zip(scores_a, scores_b) if abs(a - b) <= threshold)
    return agreements / len(scores_a)
```

### 4.6 预期结果与论文叙事

#### 4.6.1 如果结果达标（LLM-human ≥ 0.75）

> "Human calibration on 40 conversations (120 expert ratings) shows that our LLM-judge achieves {X} agreement with human experts, comparable to or exceeding the inter-human agreement of {Y}. This validates the reliability of Tutor 7D scores reported throughout the paper. Per-dimension analysis shows strongest agreement on D4 (Domain Accuracy, agreement={Z1}) and weakest on D6 (Empathetic Response, agreement={Z2}), consistent with the finding that factual dimensions are easier to judge reliably."

#### 4.6.2 如果结果未达标（LLM-human < 0.75）

分析原因，可能的应对：
- 如果某个维度特别低 → 修改该维度的 rubric 使其更 operationalizable
- 如果整体偏低 → 增加 judge runs（3→5），或使用更强的 judge model
- 如果 checklist F1 高但 D1-D7 agreement 低 → 更加依赖 checklist（调大 β）

### 4.7 实施计划

| 步骤 | 内容 | 时间 | 依赖 |
|------|------|------|------|
| 1 | 确定评分员（3 人） | 1 周 | — |
| 2 | 选择 40 个对话样本 | 1 天 | ICC 数据 |
| 3 | 准备评分材料（导出对话 + rubric + 评分表） | 1 天 | 步骤 2 |
| 4 | 评分员培训 + 2 个校准样本 | 0.5 天 | 步骤 3 |
| 5 | 正式评分（40 对话 × 7 维度 × 3 人） | 2-3 天 | 步骤 4 |
| 6 | 收集数据 + 计算指标 | 1 天 | 步骤 5 |
| 7 | 撰写论文 section | 1 天 | 步骤 6 |
| **合计** | | **~2 周** | |

### 4.8 评分员补偿与招募

| 角色 | 招募渠道 | 工作量 | 建议补偿 |
|------|---------|--------|---------|
| 量化金融专家 | 团队内部 / 行业联系人 | 40 对话 × 15-20 min = 10-13h | 按市场价或学术合作 |
| 教育学专家 | 大学教育学院 / 在线招募 | 40 对话 × 15-20 min = 10-13h | 按市场价或挂名致谢 |
| 混合背景 | 有教学经验的量化从业者 | 40 对话 × 15-20 min = 10-13h | 按市场价或挂名致谢 |

---

## 附录 A：三篇论文关键方法论对比

| 维度 | TutorBench | MathTutorBench | EduBench | **QuantTutorBench (ours)** |
|------|-----------|----------------|----------|---------------------------|
| **评估对象** | 单轮 tutor 回复 | 多轮对话 | 多场景 LLM 输出 | 多轮对话（交互式） |
| **评分方法** | Sample-specific rubric pass/fail | Pedagogical reward model | 12 维 LLM 评估 | D1-D7 ConversationalGEval + Checklist (proposed) |
| **Judge** | Claude Sonnet 4 | Trained reward model | LLM | Multi-model (Sonnet/Haiku) cross-validation |
| **人类验证** | 69 experts, agreement 0.78 | Expert/novice distinction | Human annotation | **Planned: 3 experts, 40 samples** |
| **程序化成分** | Rubric 权重系统 | — | — | Programmatic eval + code_eval + **Checklist (proposed)** |
| **学生** | 真人学生数据 (archived) | 真人学生数据 | 合成数据 | LLM 模拟学生 (3 personas) |
| **缺失维度处理** | N/A (不适用的 rubric 跳过) | — | — | N/A checklist 项不计入分数 |
| **负面行为惩罚** | negative-critical weight -5 | — | — | **negative_critical checklist (proposed)** |

## 附录 B：论文有效性论证策略

根据三篇论文的经验，我们的论文应按以下结构组织有效性论证：

```
Section 5.3: Evaluation Reliability
  ├── 5.3.1 Cross-Judge Consistency (已有数据)
  │     "两个独立 judge 下排名 100% 一致，Tutor r=0.676"
  │
  ├── 5.3.2 Programmatic Anchoring Effect (已有数据)
  │     "程序化占比越高，cross-judge r 越高"
  │
  ├── 5.3.3 Human Calibration (方案四)  ← 最关键
  │     "LLM-judge agreement with humans = X, ≥ inter-human agreement Y"
  │
  ├── 5.3.4 Checklist as Reliability Anchor (方案一)
  │     "Adding task-specific checklist raises Tutor r from 0.676 to Z"
  │
  └── 5.3.5 Scoring Granularity Ablation (已有数据)
        "Clamping D1-D7 to 5-point scale: d 1.75→1.82, negligible impact"
```

## 附录 C：快速实施检查清单

### 方案二（立即可做）
- [ ] 修改 `rubric_beginner.json` — D3 score 1, 7, 9 + D4 criteria, score 7, 9, 10
- [ ] 修改 `rubric_intermediate.json` — D3 score 1, 8 + D4 criteria, score 7, 9, 10
- [ ] 修改 `rubric_advanced.json` — D3 score 1, 9-10 (metacognitive) + D4 criteria, score 7, 9, 10
- [ ] 在 ICC 数据上 dry-run 验证分数分布变化

### 方案一（1-2 周）
- [ ] 定义 checklist JSON schema，更新 task JSON validation
- [ ] 为 8 个 ICC 任务编写 checklist
- [ ] 实现 `checklist_eval.py`（judge prompt + scoring logic）
- [ ] 在 ICC 数据上跑 checklist，计算 cross-judge r 提升
- [ ] 确认 r 提升后，为剩余 57 个任务编写 checklist
- [ ] 整合到 `tutor_conv_geval.py` 和 `score_report.py`

### 方案三（1-2 天）
- [ ] 实现对话分段逻辑
- [ ] 实现 degradation index 计算
- [ ] 在 score_report 增加 degradation section
- [ ] 在 ICC 数据上生成 degradation 分析

### 方案四（2 周，可与方案一并行）
- [ ] 招募 3 位评分员
- [ ] 选择 40 个对话样本，导出评分材料
- [ ] 评分员培训 + 校准
- [ ] 正式评分收集
- [ ] 计算 agreement 指标
- [ ] 撰写论文 evaluation reliability section
