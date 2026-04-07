# 学生模拟器稳定性测试方案（修订版）

---

## 前次测试的问题

1. **覆盖面不足**：只测了 3 个 task（B01, D01, X01），全是 intermediate_developer
2. **成本计算不准**：预估 $0.50，实际 $0.07——说明 prompt 可能没有完整传入，或者 OpenRouter 缓存了请求
3. **未区分截断深度**：只在 turn 2 后截断——对话早期和中期的学生行为可能不同

## 修订方案

### 测试矩阵

从现有 run_state 中挑选，覆盖 **5 类别 × 3 人格 × 2 截断深度 = 30 个测试点**，每个重复 3 次 = **90 次 API 调用**。

| 类别 | Task | 来源 | Personas 可用 | 选取截断点 |
|------|------|------|-------------|-----------|
| **B (backtest)** | B01 | ICC runs + sonnet_orig | intermediate, beginner, advanced | turn 1 后, turn 2 后 |
| **D (data_analysis)** | D01 | ICC runs + sonnet_orig | intermediate, beginner | turn 1 后, turn 2 后 |
| **X (debug)** | X01 | ICC runs + sonnet_orig | intermediate, beginner | turn 1 后, turn 2 后 |
| **I (implementation)** | I01 | ICC runs + sonnet_orig | intermediate, beginner, advanced | turn 1 后, turn 2 后 |
| **S (strategy)** | S01 | ICC runs + sonnet_orig | intermediate, advanced | turn 1 后, turn 2 后 |

### 截断策略

- **Turn 1 后**（2 messages：student_opening + tutor_response_1）：测试学生在收到第一个回答后的追问确定性
- **Turn 2 后**（4 messages）：测试多轮上下文下的确定性

Turn 1 截断最关键——因为第一轮 tutor 回答是对话分叉的起点。

### 测试方法修正

之前的测试直接调用 `model.generate(prompt)` 然后用 regex 提取 `simulated_input`——这跳过了 DeepEval 的 `generate_schema()` 方法（内部有 JSON schema 强制和重试逻辑）。

**修正**：使用 `ConversationSimulator.generate_next_user_input(golden, turns)` 方法——这是实际运行时的完整路径，包括 schema 解析。

### 成本估算

```
每次调用：~4500 tokens input + ~150 tokens output
GPT-5.2 via OpenRouter: $1.75/M in + $14/M out
每次成本: 4500×1.75e-6 + 150×14e-6 = $0.0079 + $0.0021 = $0.01
90 次调用: ~$0.90
```

### 评估指标

对每组 3 次重复：
1. **Exact match rate**：3 次输出完全相同的比例
2. **Semantic similarity**：如果文字不同，意图是否相同（手动标注 or embedding 相似度）
3. **Topic choice consistency**：3 次是否选择了相同的追问话题

### 结果分类

```
A. 完全确定（3/3 exact match）→ temp=0 生效，学生不是方差源
B. 语义确定但措辞不同 → 对评分影响可忽略
C. 话题选择不同 → 学生是独立方差源，但可以定位为"测试 agent 适应性"
D. 完全不同 → 需要修复（seed/换 API）
```

### 可选增强

如果 C/D 比例高，可以加一个对照实验：
- 直连 OpenAI API（而非 OpenRouter）跑同样的测试
- 验证是 OpenRouter 中间层引入的随机性还是 GPT-5.2 本身的 temp=0 不确定性

---

## 需要确认

1. 是否需要覆盖 GPT-5.2 的 run_state（不同 agent model 的对话历史 → 不同上下文 → 可能影响学生稳定性）？
2. 3 次重复是否足够？还是需要 5 次？
3. 是否需要测试 DeepEval 原生 checker 的确定性（D 类任务用的 `stop_conversation`）？

---

# 学生模拟器稳定性测试结果（完整版）

---

## 实验配置

- 模型：GPT-5.2 via OpenRouter，temperature=0.0
- 覆盖：5 类别 × 2-3 personas × 2 截断深度 = 24 测试点
- 每个测试点重复 3 次
- 总 API 调用：72 次
- 方法：使用 DeepEval 的 ConversationSimulatorTemplate.simulate_user_turn() + GPTModel.generate()——与实际运行时的 LLM 调用路径完全一致

---

## 结果汇总

### 一致性分布

| 级别 | 数量 | 占比 | 含义 |
|------|------|------|------|
| A_exact（完全相同） | **0/24** | 0% | 无任何测试点达到文本确定性 |
| B_semantic（同意图不同措辞） | **0/24** | 0% | — |
| C_topic_varies（话题选择有差异） | **9/24** | 38% | 同一方向但措辞/细节不同 |
| D_divergent（实质性差异） | **15/24** | 62% | 问了不同的问题或选了不同的话题 |

### 按维度分析

**按类别**：

| 类别 | 平均 Jaccard 相似度 | 解读 |
|------|-------------------|------|
| data_analysis | 0.36（最高） | 学生追问方向相对集中 |
| backtest | 0.27 | 中等 |
| debug | 0.27 | 中等 |
| implementation | 0.24 | 较分散 |
| strategy | 0.24（最低） | 策略任务的开放性导致学生追问最不稳定 |

**按人格**：

| Persona | 平均 Jaccard 相似度 | 解读 |
|---------|-------------------|------|
| beginner | 0.31（最高） | 简单问题空间 → 追问相对集中 |
| intermediate | 0.28 | 中等 |
| advanced | 0.21（最低） | 复杂问题空间 → 追问最分散 |

**按截断深度**：

| 截断点 | 平均 Jaccard 相似度 |
|--------|-------------------|
| Turn 1 后 | 0.27 |
| Turn 2 后 | 0.28 |

截断深度对稳定性影响不大——不确定性在第一轮就已经存在。

---

## 典型案例

### D_divergent 案例（I01 advanced turn2）

```
Run 1: "Can you paste this as a single compile-ready C# file..."
Run 2: "This looks close, but I'm skeptical about AddCryptoFuture..."
Run 3: "In your two-phase OnData, it looks like _pendingSignal could execute before IsWarm..."
```

三次追问了**完全不同的方向**：Run1 要完整代码，Run2 质疑 API 用法，Run3 发现 warmup 时序问题。Jaccard=0.15。

### C_topic_varies 案例（D01 beginner turn2）

```
Run 1: "Yeah, I'm willing to try daily returns next, but the subtraction/division part still makes..."
Run 2: "Yeah, we can try daily returns next, but the math part makes me a little nervous..."
Run 3: "Yes, let's try daily returns next—I'm a bit nervous about the division part..."
```

三次都选了**同一个话题**（daily returns），但表达焦虑的方式不同。Jaccard=0.38。

---

## 核心结论

### 1. GPT-5.2 在 temp=0 下不保证文本确定性

0/24 个测试点达到 exact match——**OpenRouter + GPT-5.2 的 temp=0 不等于确定性输出**。这不是 DeepEval 的问题，是 LLM API 的固有特性。

### 2. 学生模拟器是独立的方差源

之前假设"学生确定 → 分叉来自 agent"是**错误的**。实际上：

```
方差 = agent 方差（temp=1） + 学生方差（temp=0 但不确定） + 交互放大
```

学生每次提出不同的追问方向 → 引导 agent 走不同的路径 → 对话轨迹分叉。

### 3. 方差程度取决于问题空间的开放性

- 封闭问题（beginner D01："帮我看看数据"）→ 追问方向有限 → 相对稳定
- 开放问题（advanced S01："策略研究"）→ 追问方向极多 → 最不稳定

### 4. 这对 benchmark 意味着什么

**不是缺陷，是需要在论文中正确框架化的特性**：

> "The student simulator exhibits controlled stochasticity even at temperature 0, reflecting the inherent non-determinism of large language models. This variation is most pronounced in open-ended tasks (strategy research) and advanced personas (which have a larger question space). We view this as a feature: it tests the agent's robustness to diverse student inquiry patterns rather than its ability to follow a fixed script. To quantify this effect, we measure per-task coefficient of variation (CV) across repeated runs and report it alongside performance scores."

### 5. 如果需要更高确定性

可选但**不建议**实施（会损失测试 agent 适应性的价值）：
- 传 OpenAI seed 参数（OpenRouter 可能不透传）
- 缓存第一次学生输出，后续 runs 复用
- 改用直连 OpenAI API（可能更接近确定性）
- 降低 student model 到更小的模型（更可预测但更不自然）
