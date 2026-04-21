# QR/QP 评分体系重构方案

> **创建日期**: 2026-04-21
> **依赖文档**: `qp_qr_scoring_reference.md`（当前状态）、`qp_qr_llm_judge_audit.md`（问题清单）、`qp_qr_optimization_roadmap.md`（原始路线图）
> **本文档范围**: 架构决策 + 代码变更清单。**不包含** 具体 rubric 文本（另行文档）。

---

## 1. 达成的共识

### 1.1 核心设计原则

1. **三轴分离**：QR 评产出正确性，QP 评过程质量，Tutor 6D 评教学质量。同一行为不跨轴惩罚。
2. **量纲统一**：所有 LLM judge 维度统一为 1-5 整数，采用 6D 渐进式 rubric（Score 1 天花板检查 → Score 3 基线 → Score 5 卓越）。
3. **最小维度原则**：不刻意追求维度数量，每个维度必须有明确的、不可替代的评估目标。
4. **程序化优先**：能用确定性算法评的不用 LLM；LLM 只评需要语义理解的维度。
5. **expected_outcome 退役**：代码逻辑中完全移除 EO 字段的使用，改用 `required_capabilities` (RC)。任务 JSON 文件中保留 EO 字段不删除。

### 1.2 维度变更全表

#### 删除的维度

| 维度 | 原属 | 删除原因 |
|------|------|----------|
| QR completeness + correctness (1-10) | QR LLM | 被增强版 D4 替代 |
| process_alignment | QP LLM | 强依赖 reference_trace，无 reference 时硬零；与 action_economy 功能重叠 |
| role_adherence | QP LLM | 属于教学质量，已被 Tutor D1/D5 覆盖 |
| topic_adherence | QP LLM | 95%+ 任务得满分，无区分度 |
| code_explanation_quality | QP LLM (code_process 子项) | 属于教学质量，已被 Tutor D2 覆盖 |
| incremental_development (LLM) | QP LLM (code_process 子项) | 可由 code_lifecycle 程序化指标覆盖 |
| step_efficiency (LLM 子项) | QP LLM | redundancy_avoidance / logical_sequencing 与 proactive/reactive reasoning 重叠 |

#### 新增的维度

| 维度 | 归属 | 评估方式 | 说明 |
|------|------|----------|------|
| proactive_reasoning | QP LLM | 1-5 conv_geval | 主动推理能力：问题分解、前瞻规划、主动检验 |
| reactive_reasoning | QP LLM | 1-5 conv_geval | 被动推理能力：错误诊断、策略调整、学生反馈响应 |

#### 迁移的维度

| 维度 | 原属 | 新属 | 变化 |
|------|------|------|------|
| D4 (instructional_accuracy) | Tutor 6D | QR LLM | 增强为 QR 的 LLM judge，注入 RC 作为结构化评估锚点 |

### 1.3 EO → RC 迁移

| 位置 | 当前使用 EO 的方式 | 变更 |
|------|-------------------|------|
| `result_judge.py` prompt | 注入 EO 作为验收标准 | **整个模块被替换**（增强版 D4 取代） |
| `tutor_conv_geval.py` | 传入 `ConversationalTestCase.expected_outcome` | **删除传入**（prompt 模板从未使用此字段，是死代码） |
| `pipeline.py` `_run_result_judge()` | 传入 `task.ground_truth.expected_outcome` | **删除此调用路径**（result_judge 整体被替换） |
| `pipeline.py` `_run_tutor_eval()` | 传入 `task.ground_truth.expected_outcome` | **删除传入** |
| `prompt_config.py` `build_scenario()` | 不使用 EO，使用 RC 构建学习目标 | **无需改动** |
| `schemas.py` `GroundTruth` | `expected_outcome: str` 字段定义 | **保留字段定义**，仅确保代码逻辑不再读取 |

---

## 2. 重构后架构

### 2.1 QR（Quant Result）— 产出正确性

```
QR Score
├── Programmatic Layer（当前代码不变）
│   ├── eval_script（任务特定脚本，部分任务有）
│   └── code_eval
│       ├── Layer A: 静态分析 (15%)
│       ├── Layer B: 执行分析 (35%)
│       └── Layer C: 输出验证 (50%)，依赖 reference["key_results"]
│
└── LLM Layer: enhanced_D4（新）
    ├── 评估范围：对话中 tutor 陈述的事实准确性
    ├── 输入：enriched conversation + RC 结构化锚点
    ├── 量纲：1-5 整数 → 归一化 0-1
    └── rubric 风格：6D 渐进式（Score 1/3/5 锚点 + 2/4 中间态）
```

**QR 融合逻辑**（`pipeline.py` 第 315-361 行的 dampening blending）保持不变，仅将 `llm_judge_score` 的来源从 `result_judge` 替换为 `enhanced_D4`。

### 2.2 QP（Quant Process）— 过程质量

```
QP Score
├── Programmatic（保留，代码不变）
│   ├── tool_usage          — 工具选择/使用评分
│   ├── action_economy      — 步数比 vs reference（阈值校准）
│   └── code_lifecycle      — 4 子指标（iterative_refinement, test_before_deliver,
│                              error_recovery, code_evolution）
│
└── LLM（重构）
    ├── proactive_reasoning  — 1-5 conv_geval（新）
    └── reactive_reasoning   — 1-5 conv_geval（新）
```

**权重方案**（Phase 1 先定初始值，Phase 2 数据验证后调整）：

| 维度 | 类型 | 初始权重 |
|------|------|----------|
| tool_usage | 程序化 | 0.20 |
| action_economy | 程序化 | 0.15 |
| code_lifecycle | 程序化 | 0.15 |
| proactive_reasoning | LLM | 0.25 |
| reactive_reasoning | LLM | 0.25 |

> code_lifecycle 对 conceptual_qa 等无代码任务 score=None，权重自动归一化排除。

### 2.3 Tutor 6D（教学质量）

```
Tutor Score
├── D1 — Emotional Intelligence
├── D2 — Code Adaptation
├── D3 — Pedagogical Method（strip_code 预处理保留）
├── D5 — Adaptive Communication
└── D6 — Safety & Boundaries
```

D4 移出后，Tutor 从 6D 变为 5D。`compute_tutor_score()` 中的权重和类别矩阵需要相应调整。

### 2.4 总分公式（不变）

```
Task Score = 0.70 × QAI + 0.30 × TEI
QAI = 0.50 × QR + 0.50 × QP
TEI = average(D1, D2, D3, D5, D6)  # 按类别权重矩阵
```

---

## 3. 代码变更清单

### 3.1 删除 / 替换

| 文件 | 变更 | 说明 |
|------|------|------|
| `result_judge.py` | **整体替换** | 当前的 completeness/correctness 1-10 评估被 enhanced_D4 替代。可保留文件但重写内容为新的 D4-based QR LLM judge |
| `process_metrics.py` `_QP_DIMENSION_WEIGHTS` | **重写** | 从 7 维度 → 5 维度（tool_usage, action_economy, code_lifecycle, proactive_reasoning, reactive_reasoning） |
| `process_metrics.py` `_build_process_tasks_for_model()` | **重写** | 移除 step_efficiency / process_alignment / role_adherence / topic_adherence 的任务构建；新增 proactive/reactive reasoning 任务 |
| `process_metrics.py` `evaluate_all_process_metrics()` | **调整** | 适配新维度集 |
| `custom_conv_metrics.py` | **删除 role/topic adherence 函数** | 不再被调用 |
| `code_process.py` LLM 子维度 | **移除** code_explanation_quality 和 incremental_development | 仅保留 debugging_competence；或整体改为纯程序化（code_lifecycle 已覆盖） |

### 3.2 新增 / 迁移

| 文件 | 变更 | 说明 |
|------|------|------|
| `result_judge.py`（或新文件） | **新增** enhanced_D4 QR LLM judge | 基于 conv_geval 模板；rubric 注入 RC 作为结构化上下文；使用 enriched conversation（同原 D4） |
| `process_metrics.py` | **新增** proactive_reasoning / reactive_reasoning 异步评估函数 | 使用 conv_geval prompt 模板 |
| rubrics/ | **新增** proactive_reasoning.json, reactive_reasoning.json | rubric 定义文件（待编写） |

### 3.3 修改

| 文件 | 变更 | 说明 |
|------|------|------|
| `pipeline.py` `_run_result_judge()` | **修改** | 调用 enhanced_D4 替代原 result_judge；传入 enriched_conversation + RC；移除 expected_outcome 参数 |
| `pipeline.py` `_run_tutor_eval()` | **修改** | 移除 `expected_outcome` 传入；从 `evaluate_tutor_dimensions()` 调用中排除 D4 |
| `tutor_conv_geval.py` | **修改** | 移除 D4 相关定义（`_ENRICHED_DIMS_FULL` 中删除 D4）；调整 `CATEGORY_DIMENSION_WEIGHTS`；调整 `compute_tutor_score()` |
| `tutor_conv_geval.py` `create_tutor_geval_metrics()` | **修改** | 不再为 D4 创建 metric |
| `conv_geval.py` `_SCORE_PROMPT` | **可能修改** | QR enhanced_D4 可能需要增强版 prompt 模板（注入 RC 上下文）；或在 result_judge.py 中使用独立模板 |
| `scoring.py` | **检查** | `compute_task_score()` 通过 `compute_tutor_score()` 计算 TEI，D4 移除后权重自动重新归一化，可能无需改动 |
| `score_report.py` | **修改** | 输出格式适配新维度名称 |
| `rubrics/rubric_6d.json` | **修改** | 移除 D4 定义（或标记为 moved_to_qr） |

### 3.4 不改动

| 文件 | 原因 |
|------|------|
| `schemas.py` | `expected_outcome` 字段保留，仅确保代码不再读取 |
| `code_eval.py` | 三层程序化评估逻辑不变 |
| `tool_usage.py` | 程序化工具评分不变 |
| `prompt_config.py` | 已使用 RC，不使用 EO |
| 任务 JSON 文件 | EO 字段保留不删除 |

---

## 4. conv_geval 模板与 RC 注入

enhanced_D4 需要 task-specific 上下文（RC），但当前 `conv_geval.py` 的 `_SCORE_PROMPT` 仅支持 `{rubric}` + `{turns}` 注入。

**方案**：在 enhanced_D4 的评估入口（可能在 result_judge.py 中）使用独立的 prompt 模板，注入：

```
# Required Capabilities (评估锚点)
{required_capabilities}

# Enriched Conversation
{enriched_turns}

# Rubric
{rubric}
```

不修改通用 `conv_geval.py` 模板，避免影响 Tutor 维度。

---

## 5. 实施阶段

### Phase 1 — Rubric 编写 + 代码重构（当前）

1. 编写 proactive_reasoning rubric（1-5 渐进式）
2. 编写 reactive_reasoning rubric（1-5 渐进式）
3. 编写 enhanced_D4 rubric（1-5 渐进式 + RC 上下文注入机制）
4. 人工审核 rubric（逐条对照，确认边界无重叠）
5. 代码实施（按第 3 节清单）

### Phase 2 — 批量验证（依赖 50+ 任务数据）

- 分数分布：各维度覆盖 1-5 全域
- 跨模型一致性：同任务标准差 < 0.5
- 排名稳定性：Kendall τ > 0.8
- 维度相关性：新维度间无冗余（r < 0.85）
- 区分有效性：好/差任务在各维度 p < 0.05

### Phase 3 — 数据驱动微调

- 权重优化（基于维度与任务成功率的相关性）
- rubric 锚点调整（基于分数分布）
- key_decisions / reference 体系优化（当前 deferred）

---

## 6. Deferred 事项

| 事项 | 原因 | 触发条件 |
|------|------|----------|
| key_decisions 摘要提取 | 需人工审核，耗时 | Phase 3 数据验证后评估 ROI |
| reference_trace 扩展 | 当前覆盖有限 | 同上 |
| code_process 重构为纯程序化 | 需确认 debugging_competence 是否被 reactive_reasoning 覆盖 | Phase 1 rubric 编写时确认 |
| 具体权重数值 | 初始值为经验估计 | Phase 2 数据产出后校准 |
