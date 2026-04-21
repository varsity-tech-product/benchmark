# QP/QR 评分体系优化路线图

> **创建日期**: 2026-04-21
> **依赖文档**: `qp_qr_scoring_reference.md`（当前状态）、`qp_qr_llm_judge_audit.md`（问题清单）
> **核心原则**: 先统一量纲+细化 rubric → 再跑批量数据 → 再基于数据微调

---

## 设计原则

1. **量纲统一**：所有 LLM judge 子维度统一为 1-5 整数，参照 6D rubric 的 Score 1/3/5 底线锚点 + Score 2/4 提升逻辑
2. **边界清晰**：QP 只评过程能力，Tutor 6D 只评教学质量，不允许同一行为跨越两个评分体系被惩罚两次
3. **先稳定后优化**：rubric 改完后先人工审核，等批量任务数据产出后再验证有效性、调整权重

---

## Phase 0 — 当前基线（已完成）

- [x] 6D Tutor rubric 人工审核通过
- [x] QP/QR 框架代码运行中
- [x] `qp_qr_llm_judge_audit.md` 问题梳理完成
- [x] `qp_qr_scoring_reference.md` 当前状态文档化
- [ ] 批量任务结果（等待同事产出）

---

## Phase 1 — 量纲统一 + Rubric 细化（当前阶段）

**目标**：所有 LLM judge 维度改为 1-5 整数量纲，写出明确的 rubric 锚点，消除已知的跨边界重叠。

**不改动**：代码架构、权重（等数据验证后再调）、程序化评分逻辑。

### 1.1 改动清单

| 维度 | 改动类型 | 核心变化 |
|------|----------|----------|
| QR completeness | 量纲 + rubric | 1-10 → 1-5，写5档锚点 |
| QR correctness | 量纲 + 重命名 + rubric | 1-10 → 1-5，重命名为 `output_usability` |
| QP process_reasonableness | 量纲 + rubric + 修复bias | {0-1} → 1-5；修复 error_handling "无错误自动满分" 问题 |
| QP step_efficiency (LLM部分) | 量纲 + rubric | {0-1} → 1-5 |
| QP code_process (LLM部分) | 量纲 + rubric + 移除子项 | {0-1} → 1-5；移除 `code_explanation_quality` |
| QP role_adherence | 量纲 + rubric + 重新定义 | 1-10 → 1-5；`persona_consistency` 缩窄定义；`boundary_maintenance` 排除金融安全边界 |
| QP topic_adherence | 权重调整（Phase 1.5） | 权重暂不动，rubric 保持现状；等数据确认无区分度后再降权/删除 |

### 1.2 code_explanation_quality 的处置

移除出 `code_process`，**不并入 D3**（D3 已有互动点、结构化等维度，避免扩散）。

其评估的"代码解释质量"本质上是 D2（Code Adaptation）的延伸——D2 评估代码适配度，自然包含解释是否到位。记录为 **D2 的已覆盖行为**，不需要额外维度。

### 1.3 交付物

- [ ] 新版 prompt 文本（本文档 Phase 1 Detail 章节）
- [ ] 人工审核（逐条对照 6D rubric 风格确认）
- [ ] 提交代码改动 PR

---

## Phase 1.5 — 人工审核

- 逐条走查新 rubric，确认 1/3/5 分锚点无歧义
- 确认 QP / Tutor 6D 边界无新的重叠引入
- 确认 code_process 移除 `code_explanation_quality` 后权重归一化正确

---

## Phase 2 — 批量结果验证（依赖数据）

**触发条件**：同事产出至少 50 个完整任务的评分结果。

### 2.1 验证指标

| 检验项 | 目标状态 | 问题信号 |
|--------|----------|----------|
| 分数分布 | 各维度分布覆盖 1-5 全域 | 聚集在 3-4 → rubric 锚点需下移 |
| 跨模型一致性 | 同任务不同 eval model 标准差 < 0.5 | 标准差过大 → prompt 需更明确 |
| 排名稳定性 | 同 agent 多次运行排名 Kendall τ > 0.8 | 不稳定 → 增加 num_judge_runs |
| 维度相关性 | `code_explanation` 已移除，D3 与旧 code_process 相关 < 0.7 | 相关 > 0.85 → 确认冗余已消除 |
| 区分有效性 | 好任务 vs 差任务在各维度 p < 0.05 | 无显著差 → 该维度需重写 |
| topic_adherence | 预期 95%+ 样本得 5 分 | 确认无区分度后执行降权 |

### 2.2 产出

- 相关性热力图（维度间 Pearson r）
- 分数分布直方图（per dimension）
- 排名稳定性矩阵

---

## Phase 3 — 基于数据的微调

**典型场景及对应动作**：

| 场景 | 动作 |
|------|------|
| topic_adherence 95%+ 得 5 分 | 权重从 0.10 → 0.03，释放权重补给 process_reasonableness |
| 某维度与任务成功率相关最高 | 上调权重 |
| 两维度 r > 0.85 | 合并或删除其中一个 |
| LLM judge 与程序化系统性分歧 > 0.3 | 检查 prompt 或重新校准阻尼参数 |
| 某维度分布持续聚集 | 重写 rubric 锚点，提高区分度 |

---

## 附录：Phase 1 详细修改计划

详见本文档下方 [Phase 1 Detailed Changes](#phase-1-detailed-changes) 章节，
或参考独立文档 `qp_qr_phase1_rubric_v2.md`（Phase 1 完成后创建）。
