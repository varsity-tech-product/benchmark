# QuantTutorBench Gym 设计框架

> 日期：2026-04-08
> 分支：gym-spec (from ewan)
> 状态：已实现，live test 通过

---

## 零、考试类比：理解 Benchmark 的角色分工

整个 benchmark 可以用一场**标准化考试**来理解。这个类比不是修辞，而是设计决策的推导起点——每个组件的优先级和边界都从这里出发。

### 出题组组长（能力模型 / Task Taxonomy）

> "这次考试到底要考什么？"

在所有具体设计之前，首先需要定义：一个好的量化教学 agent 应该具备哪些能力，这些能力之间什么关系，权重怎么分配。

QuantTutorBench 的能力模型：
- **QR（量化结果）**：给出正确的数字和结论——对标"答案写对了"
- **QP（量化过程）**：分析路径合理、工具选择正确——对标"解题步骤规范"
- **Tutor（教学质量）**：能根据学生水平有效教学——对标"能不能讲清楚"

三个维度的独立性已经被实验验证（QR-Tutor r=0.19），这意味着它们确实在测不同的东西。如果只用 QR，Sonnet 和 Haiku 几乎不可区分（Cohen's d≈0）；加入 Tutor 后效应量跃升至 d=1.69。**能力模型决定了 benchmark 能看见什么。**

这是论文中 reviewer 最会 challenge 的地方——rubric、任务集、工具集都是从这个能力模型推导出来的。

### 考场（Eval 环境 / Docker 沙箱）

> "考场空调坏了影响发挥，考试结果就不可信。"

Docker 环境的确定性、工具接口的稳定性、回测引擎的一致性——这些是基础设施。如果同一个策略跑两次回测结果不同，后面所有评分都没意义。

对应到 gym：
- `ContainerManager` 保证每次 `reset()` 都是干净的沙箱
- MCP Proxy 保证工具接口行为一致
- LEAN 引擎通过 Docker 镜像锁定版本
- 网络默认关闭（`network_enabled: false`），消除外部依赖

**优先级最高但工作量未必最大**，因为工程问题边界清晰。

### 试题（任务设计 / 65 Tasks × 7 Categories）

> "试题决定了考试的区分度。"

太简单所有 agent 都满分，太难都零分，都没有信息量。任务集需要：
- 覆盖不同难度（easy / medium / hard）
- 覆盖不同量化主题（data_analysis / strategy / implementation / backtest / debug / e2e / adversarial）
- 任务之间不能换皮——不是十道题都在考"能不能跑回测"
- Persona 多样性是试题设计的一部分：beginner 和 advanced 学生会触发 agent 完全不同的能力维度

已发现的天花板效应：X01/S01/B01 上 Sonnet 和 Haiku 区分度 ≈ 0——说明 easy 任务对强模型没有信息量，这和真实考试一样。

### 草稿纸（工具集 / 25 Core + 21 Distractor）

> "草稿纸决定了考试测的是什么能力。"

15 个工具槽含 distractor，就像考试允许带计算器但混了几个没用的按钮——你想看的是 agent 能不能**识别正确的工具**并**合理使用**。

工具集的设计直接定义了 benchmark 的能力画像：
- 文件/系统工具 → 基本操作能力
- 数据处理工具 → 量化分析能力
- 回测工具 → 策略验证能力
- Distractor → 工具选择判断力（当前顶级模型 distractor 调用率 < 2%）

`tool_usage` 维度直接从 proxy 日志计算 expected/convenient/distractor 调用比，这是纯可观测的。

### 评分组（Judge + Rubric / QR+QP+Tutor 7D）

> "评分组和评分标准决定了分数的含金量。"

前面三个（考场、试题、草稿纸）做完之后基本稳定，但 **rubric 会随着看到越来越多的 agent 输出不断发现新的边界 case 需要 clarify**。这也是真实考试的规律——考场和试题考前就定了，但评分标准是改卷过程中不断细化的。

对应到 QuantTutorBench 的迭代历史：
- 旧版 Tutor=0.29（judge prompt 对 agent 过于严苛）→ 迭代后回到 0.85-0.89
- 发现 OAuth 路径导致 judge 实际跑在 temp=1.0 → 修复为 temp=0.0
- B02 run1 D6=0.2 的理由验证了评分因果链是正确的
- 双 judge 对比（Sonnet vs Haiku judge）确认 Tutor r=0.676 但排名 100% 一致

**评分链是需要持续投入最多的部分。**

### 考生（Agent / 被测对象）

考生自己决定怎么答题。考场不规定你先做哪道题、用不用草稿纸、打不打草稿。

这就是 gym 的核心理念——**agent 在外面，环境在里面**。

---

## 一、设计动机

### 1.1 问题：原架构不是 gym

原架构中，agent 被包在 orchestrator 内部：

```
Orchestrator.run_single_task()
  └─ ConversationSimulator.simulate()
       └─ model_callback(input) → agent.generate_response()  ← agent 在这里
```

这相当于**考官坐在考生旁边，替他翻卷子、递草稿纸、决定什么时候动笔**。考生只负责"接到题目后写一段文字"。这不是在考 agent 的编排能力，而是在考 LLM 在固定编排下的生成质量。

具体来说：
- **对话循环**由 orchestrator 控制——考官决定考试节奏
- **工具调用时机**被限定在 `generate_response()` 内部——考官决定什么时候允许用草稿纸
- **context 管理**被 adapter 层固定——考官替考生整理笔记
- **编排策略**被 harness 决定——考官替考生决定答题顺序

这本质上是 **callback 注入**，不是 gym。Agent 的设计空间被压缩到"在给定 context 下生成一段文本"。

### 1.2 目标：真正的 gym 环境

参照 OpenAI Gym / Gymnasium 的设计哲学，也符合考试的正确形态：

- 考生（Agent）在**外面**，考场（Environment）在**里面**
- 考生通过标准接口（`reset/step`）与考场交互
- 考生自己决定**何时做什么**——先看题、先打草稿、还是直接答题
- 考场只负责提供试题、执行操作、判断交卷、最后评分

---

## 二、架构设计

### 2.1 接口

```python
env = QuantTutorEnv(use_docker=True)
obs = env.reset("S01_ma_crossover")      # → 发卷：学生开场白 + 工具列表

while not obs.done:
    # 考生自己决定：先用草稿纸算，还是直接回答
    result = env.call_tool("fetch_market_data", symbol="AAPL")  # 用草稿纸
    result = env.call_tool("compute_indicator", ...)             # 继续算
    obs = env.send_message("Here's what I found...")             # 写上答卷

scores = env.evaluate()                   # → 改卷
env.close()                               # → 交卷离场
```

三个关键方法的角色：

| 方法 | 考试类比 | 效果 |
|------|---------|------|
| `call_tool()` | 用草稿纸 / 计算器 | 执行工具，**不推进对话**（学生看不见） |
| `send_message()` | 在答卷上写字 | 推进对话（学生看到并回应 + TC 检查） |
| `evaluate()` | 考试结束后改卷 | 运行评分链，返回 Scores |

### 2.2 与 OpenAI Gym 的类比

| Gym 概念 | QuantTutorBench 对应 | 考试类比 |
|----------|---------------------|---------|
| `env.reset()` | 创建沙箱 + 返回学生开场白 | 发卷 + 考试开始 |
| `env.step(action)` | `call_tool()` + `send_message()` | 用草稿纸 + 写答案 |
| `observation` | `Observation(student_message, tools, done, turn)` | 学生的追问 + 剩余时间 |
| `reward` | `Scores(overall, QR, QP, tutor)` — 延迟到 `evaluate()` | 最终成绩单 |
| `done` | `obs.done` — 由 TC / max_turns / timeout 决定 | 考试时间到 / 答完交卷 |

与经典 Gym 的主要区别：**action 不是离散/连续空间**，而是两种操作（工具调用 + 文本消息）。reward 不是每步给，而是对话结束后一次性评估——这更接近 episode-level evaluation，和真实考试的评分方式一致。

### 2.3 分层架构

```
┌─────────────────────────────────────────┐
│  考生 Agent（完全外部，用户控制）          │
│  - 自己管理 context window（自己整理笔记） │
│  - 自己决定调工具还是回复学生（答题策略）  │
│  - 任何架构：单 LLM / multi-agent / RAG   │
└────────────┬────────────────────────────┘
             │ reset() / call_tool() / send_message() / evaluate()
┌────────────▼────────────────────────────┐
│  考场 QuantTutorEnv (bench/gym/env.py)  │
│  ├─ Docker 沙箱 (考场设施)              │
│  ├─ MCP Proxy (草稿纸 + 计算器)         │
│  ├─ Student Simulator (出题 + 追问)      │
│  ├─ TC Checker (监考：判断是否答完)       │
│  └─ Evaluation Chain (评分组)            │
└─────────────────────────────────────────┘
             │ 复用
┌────────────▼────────────────────────────┐
│  基础设施 Existing Infrastructure        │
│  - orchestrator/container_manager.py     │
│  - mcp_servers/registry.py + proxy.py    │
│  - evaluation/scoring.py + metrics       │
│  - config/prompt_config.py               │
└─────────────────────────────────────────┘
```

---

## 三、关键设计决策

### 3.1 为什么 `call_tool` 和 `send_message` 分开？

这对应考试中"打草稿"和"写答案"的区别。

在原架构中，工具调用和文本回复是耦合的——相当于要求考生在答卷上写出每一步计算过程。分开后：

- Agent 可以**先调多个工具探索**，再决定怎么跟学生说（先打草稿再答题）
- Agent 可以**不调工具**直接回复（直接写答案）
- Agent 可以**调工具但不回复**（纯探索，不暴露给学生——只打草稿）
- 工具调用的数量和顺序完全由 agent 控制

这最大化了设计空间，也更接近真实教学场景——老师可以先备课（调工具），再上课（回复学生）。

### 3.2 为什么不用 DeepEval ConversationSimulator？

原架构依赖 DeepEval 的 `ConversationSimulator` 驱动对话循环。问题：

1. Simulator 控制循环 → agent 变成 callback（考官替考生翻卷子）
2. Simulator 的 `simulate()` 方法是黑盒 → 无法让 agent 在中间插入工具调用
3. 强依赖 DeepEval → 第三方必须安装 DeepEval 才能跑

Gym 的解决方案：
- `student_sim.py`：直接调 OpenRouter API 生成学生消息（零 DeepEval 依赖）
- `tc_checker.py`：从 `simulation.py` 提取增量 TC 检查逻辑
- DeepEval 只在 `evaluate()` 中使用（Tutor 7D 评分需要 ConversationalGEval）

**核心循环不依赖 DeepEval，只有评分链依赖。** 用考试类比：考试进行过程不需要评分组在场，评分是考完之后的事。

### 3.3 为什么 evaluation 复用 orchestrator？

评分链（QR + QP + Tutor 7D）非常复杂：
- QR：programmatic eval script + code eval（3 层）+ LLM judge，三者融合 + divergence dampening
- QP：7 维度并行 LLM 调用
- Tutor：7D × 3 shuffle × multi-model = 21+ judge calls

重写不现实，也没必要。Gym 的 `evaluate()` 直接调用 `orchestrator._evaluate_task()`，传入对话记录和工具日志。

这是合理的——**评分标准属于考试规范，不属于考场设施**。无论考生在哪个考场考试，用的是同一套评分标准。评分链属于 benchmark specification，不属于 harness。

### 3.4 懒加载策略

```python
from bench.gym import Observation, Scores    # ← 零依赖，立刻可用
from bench.gym import QuantTutorEnv          # ← 首次访问时才导入 orchestrator + deepeval
```

`types.py`（Observation, Scores）是纯 dataclass，无外部依赖。`env.py` 的重型导入全部延迟到方法调用时。这意味着：
- 第三方可以 `from bench.gym import Observation` 来定义接口类型，不需要安装 deepeval
- 只有真正跑 `env.reset()` 时才需要完整环境

类比：你可以先看考试大纲（types），不需要走进考场（env）。

---

## 四、Gym vs Harness：考场 vs 辅导班

### 4.1 角色区分

**Gym 是考场**——提供标准化环境，任何考生都可以进来考试。
**Harness 是辅导班**——我们自己用来训练和测试 baseline 的工具。

| | Gym（考场） | Harness（辅导班） |
|---|---|---|
| **用途** | 第三方 agent 评估 | 复现 baseline / 跑实验 |
| **入口** | `QuantTutorEnv` | `run_benchmark.py` |
| **对话循环** | Agent 控制（考生自己答题） | Orchestrator 控制（老师带着做） |
| **工具调用** | `env.call_tool()` | Adapter 内部通过 proxy |
| **Context 管理** | Agent 自己做 | Adapter 层拼接 |
| **学生模拟** | `student_sim.py`（直接 LLM） | DeepEval ConversationSimulator |
| **TC 检查** | `tc_checker.py`（独立） | `_EfficientSimulator`（嵌入 DeepEval） |
| **评分** | `env.evaluate()` | `orchestrator._evaluate_task()` |

### 4.2 论文中的定位

```
Section 3（Benchmark Specification）— 考试规范
  - 能力模型（QR × QP × Tutor）         ← 出题组组长
  - 任务定义（65 tasks × JSON Schema）    ← 试题
  - 工具 API 规范（25 core + 21 distractor）← 草稿纸
  - 评估公式（OAS = 0.70 × QAI + 0.30 × TEI）← 评分标准
  - Gym 环境接口                          ← 考场规则

Section 4（Reference Implementation）— 辅导班
  - Harness（orchestrator + adapters）    ← 用于复现 baseline
  - Gym（bench/gym/）                    ← 第三方接入点
  - Docker 沙箱 + LEAN 集成              ← 考场设施

Section 5（Experiments）— 考试成绩
  - Baseline 结果用 harness 跑
  - 但所有数据都可以用 gym 复现
```

### 4.3 优先级推导

从考试类比直接推导出工作优先级：

```
P0 考场设施（已完成）:
  Docker 沙箱确定性、工具接口稳定性、LEAN 回测一致性
  → 如果考场空调坏了，后面一切都没意义

P0 试题 + 评分标准（持续迭代中）:
  任务区分度验证、rubric 校准、human calibration
  → 这是改卷过程中不断细化的部分

P1 考试规范文档（BENCHMARK_SPEC.md，已完成初版）:
  让第三方知道怎么参加考试
  → 没有准考证说明，考生进不了考场

P1 Gym 接口（已完成）:
  标准化的考场入口
  → 考场建好了但没有门，考生还是进不来

P2 辅导班优化（Harness）:
  冻结功能，只修 bug
  → 辅导班的教学方法不影响考试公平性
```

---

## 五、实现细节

### 5.1 文件清单

```
bench/gym/
├── __init__.py       (32 行)   懒加载，types 零依赖导出
├── types.py          (54 行)   Observation + Scores dataclass
├── env.py            (560 行)  QuantTutorEnv 核心环境
├── student_sim.py    (145 行)  直接 LLM 调用生成学生消息
├── tc_checker.py     (263 行)  增量 TC 检查（从 simulation.py 提取）
└── example_agent.py  (194 行)  Echo agent + OpenAI agent 示例

BENCHMARK_SPEC.md     (682 行)  完整规范文档
```

总计 1930 行。对 orchestrator 零修改（只修了 `evaluate()` 里 `populate_eval_results` 的一个参数遗漏）。

### 5.2 Student Simulator 实现

不用 DeepEval 的 ConversationSimulator，直接调 OpenRouter API：

- System prompt 使用现有的 `build_user_description()` + `build_scenario()` 构建（和 harness 完全一致的 persona/scenario 质量）
- 角色映射：student → assistant, tutor → user（让 LLM 生成"学生的下一条消息"）
- 默认 temp=0.0，model=GPT-5.2（与 harness 一致）
- 对话结束时单独调用 `generate_closing()` 生成自然收尾

### 5.3 TC Checker 实现

从 `simulation.py` 的 `_EfficientSimulator` 提取核心逻辑：

- 维护 TC 覆盖位图 `covered: list[bool]`
- 每次 `send_message()` 后调用 `check(conversation)` 
- 三轮检查策略（head → tail → code blocks）处理长消息
- 直接调 OpenRouter API（不依赖 DeepEval 模型对象）
- 返回覆盖率摘要供 `obs.info` 使用

---

## 六、Live Test 验证

2026-04-08 在 `gym-spec` 分支上验证通过：

| 测试项 | 结果 | 说明 |
|--------|------|------|
| 轻量导入 (types) | ✅ | 无 deepeval 依赖 |
| `QuantTutorEnv` 实例化 | ✅ | `use_docker=False` |
| `reset()` 加载 task/persona/tools | ✅ | 15 tools（5 core + 10 distractor） |
| `call_tool()` 执行 + proxy 日志 | ✅ | 3 次调用（get_env_info, file_list, file_read） |
| `send_message()` → 学生 LLM 回复 | ✅ | 3 轮对话，学生上下文相关 |
| `evaluate()` → QR + QP + Tutor | ✅ | OAS=0.286, 21 judge calls, 117s |
| Context manager (`with`) | ✅ | 正确 close |
| 错误路径 | ✅ | 未 reset / done 后调用 / 无效 task |
| TC 解析 | ✅ | `parse_tc_items()` 正确提取编号项 |

---

## 七、后续工作

### 已完成
- [x] Gym 环境实现 + live test
- [x] BENCHMARK_SPEC.md 完整规范
- [x] 从 DeepEval 解耦核心循环
- [x] 考试类比框架文档

### 待做
- [ ] Docker 模式 live test（当前只测了 `use_docker=False`）
- [ ] LEAN 任务（I-series）验证
- [ ] 多任务顺序 `reset()` 验证（状态清理）
- [ ] TC checker 实际触发终止验证（需要 strategy/implementation 类任务）
- [ ] Harness 的 adapter 层迁移到 gym 接口（可选，不阻塞论文）
- [ ] 性能对比：gym 模式 vs harness 模式的评分一致性
