# QuantTutorBench API 分离方案（方向 A）

> 2026-04-08 | 用户使用我们的基础设施（MCP + Docker + Student），只替换 agent

---

## 一、核心思路

用户替换的**只是 agent**。其他一切（Docker 沙箱、MCP 工具代理、学生模拟器、评估流水线）由我们提供。

用户体验目标：

```python
from quanttutorbench import Benchmark, BaseAgent

class MyAgent(BaseAgent):
    def generate_response(self, messages, tools, tool_callback):
        # 用户的 LLM + agent loop
        return response_text

bench = Benchmark(docker=True)
results = bench.run(agent=MyAgent(), tasks=["D01", "B01"])
print(results.scores)
```

---

## 二、调研结论

### 2.1 Messages 历史管理

**现状**：存在两层对话管理。

**层 1（simulation.py）**：维护 `conversation_history`（纯文本 `[{role, content}]`），每次调用 `agent.generate_response(messages=list(conversation_history))` 传完整历史副本。

**层 2（Anthropic adapter）**：内部维护 `_input_history`（Anthropic 富格式，包含 tool_use/tool_result/thinking content blocks）。从传入的 messages 中只提取最新 user message（`extract_latest_user_message`），忽略完整历史。因为 Anthropic BetaToolRunner 需要结构化历史才能正确工作。

**为什么不冲突**：两套状态服务不同目的——
- simulation.py 的 conversation_history → 用于评估（传给 Tutor 7D、Result Judge 等）
- adapter 的 _input_history → 用于 agent 推理（Anthropic API 需要的富格式）

**对用户 agent 的含义**：用户的 `generate_response` 收到的是层 1 的纯文本 messages。这足够大多数 agent 使用。如果用户的 agent 需要富格式历史（如直接调 Anthropic SDK 需要 tool_use blocks），用户需要在 `generate_response` 内部自行维护——和我们的 Anthropic adapter 做法一致。

**决策**：不改架构。messages 传完整纯文本历史，用户可以选择用全部历史还是只看最新消息。文档中说明 messages 的语义。

### 2.2 Tool Loop 管理

**现状**：

- 任务 JSON 定义 `agent_max_steps`（默认 10），通过 `agent.set_agent_max_steps(n)` 传给 adapter
- Anthropic adapter：作为 BetaToolRunner 的 `max_iterations` 参数
- OpenAI adapter：作为 Runner 的 `max_turns` 参数
- Google adapter：硬编码 `max_iterations=5`

**决策**：Tool loop 管理是**用户自己的责任**。

理由：
1. 用户提供自己的 API key，loop 费用由用户承担
2. 不同 agent 架构的 loop 机制差异巨大，我们无法统一管理
3. `agent_max_steps` 作为 benchmark spec 的**建议值**提供给用户，不强制执行

我们在 MCP Proxy 层**自动记录**每个 turn 的工具调用次数。如果用户的 agent 在单个 turn 内调用了远超 agent_max_steps 的工具次数，这会在 QP 的 step_efficiency 评分中自然体现（action economy 分数降低）。不需要强制中断。

`BaseAgent` 保留 `on_task_start(task_context)` 接口，在 task_context 中包含 `agent_max_steps` 的值，供用户参考。

### 2.3 异步支持

**决策**：保持同步 `tool_callback`，不提供 async 版本。

理由：
1. 当前同步接口没有功能缺陷——Anthropic BetaToolRunner 在 SDK 内部已实现并行 tool execution
2. 大多数 agent framework（LangChain, AutoGPT, CrewAI）的 tool 接口也是同步的
3. 异步接口增加用户的实现复杂度，对绝大多数场景没有好处
4. 如果未来有需求，可以在不破坏兼容性的前提下添加 `async_tool_callback` 可选参数

"工具有副作用"不是接口问题——`file_write` 写文件、`run_lean_backtest` 消耗 trial budget，这些是工具的正常行为，通过 tool description 告知用户即可。

### 2.4 BaseAgent 功能范围

**核心原则**：`generate_response` 是唯一必须实现的方法。评分所需的一切数据（conversation、tool_logs、workspace_files）由我们的基础设施自动采集，不依赖用户的可选接口。可选接口只影响**报告丰富度**，不影响**评分准确性**。

**关于 thinking**：用户的 agent 可能有 thinking/COT 输出。这不影响评分（Tutor 7D 只看对话内容），但影响 Web 报告的展示。如果用户提供 `get_thinking_trace()`，trace 报告和 Web UI 可以展示 thinking 块。否则只显示纯文本。

**关于工具管理**：完全由我们的基础设施处理。tool schema 通过 `available_tools` 传入，tool 执行通过 `tool_callback` 代理，tool logging 由 MCP Proxy 自动完成。用户的 agent 不需要做任何工具管理。

**关于上下文管理**：compaction、history truncation 是用户 agent 内部的工程决策。如果对话太长导致上下文溢出，用户自己处理。我们不提供也不限制。

---

## 三、System Prompt 策略

**决策**：System prompt 由 benchmark 注入，用户不可修改。

`TUTOR_SYSTEM_PROMPT`（65 行）定义了"好 tutor 应该怎么做"——教学方式、安全边界、对话风格。这是**评分标准的一部分**：Tutor 7D 的 rubric 要求 agent 遵循这些行为规范。用户修改 prompt 等于修改考题。

实现方式：benchmark 在调用 `generate_response` 前，将 system prompt + task context 注入 `messages[0]`：

```python
messages = [
    {"role": "system", "content": TUTOR_SYSTEM_PROMPT + "\n\n" + task_context},
    {"role": "user", "content": "学生第一条消息"},
    ...
]
```

用户的 agent 可以有自己的内部 prompt（在 `generate_response` 里追加），但必须尊重 messages[0] 的角色设定。

---

## 四、BaseAgent 接口定义

```python
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    """QuantTutorBench agent 接口。

    用户只需实现 generate_response。其余方法为可选增强接口，
    影响报告丰富度但不影响评分准确性。
    """

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: callable,
    ) -> str:
        """核心方法。必须实现。

        一次调用 = 一个对话 turn。在此函数内部，agent 可以多次
        调用 tool_callback（即 agent 内部的 tool loop）。函数返回
        时，返回值作为 agent 给学生的文本回复。

        Args:
            messages: 对话历史。
                messages[0]: {"role": "system", "content": benchmark_prompt + task_context}
                messages[1:]: {"role": "user"|"assistant", "content": str}
                每次调用包含完整历史。
            available_tools: 可用工具列表，JSON Schema 格式。
                [{"name": str, "description": str, "parameters": {param: {type, description, required}}}]
                每个 turn 的 available_tools 相同（15 个工具）。
            tool_callback: 工具执行函数。
                tool_callback(tool_name: str, **kwargs) -> str
                返回工具执行结果的文本。失败时返回 "Error: ..." 开头字符串。
                每次调用由 MCP Proxy 自动记录（name, args, result, duration, success）。

        Returns:
            Agent 给学生的文本回复。
        """
        raise NotImplementedError

    # ── 可选增强接口 ──

    def on_task_start(self, task_context: str):
        """任务开始前调用。

        task_context 包含：任务描述、学生 persona、agent_max_steps 等。
        用户可在此初始化内部状态、设置 system prompt 等。
        默认无操作。
        """
        pass

    def on_task_end(self):
        """任务结束后调用。清理内部状态（对话历史、缓存等）。默认无操作。"""
        pass

    def get_token_usage(self) -> dict | None:
        """返回累计 token 使用量。可选。

        Returns:
            {"input_tokens": int, "output_tokens": int, "cost_usd": float,
             "model": str, "api_calls": int}
            不实现 → cost 报告 Agent 行显示 "unknown"
        """
        return None

    def get_thinking_trace(self) -> list[dict]:
        """返回 thinking/COT 记录。可选。

        Returns:
            [{"turn_index": int, "thinking": str}]
            不实现 → trace 报告不显示 thinking 块
        """
        return []

    def get_content_blocks(self) -> dict[int, list[dict]]:
        """返回结构化内容块。可选。

        Returns:
            {turn_index: [{"type": "thinking"|"text"|"tool_use"|"tool_result", "text": str}]}
            不实现 → Web UI 只显示纯文本对话
        """
        return {}
```

### 报告降级策略

| 用户实现的方法 | scores 报告 | cost 报告 | trace 报告 | Web UI |
|--------------|-----------|---------|----------|--------|
| 只有 `generate_response` | ✅ 完整（评分不受影响） | Agent 行 "unknown" | 纯文本对话 + 工具日志 | 基础版 |
| + `get_token_usage` | ✅ | ✅ 完整 | 同上 | + 费用仪表盘 |
| + `get_thinking_trace` | ✅ | ✅ | + thinking 块展示 | + COT 折叠面板 |
| + `get_content_blocks` | ✅ | ✅ | + 结构化内容 | + 富文本时间线 |

**评分数据来源**（全部由基础设施自动采集，不依赖用户）：

| 评分需要的数据 | 采集方 | 方式 |
|-------------|-------|------|
| conversation | simulation.py | 自动记录每轮 user/assistant 消息 |
| tool_logs | MCP Proxy | 自动拦截并记录每次 tool_callback 调用 |
| workspace_files | Docker 容器 | 任务结束后扫描 workspace |
| duration | orchestrator | wall clock 计时 |

---

## 五、与 DeepEval 的关系

当前我们使用 DeepEval 的两个功能：

| DeepEval 组件 | 用途 | 我们的使用方式 |
|-------------|------|-------------|
| **ConversationSimulator** | 学生模拟 + 对话循环管理 | 驱动 student↔agent 多轮交互。核心调用链：`ConversationSimulator.simulate()` → `model_callback(input)` → `agent.generate_response()` |
| **ConversationalGEval** | Tutor 7D 评分 | LLM judge 给对话打分。Phase 1 生成 evaluation steps + Phase 2 评分 |

**ConversationSimulator 的核心价值**：
1. 生成学生消息（`generate_first_user_input`, `generate_next_user_input`）
2. 判断对话是否应该终止（`stop_conversation`）
3. 管理对话循环（while loop + Turn 对象）

我们的 `model_callback`（simulation.py:495）是桥接层——DeepEval 传来学生消息字符串，我们维护 conversation_history、调用 agent_adapter.generate_response、返回 Turn 对象。**agent 调用完全在我们的 callback 里**，不经过 DeepEval 的任何 agent 逻辑。

---

## 六、实施路径

### Phase 0：接口规范文档（无代码改动）

编写 `BENCHMARK_SPEC.md`：
- BaseAgent 接口规范（本文档第四节内容）
- tool_callback 协议（参数类型、返回值格式、错误约定）
- messages 格式（system message 语义、历史管理约定）
- available_tools 的 JSON Schema 格式
- agent_max_steps 的语义（建议值，不强制）

### Phase 1：Facade API

创建 `quanttutorbench/api.py`：
- `BaseAgent` 类（从 `base_adapter.py` 精简）
- `Benchmark` 类封装 BenchmarkOrchestrator + ContainerManager + 学生模拟器
- `Benchmark.run(agent, tasks, personas)` → 内部将用户的 BaseAgent 包装为 BaseAgentAdapter
- `Benchmark.evaluate(run_state)` → 对已有结果评分

包装层逻辑：

```python
class _UserAgentWrapper(BaseAgentAdapter):
    """将用户的 BaseAgent 包装为内部 BaseAgentAdapter。"""

    def __init__(self, user_agent: BaseAgent, system_prompt: str):
        super().__init__(agent_name="user_agent")
        self._user_agent = user_agent
        self.system_prompt = system_prompt

    def generate_response(self, messages, available_tools, tool_callback):
        return self._user_agent.generate_response(messages, available_tools, tool_callback)

    def set_task_context(self, context):
        super().set_task_context(context)
        self._user_agent.on_task_start(context)

    def get_token_records(self):
        usage = self._user_agent.get_token_usage()
        if usage:
            return [TokenRecord(
                model=usage.get("model", "unknown"),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=usage.get("cost_usd", 0.0),
            )]
        return []

    def get_thinking_trace(self):
        return self._user_agent.get_thinking_trace()

    def get_content_blocks(self):
        return self._user_agent.get_content_blocks()

    def reset(self):
        super().reset()
        self._user_agent.on_task_end()
```

这个包装层不改变现有内部逻辑，只是把用户的 BaseAgent 适配为系统已有的 BaseAgentAdapter 接口。

### Phase 2：打包分发

- `pyproject.toml` + `pip install quanttutorbench`
- 依赖管理（deepeval, docker, anthropic SDK 等作为 optional dependencies）
- 示例代码（examples/simple_agent.py, examples/openai_agent.py）

### Phase 3：Web 服务化

- HTTP API：`POST /run`（提交 agent 配置 + task 列表）、`GET /results/{run_id}`
- WebSocket：实时对话流（student ↔ agent 的消息推送）
- Web UI：对话时间线、评分仪表盘、工具调用详情、thinking 展示
- 异步任务队列（长时间运行的 Docker 任务）

---

## 七、Reference Adapter 的定位

我们提供的 4 个 adapter（Anthropic、OpenAI、Google、Generic）是 **reference implementation**：
- 展示如何实现 BaseAgent 接口
- 包含 provider-specific 优化（extended thinking、context management、compaction）
- 用于论文中的 benchmark 结果

用户可以直接使用 reference adapter（传入自己的 API key），也可以完全自建。论文中明确区分：

> "QuantTutorBench consists of a benchmark specification (task format, tool API, evaluation rubrics) and a reference implementation (agent adapters, orchestrator, sandbox). The specification is adapter-agnostic: any system that can invoke the standardized tool API and produce conversational responses can be evaluated."
