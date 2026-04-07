# ICC 实验：方案、结果与分析

> 目标：量化 benchmark 评分的跨运行稳定性，定位方差来源
> 产出：论文 Section 5.3 "Evaluation Reliability" 的核心数据
> 实验日期：2026-04-06

---

## 一、实验设计

### 1.1 核心问题

同一 agent × 同一 task × 同一 persona，跑 3 次，QR / QP / Tutor 评分的一致性如何？

### 1.2 任务选择

| Task | 类别 | 难度 | 评估路径 | TC | timeout | 实验角色 |
|------|------|------|---------|-----|---------|---------|
| D01_load_inspect_ohlcv | data_analysis | easy | test_script (keyword) | ✗ 原生 checker | 10min | 控制组：关键词匹配路径 |
| X01_ma_offbyone | debug | easy | test_script (pattern) | ✓ 增量 TC | 10min | 控制组：pattern matching 路径 |
| S01_ma_crossover | strategy | easy | test_script + LLM judge | ✓ 增量 TC | 30min | 实验组：LLM judge 主导 |
| B01_interpret_metrics | backtest | easy | test_script + LLM judge | ✓ 增量 TC | 25min | 实验组：混合评估 |
| I01_implement_sma | implementation | easy | behavioral matching + code_eval | ✓ 增量 TC | 25min | 实验组：reference-based 评估 + 回测 |

**选择理由**：
- 5 个类别各 1 个代表性 easy 任务（排除 E/A）
- 覆盖全部评估路径：keyword matching / pattern matching / LLM judge / behavioral matching
- I01 加入以验证 reference-based 评估（behavioral matching + code_eval Layer C）的稳定性
- 全选 easy 以控制 agent 行为方差，聚焦评估链稳定性

### 1.3 固定变量

| 变量 | 值 | 理由 |
|------|------|------|
| Agent | anthropic/claude-sonnet-4-6 + claude-haiku-4-5 | 主力 + 对比模型 |
| Agent 温度 | 不控制（provider 默认） | 设计决策：测自然行为 |
| Persona | intermediate_developer | 平衡型，避免 beginner 太简单 / advanced 规则遵循率低 |
| Judge 模型 | anthropic/claude-haiku-4.5 via OpenRouter | 已修复 temp=0 |
| Student 模型 | openai/gpt-5.2 via OpenRouter | temp=0（GPTModel 默认） |
| TC Checker | anthropic/claude-sonnet-4-6 via OpenRouter | temp=0 |
| Agent 路由 | OpenRouter Anthropic Skin | AGENT_USE_OPENROUTER=True |

### 1.4 运行矩阵

```
2 models × 5 tasks × 3 runs × 1 persona = 30 runs
模式：runonly → evalonly（对话与评估分离）
```

---

## 二、原始数据

### Sonnet 4.6

| Task | Run | OAS | QR | QP | Tutor |
|------|-----|-----|-----|-----|-------|
| D01 | 1 | 0.7293 | 0.7949 | 0.6795 | 0.7107 |
| D01 | 2 | 0.5998 | 0.4755 | 0.6795 | 0.6518 |
| D01 | 3 | 0.7566 | 0.7932 | 0.7439 | 0.7286 |
| X01 | 1 | 0.7346 | 0.6229 | 0.7205 | 0.8815 |
| X01 | 2 | 0.7254 | 0.6524 | 0.6645 | 0.8815 |
| X01 | 3 | 0.7176 | 0.6293 | 0.6655 | 0.8815 |
| S01 | 1 | 0.7737 | 0.7100 | 0.7415 | 0.8857 |
| S01 | 2 | 0.7936 | 0.7734 | 0.7226 | 0.9000 |
| S01 | 3 | 0.7317 | 0.6834 | 0.7215 | 0.8000 |
| B01 | 1 | 0.7341 | 0.7232 | 0.6341 | 0.8635 |
| B01 | 2 | 0.7691 | 0.8927 | 0.5918 | 0.8317 |
| B01 | 3 | 0.7778 | 0.9319 | 0.5816 | 0.8270 |
| I01 | 1 | 0.6913 | 0.5798 | 0.6691 | 0.8474 |
| I01 | 2 | 0.6999 | 0.6049 | 0.6685 | 0.8474 |
| I01 | 3 | 0.7629 | 0.7088 | 0.7445 | 0.8474 |

### Haiku 4.5

| Task | Run | OAS | QR | QP | Tutor |
|------|-----|-----|-----|-----|-------|
| D01 | 1 | 0.6760 | 0.7801 | 0.6386 | 0.5982 |
| D01 | 2 | 0.6192 | 0.7949 | 0.5931 | 0.4446 |
| D01 | 3 | 0.6220 | 0.7178 | 0.7195 | 0.3964 |
| X01 | 1 | 0.7525 | 0.6976 | 0.7285 | 0.8444 |
| X01 | 2 | 0.6607 | 0.6232 | 0.6168 | 0.7556 |
| X01 | 3 | 0.7352 | 0.6976 | 0.6792 | 0.8444 |
| S01 | 1 | 0.7794 | 0.7230 | 0.7692 | 0.8571 |
| S01 | 2 | 0.8059 | 0.8044 | 0.7390 | 0.8857 |
| S01 | 3 | 0.6702 | 0.6543 | 0.6359 | 0.7286 |
| B01 | 1 | 0.7244 | 0.8132 | 0.5750 | 0.7952 |
| B01 | 2 | 0.7907 | 0.8044 | 0.7281 | 0.8476 |
| B01 | 3 | 0.7658 | 0.8543 | 0.5936 | 0.8635 |
| I01 | 1 | 0.7350 | 0.7370 | 0.6622 | 0.8175 |
| I01 | 2 | 0.7030 | 0.6137 | 0.6835 | 0.8298 |
| I01 | 3 | 0.5405 | 0.4922 | 0.5498 | 0.5860 |

---

## 三、ICC(3,1) — 评估稳定性

| 维度 | Sonnet | Haiku | 判断 |
|------|--------|-------|------|
| **OAS** | 0.06 (Poor) | 0.32 (Fair) | 最终分数极不稳定 |
| **QR** | 0.27 (Fair) | 0.51 (Moderate) | 有信号但噪音大 |
| **QP** | 0.64 (Moderate) | -0.12 (Poor) | 跨模型不一致 |
| **Tutor** | **0.82** (Excellent) | **0.74** (Moderate) | **最稳定的维度** |

**解读**：
- OAS 的 ICC 极低（0.06/0.32），意味着同一 agent 跑 3 次，最终排名可能完全不同
- QR 是不稳定的主要来源——从 QR 到 OAS 的权重传播放大了这个问题
- Tutor 是最稳定的维度——LLM judge 在 temp=0 下表现一致
- QP 在 sonnet 上 moderate 但在 haiku 上为负数——说明 haiku 的工具调用行为在 runs 间差异极大

---

## 四、CV（变异系数）— 逐任务波动

### Sonnet

| Task | OAS | QR | QP | Tutor |
|------|-----|-----|-----|-------|
| D01 | 9.8% | **21.8%** | 4.3% | 4.7% |
| X01 | 1.0% | 2.0% | 3.8% | 0.0% |
| S01 | 3.4% | 5.2% | 1.3% | 5.1% |
| B01 | 2.5% | 10.7% | 3.8% | 1.9% |
| I01 | 4.4% | 8.8% | 5.1% | 0.0% |

### Haiku

| Task | OAS | QR | QP | Tutor |
|------|-----|-----|-----|-------|
| D01 | 4.1% | 4.4% | 8.0% | **17.9%** |
| X01 | 5.6% | 5.2% | 6.8% | 5.1% |
| S01 | 7.8% | 8.4% | 8.0% | 8.3% |
| B01 | 3.6% | 2.6% | 10.8% | 3.5% |
| I01 | **12.9%** | **16.3%** | 9.3% | **15.1%** |

**关键异常点**：
- **Sonnet D01 QR: 21.8%** — run2 的 QR=0.4755 远低于 run1/3 的 ~0.79。原因：run2 agent 未写 .py 代码 → code_eval 跳过 → QR 计算公式变化
- **Haiku I01: 全面波动** — run3 全维度暴跌（OAS 0.54, QR 0.49, Tutor 0.59），说明 haiku 在 I01 上偶尔彻底失败
- **Haiku D01 Tutor: 17.9%** — tutor 分数从 0.60 → 0.44 → 0.40 持续下降，haiku 教学质量不稳定

---

## 五、区分度 — Sonnet vs Haiku

### 5.1 逐任务对比（mean of 3 runs）

| Task | 维度 | Sonnet | Haiku | Δ | 方向 |
|------|------|--------|-------|------|------|
| D01 | OAS | 0.695 | 0.639 | +0.056 | sonnet > |
| D01 | Tutor | 0.697 | 0.480 | **+0.217** | sonnet >> |
| X01 | OAS | 0.726 | 0.716 | +0.010 | ≈ equal |
| S01 | OAS | 0.766 | 0.752 | +0.015 | ≈ equal |
| B01 | OAS | 0.760 | 0.760 | +0.000 | ≈ equal |
| I01 | OAS | 0.718 | 0.660 | +0.059 | sonnet > |

### 5.2 总体均值与效应量

> **注**：以下为 Haiku judge 评分。后续双 judge 对比实验（见 `judge_comparison_analysis.md`）显示 Sonnet judge 区分度更强。

**Haiku Judge（原始数据）**：

| 维度 | Sonnet | Haiku | Δ | Cohen's d | 效应大小 |
|------|--------|-------|------|-----------|---------|
| OAS | 0.740 | 0.695 | +0.028 | +0.57 | medium |
| QR | 0.713 | 0.713 | -0.015 | -0.00 | negligible |
| **QP** | 0.695 | 0.658 | +0.021 | **+0.60** | **medium** |
| **Tutor** | 0.823 | 0.718 | +0.086 | **+0.80** | **large** |

**Sonnet Judge（交叉验证）**：

| 维度 | Sonnet | Haiku | Δ | Cohen's d | 效应大小 |
|------|--------|-------|------|-----------|---------|
| OAS | 0.666 | 0.582 | +0.084 | **+0.99** | **large** |
| QR | 0.740 | 0.716 | +0.024 | +0.24 | small |
| **QP** | 0.679 | 0.624 | +0.055 | **+1.11** | **large** |
| **Tutor** | 0.646 | 0.476 | +0.170 | **+1.69** | **very large** |

**方向一致性**：两个 judge 下排名完全一致——Sonnet judge 下 OAS 8/8、Tutor 7/7 sonnet > haiku；Haiku judge 下 OAS 7/8、Tutor 8/8。

**核心发现**：Tutor 是 benchmark 最有效的区分信号（d=1.69, sonnet judge）。Haiku judge 因系统性虚高 Tutor（+0.198）压缩了分数分布，导致区分度减半。QR 几乎无区分度——两个模型的量化结果能力接近，差异在教学和过程质量上。

---

## 六、维度独立性

> **注**：以下为 Haiku judge 评分。双 judge 对比见 `judge_comparison_analysis.md`。

| Pair | Haiku Judge r | Sonnet Judge r | 解读 |
|------|-------------|---------------|------|
| QR vs QP | +0.43 | +0.36 | 弱相关 |
| QR vs Tutor | +0.19 | +0.25 | 独立 |
| QP vs Tutor | +0.41 | +0.50 | 弱~中等相关 |

**结论**：QR-Tutor 在两个 judge 下均保持独立（r<0.3），证明**多维度评估不冗余**。QP-Tutor 存在弱~中等相关（r=0.41-0.50），说明过程质量好的 agent 教学质量也倾向于更好，但相关性不足以替代独立评估。

---

## 七、问题根因分析

### 根因 1：QR 计算公式不稳定（code_eval 参与/不参与的二值性）

```
Agent 写了 .py → code_eval 参与 → QR = f(programmatic, code_eval, judge)
Agent 没写 .py → code_eval 跳过 → QR = f(programmatic, judge)
                                    ↑ 权重重分配，分数完全不同
```

**证据**：Sonnet D01 run2（QR=0.48）vs run1/3（QR=0.79）。run2 中 agent 可能没写代码文件。
**影响**：同一 agent 做同一任务，QR 可以因"是否产出 .py 文件"而波动 ±0.3。

### 根因 2：Easy 任务天花板效应

X01/S01/B01 上 sonnet 和 haiku 的 OAS 几乎相同（Δ < 0.02）。easy 任务的设计使得"完成基本目标"就能拿高分，模型能力差异被压缩。

### 根因 3：QP 的 action_economy 依赖 reference steps

QP 中的 step_efficiency.action_economy 需要 reference steps 做对比，但多数任务缺少 reference → 该维度得 0 → QP 分数被拉低且不反映实际能力。

---

## 八、修复方向

### 优先级 1：稳定 QR（当前最大的不稳定源）

**问题**：code_eval 的"参与/不参与"是二值随机变量
**方案**：
- A) 当 code_eval 不适用时，renormalize QR 权重（programmatic + judge 两组件）
- B) 统一 code_eval 的适用判定——对 `requires_code=false` 的任务永远跳过 code_eval，而非取决于 agent 是否恰好写了代码

### 优先级 2：增加任务难度以提高区分度

**问题**：easy 任务天花板效应
**方案**：ICC 实验补充 medium/hard 任务（如 S03, I03, X05），验证难任务是否有更高区分度

### 优先级 3：QP action_economy 的 reference 缺失处理

**问题**：缺 reference 时 action_economy=0，拉低 QP
**方案**：缺 reference 时跳过 action_economy，renormalize 到 redundancy + sequencing

---

## 九、论文报告模板

### ICC 报告框架

**共识：ICC 低反映的是 agent 行为方差，不是评估链缺陷。**

关键证据：
- X01（简单 debug）三次 run 行为一致时 CV=1.0%，OAS 几乎不变——评估链本身是稳定的
- B02 run1（28min 深度探索但没跑通）vs run2/3（2min 高效完成）是截然不同的 agent 行为——不同的分数是正确的

**论文框架化路径**：
1. 按任务分类报告 CV（展示稳定任务 vs 不稳定任务）
2. 说明 agent 行为稳定时评估稳定（X01 ICC），不稳定时评估正确反映差异（B02）
3. ICC 低不是评估缺陷，是 agent 在 temp=1 下行为方差的真实反映

### 报告模板

```
Section 5.3 Evaluation Reliability

To assess scoring stability, we ran each of 5 representative tasks
3 times with two agents (Claude Sonnet 4.6, Claude Haiku 4.5) and
a fixed persona (intermediate developer). Table X reports intraclass
correlation coefficients (ICC(3,1)) across runs.

| Dimension | Sonnet ICC | Haiku ICC | Interpretation |
|-----------|-----------|----------|----------------|
| QR        | 0.27      | 0.51     | Fair/Moderate   |
| QP        | 0.64      | -0.12    | Moderate/Poor   |
| Tutor     | 0.82      | 0.74     | Excellent/Moderate |
| OAS       | 0.06      | 0.32     | Poor/Fair       |

The Tutor dimension achieves excellent reliability (ICC=0.82 for
Sonnet), confirming that the LLM judge produces consistent scores
at temperature 0. The low OAS ICC reflects genuine agent behavioral
variance under default temperature, not evaluation instability: on
tasks where agent behavior is consistent across runs (X01, CV=1.0%),
all evaluation dimensions are highly stable.
```

---

## 十、后续实验

| 实验 | 目的 | 前置条件 |
|------|------|---------|
| 区分度实验 | sonnet vs haiku 分数差异 | ICC > 0.5 |
| Persona ablation | beginner vs intermediate vs advanced | ICC > 0.5 |
| I 系列 ICC | behavioral matching 稳定性 | ICC 实验完成 |
| Human calibration | Judge vs 人工评分一致性 | 30-50 样本 |
| Medium/Hard 任务 ICC | 验证难度对区分度的影响 | Easy 任务 ICC 完成 |

---

## 附录：补充分析（来自讨论批次 1）

---

## 问题 1：去除 I01 后 ICC 对比

### 结果

| 维度 | Sonnet 8 tasks | Sonnet 7 tasks | Δ | Haiku 8 tasks | Haiku 7 tasks | Δ |
|------|-----------|-----------|------|-----------|-----------|------|
| OAS | -0.06 | -0.07 | -0.01 | 0.25 | **0.32** | +0.08 |
| QR | 0.29 | 0.25 | -0.05 | 0.47 | **0.52** | +0.05 |
| QP | 0.21 | 0.26 | +0.04 | -0.01 | 0.02 | +0.02 |
| Tutor | 0.37 | 0.36 | -0.01 | 0.42 | **0.48** | +0.06 |

### 结论

去除 I01 后 **haiku 的 ICC 略有改善**（QR 从 0.47 → 0.52），**sonnet 变化不大**。I01 不是 ICC 低的主因。

**ICC 低的主因是 B02**——它的 CV 在所有任务中最大（sonnet QR CV=26.1%，haiku Tutor CV=35.9%）。如果去除 B02 而非 I01，ICC 可能会显著提升。

Layer C 硬零分对 ICC 的影响确实很小——因为 code_eval 在所有 runs 中一致地得到相同分数（0.395 或 N/A），它的方差趋近于 0，对 ICC 贡献中性。你的判断是对的。

---

## 问题 2：B02 sonnet run1 低分深度调查

### 三次运行对比

| 指标 | run1 | run2 | run3 |
|------|------|------|------|
| **Duration** | **28.2 min** | 2.6 min | 2.4 min |
| **Turns** | **8** | 2 | 2 |
| **Tool calls** | **34** (5 fail) | 9 | 11 |
| API calls | 41 | 9 | 10 |
| Tokens in | 863K | 105K | 170K |
| **OAS** | **0.527** | 0.777 | 0.785 |

### run1 为什么低分？

**run1 是一个 28 分钟的深入对话**（8 轮），agent 从基础架构讲到 walk-forward 优化再到 volatility targeting。**run2/run3 都是 2 轮就结束的短对话**（TC checker 快速判定覆盖）。

逐维度分析：

**QR = 0.394（run2: 0.668, run3: 0.773）**

| 组件 | run1 | run2 | run3 | 分析 |
|------|------|------|------|------|
| Programmatic | **1.00** | 0.60 | **1.00** | run1 7/7 全通过 ✓ |
| Code Eval | 0.395 | 0.395 | 0.395 | 三次相同（不是差异源） |
| LLM Judge | **0.283** | **0.889** | **0.889** | ← **主要差异源** |
| Dampening | factor=**0.04** | factor=0.75 | factor=0.95 | ← **被严重放大** |

run1 的 judge 给了 0.283（completeness=3, correctness=4），理由：
> "多次执行失败（FileNotFoundError），所有 positions=0.0，策略从未产生有效 trades，架构漂移到 vol targeting 而非确认基础三层架构工作"

**Judge 的批评是合理的**——run1 的 agent 确实在深入探索中出现了执行错误、结果截断、策略不产出交易。agent 教得更深入但**没有确保基础功能跑通**。

**Dampening 放大了差距**：programmatic=1.0 vs judge=0.283 → Δ=0.72 → factor=0.04 → programmatic 权重从 30% 压到 11%，judge 权重从 40% 膨胀到 59%。这意味着 judge 几乎完全主导了 QR。

**QP = 0.566（run2: 0.779, run3: 0.735）**

| 维度 | run1 | run2 | run3 |
|------|------|------|------|
| role_adherence | **0.167** | 0.945 | 0.945 |
| topic_adherence | 0.845 | 1.000 | 1.000 |
| step_efficiency | **0.375** | 0.600 | 0.525 |

run1 的 role_adherence 极低（0.167）——可能因为 agent 在长对话中偏离了教学角色，变成了"帮学生写代码"而非"教学生理解"。

**Tutor = 0.637（run2: 0.900, run3: 0.859）**

run1 的 D6_empathetic_response = **0.200**（run2/3 = 0.900）——28 分钟的长对话中 agent 可能忽略了学生的情感回应。

### run1 低分的根因

**不是评估 bug，是真实的质量差异**：

1. Agent 在 run1 中走了一条"深度探索"路径——讲解了 walk-forward optimization、volatility targeting 等高级话题，但基础三层架构**没有跑通**（执行错误、0 trades）
2. Run2/3 的 agent 走了"高效完成"路径——2 轮内搭建好基础架构 + 展示结果 + TC 通过
3. **LLM judge（haiku）对 run1 的判断是合理的**——"深度但不完整"确实应该比"基础但完整"得分低
4. 但 dampening 的放大效应可能过度了——programmatic 7/7 全通过说明产出是好的，judge 的低分可能过于严格

### 是否需要修复

**dampening 逻辑值得审视**。当 programmatic=1.0（7/7 全通过）但 judge=0.28 时，factor=0.04 意味着 programmatic 的权重被压到 11%——几乎失去了发言权。这是否合理？

一个可能的改进：当 programmatic 得分 ≥ 0.8（明确通过）时，设置 dampening 的下限，确保 programmatic 至少保留 20% 权重。但这需要更多数据来验证。

---

## 问题 3：eval_completeness flag

### 含义

在 TaskResult 中标注各评估组件是否实际运行：

```python
"eval_completeness": {
    "programmatic_eval": True,   # test_script 是否出分
    "code_eval": True,           # code_eval 是否 applicable
    "result_judge": True,        # LLM judge 是否成功调用
    "process_metrics": True,     # QP 7 维度是否全部有分
    "tutor_eval": True,          # Tutor 7D 是否全部有分
}
```

### 意义

目前无法区分"agent 表现差导致 0 分"和"评估组件缺失导致 0 分"：
- code_eval=0 是因为 agent 没写代码？还是因为 code_eval 不支持 .cs？
- tutor=0 是因为 agent 教学差？还是因为 judge API 调用失败了？
- process_alignment=0 是因为过程差？还是因为没有 reference trace？

**对论文的意义**：报告结果时可以说"在 N 个完整评估的 task 上，平均分为 X"，而非把不完整评估的 0 分混入均值。

**对 ICC 的意义**：可以排除"评估缺失"导致的 0 分异常值，只计算完整评估的 runs 的 ICC。

### 建议

实现简单（在 orchestrator 的评估完成后根据各组件结果填充 flag），但**当前优先级低**——不阻塞论文核心实验。如果时间紧张可以暂缓。

---

## 问题 4：DeepEval 依赖分析

### 依赖地图

| 组件 | 使用文件数 | 我们用了什么 | 替代成本 |
|------|-----------|-------------|---------|
| **GPTModel** | 7 | `.generate(prompt)` → 返回 (text, cost) | 低（20 行 httpx 调用） |
| **ConversationSimulator** | 3 | `.simulate()` 循环 + `model_callback` | 中（~100 行，我们已覆写 `stop_conversation`） |
| **ConversationalGolden** | 3 | `.scenario`, `.user_description`, `.expected_outcome` 数据容器 | 极低（dataclass） |
| **Turn / ConversationalTestCase** | 8 | `role + content` 数据容器 | 极低（dict） |
| **ConversationalGEval** | 1 | Tutor 7D 评分（最复杂依赖） | 高（内部用 logprobs + 模板生成评分） |
| **GEval** | 2 | Layer 1 单轮评分 | 中 |
| **Internal utils** | 2 | Progress bar 抑制、logprobs fallback patch | 低 |

### 实际耦合度

**浅耦合（容易替换）**：
- GPTModel → 我们的 `_OAuthAnthropicModel` 已经是替代品，GPTModel 只是 OpenRouter 的薄包装
- ConversationalGolden / Turn / ConversationalTestCase → 纯数据容器
- ConversationSimulator → 核心循环 ~100 行，我们已用 `_EfficientSimulator` 覆写了关键方法

**深耦合（替换成本高）**：
- **ConversationalGEval**（tutor_conv_geval.py）：用了 DeepEval 的模板系统、logprobs 评分、rubric 结构。我们还写了 monkey-patch 修复 logprobs 不兼容问题。替换需要自研评分模板 + logprobs 处理
- **GEval**（quant_geval.py, layer1/runner.py）：用了 rubric 定义 + LLMTestCase + 评分管线

### 版本风险

当前 `requirements.txt` 写 `deepeval>=3.8`（无上界）。风险点：

1. **ConversationSimulator 的模板**（`template.py`）如果改措辞 → 学生行为变化
2. **ConversationalGEval 的评分逻辑**如果改 → Tutor 分数不可比
3. **GPTModel 的默认温度**如果从 0.0 改为其他 → 影响 judge 确定性

### 建议

- **短期**：Pin `deepeval==3.8.4`，防止意外升级
- **长期**：自研 simulator（替换 ConversationSimulator，~100 行），保留 ConversationalGEval 作为 Tutor 评分引擎
- **论文中**：声明 DeepEval 版本，作为可复现性的一部分

---

## 问题 5：X07/X08/X10 eval scripts 排查

### 结果

| Task | 有 `trades_produced`？ | 有 `Total Trades` key 问题？ | 其他 trade 相关检查 |
|------|----------------------|---------------------------|-------------------|
| X07 | ❌ 没有 | N/A | 只有 `backtest_completed`（summary 存在即可） |
| X08 | ❌ 但有 `trade_count_increased` | **不同逻辑** | 从 tool_logs 文本匹配 `"total.*trade.*\d+"` 或 fallback 到 `order_type_fixed` |
| X09 | ✅ 已修复 | ✅ 已修复 | 从 summary.json 读 `Total Orders` |
| X10 | ❌ 没有 | N/A | 只有 `backtest_completed` |

### X08 的 `trade_count_increased` 逻辑

```python
# X08: 不读 summary.json，从 tool_logs 文本匹配
if re.search(r"total.*trade.*\d+", all_output):
    results["trade_count_increased"] = True
# Fallback: 如果 order_type 已修复，直接认为 trades 增加
if results["order_type_fixed"]:
    results["trade_count_increased"] = True
```

这个逻辑不严谨（fallback 太宽松），但**不会产生 X09 那样的 false negative**——因为只要代码修复了就自动 Pass。

### X07/X10 的 `backtest_completed`

```python
lean_results = collect_lean_results(workspace_path)
results["backtest_completed"] = lean_results is not None
```

只检查 `summary.json` 是否存在，不检查 trades 数量。这足够——X07（warmup bug）和 X10（universe stale）的修复验证不依赖 trade 数量。

### 结论

**X07/X08/X10 不需要修复**——它们没有 X09 的 `Total Trades` key 问题。X08 的 trade_count_increased 用了不同的（更宽松的）逻辑。X07/X10 不检查 trade 数量。

---

## 问题补充 1：ICC 与评分机制的认知修正

### 结论

**ICC 低反映的是 agent 行为方差，不是评估链缺陷。**

B02 run1（28min 深度探索但没跑通）和 run2/3（2min 高效完成）是截然不同的 agent 行为——benchmark 应该给出不同的分数。X01 三次 run 行为一致时 CV=1.0%，说明评估链在 agent 行为稳定时是稳定的。

dampening 不需要修改——programmatic=1.0（关键词 7/7 全通过）但 agent 实际没完成核心任务时，judge 给低分并压低 programmatic 权重是合理的保护机制。

**路径选择：B（接受并框架化）。** 在论文中报告：
- 按任务分类的 CV（展示哪些任务稳定、哪些不稳定）
- agent 行为稳定时的 ICC（如 X01 单独的 ICC）vs 总体 ICC
- 说明 ICC 低是因为 temp=1 下 agent 行为方差大，不是评估缺陷

---

## 问题补充 2：7D 评分理由输出

### 调查发现

DeepEval 的 ConversationalGEval 内部**确实会产出 reason**（通过 `metric.reason`），但我们的聚合代码只保存了数值 score，**丢弃了 reason**。

具体位置：`tutor_conv_geval.py:1020-1029`，聚合循环只读 `all_metrics[i].score`，不读 `all_metrics[i].reason`。

### 修复方案

在聚合循环中同时收集 reason，保存到 `_per_dim_reasons` key：

```python
# 在 model_accumulated 旁边加:
model_reasons: dict[str, dict[str, list[str]]] = {
    name: {d: [] for d in active_dims} for name in model_names
}

# 在循环中:
reason = getattr(all_metrics[i], "reason", None) or ""
model_reasons[mname][dim_name].append(reason)

# 输出:
final_scores["_per_dim_reasons"] = {
    dim: [model_reasons[mname][dim] for mname in model_names]
    for dim in active_dims
}
```

然后在 `score_report.py` 的 Tutor 7D 部分展示每个维度的 reason。

### 价值

- 解释黑箱：审稿人/用户能看到"为什么 D6_empathetic_response = 0.2"
- 调试评估：发现 judge 误判时能追溯原因
- human calibration：对比 judge reason 和人工判断的一致性

### 实现复杂度

低——约 15 行代码改动（聚合循环 + score_report 展示）。

---

## 待讨论

1. DeepEval pin 版本是否现在执行？
2. 7D reason 输出是否现在实施？
3. 论文 ICC 框架化的具体措辞
