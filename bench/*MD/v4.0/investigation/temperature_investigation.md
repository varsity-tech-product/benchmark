# 温度控制深度调查报告

> 调查目标：彻底搞清楚系统中每一个 LLM 调用的温度设定，评估对 benchmark 可复现性和公平性的影响
> 调查结论：**系统存在严重但可修复的温度控制缺失，且不同组件的最优策略不同**

---

## 一、事实发现：全系统温度审计

### 1.1 Agent Adapters（被测对象）

| Adapter | API 调用位置 | temperature 参数 | 实际生效值 |
|---------|-------------|-----------------|-----------|
| Anthropic (Direct) | `anthropic_adapter.py:343` `runner_kwargs` | **未传** | Anthropic API 默认 = **1.0** |
| Anthropic (SDK) | ClaudeSDKClient 黑盒 | **不可控** | SDK 内部默认 |
| OpenAI (Direct) | `openai_adapter.py:280-285` `create_kwargs` | **未传** | OpenAI API 默认 = **1.0** |
| OpenAI (SDK) | Runner.run_sync() 黑盒 | **不可控** | SDK 内部默认 |
| Google | `google_adapter.py:78-81` `GenerateContentConfig` | **未传** | Gemini API 默认 = **1.0** |
| Generic | `generic_adapter.py:80-85` `create_kwargs` | **未传** | OpenAI-compatible 默认 = **1.0** |

**关键发现**：所有 agent adapter 的 API 调用均不传 temperature 参数。各 provider 的默认值均为 1.0（最大随机性）。

---

### 1.2 学生模拟器（ConversationSimulator）

| 组件 | 模型来源 | temperature | 实际生效值 |
|------|---------|-------------|-----------|
| 学生模拟器 | `simulation.py:687` → `resolve_deepeval_model("openai/gpt-5.2")` | 取决于路由 | **见下方分析** |
| TC Checker | `simulation.py:732` → `_resolve_checker_model("anthropic/claude-sonnet-4-6")` | 取决于路由 | **见下方分析** |
| 对话关闭生成 | `simulation.py:379` → `self.simulator_model.generate()` | 同学生模拟器 | 同上 |

**模型路由的温度影响（`model_resolver.py:166-231`）**：

```
resolve_deepeval_model(model_name)
├── Step 1: model starts with "anthropic/" + OAuth available
│   └── → _OAuthAnthropicModel (自定义类)
│       └── temperature: 未传给 API → Anthropic 默认 = 1.0 ❌
│
├── Step 2: OpenRouter key available
│   └── → GPTModel(model=..., api_key=..., base_url=openrouter)
│       └── temperature: GPTModel 默认 = 0.0 ✓（但这是个意外的好结果）
│
└── Step 3: Fallback
    └── → plain string → DeepEval 内部处理
        └── temperature: 取决于 DeepEval 的 initialize_model()
```

**关键发现**：
- **DeepEval 的 GPTModel 默认 temperature = 0.0**（不是 1.0！）
  - 源码：`deepeval/models/llms/openai_model.py:96-110` — 当 temperature 参数为 None 且 settings.TEMPERATURE 为 None 时，fallback 到 `0.0`
  - DeepEval 的 AnthropicModel 同理，默认也是 0.0
- **但 _OAuthAnthropicModel（我们的自定义类）完全绕过了 DeepEval 的温度系统**
  - `model_resolver.py:79-83`：API request body 中没有 temperature 字段
  - Anthropic API 在未传 temperature 时默认 = **1.0**

**结论**：学生模拟器的温度取决于模型路由路径：
- 如果走 OpenRouter（Step 2）→ GPTModel → temp=0.0 → **确定性** ✓
- 如果走 OAuth（Step 1）→ _OAuthAnthropicModel → temp=1.0 → **随机** ❌

当前配置：`SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"`，走 Step 2（OpenRouter），所以**学生模拟器实际 temp=0.0**。

但 TC Checker：`TC_CHECKER_MODEL = "anthropic/claude-sonnet-4-6"`，由于 `skip_oauth=True`（`simulation.py:62`），也走 Step 2（OpenRouter），所以 **TC Checker 实际也是 temp=0.0**。

---

### 1.3 LLM Judge（评估器）

| Judge 组件 | 模型来源 | temperature | 实际生效值 |
|-----------|---------|-------------|-----------|
| Result Judge | `result_judge.py:383-388` → `resolve_deepeval_model()` | 取决于路由 | 路由依赖 |
| Process Reasonableness | `process_reasonableness.py:145-152` → `resolve_deepeval_model()` | 取决于路由 | 路由依赖 |
| Custom Conv Metrics | `custom_conv_metrics.py:55-65` → `resolve_deepeval_model()` | 取决于路由 | 路由依赖 |
| Tutor GEval | DeepEval ConversationalGEval 内部 | `self.temperature` | GPTModel 默认 = 0.0 |
| Code Process (LLM 部分) | `code_process.py` → `resolve_deepeval_model()` | 取决于路由 | 路由依赖 |

**当前默认评估模型**：`EVAL_DEFAULT_MODELS = ["anthropic/claude-haiku-4-5-20251001"]`
- `EVAL_USE_OAUTH = True` → Step 1 匹配（anthropic/ 前缀 + OAuth）→ **_OAuthAnthropicModel → temp=1.0** ❌

**这是最严重的问题**：当前配置下，所有使用 `resolve_deepeval_model()` 的 judge 组件都走 OAuth 路径，使用 **_OAuthAnthropicModel**，该类**不传 temperature**，导致 Anthropic API 使用默认值 **1.0**。

**但如果 EVAL_USE_OAUTH = False**，则走 OpenRouter → GPTModel → temp=0.0 ✓。

---

### 1.4 独立测试脚本中的温度设置

| 文件 | 温度 | 用途 |
|------|------|------|
| `tests/tc_checker_batch.py:63` | **0.0** | TC checker 单元测试 |
| `tests/tc_checker_simulation.py:112` | **0.0** | TC checker 模拟测试 |
| `tests/tc_checker_trunc_grid.py:101` | **0.0** | TC checker 截断测试 |
| `tests/tc_checker_s456.py:85` | **0.0** | TC checker S4/5/6 测试 |
| `scripts/synthesize_tasks.py:262` | **0.7** | 任务生成（非评估流程） |

**讽刺发现**：所有独立测试脚本都显式设置了 temp=0.0，说明开发者知道 TC checker 需要确定性行为——但在实际 benchmark 流程中却遗漏了这一设置。

---

## 二、温度对各组件的影响分析

### 2.1 影响矩阵

| 组件 | 温度影响范围 | 当前 temp | 目标 temp | 影响严重度 |
|------|------------|----------|----------|-----------|
| **Agent** | 每次回复内容不同 → 对话轨迹不同 → 所有评分不同 | ~1.0 | 待讨论 | ⚠️ 需要论证 |
| **学生模拟器** | 每次提问不同 → 引导方向不同 → 对话轨迹不同 | 0.0（OpenRouter）| 0.0 | ✅ 已是最优（意外地） |
| **TC Checker** | 覆盖判定不同 → 对话长度不同 → step_efficiency 不同 | 0.0（OpenRouter）| 0.0 | ✅ 已是最优（意外地） |
| **LLM Judge** | 同一份对话评分不同 → 最终分数不同 | **1.0**（OAuth）| **0.0** | 🔴 **最严重** |
| **Tutor GEval** | 教学维度评分不同 | 0.0 | 0.0 | ✅ 已是最优 |

### 2.2 当前配置下的实际风险评估

**高风险**（需立即修复）：
- **LLM Judge (OAuth 路径) → temp=1.0**：同一份对话，两次评分可能差异显著。Result Judge 的 completeness/correctness 评分（各 1-10）可能波动 ±2 分，归一化后影响 ±0.22 的 QR 分数。这直接动摇了评估可信度。

**中风险**（需要论证决策）：
- **Agent → temp=1.0（provider 默认）**：这是一个设计决策而非 bug。

**低风险**（当前配置已是正确的）：
- 学生模拟器、TC Checker、Tutor GEval 走 OpenRouter → GPTModel → temp=0.0。但这是**路由路径的副作用**，不是显式设计——如果路由路径改变，温度也会改变。

---

## 三、核心矛盾与决策点

### 3.1 Agent 温度：控制 vs 放开？

**控制（temp=0）的论据**：
- AgentBench 使用 temp=0（greedy decoding），论文原话："to ensure reproducibility"
- 消除随机性 → 同一 task 只需跑 1 次
- 排名更稳定，统计显著性更容易达到
- 审稿人不会质疑 "你的结果稳定吗？"

**放开（temp=1.0 或不控制）的论据**：
- Agent 是被测对象，我们不应该替它做采样策略决策
- 不同 provider 的 temp=0 语义不完全一致：
  - OpenAI：temp=0 → greedy（严格确定性，但 API 已 deprecated 对 seed 的保证）
  - Anthropic：temp=0 → "nearly deterministic"（不保证完全确定性）
  - Google：temp=0 → greedy
- 策略研究任务（S 系列）需要创造性，temp=0 可能压制 agent 的创造力
- 真实产品场景中用户不会 temp=0

**推荐方案**：
```
Option A: temp=0 + single run（AgentBench 方案）
  优势：简单、低成本、可复现
  劣势：可能压制创造力；不同 provider 的 temp=0 不等价

Option B: temp=不控制 + 3 runs 取均值（GAIA 方案）
  优势：测试自然行为；统计学严谨
  劣势：3× 成本；需要报告 std dev

Option C: temp=0.3 + 3 runs（折中）
  优势：保留一定创造力；方差可控
  劣势：0.3 这个值缺乏理论依据

推荐：Option A（temp=0, single run）
理由：
1. AgentBench 的审稿人接受了这个设计
2. 成本可控（不需要 3×）
3. 论文中声明 "greedy decoding for reproducibility" 即可
4. 如果审稿人质疑，可以补充 3-run 实验作为 robustness check
```

### 3.2 LLM Judge 温度：必须修复

**无争议：Judge 必须 temp=0**。
- 这不是设计决策，这是 bug——评估器应该是确定性的
- 修复方式：在 `_OAuthAnthropicModel.a_generate()` 的 request body 中加入 `"temperature": 0.0`
- 或者：在 `resolve_deepeval_model()` 返回 GPTModel 时显式传 `temperature=0.0`

### 3.3 学生模拟器温度：确认当前行为是否稳定

**当前 temp=0.0（通过 OpenRouter → GPTModel 的默认值）是正确的**，但：
1. 这是路由的**副作用**，不是显式设计——应该在代码中显式设置
2. 如果将来把 SIMULATOR_DEFAULT_MODEL 改成 anthropic/ 开头的模型 + EVAL_USE_OAUTH=True，就会走 OAuth → temp=1.0
3. 需要在 `resolve_deepeval_model()` 中或 simulation.py 中显式传 temperature=0.0

**但 temp=0.0 的学生是否"不自然"？**
- temp=0 意味着学生每次跑都问完全相同的问题（在相同上下文下）
- 这对可复现性是好事，但对论文来说需要论证：固定学生提问序列下的评估是否有效
- 如果审稿人认为学生应该有变化 → 需要多 persona 来覆盖多样性，而非单个 persona 的随机变化

### 3.4 隐性温度依赖链

```
当前路由路径（EVAL_USE_OAUTH=True, SIMULATOR_DEFAULT_MODEL="openai/gpt-5.2"）:

学生模拟器:
  "openai/gpt-5.2" → 不是 "anthropic/" 开头 → 跳过 OAuth
  → OpenRouter key available → GPTModel(temp=0.0) ✓

TC Checker:
  "anthropic/claude-sonnet-4-6" → skip_oauth=True → 跳过 OAuth
  → OpenRouter key available → GPTModel(temp=0.0) ✓

LLM Judge:
  "anthropic/claude-haiku-4-5-20251001" → 是 "anthropic/" 开头 + OAuth available
  → _OAuthAnthropicModel(无 temp) → Anthropic API 默认 1.0 ❌
```

**这条隐性依赖链非常脆弱**——改变任一配置项都可能改变温度行为，而且不会有任何警告。

---

## 四、跨 Provider 温度语义差异

### 4.1 temp=0 的行为差异

| Provider | temp=0 行为 | 完全确定性？ | seed 参数支持 |
|----------|-----------|------------|-------------|
| OpenAI | Greedy decoding | **否**（已 deprecated deterministic guarantee）| 有（beta），近似确定性 |
| Anthropic | "Nearly deterministic" | **否** | **无** |
| Google (Gemini) | Greedy decoding | 未明确 | **无** |

**含义**：即使所有 provider 都设 temp=0，跨 provider 的结果仍不完全可复现。这是 benchmark 的固有限制，需要在论文中声明。

### 4.2 Anthropic Extended Thinking 与温度的关系

- `anthropic_adapter.py:387-391`：当 `ANTHROPIC_ENABLE_THINKING=True` 时，启用 extended thinking
- Anthropic 文档：**当 extended thinking 启用时，temperature 必须为 1.0**（API 会强制覆盖）
- **这意味着**：Anthropic agent 无论我们传什么温度，在 thinking 模式下都是 temp=1.0
- **公平性问题**：Anthropic agent 被迫 temp=1.0（因为 thinking），而 OpenAI agent 可以 temp=0。这不公平

**待决**：
- 如果统一 temp=0，Anthropic 必须关闭 extended thinking → 失去 COT 能力
- 如果允许 Anthropic 用 thinking（temp=1.0），其他 provider 也应该 temp=1.0
- 或者：接受这个差异，在论文中声明为 "provider-specific constraint"

---

## 五、决策共识（2026-04-06 讨论确认）

### 核心认知框架

Benchmark 的首要任务是证明**评估本身稳定**，而非消除 agent 的随机性：

```
Step 1: 固定 agent（同一模型、同一配置），跑 N 次
        → 证明 benchmark 评分方差小（评估稳定性）
        → "agent 行为有波动时，benchmark 仍能给出稳定评分"

Step 2: 换不同 agent（不同模型、不同 harness 配置）
        → 证明 benchmark 能区分不同 agent（区分度）
        → thinking on/off, 压缩策略等属于 harness 变量
```

### 决策 1：Agent 温度 — 不控制 ✅

**理由**：
- Agent 是被测对象，benchmark 不应替被测者做采样策略决策
- Agent 的随机性是真实世界的一部分
- 如果靠 temp=0 压制 agent 随机性来"伪造"稳定性，反而暴露 benchmark 脆弱性
- Thinking/reasoning 等功能属于 harness 范畴，应作为 Step 2 中 2×2 矩阵的维度来测试，而非在此控制
- 与 AgentBench temp=0 的区别：AgentBench 测裸模型能力（基础 ReAct），我们测 harness 化的 agent

### 决策 2：LLM Judge 温度 — 必须 temp=0 ✅

**修复方案**：`EVAL_USE_OAUTH=False`，让所有 judge 走 OpenRouter → GPTModel(temp=0.0)
- 这是最简单的修复路径，无需修改 _OAuthAnthropicModel
- 可额外在 `resolve_deepeval_model()` 中显式传 `temperature=0.0` 作为防御性编程
- OAuth 路径保留用于 agent 调用，不用于评估

### 决策 3：学生模拟器温度 — 后续讨论 ⏸️

**理由**：与 anchor LLM 设计、可复现性量化实验（ICC）关联紧密，适合在学生模拟器专题中统一处理

---

## 六、修复行动清单

| 行动 | 优先级 | 复杂度 |
|------|--------|--------|
| 设置 `EVAL_USE_OAUTH=False` 或在 judge 路径显式传 temp=0 | **P0** | 1 行配置 |
| 在 `resolve_deepeval_model()` 中增加 temperature 参数并显式传递 | **P1** | 约 10 行 |
| 在 `llm_config.py` 添加 `EVAL_JUDGE_TEMPERATURE = 0.0` 集中配置 | **P1** | 约 5 行 |
| Agent adapter **不改**——保持 provider 默认温度 | N/A | 无需改动 |

---

## 七、论文中的温度声明模板

```
Section 4.1 Experimental Setup:

"Agent models run with their provider's default sampling parameters
(typically temperature=1.0). We intentionally do not override agent
temperature: the benchmark evaluates agents as deployed, including
their inherent stochasticity. All evaluation judges use temperature=0
for deterministic scoring. To validate evaluation stability, we report
inter-run score variance across N=3 runs of the same agent on a
representative task subset (Section 5.5)."
```

---

## 八、验证实验设计

### 实验 1：评估稳定性（核心实验）
- 选 5-10 个 task，用同一 agent（默认温度）跑 3 次
- 计算 QR、QP、Tutor 各维度的 ICC（Intraclass Correlation）
- **目标**：ICC > 0.7 证明"agent 有波动，但 benchmark 评分稳定"
- 如果 ICC < 0.7 → benchmark 设计有问题，需要回头检查评估链

### 实验 2：Judge 确定性验证
- 取 10 份已有对话记录
- Judge temp=0 评分 3 次，确认分数完全一致（或仅有 API 层微小浮动）

### 实验 3：区分度验证（Step 2）
- 对比不同 harness 配置（thinking on/off）在同一模型上的得分差异
- 对比不同模型（如 sonnet vs haiku）的得分差异
- 计算 discrimination index

---

## 九、总结

| 发现 | 决策 | 状态 |
|------|------|------|
| Judge 通过 OAuth 走 temp=1.0 | 切换为 OpenRouter 或显式 temp=0 | 🔴 待修复 |
| Agent 全部 temp=1.0（默认值） | **不修改**——这是被测对象的自然行为 | ✅ 已决策 |
| 温度依赖路由路径（隐性脆弱） | 在 resolve 中显式传 temperature | ⚠️ P1 |
| Anthropic thinking 强制 temp=1.0 | 属于 harness 变量，作为实验维度 | ✅ 已决策 |
| 学生模拟器 temp | 后续学生模拟器专题中讨论 | ⏸️ 搁置 |
| TC Checker temp=0.0（已正确） | 需显式化，防止路由变化 | ⚠️ P2 |
