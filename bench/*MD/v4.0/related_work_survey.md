# Benchmark 论文调研报告：GAIA / SWE-bench & SWE-agent / AgentBench

> 目标：从实验架构设计角度调研三篇标杆论文，提炼对 QuantTutorBench 的可借鉴点和风险预警

---

## 一、三篇论文核心架构速览

### 1. GAIA — "General AI Assistants" (Meta & HuggingFace, 2023)

| 维度 | 设计 |
|------|------|
| **定位** | 通用 AI 助手的端到端能力测试 |
| **任务格式** | 466 道 factoid 问答，答案唯一、简短（数字/短字符串） |
| **评估方式** | Quasi exact match，零 LLM Judge，零 partial credit |
| **Harness 标准化** | **完全不标准化**——不规定工具集、不规定 agent 架构，只看最终答案 |
| **工具/Action Space** | 不固定。不同被测系统（GPT-4 vanilla / +plugins / AutoGPT）自带工具 |
| **可复现性** | API 模型跑 3 次取均值；承认 closed-source 模型不可完全复现 |
| **难度分级** | 3 级，按人工标注的步骤数和工具数划分 |
| **评分** | 二值（对/错），按 level 聚合百分比 |

**核心哲学**：
- "反向难度"——人类 92% 正确率，GPT-4 + plugins 仅 15%
- 答案可精确验证 → 不需要 judge → 完全自动化
- Benchmark 与 harness 彻底解耦——**只评结果，不评过程**

---

### 2. SWE-bench + SWE-agent（Princeton NLP, ICLR 2024 + NeurIPS 2024）

这是**两篇独立但互补的论文**，清晰地分离了 benchmark 和 harness：

#### SWE-bench（Benchmark 层）

| 维度 | 设计 |
|------|------|
| **定位** | 真实 GitHub Issue 修复能力 |
| **任务格式** | 2,294 个 task instance，每个 = issue 文本 + 代码库快照 |
| **评估方式** | Fail-to-Pass 测试：应用 patch 后原本失败的测试必须通过 |
| **Harness 标准化** | **不标准化 agent 架构**，只要求输出 unified diff patch |
| **环境** | Docker 容器，每个 task 一个隔离环境，99.78% 一致性 |
| **评分** | Resolution Rate（% resolved），二值 |

#### SWE-agent（Harness 层）

| 维度 | 设计 |
|------|------|
| **核心概念** | Agent-Computer Interface (ACI)——为 LM 设计的专用交互界面 |
| **Agent Loop** | thought-action-observation 循环，窗口化上下文（保留最近 ~5 轮） |
| **Action Space** | **刻意约束**：7 个导航命令 + 1 个编辑命令（带 linter 校验）+ bash + submit |
| **关键设计** | 编辑操作自带 linter guardrail——语法错误直接拒绝，文件不变 |
| **Ablation 结果** | ACI vs. 纯 shell：18% vs 7.3%（+10.7pp），证明 harness 设计的巨大影响 |

**核心哲学**：
- Benchmark 只看 patch，不关心怎么生成的
- Harness 设计对结果影响巨大（同一模型差 2.5 倍），但这是**独立研究贡献**
- **ACI 设计原则**：compact actions, concise feedback, guardrails against common failures

---

### 3. AgentBench（Tsinghua, ICLR 2024）

| 维度 | 设计 |
|------|------|
| **定位** | 跨环境的 LLM-as-Agent 综合评测 |
| **任务格式** | 8 种异构环境（OS, DB, KG, Card Game, Web Shopping 等） |
| **评估方式** | 每个环境独立指标（SR / F1 / Win Rate 等） |
| **Harness 标准化** | 统一 HTTP 协议（Server-Client 架构），但 action space 按环境各异 |
| **工具/Action Space** | 每个环境自定义（bash / SQL / SPARQL / game moves / click actions） |
| **可复现性** | temperature=0, Docker 容器化, 预构建镜像, YAML 配置驱动 |
| **Prompt 策略** | ReAct (CoT + Action)，刻意只用基础 CoT，不用 ensemble/reflection |
| **评分** | Overall Score = 加权平均，权重 = 各任务平均分的倒数（难任务权重更高） |

**核心架构**：
```
┌─────────────┐    HTTP    ┌──────────────┐    HTTP    ┌──────────┐
│ Agent Server │ ◄────────► │    Client    │ ◄────────► │Task Server│
│  (LLM API)  │            │  (Assigner)  │            │ (Docker)  │
└─────────────┘            └──────────────┘            └──────────┘
                           max-flow scheduling          Task Workers
```

**核心哲学**：
- 多环境 = 测试泛化能力，单一环境无法全面评估 agent
- 故障分类（Complete / CLE / IF / IA / TLE）本身就是 finding
- 权重设计避免简单任务主导总分
- 只用基础 ReAct，刻意不优化 agent，以测模型原始能力

---

## 二、三篇论文的关键设计决策对比

| 设计决策 | GAIA | SWE-bench/agent | AgentBench | **QuantTutorBench** |
|----------|------|-----------------|------------|---------------------|
| **Benchmark vs Harness 分离** | 完全分离（只看答案） | 完全分离（bench 看 patch，agent 是独立论文） | 分离（HTTP 协议解耦） | **部分耦合**（adapter 层 + 工具集 = harness 嵌入 benchmark） |
| **评估自动化** | Exact match，零 LLM | 测试执行，零 LLM | 环境内置判定，零 LLM | **混合**（programmatic + LLM Judge） |
| **Action Space** | 不固定 | SWE-bench 不固定；SWE-agent 固定 ~10 个命令 | 按环境各异 | 固定 15 slots |
| **Agent 架构约束** | 无 | 无（benchmark）/ 有（agent） | 基础 ReAct only | 有（adapter + SDK 约束） |
| **对话 vs 单次** | 单次（含多步推理） | 多步（agent）/ 单次（bench） | 多轮交互 | 多轮对话 |
| **Partial Credit** | 无 | 无 | 部分有（F1, Reward） | **有**（多维度连续分） |
| **人工校准** | 3 annotators/question | fail-to-pass 自验证 | 环境逻辑自验证 | **缺失**（LLM Judge 未校准） |

---

## 三、对 QuantTutorBench 的关键启示

### 启示 1：Benchmark 与 Harness 的边界必须在论文中显式声明

**问题**：三篇论文都极其注意 benchmark 与 harness 的分离。GAIA 和 SWE-bench 走到了极端——完全不规定 agent 架构。而我们的 adapter 层、SDK 集成、prompt 管理都嵌入了 benchmark 代码中。

**行动**：
- 在论文中明确划分 **Benchmark Specification**（任务定义 + 工具 API 规范 + 评估标准）和 **Reference Implementation**（adapter + orchestrator + simulation）
- 类比 SWE-bench 的做法：benchmark spec 是 ICLR 论文，reference implementation 是 NeurIPS 论文
- 声明：adapter 层是 reference implementation，第三方可以用自己的 harness 只要符合工具 API 规范

**论文表述建议**：
> "We provide reference agent adapters for major providers (Anthropic, OpenAI, Google) as a convenience; however, the benchmark specification is adapter-agnostic. Any system that can invoke the standardized MCP tool API and produce conversational responses can be evaluated."

---

### 启示 2：评估自动化程度是审稿人的核心关注点

**观察**：三篇论文**都不用 LLM Judge**：
- GAIA → exact match
- SWE-bench → fail-to-pass 测试
- AgentBench → 环境内置判定逻辑

我们使用 LLM Judge 是因为对话质量和教学维度无法用 exact match 评估，这是合理的，但必须提供充分的可信度证据。

**行动**：
- **必须做 human calibration**（30-50 样本），报告 Cohen's κ 或 Pearson r
- 参考 AgentBench 的做法：在论文中明确列出评估的 failure taxonomy（Complete / CLE / IF / IA / TLE），我们可以定义类似的 completion status
- 强调 programmatic eval（test_scripts）覆盖 QR 的 65 个 task，这部分不依赖 LLM

---

### 启示 3：固定 Action Space 是科学合理的，但需要正确定位

**GAIA 的观点**：不固定工具，测试"真实世界能力"
**SWE-agent 的观点**：固定工具，测试"标准化条件下的能力"
**AgentBench 的观点**：每个环境固定工具，但跨环境不统一

**我们的 15-slot 设计最接近 SWE-agent + AgentBench 的混合体**：
- 固定 action space 是受控实验的前提条件
- 但我们应该在论文中声明：我们测的不是 "agent 在野外的通用工具使用能力"，而是 "给定标准化量化工具集时的领域知识 × 工具编排 × 教学能力"
- Distractor tools 机制类似于 AgentBench 的 Invalid Action 检测——测试工具选择的判断力

**论文表述建议**：
> "Unlike open-ended benchmarks (GAIA) that leave tool selection to the agent, QuantTutorBench provides a standardized 15-slot tool palette per task, comprising core tools (necessary), convenient tools (bonus-eligible), and functional distractors. This design isolates domain reasoning from tool discovery, analogous to how SWE-agent's ACI isolates coding ability from shell proficiency."

---

### 启示 4：AgentBench 的权重设计值得借鉴

**问题**：AgentBench 发现不同 task 的原始分数差异巨大，naive 平均会被高分 task 主导。他们用"平均分倒数"做权重。

**对我们的启示**：
- 目前 scoring.py 使用固定权重（QR 0.5 + QP 0.5，Quant 0.7 + Tutor 0.3）
- 是否需要跨 task 的动态权重？如果某些 task 所有模型都拿满分，该 task 对 benchmark 的区分度贡献为 0
- 建议：跑完初始模型集后，计算 discrimination index，在论文中报告

---

### 启示 5：可复现性的工程标准

三篇论文的共同做法：

| 措施 | GAIA | SWE-bench | AgentBench | **我们** |
|------|------|-----------|------------|---------|
| Docker 隔离 | ✗ | ✓ (99.78%) | ✓ (预构建镜像) | ✓ |
| 固定解码温度 | ✗ (API 默认) | - | ✓ (temp=0) | ⚠️ 未统一 |
| 配置驱动 | ✗ | ✓ | ✓ (YAML) | ✓ (JSON + YAML) |
| 多次运行取均值 | ✓ (3 次) | ✗ | ✗ | ⚠️ 未规定 |
| 数据集版本控制 | ✓ (HuggingFace) | ✓ (git commit) | ✓ (GitHub release) | ✓ (HuggingFace) |
| 模型版本记录 | 部分 | 部分 | ✓ (精确 API 版本) | ⚠️ 需加强 |

**行动**：
- 固定 temperature=0（或论文中声明具体值）
- 记录精确模型版本（如 claude-sonnet-4-20250514，不是 "sonnet 4"）
- 定义 n_runs（建议 3 次），报告均值 ± 标准差
- **学生模拟器的可复现性**是独有挑战（GAIA/SWE-bench/AgentBench 都没有模拟用户的问题）

---

### 启示 6：AgentBench 的故障分类体系

AgentBench 将每次 task attempt 分为 5 类：Complete / Context Limit Exceeded / Invalid Format / Invalid Action / Task Limit Exceeded。这本身就产生了有价值的 finding（主要失败模式是 TLE，不是格式错误）。

**对我们的启示**：
- 定义类似的 completion taxonomy：
  - **Complete** — 正常结束，TC 全部覆盖
  - **Partial** — TC 部分覆盖，达到 max_turns
  - **Timeout** — 超时终止
  - **Error** — 运行时错误（API 限流、容器崩溃等）
  - **Repeat Loop** — 重复检测触发终止
- 在评分中区分对待，而非统一当作"失败"
- 在论文中报告各类 completion status 的分布——这是一个 finding

---

### 启示 7：我们的独有优势——多维度评估

**三篇论文都用一维评分**（正确率 / 解决率 / 成功率），这是它们的局限。

我们的 QR + QP + Tutor 7D 是真正的差异化：
- QR = "答案对不对"（类似 GAIA / SWE-bench）
- QP = "过程好不好"（无先例）
- Tutor 7D = "教得好不好"（无先例）

**这是论文最大的卖点**，但需要用数据证明多维度的必要性：
- 展示 QR 和 QP 排名不一致的案例
- 展示 Tutor 7D 各维度的独立区分度
- 计算维度间相关性矩阵

---

### 启示 8：SWE-agent 的 ACI 设计原则可直接应用

SWE-agent 通过 ablation 证明了 harness 设计对性能的巨大影响（+10.7pp）。其 ACI 设计原则与我们的工具设计直接相关：

| ACI 原则 | SWE-agent 实现 | 我们的对应 | 改进空间 |
|----------|---------------|-----------|---------|
| Compact actions | 7 个导航命令 | 15 slots 含 distractor | ✓ 已有 |
| Concise feedback | 搜索结果限 50 条 | 工具返回截断？ | ⚠️ 需确认 |
| Linter guardrail | 编辑自动语法检查 | 无 | 可加在 shell_exec 上 |
| Windowed context | 保留最近 ~5 轮 | Anthropic adapter 40K 压缩 | ⚠️ 策略较粗 |

---

## 四、针对之前讨论的问题的论文视角回答

### Q：我们测的到底是什么？（对应核心问题 Q2）

参考三篇论文的定位方式：

| 论文 | 声称测什么 | 实际控制了什么 | 没控制什么 |
|------|-----------|--------------|-----------|
| GAIA | 通用助手能力 | 任务定义、答案格式 | 工具、架构、prompt |
| SWE-bench | 代码修复能力 | 任务、环境、评估 | agent 架构 |
| SWE-agent | ACI 对性能的影响 | ACI 设计 | 模型选择 |
| AgentBench | LLM 作为 agent 的能力 | 环境、prompt 格式 | 模型内部架构 |

**我们应该声称测的是**：
> "Given a standardized quantitative finance toolset and tutoring scenario, we evaluate an LLM agent's ability to (1) produce correct quantitative analysis results (QR), (2) follow sound analytical processes (QP), and (3) effectively teach domain concepts to students of varying proficiency (Tutor). We control the tool API, task specification, and student simulation; we do not control the agent's internal architecture, prompt engineering, or tool-calling strategy."

这与 AgentBench 的定位最接近——提供标准化环境，测试 agent 在该环境下的综合表现。

### Q：Adapter 是越界吗？（对应反馈 2）

**不是**。参考：
- AgentBench 提供了完整的 Agent Server 接口定义和 FastChat 封装
- SWE-bench 提供了评估 harness
- GAIA 提供了 evaluation script

提供标准化接口是 benchmark 的职责。关键是在论文中区分 "benchmark specification" 和 "reference implementation"。

### Q：LLM Judge 能用吗？（对应反馈 4）

三篇都没用，但它们都能用 exact match / 测试执行来判定。我们的任务性质（对话质量、教学维度）决定了 LLM Judge 是必要的。

**关键是提供可信度证据**：
- Human agreement rate
- Cross-model judge consistency（用不同 LLM 做 judge，结果一致性如何）
- Calibration sample size 的合理性论证

---

## 五、论文写作的结构性建议

基于三篇论文的结构，建议 QuantTutorBench 论文采用以下框架：

```
1. Introduction
   - Motivation：为什么需要量化金融领域的 agent benchmark
   - Gap：现有 benchmark（GAIA/SWE-bench/AgentBench）不覆盖领域教学场景
   - Contribution：三维评估 + 对话模拟 + 标准化量化工具集

2. Related Work
   - General agent benchmarks (GAIA, AgentBench)
   - Code/SE benchmarks (SWE-bench, HumanEval)
   - Domain-specific benchmarks (FinBench, FLUE)
   - Tutoring/education benchmarks (if any)

3. Benchmark Design
   3.1 Task Taxonomy (6 categories × difficulty levels)
   3.2 Tool API Specification (15-slot design, core/convenient/distractor)
   3.3 Student Simulation Protocol (anchored simulation, TC design)
   3.4 Evaluation Framework
       - QR: Programmatic + Result Judge
       - QP: 7D Process Metrics
       - Tutor: 7D Teaching Rubric
   3.5 Scoring Aggregation (OAS, QAI, TEI formulas)

4. Implementation
   4.1 Orchestration Pipeline (5-phase lifecycle)
   4.2 Reference Agent Adapters
   4.3 Sandbox Environment (Docker + LEAN)
   4.4 Context Management

5. Experiments
   5.1 Models Evaluated (with exact versions)
   5.2 Main Results (cross-model comparison)
   5.3 Multi-Dimensional Analysis (QR vs QP vs Tutor ranking divergence)
   5.4 Ablation Studies
       - Tool palette: core-only vs full 15-slot
       - Evaluation: QR-only vs QR+QP vs QR+QP+Tutor
       - Distractor impact
   5.5 Evaluation Reliability
       - Human calibration results
       - Student simulator reproducibility (ICC, Kendall's W)
       - Cross-judge consistency

6. Discussion
   - What does the benchmark measure? (explicit scope statement)
   - Limitations and threats to validity
   - Comparison with existing benchmarks

7. Conclusion
```

---

## 六、优先行动清单（按论文投稿紧迫度排序）

| 优先级 | 行动 | 对应启示 | 工作量 |
|--------|------|---------|--------|
| **P0** | Human calibration 30-50 样本 | 启示 2 | ~15h 专家时间 |
| **P0** | 论文中显式声明 benchmark scope | 启示 1, 3 | 写作 |
| **P0** | 学生模拟器可复现性量化（跑 3 次，报告 ICC） | 启示 5 | ~2 天计算 |
| **P1** | 定义 completion taxonomy 并记录分布 | 启示 6 | ~1 天开发 |
| **P1** | 多维度区分度分析（相关性矩阵 + 排名变化） | 启示 7 | ~1 天分析 |
| **P1** | 固定 temperature + 记录精确模型版本 | 启示 5 | ~2h 配置 |
| **P2** | 定义 n_runs=3，报告均值 ± 标准差 | 启示 5 | 计算资源 |
| **P2** | 工具返回内容截断策略 | 启示 8 | ~0.5 天 |
| **P2** | Cross-model judge consistency 实验 | 启示 2 | ~1 天 |

---

## 附录：三篇论文关键数据

### GAIA
- 466 questions, 3 levels
- Human: 92% → GPT-4+plugins: 15%
- 发表：arXiv 2311.12983, HuggingFace leaderboard

### SWE-bench
- 2,294 task instances, 12 Python repos
- Best at publication: Claude 2 = 1.96% (BM25), 4.80% (Oracle)
- 发表：ICLR 2024, arXiv 2310.06770

### SWE-agent
- ACI: 7 navigation + 1 edit (with linter) + bash + submit
- GPT-4 Turbo: 7.3% (shell only) → 18% (with ACI)
- 发表：NeurIPS 2024, arXiv 2405.15793

### AgentBench
- 8 environments, 29 LLMs tested
- API models: OA avg 2.32 vs OSS avg 0.51
- 发表：ICLR 2024, arXiv 2308.03688

---

# 2026 年 Agent Benchmark / Agent Evaluation 论文调研

**调研日期**: 2026-04-07
**筛选条件**: 仅 2026 年发布（arXiv 26xx.xxxxx），聚焦对 agent 的评估（非 agent 自动化评估）

---

## 一、Agent 评估通用框架

### 1. Benchmark Test-Time Scaling of General LLM Agents
- **arXiv**: [2602.18998](https://arxiv.org/abs/2602.18998) | 2026.02
- **内容**: 系统研究 LLM agent 在推理时（test-time）的扩展行为。在搜索、编码、推理和工具使用四个领域，统一框架下评估 sequential scaling（多次尝试）和 parallel scaling（并行探索）对 agent 性能的影响。
- **启示**: 你的 benchmark 有 3-trial 机制和 pass@k 指标，可以与这篇的 scaling 行为分析对比。

### 2. ProAgentBench: Evaluating LLM Agents for Proactive Assistance
- **arXiv**: [2602.04482](https://arxiv.org/abs/2602.04482) | 2026.02
- **内容**: 评估 agent 的**主动性**——不等用户指令就预判需求。用 28,000+ 真实用户 session 事件（500+ 小时）构建，隐私合规。测试 agent 是否能在正确时机主动发起行动。
- **启示**: 你的 tutor agent 也涉及主动性（D3 scaffolding calibration），tutor 应该主动引导而非被动回答。

### 3. MAESTRO: Multi-Agent Evaluation Suite for Testing, Reliability, and Observability
- **arXiv**: [2601.00481](https://arxiv.org/abs/2601.00481) | 2026.01
- **内容**: 针对多 agent 系统的评估套件。标准化 MAS 配置和执行，输出框架无关的执行 trace + 系统级信号，用于可靠性和可观测性评估。
- **启示**: 你的 trace_report / live_monitor 与其 observability 理念一致。

### 4. Agent Psychometrics: Task-Level Performance Prediction in Agentic Benchmarks
- **arXiv**: [2604.00594](https://arxiv.org/abs/2604.00594) | 2026.04
- **内容**: 将心理测量学方法（Item Response Theory）应用于 agent benchmark，从单纯"跑分"转向理解每个任务的难度和区分度。在 SWE-bench Verified 和 Terminal-Bench 上验证。
- **启示**: 你可以用 IRT 分析 QuantTutorBench 的 102 个任务的难度/区分度，为 benchmark 质量提供统计支撑。

---

## 二、工具使用 & MCP 评估

### 5. MCPAgentBench: Evaluating LLM Agent MCP Tool Use
- **arXiv**: [2512.24565](https://arxiv.org/abs/2512.24565) | 2025.12（但持续活跃到 2026）
- **内容**: 基于 MCP 协议的真实任务数据集，动态沙箱中测试工具选择和鉴别（含 distractor tools）。
- **启示**: 你的 MCP proxy + distractor tools 设计与这篇高度相似，是最直接的工具评估对标。

### 6. FinToolBench: Evaluating LLM Agents for Real-World Financial Tool Use
- **arXiv**: [2603.08262](https://arxiv.org/abs/2603.08262) | 2026.03
- **内容**: 专门评估 LLM agent 在真实金融场景中的工具使用能力。覆盖金融数据查询、计算、分析等多步工具链。
- **启示**: **直接竞品**。你需要在 Related Work 中区分：FinToolBench 测"工具使用能力"，你测"使用工具进行教学的能力"。

---

## 三、金融领域 Agent 评估

### 7. TraderBench: How Robust Are AI Agents in Adversarial Capital Markets?
- **arXiv**: [2603.00285](https://arxiv.org/abs/2603.00285) | 2026.02
- **内容**: 双 agent 架构（分析师+交易员）+ 六个 MCP 服务器，在对抗性市场环境中评估知识检索、分析推理、期权交易、加密交易。用 Sharpe ratio、收益率、回撤等实际交易指标打分。
- **启示**: 它用了 MCP + 对抗性测试，与你的 A-series（adversarial tasks）理念相似，但它测交易执行，你测教学。

### 8. PredictionMarketBench: SWE-bench-Style Framework for Trading Agents
- **arXiv**: [2602.00133](https://arxiv.org/abs/2602.00133) | 2026.02
- **内容**: 用 SWE-bench 的方法论（从真实数据自动生成 test case）构建预测市场交易 agent 的回测框架。首批包含 Kalshi 平台上的加密货币、天气、体育等事件。
- **启示**: 方法论参考——从真实数据自动生成评估任务的思路。

### 9. Evaluating Financial Intelligence: Benchmarking SuperInvesting AI
- **arXiv**: [2603.08704](https://arxiv.org/abs/2603.08704) | 2026.03
- **内容**: 评估 LLM 引擎驱动的金融 AI 的投资智能，聚焦于超越市场基准的能力。
- **启示**: 金融领域 agent 评估的另一角度——投资决策质量。

---

## 四、教育 & 教学 Agent 评估

### 10. TutorBench: A Benchmark To Assess Tutoring Capabilities of LLMs
- **arXiv**: [2510.02663](https://arxiv.org/abs/2510.02663) | 2025.10（但 2026 年持续被引用）
- **内容**: 1,490 个对话，覆盖 6 个 STEM 学科（生物、物理、化学、统计、微积分、计算机科学），评估三种教学用途：自适应解释生成、反馈与评估、主动学习支持。
- **启示**: **最直接的竞品**。你需要清晰区分：TutorBench 覆盖多学科但以文本对话为主，QuantTutorBench 聚焦量化金融且包含代码执行和工具使用。

### 11. ISD-Agent-Bench: Evaluating Instructional Design Agents
- **arXiv**: [2602.10620](https://arxiv.org/abs/2602.10620) | 2026.02
- **内容**: 25,795 个场景，基于 ADDIE 模型的 33 个教学设计子步骤 × 51 个上下文变量。用 multi-judge protocol（多 LLM 评审）评估教学设计 agent，在 1,017 个测试场景上验证。
- **启示**: **高度相关**。它评估的是"教学设计"agent（课程设计能力），你评估的是"教学执行"agent（实时教学能力）。两者互补。multi-judge 的方法论与你的多模型多轮 judge 一致。

### 12. Unifying AI Tutor Evaluation: An Evaluation Taxonomy for Pedagogical Ability
- **arXiv**: [2412.09416](https://arxiv.org/abs/2412.09416) | 2024.12（v2 更新至 2026）
- **内容**: 提出统一评估分类法，8 个教学维度，发布 MRBench（192 个对话、1,596 个回复，来自 7 个 SOTA LLM 和人类 tutor）。
- **启示**: 你的 7D tutor 评估维度可以与这篇的 8 维度对比。你有更强的实际操作评估（代码执行、工具使用），它更聚焦纯教学话语分析。

### 13. AgentTutor: Multi-Turn Interactive Teaching in Intelligent Education
- **arXiv**: [2601.04219](https://arxiv.org/abs/2601.04219) | 2026.01
- **内容**: 多 agent 驱动的个性化教学系统，集成课程分解、学习者评估、动态策略生成、教学反思、知识记忆五个模块。
- **启示**: 它是教学 agent 的**系统设计**，你是教学 agent 的**评估框架**。可以说"像 AgentTutor 这样的系统需要 QuantTutorBench 这样的评估工具来衡量质量"。

### 14. The Path to Conversational AI Tutors
- **arXiv**: [2602.19303](https://arxiv.org/abs/2602.19303) | 2026.02
- **内容**: 综述如何将教学最佳实践融入 AI 对话式导师。提到 Google DeepMind 的 LearnLM 与 Eedi 合作，发现 75%+ 的 AI 生成教学消息无需大修。
- **启示**: 为你的 benchmark 提供动机——"AI tutor 质量已接近可用，但缺乏系统性评估方法"。

### 15. Hierarchical Pedagogical Oversight: Multi-Agent Adversarial Framework
- **arXiv**: [2512.22496](https://arxiv.org/abs/2512.22496) | AAAI 2026
- **内容**: 用 8B 参数模型在 MRBench 上达到 0.845 Macro F1，超越 GPT-4o 3.3%。采用对辩式多 agent 框架（specialist agents + 五幕辩论），用于教学质量评估。
- **启示**: 你的评估用的是多维度 judge，这篇用的是对辩式 judge。可以在 Discussion 中对比两种评估架构的优劣。

---

## 五、安全 & 对抗性评估

### 16. Unsafer in Many Turns: Benchmarking Multi-Turn Safety Risks in Tool-Using Agents
- **arXiv**: [2602.13379](https://arxiv.org/abs/2602.13379) | 2026.02
- **内容**: 评估工具使用 agent 在多轮对话中的安全风险。发现多轮交互比单轮更容易绕过安全防护。
- **启示**: 与你的 A-series adversarial tasks 直接相关——你也测多轮对话中的安全边界（投资建议、内幕交易、prompt injection 等）。

### 17. DREAM: Dynamic Red-teaming for Evaluating Agentic Multi-Environment Security
- **arXiv**: [2512.19016](https://arxiv.org/abs/2512.19016) | 2025.12（活跃到 2026）
- **内容**: 通过动态多轮对抗模拟评估 agent 的交互级安全性。攻击链在 70%+ 模型上成功，展示了有状态跨环境攻击的威力。
- **启示**: 你的 A-series 是静态对抗（预设 prompt），DREAM 是动态对抗。可以在 Limitations 中提到你的对抗评估是一阶的。

### 18. RedBench: A Universal Dataset for Comprehensive Red Teaming
- **arXiv**: [2601.03699](https://arxiv.org/abs/2601.03699) | 2026.01
- **内容**: 整合 37 个 benchmark 为统一框架，29,362 个样本，22 个风险类别 × 19 个领域。
- **启示**: 你的 A-series（17 个对抗任务）可以与这篇的风险分类法对标，说明你覆盖了哪些金融特定风险。

---

## 六、科学研究 Agent 评估

### 19. FIRE-Bench: Evaluating Agents on the Rediscovery of Scientific Insights
- **arXiv**: [2602.02905](https://arxiv.org/abs/2602.02905) | 2026.02
- **内容**: 通过让 agent "重新发现"已知的 ML 研究结论来评估其科学发现能力。解决了现有 benchmark "要么依赖 LLM-as-judge 评分，要么只用粗粒度代理指标"的两难。
- **启示**: 你的 benchmark 也面临同样的两难——你用的是 LLM-as-judge（tutor 7D）+ 程序化验证（quant result）的组合。FIRE-Bench 的 insight-rediscovery 思路值得参考。

### 20. AIRS-Bench: Tasks for Frontier AI Research Science Agents
- **arXiv**: [2602.06855](https://arxiv.org/abs/2602.06855) | 2026.02
- **内容**: 面向前沿 AI 研究 agent 的任务套件。
- **启示**: 另一个领域特定 agent benchmark，可对比任务设计方法。

### 21. BioAgent Bench: AI Agent Evaluation for Bioinformatics
- **arXiv**: [2601.21800](https://arxiv.org/abs/2601.21800) | 2026.01
- **内容**: 生物信息学 agent 评估套件。包含端到端任务（RNA-seq、变异检测、宏基因组学），用 LLM-based grader 评分管线进度和结果有效性。鲁棒性测试发现"正确的高层管线构造不等于可靠的步骤级推理"。
- **启示**: 与你的 I-series（LEAN 回测实现）类似——agent 可能"高层理解正确但具体步骤出错"。

### 22. SciVisAgentBench: Evaluating Scientific Data Analysis and Visualization Agents
- **arXiv**: [2603.29139](https://arxiv.org/abs/2603.29139) | 2026.03
- **内容**: 评估科学数据可视化 agent，覆盖数据分析和可视化工作流。
- **启示**: 你的 D-series（数据分析任务）评估了类似能力但在金融领域。

---

## 七、编码 Agent 评估

### 23. FeatureBench: Benchmarking Agentic Coding for Complex Feature Development
- **arXiv**: [2602.10975](https://arxiv.org/abs/2602.10975) | 2026.02
- **内容**: 200 个任务，24 个开源仓库。Claude 4.5 Opus 在 SWE-bench 上 74.4%，但在 FeatureBench 上仅 11.0%。揭示 bug 修复与特性创建之间的巨大能力鸿沟。
- **启示**: 类似地，你的 benchmark 区分了"知识问答"和"策略实现"，测试不同层次的能力。

### 24. ABC-Bench: Benchmarking Agentic Backend Coding in Real-World Development
- **arXiv**: [2601.11077](https://arxiv.org/abs/2601.11077) | 2026.01
- **内容**: 真实后端开发任务的 agent 评估，涵盖 API 设计、数据库操作等。
- **启示**: 你的 I-series 也是真实代码（LEAN C#）开发任务。

---

## 八、多轮交互评估

### 25. DETOUR: An Interactive Benchmark for Dual-Agent Search and Reasoning
- **arXiv**: [2602.00352](https://arxiv.org/abs/2602.00352) | 2026.02
- **内容**: 1,011 个 prompt，双 agent 架构（Primary Agent + Memory Agent），评估通过查询另一个 agent 来检索信息的能力。
- **启示**: 你的 tutor-student 也是双 agent 交互，但你的 student 是模拟的评估工具。

### 26. AgentChangeBench: Evaluating Goal-Shift Robustness in Conversational AI
- **arXiv**: [2510.18170](https://arxiv.org/abs/2510.18170) | 2025.10（更新至 2026）
- **内容**: 评估工具增强的语言模型 agent 在对话中适应目标变更的能力，覆盖三个企业领域。
- **启示**: 你的 student simulator 也会在对话中改变话题方向（learning goals 之间切换），类似于 goal-shift。

---

## 关键趋势（2026 年特有）

| 趋势 | 代表论文 | 与你的关系 |
|------|---------|-----------|
| **MCP 成为工具评估标准协议** | MCPAgentBench, FinToolBench, TraderBench | 你的 MCP proxy 架构与前沿一致 |
| **教育 AI 评估快速兴起** | TutorBench, ISD-Agent-Bench, AgentTutor, MRBench | 你的 benchmark 填补"量化金融教学评估"空白 |
| **心理测量学引入 agent 评估** | Agent Psychometrics (IRT) | 你可以用 IRT 分析任务难度/区分度 |
| **动态对抗性测试** | DREAM, Unsafer in Many Turns, TraderBench | 你的 A-series 是静态版本，可讨论扩展方向 |
| **科学领域 agent 评估爆发** | FIRE-Bench, AIRS-Bench, BioAgent Bench, SciVisAgentBench | 领域特定 benchmark 是大趋势，你的工作符合 |
| **从结果评估到过程评估** | ProAgentBench, Agent Psychometrics | 你的 QP (Quant Process) + 7D tutor 是过程评估典型 |

---

## 你的 Related Work 中建议引用的 2026 论文

### 必引
1. **TutorBench** (2510.02663) — 最直接的教学评估竞品
2. **ISD-Agent-Bench** (2602.10620) — 教学设计 agent 评估，multi-judge 方法论
3. **FinToolBench** (2603.08262) — 金融工具使用评估
4. **TraderBench** (2603.00285) — 金融 agent + MCP + 对抗性
5. **Unifying AI Tutor Evaluation** (2412.09416) — 教学评估分类法

### 强烈建议
6. **MCPAgentBench** (2512.24565) — MCP 工具评估对标
7. **Agent Psychometrics** (2604.00594) — benchmark 质量分析方法
8. **FIRE-Bench** (2602.02905) — "LLM-as-judge vs 程序化验证"的两难讨论
9. **Unsafer in Many Turns** (2602.13379) — 多轮安全评估
10. **BioAgent Bench** (2601.21800) — 领域特定 pipeline 评估（"高层正确≠步骤可靠"）

Sources:
- [Benchmark Test-Time Scaling](https://arxiv.org/abs/2602.18998)
- [ProAgentBench](https://arxiv.org/abs/2602.04482)
- [MAESTRO](https://arxiv.org/abs/2601.00481)
- [Agent Psychometrics](https://arxiv.org/abs/2604.00594)
- [MCPAgentBench](https://arxiv.org/abs/2512.24565)
- [FinToolBench](https://arxiv.org/abs/2603.08262)
- [TraderBench](https://arxiv.org/abs/2603.00285)
- [PredictionMarketBench](https://arxiv.org/abs/2602.00133)
- [Evaluating Financial Intelligence](https://arxiv.org/abs/2603.08704)
- [TutorBench](https://arxiv.org/abs/2510.02663)
- [ISD-Agent-Bench](https://arxiv.org/abs/2602.10620)
- [Unifying AI Tutor Evaluation](https://arxiv.org/abs/2412.09416)
- [AgentTutor](https://arxiv.org/abs/2601.04219)
- [The Path to Conversational AI Tutors](https://arxiv.org/abs/2602.19303)
- [Hierarchical Pedagogical Oversight](https://arxiv.org/abs/2512.22496)
- [Unsafer in Many Turns](https://arxiv.org/abs/2602.13379)
- [DREAM](https://arxiv.org/abs/2512.19016)
- [RedBench](https://arxiv.org/abs/2601.03699)
- [FIRE-Bench](https://arxiv.org/abs/2602.02905)
- [AIRS-Bench](https://arxiv.org/abs/2602.06855)
- [BioAgent Bench](https://arxiv.org/abs/2601.21800)
- [SciVisAgentBench](https://arxiv.org/abs/2603.29139)
- [FeatureBench](https://arxiv.org/abs/2602.10975)
- [ABC-Bench](https://arxiv.org/abs/2601.11077)
- [DETOUR](https://arxiv.org/abs/2602.00352)
- [AgentChangeBench](https://arxiv.org/abs/2510.18170)
