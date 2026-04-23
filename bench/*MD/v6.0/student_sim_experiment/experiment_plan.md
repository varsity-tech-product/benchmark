# 学生模拟器稳定性实验方案

> Date: 2026-04-20
> Scope: StudentSimulator prompt 设计稳定性验证 + 最优底层模型选型
> Source files: `server/core/student_sim.py`, `server/config/prompt_config.py`, `server/schemas.py`
> Experiment code: `experiments/student_sim_stability/`
> Status: 方案定稿

> 2026-04-23 issue83 update: 当前实现已改为 36 条 control、252 段总对话、
> opener-excluded D1 sampled count 252，并新增 P1/B1 controlled validation
> 以及三评分模型 agreement。下文保留的 234/288 等数字是原始方案历史记录，
> 不再作为当前 expected artifact counts。

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
| Judge | `anthropic/claude-sonnet-4-6` | 通过 OpenRouter 批量评估 `judge_inputs/*.json` |

### 3.3 为什么 Tutor 模型选择不影响实验结论

Tutor 是**控制变量**而非自变量——所有 student model 面对同一个 tutor 模型。

- Tutor 质量影响所有 student model 的**绝对分数**，但不影响**相对排名**
- D2（可复现性）：同一 tutor 模型在相同 temperature 条件下行为较一致，引入的噪声有限
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
| 低 | 高 | 异常——tutor 变异反而帮助 student 入戏。可能原因：t=0 的 tutor 过于模式化导致 student 也模式化脱戏，t=1 的 tutor 更自然反而激活了 persona 行为 |

**措辞约定**：温度消融不能声称"隔离"了 tutor 变量（t=0 也非完全确定性），准确表述为"低 tutor 方差 vs 高 tutor 方差"条件下的对比。

### 4.5 并行执行

脚本使用 `concurrent.futures.ThreadPoolExecutor` 并行执行 trials：
- 所有 trial 独立，可完全并行
- 代码默认 `MAX_WORKERS = 100`；实际运行建议显式传 `-w 6` 起步，再按 OpenRouter 限速情况调高
- 每个 trial 独立保存，支持断点续跑

---

## 5. 评估方法

### 5.1 D1 — 角色一致性 (Persona Adherence)

**评估粒度**：逐条学生消息

**评判方式**：OpenRouter judge 批量评估，默认 `anthropic/claude-sonnet-4-6`

**评分维度**（均 1-5 分）：

| 子维度 | 评判标准 |
|--------|---------|
| `knowledge_boundary` | 学生是否尊重 known/unknown concepts 的边界？ |
| `emotional_tone` | 语气是否匹配 emotional_profile？ |
| `behavioral_rules` | persona 中的行为规则是否被遵循？ |
| `overall` | 综合角色一致性 |

**采样策略**：仅采样 live tutor t=0 组，每个模型取第一次运行（repeat 0）的全部学生消息。理由：D1 测试的是 persona 内在一致性，与 tutor 多样性无关，温度消融的作用体现在 D2 中。
- 采样量 = 12 组合 × 3 模型 × 1 repeat × 8 student messages = **288 单元**

### 5.2 D2 — 跨运行可复现性 (Cross-run Reproducibility)

**评估粒度**：对话组（同一 task × persona × model × tutor_t 的 3 次运行）

**评分维度**（均 1-5 分）：`topic_trajectory`, `knowledge_display`, `emotional_consistency`, `question_patterns`, `overall_reproducibility`

**D2 天花板效应注意**：Student t=0 + tutor t=0 条件下 D2 预期较高，这是系统确定性的直接结果，不代表跨场景稳定。有意义的比较是 t=0 vs t=1：如果 D2 在 t=1 下显著下降，说明 student sim 对 tutor 变异敏感。

### 5.3 D3 — 跨模型稳定性 (Cross-model Consistency)

**评估粒度**：模型组（同一 task × persona × repeat × tutor_t 下 3 个模型各 1 段对话）
**采样**：使用全部 3 个 repeat 和两档 tutor temperature，共 12 组合 × 3 repeats × 2 temperatures = **72 单元**。

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

**已修复**：`_generate_parsed` 的 schema fallback 双重调用问题已在实验前修复（单次 API 调用 + strict 解析 + 3 次统一重试预算）。修复后每条 student 消息 = 1 次 API 调用。

### 6.2 OpenRouter 成本估算

基于 OpenRouter 实时定价（2026-04-20 查询），按每次 student 调用 ~2,000 input / ~150 output tokens，tutor 调用 ~1,500 input / ~300 output tokens 估算：

| 组件 | 模型 | Input $/1M | Output $/1M | 调用次数 | 估算成本 |
|------|------|-----------|------------|---------|---------|
| Student sim | openai/gpt-5.4 | $2.50 | $15.00 | 546 | $1.98 |
| Student sim | anthropic/claude-sonnet-4-6 | $3.00 | $15.00 | 546 | $2.26 |
| Student sim | google/gemini-3.1-pro-preview | $2.00 | $12.00 | 546 | $1.58 |
| **Tutor** | **openai/gpt-4.1-nano** | **$0.10** | **$0.40** | **1,872** | **$0.50** |
| Judge | anthropic/claude-sonnet-4-6 | $3.00 | $15.00 | 684 | 另计，取决于 prompt 长度 |
| **生成阶段合计** | | | | **3,510** | **~$6.32** |

### 6.3 Judge 评估工作量（OpenRouter）

| 维度 | 评估单元数 | 说明 |
|------|-----------|------|
| D1 | 288 | 仅 live t=0 组，12 组合 × 3 模型 × 1 repeat × 8 student messages |
| D2 | 72 | 12 组合 × 3 模型 × 2 温度 |
| D3 | 72 | 12 组合 × 3 repeats × 2 温度 |
| D4 | 234 | 全部 234 个对话 |
| 对照组 | 18 | 18 组 |
| **合计** | **684** | |

---

## 7. 执行流程

```
┌──────────────────────────────────────────────────────────────────┐
│  Step 1: 运行对话生成脚本                                           │
│  python -m experiments.student_sim_stability.cli generate         │
│  (并行执行，Tutor + Student 均调 OpenRouter，~$6.32)                │
│  ↓                                                                 │
│  Step 2: 渲染 Judge Prompt                                         │
│  python -m experiments.student_sim_stability.pipeline.render_judge_prompts  │
│  (本地运行，将对话 + 评估模板 → judge_inputs/*.json)                   │
│  ↓                                                                 │
│  Step 3: OpenRouter Judge 批量评估                                   │
│  python -m experiments.student_sim_stability.cli judge --dimension all │
│  (读取 judge_inputs/*.json，写入 judge_outputs/*.json)                 │
│  ↓                                                                 │
│  Step 4: 汇总 + 校验 + 生成报告                                      │
│  python -m experiments.student_sim_stability.cli aggregate --strict  │
│  python -m experiments.student_sim_stability.cli validate            │
│  python -m experiments.student_sim_stability.cli report              │
│  (读取 judge_outputs → 聚合 → HTML 报告)                             │
└──────────────────────────────────────────────────────────────────┘
```

### 7.0 Judge 持久化设计

每个评估单元的完整记录包含：

```
judge_inputs/{eval_id}.json     # 渲染后的完整 prompt + metadata
judge_outputs/{eval_id}.json    # judge 输出的 scores + reasoning + metadata
```

两个文件配对构成一条评估的完整审计链：prompt 输入 → judge 输出 → 解析后的分数。任何人可以通过重新运行相同的 prompt 来复现或质疑评估结果。

**模型名匿名化**：D3 prompt 中模型名替换为 `System A/B/C`，映射关系记录在 output metadata 的 `label_to_model` 字段中。Control prompt 的 A/B 顺序随机化，`persona_is_set_a` 字段记录实际映射。

### 7.1 CLI 命令

```bash
cd bench

# 查看实验规模
python -m experiments.student_sim_stability.cli dry-run

# 生成全部对话（并行执行）
python -m experiments.student_sim_stability.cli generate -w 6

# 渲染 judge prompts
python -m experiments.student_sim_stability.cli render-judges --dimension all --clean

# 运行 OpenRouter judge
python -m experiments.student_sim_stability.cli judge --dimension all --workers 6

# 汇总和校验
python -m experiments.student_sim_stability.cli aggregate --strict
python -m experiments.student_sim_stability.cli validate

# 生成报告（评估完成后）
python -m experiments.student_sim_stability.cli report
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
│   ├── live__I01_implement_sma__developer_crossover__gpt-5.4__r0_tt0.json
│   ├── control__D05_return_computation__finance_veteran__gemini-3.1-pro-preview__r0.json
│   └── ...
├── judge_inputs/
├── judge_outputs/
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
| 6. Temperature Ablation | tutor t=0/t=1 下的 D2 对比 |
| 7. Control | Persona 区分度得分 |
| 8. Conclusion | 最稳定模型推荐 + prompt 改进建议 |

---

## 9. 结论框架

实验不预设 pass/fail 阈值。报告以**相对比较和分布呈现**为主，辅以参考锚点。

### Q1: 当前 prompt 设计是否足够稳定？

以 D1 和 D4 的分布（histogram / box plot）呈现，而非 pass/fail。报告中标注 4.0 和 3.0 作为视觉参考线，但不做门槛判定。最终阈值在全量实验跑完后，根据分布的自然断点标定。

### Q2: 最稳定的模型？

以 composite 排名呈现，注明各维度的相对优劣。模型间差距的统计显著性需结合 n 和 std 判断。

### Q3: Persona 定义是否有效？

以对照组 distinctiveness 的 per-persona 柱状图呈现。关注的是**有 persona 和无 persona 的差距**，而非绝对分数。

### Q4: 是否存在漂移？

以 D4 逐轮 fidelity 折线图呈现。关注的是**后半段相对前半段的 delta**，而非单点阈值。

### Q5: Student sim 对 tutor 变异是否 robust？

以 tutor t=0 vs t=1 的 D2 配对柱状图呈现。关注的是**同一模型在两个温度下的 D2 差距**。

---

## 10. 风险与限制

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| **gpt-5.4 不支持 temperature 参数** | Student 端 t=0 设置可能被忽略（reasoning model 不接受 temperature） | 在报告中标注；如果 gpt-5.4 的 D2 异常，此为已知 confounding factor |
| gpt-4.1-nano tutor 质量不够 | 对话退化影响所有 student model | 已通过 pilot 验证 8 轮对话质量；影响绝对分但不影响排名 |
| gemini-3.1-pro-preview 模型名变化 | Trial 失败 | 运行前 dry-run 验证；config.py 可快速替换 |
| 3 次重复不够 | D2 方差估计不可靠 | 可扩展到 5 次；t=0 下方差已较小 |
| 8 轮太短 | D4 漏检长对话漂移 | E01 可扩展到 15 轮 |

### 10.1 模型 temperature 参数支持情况

运行前通过 OpenRouter `/api/v1/models` 接口验证各模型 `supported_parameters`：

| 模型 | 角色 | temperature 支持 | 说明 |
|------|------|-----------------|------|
| openai/gpt-4.1-nano | Tutor | ✓ | 温度消融有效 |
| openai/gpt-5.4 | Student | **✗** | Reasoning model，不接受 temperature。传入的 t=0 被忽略，模型使用自身默认行为 |
| anthropic/claude-sonnet-4-6 | Student | ✓ | t=0 生效 |
| google/gemini-3.1-pro-preview | Student | ✓ | t=0 生效 |

**影响**：gpt-5.4 的 D2（可复现性）可能与 sonnet/gemini 不直接可比——后两者通过 t=0 获得近确定性输出，而 gpt-5.4 的输出变异性由模型内部控制。这不影响 D1（persona adherence）和 D4（drift）的评估，也不影响 D3（跨模型一致性），但在解读 D2 结果时需要注意这一差异。
