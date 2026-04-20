# 学生模拟器稳定性实验方案

> Date: 2026-04-20
> Scope: StudentSimulator prompt 设计稳定性验证 + 最优底层模型选型
> Source files: `server/core/student_sim.py`, `server/config/prompt_config.py`, `server/schemas.py`
> Experiment code: `experiments/student_sim_stability/`
> Status: 方案定稿

---

## 1. 实验目标

学生模拟器（StudentSimulator）是 QuantTutorBench 的核心组件之一：它通过 LLM 扮演不同 persona 的学生，与 tutor agent 进行多轮对话。模拟器的稳定性直接影响 benchmark 结果的可信度。

本实验验证以下问题：

1. **当前 prompt 设计是否足够稳定？** 同一 persona 在多次运行中是否保持一致的知识水平、提问风格、情绪表现。
2. **哪个底层模型最稳定？** 在 3 个候选模型中，哪个最能稳定地"扮演"指定 persona。
3. **Persona 定义是否有效？** 不同 persona 是否真正产生有意义的行为差异，还是 prompt 被模型忽略。

---

## 2. 稳定性的四个维度

| 维度 | 代号 | 定义 | 为什么重要 |
|------|------|------|-----------|
| 角色一致性 | D1 | 每条学生消息是否符合 persona 的知识边界、行为规则、情绪特征 | 如果模拟器不遵循 persona，benchmark 对 tutor "适应不同学生"能力的评估就无效 |
| 跨运行可复现性 | D2 | 相同 (task, persona, model) 多次运行，对话走向是否一致 | 如果方差过大，单次 benchmark 结果不具备统计意义 |
| 跨模型稳定性 | D3 | 换不同 LLM 做模拟器，persona 核心特征是否被保持 | 决定模型选型：如果某模型无法稳定执行 persona，就不适合做模拟器 |
| 对话漂移 | D4 | 长对话中，persona 保真度是否随轮次退化 | 如果后期轮次学生"变成另一个人"或开始像 co-teacher，评估结果会被污染 |

---

## 3. 实验变量

### 3.1 自变量

| 变量 | 取值 | 说明 |
|------|------|------|
| Student 模型 | `openai/gpt-5.4`, `anthropic/claude-sonnet-4-6`, `google/gemini-3.1-pro-preview` | 三个 standard/premium 档模型，覆盖主流供应商 |
| Persona | `finance_veteran` + `double_novice`（主对），`developer_crossover` + `fullstack_practitioner`（备对） | 选择差异最大的 persona 对，最能暴露稳定性问题 |
| Persona 开关 | 有 persona / 无 persona（对照组） | 验证 persona 定义的有效性 |

### 3.2 控制变量

| 变量 | 固定值 | 理由 |
|------|--------|------|
| 对话轮次 | 8 轮 | 固定轮次便于逐轮对比和漂移分析 |
| 重复次数 | 3 次/组合 | 最小可比较重复数，在成本和统计意义间平衡 |
| Student temperature | 0.0 | 与生产环境一致（`EVAL_JUDGE_TEMPERATURE = 0.0`） |
| Tutor 模型 | `openai/gpt-4.1-nano` | 便宜（$0.10/1M in）但质量验证合格的模型，实时响应学生 |
| Tutor temperature | 0.0 和 1.0（消融实验） | 见 §4.4 温度消融设计 |
| Judge | Claude Code（会话内） | 免费，由 Claude 在实验完成后批量评估 |

### 3.3 为什么 Tutor 模型选择不影响实验结论

Tutor 是**控制变量**而非自变量——所有 student model 面对同一个 tutor 模型。

- Tutor 质量影响所有 student model 的**绝对分数**，但不影响**相对排名**
- D2（可复现性）：同一 tutor 模型在 t=0.3 下行为高度一致，引入的噪声有限
- D3（跨模型一致性）：tutor 固定，差异只来自 student sim
- 前提条件：tutor 质量需满足"能产生合理教学对话"的最低门槛

`gpt-4.1-nano` 通过了 6 项教学场景测试（SMA 解释、Sharpe 代码、look-ahead bias 诊断、OHLCV 概念、mean reversion、log returns），确认满足门槛。

### 3.4 任务选择

从 6 个 category 中各选 1 个代表性任务：

| Task ID | Category | 难度 | 选择理由 | Persona 对 |
|---------|----------|------|---------|-----------|
| I01_implement_sma | implementation | easy | 基础编码任务，教学流程线性 | developer_crossover, fullstack_practitioner |
| S03_mean_reversion_research | strategy | medium | 金融概念讨论，暴露知识边界差异 | **finance_veteran, double_novice** |
| X02_lookahead | debug | easy | 代码理解 + 概念解释 | developer_crossover, fullstack_practitioner |
| D05_return_computation | data_analysis | medium | 数学公式，测试 anxiety 表现 | **finance_veteran, double_novice** |
| A03_sharpe_misconception | adversarial | medium | 误导场景，测试压力下行为一致性 | **finance_veteran, double_novice** |
| E01_build_ma_system | end_to_end | medium | 长流程，最能检测对话漂移 | developer_crossover, fullstack_practitioner |

---

## 4. 实验设计

### 4.1 对话生成（Live Tutor）

**每个 trial 的对话流程**：

```
[Student Opening] → Tutor(gpt-4.1-nano)[1] → Student(被测模型)[1] → Tutor[2] → Student[2] → ... → Tutor[8]
```

- Student Opening：来自 task JSON 的 `student_openings[persona_id]`
- Tutor[1..8]：`gpt-4.1-nano` 根据上下文实时生成回复
- Student[1..7]：由 StudentSimulator 实时生成（被测对象）
- 所有 API 调用通过 OpenRouter

### 4.2 实验矩阵

| | gpt-5.4 | sonnet-4.6 | gemini-3.1-pro |
|---|---------|------------|----------------|
| I01 × developer_crossover | ×3 | ×3 | ×3 |
| I01 × fullstack_practitioner | ×3 | ×3 | ×3 |
| S03 × finance_veteran | ×3 | ×3 | ×3 |
| S03 × double_novice | ×3 | ×3 | ×3 |
| X02 × developer_crossover | ×3 | ×3 | ×3 |
| X02 × fullstack_practitioner | ×3 | ×3 | ×3 |
| D05 × finance_veteran | ×3 | ×3 | ×3 |
| D05 × double_novice | ×3 | ×3 | ×3 |
| A03 × finance_veteran | ×3 | ×3 | ×3 |
| A03 × double_novice | ×3 | ×3 | ×3 |
| E01 × developer_crossover | ×3 | ×3 | ×3 |
| E01 × fullstack_practitioner | ×3 | ×3 | ×3 |

**主实验**：12 组合 × 3 模型 × 3 重复 × 2 温度 = **216 trials**

### 4.3 对照组：无 Persona

- 去掉 persona 定义，替换为通用学生描述
- 使用同一 tutor 模型实时对话（tutor t=0）
- 每个 (task, model) 跑 1 次

**对照组**：6 tasks × 3 模型 = **18 trials**

### 4.4 Tutor 温度消融实验

**目的**：验证学生模拟器的稳定性是否独立于 tutor 回复的多样性。

**实验逻辑**：
- 即使 temperature=0，模型回复也不保证完全一致（存在 provider 侧采样、batch 顺序等因素）
- 但 temperature=1 的回复**一定比** temperature=0 更分散
- 如果 student sim 在两种条件下 D1/D4 分数相近 → student sim 对 tutor 变异 robust

**设计**：每个 (task, persona, student_model, repeat) 组合跑两次，分别使用：
- `tutor_t=0`：tutor temperature=0.0（最一致的 tutor 回复）
- `tutor_t=1`：tutor temperature=1.0（最分散的 tutor 回复）

**结果解读**：

| tutor t=0 D1/D4 | tutor t=1 D1/D4 | 结论 |
|-----------------|-----------------|------|
| 高 | 高 | Student sim 稳定且对 tutor 变异 robust |
| 高 | 低 | Student sim 稳定但依赖 tutor 一致性（脆弱） |
| 低 | 低 | Student sim 本身不稳定，与 tutor 无关 |

### 4.5 并行执行

脚本使用 `concurrent.futures.ThreadPoolExecutor` 并行执行 trials：
- 所有 trial 独立，可完全并行
- 默认 worker 数 = 6（避免 OpenRouter 限速）
- 每个 trial 独立保存，支持断点续跑

---

## 5. 评估方法

### 5.1 D1 — 角色一致性 (Persona Adherence)

**评估粒度**：逐条学生消息

**评判方式**：Claude Code 会话内评估

**评分维度**（均 1-5 分）：

| 子维度 | 评判标准 |
|--------|---------|
| `knowledge_boundary` | 学生是否尊重 known/unknown concepts 的边界？ |
| `emotional_tone` | 语气是否匹配 emotional_profile？ |
| `behavioral_rules` | persona 中的行为规则是否被遵循？ |
| `overall` | 综合角色一致性 |

**采样策略**：每个模型取第一次运行的全部学生消息评估。

### 5.2 D2 — 跨运行可复现性 (Cross-run Reproducibility)

**评估粒度**：对话组（同一 task × persona × model 的 3 次运行）

**评分维度**（均 1-5 分）：`topic_trajectory`, `knowledge_display`, `emotional_consistency`, `question_patterns`, `overall_reproducibility`

### 5.3 D3 — 跨模型稳定性 (Cross-model Consistency)

**评估粒度**：模型组（同一 task × persona 下 3 个模型各 1 次运行）

**评分维度**（均 1-5 分）：`knowledge_boundary_preserved`, `emotional_profile_preserved`, `behavioral_rules_preserved`, `persona_distinguishability`, `overall_cross_model`

**额外输出**：`best_model`, `worst_model`

### 5.4 D4 — 对话漂移检测 (Drift Detection)

**评估粒度**：整段对话的逐轮分析

**逐轮评分**：`persona_fidelity`(1-5), `knowledge_leak`(0-3), `co_teacher_drift`(0-2)

**对话级汇总**：`overall_drift_score`(1-5), `drift_onset_turn`

### 5.5 对照组 — Persona 区分度

**评分**：`distinctiveness`(1-5), `persona_value_add`(文本)

---

## 6. 实验规模与成本

### 6.1 Trial 统计

| 阶段 | Trials | Student 消息数 | Tutor 消息数 |
|------|--------|--------------|-------------|
| 主实验（tutor t=0） | 108 | 756 | 864 |
| 主实验（tutor t=1） | 108 | 756 | 864 |
| 对照组 | 18 | 126 | 144 |
| **合计** | **234** | **1,638** | **1,872** |

**已知系统行为**：`EwanLLMClient.a_generate` 未实现 `schema` 参数，导致 `StudentSimulator._generate_parsed` 每次先尝试 schema 调用（得到纯文本）再 fallback 到 text 调用，每条 student 消息实际产生 **2 次 API 调用**。这是**生产环境的既有行为**，实验保持一致不做修改。

### 6.2 OpenRouter 成本估算

基于 OpenRouter 实时定价（2026-04-20 查询），按每次 student 调用 ~2,000 input / ~150 output tokens，tutor 调用 ~1,500 input / ~300 output tokens 估算。Student 调用数含 schema fallback 的 ×2 因子：

| 组件 | 模型 | Input $/1M | Output $/1M | 实际调用次数 | 估算成本 |
|------|------|-----------|------------|---------|---------|
| Student sim | openai/gpt-5.4 | $2.50 | $15.00 | 546 × 2 = 1,092 | $3.96 |
| Student sim | anthropic/claude-sonnet-4-6 | $3.00 | $15.00 | 546 × 2 = 1,092 | $4.52 |
| Student sim | google/gemini-3.1-pro-preview | $2.00 | $12.00 | 546 × 2 = 1,092 | $3.17 |
| **Tutor** | **openai/gpt-4.1-nano** | **$0.10** | **$0.40** | **1,872** | **$0.50** |
| Judge | Claude Code 会话内 | — | — | ~600 | **$0** |
| **总计** | | | | **~5,148** | **~$12.15** |

### 6.3 Judge 评估工作量（Claude Code 会话内）

| 维度 | 评估单元数 | 说明 |
|------|-----------|------|
| D1 | ~252 | 每模型 × 温度取 1 次运行 × 7 条 |
| D2 | ~72 | 12 组合 × 3 模型 × 2 温度 |
| D3 | ~24 | 12 组合 × 2 温度 |
| D4 | ~234 | 全部 234 个对话 |
| 对照组 | ~18 | 18 组 |
| **合计** | **~600** | |

---

## 7. 执行流程

```
┌──────────────────────────────────────────────────────────┐
│  Step 1: 运行对话生成脚本                                   │
│  python -m experiments.student_sim_stability.run generate │
│  (并行执行，Tutor + Student 均调 OpenRouter，~$5.50)         │
│  ↓                                                         │
│  Step 2: Claude Code 批量评估                               │
│  读取对话文件 → D1-D4 评估 → 写入 all_evaluations.json        │
│  ↓                                                         │
│  Step 3: 生成报告                                           │
│  python -m experiments.student_sim_stability.run report     │
│  (本地运行，无 API 调用)                                      │
└──────────────────────────────────────────────────────────┘
```

### 7.1 CLI 命令

```bash
cd bench

# 查看实验规模
python -m experiments.student_sim_stability.run dry-run

# 生成全部对话（并行执行）
python -m experiments.student_sim_stability.run generate

# 生成报告（评估完成后）
python -m experiments.student_sim_stability.run report
```

### 7.2 断点续跑

- 每个 trial 完成后立即写入 `conversations/{key}.json`
- 重新运行时自动跳过已有文件
- 如需重跑某个 trial，删除对应 JSON 文件即可

### 7.3 Temperature 说明

生产环境 student sim temperature = 0.0（通过 `EVAL_JUDGE_TEMPERATURE`）。本实验保持一致。

这意味着：
- D2（可复现性）预期较高；若仍低，说明模型在 t=0 下存在内在随机性，本身是重要发现
- D4（漂移）在 t=0 下仍可能出现，因为漂移源于上下文积累而非温度

---

## 8. 产出物

### 8.1 原始数据

```
results/
├── conversations/
│   ├── live__I01_implement_sma__developer_crossover__gpt-5.4__r0.json
│   ├── control__D05_return_computation__finance_veteran__gemini-3.1-pro-preview__r0.json
│   └── ...
└── evaluations/
    └── all_evaluations.json
```

### 8.2 统计报告（HTML）

```
results/report/
├── stability_report.html
└── stability_stats.json
```

| 章节 | 内容 |
|------|------|
| 1. Overview | 模型综合稳定性排名表（composite = mean(D1, D2, D4)） |
| 2. D1 | (model × persona) 热力图 |
| 3. D2 | (model) 可复现性表 |
| 4. D3 | 按 task 一致性得分，Best/Worst 模型投票 |
| 5. D4 | 逐轮 fidelity 曲线，漂移起始轮次 |
| 6. Control | Persona 区分度得分 |
| 7. Conclusion | 最稳定模型推荐 + prompt 改进建议 |

---

## 9. 预期结论模板

### Q1: 当前 prompt 设计是否足够稳定？

- D1 ≥ 4.0 且 D4 ≥ 4.0 → **稳定，可用于 benchmark**
- D1 ∈ [3.0, 4.0) → 基本稳定，特定维度需加强
- D1 < 3.0 → 不稳定，需重大 prompt 改进

### Q2: 最稳定的模型？

- Composite 排名第一推荐为 `SIMULATOR_DEFAULT_MODEL`
- 差距 < 0.2 时综合成本考虑

### Q3: Persona 定义是否有效？

- 对照组 distinctiveness ≥ 4.0 → **有效**
- < 3.0 → 需重新设计 persona prompt

### Q4: 是否存在漂移？

- 漂移起始轮次 ≥ 6 → 可接受
- < 4 → 需要 prompt 架构级改进

---

## 10. 风险与限制

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| gpt-4.1-nano tutor 质量不够 | 对话退化影响所有 student model | 已通过 6 项场景测试验证；影响绝对分但不影响排名 |
| gemini-3.1-pro-preview 模型名变化 | Trial 失败 | 运行前 dry-run 验证；config.py 可快速替换 |
| 3 次重复不够 | D2 方差估计不可靠 | 可扩展到 5 次；t=0 下方差已较小 |
| 8 轮太短 | D4 漏检长对话漂移 | E01 可扩展到 15 轮 |
| OpenRouter 限速 | 并行执行被阻 | 默认 6 worker，可配置降低 |
