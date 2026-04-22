# QuantTutorBench v4.0 综合状态报告：共识、发现与待解决问题

> 综合日期：2026-04-07
> 覆盖范围：v4.0 阶段全部调研、实验、讨论的系统性总结
> 目的：为 NeurIPS 2026 Datasets & Benchmarks 投稿提供清晰的工程与科学决策基础

---

## 一、已达成的共识

### 1.1 Benchmark 测量目标（已定义）

> "Given a standardized quantitative finance toolset and tutoring scenario, we evaluate an LLM agent's ability to (1) produce correct quantitative analysis results (QR), (2) follow sound analytical processes (QP), and (3) effectively teach domain concepts to students of varying proficiency (Tutor)."

**已确认的范围边界**：
- 测的是**领域 agent 在受控环境下的综合能力**，不是"真实世界的通用 agent 能力"
- 工具约束（15-slot）是**科学控制变量**，不是限制。它隔离了领域推理与工具发现能力
- Adapter 层是 **reference implementation**，benchmark specification 定义在 tool API 层面
- 类比定位：AgentBench（标准化多环境评估）+ tau-bench（领域特定多轮交互）

### 1.2 温度控制策略（已决策并实施）

| 组件 | 决策 | 理由 | 实施状态 |
|------|------|------|---------|
| **Agent** | 不控制（provider 默认 temp=1.0） | agent 是被测对象，控制温度等于替被测者做决定 | ✅ 无需修改 |
| **学生模拟器** | temp=0.0（GPTModel 默认） | 学生不应是方差源（虽然实测证明 temp=0 仍有方差） | ✅ 通过 OpenRouter GPTModel 路径生效 |
| **LLM Judge** | temp=0.0（显式设定） | 评分确定性是评估可信度的基础 | ✅ 已修复：EVAL_USE_OAUTH=False + EVAL_JUDGE_TEMPERATURE=0.0 |
| **TC Checker** | temp=0.0 | TC 判定是对话长度的决定因素 | ✅ OpenRouter 路径 + skip_oauth=True |
| **其他采样参数** | 不控制 | temp=0 下 greedy decoding 取 argmax，top_p 等无影响 | ✅ 无需修改 |

**关键修复**：发现 `EVAL_USE_OAUTH=True` 时 `_OAuthAnthropicModel` 不传 temperature 给 API，导致 judge 实际运行在 temp=1.0。改为 `EVAL_USE_OAUTH=False` 走 OpenRouter GPTModel 路径解决。

### 1.3 Harness 与 Benchmark 边界（已定义）

**已决策**：采用 SWE-bench/SWE-agent 的分离策略——

```
Benchmark Specification（论文 Section 3）:
  - 任务定义（65 tasks × JSON Schema）
  - 工具 API 规范（MCP Schema）
  - 评估流程（QR + QP + Tutor 7D）
  - 学生模拟协议（Persona + Anchored Simulation + TC）

Reference Implementation（论文 Section 4）:
  - Agent Adapters（Anthropic/OpenAI/Google/Generic）
  - Orchestrator（5-phase lifecycle）
  - Docker 沙箱（LEAN C# 回测环境）
  - Context Management
```

**论文表述**：第三方可以用自己的 harness，只要符合 tool API 规范。

### 1.4 ICC 低分的正确解读（已达成共识）

**共识：ICC 低反映的是 agent 行为方差，不是评估链缺陷。**

关键证据：
- X01（简单 debug）三次 run 行为一致时 CV=1.0%，OAS 几乎不变——评估链本身是稳定的
- B02 run1（28min 深度探索但没跑通）vs run2/3（2min 高效完成）是截然不同的 agent 行为——不同的分数是正确的
- 7D reason 输出验证了 B02 run1 的低分有清晰的因果链：超时 → 匆忙 wrap up → 忽略学生问题 → D6=0.2

**论文框架化路径**：
1. 按任务分类报告 CV（展示稳定任务 vs 不稳定任务）
2. 说明 agent 行为稳定时评估稳定（X01 ICC），不稳定时评估正确反映差异（B02）
3. ICC 低不是评估缺陷，是 agent 在 temp=1 下行为方差的真实反映

### 1.5 多维度评估的必要性（已量化证明）

| 证据 | Haiku Judge 数据 | Sonnet Judge 数据 | 结论 |
|------|-----------------|------------------|------|
| **维度独立性** | QR-Tutor: r=0.19 | QR-Tutor: r=0.25 | 两个 judge 下 QR-Tutor 均独立（r<0.3） |
| **区分度 (Cohen's d)** | QR: d≈0; QP: d=0.60; Tutor: d=0.80 | QR: d=0.24; QP: d=1.11; **Tutor: d=1.69** | Sonnet judge 区分度更强 |
| **方向一致性** | OAS: 7/8 S>H; Tutor: 8/8 | OAS: **8/8** S>H; Tutor: 7/7 | 两个 judge 下排名 100% 一致 |
| **Judge 一致性** | — | QR r=0.872; QP r=0.806; Tutor r=0.676 | Programmatic eval 锚定 QR 最稳定 |

**核心论点**：如果只用 QR 评估，sonnet 和 haiku 几乎不可区分（d≈0）；加入 Tutor 后效应量跃升至 very large（d=1.69，sonnet judge）。多维度评估**不是**锦上添花，而是 benchmark 区分能力的核心来源。此结论在两个独立 judge 下完全一致。详见 `judge_comparison_analysis.md`。

### 1.6 学生模拟器非确定性的定位（已决策）

**共识：接受非确定性，定位为特性而非缺陷。**

实验数据（72 次 API 调用，24 个测试点 × 3 重复）：
- 0/24 达到 exact match（GPT-5.2 在 temp=0 下不保证文本确定性）
- 62% D_divergent（实质性差异），38% C_topic_varies
- 开放问题（strategy）比封闭问题（data_analysis）更不稳定（Jaccard 0.24 vs 0.36）
- advanced persona 比 beginner 更不稳定（0.21 vs 0.31）

**论文框架化**（三层论证）：
1. **测量并报告**：per-task CV，student variation 作为已知变量
2. **证明区分度仍然有效**：即使有学生变异，Tutor d=1.69（sonnet judge），模型排名在两个独立 judge 下 100% 一致
3. **定位为 robustness 测试**：不同的学生追问方向测试了 agent 对多样学生行为的适应能力

**论文表述**：
> "The student simulator exhibits controlled stochasticity even at temperature 0, reflecting the inherent non-determinism of large language models. This variation is most pronounced in open-ended tasks and advanced personas. We view this as a feature: it tests the agent's robustness to diverse student inquiry patterns rather than its ability to follow a fixed script."

---

## 二、实验结果汇总

### 2.1 ICC 实验（评估稳定性）

**配置**：Sonnet 4.6 + Haiku 4.5 × 5 tasks × 3 runs × intermediate_developer = 30 runs

**ICC(3,1) 结果**：

| 维度 | Sonnet | Haiku | 解读 |
|------|--------|-------|------|
| OAS | 0.06 | 0.32 | 最终分数不稳定——但原因是 agent 行为方差 |
| QR | 0.27 | 0.51 | QR 是不稳定的主要来源 |
| QP | 0.64 | -0.12 | Sonnet QP 中等稳定；Haiku 工具行为方差极大 |
| Tutor | **0.82** | **0.74** | **最稳定的维度**——LLM judge 在 temp=0 下表现一致 |

**关键异常点**：
- Sonnet D01 QR CV=21.8%：run2 未写 .py → code_eval 跳过 → QR 公式变化（已修复）
- Haiku I01：run3 全维度暴跌（OAS 0.54）——haiku 在 LEAN 任务上偶尔彻底失败
- B02 是 ICC 低的最大贡献者（sonnet QR CV=26.1%，haiku Tutor CV=35.9%）

**结论**：Tutor 维度 ICC 优秀（0.74-0.82），是最可靠的评估维度。OAS 的不稳定主要来自 QR 中 agent 行为差异的真实反映。

### 2.2 学生模拟器稳定性实验

**配置**：GPT-5.2 via OpenRouter, temp=0.0, 5 类别 × 2-3 personas × 2 截断深度 = 24 测试点 × 3 重复 = 72 次 API 调用

**一致性分布**：

| 级别 | 数量 | 含义 |
|------|------|------|
| A_exact（完全相同） | 0/24 (0%) | 无任何测试点达到文本确定性 |
| B_semantic（同意图不同措辞） | 0/24 (0%) | — |
| C_topic_varies（话题选择有差异） | 9/24 (38%) | 同一方向但细节不同 |
| D_divergent（实质性差异） | 15/24 (62%) | 问了不同的问题 |

**按维度分析**：
- 类别：data_analysis 最稳定（Jaccard 0.36），strategy 最不稳定（0.24）
- 人格：beginner 最稳定（0.31），advanced 最不稳定（0.21）
- 截断深度：turn 1 后 vs turn 2 后差异不大（0.27 vs 0.28）

**核心结论**：方差 = agent 方差(temp=1) + 学生方差(temp=0 但不确定) + 交互放大。学生模拟器是独立的方差源。

### 2.3 跨模型区分度（Sonnet vs Haiku）

**Cohen's d 效应量**：

| 维度 | Sonnet mean | Haiku mean | Cohen's d | 效应大小 |
|------|-----------|----------|-----------|---------|
| OAS | 0.740 | 0.695 | +0.59 | medium |
| QR | 0.713 | 0.713 | -0.00 | negligible |
| **QP** | 0.695 | 0.658 | **+0.60** | **medium** |
| **Tutor** | 0.823 | 0.718 | **+0.83** | **large** |

**方向一致性**：Tutor 在全部 8 个任务上 sonnet > haiku（8/8），OAS 7/8，QR 仅 5/8。

**核心发现**：Tutor 是 benchmark 最有效的区分信号。QR 几乎无区分度——两个模型的量化结果能力接近，差异在教学和过程质量上。

### 2.4 Persona 效应（Sonnet Original 数据）

来自 28 scored runs（旧版评估代码）的定性参考：

| Task | Beginner | Intermediate | Advanced | Δ(max-min) |
|------|----------|-------------|----------|-----------|
| S04 | 0.68 | 0.78 | 0.70 | 0.103 |
| S06 | **0.80** | 0.67 | 0.75 | 0.138 |

S06 的 beginner > intermediate 是反直觉的——可能因为 beginner 的简单问题让 agent 表现更好。Persona 间差异 0.02-0.14 证明 **persona 设计确实影响评分**。

⚠️ 注意：此数据使用旧版评估代码，与 ICC 实验（最新代码）的结果不可直接比较。

### 2.5 评估迭代校准（I01 old → latest）

| Version | OAS 范围 | Tutor 范围 |
|---------|---------|-----------|
| old1/old2 | 0.42-0.49 | 0.29-0.43 |
| latest | 0.71-0.79 | 0.85-0.89 |

旧版 Tutor=0.29 说明 judge prompt 存在严重偏差（对 agent 过于严苛），迭代修复后回到合理范围。这是论文中 "evaluation calibration" 的直接证据。

### 2.6 7D Reason 输出验证

已实施并验证：每个 Tutor 维度的评分现在附带 judge 的文字理由。

**验证结果**：
- 分数完全一致（reason 收集不影响评分逻辑）
- B02 run1 D6=0.2 的理由："assistant failed to answer the user's direct technical question" → 与 28min 超时 wrap-up 的事实一致
- 评分合理性 ✅：reasons 证实了评分的因果链

---

## 三、已完成的修复与改进

### 3.1 代码层面

| 修复项 | 文件 | 描述 |
|--------|------|------|
| Judge 温度 | config/llm_config.py, model_resolver.py | EVAL_USE_OAUTH=False, EVAL_JUDGE_TEMPERATURE=0.0 |
| Proxy 通用化 | mcp_proxy.py | 5 段工具名硬编码 → 4 个通用模式 |
| Code eval 规则 | orchestrator.py | requires_code=false 时跳过 code_eval |
| BetaToolRunner 崩溃恢复 | anthropic_adapter.py | 捕获 NoneType 异常，恢复已积累文本 |
| Trial 预算强制 | mcp_servers/core/tools.py | tm.can_run() 检查 |
| X09 trades key | X09_alpha_conflict.py | Total Trades → Total Orders fallback |
| Student code 路径 | 6 个 student_code 文件 | AAPL.csv → AAPL_2018_2024.csv |
| LEAN 辅助数据 | Docker mount | 从镜像提取 quote/margin/cfd 等数据 |
| X07-X10 任务重构 | 4 个 task JSON | 添加 trial 工具、TC、更新能力要求 |
| X01 TC 添加 | X01 task JSON | 添加 4 个 termination criteria |
| Debug 类别加入 TC | simulation.py | _INCREMENTAL_CHECKER_CATEGORIES 添加 "debug" |
| Debug judge 指引 | result_judge.py | 添加 debug 类任务的评分指导 |
| 7D reason 输出 | tutor_conv_geval.py, score_report.py | 收集并展示每维度评分理由 |
| context_management 修复 | anthropic_adapter.py | clear_thinking 必须在 edits 数组首位，value≥1 |

### 3.2 决策层面

| 决策 | 结论 | 理由 |
|------|------|------|
| B-02 Base adapter Anthropic 接口 | 不修改 | 仅影响 UI 展示，不影响评分 |
| B-03 Adapter 硬编码任务知识 | 论文声明即可 | 属于 harness 范畴 |
| B-05 Simulation Anthropic 感知 | 不修改 | null-safe，不影响评分 |
| E-01 关键词匹配脆弱性 | P3 论文声明 | 是能力边界非 bug |
| E-02 Behavioral Matching 容差 | 当前合理 | 2-bar tolerance 有工程理由 |
| E-04 解释正确性 | 已关闭 | Tutor 7D correctness 已覆盖 |
| S-02 学生锚定 | 当前 5 层锚定足够 | prompt 工程质量决定锚定质量 |
| S-05 Anchor LLM | 当前不引入 | 等更多数据后再评估 |
| S-07 DeepEval 黑盒 | 已审查源码 | 核心逻辑安全 |
| S-08 Persona 行为规则 | 用 per-persona 分数差异间接验证 | 直接验证成本过高 |
| Dampening 逻辑 | 不修改 | B02 run1 的低分是合理的 |

---

## 四、未解决的问题

### 4.1 高优先级（P0-P1，阻塞论文核心主张）

| # | 问题 | 当前状态 | 影响 | 建议行动 |
|---|------|---------|------|---------|
| 1 | **ICC 实验不完整** | Sonnet: I02(0 runs), X09(0 runs); Haiku: I02(1/3), X09(0/3) | 8 tasks 中仅 5 个有完整 ICC 数据 | 补跑缺失 runs |
| 2 | **人工校准未做** | 无数据 | 审稿人几乎必问 "LLM judge vs human agreement" | 30-50 样本 × 3 专家 ≈ 15h |
| 3 | **完整 benchmark 未跑** | 65 tasks × 3 personas × models 仅部分完成 | 论文主结果表缺数据 | 需要大量计算资源 |
| 4 | **B-04 Benchmark Spec 缺失** | 无 BENCHMARK_SPEC.md | 第三方无法集成 agent | 编写 Submission JSON Schema + Tool API 参考 |
| 5 | **B-06 论文 Section 3/4 边界** | 未写 | benchmark spec vs reference impl 需要清晰划分 | 写作 |

### 4.2 中优先级（P2，提升论文质量但不阻塞）

| # | 问题 | 当前状态 | 建议行动 |
|---|------|---------|---------|
| 6 | S-01 DeepEval 版本锁定 | requirements.txt 写 `>=3.8` 无上界 | Pin `deepeval==3.8.4` |
| 7 | E-03 Layer C 缺 reference 时硬零分 | 占 code_eval 50% 权重直接为 0 | renormalize（A:30% + B:70%） |
| 8 | E-06 Data Source Cap 过于严厉 | 乘法惩罚 | 改为固定 15% 折扣 |
| 9 | E-07 eval_completeness flag | 无法区分"表现差"与"评估缺失" | 添加 flag 到 TaskResult |
| 10 | S-03 TC Checker majority vote | 未评估 false positive 率 | 先跑数据看情况 |
| 11 | S-06 对话终止时机 | step_efficiency 未加 TC 覆盖率权重 | 讨论后实施 |
| 12 | E-05 执行验证层 | X 系列可加编译/运行 pass/fail | 补充 SWE-bench 风格验证 |

### 4.3 已知但暂不处理

| 问题 | 为什么不处理 |
|------|-------------|
| 关键词匹配 false positive/negative | 是评估能力的工程边界，非 bug。论文中声明即可 |
| Adapter 硬编码 token 阈值 | 属于 harness 工程，不影响 benchmark spec |
| Context management 按 task 动态调整 | 工程改进，当前阈值可接受 |
| 评估链误差传播分析 | 需要 sensitivity analysis，暂缺数据 |
| ~~跨模型 judge 一致性~~ | ✅ **已完成**（2026-04-07）。Sonnet + Haiku 双 judge 对比：QR r=0.872, Tutor r=0.676，排名 100% 一致。Sonnet judge 推荐为主 judge。详见 `judge_comparison_analysis.md` |

---

## 五、数据资产清单

| 数据集 | Runs | 模型 | 评估代码版本 | 可计算什么 |
|--------|------|------|-------------|-----------|
| **Sonnet ICC** | 24 (5 tasks × 3 runs + 3 tasks × 3 runs) | claude-sonnet-4-6 | ✅ 最新 | ICC, Cohen's d, CV, 维度独立性 |
| **Haiku ICC** | 24 (同上) | claude-haiku-4-5-20251001 | ✅ 最新 | 同上 |
| **Sonnet Original** | ~28 | claude-sonnet-4-6 | ⚠️ 旧版 | Persona effect, 评估迭代校准, 难度排名 |
| **GPT-5.2** | ~24 | gpt-5.2 | ⚠️ 旧版 | B-series 三模型对比 |
| **Student Determinism** | 72 calls | GPT-5.2 (student) | ✅ 最新 | 模拟器稳定性证明 |

**论文分层报告建议**：
- Table 1（主结果）：最新代码的 sonnet + haiku 对比（48 runs）
- Table 2（扩展）：sonnet original 的 persona effect（28 runs，标注旧版）
- Table 3（三模型）：B-series sonnet vs GPT-5.2 vs haiku（~30 runs，标注条件差异）
- 附录：评估迭代校准 narrative（I01 old vs latest）

---

## 六、框架合理性论证

### 6.1 评估有效性

| 维度 | 证据 | 结论 |
|------|------|------|
| **区分度** | Cohen's d=1.69（Tutor, sonnet judge），两个 judge 下排名 100% 一致 | Benchmark 能有效区分不同能力的模型 |
| **独立性** | QR-QP r=-0.09，QR-Tutor r=0.09 | 三维度测量不同能力，无冗余 |
| **稳定性（评估链）** | X01 CV=1.0%（agent 行为稳定时） | 评估链本身是确定性的 |
| **因果可追溯** | 7D reason 输出验证评分有清晰的文字理由 | 评分不是黑箱 |

### 6.2 可复现性

| 层面 | 措施 | 状态 |
|------|------|------|
| 数据 | HuggingFace 缓存 + 冻结 CSV 数据集 | ✅ |
| 环境 | Docker 隔离 + LEAN 固定 commit | ✅ |
| 评估 | Judge temp=0, TC checker temp=0 | ✅ |
| 框架 | DeepEval pin（建议 ==3.8.4） | ⬜ 待执行 |
| Agent | 不控制温度（设计决策） | ✅ 论文中声明 |
| 学生 | temp=0 但不保证文本确定性 | ⚠️ 已量化，论文中如实报告 |

**可复现性的诚实声明**：
- 完全确定性复现**不可能**（agent temp=1 + 学生 temp=0 仍有方差 + LLM API 固有非确定性）
- 统计层面可复现：模型排名在多次运行间稳定（Tutor 8/8 方向一致），且跨 judge 模型完全一致（两个独立 judge 下排名 100% 一致）
- 建议：n_runs=3，报告均值 ± 标准差

### 6.3 与标杆论文的对标

| 设计决策 | GAIA | SWE-bench | AgentBench | **QuantTutorBench** |
|----------|------|-----------|------------|---------------------|
| Benchmark/Harness 分离 | 完全 | 完全 | HTTP 解耦 | **API 层定义 + reference impl** |
| 评估自动化 | Exact match | 测试执行 | 环境判定 | **混合（programmatic + LLM judge）** |
| LLM Judge | 无 | 无 | 无 | **必要（对话质量无法 exact match）** |
| Partial Credit | 无 | 无 | 部分 | **有（多维度连续分）** |
| 模拟用户 | 无 | 无 | 无 | **有（独有挑战）** |
| 多维度评估 | 无 | 无 | 无 | **有（QR+QP+Tutor 7D）** |

**独有优势**：QuantTutorBench 是唯一同时具备"模拟用户交互 + 多维度过程评估 + 领域工具编排"的 benchmark。

---

## 七、论文中需要注意说明的内容

### 7.1 审稿人必问清单及应对

| 预期质疑 | 应对数据/策略 |
|----------|-------------|
| "学生模拟器跑两次结果一样吗？" | 24 点 × 3 重复的 determinism 实验 + ICC 数据 + CV 分层报告 |
| "LLM judge 和人类评分一致吗？" | ⚠️ **待做**：30-50 样本 human calibration。**已有间接证据**：双 judge 交叉验证（Pearson r=0.676-0.872），排名 100% 一致 |
| "第三方如何接入 agent？" | ⚠️ **待做**：BENCHMARK_SPEC.md |
| "关键词匹配的误判率？" | 论文声明 programmatic eval 的能力边界 + 与 LLM judge 互补 |
| "QR 和 QP 排名真的不同吗？" | ✅ r=-0.09，加四象限散点图 |
| "不同模型温度设置公平吗？" | ✅ 统一策略：agent 不控制，judge temp=0 |
| "Distractor tools 有区分度吗？" | 需要弱模型数据（当前顶级模型 distractor 调用率 < 2%） |
| "为什么不像 SWE-bench 用测试执行？" | 对话质量和教学维度无法用 exact match，LLM judge 是必要的 |

### 7.2 论文中应如实报告的限制

1. **学生非确定性**：temp=0 不保证文本确定性，学生是独立方差源（已量化）
2. **Agent 行为方差**：temp=1 下复杂任务（B02、I01）的 agent 行为差异导致 OAS ICC=0.06-0.32
3. **LLM Judge 依赖**：QP 5/7 维度、Tutor 7/7 维度依赖 LLM judge——需要 human calibration 支撑。双 judge 对比显示 Tutor r=0.676（最低跨 judge 一致性），但 agent 排名跨 judge 完全一致
4. **旧版数据条件差异**：Sonnet Original 和 GPT-5.2 数据使用旧版评估代码，与 ICC 实验不可严格比较
5. **DeepEval 框架依赖**：ConversationalGEval 是深耦合依赖（替换成本高），需要版本锁定
6. **任务类别边界模糊**：S 系列含回测 vs B 系列从头教回测（认知倒退）、B→I Python→LEAN 技能断层、X 系列 Python/LEAN 内部分裂
7. **Easy 任务天花板效应**：X01/S01/B01 上 sonnet 和 haiku 区分度 ≈ 0

### 7.3 建议的论文数据呈现

**Section 5.1 Main Results**：
- 跨模型对比表（sonnet vs haiku，8 tasks × 3 维度）
- 维度雷达图
- 难度曲线（easy vs medium 的 Δ 差异）

**Section 5.2 Multi-Dimensional Analysis**：
- QR-QP-Tutor 相关性矩阵热力图
- 四象限散点图（QR 高/QP 低 vs QR 低/QP 高的 case study）
- 维度区分度表（discrimination index）

**Section 5.3 Evaluation Reliability**：
- Per-task CV 表
- ICC 分层报告（按维度、按任务复杂度）
- 学生稳定性实验结果（一致性分布 + Jaccard 分析）
- Human calibration（⚠️ 待做）

---

## 八、系统亮点（论文应突出的技术贡献）

1. **多维评估体系**：QR + QP + Tutor 7D 三轴分离，已量化证明独立性和区分度
2. **增量 TC 覆盖追踪**：bitmap + 增量 LLM 检查，~97% token 节省
3. **功能性 Distractor Tools**：VaR、GARCH、Monte Carlo 等真实量化工具作为干扰项，测试工具选择判断力
4. **Trial System**：原子锁 + 快照 + 自动选优，模拟真实量化开发迭代流程
5. **2×2 实验矩阵**：分离工具贡献和 prompt 贡献
6. **Docker 沙箱 + LEAN 集成**：真实 C# 回测，非模拟
7. **完整自动化闭环**：从 `run-layer2` 到 `scores.md` 全链路无人工干预
8. **QR Blending with Divergence Dampening**：programmatic + code_eval + LLM judge 动态权重调整

---

## 九、调研支撑

### 9.1 直接对标论文（Related Work 必引）

| 论文 | 与我们的关系 |
|------|-------------|
| Finance Agent Benchmark (2508.00828) | 金融 benchmark 对比：它测知识 QA，我们测教学交互 |
| tau-bench (2406.12045) | 领域特定多轮交互标杆 |
| A Survey on LLM-as-a-Judge (2411.15594) | 评估方法论基础 |
| MCPAgentBench (2512.24565) | MCP 工具使用评估，与我们的 distractor 设计可对比 |
| PaperBench (2504.01848) | 细粒度检查点式评估参考 |
| SWE-bench (2310.06770) + SWE-agent (2405.15793) | benchmark/harness 分离范式 |
| AgentBench (2308.03688) | 多环境标准化评估 |
| TutorBench (2510.02663) | LLM tutoring benchmark：1490 STEM 对话，纯文本无代码执行无工具 |
| ISD-Agent-Bench (2602.10620) | 教学设计 agent 评估：multi-judge protocol 方法论参考 |
| FinToolBench (2603.08262) | 金融工具使用评估：760 可执行 API，capability/compliance 分离 |
| TraderBench (2603.00285) | 金融 agent + MCP + 对抗性市场环境 |
| Agent Psychometrics (2604.00594) | IRT 分析 benchmark 任务难度/区分度 |
| Unsafer in Many Turns (2602.13379) | 多轮工具使用 agent 安全评估，MAT 攻击分类法 |

### 9.2 填补的文献空白

目前没有 benchmark 同时覆盖：
- 垂直领域 agent + 工具编排 + 教学质量 + 过程评估

**一句话定位**："Domain-Specific Multi-Dimensional Evaluation Benchmark for LLM Agent Tutoring Systems"

### 9.3 2026 论文深度分析启示（2026-04-07 更新）

#### A. ISD-Agent-Bench 的 Multi-Judge 方法论 → 对我们的评估可靠性报告的启示

ISD-Agent-Bench 的 multi-judge protocol 核心设计：
- **跨供应商 judge**：OpenAI (GPT-4o-mini) + Google (Gemini-2.5-flash-lite) + Anthropic (Claude-3.5-Sonnet)，故意选不同供应商消除自偏好偏差
- **中位数聚合**（非平均值）：抗离群 judge 干扰
- **两阶段评估**：先分类判定（Absent/Poor/Satisfactory/Good/Excellent），再在有界区间内打数值分——防止 judge 先打分再找理由的不一致
- **可靠性指标**：ICC(2,k)=0.891, Krippendorff's α=0.823, Pearson r=0.847, 分歧率 8.3%

**对我们的启示**：
1. **跨供应商 judge**：我们当前用 2 个 eval 模型但可能来自同一供应商。应确保至少一个 judge 来自不同供应商（如一个 Claude、一个 GPT），并在论文中报告
2. **中位数聚合**：我们当前用平均值，中位数更鲁棒。可作为 ablation 实验
3. **两阶段评估**：我们的 7D tutor 用 10 分制直接打分，可考虑先分等级再在区间内打分来提升 judge 一致性
4. **可靠性指标**：论文中**必须报告** ICC 或 Krippendorff's α——这是 reviewer 必问的。ISD-Agent-Bench 的 0.891 ICC 是我们的对标线
5. **分歧解决机制**：当 judge 分差 >2 分时标记并用中位数，我们可以采用同样的 flagging 机制

#### B. FinToolBench → 与我们的框架对比及学习点

**核心差异**：

| 维度 | FinToolBench | QuantTutorBench |
|------|-------------|-----------------|
| 工具 | 760 个可执行真实金融 API（RapidAPI + AkShare） | ~15 个 MCP 工具（自建沙箱封装） |
| 任务 | 295 个（单轮问答，单/多工具） | 102 个（多轮教学对话，工具+代码+教学） |
| 交互模式 | **单轮**（一问一答，无对话） | **多轮**（tutor-student 10-30 轮） |
| 评估目标 | 工具调用正确性 + 金融合规性 | 教学质量 + 工具使用 + 领域知识 |
| 学生模拟 | 无 | 有（DeepEval ConversationSimulator） |
| 代码执行 | 无（只调 API） | 有（Docker 沙箱，LEAN C# 回测） |

**FinToolBench 的创新评估维度（值得学习）**：

Capability（能力）和 Compliance（合规性）分离是其核心贡献：
- **TMR（时效性失配率）**：工具的 update_frequency（实时/日度/静态）是否匹配问题需求。例如问"当前汇率"但用了日频快照——即使 API 调用成功也是失配。*我们没有此维度，但 D-series 涉及实时数据获取*
- **IMR（意图失配率）**：工具分 informational/advisory/transactional，agent 不能越权执行交易操作。**最高达 72%**——大多数模型在金融场景会越权。*我们的 A-series adversarial 评估了类似安全边界*
- **DMR（领域失配率）**：工具标注领域（equity/bond/forex/crypto），不能跨领域误用。*我们用 distractor tools 测类似能力*

**值得采纳的方法**：
1. **Trace-level 审计**：每个 tool call 单独审计合规性，一次违规标记整个任务。我们的 mcp_proxy 已记录完整 trace，可加 call-level 合规检查
2. **能力与合规分离报告**：不把正确性和安全性混在一个分数里。我们可以在 tool_usage 维度进一步拆为"用对工具"和"没有越权"
3. **Tool Card 设计**：给工具描述注入领域属性（时效性、意图、领域），让 agent 做 constraint-aware planning

**论文定位表述**："FinToolBench evaluates *what tools to call*; we evaluate *how to teach with tools*. They measure compliance at the API call level; we measure pedagogical effectiveness at the conversation level."

#### C. Agent Psychometrics → IRT 分析 benchmark 任务质量

**核心概念**：用 Item Response Theory（项目反应理论）的 1PL Rasch 模型分析 agent benchmark：
- `P(通过) = sigmoid(θ_agent − β_task)` — θ 是 agent 能力值，β 是任务难度值
- **LLM + Scaffold 分解**：`θ_agent = θ_LLM + θ_scaffold` — 把 agent 能力拆为模型本身推理能力和框架/工具链执行效率
- **不跑 agent 预测任务难度**：从任务描述提取特征（issue statement + repo context + solution + test cases），训练 ridge regression 预测 β，SWE-bench 上 AUC-ROC=0.842
- **自适应任务选择**：预算有限时用 Fisher information 选最有信息量的任务子集

**对我们的启示**：
1. **任务难度分布**：我们有 102 个任务，可用 IRT 拟合后画 "Task Difficulty Distribution" 图——如果 β 值聚集说明难度分布不够
2. **LLM 能力排名**：IRT 的 θ_LLM 比简单 pass rate 更有统计学意义，可用于跨模型比较
3. **天花板/地板效应检测**：如果某些任务的 β 极低（所有模型都过）或极高（所有模型都挂），说明任务区分度差——对应我们已发现的 "Easy 任务天花板效应"（X01/S01/B01）
4. **论文加分项**：在附录中加一个 IRT 分析，展示任务难度-区分度散点图。reviewer 对 benchmark 质量的统计验证非常看重

#### D. Unsafer in Many Turns → 多轮安全评估启示

**核心发现**：
- 多轮工具使用 agent 的 Attack Success Rate（ASR）比单轮平均**高 16%**
- Claude-4.5-Sonnet 最大增幅 +27.1%；Deepseek-v3.2 能力最强但安全最差（85.4% ASR）
- **MAT 攻击分类法**（Multi-Turn Attack Taxonomy）：Format（Addition/Decomposition）× Method（Mapping/Wrapping/Composition/Identity）× Target（Data/Environment）= 8 类攻击模式
- **ToolShield 防御**：无需训练，agent 自主生成安全测试用例 → 模拟执行 → 提取经验 → 部署时注入。Claude ASR 从 72% 降到 22%，且零误拒

**对我们的启示**：
1. **我们的 A-series 是静态单轮对抗**（17 个固定 prompt）。论文 Limitations 中应声明："our adversarial evaluation uses fixed single-turn prompts; multi-turn adaptive attacks (as shown by MT-AgentRisk, 2602.13379) may reveal additional vulnerabilities"
2. **MAT 分类法可扩展 A-series**：当前对抗任务是手动设计，MAT 提供系统性多轮攻击生成框架（如把"帮我买 AAPL"分解为多轮逐步引导）
3. **ToolShield 的"经验注入"**与我们的 system prompt SAFETY BOUNDARIES 类似——都是给 agent 预注入安全策略。可在 Discussion 中对比两种方法
4. **我们有 5 个 MCP 工具，他们也用 5 个 MCP 工具**（Filesystem/Browser/PostgreSQL/Notion/Terminal）——设计规模相当，但我们测教学安全，他们测系统安全

---

## 十、下一步行动优先级

```
P0（论文核心数据）：
  - 补跑 ICC 缺失 runs（I02, X09）
  - 完整 benchmark run（65 tasks × 3 personas × 2+ models）
  - Human calibration（30-50 样本）

P1（论文质量提升）：
  - BENCHMARK_SPEC.md 编写
  - DeepEval pin 版本 ==3.8.4
  - 论文 Section 3/4 边界写作
  - eval_completeness flag 实现

P2（改进但不阻塞）：
  - Layer C renormalize
  - Data Source Cap 改为固定折扣
  - TC Checker majority vote 评估
  - 弱模型数据收集（验证 distractor 区分度）

论文写作：
  - Section 3 Benchmark Design（定义 scope）
  - Section 5.3 Evaluation Reliability（ICC + student + human cal）
  - Section 6 Limitations（诚实声明已知限制）
```

---

*本文档综合了 v4.0 阶段全部 20+ 份调研/讨论文档、2 项独立实验（ICC + 学生稳定性）、50+ 次对话轮次的决策记录。对于历史未记载或不确定的内容（如完整 benchmark run 的预期时间、human calibration 的专家资源来源），文档如实标注为"待定"或"待做"，不做推测。*
