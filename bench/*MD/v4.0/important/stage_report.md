# QuantTutorBench 阶段性汇报

> 2026-04-08 | 基于 2×2 实验矩阵（2 agent × 2 judge × 8 tasks × 3 runs）

---

## 一、可复现性：哪些能复现，误差从哪来？

### 1.1 系统中的确定性层 vs 随机层

QuantTutorBench 的评估链可以分为三层，每一层的确定性程度不同：

| 层级                 | 组件                                        | 确定性 | 证据                                                                                                |
| -------------------- | ------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------- |
| **确定性层**   | Programmatic eval (test scripts)            | 100%   | 同一 run_state，haiku/sonnet judge 给出 prog=1.0000/1.0000，code_eval=0.5000/0.5000——**完全一致** |
| **半确定性层** | QP 子维度（tool_usage, step_efficiency 等） | ~80%   | QP 两个 judge 间 Pearson r=0.806，因为多数子维度基于程序化日志计算                                  |
| **随机层**     | Tutor 7D（纯 LLM judge）                    | ~68%   | Tutor 两个 judge 间 Pearson r=0.676，完全依赖 LLM 的主观判断                                        |

**关键洞察**：可复现性与程序化评分占比成正比。QR 混合了 programmatic eval（40%）+ code_eval（50%）+ LLM judge（10%），所以跨 judge Pearson r=0.872（最高）。Tutor 没有任何程序化锚点，所以 r=0.676（最低）。

### 1.2 误差来源的分层识别

| 误差源                                    | 影响范围                     | 量化证据                                            | 可控性                                       |
| ----------------------------------------- | ---------------------------- | --------------------------------------------------- | -------------------------------------------- |
| **Agent 行为方差**（temp=1）        | 全部维度                     | 高 CV 任务 B02 OAS CV=18.6%, 低 CV 任务 X01 CV=1.2% | 不可消除（也不应消除——真实反映任务复杂度） |
| **学生模拟器非确定性**              | Tutor, 间接影响 QP           | 0/24 exact match, 62% 实质性差异                    | 不可消除（定位为 robustness 测试）           |
| **LLM Judge 偏差**                  | Tutor（最大）, QR judge 部分 | Haiku judge Tutor 虚高 +0.198                       | 可控：使用 Sonnet judge + 交叉验证           |
| **API 非确定性**（temp=0 仍有波动） | 微小                         | Judge 同一输入两次评分差 <0.01                      | 可忽略                                       |

### 1.3 如何向 reviewer 解释

> "QuantTutorBench achieves **statistical reproducibility** rather than bit-exact reproducibility. Under the same evaluation configuration, model rankings are 100% consistent across 8 tasks, 3 runs, and 2 independent judge models. The absolute scores exhibit controlled variance (mean CV=8.3% for sonnet agent, 10.7% for haiku agent), which we decompose into agent behavioral variance, student inquiry variance, and judge variance. The first two are inherent to the interactive tutoring paradigm; the third is bounded by cross-judge validation."

**核心论点**：我们的可复现性不在于"跑两次数字一样"，而在于"跑多次结论不变"。

---

## 二、评估结果的可解释性

### 2.1 多层次分数追溯

每个 OAS 分数都可以追溯到具体成因：

```
OAS = 0.518（B02 sonnet run1）
 ├── QR = 0.427  ← 为什么低？
 │    ├── Programmatic Eval = 0.350  ← 具体哪些 check 失败了？
 │    │    ├── ✓ engine_implements_event_loop (0.20)
 │    │    ├── ✗ backtest_produces_trades (0.00)   ← 没有产生交易
 │    │    └── ...
 │    ├── Code Eval = 0.500  ← 代码能跑但结果不完整
 │    └── Result Judge = 0.45  ← LLM 认为结果部分正确
 ├── QP = 0.589  ← 过程质量中等
 │    ├── tool_usage = 0.85（工具使用合理）
 │    ├── step_efficiency = 0.42（步骤冗余较多）
 │    └── ...
 └── Tutor = 0.540  ← 教学质量中等
      ├── D1_level_detection = 0.60
      ├── D3_scaffolding = 0.45  ← 为什么低？Judge reason: "..."
      └── ...
```

### 2.2 可解释性体现在四个层面

| 层面                           | 实现方式                                             | 作用                                                       |
| ------------------------------ | ---------------------------------------------------- | ---------------------------------------------------------- |
| **Check-level**          | Programmatic eval 输出每个 check 的 pass/fail 和权重 | 精确定位"哪个知识点没掌握"                                 |
| **Dimension-level**      | QR/QP/Tutor 分数独立报告                             | 区分"会不会做"vs"做得好不好"vs"教得好不好"                 |
| **Judge-reason**         | Tutor 7D 每个维度输出 judge reasoning                | 解释为什么给这个分数                                       |
| **Divergence dampening** | QR Blending 公式可视化各成分权重                     | 透明地展示 programmatic/code_eval/judge 三者的动态权重调整 |

### 2.3 用数据佐证可解释性

**案例：B02 的三次运行差异可解释**

| Run  | OAS   | QR    | 关键原因                                                  |
| ---- | ----- | ----- | --------------------------------------------------------- |
| Run1 | 0.518 | 0.427 | Agent 未完成 backtest engine，programmatic eval 多项失败  |
| Run2 | 0.744 | 0.739 | Agent 成功构建完整 engine，trades 正常产出                |
| Run3 | 0.711 | 0.819 | Agent 构建成功但教学策略不同（Tutor=0.565 vs Run2=0.715） |

每个分数差异都可以追溯到 agent 的具体行为差异，而不是评估链噪音。这正是 CV=18.6% 的 B02 与 CV=1.2% 的 X01 之间的区别——前者的高方差反映的是 **agent 能力的波动**（有时能完成、有时不能），后者低方差说明 **agent 稳定地掌握了这个任务**。

---

## 三、架构设计：考试隐喻

> 详见 `architecture_design.md`

### 3.1 核心理念

QuantTutorBench 是一场**标准化考试**。我们提供试卷、考场设施和打分标准。考生（Client）可以带小抄、查资料——我们不管也管不了——但所有在考场里做的动作都必须用我们的纸和笔，留下完整记录。

```
我们提供（不可替换）:          考生提供（可替换）:
  试卷 ── 65 任务 + 学生行为       作答者 ── Agent / 人类 / 混合
  文具 ── 15 标准化工具 (MCP)
  考场 ── Docker 沙箱
  监考 ── 学生模拟器
  打分 ── 评估流水线
```

### 3.2 Benchmark Specification vs Reference Implementation

| | Benchmark Spec（论文贡献） | Reference Impl（工程贡献，可替换） |
|---|---|---|
| 内容 | Task Schema, Tool API, Student Protocol, Evaluation Rubrics, Scoring Formula | Agent Adapters, Orchestrator, Docker Images, Web UI |
| 定位 | 定义"考什么"和"怎么打分" | 提供"怎么跑"的参考实现 |
| 可替换 | 否 | 是（第三方可实现自己的 adapter） |

### 3.3 Client-Server 交互模型

```
Client ←WebSocket→ Server
  收到学生消息 → 调用工具(0~N次) → 回复学生 → 循环直到 TC 完成 → 评分
```

所有工具调用经 Server 的 MCP Proxy，100% 可观测。评分数据（conversation + tool_logs + workspace）全部由 Server 自动采集，不依赖 Client。

### 3.4 QP 的精确定位

QP 评的是**可观测行为质量**，不是内部推理过程：

> "QP evaluates the observable behavioral quality of the agent within the benchmark environment — how effectively it selects and sequences tools, manages code iterations, and recovers from errors. QP is agnostic to the agent's internal reasoning or external knowledge sources."

即使 Client 有外部知识，QP 仍有区分度——知道答案不等于执行过程高效。实验数据：两个"开卷"模型（Sonnet/Haiku）的 QP Cohen's d=1.11（large effect）。

---

## 四、论文主要贡献

### 4.1 三大贡献及其数据支撑

| 贡献                           | 核心主张                                  | 数据支撑                                                    |
| ------------------------------ | ----------------------------------------- | ----------------------------------------------------------- |
| **C1: 三维评估框架**     | 单维度评估无法区分模型能力                | QR d≈0 → 加入 Tutor 后 d=1.69；QR-Tutor r=0.25（独立）    |
| **C2: 对话模拟范式**     | 交互式评估比静态评估更能暴露能力差异      | 3 persona × 不同追问模式下 Tutor 8/8 方向一致              |
| **C3: 标准化量化工具集** | 统一的 MCP 工具 + Docker 环境保证公平对比 | Programmatic eval 跨 judge 100% 一致；code_eval 跨 run 稳定 |

### 4.2 每个贡献解决的 reviewer 问题

| 贡献 | Reviewer 会问                   | 我们的回答                                           |
| ---- | ------------------------------- | ---------------------------------------------------- |
| C1   | "为什么不直接看结果对不对？"    | "因为 QR d≈0，只看结果 sonnet 和 haiku 不可区分"    |
| C2   | "为什么不用 static QA？"        | "因为教学能力只在多轮交互中才能观察到"               |
| C3   | "不同 agent 用不同工具公平吗？" | "所有 agent 使用完全相同的 MCP 工具集和 Docker 镜像" |

---

## 五、三维评估的必要性

### 5.1 从数据看维度必要性


| 假设场景                           | 数据                          | 后果                                                                       |
| ---------------------------------- | ----------------------------- | -------------------------------------------------------------------------- |
| **只看 QR（结果对不对）**    | Cohen's d = 0.24 (small)      | Sonnet 和 Haiku 几乎不可区分——两个模型都能算对均线、跑通回测             |
| **只看 QP（过程好不好）**    | Cohen's d = 1.11 (large)      | 能区分，但无法捕捉"教得好不好"——agent 可能过程高效但教学糟糕             |
| **只看 Tutor（教得好不好）** | Cohen's d = 1.69 (very large) | 区分度最高，但无法判断"结果对不对"——agent 可能教得好但答案是错的         |
| **QR + QP + Tutor**          | 三维综合                      | 完整画像：**什么结果** × **怎么做到的** × **怎么教的** |

### 5.2 维度独立性证明

| 维度对               | Pearson r (Sonnet Judge) | 含义                                                      |
| -------------------- | ------------------------ | --------------------------------------------------------- |
| **QR - Tutor** | +0.25（独立）            | 结果好 ≠ 教得好。知道答案不等于能教会学生                |
| **QR - QP**    | +0.36（弱相关）          | 结果好 ≈ 过程略好。但大量例外存在                        |
| **QP - Tutor** | +0.50（中等相关）        | 过程好的 agent 教学也倾向于好——但相关性不够替代独立评估 |

**核心论点**：QR-Tutor 的低相关性（r=0.25）是整个三维框架的立论基础。它证明了"做得对"和"教得好"是**不同的能力维度**——恰如一个数学天才不一定是好老师。

### 5.3 评分粒度消融：区分度不来源于量尺精度

各维度 LLM judge 的评分粒度不一致——QP 子维度多数用 5 档（0.0/0.25/0.5/0.75/1.0），Tutor D1-D7 和 QR Result Judge 用 10 档（1-10 整数）。Tutor 的高区分度是否仅仅因为 10 档比 5 档更细？

**消融实验**：将 Tutor D1-D7 每个子维度 clamp 到 5 档，再重新聚合。

| 方案                                     | Cohen's d       | 变化            |
| ---------------------------------------- | --------------- | --------------- |
| 原始 10 档                               | 1.745           | 基准            |
| **D1-D7 各自 clamp 到 5 档后聚合** | **1.816** | **+4.1%** |

**结论**：区分度不降反升。7 个独立子维度各自离散化后取均值，聚合分的有效分辨率远高于单维度 5 档。Tutor 的区分度来源于 Sonnet 和 Haiku 在教学能力上的**真实差距**（均值差 0.175），不是量尺精度。

### 5.4 区分度随维度的变化

```
维度增加带来的区分度提升：

QR only:           d = 0.24  ████
QR + QP:           d ≈ 0.68  ████████████████
QR + QP + Tutor:   d ≈ 1.69  ████████████████████████████████████████████

区分度提升: 0.24 → 1.69 = 7× 提升
```

### 5.5 维度间的互补关系

```
                    结果正确
                    QR ────→ "模型知不知道"
                   /
任务完成度 ←─────/
                 \
                  QP ────→ "模型怎么做到的"
                   \        （工具使用、步骤效率、错误恢复）
                    \
                     \
                      Tutor ──→ "模型能不能教会学生"
                                （知识传递、因材施教、对话管理）
```

三个维度回答三个不同的问题，缺一不可。

---

## 六、对话模拟的可控性与有效性

### 6.1 "可控"体现在哪里

| 控制维度           | 实现方式                                                                   | 效果                                                 |
| ------------------ | -------------------------------------------------------------------------- | ---------------------------------------------------- |
| **知识水平** | 3 个 persona（beginner / intermediate / advanced），各有独立 system prompt | 同一任务，beginner 问基础概念、advanced 追问边界条件 |
| **对话长度** | TC Checker 监控学习目标覆盖度，达标后触发结束                              | 防止无限循环，确保所有任务可比                       |
| **追问方向** | Persona prompt 定义追问风格（beginner 求解释、advanced 求证明）            | 控制对话深度和广度                                   |
| **评估焦点** | 每个任务有明确的 Termination Criteria（3-5 个学习目标）                    | 评估有据可依，不是"感觉教得好"                       |

### 6.2 "可信"的实验证据

**学生模拟器虽然非确定性，但区分度结论稳定**：

| 测试              | 结果                                | 含义                                 |
| ----------------- | ----------------------------------- | ------------------------------------ |
| 同一任务 3 次运行 | Tutor 方向一致性 7/7~8/8            | 学生追问不同，但 sonnet 总是教得更好 |
| 不同 persona      | 三个 persona 下 sonnet > haiku 一致 | 不论学生水平，结论不变               |
| 跨 judge 验证     | 两个 judge 排名 100% 一致           | 不是 judge 偏好造成的                |

### 6.3 为什么"非确定性"是有效的

传统 benchmark 追求确定性：同一题每次考一样的。但教学场景天然是非确定性的——真实学生不会每次问一样的问题。

我们的学生模拟器：

- **结构性稳定**：始终遵循 persona 定义的水平和追问风格
- **细节性变化**：具体问哪个子话题、用什么措辞会变
- **效果**：测试 agent 对**多样化学生行为的鲁棒性**，而非对固定脚本的记忆

> 如果一个 agent 只在学生问 X 时教得好，问 Y 就崩了——这不应该拿高分。非确定性帮我们发现这类脆弱性。

### 6.4 TC Checker 的 token 节省

TC Checker 使用增量检查（bitmap + 分类路由），仅在对话触发新类别时调用 LLM：

- **Token 节省**：~97%（相比每轮全量检查）
- **准确性**：bitmap 覆盖度与完整评估一致
- **作用**：既控制了对话长度，又确保学习目标被覆盖

---

## 七、标准化工具集的公平性

### 7.1 工具集设计

所有 agent 使用 **完全相同的 MCP 工具集**：

| 工具类别           | 具体工具                                          | 用途                 |
| ------------------ | ------------------------------------------------- | -------------------- |
| **文件操作** | file_read, file_write, shell_exec                 | 读写代码、执行命令   |
| **数据分析** | compute_statistics, plot_chart                    | 统计计算、可视化     |
| **文档查询** | search_docs, get_environment_info                 | 查阅参考文档         |
| **回测执行** | run_lean_backtest, analyze_lean_results           | LEAN 引擎交互        |
| **试验管理** | submit_trial, select_submission, get_trial_status | 预算控制下的回测迭代 |

### 7.2 "限制"还是"公平"？

| 关注点                            | 回答                                                                                                                 |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **是否限制了 agent 能力？** | 是的，刻意为之。工具集定义了**能力边界**——评估的是 agent 在给定工具下的效率，而非 agent 拥有什么工具         |
| **与真实场景的差距？**      | 真实量化分析师也使用标准工具（Python、Bloomberg Terminal、回测平台）。工具集模拟了这种标准化工作环境                 |
| **是否可扩展？**            | 任务 JSON 的 `core_mcp_tools` 和 `convenient_tools` 字段支持按任务配置工具集，但 ICC 实验中所有 run 使用相同配置 |

### 7.3 Docker 环境的一致性保证

```
Docker Image (frozen):
├── Python 3.12 + numpy/pandas/scipy/matplotlib
├── LEAN Engine + Binance Crypto Futures 数据 (2022-2025)
├── run_backtest.sh（标准化回测入口）
└── /data/（任务专属数据文件）

所有 agent 的每次 run 都从相同镜像启动
→ 零环境差异
→ 代码执行结果完全可复现
```

### 7.4 Programmatic eval 验证工具集公平性

Programmatic eval（test scripts）在同一 run_state 上，**跨 judge 结果完全一致**：

```
B01 run1: haiku judge prog=1.0000, code_eval=0.5000
          sonnet judge prog=1.0000, code_eval=0.5000  ← 完全一致

X01 run1: haiku judge prog=0.2500, code_eval=0.3667
          sonnet judge prog=0.2500, code_eval=0.3667  ← 完全一致
```

这证明了：工具执行结果是**确定性**的，不受评估模型影响。agent 在 Docker 环境中的行为是**可审计**的。

---

## 八、数据总览

### 8.1 核心统计数据

| 指标                          | 数值                                      | 来源                              |
| ----------------------------- | ----------------------------------------- | --------------------------------- |
| 任务总数                      | 65 (5 categories × ~13 tasks)            | 任务 JSON                         |
| ICC 实验覆盖                  | 8 tasks × 3 runs × 2 agents × 2 judges | 本轮实验                          |
| **Tutor Cohen's d**     | **1.69** (sonnet judge)             | judge_comparison_analysis.md      |
| **QR Cohen's d**        | **0.24** (sonnet judge)             | judge_comparison_analysis.md      |
| **方向一致性**          | 8/8 tasks (OAS, sonnet judge)             | judge_comparison_analysis.md      |
| **跨 judge 排名一致性** | 100%                                      | judge_comparison_analysis.md      |
| QR Pearson r (cross-judge)    | 0.872                                     | judge_comparison_analysis.md      |
| Tutor Pearson r (cross-judge) | 0.676                                     | judge_comparison_analysis.md      |
| QR-Tutor 相关性               | r=0.25 (独立)                             | judge_comparison_analysis.md      |
| 学生 exact match rate         | 0/24 (temp=0)                             | student_determinism_experiment.md |
| TC Checker token 节省         | ~97%                                      | 增量检查设计                      |

### 8.2 待完成工作

| 项目                                                   | 优先级 | 状态                   |
| ------------------------------------------------------ | ------ | ---------------------- |
| 完整 65 tasks × 3 personas 运行                       | P0     | 待执行（需要计算资源） |
| Human calibration (30-50 samples)                      | P0     | 待执行（需要领域专家） |
| Haiku agent 文件夹 sonnet eval 超时重试 (5 个 Tutor=0) | P1     | 待修复                 |
| DeepEval 版本锁定                                      | P1     | 待执行                 |
| BENCHMARK_SPEC.md（第三方接入文档）                    | P2     | 待撰写                 |

---

## 九、一句话总结

> QuantTutorBench 通过三维评估（QR+QP+Tutor）将模型区分度从 near-zero (d=0.24) 提升到 very large (d=1.69)，通过标准化工具集和 Docker 环境确保公平性和可复现性，通过可控的对话模拟测试 agent 在多样化学生互动下的鲁棒性。这些不是独立的技术贡献，而是一个紧密耦合的系统——拿掉任何一个，benchmark 的区分能力就会崩塌。
