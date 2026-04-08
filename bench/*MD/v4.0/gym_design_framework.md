# QuantTutorBench Gym 设计框架

> 日期：2026-04-08
> 分支：gym-spec (from ewan)
> 状态：已实现，live test 通过

---

## 一、设计动机

### 1.1 问题：原架构不是 gym

原来的 benchmark 架构中，agent 被包在 orchestrator 内部：

```
Orchestrator.run_single_task()
  └─ ConversationSimulator.simulate()
       └─ model_callback(input) → agent.generate_response()  ← agent 在这里
```

第三方只能实现一个 `respond()` 函数注入到 orchestrator 的 callback 位置。这意味着：
- **对话循环**由 orchestrator 控制，不是 agent
- **工具调用时机**被限定在 `generate_response()` 内部
- **context 管理**被 adapter 层固定
- **编排策略**（什么时候调工具、什么时候回复学生）被 harness 决定

这本质上是 **callback 注入**，不是 gym。Agent 的设计空间被压缩到"在给定 context 下生成一段文本"。

### 1.2 目标：真正的 gym 环境

参照 OpenAI Gym / Gymnasium 的设计哲学：

- Agent 在**外面**，Environment 在**里面**
- Agent 通过标准接口（`reset/step`）与环境交互
- Agent 自己决定**何时做什么**——编排策略完全开放
- Environment 只负责执行动作、返回观察、判断终止

---

## 二、架构设计

### 2.1 接口

```python
env = QuantTutorEnv(use_docker=True)
obs = env.reset("S01_ma_crossover")      # → Observation

while not obs.done:
    result = env.call_tool("fetch_market_data", symbol="AAPL")  # → str
    result = env.call_tool("compute_indicator", ...)             # → str
    obs = env.send_message("Here's what I found...")             # → Observation

scores = env.evaluate()                   # → Scores
env.close()
```

三个关键方法：
- `call_tool(name, **kwargs) → str`：执行工具，**不推进对话**
- `send_message(text) → Observation`：发送回复，**推进对话**（触发学生回复 + TC 检查）
- `evaluate() → Scores`：对话结束后运行评分链

### 2.2 与 OpenAI Gym 的类比

| Gym 概念 | QuantTutorBench 对应 |
|----------|---------------------|
| `env.reset()` | 创建沙箱 + 返回学生开场白 |
| `env.step(action)` | `call_tool()` (环境交互) + `send_message()` (对话推进) |
| `observation` | `Observation(student_message, tools, done, turn)` |
| `reward` | `Scores(overall, QR, QP, tutor)` — 延迟到 `evaluate()` |
| `done` | `obs.done` — 由 TC checker / max_turns / timeout 决定 |
| `info` | `obs.info` — 终止原因、TC 覆盖状态等 |

与经典 Gym 的主要区别：**action 不是离散/连续空间**，而是两种操作（工具调用 + 文本消息）。reward 不是每步给，而是对话结束后一次性评估。这更接近 episode-level evaluation。

### 2.3 分层架构

```
┌─────────────────────────────────────────┐
│  Agent（完全外部，用户控制）              │
│  - 自己管理 context window               │
│  - 自己决定调工具还是回复学生             │
│  - 可以是任何架构：单 LLM / multi-agent / RAG │
└────────────┬────────────────────────────┘
             │ reset() / call_tool() / send_message() / evaluate()
┌────────────▼────────────────────────────┐
│  QuantTutorEnv (bench/gym/env.py)       │
│  ├─ Docker 沙箱 (ContainerManager)      │
│  ├─ MCP Proxy (工具注册 + 日志)          │
│  ├─ Student Simulator (直接 LLM 调用)    │
│  ├─ TC Checker (增量终止判定)            │
│  └─ Evaluation Chain (QR+QP+Tutor 7D)   │
└─────────────────────────────────────────┘
             │ 复用
┌────────────▼────────────────────────────┐
│  Existing Infrastructure (不修改)        │
│  - orchestrator/container_manager.py     │
│  - mcp_servers/registry.py + proxy.py    │
│  - evaluation/scoring.py + metrics       │
│  - config/prompt_config.py               │
└─────────────────────────────────────────┘
```

---

## 三、关键设计决策

### 3.1 为什么 `call_tool` 和 `send_message` 分开？

在原架构中，工具调用和文本回复是耦合的——都在 `generate_response()` 内部发生。分开后：

- Agent 可以**先调多个工具探索**，再决定怎么跟学生说
- Agent 可以**不调工具**直接回复（纯对话教学）
- Agent 可以**调工具但不回复**（纯探索，不暴露给学生）
- 工具调用的数量和顺序完全由 agent 控制

这最大化了设计空间，也更接近真实教学场景——老师可以先备课（调工具），再上课（回复学生）。

### 3.2 为什么不用 DeepEval ConversationSimulator？

原架构依赖 DeepEval 的 `ConversationSimulator` 驱动对话循环。问题：

1. Simulator 控制循环 → agent 变成 callback
2. Simulator 的 `simulate()` 方法是黑盒 → 无法让 agent 在中间插入工具调用
3. 强依赖 DeepEval → 第三方必须安装 DeepEval 才能跑

Gym 的解决方案：
- `student_sim.py`：直接调 OpenRouter API 生成学生消息（零 DeepEval 依赖）
- `tc_checker.py`：从 `simulation.py` 提取增量 TC 检查逻辑
- DeepEval 只在 `evaluate()` 中使用（Tutor 7D 评分需要 ConversationalGEval）

**核心循环不依赖 DeepEval，只有评分链依赖。**

### 3.3 为什么 evaluation 复用 orchestrator？

评分链（QR + QP + Tutor 7D）非常复杂：
- QR：programmatic eval script + code eval（3 层）+ LLM judge，三者融合 + divergence dampening
- QP：7 维度并行 LLM 调用
- Tutor：7D × 3 shuffle × multi-model = 21+ judge calls

重写不现实，也没必要。Gym 的 `evaluate()` 直接调用 `orchestrator._evaluate_task()`，传入对话记录和工具日志。这是合理的——评分逻辑属于 benchmark specification，不属于 harness。

### 3.4 懒加载策略

```python
from bench.gym import Observation, Scores    # ← 零依赖，立刻可用
from bench.gym import QuantTutorEnv          # ← 首次访问时才导入 orchestrator + deepeval
```

`types.py` (Observation, Scores) 是纯 dataclass，无外部依赖。`env.py` 的重型导入（orchestrator, deepeval, config）全部延迟到方法调用时。这意味着：
- 第三方可以 `from bench.gym import Observation` 来定义接口类型，不需要安装 deepeval
- 只有真正跑 `env.reset()` 时才需要完整环境

---

## 四、Gym vs Harness 分离

### 4.1 两种使用模式

| | Gym 模式 | Harness 模式 |
|---|---|---|
| **用途** | 第三方 agent 评估 | 复现 baseline / 跑实验 |
| **入口** | `QuantTutorEnv` | `run_benchmark.py` |
| **对话循环** | Agent 控制 | Orchestrator 控制 |
| **工具调用** | `env.call_tool()` | Adapter 内部通过 proxy |
| **Context 管理** | Agent 自己做 | Adapter 层拼接 |
| **学生模拟** | `student_sim.py` (直接 LLM) | DeepEval ConversationSimulator |
| **TC 检查** | `tc_checker.py` (独立) | `_EfficientSimulator` (嵌入 DeepEval) |
| **评分** | `env.evaluate()` | `orchestrator._evaluate_task()` |
| **底层** | 复用 container_manager + proxy + evaluation | 完整 orchestrator |

### 4.2 论文中的定位

```
论文 Section 3（Benchmark Specification）:
  - 任务定义（65 tasks × JSON Schema）
  - 工具 API 规范（25 core + 21 distractor）
  - 评估公式（OAS = 0.70 × QAI + 0.30 × TEI）
  - Gym 环境接口（reset / call_tool / send_message / evaluate）

论文 Section 4（Reference Implementation）:
  - Harness（orchestrator + adapters）→ 用于复现 baseline
  - Gym（bench/gym/）→ 第三方接入点
  - Docker 沙箱 + LEAN 集成

论文 Section 5（Experiments）:
  - Baseline 结果用 harness 跑
  - 但所有数据都可以用 gym 复现
```

---

## 五、文件清单

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

### 待做
- [ ] Docker 模式 live test（当前只测了 `use_docker=False`）
- [ ] LEAN 任务（I-series）验证
- [ ] 多任务顺序 `reset()` 验证（状态清理）
- [ ] TC checker 实际触发终止验证（需要 strategy/implementation 类任务）
- [ ] Harness 的 adapter 层迁移到 gym 接口（可选，不阻塞论文）
- [ ] 性能对比：gym 模式 vs harness 模式的评分一致性
