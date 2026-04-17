# 学生模拟器稳定性实验方案

> Date: 2026-04-17
> Scope: StudentSimulator prompt 设计稳定性验证 + 最优底层模型选型
> Source files: `server/core/student_sim.py`, `server/config/prompt_config.py`, `server/schemas.py`
> Experiment code: `experiments/student_sim_stability/`
> Status: 方案定稿，待执行

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
| Tutor 方案 | 脚本化（Phase 1）/ 实时模型（Phase 2） | Phase 1 消除 tutor 变量，Phase 2 测试真实场景 |
| Persona 开关 | 有 persona / 无 persona（对照组） | 验证 persona 定义的有效性 |

### 3.2 控制变量

| 变量 | 固定值 | 理由 |
|------|--------|------|
| 对话轮次 | 8 轮 | 固定轮次便于逐轮对比和漂移分析。8 轮足以覆盖大部分 learning objectives，又不至于太短 |
| 重复次数 | 3 次/组合 | 最小可比较重复数，在成本和统计意义间平衡 |
| Student temperature | 0.7 | 非零以产生自然变异，但不至于完全随机 |
| Tutor 模型（Phase 2） | `anthropic/claude-sonnet-4-6` | 固定 tutor 端，隔离 student 侧变量 |
| Tutor temperature | 0.3 | 低温确保 tutor 相对一致 |
| Judge 模型 | `anthropic/claude-opus-4-6` | 强模型做评估，temperature=0.0 确保评判确定性 |

### 3.3 任务选择

从 6 个 category 中各选 1 个代表性任务，覆盖不同难度和对话模式：

| Task ID | Category | 难度 | 选择理由 | Persona 对 |
|---------|----------|------|---------|-----------|
| I01_implement_sma | implementation | easy | 基础编码任务，对话模式清晰，教学流程线性 | developer_crossover, fullstack_practitioner |
| S03_mean_reversion_research | strategy | medium | 需要金融概念讨论，能暴露 persona 知识边界差异 | **finance_veteran, double_novice** |
| X02_lookahead | debug | easy | 需要代码理解 + 概念解释，两类 persona 会有不同反应 | developer_crossover, fullstack_practitioner |
| D05_return_computation | data_analysis | medium | 基础数据任务，涉及数学公式，能测试 anxiety 表现 | **finance_veteran, double_novice** |
| A03_sharpe_misconception | adversarial | medium | 误导场景，测试 persona 在压力下的行为一致性 | **finance_veteran, double_novice** |
| E01_build_ma_system | end_to_end | medium | 长流程端到端任务，最能检测对话漂移 | developer_crossover, fullstack_practitioner |

Persona 分配逻辑：
- S03、D05、A03 的 task JSON 中包含 `finance_veteran` 和 `double_novice`，使用**主对**
- I01、X02、E01 的 task JSON 中只有 `developer_crossover` 和 `fullstack_practitioner`，使用**备对**
- 两组 persona 对的差异轴不同：主对是"金融深 vs 全新手"，备对是"懂代码不懂金融 vs 全都懂"

---

## 4. 两阶段实验设计

### 4.1 Phase 1：脚本化 Tutor（可复现性测试）

**目的**：消除 tutor 端随机性，将观察到的差异完全归因于学生模拟器。

**方法**：
1. 用 `sonnet-4.6`（temperature=0.3）为每个 (task, persona) 组合预生成 8 条固定 tutor 消息
2. Tutor 消息遵循渐进式教学计划，基于 task 的 `required_capabilities` 设计
3. 消息内容自包含（不依赖特定学生回复），确保面对不同学生回复时仍然合理
4. 生成后缓存为 `scripted_tutor_plans.json`，后续所有 trial 复用

**对话流程**：
```
[Student Opening] → Tutor[1] → Student[1] → Tutor[2] → Student[2] → ... → Tutor[8]
```
- Student Opening：来自 task JSON 的 `student_openings[persona_id]`
- Tutor[1..8]：脚本化固定消息
- Student[1..7]：由 StudentSimulator 实时生成（被测对象）

**实验矩阵**：

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

**小计**：12 组合 × 3 模型 × 3 重复 = **108 trials**

### 4.2 Phase 2：实时 Tutor（真实场景测试）

**目的**：测试在真实对话场景下（tutor 也是活 LLM），学生模拟器的稳定性。

**方法**：
1. Tutor 统一使用 `sonnet-4.6`（temperature=0.3）
2. Tutor 根据对话上下文动态生成回复（最近 3 轮上下文窗口）
3. 其余设置与 Phase 1 相同

**对话流程**：
```
[Student Opening] → Tutor(live)[1] → Student[1] → Tutor(live)[2] → Student[2] → ... → Tutor(live)[8]
```

**小计**：12 组合 × 3 模型 × 3 重复 = **108 trials**

### 4.3 对照组：无 Persona

**目的**：验证 persona 定义确实产生了有意义的行为差异。

**方法**：
1. 去掉 persona 的 `description`、`known/unknown_concepts`、`emotional_profile`、`behavioral_rules`
2. 替换为通用描述：_"You are a student learning about quantitative finance and programming."_
3. 使用 Phase 1 的脚本化 tutor 消息
4. 每个 (task, model) 跑 1 次（无需重复，目的是对比有/无 persona 的差异）

**小计**：6 tasks × 3 模型 = **18 trials**

---

## 5. 评估方法

### 5.1 D1 — 角色一致性 (Persona Adherence)

**评估粒度**：逐条学生消息

**评判方式**：LLM-as-judge（opus-4.6, temperature=0.0）

**评分维度**（均 1-5 分）：

| 子维度 | 评判标准 |
|--------|---------|
| `knowledge_boundary` | 学生是否尊重 known/unknown concepts 的边界？是否"知道"了不该知道的？ |
| `emotional_tone` | 语气是否匹配 emotional_profile？（如 curious_anxious 应在公式出现时紧张） |
| `behavioral_rules` | persona 中的行为规则是否被遵循？（如 finance_veteran 遇到已知金融概念应立即跳过） |
| `overall` | 综合角色一致性 |

**评分标准**：
- 5 = 完美入戏：知识边界、行为规则、情绪语调全部匹配
- 4 = 基本入戏，有轻微偏差
- 3 = 部分入戏，有明显不一致
- 2 = 大部分脱戏，知识边界或情绪明显错误
- 1 = 完全脱戏，表现为另一个 persona

**输入给 judge 的信息**：
- Persona 完整定义（description, known/unknown, emotional_profile, behavioral_rules）
- 当前轮次编号
- 上一条 tutor 消息（提供上下文）
- 待评估的学生消息

**采样策略**：每个模型取第一次运行的全部学生消息评估（避免 judge 成本爆炸）。

**产出指标**：
- 每条消息的 4 维分数
- 每次对话的均分
- 按 (model × persona) 分组的热力图

### 5.2 D2 — 跨运行可复现性 (Cross-run Reproducibility)

**评估粒度**：对话组（同一 task × persona × model 的 3 次运行）

**评判方式**：LLM-as-judge（opus-4.6, temperature=0.0）

**评分维度**（均 1-5 分）：

| 子维度 | 评判标准 |
|--------|---------|
| `topic_trajectory` | 3 次运行是否讨论了相似的话题，顺序是否相近？ |
| `knowledge_display` | 学生表现出的知识水平是否一致？ |
| `emotional_consistency` | 情绪语调在 3 次运行中是否相似？ |
| `question_patterns` | 学生提问的类型和深度是否一致？ |
| `overall_reproducibility` | 综合可复现性 |

**评分标准**：
- 5 = 3 次运行行为模式几乎相同
- 4 = 非常相似，仅措辞差异
- 3 = 大方向一致但话题顺序或深度有明显差异
- 2 = 显著行为差异
- 1 = 完全不同 — 不可复现

**输入给 judge 的信息**：
- Persona 描述
- Task 描述
- 3 次运行中所有学生消息（每条截取前 200 字符避免上下文爆炸）

**产出指标**：
- 每组 5 维分数
- 按 (model × phase) 分组的对比表
- Phase 1 vs Phase 2 的可复现性差距（量化 tutor 方差对 student 的传导效应）

### 5.3 D3 — 跨模型稳定性 (Cross-model Consistency)

**评估粒度**：模型组（同一 task × persona 下 3 个模型各 1 次运行）

**评判方式**：LLM-as-judge（opus-4.6, temperature=0.0）

**评分维度**（均 1-5 分）：

| 子维度 | 评判标准 |
|--------|---------|
| `knowledge_boundary_preserved` | 所有模型是否尊重相同的知识边界？ |
| `emotional_profile_preserved` | 所有模型是否产生相似的情绪表现？ |
| `behavioral_rules_preserved` | 所有模型是否遵循行为规则？ |
| `persona_distinguishability` | 你能否识别这些对话来自同一个 persona？ |
| `overall_cross_model` | 综合跨模型一致性 |

**额外输出**：
- `best_model`：最好地还原了 persona 的模型
- `worst_model`：最差地还原了 persona 的模型

**产出指标**：
- 按 task 分组的一致性得分
- Best/Worst 模型投票汇总

### 5.4 D4 — 对话漂移检测 (Drift Detection)

**评估粒度**：整段对话的逐轮分析

**评判方式**：LLM-as-judge（opus-4.6, temperature=0.0）

**逐轮评分**：

| 指标 | 量表 | 说明 |
|------|------|------|
| `persona_fidelity` | 1-5 | 该轮消息与 persona 的匹配度 |
| `knowledge_leak` | 0-3 | 0=无泄漏，1=轻微，2=显著，3=完全跳脱 |
| `co_teacher_drift` | 0-2 | 0=正常学生，1=轻微解释倾向，2=明显像 co-teacher |

**对话级汇总**：

| 指标 | 说明 |
|------|------|
| `overall_drift_score` | 1-5，5=无漂移，1=严重漂移 |
| `drift_onset_turn` | 漂移首次出现的轮次（null 表示无漂移） |

**产出指标**：
- 按模型分组的逐轮 fidelity 曲线（前 4 轮 vs 后 4 轮对比）
- 漂移起始轮次的分布
- 按 (model × phase) 的漂移得分

### 5.5 对照组评估 — Persona 区分度

**评估粒度**：对话对（有 persona vs 无 persona）

**评分**：
- `distinctiveness`（1-5）：有/无 persona 的行为差异程度
- `persona_value_add`（文本）：persona 定义具体增加了哪些行为

**评分标准**：
- 5 = 行为完全不同，persona 明确塑造了学生行为
- 3 = 有差异也有相似
- 1 = 无法区分，persona 定义无效

---

## 6. 实验规模与成本

### 6.1 Trial 统计

| 阶段 | Trials | 学生消息数 | 说明 |
|------|--------|-----------|------|
| Phase 1（脚本化） | 108 | 756 | 12 组合 × 3 模型 × 3 重复 × 7 条/trial |
| Phase 2（实时） | 108 | 756 | 同上 |
| 对照组 | 18 | 126 | 6 task × 3 模型 × 7 条/trial |
| **合计** | **234** | **1,638** | |

注：每 trial 产生 7 条学生消息（开头 1 条 + 对话中 7 条，最后一轮无学生回复）。

### 6.2 Judge 调用统计

| 评估维度 | 调用次数 | 说明 |
|----------|---------|------|
| D1 | ~252 | 每模型取 1 次运行 × 7 条 = 6 task × 2 persona × 3 model × 7 msg × 2 phase |
| D2 | ~72 | 12 组合 × 3 模型 × 2 phase |
| D3 | ~24 | 12 组合 × 2 phase |
| D4 | ~216 | 234 trials（全部对话） |
| 对照组 | ~18 | 18 组 |
| **合计** | **~582 次 judge 调用** | |

### 6.3 成本估算

基于 OpenRouter 定价（2026-04-15 验证）：

| 组件 | 模型 | 每次输入 tokens | 每次输出 tokens | 单价($/1M in) | 估算总成本 |
|------|------|----------------|----------------|--------------|-----------|
| Student sim | gpt-5.4 | ~2,000 | ~150 | $2.50 | 108 trials × 7 calls = 756 × ~$0.006 ≈ $4.5 |
| Student sim | sonnet-4.6 | ~2,000 | ~150 | $3.00 | 同上 ≈ $5.4 |
| Student sim | gemini-3.1 | ~2,000 | ~150 | ~$2.00 | 同上 ≈ $3.6 |
| Live tutor | sonnet-4.6 | ~1,500 | ~300 | $3.00 | 108 trials × 8 calls = 864 × ~$0.007 ≈ $6.0 |
| Scripted tutor gen | sonnet-4.6 | ~1,000 | ~2,000 | $3.00 | 12 plans × ~$0.02 ≈ $0.24 |
| Judge | opus-4.6 | ~2,500 | ~200 | $5.00 | 582 calls × ~$0.014 ≈ $8.1 |
| **总计** | | | | | **~$28** |

这是保守上限估算，实际可能更低。

---

## 7. 产出物

### 7.1 原始数据

```
results/
├── conversations/              # 234 个 JSON 文件，每个含完整对话
│   ├── scripted__I01_implement_sma__developer_crossover__gpt-5.4__r0.json
│   ├── live__S03_mean_reversion_research__finance_veteran__sonnet-4.6__r1.json
│   ├── control__D05_return_computation__finance_veteran__gemini-3.1-pro-preview__r0.json
│   └── ...
├── scripted_tutor_plans.json   # 12 组脚本化 tutor 消息
└── evaluations/
    └── all_evaluations.json    # 全部 D1-D4 + 对照组评估结果
```

### 7.2 统计报告（HTML）

```
results/report/
├── stability_report.html       # 主报告
└── stability_stats.json        # 原始统计数据
```

报告包含以下 7 个章节：

| 章节 | 内容 |
|------|------|
| 1. Overview Dashboard | 模型综合稳定性排名表（composite = mean(D1, D2, D4)），总评估数 |
| 2. D1 Persona Adherence | (model × persona) 热力图，Phase 1 vs Phase 2 对比 |
| 3. D2 Reproducibility | (model × phase) 可复现性表，Phase 间差距分析 |
| 4. D3 Cross-model | 按 task 分组一致性得分，Best/Worst 模型投票 |
| 5. D4 Drift Detection | 逐轮 fidelity 曲线（前半 vs 后半 delta），漂移起始轮次 |
| 6. Control Group | Persona 区分度得分，分 persona 对比 |
| 7. Conclusion | 最稳定模型推荐，prompt 设计改进建议 |

---

## 8. 执行方式

### 8.1 前提条件

- `OPENROUTER_API_KEY` 环境变量已设置
- `google/gemini-3.1-pro-preview` 在 OpenRouter 可用（需确认模型名）
- Python 环境包含 `numpy`（报告生成需要）

### 8.2 CLI 命令

```bash
cd bench

# 查看实验规模（不消耗 API）
python -m experiments.student_sim_stability.run dry-run

# 一键全流程
python -m experiments.student_sim_stability.run all

# 或分步执行（支持断点续跑）
python -m experiments.student_sim_stability.run phase1     # ~15 min, ~$13
python -m experiments.student_sim_stability.run phase2     # ~20 min, ~$11
python -m experiments.student_sim_stability.run control    # ~3 min, ~$1
python -m experiments.student_sim_stability.run evaluate   # ~15 min, ~$8
python -m experiments.student_sim_stability.run report     # 即时，无 API 调用
```

### 8.3 断点续跑机制

- 每个 trial 完成后立即写入 `conversations/{trial_key}.json`
- 重新运行时自动跳过已有文件
- 如需重跑某个 trial，删除对应 JSON 文件即可

---

## 9. 实验代码结构

```
experiments/student_sim_stability/
├── __init__.py
├── config.py               # 实验参数、任务/模型/persona 配置
│   ├── STUDENT_MODELS       # 3 个待测模型
│   ├── EXPERIMENT_TASKS     # 6 个代表性任务
│   ├── TASK_PERSONA_MAP     # 每个 task 使用的 persona 对
│   ├── FIXED_TURNS = 8      # 固定对话轮次
│   └── REPEATS = 3          # 每组合重复次数
├── scripted_tutor.py        # Phase 1 脚本化 tutor 消息生成
│   ├── generate_scripted_tutor_plan()    # 为 1 个 (task, persona) 生成 8 条 tutor 消息
│   └── load_or_generate_scripted_plans() # 批量生成 + 缓存
├── runner.py                # 主编排器
│   ├── run_single_trial()   # 执行 1 次对话
│   └── ExperimentRunner     # 管理 Phase 1/2/Control/Evaluation
│       ├── run_phase1()     # 脚本化 tutor 实验
│       ├── run_phase2()     # 实时 tutor 实验
│       ├── run_control()    # 对照组
│       ├── run_evaluation() # D1-D4 评估
│       └── run_all()        # 全流程
├── evaluator.py             # LLM judge 评估器
│   └── StabilityEvaluator
│       ├── eval_d1_message()       # 单条消息角色一致性
│       ├── eval_d1_conversation()  # 整段对话 D1
│       ├── eval_d2()               # 跨运行可复现性
│       ├── eval_d3()               # 跨模型一致性
│       ├── eval_d4()               # 漂移检测
│       └── eval_control()          # 对照组区分度
├── report.py                # HTML 报告生成
│   └── ReportGenerator
│       ├── _aggregate_d1/d2/d3/d4()  # 统计聚合
│       ├── _compute_model_ranking()  # 模型排名
│       └── generate()                # 输出 HTML + JSON
└── run.py                   # CLI 入口
```

---

## 10. Prompt 设计细节

### 10.1 学生模拟器 Prompt（被测对象）

当前 prompt 由两部分组成：

**user_description**（来自 `prompt_config.py:build_user_description`）：
```
Your profile: {persona.description}

Emotional style:
{expanded emotional_profile}

Behavioral rules (follow strictly):
  - {rule_1}
  - {rule_2}
  ...

Interaction rules:
  - 【If the tutor asks you a question, ANSWER IT FIRST...】
  - Respond naturally ...
  - 【NEVER fabricate data, code, or files...】
  - You interact through TEXT-ONLY chat...
```

**scenario**（来自 `prompt_config.py:build_scenario`）：
```
Scenario: {task.description}
Your opening message was: "{opening}"

Learning goals:
  1. {capability_1}
  2. {capability_2}
  ...

Introduce goals one at a time ...
```

**最终 prompt**（`student_sim.py:_NEXT_MESSAGE_PROMPT`）：
```
You are role-playing as a real person using an LLM tutoring app.

{user_description}

{scenario}

Reply format: 2-4 sentences. ...
{runtime_guidance_block}

Conversation so far:
{transcript}

Respond with a JSON object containing a single key `simulated_input`.
```

### 10.2 脚本化 Tutor Prompt（Phase 1 生成用）

```
You are generating a scripted tutor response sequence for a tutoring
simulation experiment. The tutor teaches quantitative finance topics.

Task: {task_description}
Category: {category}
Student persona: {persona_description}
Student opening message: "{student_opening}"

Learning objectives the tutor should cover:
{capabilities}

Generate exactly 8 tutor messages that form a coherent teaching sequence.
...
Return a JSON object with key "tutor_messages" containing array of 8 strings.
```

### 10.3 实时 Tutor Prompt（Phase 2 用）

```
You are a quantitative finance tutor. A student is asking for help.

Task context: {task_description}

The student has just said:
"{student_message}"

Conversation so far:
{last 3 turns}

Respond as a helpful, patient tutor. ...
```

### 10.4 对照组 Prompt

替换 user_description 为：
```
You are a student learning about quantitative finance and programming.
You are curious and want to understand the topics being discussed.
Respond naturally in 2-4 sentences.
```

---

## 11. 预期结论模板

实验完成后，报告应能回答：

### Q1: 当前 prompt 设计是否足够稳定？

- 如果 D1 均分 ≥ 4.0 且 D4 漂移得分 ≥ 4.0 → **稳定，可用于 benchmark**
- 如果 D1 ∈ [3.0, 4.0) → 基本稳定，但特定维度需要加强
- 如果 D1 < 3.0 → 不稳定，需要重大 prompt 改进

### Q2: 最稳定的模型是哪个？

- Composite 排名第一的模型推荐为默认 `SIMULATOR_DEFAULT_MODEL`
- 如果第一名和第二名差距 < 0.2，则需要综合成本考虑

### Q3: Persona 定义是否有效？

- 对照组 distinctiveness ≥ 4.0 → **有效，persona 明显塑造了行为**
- 3.0-4.0 → 有效但可以加强
- < 3.0 → 需要重新设计 persona prompt

### Q4: 是否存在漂移？

- D4 漂移起始轮次 ≥ 6 → 后期才出现，可接受
- 漂移起始轮次 ∈ [4, 6) → 需要在 runtime_guidance 中加入 persona 重述
- 漂移起始轮次 < 4 → 严重问题，需要 prompt 架构级改进

### 可能的改进方向

根据实验结果，可考虑的后续行动：

1. **Prompt 层面**：在 runtime_guidance 中周期性重述 persona 关键特征
2. **架构层面**：增加 persona 一致性检查器（类似 TC checker 的角色）
3. **模型选型**：更新 `SIMULATOR_DEFAULT_MODEL` 为最稳定模型
4. **Persona 设计**：强化区分度低的 persona 的关键行为规则

---

## 12. 风险与限制

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Judge 模型自身不稳定 | 评估分数噪声大 | 使用 opus-4.6 + temperature=0.0；若方差大可加 3 次 judge 取均值 |
| 脚本化 tutor 不自然 | Phase 1 对话质量低，学生回复被迫生硬 | 脚本设计为渐进式教学而非回应式；Phase 2 补充验证真实场景 |
| 8 轮太短无法检测漂移 | D4 漏检长对话漂移 | E01 任务（end_to_end）原 max_turns=40，若需要可扩展到 15 轮 |
| gemini-3.1-pro-preview 模型名可能变化 | Trial 失败 | 运行前验证模型可用性；config.py 可快速替换 |
| 3 次重复不够 | D2 方差估计不可靠 | 可后续扩展到 5 次；当前 3 次是成本与信息量的平衡 |
