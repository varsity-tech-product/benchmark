# 学生模拟器深度调查报告

> 调查范围：S-01 ~ S-08，覆盖 DeepEval 框架依赖、锚定机制、TC checker、可复现性、对话终止
> 核心发现：系统的锚定设计层次丰富，但**所有约束都是 prompt 层软约束**，无代码级硬保证

---

## 学生模拟器架构总览

```
DeepEval ConversationSimulator.simulate()
  ├── 每轮循环：
  │   ├── stop_conversation()          ← 我们的 _EfficientSimulator 覆写
  │   ├── DeepEval template → 学生 LLM → 学生消息
  │   ├── model_callback(学生消息)     ← 我们的回调，内部调 agent_adapter
  │   └── 累积 Turn 到 turns list
  └── 返回 ConversationalTestCase

注入点：
  golden.user_description  ← build_user_description()：persona + 情感 + 行为规则
  golden.scenario          ← build_scenario()：任务 + 学习目标 + PACING/COVERAGE
  _EfficientSimulator      ← TC bitmap + 增量 checker + 关闭消息
```

---

## S-01：DeepEval 框架锁定

### 依赖清单

| 依赖类 | 用途 | 可替代性 |
|--------|------|---------|
| `ConversationSimulator` | 学生模拟循环 | 高成本（需重写模拟逻辑） |
| `ConversationalGolden` | 传入 scenario + user_description | 低成本（简单数据容器） |
| `Turn` | role + content 数据结构 | 极低（用 dict 即可） |
| `ConversationalTestCase` | 返回类型 | 极低 |

### DeepEval 内部行为

**`simulate()` 核心循环**（`conversation_simulator.py:196-293`）：

1. 调用 DeepEval 模板生成学生消息（template.py:54-102）
2. 模板将 `golden.user_description` + `golden.scenario` + 完整对话历史注入 student LLM prompt
3. **无 system prompt**——DeepEval 只发 user message
4. **无历史截断**——完整 turns 序列化为 JSON 传入，长对话可能撞 context limit
5. **无温度控制**——DeepEval 不暴露温度参数给 `simulate()` 调用者

### DeepEval 的 student prompt 模板

**首轮**（template.py:18-51）：
```
Pretend you are a user of an LLM app. Your goal is to start a conversation...
User Profile: "[user_description]"
Scenario: "[scenario]"
JSON Output: {"simulated_input": "..."}
```

**后续轮**（template.py:54-102）：
```
Continue the conversation as the user...
Previous conversation: [全部历史 JSON]
User Profile: "[user_description]"
Scenario: "[scenario]"
JSON Output: {"simulated_input": "..."}
```

**关键发现**：DeepEval 的模板是**通用的**（"Pretend you are a user of an LLM app"），我们的领域特化全部通过 user_description 和 scenario 注入。这意味着我们的锚定质量**完全取决于 prompt 工程**，DeepEval 本身只是个循环调度器。

### 版本风险

- `requirements.txt` 写 `deepeval>=3.8`（无上界）
- DeepEval 升级可能改变模板措辞 → 学生行为变化 → 结果不可复现
- **建议**：pin 到 `deepeval==3.8.4`

### 结论

DeepEval 的实际价值是**循环调度 + JSON 输出解析**。锚定质量来自我们的 prompt，不来自框架。如果未来需要更多控制（如温度、seed、历史截断），自研 simulator 的成本不高——核心逻辑约 100 行。

---

## S-02：学生锚定机制

### 5 层锚定体系

| 层级 | 注入位置 | 内容 | 约束强度 |
|------|---------|------|---------|
| **L1: Persona** | user_description | 知识水平 + 情感特征 + 行为规则 | 中（LLM 可能忽略） |
| **L2: Scenario** | scenario | 任务描述 + 学习目标 + 开场白 | 高（定义了对话主题） |
| **L3: Pacing** | scenario 内嵌 | "不要一次问完所有目标" | 低（纯文本指令） |
| **L4: Coverage** | scenario 内嵌 | "3 轮后切换到下一个目标" | 低（纯文本指令） |
| **L5: TC Checker** | _EfficientSimulator | TC bitmap + 增量 LLM 检查 | 高（代码级终止控制） |

### Pacing 和 Coverage 的实际效力

**PACING 指令**（prompt_config.py:729-735）：
```
Do NOT ask about all goals at once. Start with the opening message only.
After the tutor addresses one topic, naturally transition to the next...
```

**COVERAGE TRACKING 指令**（prompt_config.py:756-763）：
```
After 3 consecutive follow-up turns on the same goal, transition to
the next uncovered goal...
```

**问题**：这些都是**对 student LLM 的请求**，不是代码级保证。学生 LLM 可能：
- 在第 1 轮就列出全部学习目标
- 在一个目标上纠缠 5+ 轮不切换
- 忽略 "3 consecutive turns" 规则

**但**：从实际测试观察（sonnet 4.6 / gpt 5.2 作为学生），这些指令在大多数情况下被遵循。问题更多出现在弱模型上。

### 固定 vs 可变的元素

| 元素 | 每次运行 | 可控性 |
|------|---------|--------|
| 开场白 | **固定**（task.student_openings[persona_id]） | 完全可控 |
| Persona 描述 | **固定** | 完全可控 |
| 学习目标列表 | **固定** | 完全可控 |
| TC items | **固定** | 完全可控 |
| 后续提问 | **不固定**（LLM 生成） | 仅 prompt 约束 |
| 提问顺序 | **不固定** | 仅 PACING 约束 |
| 情感反应 | **不固定** | 仅 emotional_profile 约束 |

---

## S-03：TC Checker 可靠性

### 增量检查机制

```
每轮（exchange = 1 student + 1 tutor）：
  ├── Gate 1: n_exchanges >= 1?
  ├── Gate 2: 新 exchange 发生?
  └── _incremental_check(turns)：
      ├── Pass 1: head（最近 2 条消息的前 3000 字符）
      ├── Pass 2: tail（如果消息超长，最后 3000 字符）
      └── Pass 3: code blocks（如果有未覆盖的含 "code" 的 TC）
          → 提取代码块 ± 200 字符上下文
```

### TC Checker Prompt

```
You are tracking a tutoring session's progress against specific learning objectives.

Current status:
  1. [COVERED] Showed C# code for SMA(20)...
  2. [NOT COVERED] Presented backtest results...
  3. [NOT COVERED] Showed trade log entries...

Latest exchange:
[最近 1 个 exchange 的 JSON]

Which NOT-YET-COVERED items (if any) were demonstrated with
computational evidence (actual numbers, code execution, or
concrete analysis) in this exchange?
Return ONLY a JSON object: {"newly_covered": [1, 3]} or {"newly_covered": []}
```

### 误判风险

**False Positive（过早标记 covered）**：
- TC item："Presented backtest results with specific numerical values"
- Tutor 说 "The backtest will show you Sharpe ratio around 1.5"（预测性描述，非实际结果）
- Checker 可能判定 "specific numerical values" 已出现 → 标记 COVERED
- **后果**：如果这是最后一个未覆盖项 → `all(self._covered)=True` → 对话立即结束

**False Negative（漏标 covered）**：
- 3000 字符截断可能切掉关键数值输出
- 代码块提取只在 TC 含 "code" 关键词时触发——TC 描述为 "showed implementation" 而非 "showed code" 时，Pass 3 不执行
- **后果**：对话继续，浪费 turns，但不会错误终止

**影响不对称**：False Positive 导致对话过早结束（严重），False Negative 导致对话过长（可容忍）。

### 当前缓解措施

- "Computational evidence" 的要求提高了误判阈值——纯口头描述不应被标记
- 3-pass 策略覆盖长消息的头/尾/代码块
- 失败时 graceful fallback（返回空列表，不覆盖任何 TC）

### 建议

- **TC checker 温度已确认 temp=0**（走 OpenRouter → GPTModel 默认 0.0）
- 可考虑 **majority vote**（3 次 check，2/3 通过才标记）→ 降低 false positive
  - 代价：3× checker 成本（但每次 ~1400 tokens，成本可控）
  - 优先级 P2，先跑 ICC 实验看当前 false positive 率再决定

---

## S-04：可复现性

### 变异源分析

| 变异源 | 影响 | 是否可控 |
|--------|------|---------|
| 学生 LLM 采样（student questions） | 高——不同提问 → 不同对话轨迹 | 仅 temp 控制（当前 temp=0.0 via OpenRouter） |
| Agent LLM 采样（tutor responses） | 高——不同回答 → 不同后续 | 不控制（设计决策） |
| TC Checker 判定 | 中——不同覆盖判定 → 不同对话长度 | temp=0.0 |
| DeepEval 模板生成 | 低——相同 prompt 下输出相似 | 无 seed |

### 关键发现：学生 temp=0.0 意味着**近似确定性**

当前配置：`SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"` → OpenRouter → GPTModel(temp=0.0)

在 temp=0.0 下，给定相同的 user_description + scenario + 对话历史，student LLM 应该产出相同的消息。这意味着：
- **如果 agent 也是确定性的**（temp=0），则整条对话链确定性
- **如果 agent 不确定**（我们的设计决策：不控制），则 agent 的第一个不同回答会导致后续对话分叉

### seed 字段：声明了但未使用

`schemas.py:97-98`：`seed: Optional[int] = None`
- 用在 `orchestrator.py:335-339` 传给 `create_proxy_for_task()`（distractor 选择的种子）
- **未传给 student simulator**——DeepEval `ConversationSimulator` 不接受 seed 参数

### 需要的验证实验

选 5-10 个 task，同一 agent 跑 3 次，计算：
1. **对话文本相似度**：student 提问序列的 BLEU / edit distance
2. **评分方差**：QR / QP / Tutor 的 CV（变异系数）
3. **ICC**（Intraclass Correlation）：> 0.7 才可接受

如果 temp=0 下 student 确定 + agent 不确定 → 方差主要来自 agent → benchmark 评分应该反映 agent 的自然波动 → 这是可接受的

---

## S-05：Anchor LLM 必要性

### 现状 vs Anchor LLM 方案

**现状**：
```
TC Coverage Bitmap → TC Checker（判定覆盖）→ 更新 bitmap
学生 LLM → 根据 PACING/COVERAGE prompt 自行决定下一个问题
```
两个系统**解耦运行**——TC Checker 不影响学生的提问方向，学生也不知道 TC bitmap 的状态。

**Anchor LLM 方案**：
```
TC Coverage Bitmap → Anchor LLM → "下一个问题应覆盖 TC #3"
→ 注入学生 LLM prompt → 学生提出关于 TC #3 的问题
```

### 分析

**Anchor LLM 的优势**：
- 保证所有 TC 被覆盖（不依赖学生 LLM 的"自觉性"）
- 提问顺序可控但措辞自然
- 减少浪费 turns

**Anchor LLM 的问题**：
- 增加一次 LLM 调用/轮 → 成本 + 延迟
- 可能使学生行为过于机械——"按清单提问"
- 本质上等于变相 scripted questions

**替代方案：Rule-Based Anchor（更简单）**：
```python
# 伪代码
uncovered = [i for i, c in enumerate(self._covered) if not c]
if len(uncovered) > 0 and n_exchanges > 3:
    # 在 scenario 中追加提示
    hint = f"Your next question should explore: {tc_items[uncovered[0]]}"
    # 注入 golden.scenario 的末尾
```

**问题**：DeepEval 的 `ConversationalGolden` 在 `simulate()` 开始时就固定了——**运行时无法修改 scenario**。要实现 rule-based anchor，必须绕过 DeepEval 的模板系统。

### 建议

- **当前不引入 Anchor LLM**——等 ICC 实验数据（S-04）出来后，如果 TC 覆盖率足够高（>90%），不需要额外机制
- 如果 TC 覆盖率低——优先考虑 **增加 PACING 指令的强度**（措辞更强硬），而非引入新的 LLM 层
- 长远如果自研 simulator（替代 DeepEval），可以在循环内动态修改 student prompt

---

## S-06：对话终止时机

### 6 种终止条件

| 条件 | 触发点 | 影响评分 |
|------|--------|---------|
| TC 全覆盖 | `_EfficientSimulator.stop_conversation()` | 正面——正常结束 |
| max_turns | `simulate()` 循环上限 | 负面——step_efficiency 惩罚 |
| 超时 | `model_callback` 中 deadline 检查 | 负面——对话不完整 |
| 重复检测 | 2 次连续相同 agent 回复 | 负面——异常终止 |
| 用户取消 | web UI cancel_event | 中性——人为干预 |
| Expected outcome（原生 DeepEval） | 非 incremental 类别 | 视情况 |

### TC False Positive → 过早终止的影响链

```
TC Checker 误判 item #4 已覆盖
→ all(self._covered) = True
→ _generate_closing() 生成告别消息
→ turns.append(Turn(role="user", content=closing))
→ stop_conversation() returns True
→ simulate() 循环结束
→ 对话缺少 item #4 的实际覆盖
→ QR 评分：test_script 可能检测到缺失内容 → 扣分
→ QP 评分：step_efficiency 看起来"高效"（少 turns）→ 虚高
→ Tutor 评分：7D 中的 Completeness 可能检测到缺失 → 扣分
```

**矛盾**：QP 的 step_efficiency 会奖励"对话短"——如果对话因为 false positive 而提前结束，agent 反而在效率维度得到高分。这是一个评估设计问题。

### 建议

在 step_efficiency 计算中引入 **TC 覆盖率加权**：
```
adjusted_efficiency = raw_efficiency × tc_coverage_rate
```
如果只覆盖了 3/4 的 TC → efficiency × 0.75。这样 false positive 导致的提前结束不会获得效率奖励。

---

## S-07：DeepEval 黑盒行为

### 已确认的内部行为

| 行为 | 详情 | 风险 |
|------|------|------|
| 无 system prompt | DeepEval 只发 user message 给 student LLM | 低——我们的锚定全在 user_description 里 |
| 无历史截断 | 完整 turns JSON 传入 | 中——长对话可能撞 context limit |
| 无温度控制 | 框架不暴露温度参数 | 低——我们通过 GPTModel(temp=0) 在创建时控制 |
| JSON 输出解析 | 期望 `{"simulated_input": "..."}` | 低——有 fallback |
| 异常吞没 | 内部 catch Exception → 返回空 turns | 中——可能丢失 agent 工作 |

### 长对话 context 溢出风险

DeepEval 将完整对话历史传给 student LLM。假设：
- 平均每轮 student 300 tokens + tutor 800 tokens = 1100 tokens/轮
- 30 轮 = 33,000 tokens 对话历史
- 加上 user_description (~2000 tokens) + scenario (~1500 tokens) = ~36,500 tokens
- GPT-5.2 context = 128K → 安全
- **但如果 tutor 回复非常长**（包含代码、回测结果等，单次 3000+ tokens）→ 30 轮可能 >100K tokens

**当前缓解**：max_turns 通常 15-30，且 TC checker 倾向于在 8-15 轮结束对话。实际风险低。

---

## S-08：Persona 行为规则可执行性

### 规则执行验证：当前不存在

24 条行为规则全部作为 prompt 指令注入，**没有任何 post-hoc 验证**机制。

### 规则遵循率的推测

| Persona | 规则类型 | 遵循难度 | 推测遵循率 |
|---------|---------|---------|-----------|
| Beginner (5 条) | 简单情感反应 | 低 | ~80% |
| Intermediate (9 条) | 行为偏好 | 中 | ~70% |
| Advanced (11 条) | 方法论批判 | 高 | ~50% |

**高遵循**：`"Show excitement when code runs successfully"` → LLM 容易做到
**低遵循**：`"Question statistical rigor: 'What is the p-value threshold?'"` → LLM 可能跳过或简化

### 影响

行为规则的目的是让 **Tutor 面对不同难度的学生**。如果 advanced persona 的规则被忽略：
- Tutor 面对的不是"挑战性的高级用户"，而是"温和的提问者"
- Tutor 7D 的 `adaptiveness` 维度无法有效测量
- 不同 persona 间的得分差异缩小 → adaptiveness_score (AS) 区分度降低

### 建议

- **短期**：不引入 post-hoc 验证（成本高，且不影响核心评估流程）
- **论文中**：报告不同 persona 下的 Tutor 评分差异作为间接证据——如果差异显著，说明 persona 规则在某种程度上生效
- **长期**：如果 adaptiveness_score 区分度不足，考虑更强的行为注入（如 few-shot examples 而非纯指令）

---

## 综合评估矩阵

| 问题 | 核心风险 | 优先级 | 行动 |
|------|---------|--------|------|
| **S-01** DeepEval 锁定 | 框架升级破坏兼容性 | **P2** | Pin 版本 `deepeval==3.8.4`；评估自研 simulator 的 ROI |
| **S-02** 锚定机制 | 全部是 prompt 软约束 | **P3 论文声明** | 足够丰富，论文中描述分层体系 |
| **S-03** TC Checker | False positive → 对话过早结束 | **P2** | 考虑 majority vote；先跑数据看 false positive 率 |
| **S-04** 可复现性 | 无 ICC 数据支撑 | **P0 实验** | 跑 3 次复现性实验，计算 ICC |
| **S-05** Anchor LLM | Pacing 指令可能被忽略 | **P3** | 等 ICC 数据；当前不引入新 LLM 层 |
| **S-06** 终止时机 | step_efficiency 与 TC 覆盖率脱钩 | **P2** | 在 efficiency 中加 TC 覆盖率权重 |
| **S-07** DeepEval 黑盒 | 长对话 context 溢出 | **P3** | 当前 max_turns 安全，监控 |
| **S-08** 行为规则 | Advanced persona 遵循率低 | **P3** | 论文中用 per-persona 分数差异间接验证 |

### 论文中必须讨论的 S 类问题

1. **S-02**：描述 5 层锚定体系——这是论文的 contribution 之一
2. **S-03**：描述增量 TC checker 的设计（97% token 节省）和 "computational evidence" 标准
3. **S-04**：报告 ICC 数据——benchmark 可信度的核心证据
4. **S-08**：报告 per-persona 得分差异——证明 persona 设计有效
