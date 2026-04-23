# QR/QP 评分体系重构方案

> **创建日期**: 2026-04-21
> **最后更新**: 2026-04-22（文档收敛：合并 rubric 摘要 + 批量验证计划）
> **相关代码**: `bench/server/eval/rubrics/rubric_qp.json`、`bench/server/eval/rubrics/rubric_qr.json`、`bench/server/eval/ewan_eval/process_metrics.py`
> **本文档范围**: QP/QR 架构决策、代码变更清单、rubric 摘要、批量验证计划。本文已合并旧评分参考、prompt 审计、优化路线图、rubric 定义文档中的有效内容。

---

## 1. 达成的共识

### 1.1 核心设计原则

1. **三轴分离**：QR 评产出正确性，QP 评过程质量，Tutor 6D 评教学质量。同一行为不跨轴惩罚。
2. **量纲统一**：所有 LLM judge 维度统一为 1-5 整数，采用 6D 渐进式 rubric（Score 1 天花板检查 → Score 3 基线 → Score 5 卓越）。
3. **最小维度原则**：不刻意追求维度数量，每个维度必须有明确的、不可替代的评估目标。
4. **程序化优先**：能用确定性算法评的不用 LLM；LLM 只评需要语义理解的维度。
5. **expected_outcome 退役**：代码逻辑中完全移除 EO 字段的使用，改用 `required_capabilities` (RC)。任务 JSON 文件中保留 EO 字段不删除。
6. **D4 留在 Tutor 6D**：经真实执行数据验证，D4 评估"tutor 说的对不对"（口头准确性），QR LLM 评估"产出齐不齐、能不能用"（交付物质量），两者可独立变化，不应合并。
7. **统一归一化**：所有 1-5 LLM judge（含 Tutor 6D）归一化为 `(score - 1) / (max_score - 1)` → {0, 0.25, 0.5, 0.75, 1.0}。Score 1 = 失败 = 0 分，Score 3 = 基线 = 0.5，Score 5 = 卓越 = 1.0。替代旧 `/max_score` 公式。Breaking change：历史 Tutor 6D 分数不可直接对比。
8. **`_SCORE_PROMPT` 泛化**：原 `conv_geval.py` 模板硬编码 "Educational Analyst" 角色和 tutor-specific 规则。改为参数化 `{role}` / `{rules}` / `{context}`，使同一类可服务 Tutor 6D / QP / QR。
9. **context 统一构建**：`ConversationalTestCase` 中 `turns` 改为通用 `context: str`。Tutor 6D 传纯对话，QR/QP 传各自的 enriched context。新建 `context_builder.py` 集中所有 context 构建逻辑。纯对话通过统一出口格式化。
10. **死代码清理**：`ConversationalTestCase.expected_outcome` / `scenario` / `user_description` 从未注入 prompt，全部移除。旧 `_scoring_utils.py` 中 `normalize_10pt` / `SCALE_10PT` 随旧 result_judge 一起删除。

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
| debugging_competence (LLM) | QP LLM (code_process 子项) | 被新维度 problem_solving 覆盖 |
| step_efficiency (LLM 子项) | QP LLM | redundancy_avoidance / logical_sequencing 与新 LLM 维度重叠 |
| process_reasonableness (全模块) | QP LLM | 被 task_planning + problem_solving 替代，整文件删除 |

> **code_process LLM 部分全部移除**，仅保留程序化 code_lifecycle 四项（iterative_refinement, test_before_deliver, error_recovery, code_evolution）。
> **process_reasonableness.py 整文件删除**（problem_decomposition / execution_soundness / error_handling 被新维度覆盖）。
> **custom_conv_metrics.py 整文件删除**（role_adherence / topic_adherence 评估器及其辅助函数）。

#### 新增的维度

| 维度 | 归属 | 评估方式 | 说明 |
|------|------|----------|------|
| task_planning | QP LLM | 1-5 conv_geval | 任务拆解规划能力：问题分解、路径选择、验证点设置 |
| problem_solving | QP LLM | 1-5 conv_geval | 问题解决能力：错误诊断、策略调整、意外结果追查。无触发点时 N/A 排除 |

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
└── LLM Layer: reformed result_judge（改造，通过 EwanConvGEval 统一调用）
    ├── 评估范围：任务交付物的完整性与可用性
    ├── 输入：context_builder 构建（task + RC + tool outputs + workspace + agent summary）
    ├── 量纲：1-5 整数 → (score-1)/4 → {0, 0.25, 0.5, 0.75, 1.0}
    ├── rubric 来源：rubrics/rubric_qr.json
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
└── LLM（重构，通过 EwanConvGEval 统一调用）
    ├── task_planning       — 1-5 conv_geval（新），rubrics/rubric_qp.json
    └── problem_solving     — 1-5 conv_geval（新），无显式错误时 score=None 排除
```

**权重方案**（Phase 1 先定初始值，Phase 2 数据验证后调整）：

| 维度 | 类型 | 初始权重 |
|------|------|----------|
| tool_usage | 程序化 | 0.20 |
| action_economy | 程序化 | 0.15 |
| code_lifecycle | 程序化 | 0.15 |
| task_planning | LLM | 0.25 |
| problem_solving | LLM | 0.25 |

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

### 2.4 统一 LLM Judge Pipeline

```
Layer 1: Rubric 数据
├── rubrics/rubric_6d.json   — Tutor 6D（增加 role/rules 顶层字段）
├── rubrics/rubric_qr.json   — result_judge（新建）
└── rubrics/rubric_qp.json   — task_planning + problem_solving（新建）

Layer 2: 构建器
└── rubric_builder.py
    ├── load_6d_rubric()            — 6D 专用（保留，含 [KNOWN]/[UNKNOWN] 注入）
    ├── load_rubric(name)           — 通用加载（新增）
    └── build_eval_params(rubric, dim) → {role, rules, criteria, max_score}（新增）

Layer 3: Context 构建
└── context_builder.py（新建）
    ├── format_conversation(conv)                — 统一对话格式化出口
    ├── build_tutor_context(conv, enriched, dim) — 6D：纯对话 + D4/D6 enrichment + strip_code
    ├── build_result_judge_context(...)           — QR：task + RC + tool outputs + workspace + summary
    ├── build_task_planning_context(...)          — QP：enriched conv + RC checklist
    └── build_problem_solving_context(...)        — QP：enriched conv + 错误摘要

Layer 4: 评估类（统一）
└── conv_geval.py — EwanConvGEval
    ├── 构造参数：name, criteria, role, rules, model, max_score
    ├── _SCORE_PROMPT：{role} / {rubric} / {rules} / {context} / {max_score}
    ├── 归一化：(score - 1) / (max_score - 1)
    └── a_measure(EvalTestCase(context=str)) → float

Layer 5: 编排器
├── tutor_conv_geval.py  — Tutor 6D（适配新接口，逻辑不变）
├── process_metrics.py   — QP 5 维度（重写）
└── result_judge.py      — QR（重写为 EwanConvGEval 薄壳）
```

**result_judge rules（从旧 guidelines 精简）**:

1. 方法中立：评交付物是否满足 RC，不评具体方法是否匹配预期。
2. 实现正确性 ≠ 结果好坏：评方法论正确性，不评结果是否 "好看"。Debug 任务只评 bug 是否修复。
3. 只计执行过的代码：`[FAIL]` 或无执行记录的代码不计入交付物。
4. 只评最终交付物：中间产物、调试输出不扣分。过程由 QP 评。

### 2.5 总分公式（不变）

```
Task Score = 0.70 × QAI + 0.30 × TEI
QAI = 0.50 × QR + 0.50 × QP
TEI = average(D1, D2, D3, D4, D5, D6)  # 按类别权重矩阵
```

---

## 3. 代码变更清单

### 3.1 删除（整文件）

| 文件 | 说明 |
|------|------|
| `process_reasonableness.py` | 被 task_planning + problem_solving 替代 |
| `custom_conv_metrics.py` | role_adherence + topic_adherence 移除 |
| `score_report.py` | Markdown 报告路径删除，前端/调用方直接读取 `score.json` |

### 3.2 删除（文件内部分代码）

| 文件 | 删除内容 | 说明 |
|------|----------|------|
| `code_process.py` | LLM 全部：debugging_competence / incremental_development / code_explanation_quality / `async_eval_code_process_llm()` | 仅保留程序化 code_lifecycle 4 指标 |
| `process_metrics.py` | step_efficiency LLM / process_alignment / role_adherence / topic_adherence / process_reasonableness 调用 + 旧 `_QP_DIMENSION_WEIGHTS` + 旧 `_build_process_tasks_for_model()` | 重写为 5 维度 |
| `conv_geval.py` | `ConversationalTestCase.expected_outcome` / `scenario` / `user_description`（从未注入 prompt 的死字段） | 简化为 `EvalTestCase(context: str)` |
| `_scoring_utils.py` | `normalize_10pt` / `denormalize_10pt` / `SCALE_10PT` | 旧 1-10 归一化不再使用 |

### 3.3 改造

| 文件 | 变更 | 说明 |
|------|------|------|
| `conv_geval.py` | **核心改造** | `_SCORE_PROMPT` 泛化（`{role}` / `{rules}` / `{context}`）+ 归一化 `(score-1)/(max_score-1)` + `EvalTestCase` 替代 `ConversationalTestCase` + 构造函数增加 `role` / `rules` 参数 |
| `rubric_6d.json` | **增加字段** | 顶层增加 `role` + `rules`（从 `_SCORE_PROMPT` 硬编码中抽出） |
| `rubric_builder.py` | **扩展** | 新增 `load_rubric(name)` + `build_eval_params(rubric, dim_name)` 通用函数 |
| `result_judge.py` | **重写** | 自建 prompt + 多模型并行 → 薄壳复用 EwanConvGEval + context_builder。删除 `_build_result_judge_prompt` / `_SUB_WEIGHTS` / 自建 LLM 调用 |
| `process_metrics.py` | **重写** | 5 维度权重 + LLM 维度通过 EwanConvGEval 统一调用 + `_compute_action_economy` 保留为独立程序化维度 |
| `code_process.py` | **大幅简化** | 移除 LLM 全部，`async_eval_code_process()` 改为纯程序化（导出为 code_lifecycle） |
| `tutor_conv_geval.py` | **适配** | 改用 `EvalTestCase(context=...)` + 从 rubric JSON 读取 `role`/`rules` 传给 EwanConvGEval + 移除 `expected_outcome` 传参链 |
| `pipeline.py` | **适配** | `_run_result_judge()` 传 RC 替代 EO + `_run_process_metrics()` 传 enriched_conv / RC + `_run_tutor_eval()` 移除 `expected_outcome` |

### 3.4 新增

| 文件 | 说明 |
|------|------|
| `rubrics/rubric_qr.json` | result_judge rubric（1-5 渐进式 + RC 锚定 + 4 条 rules） |
| `rubrics/rubric_qp.json` | task_planning + problem_solving rubric（1-5 渐进式） |
| `context_builder.py` | 统一 context 构建 pipeline：`format_conversation()` / `build_tutor_context()` / `build_result_judge_context()` / `build_task_planning_context()` / `build_problem_solving_context()` |

### 3.5 不改动

| 文件 | 原因 |
|------|------|
| `schemas.py` | `expected_outcome` 字段保留，仅确保代码不再读取 |
| `code_eval.py` | 三层程序化评估逻辑不变 |
| `tool_usage.py` | 程序化工具评分不变 |
| `prompt_config.py` | 已使用 RC，不使用 EO |
| `enrichment.py` | `enrich_conversation_with_tools()` 保留不变 |
| 任务 JSON 文件 | EO 字段保留不删除 |

---

## 4. 实施阶段

### Phase 1 — Rubric 编写 + 代码重构（已完成）

1. 编写 task_planning rubric（1-5 渐进式）
2. 编写 problem_solving rubric（1-5 渐进式，N/A 机制）
3. 改写 result_judge rubric（1-5 渐进式 + RC 锚定）
4. 人工审核 rubric（逐条对照，确认边界无重叠）
5. 代码实施（按第 3 节清单）

### Phase 2 — 批量验证（依赖 50+ 任务数据）

**触发条件**：至少 50 个完整任务的评分结果。

| 检验项 | 目标状态 | 问题信号 | 后续动作 |
|--------|----------|----------|----------|
| 分数分布 | 各维度分布覆盖 1-5 全域 | 聚集在 3-4 | 下移或重写 rubric 锚点，提高区分度 |
| 跨模型一致性 | 同任务不同 eval model 标准差 < 0.5 | 标准差过大 | 收紧 prompt 规则，增加 evidence 要求 |
| 排名稳定性 | 同 agent 多次运行排名 Kendall τ > 0.8 | 排名不稳定 | 增加 judge runs 或调整聚合方式 |
| 维度相关性 | 新维度间 Pearson r < 0.85 | 两维度 r > 0.85 | 合并或删除其中一个维度 |
| 区分有效性 | 好/差任务在各维度 p < 0.05 | 无显著差异 | 重写低信号维度或调低权重 |
| QP/Tutor 边界 | 教学质量与过程质量不重复惩罚 | D2/D3/D5 与 QP 高相关 | 重新检查维度边界与 prompt scope |

Phase 2 产出：

- 维度间 Pearson 相关性热力图
- 每个维度的分数分布直方图
- 同任务跨模型标准差统计
- 同 agent 多次运行排名稳定性矩阵
- 维度与任务成功率/人工标签的相关性表

### Phase 3 — 数据驱动微调

| 场景 | 动作 |
|------|------|
| 某维度与任务成功率相关最高 | 上调权重，但保持 QR/QP/Tutor 三轴边界 |
| 两个维度 r > 0.85 | 合并或删除其中一个，优先保留更可解释、成本更低的维度 |
| LLM judge 与程序化评分系统性分歧 > 0.3 | 检查 prompt、context 构建或程序化阈值 |
| 某维度分布持续聚集 | 重写 rubric 锚点，提高低分/高分条件的可观察性 |
| problem_solving 触发率过低 | 确认错误检测逻辑是否漏掉 runtime/tool failures |

---

## 5. Rubric 定义摘要

> **Source of truth**: 运行时以 `rubrics/rubric_qp.json` 和 `rubrics/rubric_qr.json` 为准；本节用于人类评审和设计讨论。

### 5.1 task_planning（QP LLM — 任务规划能力）

**输入**：enriched conversation + RC checklist。
**评估范围**：任务拆解、依赖顺序、路径选择。不评 step count（action_economy）、tool selection（tool_usage）、验证执行质量（code_lifecycle），也不评交付物是否满足 RC（result_judge 负责）。

| Score | 定义 |
|-------|------|
| 1 — Failure | 执行顺序违反数据或步骤依赖，例如未获取数据即开始计算。 |
| 2 — Below Expectations | 有初步分解，但阶段划分粗糙，或遗漏了该任务显然必要的阶段。 |
| 3 — Adequate | 任务被拆分为可识别阶段；阶段依赖顺序正确；分解粒度与任务复杂度基本匹配。 |
| 4 — Good | 满足 3 分，且路径选择体现任务理解，例如先探索数据结构再决定分析方法。 |
| 5 — Excellent | 满足 4 分，且识别并处理 RC 未显式列出但完成任务必需的依赖或子问题。 |

### 5.2 problem_solving（QP LLM — 问题解决能力）

**输入**：enriched conversation。
**触发条件**：execution trace 中存在显式错误（tool call failures / runtime errors）。无触发点时 `score=None`，从 QP 聚合中排除。
**评估范围**：错误识别、根因诊断、修复方向和验证。不评最终产出正确性。

| Score | 定义 |
|-------|------|
| 1 — Failure | 对错误无响应、持续未定位根因，或处理方式使问题恶化。 |
| 2 — Below Expectations | 识别到错误，但诊断方向错误，或修复尝试未指向实际问题。 |
| 3 — Adequate | 识别错误；做出合理修复尝试；修复方向指向实际问题。 |
| 4 — Good | 满足 3 分，且诊断触及根因而非只处理表面症状。 |
| 5 — Excellent | 满足 4 分，且展现系统性问题解决，例如控制变量排查或主动验证修复未引入新问题。 |

### 5.3 result_judge（QR LLM — 交付物质量）

**输入**：tool logs + workspace files + agent summary + RC checklist。
**评估范围**：交付物完整性与可用性。不评过程质量（QP 负责），不评教学表达（Tutor 6D 负责）。

核心规则：

1. 方法中立：评交付物是否满足 RC，不要求匹配某个预设方法。
2. 实现正确性不等于结果好坏：策略亏钱、统计不显著，不等于实现错误。
3. 只计执行过的代码：未成功执行的代码、草稿、计划不计入交付物。
4. 只评最终交付物：中间调试输出和探索步骤由 QP 评价。

| Score | 定义 |
|-------|------|
| 1 — Failure | 关键 RC 交付物缺失，或交付物完全不可用。 |
| 2 — Below Expectations | 部分 RC 项已完成，但存在重要缺失，或交付物有显著正确性/可用性问题。 |
| 3 — Adequate | RC 列出的交付物均已产出；功能正确、可直接使用；数值结果与工具实际输出一致。 |
| 4 — Good | 满足 3 分，且交付物体现细节质量，例如边界情况、格式、代码结构较好。 |
| 5 — Excellent | 满足 4 分，且覆盖 RC 未显式要求但对任务完整性有实质价值的内容。 |

---

## 6. Deferred 事项

| 事项 | 原因 | 触发条件 |
|------|------|----------|
| key_decisions 摘要提取 | 需人工审核，耗时 | Phase 3 数据验证后评估 ROI |
| reference_trace 扩展 | 当前覆盖有限 | 同上 |
| ~~code_process 重构为纯程序化~~ | ~~已确认~~ | **已决定**：code_process LLM 全部移除 |
| ~~归一化方案~~ | ~~待确认~~ | **已决定**：`(score-1)/(max_score-1)` 全量适用（含 Tutor 6D） |
| ~~_SCORE_PROMPT 泛化~~ | ~~待确认~~ | **已决定**：参数化 role/rules/context，不影响 Tutor 6D 行为 |
| ~~result_judge guidelines 处理~~ | ~~待分析~~ | **已决定**：6 → 4 条精简，放入 rubric_qr.json rules 字段 |
| D5 user_description 注入 | rubric 有 `inject_user_description: true` 但代码从未实现 | 需单独讨论是否根据不同情绪画像设置不同 rubric |
| 具体权重数值 | 初始值为经验估计 | Phase 2 数据产出后校准 |
