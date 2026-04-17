# Tutor 评分体系增强方案：基于文献调研与统计指标分析的五项改进

> 版本：v3.0 | 日期：2026-04-16
> 基于论文调研：TutorBench (2510.02663), MathTutorBench (2502.18940), EduBench (2505.16160)
> 对标文档：stage_report.md, judge_comparison_analysis.md
> 状态：实现规格文档（可直接用于开发）
> v2.0 变更：新增方案五（统计指标增强）；基于论文指标深度调研重构方案四；统一验证指标体系
> v3.0 变更：标注方案二因人格体系重构而作废；新增人格体系重构说明（§0.5）

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
| **MathTutorBench** (ETH/EMNLP 2025 Oral) | Pedagogical reward model；pairwise ranking accuracy 验证；"解题≠教学"实证 | 验证 QR-Tutor 独立性设计的正确性；长对话退化分析 |
| **EduBench** (2025) | 12 维评估，教师+学生双视角；Kendall's W 一致性检验 | 双视角评估思路；多 rater 一致性度量方法 |

### 0.3 三篇论文的统计方法论审计

深入调研发现，三篇论文的统计验证方法均存在显著缺陷，我们的现有指标体系已在多个维度上领先：

| 统计目的 | TutorBench | MathTutorBench | EduBench | **我们（现有）** |
|---------|-----------|----------------|----------|----------------|
| **评分员间一致性** | Simple agreement rate（未校正偶然一致） | **未报告** | Kendall's W | Pearson r + ICC + Kendall τ |
| **区分度/效应量** | **未报告** | Pairwise ranking accuracy | **未报告** | Cohen's d + Wilcoxon p |
| **评分稳定性** | **未报告** | **未报告** | **未报告** | CV (变异系数) |
| **维度独立性** | **未报告** | 定性讨论 | **未报告** | 维度间 Pearson r |
| **评分粒度影响** | **未报告** | **未报告** | **未报告** | 消融实验 |
| **LLM-Judge 人类验证** | Agreement 0.78, F1 0.82 (69人) | 未做 | Kendall's W + MAE (3人) | **未做（本文档规划）** |
| **置信区间** | ±值（方法未说明） | **未报告** | **未报告** | **未报告** |
| **偶然一致校正** | **未做** | **未做** | **未做** | **未做** |

**关键发现**：
- **TutorBench 的 agreement=0.78 有统计缺陷**：对 pass/fail 二分类使用 simple agreement 而非 Cohen's Kappa，未扣除偶然一致的 baseline（base rate 70% pass 时，随机猜测即可达 ~58% agreement）。
- **MathTutorBench 的验证最薄弱**：仅靠 pairwise ranking accuracy（0.84）一个指标，无一致性、无效应量、无置信区间。
- **三篇论文均缺少**：Cohen's Kappa、Krippendorff's Alpha、Bootstrap CI、Bland-Altman 分析——这些恰恰是 reviewer 最熟悉的"标准"验证指标。
- **我们的独有优势**：Cross-run CV、维度间独立性分析、评分粒度消融——三篇论文均未涉及。

### 0.4 五项改进的定位

```
方案一 ──→ 引入程序化锚点       ──→ 提升 Tutor cross-judge r        [P0, 工程成本中]
方案二 ──→ 修改现有 rubric       ──→ 覆盖缺失维度（最小成本）       [P1, 工程成本极低]
方案三 ──→ 对话长度退化分析     ──→ 新分析维度（score_report 增强）  [P2, 工程成本低]
方案四 ──→ 统计指标增强         ──→ 补齐论文级统计证据（零代码成本）  [P0, 工程成本极低] ★ 新增
方案五 ──→ Human Calibration    ──→ 提供有效性硬证据                 [P0, 需领域专家]
```

五个方案独立实施、渐进叠加，不互相依赖。方案四可在现有数据上立即执行，方案五依赖专家招募。

### 0.5 人格体系重构对本文档的影响

> **关联决策**：[Issue #12 — Restructure persona system from 3-level proficiency to Finance×Code binary matrix](https://github.com/varsity-tech-product/benchmark/issues/12)

人格体系将从三级 proficiency（Beginner / Intermediate / Advanced）重构为 {Finance, Code} × {听说未实践, 精通} 的四象限矩阵。对本文档五个方案的影响：

| 方案 | 影响 |
|------|------|
| **方案一**（Task-Specific Checklist） | 方法论有效。Checklist 条目需按四象限重新设计，但 pass/fail 判定机制不变 |
| **方案二**（D3/D4 Rubric 修改） | **作废。** 修改对象是旧三人格的 rubric 文本，四象限重构后全部 rubric 将重写。Plan 2 子目录已删除 |
| **方案三**（对话长度退化分析） | 不受影响。分析方法与人格体系正交 |
| **方案四**（统计指标增强） | 不受影响。统计方法与人格体系正交。已实施 |
| **方案五**（Human Calibration） | 不受影响。校准方法论不依赖具体人格定义，但实验设计需适配四象限 |

### 0.6 维度体系重构：7D → 6D

> **关联文档**：[6d_dimension_restructure.md](6d_dimension_restructure.md)

基于 2×2 人格矩阵和旧 D1-D3 维度间 r > 0.92 的实证数据，TEI 维度从 7D 重构为 6D：

| 新维度 | 来源 | 变动说明 |
|--------|------|---------|
| D1 Finance-Axis Adaptation | 旧 D1/D2/D3 在金融轴的部分 | 按知识轴切分，非按交付模态 |
| D2 Code-Axis Adaptation | 旧 D1/D2/D3 在代码轴的部分 | 同上 |
| D3 Pedagogical Method | 旧 D3 的过程质量部分 | 新增独立维度：教学过程的响应性与结构化 |
| D4 Instructional Accuracy | 旧 D4 重新定义 | 范围收窄为对话中解释的事实正确性 |
| D5 Empathetic Response | 旧 D6 | 重编号，定义不变 |
| D6 Safety & Boundaries | 旧 D7 | 重编号，定义不变 |
| ~~D5 Code Teaching~~ | **已删除** | 代码适配 → D2；代码质量 → QR/QP |

对五个方案的额外影响：
- **方案一**：checklist tag taxonomy 需更新（旧 `scaffolding` 拆分为 `finance_adaptation` / `code_adaptation` / `pedagogical_method`）
- **方案三**：退化分析维度名称更新为 6D
- **方案五**：评分表从 7D 更新为 6D

---

## 方案一：Task-Specific Behavioral Checklist

> **目标**：为 Tutor 引入半程序化锚点，预期将 cross-judge r 从 0.676 提升至 0.75+
> **优先级**：P0 | **工程成本**：中 | **论文价值**：高（新方法论贡献）

### 1.1 设计原理

TutorBench 的核心洞察：**通用维度 1-10 打分的 judge 间一致性，远低于 sample-specific rubric 的 pass/fail 判定**。

| 评估方式 | TutorBench 报告的一致性 | 原因 |
|---------|---------------------|------|
| 通用维度 1-10 打分 | — (未使用) | 10 个档位间的边界模糊，judge 偏好不同 |
| Sample-specific pass/fail | **0.78** agreement (> 人类间 0.75) | 每条 rubric 是可验证的具体行为，二分判定歧义小 |

> **注意**：TutorBench 的 0.78 是 simple agreement rate，未用 Cohen's Kappa 校正偶然一致。实际信息量可能低于表面数字。但即便如此，pass/fail 判定的一致性优势是公认的——我们在验证时将同时报告 agreement rate 和 Kappa 以避免同样的统计缺陷（见方案四 §4.3）。

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
| **Self-contained** | 每条 rubric 可独立判定，不依赖其他条目的结果 | "如果 TC01 为 pass，则检查..." |
| **Mutually exclusive** | 不同条目不评同一个行为的同一个方面 | TC01 和 TC02 都检查"是否解释了 Sharpe ratio" |
| **Verifiable** | 描述可观测的行为，不推测意图 | "tutor 理解了学生的困惑"（不可观测） |
| **Binary** | 结果必须是 pass/fail/na，不允许 "partially pass" | "tutor 部分解释了..." |
| **Persona-aware** | 标明适用的 persona，避免对 advanced 学生强求 beginner 标准 | 对 advanced 要求"使用类比解释基础概念" |

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
    {"tag": "scaffolding",            "type": "non_critical",      "template": "Tutor breaks implementation into testable increments (e.g., signal generation -> position sizing -> execution)"},
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
    {"tag": "scaffolding",            "type": "critical",          "template": "Tutor guides student through a systematic debugging methodology (hypothesis -> test -> verify)"},
    {"tag": "code_teaching",          "type": "non_critical",      "template": "Tutor teaches a transferable debugging technique (e.g., print debugging, bisection, assertion)"},
    {"tag": "domain_accuracy",        "type": "critical",          "template": "Tutor correctly identifies {specific_bug} as the root cause"}
  ]
}
```

### 1.9 实施计划

| 步骤 | 内容 | 产出 | 工时估算 |
|------|------|------|---------|
| 1 | 为 8 个 ICC 任务编写 checklist | 8 x ~10 条 = ~80 条 | 4h |
| 2 | 实现 `checklist_eval.py` | 评估模块 | 4h |
| 3 | 在 ICC 数据上跑 checklist + D1-D7 对比 | cross-judge r 对比报告 | 2h |
| 4 | 验证 r 提升后，为全部 65 任务编写 checklist | 65 x ~10 条 = ~650 条 | 16h |
| 5 | 整合到 `tutor_conv_geval.py` 和 `score_report.py` | 生产代码 | 4h |
| **合计** | | | **~30h** |

### 1.10 预期效果与验证标准

| 指标 | 当前值 | 目标值 | 验证方法 |
|------|--------|--------|---------|
| Cross-judge Pearson r (Tutor) | 0.676 | >= 0.75 | 在 ICC 数据上对比 r_old vs r_new |
| Cross-judge weighted Kappa (Tutor) | 未计算 | >= 0.60 | 方案四提供 baseline，方案一验证提升 |
| Cohen's d (Tutor) | 1.69 | >= 1.50 (不大幅下降) | 确认区分度未因锚定而压缩 |
| Checklist judge 一致性 | -- | >= 0.80 (pass/fail Cohen's Kappa) | 两个 judge 的 checklist 判定一致性 |
| Checklist F1 (vs human) | -- | >= 0.80 | 与 human calibration (方案五) 交叉验证 |

> **注意**：这里的 checklist 一致性指标使用 Cohen's Kappa 而非 simple agreement rate，以避免 TutorBench 未校正偶然一致的缺陷。pass/fail 二分类的偶然一致概率较高，Kappa 能更真实地反映 judge 间的实质性一致。

### 1.11 与 score_report 的集成

在 Tutor 7D Breakdown section 后新增：

```markdown
### Tutor Behavioral Checklist

| Item | Tag | Type | Weight | Verdict | Justification |
|------|-----|------|--------|---------|---------------|
| TC01 | error_diagnosis | critical | +5 | PASS | "In turn 4, tutor correctly identified..." |
| TC02 | autonomy_preservation | neg_critical | -5 | PASS | "Tutor guided student through steps..." |
| TC03 | domain_accuracy | critical | +5 | FAIL | "Tutor stated Sharpe = mean/std but..." |
| TC04 | language_adaptation | non_critical | +1 | N/A | "Student did not ask about this concept" |

**Checklist Score**: 0.750 (5/10 applicable items passed, weighted)
**Tutor Score (blended)**: 0.60 x D1-D7 (0.645) + 0.40 x Checklist (0.750) = **0.687**
```

---

## ~~方案二~~ [已作废，被 6D 重构取代]

---

## 方案三：对话长度退化分析

> **目标**：检测 tutor 是否在长对话中教学质量退化（MathTutorBench 核心发现）
> **优先级**：P2 | **工程成本**：低 | **论文价值**：中（interesting finding section）

### 3.1 背景

MathTutorBench 的关键发现（§5.1）：

> "Tutoring becomes more challenging in longer dialogs, where simpler questioning strategies (e.g., basic Socratic prompting) begin to fail."

MathTutorBench 报告了对话长度与教学质量的显著负相关（r = -0.34, p < 0.01），且退化在 scaffolding 和 formative assessment 维度最为明显。

我们的数据中 B02 的高 CV (18.6%) 和 S03 的分数波动可能部分来自对话长度差异。如果能证明"Sonnet 在长对话中退化更少"，这是一个有力的区分度来源。

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

#### 3.2.3 统计显著性检验

退化分析应包含统计检验，避免 MathTutorBench 仅报告相关系数而无效应量的问题：

```python
# 1. 配对 t 检验或 Wilcoxon signed-rank（前后半段差异是否显著）
from scipy.stats import wilcoxon
stat, p_value = wilcoxon(first_half_scores_list, second_half_scores_list)

# 2. 效应量（前后半段差距大小）
degradation_d = compute_cohens_d(first_half_scores_list, second_half_scores_list)

# 3. Pearson r（对话轮次数 vs Tutor 总分的相关性）
r, p = pearsonr(turn_counts, tutor_scores)
```

### 3.3 报告输出

在 score_report 的 Tutor section 增加：

```markdown
### Tutor Degradation Analysis

| Dimension | First Half | Second Half | Delta | Trend |
|-----------|-----------|------------|-------|-------|
| D1_level_detection | 0.72 | 0.68 | -0.04 | slight decline |
| D2_language_adaptation | 0.65 | 0.63 | -0.02 | stable |
| D3_scaffolding | 0.70 | 0.55 | -0.15 | notable decline |
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
| 4 | 在 ICC 数据上验证 | -- |

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

## ~~方案四~~ [已完成]

> 已实施。代码：`bench/scripts/analyze_judge_comparison.py`。报告：`bench/*MD/v5.0/judge_comparison_analysis_v2.md`。
>
> 关键发现：Tutor Cohen's d = +1.694 [1.17, 2.49]（区分度稳健）；Cross-judge Pearson r = 0.676（Tutor 最低）；旧 D1-D3 维度间 r > 0.92（维度不独立，驱动了 6D 重构决策）。

---

## 方案五：Human Calibration 实验

> **目标**：提供 Tutor 评分有效性的硬证据，回答 reviewer 核心质疑："你的 LLM judge 可靠吗？"
> **优先级**：P0 | **工程成本**：中（需要领域专家） | **论文价值**：极高

### 5.1 为什么这是 P0

stage_report S8.2 已列出 P0 项："Human calibration (30-50 samples)"。对标三篇论文：

| 论文 | 人类验证方案 | 统计指标 | 我们的评价 |
|------|-----------|---------|-----------|
| TutorBench | 69 experts, 2475 criteria | Simple agreement rate 0.78, F1 0.82 | **规模大但指标粗糙**：未用 Kappa 校正偶然一致，pass/fail base rate ~70% 时随机猜测即达 ~58% agreement |
| MathTutorBench | Expert/novice distinction | Pairwise ranking accuracy 0.84 | **间接验证**：不是人类直接给分，而是判断 reward model 能否区分 expert vs novice |
| EduBench | 3 annotators, 198 samples | Kendall's W 0.63, MAE | **方向正确但指标单一**：W 只衡量排序一致性，不量化绝对分数偏差 |

**没有这个实验，reviewer 可以合理质疑所有基于 Tutor 分数的结论。**

方案五是 Kappa / Agreement Rate 等绝对一致性指标的正确使用场景：Sonnet judge vs 人类专家之间不存在先验的系统偏差假设，Kappa 衡量的才是真正的"评估者间一致性"而非"校准差异"。方案四的 cross-judge Kappa（0.231-0.733）作为 baseline 参考。

### 5.2 实验设计

#### 5.2.1 样本选择

```
总样本量：40 个对话（覆盖分数分布）
|- 高分段 (Tutor >= 0.70): 12 个
|- 中分段 (0.45 <= Tutor < 0.70): 16 个
|- 低分段 (Tutor < 0.45): 12 个
+- 覆盖：至少 5 个 task category, 2 个 agent, 3 个 persona level
```

**选样标准**：
- 排除 Tutor=0.0 的 API 超时异常值
- 优先选择两个 judge 分歧大（Delta > 0.15）的对话——这些是最需要人类校准的
- 确保 sonnet agent 和 haiku agent 各占一半

#### 5.2.2 评分方式

每个对话由 **3 位独立评分员** 评估，评分员构成：

| 角色 | 人数 | 负责内容 | 资格要求 |
|------|------|---------|---------|
| 量化金融专家 | 1-2 人 | D4 Domain Accuracy, D5 Code Teaching, D7 Safety | 量化分析/交易经验 3+ 年 |
| 教育学/教学设计专家 | 1-2 人 | D1 Level Detection, D2 Language, D3 Scaffolding, D6 Empathy | 教学设计/教育技术背景 |
| 混合背景 | 1 人 | 全部维度 | 有教学经验的量化从业者 |

#### 5.2.3 评分内容

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

### 5.3 评分工具

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

### 5.4 分析指标（统一框架）

基于方案四的统计指标体系，Human Calibration 的分析指标分为三层，解决三篇论文各自的统计缺陷：

#### 5.4.1 核心指标（论文正文必须报告）

| 指标 | 计算对象 | 目标值 | 对标 | 为什么选这个指标 |
|------|---------|--------|------|----------------|
| **Weighted Cohen's Kappa** | LLM-judge vs Human majority | >= 0.60 | TutorBench 未报告 Kappa | 校正偶然一致，reviewer 最认的"金标准"一致性指标 |
| **Krippendorff's Alpha** | 3 位人类评分员 | 报告实际值 | EduBench 未使用 | 支持多 rater、有序评分、缺失值的通用一致性指标 |
| **Simple Agreement Rate** (threshold=1) | LLM-judge vs Human | >= 0.75 | TutorBench baseline: 0.78 | 与 TutorBench 形成直接可比性 |
| **Kendall tau** | LLM-judge ranks vs Human ranks | >= 0.60 | EduBench 用 Kendall's W | 排序一致性，不受绝对分数偏移影响 |

**核心叙事策略**：

先报告 Agreement Rate（与 TutorBench 可比），再报告 Kappa（展示我们更严谨），最后报告 Alpha（多 rater 场景）。三个指标从"向后兼容"到"方法论创新"递进。

```python
# 1. LLM-judge vs Human: Weighted Cohen's Kappa（核心）
kappa = compute_weighted_kappa(llm_scores_binned, human_majority_binned)
# 目标: >= 0.60 ("较好")

# 2. 人类间：Krippendorff's Alpha（核心）
import krippendorff
alpha = krippendorff.alpha(
    reliability_data=[rater1_scores, rater2_scores, rater3_scores],
    level_of_measurement='ordinal'
)
# 报告实际值，与 EduBench Kendall's W 对标

# 3. LLM-judge vs Human: Agreement Rate（TutorBench 可比）
agreement = compute_agreement_rate(llm_scores, human_majority_scores, threshold=1.0)
# 目标: >= 0.75 (TutorBench baseline: 0.78)

# 4. 排序一致性
tau, p = kendalltau(llm_ranks, human_ranks)
# 目标: tau >= 0.60
```

#### 5.4.2 补充指标（论文正文或附录）

| 指标 | 计算对象 | 用途 |
|------|---------|------|
| **逐维度 Kappa** | LLM vs Human, per D1-D7 | 识别哪些维度 LLM-judge 最不可靠 |
| **Bootstrap 95% CI** | 所有核心指标 | 量化估计的不确定性 |
| **Checklist F1** (如方案一已实施) | LLM checklist vs Human checklist | pass/fail 判定的精确度和召回率 |
| **Checklist Cohen's Kappa** (如方案一已实施) | LLM checklist vs Human checklist | 校正偶然一致的 checklist 一致性 |

```python
# 5. 逐维度分析
per_dim_kappa = {
    dim: compute_weighted_kappa(llm_dim_scores, human_dim_scores)
    for dim in DIMENSIONS
}
# 目的：识别哪些维度的 LLM-judge 最不可靠

# 6. Bootstrap CI for Kappa
kappa_point, kappa_lo, kappa_hi = bootstrap_ci(
    llm_scores_binned, human_majority_binned,
    lambda a, b: cohen_kappa_score(a, b, weights='quadratic')
)

# 7. 如果方案一已实施：Checklist Kappa（比 F1 更严谨）
checklist_kappa = cohen_kappa_score(
    llm_checklist_verdicts, human_checklist_verdicts
)
# 目标: >= 0.65
# TutorBench 报告 F1=0.82 但未报告 Kappa；我们同时报告两者
```

#### 5.4.3 不报告的指标（避免冗余）

| 指标 | 不报告的原因 |
|------|------------|
| MAE | 我们的分数是 0-1 归一化的加权聚合分，MAE 的解释性不如 Kappa |
| Kendall's W | 我们只有 2 个 LLM-judge + 3 个人类，W 不如 Alpha 通用 |
| Pearson r (LLM vs Human) | Pearson r 不校正偶然一致且对异常值敏感，Kappa 严格优于它 |

### 5.5 Agreement Rate 计算方法

保留与 TutorBench 可比的 agreement rate，但明确标注其统计局限：

```python
def compute_agreement_rate(scores_a: list[float], scores_b: list[float], threshold: float = 1.0) -> float:
    """
    计算两组分数的 agreement rate（与 TutorBench 可比）。

    对于 1-10 scale，threshold=1 意味着允许 1 分误差。
    对于 pass/fail，直接比较是否相同。

    注意：此指标未校正偶然一致。论文中应与 Kappa 一起报告，
    不应单独作为一致性结论的唯一依据。
    """
    assert len(scores_a) == len(scores_b)
    agreements = sum(1 for a, b in zip(scores_a, scores_b) if abs(a - b) <= threshold)
    return agreements / len(scores_a)
```

### 5.6 预期结果与论文叙事

#### 5.6.1 如果结果达标（Kappa >= 0.60, Agreement >= 0.75）

> "Human calibration on 40 conversations (120 expert ratings across 7 dimensions) yields weighted Cohen's Kappa = {kappa} [95% CI: {lo}, {hi}] between the LLM-judge and human majority vote, indicating {interpretation} agreement after chance correction. For comparison, simple agreement rate = {agreement} (threshold: 1 point on 1-10 scale), comparable to TutorBench's reported 0.78 -- but our Kappa provides a stricter, chance-corrected measure that TutorBench did not report. Inter-human reliability (Krippendorff's alpha = {alpha}) establishes the human ceiling. Per-dimension analysis reveals strongest agreement on D4 Domain Accuracy (kappa = {k4}) and weakest on D6 Empathetic Response (kappa = {k6}), consistent with the general finding that factual dimensions are easier to judge reliably than affective ones."

#### 5.6.2 如果结果未达标（Kappa < 0.60）

分析原因，可能的应对：
- 如果某个维度 Kappa 特别低 -> 修改该维度的 rubric 使其更 operationalizable
- 如果整体偏低 -> 增加 judge runs（3->5），或使用更强的 judge model
- 如果 checklist Kappa 高但 D1-D7 Kappa 低 -> 更加依赖 checklist（调大 blend beta）
- 如果 Agreement 高但 Kappa 低 -> 说明偶然一致占比大，印证了 TutorBench 高 agreement 可能虚高的判断

### 5.7 实施计划

| 步骤 | 内容 | 时间 | 依赖 |
|------|------|------|------|
| 1 | 确定评分员（3 人） | 1 周 | -- |
| 2 | 选择 40 个对话样本 | 1 天 | ICC 数据 |
| 3 | 准备评分材料（导出对话 + 6D rubric + 评分表） | 1 天 | 步骤 2 + 6D rubric 完成 |
| 4 | 评分员培训 + 2 个校准样本 | 0.5 天 | 步骤 3 |
| 5 | 正式评分（40 对话 x **6 维度** x 3 人） | 2-3 天 | 步骤 4 |
| 6 | 收集数据 + 计算完整指标矩阵（Kappa, Alpha, Agreement, CI, 逐维度分析） | 1 天 | 步骤 5 |
| 7 | 撰写论文 section | 1 天 | 步骤 6 |
| **合计** | | **~2 周** | |

### 5.8 评分员补偿与招募

| 角色 | 招募渠道 | 工作量 | 建议补偿 |
|------|---------|--------|---------|
| 量化金融专家 | 团队内部 / 行业联系人 | 40 对话 x 15-20 min = 10-13h | 按市场价或学术合作 |
| 教育学专家 | 大学教育学院 / 在线招募 | 40 对话 x 15-20 min = 10-13h | 按市场价或挂名致谢 |
| 混合背景 | 有教学经验的量化从业者 | 40 对话 x 15-20 min = 10-13h | 按市场价或挂名致谢 |

---

## 附录：快速实施检查清单

### 方案一：Task-Specific Checklist（1-2 周）
- [ ] 定义 checklist JSON schema，更新 task JSON validation
- [ ] 更新 `persona_scope` 为四象限 tag（A/B/C/D）
- [ ] 更新 tag taxonomy：旧 `scaffolding` → `finance_adaptation` / `code_adaptation` / `pedagogical_method`
- [ ] 为 8 个 ICC 任务编写 checklist
- [ ] 实现 `checklist_eval.py`（judge prompt + scoring logic）
- [ ] 在 ICC 数据上跑 checklist，计算 cross-judge Kappa 提升
- [ ] 确认 Kappa 提升后，为剩余任务编写 checklist
- [ ] 整合到 `tutor_conv_geval.py` 和 `score_report.py`

### 方案三：对话长度退化分析（1-2 天）
- [ ] 实现对话分段逻辑
- [ ] 实现 degradation index 计算（含 Wilcoxon 检验和效应量）
- [ ] 在 score_report 增加 degradation section（6D 维度名）
- [ ] 在 ICC 数据上生成 degradation 分析

### 方案五：Human Calibration（2 周，可与方案一并行）
- [ ] 招募 3 位评分员
- [ ] 选择 40 个对话样本，导出评分材料
- [ ] 准备 **6D** rubric 评分表
- [ ] 评分员培训 + 校准
- [ ] 正式评分收集（40 对话 × 6 维度 × 3 人）
- [ ] 计算完整指标矩阵（Kappa, Alpha, Agreement, tau, Bootstrap CI, 逐维度分析）
- [ ] 撰写论文 evaluation reliability section
