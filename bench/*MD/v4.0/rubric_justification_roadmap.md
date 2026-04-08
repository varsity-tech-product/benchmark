# Rubric Justification Roadmap

> 日期：2026-04-08
> 目的：为 QuantTutorBench 的评分体系补充理论依据和实证验证，回应 reviewer 对维度选择、权重分配、评分设计的合理性质疑
> 状态：框架已定，各项待细化执行

---

## 零、核心命题

我们是交易公司，定义"什么是好的量化教学"这件事本身没有争议。但论文不能只写"我们觉得这 7 个维度重要"——reviewer 会问：

1. **为什么是这 7 个维度，不是 5 个或 9 个？**
2. **为什么权重是这个比例？**
3. **有没有教育学或量化金融领域的文献支撑？**

需要做的不是改决策，而是**给决策补上 justification**。本质上：domain expertise 是真实的，论文要做的是让这个 expertise 的推理过程对外部可见，让 benchmark 更容易被社区采纳。

---

## 一、Tutor 7D 维度选择：映射到已有教学评估框架

### 1.1 问题

当前 Tutor 7D（Level Detection, Language Adaptation, Scaffolding Calibration, Domain Accuracy, Code Teaching, Empathetic Response, Safety & Boundaries）的选择缺少理论锚点。Reviewer 会认为这是"拍脑袋想的"。

### 1.2 策略：在已有理论上做领域适配

不照搬任何单一框架，而是说明"我们的 7 个维度覆盖了 X 框架中的 Y 原则，并针对量化金融 AI 教学的特殊性增加了领域准确性和工具使用"。

### 1.3 映射表

| Tutor 维度 | Merrill's First Principles (2002) | Chi et al. Tutoring Hypotheses (2001) | Bloom's Revised Taxonomy (Anderson & Krathwohl, 2001) | 量化金融特殊性 |
|---|---|---|---|---|
| **D1 Level Detection** | Activation（激活已有知识） | Student-centered hypothesis（tutor 需识别学生知识状态） | 对应 Remember/Understand 层级判断 | — |
| **D2 Language Adaptation** | — | Interactive hypothesis（有效互动需语言匹配） | — | 量化金融术语密度极高，错配直接阻断学习 |
| **D3 Scaffolding Calibration** | Application + Integration（循序渐进引导应用） | Tutor-centered hypothesis → Interactive（从讲授到引导的连续体） | 对应 Apply/Analyze 层级的支架设计 | — |
| **D4 Domain Accuracy** | Demonstration（示范必须正确） | — | — | **量化金融核心**：公式错误直接导致资金损失，标准比一般教学高 |
| **D5 Code Teaching** | Application（让学生在实践中学习） | — | 对应 Apply/Create 层级 | **量化特殊**：代码即策略，教学和实现不可分离 |
| **D6 Empathetic Response** | — | Interactive hypothesis（tutor 响应学生情感状态提升学习效果） | Affective domain (Krathwohl) | 量化任务挫败感高（debug 回测、数据异常），情感支持影响学习持续性 |
| **D7 Safety & Boundaries** | — | — | — | **量化金融独有**：拒绝投资建议是法律合规要求，无教育学对标 |

### 1.4 论文表述模板

> "The seven tutoring dimensions draw on established instructional design theory. D1 (Level Detection) and D3 (Scaffolding Calibration) operationalize Merrill's (2002) Activation and Application principles; D2 (Language Adaptation) and D6 (Empathetic Response) reflect Chi et al.'s (2001) finding that interactive tutoring — requiring the tutor to adapt to the student's communicative and affective state — produces superior learning outcomes compared to didactic instruction. D4 (Domain Accuracy) and D5 (Code Teaching) are domain-specific adaptations necessitated by quantitative finance, where factual errors carry financial consequences and code is the primary medium of strategy expression. D7 (Safety & Boundaries) addresses regulatory compliance unique to financial education, with no direct analog in general pedagogical frameworks."

### 1.5 文献

| # | 文献 | 用途 |
|---|------|------|
| 1 | **Merrill, M.D. (2002). First Principles of Instruction.** ETR&D, 50(3), 43-59. [Springer](https://link.springer.com/article/10.1007/BF02505024) / [Free PDF](https://mdavidmerrill.files.wordpress.com/2019/04/firstprinciplesbymerrill.pdf) | 五大原则（真实问题、激活知识、示范、应用、融入场景）→ 支撑 D1/D3/D4/D5 |
| 2 | **Chi, M.T.H. et al. (2001). Learning from Human Tutoring.** Cognitive Science, 25(4), 471-533. [Wiley](https://onlinelibrary.wiley.com/doi/10.1207/s15516709cog2504_1) / [ResearchGate](https://www.researchgate.net/publication/222651587_Learning_from_human_tutoring) | Tutor-centered/Student-centered/Interactive 三假设 → 支撑 D1/D2/D6 的交互性要求 |
| 3 | **VanLehn, K. (2011). The Relative Effectiveness of Human Tutoring, ITS, and Other Tutoring Systems.** Educational Psychologist, 46(4), 197-221. [T&F](https://www.tandfonline.com/doi/abs/10.1080/00461520.2011.611369) | 人类 tutoring d=0.79, ITS d=0.76 → 支撑"用 AI 评 AI tutoring"这个设定的意义 |
| 4 | **Anderson, L.W. & Krathwohl, D.R. (2001). A Taxonomy for Learning, Teaching, and Assessing.** ISBN: 978-0801319037 | Bloom's Taxonomy 修订版 → 支撑学生 persona 知识水平分级 + D1/D3 的层级判断 |

### 1.6 待办

- [ ] 确认每个维度的映射准确性（需要通读 Merrill 和 Chi 的原文）
- [ ] 如果发现某个维度映射很勉强，考虑调整论文表述而非强行对标
- [ ] 考虑是否需要引用更多 ITS (Intelligent Tutoring Systems) 文献，如 VanLehn (2006) "The Behavior of Tutoring Systems"

---

## 二、QR/QP 权重与 OAS 权重：Sensitivity Analysis

### 2.1 问题

- OAS = 0.70 × QAI + 0.30 × TEI —— 为什么七三开？
- QAI = 0.50 × QR + 0.50 × QP —— 为什么五五开？

Reviewer 会问：换成六四、八二，结论变不变？

### 2.2 当前实际权重（代码验证 2026-04-08）

```python
# scoring.py
QUANT_WEIGHT = 0.70   # QAI in OAS
TUTOR_WEIGHT = 0.30   # TEI in OAS
RESULT_WEIGHT = 0.50  # QR in QAI
PROCESS_WEIGHT = 0.50 # QP in QAI

# QR blend (orchestrator.py) — 非固定权重，使用 sigmoid dampening：
#   divergence = |programmatic - llm_judge|
#   factor = 1 / (1 + exp(10 × (divergence - 0.40)))
#   with code_eval:    w_prog = 0.10 + 0.20×factor, w_code = 0.30, w_judge = 1 - w_prog - w_code
#   without code_eval: w_prog = 0.15 + 0.25×factor, w_judge = 1 - w_prog

# Result Judge sub-weights (result_judge.py):
#   completeness: 0.55, correctness: 0.45  (NOT 3-dim 0.35/0.35/0.30)
```

### 2.3 策略：扫描权重空间，展示排名稳定性

把顶层权重当作变量，在合理范围内网格搜索：

```
OAS 中 QAI 权重: α ∈ {0.50, 0.60, 0.70, 0.80, 0.90}  (TEI = 1 - α)
QAI 中 QR 权重:  β ∈ {0.30, 0.40, 0.50, 0.60, 0.70}  (QP = 1 - β)
```

总共 25 个组合。对每个组合重新计算所有 agent 的 OAS，检查：

1. **Top-3 排名是否稳定**：如果无论怎么调权重，Top-3 都是同一批 agent → "our conclusions are robust to weight selection"
2. **Kendall's τ 排名相关**：所有 25 个排名两两比较，报告 τ 的 min/mean/max
3. **Critical switching point**：如果存在某个 α 值使得排名剧变 → 说明该维度是区分性最强的维度（本身就是有意义的发现）

> **注意**：QR 内部的 blend 权重（sigmoid dampening）不纳入 sensitivity sweep——那是 eval pipeline 的信号融合逻辑，不是 benchmark 的设计参数。Sensitivity analysis 只覆盖顶层可选权重（α, β）。

### 2.4 论文表述模板

**如果稳定：**
> "We conducted a sensitivity analysis over the weight space α ∈ [0.5, 0.9] (QAI weight in OAS) and β ∈ [0.3, 0.7] (QR weight in QAI). Across all 25 configurations, the top-3 agent ranking remained unchanged (Kendall's τ > 0.95), indicating that our conclusions are robust to the specific weight choices."

**如果不稳定：**
> "Sensitivity analysis reveals that agent rankings are most sensitive to the QAI-TEI weight (α), with a phase transition at α ≈ 0.6 where Agent X overtakes Agent Y. This suggests that tutoring effectiveness is the primary discriminator between top-performing agents — a finding consistent with our observation that QR alone yields near-zero Cohen's d between Sonnet and Haiku."

### 2.5 待办

- [ ] 收集足够多 agent 的完整分数数据（至少 3-4 个不同模型）
- [ ] 实现 sensitivity sweep 脚本（纯计算，不需要重跑 eval）
- [ ] 生成 heatmap 可视化（α × β → Kendall's τ）
- [ ] 如果 QP 7 维度的内部权重也被 challenge，同样可以做内部 sensitivity analysis

---

## 三、QR Ground Truth Validation：用 quant 标准解法验证评分

### 3.1 问题

QR 评分（3-source blend）是否真的在衡量"量化分析结果的正确性"？有没有可能一个有 bug 的策略拿了高分，或者一个正确策略被低估？

### 3.2 策略：用 quant 手写的标准解法做 ground truth validation

我们是交易公司，最大的优势就是**知道什么策略是对的**。

**选取 3-5 个经典任务：**

| 任务 | 类型 | 预期标准解法 |
|------|------|-------------|
| S01 MA Crossover | Strategy | 经典 SMA 交叉，预期收益/Sharpe 在已知范围内 |
| I01 SMA Trend Filter | Implementation | LEAN C# 标准实现，85 trades, Sharpe 0.168 |
| D01 (Data Analysis) | Data Analysis | 标准统计摘要，已知 key_results |
| X01 (Debug) | Debug | 已知 bug 位置 + 正确修复 |
| B01 (Backtest) | Backtest | 标准回测指标解读 |

**对每个任务：**

1. Quant 手动写出标准解法和预期回测指标（已有的 reference 可以直接用）
2. 收集多个 agent 的输出（包括明显好的和明显差的）
3. 让 quant 人工打分（5 点量表：0.0 / 0.25 / 0.5 / 0.75 / 1.0）
4. 比较人工 QR 评分和自动 QR 评分的相关性

**关键检验：**
- Pearson r > 0.7 → QR 评分和人类 quant 判断高度一致
- 如果某个 agent 有 bug 但 QR 给了高分 → 说明 QR 评分逻辑有漏洞，需修复
- 如果 Pearson r < 0.5 → QR 评分需要系统性重新校准

### 3.3 论文表述模板

> "To validate the QR scoring pipeline, we recruited two quantitative analysts from our team to independently score a subset of 5 tasks × 4 agents = 20 evaluation instances. Human-QR Pearson correlation was r = X.XX (p < 0.001), with primary disagreements occurring in [specific scenario]. This validates that the automated QR score tracks expert judgment on quantitative correctness."

### 3.4 待办

- [ ] 选定验证任务集（建议复用已有 reference 最完善的任务）
- [ ] 设计人工评分表（需要明确评分标准，避免人工评分自身不一致）
- [ ] 收集至少 2 个 quant 的独立评分（计算 inter-rater reliability）
- [ ] 如果人力有限，3 个任务 × 3 个 agent = 9 个 case 也够

---

## 四、Human Baseline：人机对照

### 4.1 问题

Benchmark 的评分体系是否对人类也适用？还是只能评 AI 的奇怪指标？读者需要一个参照系。

### 4.2 策略：让 1-2 个 junior quant 做同样的教学任务

**实验设计（最简版）：**

- 选取 2-3 个任务（建议 S01 + D01 + I01，覆盖策略/数据/实现）
- 每个任务 × 1 个 persona（建议 intermediate，避免极端）
- Junior quant 面对同一个学生 persona，使用同样的工具集，进行教学
- 学生由同一个 student simulator 扮演（保持一致性）
- 用同样的评分链（QR + QP + Tutor 7D）评估 quant 的教学

**不需要严格对照实验**，只需要一个锚点。

### 4.3 论文表述模板

> "As an anchor, we evaluated a human quantitative analyst (2 years experience) on the same tasks using our scoring pipeline. The human tutor scored OAS = X.XX (QR = X.XX, QP = X.XX, Tutor = X.XX), compared to the strongest agent at OAS = Y.YY. The gap was concentrated in [dimension] — the human tutor [did better/worse at Z], suggesting that [insight]. This demonstrates that (a) our scoring framework produces meaningful scores for human tutors, not just AI agents, and (b) provides an intuitive reference point for interpreting agent performance."

### 4.4 待办

- [ ] 确认是否有 junior quant 可以投入 2-3 小时做这个实验
- [ ] 如果不可行，考虑用高级 prompt engineering（让 GPT-5.2 with careful system prompt 扮演 quant）作为 pseudo-human baseline——但论文里要说清楚这不是真人
- [ ] 设计实验流程：quant 使用什么界面？直接调 MCP tool？还是用 gym 接口包装一个人工模式？

---

## 五、Distractor 工具设计依据

### 5.1 问题

15 个工具槽（core + convenient + distractor），distractor 从 105 个候选池中随机采样填满剩余槽位。设计很好——但为什么是这 105 个候选？Reviewer 需要看到选择逻辑。

### 5.2 策略（按证据强度排序）

**最强：Pilot study 数据驱动**

> "We analyzed agent tool-call logs from a pilot study (N = XX runs across YY tasks) and identified the ZZ most frequently misused tools. These tools share semantic similarity with correct tools in name or description (e.g., `deploy_trading_bot` vs. core trading analysis tools), making them plausible but incorrect choices."

**中等：语义相似性分析**

> "Distractor tools were selected to share semantic overlap with correct tools — `deploy_trading_bot` mirrors the trading domain vocabulary of core analysis tools; `send_notification` resembles communication-related tool names. This simulates the tool selection noise agents face in real-world environments where APIs are discovered, not curated."

**最弱（但可接受）：设计原则声明**

> "Distractor tools simulate the realistic scenario where agents must select from a noisy tool inventory. Each distractor was designed to be (a) superficially plausible in a quantitative finance context and (b) clearly incorrect for any benchmark task upon inspection of the tool schema."

### 5.3 已有的支撑数据

- 当前顶级模型 distractor 调用率 < 2%（来自 consensus report）
- 可以分析现有 proxy log 提取 distractor 调用的具体 pattern
- 如果有 pilot 阶段的 log，可以量化"哪些 distractor 最容易被误调用"

### 5.4 已有数据

- 105 个 distractor 候选池（`bench/mcp_servers/distractors/distractor_tools.py`）
- 15 总槽位，seed-based 随机采样（`bench/mcp_servers/registry.py`，`_TOTAL_TOOL_SLOTS = 15`）
- 顶级模型 distractor 调用率 < 2%（consensus report）

### 5.5 待办

- [ ] 从现有 proxy log 中统计 105 个 distractor 各自被调用的频率（哪些最容易被误调）
- [ ] 计算 distractor 工具描述与 core 工具描述之间的 embedding cosine similarity
- [ ] 如果相似度数据有意义，可以在论文中放一个 distractor-core similarity heatmap
- [ ] 分析采样 seed 对 distractor 组合的影响——不同 seed 下 agent 表现是否一致（可选，工作量大）

---

## 六、LLM-as-Judge 方法论依据

### 6.1 问题

用 LLM 评分本身需要 justify——为什么 LLM judge 的分数是可信的？

### 6.2 已有的防御措施（需在论文中显式声明）

| 已知 Bias | 我们的 Mitigation | 文献依据 |
|-----------|-------------------|----------|
| Position bias | 3× shuffled evaluation → 取平均 | Zheng et al. (2023) MT-Bench |
| Self-enhancement bias | Multi-model judge（架构支持多模型并行，当前默认 sonnet-4.6 单模型；gpt-5.2 / opus-4.6 可通过配置启用） | Zheng et al. (2023) |
| Verbosity bias | 10-point ordinal scale (1-10) → normalize_10pt → [0.0, 1.0]，离散化限制评分噪声 | Zheng et al. (2023) |
| Single-model variance | Cross-model averaging 架构已实现（`EVAL_DEFAULT_MODELS` 列表），可随时扩展 | — |
| Judge temperature | temp=0.0 强制 greedy decoding | 内部实验验证（consensus report §1.2） |

> **注意：当前状态 vs 论文主张**
> - Result Judge 使用 **2 个子维度**：Completeness (0.55) + Correctness (0.45)，不是旧文档中的 3 个
> - 量表是 **10-point linear**（1→0.0, 10→1.0），不是 5-point ordinal
> - 多模型 judge 架构已实现但当前默认只跑 **1 个模型**（sonnet-4.6），论文中如果要声称 multi-model averaging 需要先启用多模型配置并重跑实验

### 6.3 文献

| # | 文献 | 用途 |
|---|------|------|
| 5 | **Zheng, L. et al. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.** NeurIPS 2023. [arXiv](https://arxiv.org/abs/2306.05685) / [OpenReview](https://openreview.net/forum?id=uccHPGDlao) | LLM-as-judge 方法论，position/verbosity/self-enhancement bias → 支撑我们的 shuffle + multi-model + ordinal scale 设计 |

### 6.4 额外支撑：双 Judge 一致性

已有数据：Haiku Judge vs Sonnet Judge

- QR 相关 r=0.872, QP r=0.806, Tutor r=0.676
- 排名 100% 一致（Sonnet > Haiku 在所有任务上）
- 这本身就是 judge reliability 的实证

### 6.5 待办

- [ ] 确认论文中是否已引用 Zheng et al. (2023)
- [ ] 考虑补充 human-judge correlation（和 §三 的 ground truth validation 合并做）

---

## 七、Agent Benchmark 整体架构依据

### 7.1 对标文献

| # | 文献 | 我们借鉴了什么 |
|---|------|---------------|
| 6 | **Jimenez et al. (2024). SWE-bench.** ICLR 2024. [arXiv](https://arxiv.org/abs/2310.06770) / [OpenReview](https://openreview.net/forum?id=VTF8yNQM66) | Benchmark spec + reference implementation 分离策略；Docker 隔离环境 |
| 7 | **Mialon et al. (2023). GAIA: a benchmark for General AI Assistants.** [arXiv](https://arxiv.org/abs/2311.12983) / [HuggingFace](https://huggingface.co/papers/2311.12983) | Agent 使用工具完成任务的评估；工具选择能力（对标 tool_usage + distractor）|

### 7.2 量化金融领域标准

| # | 文献 | 用途 |
|---|------|------|
| 8 | **CFA Institute CBOK & Learning Outcome Statements** — [Level I](https://www.cfainstitute.org/sites/default/files/docs/programs/cfa-program/2026-l1-topics-combined.pdf) / [Level II](https://www.cfainstitute.org/sites/default/files/-/media/documents/study-session/2025-l2-topics-combined.pdf) | 任务 taxonomy 的知识领域覆盖 + 难度分级（beginner/intermediate/advanced 对应 CFA Level 分布） |

### 7.3 待办

- [ ] 在 Related Work 中明确写出与 SWE-bench / GAIA / AgentBench 的对比定位
- [ ] 考虑引用 tau-bench（域特定多轮交互）如果有正式发表

---

## 八、执行优先级

按投入产出比排序：

| 优先级 | 项目 | 工作量 | 说服力 | 依赖 |
|--------|------|--------|--------|------|
| **P0** | §一 Tutor 7D 理论映射 | 低（写作为主） | 高（直接回应 reviewer 最常见的质疑） | 需通读 2-3 篇核心文献 |
| **P0** | §六 LLM-as-Judge 方法论 | 低（已有数据，写作为主） | 高（必引 Zheng et al.） | 无 |
| **P1** | §二 Sensitivity Analysis | 中（需写脚本 + 跑数据） | 高（直接证明 robustness） | 需要多 agent 评分数据 |
| **P1** | §五 Distractor 分析 | 低-中（分析 log 数据） | 中（reviewer 可能不会重点 challenge） | 需要 proxy log |
| **P2** | §三 QR Ground Truth | 中（需 quant 投入 2-3 小时） | 高（但不是所有 reviewer 都会要求） | 需要人力协调 |
| **P2** | §四 Human Baseline | 高（需 quant 投入半天） | 高（给读者直观参照系） | 需要人力协调 + 实验设计 |

### 最小可行论文版本

只做 P0 + P1（§一 + §二 + §五 + §六），大约 1-2 天工作量，就能回应 reviewer 的绝大多数合理质疑。P2 是锦上添花，如果时间允许再做。

---

## 九、完整文献索引

| # | 引用 | 论文中使用位置 |
|---|------|---------------|
| 1 | Merrill (2002). First Principles of Instruction. | §3 Tutor 7D 维度设计依据 |
| 2 | Chi et al. (2001). Learning from Human Tutoring. | §3 Tutor 7D 维度设计依据 |
| 3 | VanLehn (2011). Relative Effectiveness of Human Tutoring, ITS. | §1 Introduction / §3 评估意义 |
| 4 | Anderson & Krathwohl (2001). Bloom's Revised Taxonomy. | §3 Persona 分级设计 |
| 5 | Zheng et al. (2023). Judging LLM-as-a-Judge. NeurIPS 2023. | §4 评估方法论 |
| 6 | Jimenez et al. (2024). SWE-bench. ICLR 2024. | §2 Related Work + §4 架构设计 |
| 7 | Mialon et al. (2023). GAIA. | §2 Related Work |
| 8 | CFA Institute CBOK (2025/2026). | §3 任务 taxonomy 知识领域映射 |
