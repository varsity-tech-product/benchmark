# QR/QP 评分体系重构方案

> **创建日期**: 2026-04-21
> **最后更新**: 2026-04-21（回退 D4 迁移决策）
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
6. **D4 留在 Tutor 6D**：经真实执行数据验证，D4 评估"tutor 说的对不对"（口头准确性），QR LLM 评估"产出齐不齐、能不能用"（交付物质量），两者可独立变化，不应合并。

### 1.2 维度变更全表

#### 删除的维度

| 维度 | 原属 | 删除原因 |
|------|------|----------|
| QR completeness + correctness (1-10) | QR LLM | 改造为 1-5 量纲 + RC 锚定（非删除，是改造） |
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

#### 改造的维度

| 维度 | 原状 | 新状 | 变化 |
|------|------|------|------|
| QR LLM judge (result_judge) | completeness + correctness, 1-10, EO 锚定 | 1-5 量纲, RC 锚定, 6D 渐进式 rubric | 量纲统一 + 评估锚点替换 |
| D4 (instructional_accuracy) | Tutor 6D 内，rubric 已定义 | **保持不变，留在 Tutor 6D** | 无改动 |

### 1.3 EO → RC 迁移

| 位置 | 当前使用 EO 的方式 | 变更 |
|------|-------------------|------|
| `result_judge.py` prompt | 注入 EO 作为验收标准 | **改为注入 RC**（结构化 checklist 替代叙述性 EO） |
| `tutor_conv_geval.py` | 传入 `ConversationalTestCase.expected_outcome` | **删除传入**（prompt 模板从未使用此字段，是死代码） |
| `pipeline.py` `_run_result_judge()` | 传入 `task.ground_truth.expected_outcome` | **改为传入 RC** |
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
└── LLM Layer: reformed result_judge（改造）
    ├── 评估范围：任务交付物的完整性与可用性
    ├── 输入：工具日志 + workspace 文件 + agent summary + RC 锚点
    ├── 量纲：1-5 整数 → 归一化 0-1（原 1-10）
    └── rubric 风格：6D 渐进式（Score 1/3/5 锚点 + 2/4 中间态）
```

**QR 融合逻辑**（`pipeline.py` 第 315-361 行的 dampening blending）保持不变，`llm_judge_score` 来源仍为 result_judge（改造后）。

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

### 2.3 Tutor 6D（教学质量）— 不变

```
Tutor Score
├── D1 — Finance Adaptation
├── D2 — Code Adaptation
├── D3 — Pedagogical Method（strip_code 预处理保留）
├── D4 — Instructional Accuracy（保留）
├── D5 — Empathetic Response
└── D6 — Safety & Boundaries
```

Tutor 维持完整 6D 结构，无维度变更。

### 2.4 总分公式（不变）

```
Task Score = 0.70 × QAI + 0.30 × TEI
QAI = 0.50 × QR + 0.50 × QP
TEI = average(D1, D2, D3, D4, D5, D6)  # 按类别权重矩阵
```

---

## 3. 代码变更清单

### 3.1 删除

| 文件 | 变更 | 说明 |
|------|------|------|
| `process_metrics.py` 中 step_efficiency / process_alignment / role_adherence / topic_adherence | **移除** | 不再被新维度集包含 |
| `custom_conv_metrics.py` 中 role/topic adherence 函数 | **删除** | 不再被调用 |
| `code_process.py` 中 code_explanation_quality 和 incremental_development | **移除** | 已被 Tutor D2 和 code_lifecycle 覆盖 |

### 3.2 改造

| 文件 | 变更 | 说明 |
|------|------|------|
| `result_judge.py` | **改造** | 量纲 1-10 → 1-5；EO 锚定 → RC 锚定；rubric 改写为 6D 渐进式 |
| `process_metrics.py` `_QP_DIMENSION_WEIGHTS` | **重写** | 从 7 维度 → 5 维度（tool_usage, action_economy, code_lifecycle, proactive_reasoning, reactive_reasoning） |
| `process_metrics.py` `_build_process_tasks_for_model()` | **重写** | 移除旧维度任务构建；新增 proactive/reactive reasoning 任务 |

### 3.3 新增

| 文件 | 变更 | 说明 |
|------|------|------|
| `process_metrics.py` | **新增** proactive_reasoning / reactive_reasoning 异步评估函数 | 使用 conv_geval prompt 模板 |
| rubrics/ | **新增** proactive_reasoning.json, reactive_reasoning.json | rubric 定义文件（待编写） |

### 3.4 修改

| 文件 | 变更 | 说明 |
|------|------|------|
| `pipeline.py` `_run_result_judge()` | **修改** | 传入 RC 替代 EO |
| `pipeline.py` `_run_tutor_eval()` | **修改** | 移除 `expected_outcome` 传入（死代码清理） |
| `process_metrics.py` `evaluate_all_process_metrics()` | **调整** | 适配新维度集 |
| `score_report.py` | **修改** | 输出格式适配新维度名称 |

### 3.5 不改动

| 文件 | 原因 |
|------|------|
| `schemas.py` | `expected_outcome` 字段保留，仅确保代码不再读取 |
| `code_eval.py` | 三层程序化评估逻辑不变 |
| `tool_usage.py` | 程序化工具评分不变 |
| `prompt_config.py` | 已使用 RC，不使用 EO |
| `tutor_conv_geval.py` | D4 保留，Tutor 6D 不变 |
| `rubrics/rubric_6d.json` | D4 rubric 保留不动 |
| `conv_geval.py` | 通用模板不变 |
| 任务 JSON 文件 | EO 字段保留不删除 |

---

## 4. 实施阶段

### Phase 1 — Rubric 编写 + 代码重构（当前）

1. 编写 proactive_reasoning rubric（1-5 渐进式）
2. 编写 reactive_reasoning rubric（1-5 渐进式）
3. 改写 result_judge rubric（1-5 渐进式 + RC 锚定）
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

## 5. Deferred 事项

| 事项 | 原因 | 触发条件 |
|------|------|----------|
| key_decisions 摘要提取 | 需人工审核，耗时 | Phase 3 数据验证后评估 ROI |
| reference_trace 扩展 | 当前覆盖有限 | 同上 |
| code_process 重构为纯程序化 | 需确认 debugging_competence 是否被 reactive_reasoning 覆盖 | Phase 1 rubric 编写时确认 |
| 具体权重数值 | 初始值为经验估计 | Phase 2 数据产出后校准 |
