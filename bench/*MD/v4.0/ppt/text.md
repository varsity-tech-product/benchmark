# QuantTutorBench PPT 文字稿

> 按系统架构顺序组织，每页包含标题、图表说明、关键文字和演讲稿

---

## Slide 1: System Overview — The Exam Metaphor

**标题**: QuantTutorBench: A Standardized Exam for Quantitative Finance Tutoring Agents

**图表**: `slide_1/architecture.png` — 系统架构图（Server/Client 分离）

**关键文字**:
- Server provides: Tasks (65) + Tools (15 MCP) + Sandbox (Docker) + Student Simulator + Evaluation Pipeline
- Client provides: The agent (LLM / Human / Hybrid)
- All actions inside the exam hall are recorded — external preparation is allowed but unmonitored

**演讲稿**:
QuantTutorBench 的设计理念很简单：我们提供一场标准化考试。试卷是 65 个量化金融任务，考场是 Docker 沙箱加 15 个标准化工具，监考是学生模拟器，打分是我们的三维评估流水线。考生可以是任何能对话的东西——LLM agent、人类、或者两者混合。我们不限制考生的外部知识——就像开卷考试。但所有在考场里做的动作——说的每句话、调的每个工具——都通过我们的 Server 记录，一个不漏。

---

## Slide 2: Task Design — 65 Tasks Across 6 Categories

**标题**: Task Taxonomy: From Data Loading to Full Quant Workflows

**图表**: `slide_2/task_taxonomy.png` — 任务分类矩阵（类别 × 难度）

**关键文字**:
- 6 categories: Data Analysis (D), Strategy Research (S), Backtesting (B), Implementation (I), Debug (X), End-to-End (E)
- 3 difficulty levels: easy / medium / hard
- Each task: JSON definition with description, expected_outcome, required_capabilities, eval_script, termination_criteria
- 3 student personas: beginner_no_finance, intermediate_developer, advanced_quant

**演讲稿**:
65 个任务覆盖量化金融工作流的完整链条——从数据加载、策略研究、回测引擎搭建，到代码实现和 debug。每个任务有三个难度等级，配合三个不同水平的学生 persona。任务定义是标准化的 JSON 格式，包含任务描述、期望结果、能力要求和专门的评估脚本。这些 eval script 是人工编写的程序化检查——不是 LLM 判断，是确定性的 pass/fail。

---

## Slide 3: Conversation Simulation — Controlled Non-Determinism

**标题**: Student Simulator: Structurally Stable, Detailed Variation

**图表**: `slide_3/simulation_flow.png` — 对话循环流程图（Student → Agent → Tools → Student → TC Check）

**关键文字**:
- Student behavior is part of the EXAM, not infrastructure
- 3 personas with distinct questioning patterns (beginner asks "what is X?", advanced asks "under what conditions does X break?")
- TC Checker: bitmap-based incremental checking, ~97% token savings
- Non-determinism data: 0/24 exact match at temp=0, but Tutor ranking 8/8 consistent

**演讲稿**:
学生模拟器是试卷的一部分，不是基础设施。它不是一个简单的问答机器——它有角色定义、追问模式和终止判定。三个 persona 有完全不同的追问方式：beginner 会问"什么是 Sharpe ratio"，advanced 会问"在什么条件下 Sharpe 会误导你"。我们的 TC Checker 使用位图增量检查，当所有学习目标被覆盖后终止对话，节省了 97% 的 token。学生的具体措辞每次不同——但这不是 bug，这是 feature。它测试的是 agent 对多样化学生行为的鲁棒性。实验数据显示：虽然 0/24 个测试点达到完全一致的文本输出，但模型排名在所有任务上 8/8 方向一致。

---

## Slide 4: Tools & Environment — Standardized and Auditable

**标题**: 15 MCP Tools + Docker Sandbox = Fair and Observable

**图表**: `slide_4/tool_architecture.png` — 工具架构图（Client → WebSocket → MCP Proxy → Docker Container）

**关键文字**:
- 15-slot tool design: core tools + convenient tools + distractors = 15
- All tool calls go through MCP Proxy → 100% logged (name, args, result, duration, success)
- Docker: `--network none` (security), frozen image (consistency), per-task workspace (isolation)
- Programmatic eval on same run_state: haiku judge = sonnet judge = identical scores

**演讲稿**:
每个考生面对完全相同的 15 个工具——文件操作、数据分析、文档查询、回测执行、试验管理。所有工具调用必须经过我们的 MCP Proxy，每一次调用的参数、返回值、耗时和成功状态都被自动记录。Docker 容器提供隔离的执行环境——网络关闭防止代码泄漏，frozen 镜像保证每次运行的环境完全一致。最重要的证据：同一份运行结果交给两个不同的 judge 模型，Programmatic eval 和 Code eval 的分数完全一致——因为这些是确定性评分，不依赖 LLM。

---

## Slide 5: Three-Dimensional Evaluation Framework

**标题**: QR + QP + Tutor: What × How × Teach

**图表**: `slide_5/three_dim.png` — 三维评估框架图 + OAS 公式

**关键文字**:
- OAS = 0.70 × (0.50 × QR + 0.50 × QP) + 0.30 × Tutor
- QR: Was the result correct? (Programmatic + Code Eval + LLM Judge, with dampening)
- QP: Was the process effective? (Tool usage + Step efficiency + Code process + ...)
- Tutor: Was the teaching good? (7 dimensions: level detection, scaffolding, domain accuracy, ...)
- Each dimension has different determinism levels → different reliability profiles

**演讲稿**:
我们的评估有三个维度，回答三个不同的问题。QR 问"结果对不对"——通过程序化检查、代码执行和 LLM judge 混合评估。QP 问"行为好不好"——评估工具调用的选择、顺序、效率和代码迭代质量。Tutor 问"教得好不好"——从因材施教到知识脚手架到情感回应，7 个独立维度。每个维度的确定性程度不同：QR 最确定（有 programmatic 锚点），Tutor 最依赖 LLM judge。这不是缺陷——教学质量本身就无法用程序检查，必须用 LLM judge。

---

## Slide 6: QR Deep Dive — Programmatic Anchoring + Dampening

**标题**: QR Blending: Deterministic Scores Anchor LLM Judge

**图表**: `slide_6/qr_blending.png` — QR Blending 公式可视化（B01 实际数据）

**关键文字**:
- Three components: Programmatic Eval (check items) + Code Eval (3 layers) + LLM Result Judge
- Dampening: when programmatic and judge diverge, weights adjust dynamically
- B01 example: prog=1.000, code=0.500, judge=0.778 → dampening factor=0.855 → Final QR=0.859
- Cross-judge: programmatic scores identical (1.0000/1.0000), QR Pearson r=0.872 (highest)

**演讲稿**:
QR 的评分不是单一的 LLM 判断。它混合了三个成分：程序化评估是人工编写的 check items——比如"回测是否执行了""Sharpe ratio 是否出现在结果中"。Code eval 检查代码是否能跑、是否正确。LLM judge 做更细粒度的完整性和正确性判断。三者通过 dampening 机制动态融合——当程序化评分和 LLM judge 意见分歧大时，权重自动调整。这个设计让 QR 的跨 judge 一致性达到了 0.872——是所有维度中最高的。

---

## Slide 7: QP Deep Dive — Observable Behavioral Quality

**标题**: QP: What We See in the Exam Hall

**图表**: `slide_7/qp_dimensions.png` — QP 7 维度权重 + 评分方式（程序化 vs LLM）

**关键文字**:
- QP measures OBSERVABLE behavior, not internal reasoning
- 7 sub-dimensions: tool_usage (0.20, math), step_efficiency (0.15, hybrid), process_reasonableness (0.20, LLM), code_process (0.15, LLM), process_alignment (0.10, LLM), role_adherence (0.10, LLM), topic_adherence (0.10, LLM)
- Agent-agnostic: doesn't matter if client used external knowledge — execution quality still varies
- Evidence: Sonnet vs Haiku QP d=1.11 (large), both "open book"

**演讲稿**:
QP 评的不是"agent 怎么想的"，而是"agent 在考场里做了什么"。7 个子维度覆盖工具选择、步骤效率、过程合理性、代码质量、角色遵守等。tool_usage 完全是数学公式，step_efficiency 混合了程序化计算和 LLM 判断，其余主要依赖 LLM。即使考生有外部知识——知道答案不等于执行过程高效。我们的数据证明了这一点：Sonnet 和 Haiku 都是"开卷"的 LLM，但 QP 的 Cohen's d=1.11，是 large effect。行为质量的差异是真实的。

---

## Slide 8: Tutor 7D Deep Dive — Teaching Quality

**标题**: Tutor 7D: Can the Agent Actually Teach?

**图表**: `slide_8/tutor_7d.png` — 7 维度雷达图（Sonnet vs Haiku 对比）

**关键文字**:
- 7 dimensions: D1 Level Detection, D2 Language Adaptation, D3 Scaffolding, D4 Domain Accuracy, D5 Code Teaching, D6 Empathetic Response, D7 Safety Boundaries
- Per-category weights (e.g., implementation tasks weight D5 higher)
- 3 shuffled judge runs per evaluation → per-run Std: 4/7 dims = 0.000
- Each dimension outputs a text reason explaining the score
- Granularity ablation: clamp 10-level to 5-level → d unchanged (1.75 → 1.82)

**演讲稿**:
Tutor 是我们最创新的评估维度。7 个子维度分别衡量：agent 能不能检测到学生水平、能不能调整语言、能不能搭建知识脚手架、领域知识准不准确、代码教学好不好、有没有情感回应、有没有遵守安全边界。每个维度的权重根据任务类别动态调整——比如 implementation 任务更看重代码教学。每次评估跑 3 次 shuffled judge run 来消除维度顺序偏差，实测 4/7 维度三次完全一致。我们还做了粒度消融实验——把 10 档评分降到 5 档，区分度不降反升，证明 d=1.69 反映的是真实能力差异。

---

## Slide 9: Cross-Judge Reliability

**标题**: Two Independent Judges, Same Rankings

**图表**: `slide_9/cross_judge.png` — 双 judge 对比表 + 散点图

**关键文字**:
- Haiku judge: systematically inflates Tutor by +0.198, compresses distribution
- Sonnet judge: stricter, wider distribution → higher discrimination (d=1.69 vs 0.80)
- Rankings 100% consistent across both judges (8/8 tasks)
- QR r=0.872 (anchored by programmatic), QP r=0.806, Tutor r=0.676

**演讲稿**:
我们用两个独立的 judge 模型对同一份对话评分，交叉验证评估的可靠性。Haiku judge 给 Tutor 系统性偏高 0.2 分——它不是"不准"，而是"不区分"，给所有对话都打高分。Sonnet judge 更严格，分数分布更宽，区分度更高。但最关键的发现是：不管用哪个 judge，模型排名 100% 一致——8 个任务中 Sonnet agent 都优于 Haiku agent。绝对分数变了，结论不变。

---

## Slide 10: Reproducibility — Rankings Hold, Not Numbers

**标题**: Statistical Reproducibility: Decomposing Variance Sources

**图表**: `slide_10/variance_decomposition.png` — 误差来源分层条形图 + B02 vs X01 对比

**关键文字**:
- Variance sources (largest to smallest): Agent behavior (temp=1) > Student non-determinism > LLM judge bias > API noise
- B02: CV=18.6% — agent sometimes completes, sometimes doesn't (real behavioral variance)
- X01: CV=1.2% — agent consistently solves the task (evaluation chain is stable)
- Model rankings: 100% consistent across 3 runs × 2 judges × 8 tasks

**演讲稿**:
我们不追求"跑两次数字一样"——这在多轮交互 benchmark 中不可能也不需要。我们追求的是"跑多次结论不变"。方差有四个来源：最大的是 agent 行为方差，因为 thinking 要求 temperature=1；其次是学生的非确定性；然后是 LLM judge 的偏差；最小的是 API 的随机性。B02 的 CV=18.6% 看起来很高，但每一次运行的分数差异都可以追溯到 agent 的具体行为——run1 没完成 engine，run2 完成了。而 X01 的 CV=1.2% 证明当 agent 行为一致时，评估链本身是高度稳定的。

---

## Slide 11: Multi-Dimensional Necessity

**标题**: Without Tutor, Models Are Indistinguishable

**图表**: `slide_11/discrimination.png` — 区分度条形图 + QR-Tutor 散点图

**关键文字**:
- QR only: d=0.24 (negligible) — both models get correct answers
- QR + QP: d≈0.68 (medium)
- QR + QP + Tutor: d=1.69 (very large) — 7× improvement
- QR-Tutor correlation: r=0.25 (independent) — correct results ≠ good teaching
- Dimensions answer different questions: "Does it know?" × "How does it work?" × "Can it teach?"

**演讲稿**:
如果只看结果——QR——Sonnet 和 Haiku 几乎不可区分，Cohen's d 只有 0.24。两个模型都能算对均线、跑通回测。差距在哪里？在过程和教学上。加入 QP 后 d 升到 0.68，加入 Tutor 后 d 达到 1.69——区分度提升了 7 倍。QR 和 Tutor 的相关性只有 0.25，说明"做得对"和"教得好"是两个独立的能力维度。这就像一个数学天才不一定是好老师。多维度评估不是锦上添花——它是 benchmark 区分能力的核心来源。

---

## Slide 12: Summary & Key Numbers

**标题**: QuantTutorBench at a Glance

**图表**: `slide_12/summary.png` — 核心数据仪表盘

**关键文字**:

| Metric | Value |
|--------|-------|
| Tasks | 65 (6 categories × 3 difficulties) |
| Student Personas | 3 (beginner / intermediate / advanced) |
| Tools | 15 MCP (standardized) |
| Evaluation Dimensions | 3 (QR + QP + Tutor 7D) |
| Tutor Cohen's d | **1.69** (very large) |
| Cross-judge ranking consistency | **100%** (8/8 tasks) |
| QR cross-judge Pearson r | **0.872** |
| Granularity ablation | d: 1.75 → 1.82 (粒度不影响区分度) |
| Reproducibility | Rankings hold across 3 runs × 2 judges |

**演讲稿**:
总结一下。QuantTutorBench 提供标准化的考场环境——65 个任务、15 个工具、3 个学生 persona——评估考生在考场内的可观测行为和教学表现。三维评估将区分度从 near-zero 提升到 very large。双 judge 交叉验证证明排名 100% 一致。评分粒度消融证明区分度来自真实能力差异。我们的可复现性不在于数字一致，而在于结论不变。
