# QuantTutorBench Harness Engineering 深度分析

> 从 Harness Engineering 视角对 QuantTutorBench 进行的系统性审视，涵盖架构亮点、不足、核心问题回答和改进建议。

---

## 一、什么是 Harness Engineering

**Harness Engineering** 是指围绕 LLM/AI Agent 设计和优化其**外部脚手架（harness/scaffold）**的工程实践——包括系统提示、工具定义、执行环境、评估流程、上下文管理等——以最大化模型在特定任务上的表现。

核心思想：

> 模型的实际表现 = 模型能力 × harness 质量

同一个模型，换一套 harness，表现可能天差地别。传统关注点是模型本身（训练数据、参数、RLHF），而 Harness Engineering 的核心洞察是：**harness 的质量往往是决定性的变量**。

### Harness 的组成部分

| 层级 | 内容 | 示例 |
|------|------|------|
| **System Prompt** | 角色定义、行为约束、输出格式 | "你是一个量化策略专家，回答时必须包含代码" |
| **Tool Design** | 给模型提供哪些工具、工具的参数设计 | 文件读写、代码执行、搜索 |
| **Orchestration** | 多轮对话流程、重试逻辑、分支策略 | Agent loop、plan-then-execute |
| **Context Management** | 什么信息在什么时候喂给模型 | RAG、滑动窗口、摘要压缩 |
| **Evaluation Pipeline** | 如何判断输出质量 | 自动化测试、LLM-as-judge |
| **Environment** | 沙箱、文件系统、运行时 | Docker 容器、git worktree |

### Harness Engineering vs Prompt Engineering

| | Prompt Engineering | Harness Engineering |
|---|---|---|
| **范围** | 单次提示词 | 整个执行管线 |
| **关注点** | 措辞、few-shot | 工具、环境、流程、评估 |
| **复杂度** | 一个字符串 | 一个系统 |
| **类比** | 写好一道题 | 设计整个考试系统 |

### 为什么最近火了

1. **Benchmark 竞争白热化**：SWE-bench、GAIA 等榜单上，各家模型分数接近，差异化主要靠 harness
2. **Agent 时代到来**：从单轮 Q&A 到多步骤自主执行，harness 的复杂度和重要性指数级上升
3. **实际案例验证**：同一模型在不同 harness 下，SWE-bench 分数可以差 20%+
4. **工程 > 炼丹**：对应用开发者来说，优化 harness 的 ROI 远高于等待下一代模型

---

## 二、QuantTutorBench Harness 架构全景

本系统本质上是一个 **5 层 harness**：

```
┌─────────────────────────────────────────────────┐
│  Layer 5: Evaluation (多维评分 + LLM-as-Judge)    │
├─────────────────────────────────────────────────┤
│  Layer 4: Simulation (DeepEval 学生模拟器)        │
├─────────────────────────────────────────────────┤
│  Layer 3: Orchestration (5-phase lifecycle)       │
├─────────────────────────────────────────────────┤
│  Layer 2: Tool Layer (MCPProxy + 40+ tools)      │
├─────────────────────────────────────────────────┤
│  Layer 1: Agent Adapter (Anthropic/OpenAI/Google) │
└─────────────────────────────────────────────────┘
```

### Layer 1: Agent Adapter

多厂商适配层，支持 Anthropic（BetaToolRunner + Claude SDK）、OpenAI（Direct API + Agents SDK）、Google（genai SDK）、Generic（OpenAI-compatible）。

关键特性：
- 跨 turn 上下文持久化（`_input_history`）
- Token 使用追踪（`TokenRecord` 累积）
- Extended thinking 捕获（Anthropic）
- 历史压缩（Anthropic >40K token、OpenAI >100K token 时自动摘要）
- 取消信号传播（`threading.Event`）

### Layer 2: Tool Layer

MCPProxy 透明代理 + 40+ 量化金融工具 + 15-tool slot 机制。

关键特性：
- 所有工具调用通过 proxy 统一记录（args、result、duration、success）
- 结果截断策略（12K char，head+tail 保留结构和结论）
- Distractor tools（功能性的，非简单报错）
- 路径安全约束（文件访问限制在 workspace/data/docs/student_code）
- Deadline 和 cancellation 强制执行

### Layer 3: Orchestration

5-phase 生命周期：RESET → INTERACT → CAPTURE → EVALUATE → TEARDOWN。

关键特性：
- Docker 沙箱隔离（LEAN C# 回测环境）
- 持久化 tool_executor daemon（JSON-line 双向通信）
- LEAN 预热编译（~13s vs ~190s 冷启动）
- 动态上下文注入（oracle_context / tutor_context）
- 试验系统（Trial Manager，原子锁 + 快照 + 自动选优）

### Layer 4: Simulation

DeepEval ConversationSimulator 包装 + 自定义 _EfficientSimulator。

关键特性：
- Persona 驱动的学生模拟（3 级：beginner / intermediate / advanced）
- 增量 TC 覆盖追踪（bitmap，~97% token 节省）
- 重复检测（连续 2 次相同回复强制停止）
- 超时执行（wall-clock deadline）
- 重试机制（0 turn 但消耗了 token 时自动重试，最多 2 次）

### Layer 5: Evaluation

多维评分体系：

```
Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score
Quant Agent Score = 0.50 × Result Sub-score + 0.50 × Process Sub-score
```

**Quant Result (QR)**: Code Eval (3 层: 15% 静态分析 + 35% 执行结果 + 50% 输出验证) + Programmatic Eval (test_scripts) + LLM Result Judge (completeness 55% + correctness 45%)

**Quant Process (QP)**: 7 维加权 (tool_usage 0.20, process_reasonableness 0.20, step_efficiency 0.15, code_process 0.15, process_alignment 0.10, role_adherence 0.10, topic_adherence 0.10)

**Tutor Quality**: 7D rubric via ConversationalGEval (D1_level_detection, D2_language_adaptation, D3_scaffolding_calibration, D4_domain_accuracy, D5_code_teaching, D6_empathetic_response, D7_safety_boundaries)

---

## 三、亮点分析（应该做得更突出的）

### 亮点 1: 多维评估体系设计精良

QR（结果）、QP（过程）、Tutor（教学质量）三维分离，且 QP 内部又有 7 个子维度，这是本系统**最大的差异化优势**。

**为什么突出**：大多数 benchmark（SWE-bench、HumanEval）只看结果对不对。QuantTutorBench 同时评估了 agent 的**思维过程**和**教学能力**，这在学术上是有新颖性的。

**如何做得更突出**：
- 在论文/presentation 中，用具体案例展示"QR 高但 QP 低"的场景（agent 凑出了正确答案但过程一塌糊涂）和"QR 低但 Tutor 高"的场景（结果有偏差但教学过程很好），证明多维度的必要性
- 考虑增加维度间的**诊断性分析**——比如 QP 和 QR 的相关性系数，能揭示 "好过程是否导致好结果"

### 亮点 2: _EfficientSimulator 的 TC 增量覆盖

`simulation.py` 中的 `_EfficientSimulator` 用 bitmap 跟踪 termination criteria 覆盖状态，每轮只检查最新交换 + 覆盖状态，实现了 **~97% 的 token 节省**（固定 ~1400 token vs O(n) 增长）。

**为什么突出**：这是一个非常实际的工程优化，直接影响运行成本和可扩展性。

**如何做得更突出**：
- 量化这个优化的 cost saving（比如 15 轮对话下节省了多少 token/美元）
- 作为一个独立的工程贡献点在论文中描述

### 亮点 3: Distractor Tools 设计

`registry.py` 的 15-tool slot 机制（core + convenient + random distractors），且 distractor 是**功能性的**（VaR、GARCH、Monte Carlo——真实的量化工具，但与任务无关），不是简单报错的 dummy tool。

**为什么突出**：这测试了 agent 的**工具选择判断力**——它不能靠试错发现哪个工具是假的，必须真正理解任务才能避免无关工具。

**如何做得更突出**：
- 做 ablation：有 distractor vs 无 distractor 的得分差异
- 分析不同模型的 distractor 调用率，作为"工具判断力"的独立指标

### 亮点 4: Trial System（I 系列）

`trial_manager.py` 的原子锁 + 快照 + 自动选优机制，模拟了真实的量化开发迭代流程（写代码 → 回测 → 看指标 → 修改 → 再回测）。

**为什么突出**：有预算约束的迭代优化是真实量化工作的核心模式，5-trial 限制迫使 agent 做出有意义的改进而非暴力搜索。

### 亮点 5: 2×2 实验矩阵

```
              Tools-Enabled  |  Pure LLM
Tutor Prompt     agent       |  pure_llm
Baseline         baseline    |  pure_llm_baseline
```

这允许**分离工具贡献和 prompt 贡献**，是科学严谨性的体现。

### 亮点 6: Docker 沙箱隔离 + LEAN 集成

每个 task 运行在独立的 Docker 容器中，挂载只读数据 + 读写 workspace。LEAN C# 回测引擎的集成实现了**真实的量化回测能力**，而非模拟或近似。

### 亮点 7: 完整的自动化闭环

```
Task Definition → Agent Creation → Student Simulation → Multi-turn Interaction
→ Tool Execution (Docker sandbox) → Result Capture → Multi-dimensional Evaluation
→ Score Aggregation → Report Generation
```

从 `python run_benchmark.py run-layer2` 到拿到完整的 scores.md，中间不需要人工干预。

---

## 四、不足分析（需要修复的）

### 问题 1: 学生模拟器是黑盒，且行为不可控

当前依赖 DeepEval 的 `ConversationSimulator`，学生行为由 persona JSON 描述驱动，但：
- 模拟器的行为**不可复现**——相同 persona + 相同 task，两次运行的对话路径可能完全不同
- 没有 seed 控制模拟器的随机性
- persona 的 behavioral_rules 只是提示词层面的约束，无法保证模拟器严格执行

**影响**：这是 benchmark **可复现性**的最大威胁。如果学生问了不同的问题，agent 的表现自然不同，那分数的方差有多少来自模拟器本身？

**建议**：
- 增加 **deterministic mode**：预定义学生的每轮消息序列（scripted conversation），作为基线对比
- 至少记录并发布模拟器的 system prompt + temperature，让其他研究者可以近似复现
- 报告分数时，增加 **inter-run variance** 指标（同 task × persona 跑 3 次取方差）

### 问题 2: 评估过度依赖 LLM-as-Judge

评估链条里有大量的 LLM 调用：
- Result Judge (completeness + correctness)
- Process 的 5/7 个维度是 LLM-judged
- Tutor 的 7 个维度全是 ConversationalGEval
- 当 programmatic eval 不够时，权重自动转移给 Judge

**影响**：
- 评估成本高（每个 task 需要多次 LLM 评估调用）
- LLM Judge 本身有偏差（Claude 评 Claude 可能偏高）
- 评估结果不稳定——已经用了 `NUM_JUDGE_RUNS = 3` 取平均，但仍有噪声

**建议**：
- **增加 programmatic eval 的覆盖面**。目前 test_scripts 已经很好了，但可以进一步：比如 code_process 的 "iterative refinement" 完全可以用 tool_log 的时序分析来做（write → exec → error → write → exec → success），不需要 LLM
- 对 LLM Judge 做 **cross-model consistency check**——已经支持 `EVAL_DEFAULT_MODELS` 列表，建议默认用至少 2 个不同厂商的模型，报告 inter-judge agreement (Cohen's Kappa)
- 区分 "LLM-required" vs "LLM-optional" 的维度，在报告中标注

### 问题 3: Context Management 策略不够精细

Anthropic adapter 的 compaction 策略（>40K token 时总结 + 清除旧 tool_use blocks 保留最新 6 个）和 OpenAI 的（>100K 时总结）是合理的，但：
- **阈值是硬编码的**，不同 task 复杂度差异很大（I01 简单 SMA vs I10 的 250 参数网格搜索）
- 清除 tool_use blocks 时可能丢失关键的中间结果上下文
- 没有**选择性保留**——比如对于 I 系列，前几个 trial 的错误信息比成功的数据更有价值

**建议**：
- 基于 task category 动态调整 compaction 阈值
- 对 implementation 类 task，保留 error 类的 tool_result 而非最近 N 个

### 问题 4: 超时处理粗糙

当前 `timeout_minutes` 是 task 级别的硬超时，超时后截断对话、用已有 turns 评估。但：
- 没有区分"agent 在思考"和"agent 在死循环"
- 没有 **per-turn timeout**（一个 turn 卡住 10 分钟 vs 15 个 turn 各用 1 分钟，对评估的意义完全不同）
- 超时后的部分评估，分数可能因为 "未完成" 而偏低，但这到底是 agent 能力问题还是超时设置问题？

**建议**：
- 增加 per-turn soft timeout（比如单轮 3 分钟警告）
- 记录 timeout 发生时的 completion_ratio（已完成的 TC 项占比），在评估时区分 "完成但做错" vs "没来得及做"
- 在报告中标注哪些分数是在 timeout 状态下产生的

### 问题 5: 缺乏 Harness 自身的 Meta-Evaluation

有 65 个 task × 3 persona × 多个 model 的评估矩阵，但没有对 harness 本身的质量度量：
- 评估指标的**区分度**如何？（如果所有模型在某个维度上都得 0.8-0.9，这个维度可能太容易或打分太宽）
- 评估指标的 **test-retest reliability** 如何？
- Programmatic eval 和 LLM Judge 的**一致性**如何？

**建议**：
- 计算每个评估维度的 **item discrimination index**
- 发布 evaluation meta-report

---

## 五、核心问题回答

### Q1: 这算自动化测评吗？

**是的，而且自动化程度很高。** 系统实现了完整的自动化闭环：

```
Task Definition → Agent Creation → Student Simulation → Multi-turn Interaction
→ Tool Execution (Docker sandbox) → Result Capture → Multi-dimensional Evaluation
→ Score Aggregation → Report Generation
```

从 `python run_benchmark.py run-layer2` 到拿到每个 task 的 scores.md，中间不需要人工干预。这在 agent benchmark 领域是相当完整的。

**但有一个关键缺失**：当前的自动化是**单次运行**的自动化，不是**持续评估**的自动化。缺乏：
- 模型版本变更后的 regression detection
- 跨版本分数趋势追踪
- 自动触发评估的 CI/CD 集成

这些是从"自动化测评工具"到"自动化测评平台"的差距。

### Q2: 限制工具使用范围后，我们测的究竟是什么？Benchmark 还有意义吗？

**矛盾是真实存在的，但并非无解。**

当前测试的是一个**受控环境下的 agent 能力组合**：

```
实际测试的 = 领域知识 × 工具使用判断力 × 教学能力 × 在约束下的问题解决能力
```

这**不等于**"真实世界的 agent 能力"，但这不意味着没有意义。类比：

> 高考不测试真实世界的工作能力，但它测试了一组有预测力的能力子集。SAT 不让你上网查资料，但它仍然是一个有效的评估。

**工具约束实际上有三层价值：**

**1. 控制变量的科学价值**

如果不固定工具集，无法比较模型 A 和模型 B——因为它们可能调用了完全不同的外部服务。15-tool slot + distractor 机制给了一个**标准化的能力测试环境**。

**2. 测试工具选择判断力的价值**

给了 agent 15 个工具（含 distractor），它必须判断哪些该用、怎么用、什么顺序用。这本身就是 agent 能力的核心维度。开放式 tool-use 反而可能测的是"谁的工具生态更丰富"而非 agent 本身的能力。

**3. 可复现性的工程价值**

固定工具集 = 固定的 action space，这让结果可以被其他研究者复现。

**需要坦诚地界定 scope：**

Benchmark 测的不是 "Which agent is the best at quantitative finance"，而是：

> **"Given a standardized quant-finance toolkit and tutoring scenario, which LLM demonstrates the best combination of domain knowledge, tool orchestration, pedagogical skill, and process quality?"**

这个定义是完全合理的，也是可以发论文的。建议在论文的 Limitations 中明确说明：
- 固定工具集意味着不测试 agent 自主发现/创造工具的能力
- 不测试真实世界中的 API 集成、数据获取、环境配置等能力
- 结果的 external validity 需要配合产品级测试来验证

### Q3: 延迟问题与 Benchmark 有效性

**延迟问题分两层：**

**第一层：模拟对话中的延迟是人工产物，不影响 benchmark 有效性。**

在 simulation 中，学生模拟器和 agent 之间的对话是异步批处理的——学生不会因为等 30 秒就"不耐烦退出"。评估的是**对话内容质量**，不是**对话响应速度**。所以当前的 benchmark 设计中，延迟不是一个变量，也不应该是。

**第二层：真实产品中的延迟是另一个需要单独测评的维度。**

当这套系统部署为真实产品时，延迟确实会影响用户体验。但这属于 **UX/系统性能测评**，不属于 **agent 能力测评**。它们应该是两套独立的评估：

```
Benchmark (当前):     能力 → 内容质量、过程质量、教学质量
Production Eval (未来): 体验 → 延迟、首 token 时间、流式输出效果、用户满意度
```

**"使用了真实产品之后延迟问题是否能得到解决？"**

是的，有几个原因：
1. **流式输出**：真实产品可以 stream token，用户看到的是逐字输出而非等待空白页
2. **预计算/缓存**：常见的工具调用结果（如 `fetch_market_data`）可以预加载
3. **异步工具执行**：LEAN 回测等耗时操作可以在后台运行，前端显示进度条
4. **模型优化**：小模型做简单应答、大模型做深度分析的分层架构
5. **乐观 UI**：先展示已知部分（如"正在回测中..."），再补充结果

这些都是产品工程问题，与 benchmark 的有效性无关。Benchmark 测的是"agent 最终能否给出高质量的量化教学"，而非"它能多快给出"。

---

## 六、改进建议优先级

| 优先级 | 行动 | 理由 |
|--------|------|------|
| **P0** | 增加 scripted conversation mode（确定性学生） | 解决可复现性——这是论文最可能被 reviewer 质疑的点 |
| **P0** | 明确定义 benchmark scope（"我们测的是什么"） | 直接回应 Q2 焦虑，也是论文 framing 的核心 |
| **P1** | 增加 programmatic eval 覆盖面，减少 LLM Judge 依赖 | 降低成本 + 提高稳定性 |
| **P1** | 增加 inter-run variance 报告 | 量化 benchmark 的可靠性 |
| **P2** | Context management 按 task 类型动态调整 | 提升复杂 task 的 agent 表现 |
| **P2** | Evaluation meta-report（区分度、一致性） | 证明评估体系本身的质量 |
| **P3** | CI/CD 集成 + 跨版本 regression tracking | 从工具升级到平台 |

---

## 七、附录：系统组件详细分析

### A. Orchestrator 详细架构

#### A.1 Simulation System (simulation.py)

**_EfficientSimulator**: 自定义 ConversationSimulator 子类，增量 TC 覆盖追踪。
- 维护 bitmap 而非发送完整对话给检查 LLM
- 三遍检查策略：head → tail（长消息）→ code blocks（TC 提及代码时）
- 固定 ~1400 token vs O(n) 增长
- TC 全部覆盖时自动生成自然结束消息

**create_model_callback**: 核心对话循环函数。
- 路由学生消息 → agent（通过 proxy 记录工具调用）
- 超时执行（每轮开始检查 deadline）
- 取消支持（线程检查 cancel_event.is_set()）
- 重复检测（连续 2 次相同回复强制停止）
- 实时事件推送到 web UI（student_message, tutor_response with content_blocks）

**run_conversation_simulation**: 主编排函数。
- 构建 ConversationalGolden（scenario, expected_outcome/termination_criteria, user_description）
- 计算 wall-clock deadline
- 对 strategy/backtest/implementation 类别（有编号 TC 项）使用 _EfficientSimulator
- 其他类别回退到原生 ConversationSimulator
- 重试循环：DeepEval 返回 0 turn 但 agent 消耗了 token 时，最多重试 2 次

#### A.2 Agent Adapters

**Anthropic Adapter** — 两个独立开关：
1. `ANTHROPIC_USE_SDK`（传输层）：True = Claude Agent SDK；False = BetaToolRunner（主实现）
2. `AGENT_USE_OAUTH`（认证层）：True = keychain Bearer token；False = API key

BetaToolRunner 模式的关键特性：
- 跨 turn 上下文持久化（`_input_history`）
- DynamicTool 对象（包装 benchmark 工具，自动执行）
- Context compaction（>40K token 时总结 + 清除旧 tool_use blocks 保留最新 6）
- Extended thinking 捕获（在 runner 剥离前捕获）
- Fallback summary（纯 tool_use 无文本时额外调用获取总结）

**OpenAI Adapter** — 两种模式：
1. Direct API（主实现）：手动 while loop + tool 处理 + history compaction（>100K token）
2. SDK 模式：OpenAI Agents SDK

**Google Adapter**：新 google-genai SDK，function calling 循环，最多 5 次迭代。

**Generic Adapter**：OpenAI-compatible API，单次 tool 处理。

#### A.3 Job Execution

**run_single_job**: 每个 job 创建全新 agent + orchestrator + container（完全隔离）。

**run_jobs_parallel**: ThreadPoolExecutor + DeepEval Rich progress 补丁（线程安全 no-ops）。

**eval_single_job**: 加载之前 run_state.json，重建 tool logs 和对话，仅运行评估阶段。

### B. 评估系统详细架构

#### B.1 Quant Result (QR)

**Code Evaluation** (3 层):
- Layer A — 静态分析 (15%): AST 解析、语法有效性、结构评分、安全分析
- Layer B — 执行结果分析 (35%): 每脚本最后一次执行的分类评分（成功 1.0 → 错误 0.1 → 语法错误 0.0）
- Layer C — 输出验证 (50%): 与 reference key_results 的相对误差评分（<5% → 1.0, 5-15% → 0.75, 15-30% → 0.5, >30% → 0.25）

**Programmatic Eval** (test_scripts): 每个 task 有 `evaluate()` 函数，返回 checklist + weighted score + gates。

**LLM Result Judge**: 多模型并行评估，completeness (55%) + correctness (45%)，1-10 分归一化到 0-1。

**QR 混合策略**: Programmatic (40%) + LLM Judge (60%) 基线，含 divergence dampening。

#### B.2 Quant Process (QP)

7 维加权评估：

| 维度 | 权重 | 方法 |
|------|------|------|
| tool_usage | 0.20 | 纯数学（selection 60% + effectiveness 40%）|
| process_reasonableness | 0.20 | LLM（decomposition 0.3 + soundness 0.4 + error_handling 0.3）|
| step_efficiency | 0.15 | 混合（action_economy 0.4 程序化 + redundancy 0.3 LLM + sequencing 0.3 LLM）|
| code_process | 0.15 | 混合（50% 程序化 + 50% LLM）|
| process_alignment | 0.10 | LLM（coverage 0.4 + depth 0.35 + soundness_delta 0.25）|
| role_adherence | 0.10 | LLM（persona_consistency + pedagogical_value）|
| topic_adherence | 0.10 | LLM（量化金融课程覆盖率）|

#### B.3 Tutor Quality (7D)

ConversationalGEval，每个维度 1-10 分，3 次运行取平均：
- D1_level_detection
- D2_language_adaptation
- D3_scaffolding_calibration
- D4_domain_accuracy
- D5_code_teaching
- D6_empathetic_response
- D7_safety_boundaries

每个类别有不同的维度权重（如 data_analysis 中 D5_code_teaching 权重 0.3，strategy 中 1.0）。

### C. Tool 系统详细架构

#### C.1 核心工具 (40+)

| 类别 | 工具 |
|------|------|
| 文件 I/O | shell_exec, file_read, file_write, file_list |
| 数据获取 | fetch_market_data（冻结 CSV 数据集）|
| 技术分析 | compute_indicator (SMA, EMA, RSI, BOLLINGER, MACD) |
| 回测 | run_backtest（内置策略: ma_crossover, rsi_threshold, bollinger_breakout）|
| 统计 | compute_statistics (ADF, correlation, cointegration, lead-lag, rolling) |
| 图表 | plot_chart（matplotlib → PNG）|
| 信号 | construct_signal (7 类: zscore, momentum, mean_reversion, spread, crossover, composite, volume_imbalance) |
| 特征工程 | engineer_features (11 类: vwap_ratio, realized_vol, OBV, ATR, etc.) |
| 时序对齐 | align_timeseries |
| PnL 分析 | breakdown_pnl |
| 搜索 | search_web (DuckDuckGo), search_docs (本地文档) |
| LEAN | run_lean_backtest (C# 编译 + 运行 + trial 追踪) |
| Trial 系统 | submit_trial, select_submission, get_trial_status |

#### C.2 Distractor Tools

功能性 distractor（非简单报错）：
- compute_var — Value at Risk（历史模拟）
- fit_garch_model — GARCH 波动率预测
- optimize_portfolio — 均值方差优化
- run_monte_carlo — GBM 价格路径模拟

#### C.3 Registry 机制

```python
proxy = create_proxy_for_task(
    core_tool_names=["shell_exec", "file_read", "file_write", "run_lean_backtest"],
    convenient_tool_names=["fetch_market_data", "compute_indicator"],
    seed=42,  # 确定性 distractor 采样
)
# 结果: 4 core + 2 convenient + 9 random distractors = 15 tools
```

### D. Task 体系

#### D.1 65 个 Layer 2 Task

| 类别 | 数量 | 难度 | 典型超时 | 工具需求 |
|------|------|------|----------|----------|
| Implementation (I01-I10) | 10 | Easy→Hard | 25-60 min | LEAN C# |
| Data Analysis (D01-D11) | 11 | Easy-Med | 10-30 min | Python + 文件 |
| Strategy (S01-S06) | 6 | Easy-Med | 30 min | Python 回测 |
| Backtest (B01-B06) | 6 | Easy-Med | 25 min | 指标分析 |
| Debug (X01-X10) | 10 | Easy-Med | 10 min | 代码检查 |
| End-to-End (E01-E05) | 5 | Med-Hard | 25-30 min | 全流程 |
| Adversarial (A01-A17) | 17 | Med-Hard | 10 min | 教育导向 |

#### D.2 3 个 Persona

| Persona | 知识水平 | 行为特征 |
|---------|----------|----------|
| beginner_no_finance | 初级 | 好奇但焦虑，问"OHLCV 是什么"，要类比 |
| intermediate_developer | 中级 | 务实但不耐烦，跳过基础，要可运行代码 |
| advanced_quant | 高级 | 分析但怀疑，质疑假设，要求统计严谨性 |

#### D.3 2×2 实验条件

```
              Tools-Enabled  |  Pure LLM
Tutor Prompt     agent       |  pure_llm
Baseline         baseline    |  pure_llm_baseline
```

### E. 基础设施

#### E.1 Web Dashboard

FastAPI + SSE 实时更新，支持：
- 任务发现和浏览
- 单任务/批量运行配置
- 实时对话 + 工具调用监控
- 结果浏览和重新评估
- Markdown + KaTeX 渲染

#### E.2 Docker 沙箱

LEAN C# 回测环境：
- .NET SDK 10.0 + QuantConnect LEAN engine（固定 commit）
- 挂载点：/workspace (rw), /lean/Data (ro), /data (ro), /docs (ro)
- run_backtest.sh 4 步流程：复制 → 编译 → 运行 → 提取结果
- Exit code 约定：2=编译失败, 3=运行时失败, 4=结果提取失败, 124=超时

#### E.3 数据管线

Binance → Raw CSV → LEAN 目录结构 → HuggingFace 缓存，支持全宇宙下载（1000+ 加密货币对）。

---

*文档生成时间：2026-04-04*
*基于对 /bench 目录下所有核心模块的深度代码阅读*

---

# QuantTutorBench Harness Engineering 分析 Part 2: 论文导向深度讨论

> 针对 Part 1 的 10 项反馈逐一展开，从 NeurIPS 2026 Datasets & Benchmarks Track 投稿视角出发。

---

## 反馈 1: QP-QR 相关性分析与多维度区分度验证

### 1.1 QP-QR 相关性系数的具体实现

**数据准备**：跑完一轮 benchmark 后（比如 3 个 model × 65 tasks × 3 personas = 585 个数据点），每个数据点有：
- `quant_result_score` (QR): 0-1
- `quant_process_score` (QP): 0-1
- `tutor_score`: 0-1
- 以及 QP 的 7 个子维度分数

**分析方法**：

```python
# 1. Pearson 相关性矩阵
import pandas as pd
import scipy.stats as st

df = pd.DataFrame(all_results)  # 每行一个 task×persona×model 的结果
r_qr_qp, p_val = st.pearsonr(df["quant_result"], df["quant_process"])
# 如果 r ≈ 0.9 → 两个维度高度冗余，合并即可
# 如果 r ≈ 0.3-0.6 → 相关但有独立信息，多维度有价值
# 如果 r ≈ 0.0 → 完全独立，更证明多维度的必要性

# 2. 条件分析：固定 QR 水平看 QP 的方差
high_qr = df[df["quant_result"] > 0.7]
print(high_qr["quant_process"].describe())
# 如果 QR>0.7 的 case 中 QP 仍然有 0.2-0.9 的分布 → 证明 QR 高不代表 QP 好

# 3. 四象限散点图
# 横轴 QR，纵轴 QP，每个点标注 task_id
# 关键是找到"离群象限"的案例：
#   - 右下角（QR高/QP低）："碰巧做对了但过程混乱"
#   - 左上角（QR低/QP高）："方法论正确但实现有误"
```

**说服力**在于：
- 如果 QR-QP 相关性 ≤ 0.6，直接证明它们测的是不同能力维度
- 四象限分析的 case study 能让 reviewer 直观理解——"这个 agent 拿到了正确的回测结果（QR=0.85），但它跳过了数据检查、没有处理缺失值、用了 3 个 distractor tool、写了一个不可读的代码块（QP=0.35）"
- 这种分析在 paper 中放 Figure 2-3，是审稿人最喜欢看的 insight

### 1.2 隔离 QR/QP 做区分度测试的具体操作

**方法 1：Ablation Study**

```
实验设计：
- Condition A: 完整评估 (QR + QP + Tutor)
- Condition B: 仅 QR (eval_mode="qr_only")
- Condition C: 仅 QP (eval_mode="qp_only")

对比维度：
- 模型排名是否一致？ → Kendall's τ rank correlation
- 如果 A 和 B 的排名一致 → QP 没有贡献区分度
- 如果 A 和 B 排名不同 → QP 改变了评估结论，证明其价值
```

**方法 2：Item Discrimination Index**

对每个评估维度计算区分度：
```python
# 按总分排序，取上位组(top 27%)和下位组(bottom 27%)
top_group = df.nlargest(int(len(df)*0.27), "overall_score")
bottom_group = df.nsmallest(int(len(df)*0.27), "overall_score")

for dim in ["tool_usage", "process_reasonableness", "step_efficiency", ...]:
    discrimination = top_group[dim].mean() - bottom_group[dim].mean()
    # > 0.4: 优秀区分度
    # 0.3-0.4: 良好
    # 0.2-0.3: 一般
    # < 0.2: 这个维度几乎不区分好坏 agent
```

**方法 3：每个模型的维度雷达图**

```
Claude Sonnet 4.6:  QR=0.72  QP=0.81  Tutor=0.85
GPT-5.2:            QR=0.78  QP=0.65  Tutor=0.70
Gemini 2.5:         QR=0.60  QP=0.73  Tutor=0.68
```

如果不同模型在不同维度上各有优劣（而非某个模型全面碾压），就说明多维度评估确实揭示了不同的能力侧面。

### 1.3 在论文中的呈现方式

建议用一个 Section "Multi-dimensional Analysis" (约 1 页)，包含：
1. QR-QP-Tutor 相关性矩阵热力图 (Figure)
2. 四象限散点图 + 2-3 个标注 case study (Figure)
3. 维度区分度表格 (Table)
4. 模型排名在不同评估模式下的变化 (Table: Kendall's τ)

关键句式："Under QR-only evaluation, Model A ranks first; however, incorporating QP reveals that Model A's process quality is significantly lower than Model B, suggesting that Model A may be overfitting to outcome metrics while employing methodologically unsound processes."

---

## 反馈 2: Adapter 构建是否越界？Benchmark vs Agent Engineering 的边界

### 2.1 这个矛盾是真实的，但你们的做法是合理的

先看看已有 benchmark 怎么处理：

| Benchmark | Harness 提供的 | 理由 |
|-----------|---------------|------|
| SWE-bench | Docker 沙箱 + 评估脚本 | 不提供 agent 实现 |
| SWE-agent | 完整的 agent scaffold | 明确定位为"harness 工程的贡献" |
| GAIA | 工具 API 定义 | agent 实现由参与者自己做 |
| τ-bench | 标准化 tool API + simulator | 提供 baseline agent |
| AgentBench | 每个环境的标准化接口 | 提供参考 agent |

**关键原则**：Benchmark 需要提供**标准化的测试接口**，这不是越界，这是 benchmark 的核心责任。

你们构建 adapter 的原因不是"帮 agent 做得更好"，而是：
1. **标准化输入输出**：确保不同 LLM 接收相同格式的工具描述、系统 prompt
2. **公平对比**：如果不标准化 adapter，测试的就不是模型能力而是"谁的 SDK 碰巧更适合你的工具定义格式"
3. **记录 trace**：评估过程质量需要拦截和记录工具调用，这只能在 adapter 层做

### 2.2 在论文中如何论述

建议在 Section 3 "Benchmark Design" 中明确划分两个层次：

```
┌─────────────────────────────────────────┐
│  Benchmark Infrastructure (我们提供的)     │
│  - Tool API 定义（MCP Schema）             │
│  - 沙箱环境（Docker + LEAN）               │
│  - Adapter Interface（标准化接口规范）       │
│  - Evaluation Pipeline                    │
├─────────────────────────────────────────┤
│  Agent Configuration (可变的)              │
│  - LLM 选择                              │
│  - System Prompt（我们提供 default）        │
│  - Agent Loop 策略（SDK 原生）             │
│  - Context Management（SDK 原生）          │
└─────────────────────────────────────────┘
```

关键论述："We provide reference adapter implementations for major LLM providers as a convenience for reproducibility, but our benchmark specification is defined at the **tool API level**, not the adapter level. Participants may implement their own adapters as long as they conform to the tool schema."

### 2.3 关于教师 prompt 和行为规范

你在 prompt 上花的时间（TUTOR prompt 的精心设计、动态注入、per-category 行为要求）是 benchmark 设计的一部分，不是越界。因为：

**你测的是 "给定这个角色定义，模型能否履行角色"**——角色定义本身就是 benchmark spec 的一部分。

这和考试出题是一回事：你出了一道题"请用导师的方式教学生 SMA"，如果不定义"导师应该怎样教"，那你就无法评估。

**但需要注意**：2×2 矩阵中的 `baseline` condition（无 tutor prompt）就是对照组——它分离了 prompt 贡献，证明你们不是"靠 prompt 作弊"。

### 2.4 Distractor 不被调用的问题

这其实是**积极信号**：说明当前顶级模型在工具选择上已经很强。这本身就是一个 finding，在论文中可以这样呈现：

> "Top-tier models (Sonnet 4.6, GPT-5.2) achieved near-zero distractor call rates (< 2%), suggesting that tool selection judgment is no longer a primary differentiator at this capability level. However, we observe significant differentiation on tool *sequencing* and *effectiveness*..."

建议：
- 保留 distractor 机制（它是 benchmark 设计完整性的体现）
- 用 distractor call rate 作为一个**辅助指标**而非核心指标
- 在 tool_usage 评分中适当降低 distractor penalty 的权重，提升 effectiveness 和 sequencing 的权重
- 如果有 weaker model（如 Gemini Flash、小参数开源模型），它们可能会调用 distractor，形成区分度

---

## 反馈 3: 学生模拟器的稳定性问题

这是整篇论文技术挑战中最重要的一个。

### 3.1 核心矛盾

```
可复现性  ←→  自然性
(scripted)     (generative)
```

完全 scripted 的对话失去了对话的自然性和 agent 的应变能力测试；完全 generative 的对话无法保证可复现性。

### 3.2 建议方案：Anchored Simulation（锚定式模拟）

**核心思想**：不完全 script 学生消息，而是定义**关键锚点（anchor points）**——学生必须在对话中触及的关键问题/行为，但具体措辞由模拟器生成。

```json
{
  "task_id": "I01_implement_sma",
  "persona": "beginner_no_finance",
  "anchors": [
    {
      "trigger_after": "tutor_mentions_SMA",
      "student_must_ask": "什么是移动平均线？能给个简单的例子吗？",
      "flexibility": "paraphrase",
      "purpose": "测试导师对零基础学生的解释能力"
    },
    {
      "trigger_after": "tutor_shows_code",
      "student_must_express": "对代码感到困惑，询问某个参数的含义",
      "flexibility": "contextual",
      "purpose": "测试导师的代码教学能力"
    },
    {
      "trigger_after": "tutor_presents_backtest_results",
      "student_must_ask": "这个 Sharpe ratio 是好还是坏？",
      "flexibility": "paraphrase",
      "purpose": "测试导师对指标的解释"
    }
  ]
}
```

**flexibility 等级**：
- `exact`: 使用精确的预定义文本（最高可复现性）
- `paraphrase`: 模拟器可以改写但必须包含核心语义（中等）
- `contextual`: 模拟器根据上下文自由发挥，但必须触及 anchor 主题（最高自然性）

**实现方式**：在 `_EfficientSimulator` 的 TC 检查机制上扩展：
1. 现有的 TC bitmap 已经追踪"导师完成了哪些内容项"
2. 增加一个 "student anchor bitmap"，追踪"学生提出了哪些关键问题"
3. 模拟器每轮生成消息前，检查下一个未触及的 anchor，将其作为 hint 注入模拟器 prompt

**论文中的表述**："We introduce anchored simulation, a hybrid approach between fully scripted and fully generative student simulation. Key dialogue milestones are pre-specified as anchors, ensuring coverage of critical evaluation points, while the simulator retains freedom in phrasing and contextual responses."

### 3.3 可复现性的量化

无论最终用哪种方案，都需要报告：

```
Metric: Inter-Run Consistency (IRC)
方法: 同一 task × persona × model 跑 3 次
计算:
  - Score ICC (Intraclass Correlation Coefficient) → 分数一致性
  - TC Coverage Overlap → 对话内容覆盖的一致性
  - Kendall's W → 模型排名在多次运行间的一致性

目标:
  - ICC > 0.7 → 可接受
  - ICC > 0.85 → 优秀
  - Kendall's W > 0.8 → 排名稳定
```

**对审稿人的预防式回应**："We acknowledge that generative student simulation introduces variance. To quantify this, we report inter-run consistency (ICC=X.XX, Kendall's W=X.XX across N=3 runs per condition). While individual scores may vary, model-level rankings remain stable (τ=X.XX), confirming the benchmark's reliability for comparative evaluation."

---

## 反馈 4: LLM-as-Judge 的防御策略

### 4.1 审稿人会怎么质疑

基于 NeurIPS D&B Track 常见 review 模式：

> "The evaluation relies heavily on LLM-as-judge, which has known biases (self-enhancement, verbosity preference, position bias). How do you ensure evaluation reliability? Have you validated against human annotations?"

### 4.2 防御策略（必须在论文中包含的）

**策略 A: Human Calibration Study（最重要）**

不需要对所有 585 个结果做人工标注，只需要：
- 随机采样 30-50 个 task×model 结果
- 3 个人类专家（量化背景）对这些结果打分（使用与 LLM Judge 相同的 rubric）
- 计算 human-LLM agreement:
  - Pearson r（连续分数一致性）
  - Quadratic Weighted Kappa（离散等级一致性）
  - Kendall's τ（排名一致性）

**目标**：r > 0.7，κ > 0.6。如果达到，就足以论证 LLM-as-Judge 在本 benchmark 上是有效的。

**成本**：30 个样本 × 3 专家 × 每人 10 分钟 ≈ 15 小时专家时间。这是论文质量的必要投资。

**策略 B: Cross-Model Judge Consistency（已有基础设施）**

你的 `EVAL_DEFAULT_MODELS` 已经支持多模型。报告：
```
Judge Model Pair          | Pearson r | Kappa
Claude Haiku vs GPT-5.2   | 0.XX      | 0.XX
Claude Haiku vs Sonnet 4.6 | 0.XX      | 0.XX
GPT-5.2 vs Sonnet 4.6     | 0.XX      | 0.XX
```

如果 cross-model agreement 高，说明评估不依赖特定 judge 模型。

**策略 C: Programmatic-LLM Agreement**

对于同时有 programmatic eval 和 LLM judge 的 task，报告两者的一致性：
```
对 I01-I10 这 10 个有 reference 的 task:
- Programmatic score vs LLM Judge score: r = 0.XX
- 当两者 diverge > 0.3 时的案例分析
```

### 4.3 在论文中的呈现

建议用一个 Section "Evaluation Reliability" (约 0.5-1 页):
1. Table: Human-LLM agreement metrics
2. Table: Cross-model judge consistency
3. Table: Programmatic-LLM agreement (where applicable)
4. 讨论：哪些维度 LLM judge 最可靠（通常 binary/coarse 判断好于 fine-grained 打分）

### 4.4 实际可做的改进

- **Rubric 锚定**：目前的 GEval 维度描述已经比较详细，但可以在每个维度增加 2-3 个 **anchoring examples**（"score 3 looks like X, score 7 looks like Y"），这被证明显著提升 judge 一致性
- **粗粒度优先**：对于论文中的主要结论，使用 3-tier (Low/Medium/High) 而非 10-point scale 来报告，降低细粒度噪声
- **Programmatic 优先原则**：明确 policy——"如果一个维度可以用 programmatic 方法评估到 >80% 准确度，就不用 LLM judge"

---

## 反馈 5: 更智能的动态 Context Management 策略

### 5.1 当前问题的本质

目前的 compaction（>40K/100K token 时摘要 + 截取旧 tool_use）本质上是**被动式**的——等到快溢出了才处理。更好的策略应该是**主动式**的——根据任务状态决定保留什么。

### 5.2 建议方案：Task-Aware Semantic Compaction

**核心思想**：不是按时序截取，而是按**语义重要性**保留。

```
对于 implementation 类 task（I 系列）：
- 始终保留：最新 trial 的错误信息（这是修复的关键信号）
- 始终保留：task spec + reference 提示
- 可压缩：成功执行的中间 tool_result（只保留摘要）
- 可丢弃：重复的数据查询结果、相同内容的 file_read

对于 strategy 类 task（S 系列）：
- 始终保留：数据分析结论（统计值、图表描述）
- 始终保留：策略假设和验证结果
- 可压缩：原始数据展示
- 可丢弃：环境信息查询

对于 debug 类 task（X 系列）：
- 始终保留：bug 的原始代码 + 错误现象
- 始终保留：诊断过程中的关键发现
- 可压缩：与 bug 无关的探索性调用
- 可丢弃：已确认无关的假设验证
```

**实现层面**（对 adapter 的改动）：

```python
def _compact_history(self, task_category: str):
    """语义感知的历史压缩"""
    # 1. 标记每个 message block 的类型
    for block in self._input_history:
        block._importance = self._classify_importance(block, task_category)

    # 2. 按重要性保留
    # critical: 永不压缩
    # important: 压缩为摘要
    # disposable: 直接移除

    # 3. 对 tool_result 特殊处理
    # 错误结果 → critical（包含修复线索）
    # 成功但大量数据 → important（压缩为 "executed successfully, output: 42 rows × 5 cols"）
    # 环境查询 → disposable
```

### 5.3 但这是否应该在论文中强调？

**不建议**作为论文主要贡献。原因：
- Context management 是 adapter 层的工程细节，不是 benchmark 设计的核心
- 它更像是 "making the harness work" 的基础设施，不是 "what the benchmark measures"
- 在论文中提及即可（1-2 段描述策略），不需要单独成 Section

**但**如果你做了 ablation（不同 compaction 策略 → 不同分数），这可以作为一个 insight 放在分析部分："We observe that context management strategy significantly affects agent performance on complex tasks (I10: +12% with semantic compaction vs. naive truncation), highlighting the importance of harness design in agent evaluation."

---

## 反馈 6: 超时行为融入评分

### 6.1 设计原则

超时不应该简单地 = 低分。需要区分三种情况：

```
Case A: 完成了 80% TC + 超时 → 应该按 80% 完成度评分，不惩罚超时
Case B: 完成了 20% TC + 超时（在 idle/死循环） → 应该惩罚
Case C: 完成了 20% TC + 超时（在积极工作但任务太复杂） → 不惩罚超时本身，但 QR 自然偏低
```

### 6.2 具体实现

```python
# 在 TaskResult 中增加字段
@dataclass
class TimeoutAnalysis:
    timed_out: bool
    tc_coverage_at_timeout: float  # 0-1，已覆盖的 TC 项比例
    last_activity_type: str  # "tool_call" | "thinking" | "idle" | "repeat"
    turns_completed: int
    turns_max: int

    @property
    def completion_ratio(self) -> float:
        return self.turns_completed / self.turns_max

# 评分调整
def adjust_for_timeout(score: float, timeout_analysis: TimeoutAnalysis) -> float:
    if not timeout_analysis.timed_out:
        return score  # 没超时，不调整

    # Case A: 高完成度超时 → 不惩罚
    if timeout_analysis.tc_coverage_at_timeout > 0.7:
        return score

    # Case B: 低完成度 + idle → 惩罚
    if timeout_analysis.last_activity_type in ("idle", "repeat"):
        return score * 0.8  # 20% penalty

    # Case C: 低完成度 + 积极工作 → 标注但不惩罚
    return score  # 自然低分已经反映了能力不足
```

### 6.3 论文中的处理

在报告结果时：
- 标注哪些分数是在 timeout 下产生的（Table 中加 † 标记）
- 报告 timeout rate per model（作为效率指标）
- 讨论："Model A completed 95% of tasks within time limits, while Model B timed out on 30% of implementation tasks, suggesting lower code iteration efficiency."

---

## 反馈 7: 为什么需要 Meta-Evaluation？

### 7.1 直接原因：审稿人会问

NeurIPS D&B Track 的 reviewer checklist 明确包括：

> "Is the evaluation methodology validated? Are the metrics well-calibrated?"

如果你不做 meta-evaluation，reviewer 会问："How do you know your 7D tutor rubric actually measures tutoring quality? How do you know your QP metrics actually correlate with process quality?"

### 7.2 具体需要做什么

不需要一个独立的 "meta-evaluation section"，但需要在论文中回答这些问题：

**Q: "你的评估指标有区分度吗？"**
→ 报告每个维度的 discrimination index（反馈 1 已讨论）

**Q: "你的评估指标稳定吗？"**
→ 报告 inter-run consistency（反馈 3 已讨论）

**Q: "你的 LLM judge 可靠吗？"**
→ 报告 human-LLM agreement + cross-model consistency（反馈 4 已讨论）

**Q: "你的评估指标之间是否冗余？"**
→ 报告 QR-QP-Tutor 相关性（反馈 1 已讨论）

### 7.3 所以本质上

Meta-evaluation 不是一个额外的工作，而是**反馈 1、3、4 的产出物打包在一起**。它在论文中的存在形式是散布在不同 section 中的验证实验，而不是一个独立的 section。

建议将这些验证统一放在 Section 5 "Evaluation Reliability and Calibration" (1-1.5 页)。

---

## 反馈 8: 平台全模式可选的愿景

### 8.1 你理解得没错，但论文的 focus 不应该在这里

你说的 `run all / run layer / run group / run single` 全模式平台是**工程完整性**的体现，论文中用 1 段描述即可。

### 8.2 我提到的"缺失"的真正含义

我说的三项（regression detection、趋势追踪、CI/CD 集成）不是说你的 benchmark 不好，而是指出**从 "benchmark tool" 到 "benchmark platform" 的进化方向**。

对于 NeurIPS 论文来说，你现在的自动化程度完全足够。这三项是论文之外的后续工作。

**在论文中可以这样写**：

> "QuantTutorBench ships as a fully automated evaluation pipeline supporting single-task, group, and full-benchmark execution modes. While the current release focuses on point-in-time evaluation, we plan to extend the platform with regression detection and continuous model monitoring in future work."

---

## 反馈 9: 我们到底在测什么？Adapter 标准化的合理性

### 9.1 核心定位

**你的 benchmark 测的是：**

> **"在标准化的量化金融教学场景下，不同 LLM 作为 agent 核心的教学质量、专业能力、和工具编排能力。"**

**你的 benchmark 不测的是：**
- Agent loop 本身的设计优劣（你使用 SDK 原生 loop 作为控制变量）
- 工具生态的丰富程度（你固定了 15-tool slot）
- 基础设施能力（Docker、网络等由 benchmark 统一提供）

### 9.2 "不同的 agent loop 内部真的有那么大差别吗？"

**有差别，但这些差别不是你的 benchmark 要测的。**

```
SDK 层面的差别：                    你的 benchmark 的处理：
─────────────────                  ───────────────────────
Anthropic: 暴露思维链               → 记录但不评分（trace 用于分析）
OpenAI: 不暴露思维链                → 不依赖思维链评分
Anthropic: BetaToolRunner 自动循环  → 统一为 tool_callback 接口
OpenAI: Direct API 手动循环         → 统一为 tool_callback 接口
Google: Function calling            → 统一为 tool_callback 接口
```

你的 adapter 层做的事情是**抹平 SDK 差异，暴露统一的评估接口**。这不是越界，这是 benchmark 的职责。

### 9.3 "由 benchmark 编写者提供 adapter 是否合理？"

**完全合理，而且是行业标准做法。**

- SWE-bench 提供了 harness 和 inference script
- GAIA 提供了 baseline agent 实现
- AgentBench 每个环境都提供了标准化的 agent 接口
- τ-bench 提供了完整的 tool API + baseline agent

**论文中的论述**：

> "To ensure fair comparison across heterogeneous LLM providers, we provide reference adapter implementations that normalize tool calling conventions, context management, and response extraction. Our adapters use each provider's native SDK and agent loop, making minimal modifications beyond what is necessary for (1) tool call interception for evaluation logging, and (2) standardized input/output formatting. The adapter source code is released alongside the benchmark for transparency and reproducibility."

### 9.4 关于 QP 评估在不同 adapter 下的公平性

你提到的矛盾是真实的：Anthropic 暴露思维链而 OpenAI 不暴露，这影响 QP 中某些维度的评估。

**解决方案**：

```
QP 维度对 adapter 差异的敏感度：

tool_usage (0.20)            → 不敏感（纯数学，基于 tool log）
process_reasonableness (0.20) → 中等敏感（LLM judge 看对话内容）
step_efficiency (0.15)       → 低敏感（action_economy 基于 tool log 计数）
code_process (0.15)          → 低敏感（50% 基于 tool log 时序分析）
process_alignment (0.10)     → 中等敏感（需要看 trace）
role_adherence (0.10)        → 不敏感（看对话内容，不依赖思维链）
topic_adherence (0.10)       → 不敏感（看对话内容）

结论：7 个 QP 维度中，5 个（占权重 0.70）不依赖思维链。
2 个中等敏感的维度（占权重 0.30）通过对话内容也能评估。
```

**在论文中声明**：
> "Our QP metrics are designed to be observable from tool call logs and conversation content, without requiring access to internal chain-of-thought. While some providers expose reasoning traces, our evaluation does not privilege this information."

### 9.5 更深层的思考："我们的 agent 和别人的 agent 有什么不同？"

你们的 2×2 矩阵其实已经回答了这个问题：

```
如果 (agent) >> (pure_llm)：
  → 工具使用带来了显著提升，agent 能力 matters

如果 (agent) ≈ (pure_llm)：
  → 对于这类 task，纯 LLM 知识就够了，工具没有边际贡献

如果 (agent, tutor) >> (agent, baseline)：
  → Prompt 设计（角色定义）显著影响了教学质量

如果 (agent, tutor) ≈ (agent, baseline)：
  → 模型本身已经有足够的教学能力，不需要额外 prompt
```

这四个条件的对比结果，本身就是论文的核心 finding 之一。

---

## 反馈 10: Paper 宏观策略与审稿人视角

### 10.1 NeurIPS D&B Track 的审稿标准

基于历年 reviewer guidelines：

| 维度 | 权重 | QuantTutorBench 现状 |
|------|------|---------------------|
| **Novelty** | 高 | ✅ 多维度（QR+QP+Tutor）、量化金融领域、persona-aware tutoring |
| **Quality & Rigor** | 高 | ⚠️ 需要 human calibration + inter-run consistency |
| **Significance** | 高 | ✅ Agent tutoring 是热门方向，量化金融有实际应用 |
| **Reproducibility** | 高 | ⚠️ 学生模拟器可复现性需要解决 |
| **Documentation** | 中 | ✅ 代码完整，需要 datasheet/benchmark card |
| **Ethical** | 中 | ✅ 量化金融无隐私问题，adversarial tasks 测试安全边界 |

### 10.2 论文结构建议（9 页正文）

```
Page 1:    Abstract + Introduction
           - 1 段动机：LLM agent tutoring 是新兴场景，缺乏系统化评估
           - 1 段差异化：多维度（不只看结果）+ 领域专属（量化金融）+ persona-aware
           - 1 段贡献列表

Page 2-3:  Related Work (1 页) + Benchmark Design Overview (1 页)
           - Related: SWE-bench/GAIA/AgentBench/τ-bench 的局限性
           - Design: 5 层架构、65 tasks、3 personas、2×2 矩阵

Page 3-5:  Task Design & Evaluation Framework (2 页)
           - Task taxonomy（7 categories, difficulty distribution）
           - Evaluation dimensions（QR/QP/Tutor 详细公式）
           - Tool system（15-slot, distractor design）
           - Anchored simulation（学生模拟器设计）

Page 5-7:  Experiments & Results (2 页)
           - Main results table（3+ models × 7 categories × overall）
           - Multi-dimensional analysis（QR-QP correlation, 四象限图）
           - 2×2 矩阵对比（tool 贡献 vs prompt 贡献）
           - Difficulty scaling analysis

Page 7-8:  Evaluation Reliability (1 页)
           - Human-LLM agreement
           - Cross-model judge consistency
           - Inter-run variance
           - Dimension discrimination analysis

Page 8-9:  Discussion & Limitations (1 页)
           - What we measure vs what we don't
           - Adapter standardization rationale
           - Generalizability beyond quant finance
           - Future: contamination resistance, continuous monitoring

Appendix:  详细的 rubric、完整结果表、case studies、数据sheet
```

### 10.3 审稿人可能的攻击点及预防

**Attack 1: "这只是一个量化金融的 chatbot 评测，太 narrow 了"**

预防：
- 论述 framework 的通用性——"While we instantiate our framework in quantitative finance, the multi-dimensional evaluation architecture (Result × Process × Tutoring) is domain-agnostic"
- 在 Discussion 中说明如何 generalize 到其他领域（医疗教学、法律教学等）
- 量化金融的选择有具体理由：任务结果可自动验证（回测有数值结果）、工具使用是核心需求、安全边界重要

**Attack 2: "评估指标太多太复杂，哪个 actually matters？"**

预防：
- 维度区分度分析证明每个维度都有独立贡献
- 提供 simplified version：如果只看 OAS（Overall Agent Score），依然有效
- 指出复杂度是分析工具（"researchers can zoom into specific dimensions"），不是使用门槛

**Attack 3: "学生模拟器不可靠，你测的是模拟器还是 agent？"**

预防（最关键的攻击点）：
- Inter-run consistency 数据
- Anchored simulation 设计
- 论述："The simulator is intentionally stochastic to model real student variability. We validate that this variance does not affect model-level rankings."

**Attack 4: "为什么不和 SWE-bench 这些 established benchmarks 比较？"**

预防：
- 明确定位差异："SWE-bench evaluates code generation; we evaluate tutoring-assisted code development"
- 如果可能，在 Discussion 中引用 SWE-bench 上的模型排名，对比你的排名，讨论一致性和差异

**Attack 5: "没有 human baseline"**

预防：
- 如果有人类专家做教师的录制对话，可以作为 upper bound
- 如果没有，在 Limitations 中坦诚说明，并指出："Human tutoring is inherently variable; our persona-based TC coverage serves as a proxy for task completion rather than comparison with human performance."

**Attack 6: "LLM-as-Judge 偏差"**

预防：反馈 4 已详细讨论

### 10.4 论文热点对齐

2025-2026 的 AI 研究热点中，与你的工作最相关的：

1. **Agent Evaluation** — 你的核心定位。SWE-bench、GAIA 之后，community 需要更 fine-grained 的 agent 评估
2. **AI Tutoring / Education** — LLM 教学应用是产业热点，但缺乏 rigorous evaluation
3. **Process Evaluation** — 不只看结果看过程，这是 agent 评估的前沿方向
4. **Benchmark Reliability** — 社区对 benchmark contamination 和 evaluation reliability 越来越关注
5. **Domain-Specific Agent** — 垂直领域的 agent 评估（不是又一个通用 benchmark）

### 10.5 标题建议

```
候选 1: "QuantTutorBench: Multi-Dimensional Evaluation of LLM Agents
         as Quantitative Finance Tutors"

候选 2: "Beyond Correctness: Evaluating Process Quality and Pedagogical
         Effectiveness of LLM Agent Tutors in Quantitative Finance"

候选 3: "QuantTutorBench: A Benchmark for Evaluating Domain Knowledge,
         Tool Orchestration, and Teaching Ability of LLM Agents"
```

建议用候选 2 — 它直接点出了 "Beyond Correctness"（多维度的核心卖点）和 "Process Quality + Pedagogical Effectiveness"（两个差异化维度）。

### 10.6 复现者视角

论文需要提供：
- [ ] GitHub repo（代码、配置、Docker image）
- [ ] HuggingFace dataset（task definitions、reference data、evaluation scripts）
- [ ] Benchmark Card（类似 Model Card，描述 benchmark 的设计选择、已知局限、使用建议）
- [ ] 参考 adapter 实现（Anthropic、OpenAI、Google）
- [ ] 预计算的 reference results（让复现者可以验证 evaluation pipeline）
- [ ] 运行一个 single task 的最小示例（5 分钟内完成）
- [ ] 完整 benchmark 的运行成本估算（API 费用）

### 10.7 关键时间线建议

```
NeurIPS 2026 预计时间线（基于往年）：
- CFP 发布: ~2026-01
- 提交 deadline: ~2026-05（Abstract ~2026-05-中, Full ~2026-05-底）
- Review period: ~2026-06 to 2026-08
- Decision: ~2026-09
- Conference: ~2026-12

你需要在提交前完成：
1. 至少 3 个 model 的完整 benchmark 运行结果
2. Human calibration study（30-50 样本 × 3 专家）
3. Inter-run consistency 实验（关键 task 跑 3 次）
4. Anchored simulation 实现 + 验证
5. 论文撰写 + 代码整理 + 数据集发布准备
```

---

## 总结：10 条反馈的核心行动项

| # | 反馈 | 核心行动 | 论文中的位置 |
|---|------|---------|-------------|
| 1 | QP-QR 相关性 | 跑完结果后计算相关性矩阵 + 四象限图 | Section 5: Experiments |
| 2 | Adapter 是否越界 | 论文中明确划分 benchmark spec vs reference impl | Section 3: Design |
| 3 | 学生模拟器稳定性 | 实现 Anchored Simulation + 报告 IRC | Section 4 + Section 6 |
| 4 | LLM Judge 防御 | Human calibration study（30-50 样本） | Section 6: Reliability |
| 5 | Context Management | 作为工程细节描述，不作为核心贡献 | Section 3: 1-2 段 |
| 6 | 超时评分 | 增加 timeout_analysis + completion_ratio | Section 4: Evaluation |
| 7 | Meta-evaluation | 反馈 1+3+4 的产出物打包 | Section 6 |
| 8 | 平台全模式 | 1 段工程描述 | Section 3 |
| 9 | 测什么 + adapter 合理性 | 明确 scope 定义 + 2×2 矩阵论述 | Section 1 + Section 3 |
| 10 | Paper 策略 | 按上述结构撰写，预防 6 个攻击点 | 全文 |

---

# 附录：Harness/Benchmark 边界逐项调查


> 调查目标：精确定位 harness 逻辑与 benchmark specification 的交叉污染点，评估每个问题的实际影响和论文风险
> 调查方法：逐文件审查代码，区分"影响评分"与"仅影响展示"

---

## B-01：MCPProxy 中嵌入了工具特定逻辑

### 事实发现

**文件**：`mcp_servers/proxy/mcp_proxy.py:237-272`

proxy 的 `call_tool()` 方法中，在 `log.success = True`（工具函数正常返回）之后，有 5 段硬编码的 success 覆写逻辑：

```python
# (1) shell_exec: 非零 exit code → success=False   (line 240-243)
if log.success and log.name == "shell_exec":
    m = _EXIT_CODE_RE.search(log.result)
    if m and int(m.group(1)) != 0:
        log.success = False

# (2) shell_exec: 超时错误 → success=False          (line 248-250)
if log.success and log.name == "shell_exec":
    if log.result.startswith("Error: Command timed out"):
        log.success = False

# (3) shell_exec: stderr 关键错误 → success=False    (line 254-259)
if log.success and log.name == "shell_exec":
    if "[stderr]:" in log.result and any(kw in log.result
        for kw in ("No such file", "command not found", "Permission denied")):
        log.success = False

# (4) run_lean_backtest: 编译/运行时错误 → success=False (line 262-267)
if log.success and log.name == "run_lean_backtest":
    if any(s in log.result for s in ("Status: compile_error", "Status: runtime_error")):
        log.success = False

# (5) plot_chart: Error 前缀 → success=False          (line 270-272)
if log.success and log.name == "plot_chart":
    if log.result.startswith("Error"):
        log.success = False
```

### 影响分析

**`log.success` 被谁消费？**

| 消费者 | 文件 | 用途 | 影响评分？ |
|--------|------|------|-----------|
| `_tool_call_effective()` | `tool_usage.py:29` | effectiveness 计算（QP 的 tool_usage 维度的 40%） | **是** |
| `_is_exec_successful()` | `code_process.py:99-101` | code_process 中的 iterative refinement / error recovery | **是** |
| `_build_trace_summary_for_prompt()` | `process_metrics.py:185` | LLM judge 的 tool trace 描述（"[OK]" vs "[FAIL]"） | **间接是**（影响 LLM 判断） |
| `_extract_agent_key_outputs()` | `result_judge.py:62` | Result Judge 的 tool output 描述 | **间接是** |
| `trace_report.py:25` | 仅展示 | Markdown trace 报告 | 否 |
| `code_eval.py:196` | 执行结果分析 | Layer B 的 per-script success rate | **是** |

**结论**：`log.success` 直接影响 QP 的 tool_usage 和 code_process 评分，间接影响所有通过 LLM judge 的评估。这不是展示层问题，是**评分准确性问题**。

### 实际风险评估

**风险等级：中等偏低**

理由：
1. 这些覆写逻辑是**收紧**（从 True 改为 False），不是**放松**。即：proxy 不会把失败的工具调用标记为成功，只会把"返回正常但内容表明失败"的调用标记为失败。这个方向是安全的——错误地标记失败只会轻微低估 agent 表现
2. `tool_usage.py:22-38` 的 `_tool_call_effective()` 已经独立做了类似检查（检查 "Error:" 前缀和 traceback），所以即使 proxy 不覆写，tool_usage 评分的 effectiveness 部分也会正确判定大多数失败
3. 第三方工具不太可能使用这些特定的错误格式（"[exit code]:"、"Status: compile_error"），所以不会被误判

**但论文视角的风险**：
- 审稿人可能质疑"为什么 proxy 层知道 shell_exec 的输出格式？"
- 解耦方案清晰（让工具自己报告 success/failure），但当前影响可控

### 建议

- **不需要紧急修复**——当前逻辑方向正确（收紧不放松），且 tool_usage.py 有独立的二次检查
- **论文中不需要讨论这个细节**——这是实现层面的工程决策
- **长期改进**：让每个工具函数返回 `(result_text, success_bool)` 元组，proxy 直接使用，不做后处理推断

---

## B-02：Base Adapter 暴露 Anthropic 特有接口

### 事实发现

**文件**：`base_adapter.py:176-198`

```python
def get_thinking_trace(self) -> list[dict]:           # line 176 — Anthropic only
def get_content_blocks(self) -> dict[int, list[dict]]: # line 184 — Anthropic only
def get_last_content_blocks(self) -> list[dict] | None: # line 192 — Anthropic only
```

这三个方法定义在 base class 中，但只有 `ClaudeAgentAdapter` 有实际实现。其他 adapter 返回空值（`[]`, `{}`, `None`）。

### 影响分析

**这些方法被谁消费？**

| 消费者 | 文件 | 用途 | 影响评分？ |
|--------|------|------|-----------|
| `get_last_content_blocks()` | `simulation.py:609` | Web UI 实时渲染（thinking/tool_use 展开） | **否** |
| `get_content_blocks()` | `job_runner.py:105-106` | 存入 trace JSON，嵌入对话记录 | **否** |
| `get_thinking_trace()` | `job_runner.py:103` | 存入 trace JSON | **否** |
| `content_blocks` | `web/static/js/chat.js` | 前端渲染 | **否** |
| `content_blocks` | `evaluation/` | **完全不使用** | **否** |

**结论**：`content_blocks` 和 `thinking_trace` **完全不影响评分**。它们只用于 web UI 展示和 trace 记录。评估管线（scoring.py, test_scripts/, deepeval_metrics/）完全不读取这些字段。

### 实际风险评估

**风险等级：低**

- 不影响评分公平性
- Web UI 对 Anthropic 展示更丰富（能看到 thinking blocks、tool use 内联展示），但这只影响人工审查的便利性，不影响自动化评估
- 在 base class 中定义这些方法是合理的 OOP 实践（子类 override，基类提供默认空值）

### 建议

- **不需要修改**——当前设计是安全的
- **论文中不需要讨论**——这是 UI 功能，不影响 benchmark 评估

---

## B-03：Adapter 层硬编码了 Benchmark 任务知识

### 事实发现

**Anthropic Adapter**（`anthropic_adapter.py:355-364`）：
```python
# Default SDK threshold is 100K tokens — far too late for
# strategy tasks where 10 iterations of shell_exec + plot_chart
# generate 30-40K tokens of tool_use blocks alone.  Lowering to
# 40K triggers summarization after ~6 iterations, preventing
# O(n² ) token growth within a single tool_runner burst.
runner_kwargs["compaction_control"] = {
    "enabled": True,
    "context_token_threshold": 40_000,
}
```

**OpenAI Adapter**（`openai_adapter.py:65-82`）：
```python
COMPACTION_TOKEN_THRESHOLD = 100_000

COMPACTION_SUMMARY_PROMPT = (
    "You have been tutoring a student. Summarize the conversation so far "
    "into a concise continuation context..."
)
```

**Google Adapter**：无压缩逻辑

### 影响分析

**这些设置影响评分吗？**

压缩/compaction 改变了 agent 看到的上下文，间接影响 agent 的回答质量，从而影响所有评分维度。但这里的关键问题是：

1. **40K vs 100K 阈值是否构成不公平？** Anthropic 在 40K 时开始压缩，OpenAI 在 100K。这意味着 Anthropic agent 在长对话中丢失上下文更早。但这个差异源于 Anthropic BetaToolRunner 的 tool_use blocks 远比 OpenAI 的 tool_calls 占更多 token（Anthropic 的 content blocks 包含完整的 thinking text）
2. **COMPACTION_SUMMARY_PROMPT 包含 "tutoring a student"**：这是 benchmark 场景的知识泄漏进 adapter。如果这个 adapter 用于非教学场景，这个 prompt 不适用

### 实际风险评估

**风险等级：中等——但属于 harness 范畴**

根据我们已确立的原则（adapter 是 reference implementation，不是 benchmark spec），这些都是 harness 设计决策：
- 40K 阈值是针对 Anthropic SDK 行为的优化
- 100K 阈值是针对 OpenAI API 行为的优化
- 不同 adapter 有不同的上下文管理策略是**正常的**——这正是 harness engineering 的内容

### 建议

- **不需要修改**——这是 harness 层面的合理优化
- **论文定位**：在论文中声明 "Reference adapters include provider-specific context management optimizations; third-party implementations may use different strategies"
- **长期改进**（非必要）：将 threshold 和 summary prompt 提取为配置常量，不硬编码在 adapter 中

---

## B-04：缺少 Benchmark Specification Document

### 事实发现

**搜索结果**：项目中不存在以下任何文件：
- `BENCHMARK_SPEC.md`
- `TOOL_SCHEMA.json` / `tool_api.yaml`
- `ADAPTER_CONTRACT.md`
- `SUBMISSION_FORMAT.md`

**现有的"规范"分散在代码中**：
- 任务格式：`schemas.py` 中的 Pydantic model（`QuantTutorTask`, `GroundTruth`, `EnvironmentConfig`）
- 工具接口：`mcp_servers/core/tools.py` 中的 `CORE_TOOLS` dict（func + description + params）
- Adapter 接口：`base_adapter.py` 中的 `BaseAgentAdapter` ABC
- 评估协议：分散在 `scoring.py`, `code_eval.py`, `test_scripts/`, `deepeval_metrics/`

### 对比参考论文

| 论文 | 第三方接入方式 | 需要读源码？ |
|------|-------------|------------|
| **SWE-bench** | 提交 JSONL（instance_id + model_patch） | 不需要 |
| **AgentBench** | 实现 HTTP API（`/api/start_sample`, `/api/interact`） | 不需要 |
| **GAIA** | 提交答案字符串（`FINAL ANSWER: [...]`） | 不需要 |
| **QuantTutorBench** | ？ | **需要** |

### 实际风险评估

**风险等级：对论文高，对代码低**

- **代码层面**：当前系统功能完整，四个 adapter 正常工作。缺少 spec 文档不影响系统运行
- **论文层面**：审稿人会问 "How does a third party evaluate their own agent on your benchmark?" 如果答案是"读我们的 Python 代码"，这不符合 benchmark 论文的标准

### 需要定义的最小化 Benchmark Contract

```
1. Task Format（已有，需导出）：
   - QuantTutorTask JSON Schema（从 Pydantic model 自动生成）
   - 示例 task JSON

2. Tool API（需编写）：
   - 15 个 tool 的 name, description, parameters JSON Schema
   - 调用协议：tool_callback(name, **kwargs) -> str
   - 返回值规范：纯文本字符串

3. Conversation Protocol（需编写）：
   - 输入：list[{role: "user"|"assistant", content: str}]
   - 输出：str（assistant 回复文本）
   - 每轮可调用 0~N 次工具

4. Submission Format（需编写）：
   - 最小要求：对话记录 + 工具调用日志 + workspace 产物
   - 格式：与 TaskResult JSON 兼容

5. Evaluation Protocol（需编写）：
   - 输入：submission files
   - 输出：QR, QP, Tutor 各维度分数
   - 可独立于 adapter 运行
```

### 建议

- **P1 优先级**：编写 `BENCHMARK_SPEC.md`，聚焦 Tool API 和 Submission Format
- **快速方案**：从 Pydantic model 自动导出 JSON Schema，附上 2-3 个示例 task
- **论文表述**：Section 3 "Benchmark Design" 中包含 spec 摘要，完整 spec 作为 supplementary material

---

## B-05：Simulation 层的 Anthropic 感知

### 事实发现

**文件**：`simulation.py:606-612`

```python
# Attach content_blocks (thinking/tool_use/tool_result/text) for
# live web UI inline rendering.  Only Anthropic adapter provides
# these; others return None and the frontend falls back to plain text.
turn_blocks = agent_adapter.get_last_content_blocks()
event_data: dict = {"content": response, "turn_index": turn_idx}
if turn_blocks:
    event_data["content_blocks"] = turn_blocks
```

### 影响分析

这段代码的功能是：如果 adapter 提供了结构化的 content_blocks，就把它附加到 web UI 的实时事件中。如果没有，就只传 plain text。

**影响评分？否。** `content_blocks`：
- 不被 evaluation/ 下任何模块读取
- 不影响 ConversationSimulator 的行为
- 不影响 TC checker 的判断
- 仅影响 web UI 展示

**注释 "Only Anthropic adapter provides these" 是否有问题？**
- 这是事实描述，不是逻辑依赖。代码通过 `if turn_blocks:` 做 null-safe 检查，不检查 adapter 类型
- 如果未来 OpenAI adapter 也提供 content_blocks（例如 reasoning tokens），代码无需修改即可工作

### 实际风险评估

**风险等级：极低**

- 代码是正确的 null-safe 模式
- 注释可能给读者造成"这里有 Anthropic 依赖"的错觉，但实际没有
- 不影响评分，不影响论文

### 建议

- **不需要修改**
- 如果想改善可读性，可将注释改为 "Adapters that capture structured content blocks (e.g., thinking, tool_use) can attach them here for richer UI rendering. Falls back to plain text."

---

## B-06：论文中如何定位 Adapter 层？

### 核心问题

Adapter 是 benchmark 的一部分还是独立贡献？

### 分析

参考 SWE-bench / SWE-agent 的分离：
- **SWE-bench**（ICLR 2024）= benchmark spec（任务 + 评估）
- **SWE-agent**（NeurIPS 2024）= reference harness（ACI 设计）
- 两篇独立论文，同一作者团队

我们不需要分成两篇论文，但**论文内部必须明确区分**：

```
Benchmark Specification（论文贡献）:
├── Task taxonomy (65 tasks × 6 categories)
├── Tool API spec (15-slot design)
├── Evaluation protocol (QR + QP + Tutor 7D)
├── Scoring formulas (OAS, QAI, TEI)
└── Student simulation protocol (persona + TC)

Reference Implementation（工程贡献，不是核心论文贡献）:
├── Agent adapters (Anthropic, OpenAI, Google, Generic)
├── Orchestrator (5-phase lifecycle)
├── Docker sandbox (LEAN integration)
├── Context management (compaction strategies)
└── Web UI (live monitoring)
```

### 已确立的决策

在之前的讨论中已经确认：
- Adapter 层不算越界——提供标准化接口是 benchmark 的职责
- Adapter 是 reference implementation，第三方可以用自己的 harness

### 建议论文表述

```
"QuantTutorBench consists of a benchmark specification (task format,
tool API, evaluation rubrics) and a reference implementation (agent
adapters, orchestrator, sandbox). The specification is adapter-agnostic:
any system that can invoke the standardized tool API and produce
conversational responses can be evaluated. We provide reference adapters
for Anthropic, OpenAI, Google, and OpenRouter-compatible models as a
convenience; these adapters include provider-specific optimizations
(context management, extended thinking) that are not part of the
benchmark specification."
```

---

## 综合评估：B 类问题优先级矩阵

| 问题 | 影响评分？ | 影响论文？ | 修复难度 | 优先级 | 行动 |
|------|-----------|-----------|---------|--------|------|
| **B-01** proxy 工具特定逻辑 | 是（tool_usage 评分） | 低 | 中 | **P3** | 当前方向安全，长期改为工具自报告 |
| **B-02** base adapter 暴露 Anthropic 接口 | 否（仅 UI） | 低 | 无需改 | **N/A** | 不修改 |
| **B-03** adapter 硬编码任务知识 | 间接（影响上下文） | 低 | 低 | **P3** | 属于 harness 范畴，论文声明即可 |
| **B-04** 缺少 Benchmark Spec | 否 | **高** | 中 | **P1** | 编写 BENCHMARK_SPEC.md |
| **B-05** simulation Anthropic 感知 | 否（仅 UI） | 极低 | 无需改 | **N/A** | 不修改 |
| **B-06** adapter 论文定位 | 否 | **高** | 写作 | **P0** | 论文中显式区分 spec vs implementation |

### 关键结论

**B 类问题中，真正需要行动的只有两件事**：

1. **B-06（P0）**：论文中显式区分 benchmark specification 和 reference implementation——这是写作问题，不需要改代码
2. **B-04（P1）**：编写 BENCHMARK_SPEC.md，定义 Tool API + Submission Format——支撑论文的 "third-party reproducibility" 论证

其余 B-01/02/03/05 均为**可接受的工程决策**，不影响评分公平性，不需要论文讨论。
