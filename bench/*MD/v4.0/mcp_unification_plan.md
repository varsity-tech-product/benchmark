# MCP Unification Plan：对话即工具调用

> 日期：2026-04-08
> 状态：规划 v2，待确认后执行
> 原则：不是代码重写，是把对话循环从 framework 侧移到 agent 侧

---

## 一、现状问题

当前 agent 面对两个交互面：

```
交互面 1（对话）:  ConversationSimulator → model_callback → agent.generate_response() → 返回文本
交互面 2（工具）:  agent → tool_callback → MCPProxy.call_tool() → 返回结果
```

对话循环由 DeepEval ConversationSimulator 驱动。Agent 是被动的——每次收到学生消息，被迫回复一段文本。Agent 不能选择"先调 5 个工具再跟学生说话"，因为 generate_response() 的返回值就是给学生的回复。

Gym 试图解决这个问题，但方式是建一个平行的 runtime（1,248 行），复制了 TC 逻辑和学生模拟器。

## 二、目标架构：Benchmark 即 MCP Server

### 2.1 核心理念

Benchmark 是一个独立的 MCP server。Agent 是任意 MCP client。两者之间**只有 MCP 协议**，没有 harness、没有 adapter、没有 callback。

```
QuantTutorBench MCP Server（独立进程）      Agent（独立进程，任意实现）
  ├── get_session_info()                      连接 server
  ├── send_message(text)        ←MCP协议→     发现工具
  ├── shell_exec(command)                     自主调用
  ├── file_read(path)                         自主决策
  ├── file_write(path, content)               自主结束
  ├── fetch_market_data(...)
  ├── run_backtest(...)
  ├── deploy_trading_bot(...)   [distractor]
  └── ...

  全部记录到统一 interaction log
  session 结束后 → evaluation pipeline → scores
```

`send_message` 就是一个 tool。Agent 不知道背后是学生 LLM，就像不知道 `shell_exec` 背后是 Docker。

### 2.2 触发模型：Skill 文档启动

MCP server 是被动的，不能主动触发 agent。Agent 的第一推力来自**自身的配置**——一份 skill 文档（system prompt / CLAUDE.md / 配置文件），在 agent 启动时加载：

```
Benchmark MCP Server（考场）        Skill 文档（准考证）         Agent（考生）
  独立运行，等待调用                  静态文件，启动前发放          读了须知就开始
  暴露工具                          "调 get_session_info()"     自主连接
  记录一切                          "用 send_message 教学"      自主调用
  评分                              "直到 status=completed"     自主结束
```

**Benchmark 不控制 agent，不知道 agent 是谁，不参与 agent 的决策过程。**

Skill 文档示例：

```markdown
# QuantTutorBench Agent Protocol

You are connected to a QuantTutorBench MCP server.

## Start
Call `get_session_info()` to receive your task description, student profile,
and the student's opening message.

## Interact
- Use any available tool (shell_exec, file_read, fetch_market_data, etc.)
  to prepare your teaching.
- Use `send_message(text)` to talk to the student. It returns the student's
  reply and session status.
- You decide when to research, when to code, when to talk. There is no
  fixed order.

## End
When `send_message` returns `status: "completed"`, the session is over.
Stop calling tools.
```

任何 MCP 兼容的 agent 拿到这份文档就能跑：
- Claude Code → 放 CLAUDE.md
- GPT agent → 放 system prompt
- 自定义 agent → 放配置文件

### 2.3 run_benchmark.py 的角色

纯**批量调度器**——不参与 agent 和 environment 的交互：

```python
for task, persona in job_list:
    server = start_mcp_server(task, persona)    # 1. 启动 server
    agent = start_agent_process(server.url)     # 2. 启动 agent 进程
    agent.wait_until_done()                     # 3. 等待
    scores = server.evaluate()                  # 4. 收分
    server.shutdown()                           # 5. 清理
```

和 `docker compose up` 同等角色——拉起服务、拉起客户端、等结果。不碰交互内容。

---

## 三、具体改动清单

### 3.1 新增：Session 状态管理（~150 行）

**新文件 `bench/mcp_servers/session.py`**

```python
class TutoringSession:
    """管理一次教学 session 的状态。被 send_message / get_session_info 工具调用。"""

    def __init__(self, task, persona, student_sim, tc_checker, max_turns, deadline):
        self._task = task
        self._persona = persona
        self._conversation: list[dict] = []   # {role, content}
        self._turn: int = 0
        self._done: bool = False
        self._student_sim = student_sim
        self._tc_checker = tc_checker
        self._max_turns = max_turns
        self._deadline = deadline

    def handle_get_session_info(self) -> dict:
        """get_session_info 工具的后端。返回任务描述 + 学生开场白。"""
        opening = self._persona.get_opening(self._task)
        self._conversation.append({"role": "user", "content": opening})
        return {
            "task_description": self._task.description,
            "student_profile": self._persona.knowledge_level,
            "student_opening": opening,
            "max_turns": self._max_turns,
            "available_categories": self._task.category,
        }

    def handle_send_message(self, text: str) -> dict:
        """send_message 工具的后端。记录 agent 消息，生成学生回复。"""
        self._conversation.append({"role": "assistant", "content": text})
        self._turn += 1

        # TC 检查
        if self._tc_checker and self._tc_checker.check(self._conversation):
            closing = self._student_sim.generate_closing(self._conversation)
            self._conversation.append({"role": "user", "content": closing})
            self._done = True
            return {"student_reply": closing, "status": "completed", "turn": self._turn}

        # 超时 / max_turns
        if self._turn >= self._max_turns or (self._deadline and time.time() > self._deadline):
            self._done = True
            return {"student_reply": "", "status": "completed", "turn": self._turn}

        # 生成学生回复
        reply = self._student_sim.generate_message(self._conversation)
        self._conversation.append({"role": "user", "content": reply})
        return {"student_reply": reply, "status": "active", "turn": self._turn}

    @property
    def conversation(self) -> list[dict]:
        return list(self._conversation)

    @property
    def done(self) -> bool:
        return self._done
```

**这不是新逻辑**——是把 gym/env.py 的 `send_message()` 和 simulation.py 的 TC 职责收敛到一个地方。

### 3.2 修改：工具注册（registry.py，~40 行改动）

在 `create_proxy_for_task()` 中注册 session 工具：

```python
def create_proxy_for_task(..., session: TutoringSession = None) -> MCPProxy:
    proxy = MCPProxy()

    # 现有：注册 core + convenient + distractor 工具（不变）
    ...

    # 新增：注册 session 工具（不占 15 个 tool slot）
    if session:
        proxy.register_tool(
            name="get_session_info",
            func=session.handle_get_session_info,
            description="Get task description, student profile, and opening message. Call this first.",
            params={"type": "object", "properties": {}, "required": []}
        )
        proxy.register_tool(
            name="send_message",
            func=session.handle_send_message,
            description="Send a message to the student. Returns the student's reply and session status.",
            params={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Your message to the student"}
                },
                "required": ["text"]
            }
        )

    # distractor 填充（不变）
    ...
```

**设计决策：session 工具不占 15 个 tool slot。** 它们是 session 基础设施，不是任务工具。和 `get_environment_info` 类似——所有任务都有，不参与 tool_usage 评分。

### 3.3 修改：Orchestrator PHASE 2（orchestrator.py + simulation.py，~150 行）

**去掉 DeepEval ConversationSimulator 驱动的循环，改为 agent 自驱动。**

orchestrator.py 的 PHASE 2 改为：

```python
# PHASE 2: INTERACT — agent 自驱动
session = TutoringSession(task, persona, student_sim, tc_checker, max_turns, deadline)
register_session_tools(proxy, session)  # 注册 send_message + get_session_info

# Agent 自主运行——它通过 tool_callback 调用所有工具（包括 send_message）
# Adapter 的 generate_response 在内部跑一个长 tool-use 循环
response = agent_adapter.generate_response(
    messages=[{"role": "user", "content": AGENT_BOOTSTRAP_PROMPT}],
    available_tools=proxy.get_available_tools(),
    tool_callback=proxy.call_tool,
)

conversation = session.conversation  # 从 session 拿对话记录
```

**AGENT_BOOTSTRAP_PROMPT** 就是 skill 文档的 inline 版本：
```
You are a quantitative finance tutor. A student is waiting for your help.
Call get_session_info() to see the task and the student's opening message.
Use send_message(text) to communicate with the student.
Use other tools (shell_exec, file_read, etc.) to prepare your teaching.
Continue until send_message returns status "completed".
```

**关键行为变化：**

| | 现在 | 改后 |
|---|---|---|
| 谁驱动对话循环 | DeepEval ConversationSimulator | Agent 自己 |
| Agent 怎么收到学生消息 | framework 传入 model_callback | 调 get_session_info / send_message 的返回值 |
| Agent 怎么回复学生 | generate_response 返回文本 | 调 send_message 工具 |
| 一轮里能调多少工具 | 受 max_iterations 限制 | 同左，但包含 send_message |
| 谁决定对话什么时候结束 | TC checker（framework 侧） | TC checker（send_message 返回值里的 status） |

### 3.4 修改：评分链输入（orchestrator.py `_evaluate_task`，~30 行）

评分链需要 `conversation` 和 `tool_logs`。从统一 log 中提取：

```python
def extract_conversation_from_logs(tool_logs: list[ToolCallLog]) -> list[dict]:
    """从统一 tool log 中提取对话记录。"""
    conversation = []
    for log in tool_logs:
        if log.name == "send_message":
            conversation.append({"role": "assistant", "content": log.args.get("text", "")})
            result = json.loads(log.result)
            if result.get("student_reply"):
                conversation.append({"role": "user", "content": result["student_reply"]})
        elif log.name == "get_session_info":
            result = json.loads(log.result)
            if result.get("student_opening"):
                conversation.append({"role": "user", "content": result["student_opening"]})
    return conversation
```

评分链其余部分（QR blend、QP 7 维度、Tutor 7D）**零改动**——它们只消费 conversation + tool_logs，不关心这些数据怎么来的。

### 3.5 删除：gym/

整个 `bench/gym/` 目录删除（1,248 行）。其功能去向：

| Gym 组件 | 去向 |
|----------|------|
| `types.py` (Observation, Scores) | 删除，不再需要 |
| `env.py` (QuantTutorEnv) | 功能被 TutoringSession + registry 吸收 |
| `student_sim.py` (StudentSimulator) | **保留，移到 `bench/mcp_servers/student_sim.py`** |
| `tc_checker.py` (TCChecker) | **保留，移到 `bench/mcp_servers/tc_checker.py`**（消除和 simulation.py 的重复） |
| `example_agent.py` | 删除，skill 文档替代 |
| `__init__.py` | 删除 |

### 3.6 新增：Skill 文档

**新文件 `bench/AGENT_PROTOCOL.md`**（skill 文档 / 准考证）

内容见 §2.2。这份文档是 benchmark 对外的唯一接口说明。不是代码，是协议。

### 3.7 更新：BENCHMARK_SPEC.md

§3 从 gym 接口改为 MCP server + skill 文档描述：

> QuantTutorBench exposes the evaluation environment as a standard MCP server. Agents connect as MCP clients, discover available tools, and interact autonomously. The agent protocol is defined in `AGENT_PROTOCOL.md`: call `get_session_info()` to receive the task, use domain tools to prepare, use `send_message()` to teach the student, and continue until the session completes. The server records a unified interaction log; the evaluation pipeline scores this log along three axes (QR, QP, Tutor 7D) without access to the agent's internals.

---

## 四、不改什么

| 组件 | 理由 |
|------|------|
| MCPProxy 核心（call_tool, logging, truncation） | 已经是统一日志，不需要动 |
| 评分链（QR blend, QP 7 维度, Tutor 7D） | 只消费 conversation + tool_logs，不关心来源 |
| ContainerManager / Docker 沙箱 | 基础设施层，与对话模型无关 |
| Tool executor daemon | 工具执行层，不变 |
| 15-slot tool 系统 | Distractor 逻辑不变，session 工具不占 slot |
| Adapter 内部实现（Anthropic BetaToolRunner 等） | 已支持多轮 tool calling，天然兼容 |

---

## 五、风险和 Fallback

### 5.1 Agent 不调 send_message 怎么办？

Agent 可能忽略 send_message 工具，直接返回文本当作给学生的回复（当前行为）。

**Fallback：** 在 orchestrator 的 PHASE 2 结尾检测——如果 session.conversation 为空（agent 从未调 send_message），把 agent 的文本返回值包装成一次 send_message 调用。向后兼容，log 格式统一。

### 5.2 DeepEval ConversationalGolden / TestCase 还需要吗？

**评分链需要 ConversationalTestCase。** Tutor 7D 的 ConversationalGEval 期望 DeepEval 格式输入。

**解决：** 在 `_evaluate_task()` 入口处，从 session.conversation 构建 ConversationalTestCase。纯数据格式转换，不影响评分逻辑。

### 5.3 TC 逻辑重复怎么处理？

**统一到 `bench/mcp_servers/tc_checker.py`。** simulation.py 中的 `_EfficientSimulator` 删除，改为 TutoringSession 内部使用 TCChecker。一份代码，一个行为。

### 5.4 学生模拟器后端不一致（DeepEval vs OpenRouter）？

**统一用 OpenRouter 直连。** StudentSimulator（从 gym 移过来的）不依赖 DeepEval。DeepEval 只在评分时使用（ConversationalGEval），不再用于对话生成。

### 5.5 Adapter 接口变化

Adapter 的 `generate_response()` 签名不变。但行为变了：
- 之前：每次调用处理一轮对话（一个学生消息 → 一个 tutor 回复）
- 现在：一次调用跑完整个 session（多轮 tool-use 循环）

**需要调高 max_iterations** —— Anthropic adapter 的 BetaToolRunner 需要足够的迭代次数来完成多轮对话 + 多次工具调用。建议 `max_iterations = max_turns * 10`。

Generic adapter（OpenAI）需要加一个外层循环：tool calls → execute → 放回 messages → 再调 API，直到没有 tool_calls。~20 行。

---

## 六、执行步骤

```
Step 1: 提取共享组件（不改行为）
  - 移动 gym/student_sim.py → mcp_servers/student_sim.py
  - 移动 gym/tc_checker.py → mcp_servers/tc_checker.py
  - simulation.py 中的 _EfficientSimulator 改为导入 tc_checker
  - 跑 run_benchmark.py 验证行为不变

Step 2: 新增 session 层 + session 工具
  - 新建 mcp_servers/session.py（TutoringSession）
  - 修改 registry.py 注册 get_session_info + send_message
  - 此时 session 工具存在但没人调——旧路径仍然工作

Step 3: 切换对话循环
  - 修改 orchestrator.py PHASE 2：创建 session → agent 自驱动
  - 修改 simulation.py：旧 run_conversation_simulation 保留但标记 deprecated
  - 加 fallback：agent 不调 send_message 时自动包装
  - 修改评分链入口：从 session log 提取 conversation
  - 跑 run_benchmark.py 验证分数一致

Step 4: 清理
  - 删除 bench/gym/
  - 删除 simulation.py 中的旧 ConversationSimulator 路径（确认新路径稳定后）
  - 新建 bench/AGENT_PROTOCOL.md（skill 文档）
  - 更新 BENCHMARK_SPEC.md §3

Step 5: 验证
  - 对比改动前后的评分（同 task × 同 persona × 同 seed）
  - 确认 tool_logs 中 send_message 记录格式正确
  - 确认评分链从统一 log 提取的 conversation 和之前一致
  - 用 Claude Code 连 MCP server 手动跑一次验证端到端
```

---

## 七、改动量估算

| 文件 | 改动类型 | 估算行数 |
|------|---------|---------|
| `mcp_servers/session.py` | **新建** | ~150 |
| `mcp_servers/student_sim.py` | **移动** from gym/ | 0（原样） |
| `mcp_servers/tc_checker.py` | **移动** from gym/ | 0（原样） |
| `mcp_servers/registry.py` | 修改 | ~40 |
| `orchestrator/orchestrator.py` | 修改（PHASE 2 切换 + 评分链适配） | ~80 |
| `orchestrator/simulation.py` | 标记 deprecated（后续删除） | ~10 |
| `AGENT_PROTOCOL.md` | **新建** | ~50 |
| `BENCHMARK_SPEC.md` | 修改 §3 | ~50 |
| `gym/` | **删除** | -1,248 |
| **净改动** | | **约 -870 行**（删的比加的多） |

---

## 八、论文表述

### Section 3 (Benchmark Specification)

> QuantTutorBench exposes the evaluation environment as a standard MCP server. The agent protocol consists of three phases:
>
> 1. **Discovery**: The agent calls `get_session_info()` to receive the task description, student persona, and opening message.
> 2. **Interaction**: The agent uses domain tools (`shell_exec`, `file_read`, `fetch_market_data`, etc.) to prepare its teaching, and `send_message(text)` to communicate with the student. The student's reply is returned as the tool result. The agent decides the interleaving of tool use and dialogue autonomously.
> 3. **Termination**: When all learning objectives are covered (detected server-side), `send_message` returns `status: "completed"`. The session ends.
>
> The server records a unified interaction log capturing every tool invocation and its result. The evaluation pipeline (§4) extracts pedagogical dialogue and tool-use patterns from this log to compute scores along three axes: Quantitative Result (QR), Quantitative Process (QP), and Tutoring Effectiveness (Tutor 7D).
>
> This design follows MCP's philosophy: the agent sees a set of tools with names and schemas, invokes them as needed, and receives results — without knowledge of the backend implementation. The student simulator, sandbox environment, and termination checker are all opaque services behind tool interfaces.

### Section 4 (Reference Implementation)

> Our reference harness (`run_benchmark.py`) instantiates the MCP server for each (task, persona) pair, bootstraps an LLM agent with the protocol document (`AGENT_PROTOCOL.md`), and collects evaluation scores. The harness is a batch scheduler — it does not mediate agent-environment interaction. Third-party agents can connect to the same MCP server using any MCP-compatible client.
