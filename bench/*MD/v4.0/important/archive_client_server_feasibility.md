# Client-Server 架构可行性深度分析

> 2026-04-08 | 试卷(task) + 文具(tools) + 考场(docker) + 打分(eval) = Server；作答者 = Client

---

## 一、架构模型

```
Server（我们）                              Client（任何东西）
┌─────────────────────────────────┐       ┌─────────────────────┐
│ Task Registry (65 tasks)        │       │                     │
│ Student Simulator (DeepEval)    │       │  LLM Agent          │
│ Docker Sandbox (per session)    │ ←──→  │  人类 (Web UI)       │
│ MCP Tool Proxy (logging)        │       │  人类 + Agent 混合   │
│ Evaluation Pipeline             │       │  curl / SDK / 任何   │
│                                 │       │                     │
│ 所有工具执行在 Server 侧        │       │  Client 只发消息     │
│ 所有日志由 Server 自动采集       │       │  和工具调用请求      │
└─────────────────────────────────┘       └─────────────────────┘
```

**核心原则**：Client 不运行任何评估代码，不接触任何数据文件，不拥有 Docker 容器。Client 只做两件事：
1. 收到学生消息 → 回复文本
2. 回复之间 → 请求调用工具并接收结果

---

## 二、协议设计与 Turn 边界

### 2.1 一次对话 turn 的完整生命周期

```
Phase A: Server 发送学生消息
  Server → Client: {type: "student_message", content: "What is Sharpe ratio?", turn_index: 0}

Phase B: Client 的 action 阶段（可以有 0~N 次工具调用）
  Client → Server: {type: "tool_call", name: "search_docs", args: {query: "sharpe"}}
  Server → Client: {type: "tool_result", name: "search_docs", result: "...", success: true}
  Client → Server: {type: "tool_call", name: "compute_statistics", args: {...}}
  Server → Client: {type: "tool_result", name: "compute_statistics", result: "...", success: true}
  ...

Phase C: Client 结束 turn
  Client → Server: {type: "response", content: "The Sharpe ratio measures..."}

Phase D: Server 处理
  Server: 记录对话 → 学生模拟器生成下一条消息 → 回到 Phase A
  或: TC Checker 判定完成 → 进入评估
```

### 2.2 Turn 边界的精确定义

**一个 turn = Phase A 到 Phase C**。规则：

| 规则 | 说明 |
|------|------|
| Client 发 `response` 才算 turn 结束 | 这是唯一的 turn 终止信号 |
| `response` 之后不能再发 `tool_call` | Server 拒绝并返回 error |
| `tool_call` 之间 Client 可以等待任意时长 | Server 只在 session 级别设超时，不在 tool_call 间设超时 |
| Client 可以发 0 次 tool_call 直接 response | 合法（纯文本回复，不用工具） |
| Client 可以发 N 次 tool_call 然后 response | 合法（agent 的 tool loop） |
| Client 发 `response` 后 content 为空 | 合法但评分会很低（没有回复学生） |

### 2.3 并行工具调用

某些 LLM 在一次 API 调用中返回多个 tool_use。协议支持批量：

```json
Client → Server: {
    "type": "batch_tool_call",
    "calls": [
        {"name": "compute_statistics", "args": {...}},
        {"name": "plot_chart", "args": {...}}
    ]
}
Server → Client: {
    "type": "batch_tool_result",
    "results": [
        {"name": "compute_statistics", "result": "...", "success": true},
        {"name": "plot_chart", "result": "...", "success": true}
    ]
}
```

Server 内部并行执行这些工具（MCP Proxy 已支持），按请求顺序返回结果。

### 2.4 特殊消息类型

```json
// Session 开始
Server → Client: {
    "type": "session_start",
    "task_id": "D01_load_inspect_ohlcv",
    "system_prompt": "You are an expert quantitative finance tutor...",
    "task_context": "Task: Guide a student to load and inspect OHLCV data...",
    "available_tools": [{name, description, parameters}, ...],
    "agent_max_steps": 10,
    "timeout_minutes": 15
}

// Session 结束（Server 发起）
Server → Client: {
    "type": "session_end",
    "reason": "tc_complete" | "max_turns" | "timeout" | "client_disconnect",
    "turns_completed": 5
}

// 可选 metadata（Client 发起，不影响评分）
Client → Server: {
    "type": "metadata",
    "thinking": "Let me analyze the student's question...",
    "token_usage": {"input": 5000, "output": 800, "model": "gpt-5.2"}
}

// 评估结果
Server → Client: {
    "type": "evaluation_complete",
    "scores": {"OAS": 0.72, "QR": 0.85, "QP": 0.68, "Tutor": 0.64},
    "report_url": "/results/session_abc123/scores.md"
}
```

---

## 三、Session 管理与 Docker 生命周期

### 3.1 Session 状态机

```
                    Client connects
                         │
                         ▼
              ┌─── INITIALIZING ───┐
              │  创建 Docker 容器    │
              │  挂载数据/文档      │
              │  启动 tool_executor │
              │  LEAN 预编译(如需)  │
              └────────┬───────────┘
                       │ (~5-15s)
                       ▼
              ┌─── READY ─────────┐
              │  发送 session_start │
              │  发送第一条学生消息  │
              └────────┬───────────┘
                       │
                       ▼
              ┌─── ACTIVE ────────┐
              │  对话进行中         │◄──── 循环：student_message → tool_calls → response
              └────────┬───────────┘
                       │ TC complete / max_turns / timeout / disconnect
                       ▼
              ┌─── EVALUATING ────┐
              │  运行评估流水线     │
              │  (~30-120s)       │
              └────────┬───────────┘
                       │
                       ▼
              ┌─── COMPLETE ──────┐
              │  发送评估结果       │
              │  保存报告文件       │
              └────────┬───────────┘
                       │ (grace period 60s)
                       ▼
              ┌─── TEARDOWN ──────┐
              │  销毁 Docker 容器   │
              │  清理临时文件       │
              └───────────────────┘
```

### 3.2 异常处理

| 异常 | 处理 |
|------|------|
| **Client 断连 (ACTIVE 阶段)** | Server 等待 reconnect_timeout (60s)。如果 Client 带相同 session_id 重连，恢复对话。超时则 session_end(reason=disconnect)，保存已有对话，运行评估 |
| **Client 超时不回复** | Session 级别 timeout_minutes（task JSON 定义，默认 15min）。超时后 Server 注入 timeout wrap-up 消息（复用现有 simulation.py 的 timeout 逻辑），结束对话，运行评估 |
| **Docker 容器崩溃** | tool_executor 自动重启（现有 `_restart_executor` 逻辑）。如果容器本身挂了，session_end(reason=container_error) |
| **评估超时** | 评估异步运行，Client 可以断开。结果通过 webhook 或轮询获取 |

### 3.3 容器资源管理

| 策略 | 说明 |
|------|------|
| **容器池** | 预启动 N 个通用容器（quant-tutor-env），Client 连接时从池中分配。减少 INITIALIZING 阶段的等待 |
| **LEAN 容器** | LEAN 镜像大（~4GB）且预编译慢（~15s）。LEAN task 需要单独的容器池或按需创建 |
| **并发限制** | 每个 server 实例最多 M 个活跃 session（取决于 CPU/RAM）。超出排队 |
| **空闲回收** | ACTIVE 阶段如果 5 分钟没有任何 Client 消息，自动转入 EVALUATING |

---

## 四、Docker 在 Client-Server 模型下的角色

### 4.1 当前 Docker 的隔离作用

当前架构中，Docker 容器提供：

| 隔离维度 | 实现方式 | 当前有效性 |
|----------|---------|-----------|
| **文件系统隔离** | workspace 在容器内，agent 只能读写 /workspace | ✅ 有效 |
| **网络隔离** | `--network none`（默认），阻止容器访问外网 | ✅ 有效 |
| **资源限制** | `--cpus`, `--memory` 限制 | ✅ 有效 |
| **代码执行隔离** | shell_exec 在容器内执行，不影响 host | ✅ 有效 |

### 4.2 Client-Server 下 Docker 的角色变化

**关键转变**：当前 Docker 隔离的是 **agent 的工具执行**——agent 通过 tool_callback 在容器内跑代码。Client-Server 下：

- **工具执行仍然在 Docker 内**：Client 发 `tool_call`，Server 通过 `call_tool_in_container` 在 Docker 内执行，返回结果。Docker 的隔离作用**完全保留**。
- **Client 不接触 Docker**：Client 不知道 Docker 的存在。它只知道"发一个 tool_call 请求，收到一个文本结果"。
- **网络隔离更加彻底**：当前 agent 和工具在同一进程中，agent 理论上可以绕过 tool_callback 直接访问 host 文件系统（虽然我们的 adapter 不会这样做，但一个恶意的自定义 adapter 可以）。Client-Server 下这完全不可能——Client 是远程进程，唯一的交互通道是 WebSocket 协议。

### 4.3 Docker 不能隔离的：Client 的外部能力

Docker 隔离的是**工具执行环境**，不是 Client 本身。Client 的外部能力不受我们控制：

| Client 行为 | Docker 能否阻止 | 影响 |
|-------------|---------------|------|
| Client 调用外部 API（如 Google 搜索获取知识） | ❌ 不能 | Client 可以获得额外知识来回答学生 |
| Client 使用更强的 LLM（比 benchmark 规定的更强） | ❌ 不能 | 但这不是"作弊"——benchmark 评的是输出质量，不限制输入 |
| Client 预先知道 eval script 的 check items | ❌ 不能 | 可以刷 QR 的 programmatic eval 分数 |
| Client 伪造 tool_result（篡改工具返回值） | ✅ **能** | Client 只能发 tool_call 请求，result 由 Server 返回 |
| Client 在 Docker 内执行恶意代码 | ✅ **能** | shell_exec 在隔离容器内，`--network none` 阻止数据泄漏 |

---

## 五、作弊分析

### 5.1 Client-Server 模型的天然防护

| 防护 | 原因 |
|------|------|
| **工具结果不可篡改** | tool_result 从 Server 发出，Client 只读 |
| **工具日志不可遗漏** | 所有 tool_call 经 Server 的 MCP Proxy，100% 记录 |
| **对话内容不可修改** | conversation 由 Server 维护，Client 只能追加 response |
| **评估在 Server 侧** | eval scripts、scoring formula、LLM judge 都在 Server 跑 |

### 5.2 仍然存在的作弊风险

**风险 1：知识注入**

Client 可以在收到学生问题后，先查 Google/外部知识库，再回复。这等于"开卷考试"。

**是否需要防护？不需要。**

理由：
1. 我们测的不是"agent 知不知道"（那是 QR 的一小部分），而是"agent 能不能教好"（Tutor 7D）
2. 即使 agent 知道所有答案，如果不会因材施教、不会搭脚手架、不会管理对话节奏，Tutor 分数仍然很低
3. 人类 Client 也会查资料——这是 benchmark 设计允许的
4. SWE-bench、GAIA 等 benchmark 也不限制 agent 使用外部知识

**风险 2：eval script 逆向工程**

如果 eval scripts 开源（论文发表后），Client 可以针对 check items 优化输出。例如 B01 检查 "sharpe_present"，Client 只要在回复里塞一个数字跟 "Sharpe" 就能拿到 0.2 分。

**是否严重？不严重。**

理由：
1. Programmatic eval 只是 QR 的一部分（名义权重 30%，dampening 后可能更低）
2. 刷 programmatic 不影响 Tutor 7D（占 OAS 的 30%）
3. QP 评估的是工具调用过程质量，不能通过输出文本刷分
4. 这和传统 benchmark 的"过拟合训练数据"问题一样——是已知的、可接受的限制

**风险 3：Client 不是真的在"教学"**

Client 可以直接给学生完整答案，不教学过程。这不是作弊，但会导致 Tutor 分数低——因为 rubric 明确要求 scaffolding、level detection 等教学行为。

**这恰恰是 benchmark 的设计目标**——区分"会做"和"会教"。

### 5.3 我们需要验证但无法强制的

| 验证项 | 方式 | 可行性 |
|--------|------|--------|
| Client 是否使用了声称的模型 | Client 提交 metadata 中的 model 字段 | 信任但可选（类似 leaderboard 的 honor system） |
| Client 是否使用了额外知识 | 无法验证 | 不需要（开卷考试设计） |
| Client 的推理成本 | Client 提交 token_usage | 可选，用于成本效率排行 |

---

## 六、与现有架构的兼容路径

### 6.1 核心改动：model_callback 的 Server 化

当前 `simulation.py` 的 `model_callback` 是同步函数：

```python
def model_callback(input: str, **kwargs):
    conversation_history.append({"role": "user", "content": input})
    response = agent_adapter.generate_response(messages, tools, tool_callback)
    conversation_history.append({"role": "assistant", "content": response})
    return Turn(role="assistant", content=response)
```

Client-Server 下变为：

```python
def model_callback(input: str, **kwargs):
    conversation_history.append({"role": "user", "content": input})
    # 发送学生消息给 Client
    ws.send({"type": "student_message", "content": input, "turn_index": turn_idx})
    # 阻塞等待 Client 完成 turn（可能包含多次 tool_call 往返）
    response = wait_for_client_response(ws, proxy, timeout)
    conversation_history.append({"role": "assistant", "content": response})
    return Turn(role="assistant", content=response)

def wait_for_client_response(ws, proxy, timeout):
    """处理 Client 的 tool_call / response 消息循环"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.recv(timeout=deadline - time.time())
        if msg["type"] == "tool_call":
            result = proxy.call_tool(msg["name"], **msg["args"])
            ws.send({"type": "tool_result", "name": msg["name"], "result": result})
        elif msg["type"] == "batch_tool_call":
            results = [proxy.call_tool(c["name"], **c["args"]) for c in msg["calls"]]
            ws.send({"type": "batch_tool_result", "results": results})
        elif msg["type"] == "response":
            return msg["content"]
        elif msg["type"] == "metadata":
            store_metadata(msg)  # 可选，不影响流程
    raise TimeoutError("Client did not respond within timeout")
```

**关键兼容性**：
- `DeepEval ConversationSimulator` 不变——它只知道调 `model_callback` 拿回 Turn
- 评估流水线不变——它只需要 conversation + tool_logs + workspace
- MCP Proxy 不变——`proxy.call_tool` 已有完整的日志记录

### 6.2 同时支持两种模式

```python
# 模式 1：进程内（现有 adapter，用于我们自己跑 benchmark）
model_callback = create_model_callback(agent_adapter=anthropic_adapter, proxy=proxy, ...)

# 模式 2：Client-Server（用户远程连接）
model_callback = create_server_callback(websocket=ws, proxy=proxy, ...)
```

两种模式产出相同的 conversation_history + tool_logs，评估流水线完全不需要知道 callback 的具体实现。

### 6.3 不需要改动的模块

| 模块 | 原因 |
|------|------|
| `evaluation/*` | 输入不变（conversation + tool_logs + workspace） |
| `scoring.py` | 输入不变（各维度分数） |
| `score_report.py` / `cost_report.py` / `trace_report.py` | 输入不变 |
| `mcp_servers/core/tools.py` | 工具定义不变 |
| `mcp_servers/proxy/mcp_proxy.py` | 工具执行和日志记录不变 |
| `container_manager.py` | Docker 管理不变 |
| `orchestrator.py:_evaluate_task()` | 评估逻辑不变 |
| Task JSON / Persona JSON | 数据定义不变 |

### 6.4 需要新增的模块

| 模块 | 功能 |
|------|------|
| `server/session_manager.py` | Session 生命周期管理（创建、激活、超时、销毁） |
| `server/websocket_handler.py` | WebSocket 协议处理（解析 Client 消息、路由） |
| `server/server_callback.py` | 将 WebSocket 交互包装为 model_callback |
| `server/api.py` | HTTP/WS 入口（FastAPI/Starlette） |
| `server/container_pool.py` | 容器池管理（可选，优化启动速度） |

---

## 七、传输协议选择

### 7.1 WebSocket vs HTTP

| | WebSocket | HTTP (长轮询/SSE) |
|---|---|---|
| 工具调用延迟 | 低（持久连接，无握手开销） | 高（每次请求新连接） |
| 双向通信 | ✅ 天然支持 | ❌ 需要轮询或 SSE |
| 连接管理 | 复杂（心跳、重连） | 简单（无状态） |
| 防火墙友好 | ⚠️ 部分环境拦截 WS | ✅ 标准 HTTP |
| 适合人类 Client | ✅ 实时推送 | ⚠️ 需要额外机制 |

**建议**：主协议用 WebSocket（对话交互需要低延迟双向通信），同时提供 HTTP REST 端点用于 session 管理（创建、查询状态、获取结果）。

### 7.2 API 设计

```
REST Endpoints:
  POST   /sessions              → 创建 session（返回 session_id + ws_url）
  GET    /sessions/{id}         → 查询 session 状态
  GET    /sessions/{id}/results → 获取评估结果（评估完成后）
  DELETE /sessions/{id}         → 提前终止 session

WebSocket:
  ws://server/sessions/{id}/ws  → 对话交互通道

  Server → Client:
    session_start, student_message, tool_result, batch_tool_result, session_end, evaluation_complete

  Client → Server:
    tool_call, batch_tool_call, response, metadata
```

---

## 八、成本模型

### 8.1 Server 端成本（我们承担或向用户收费）

| 资源 | 每 session 成本 | 说明 |
|------|----------------|------|
| Docker 容器 | ~$0.001-0.01/小时 | CPU/RAM 按需分配 |
| 学生模拟器 LLM | ~$0.01-0.05/task | GPT-5.2 via OpenRouter |
| TC Checker LLM | ~$0.005/task | 增量检查，token 节省 97% |
| 评估 LLM | ~$0.30-0.65/task | Tutor 7D 最贵（21 judge calls） |
| 存储 | 极低 | workspace files + run_state |

**评估成本是大头**——每个 task 约 $0.35-0.70，主要来自 Tutor 7D。Phase 1 缓存优化后约节省 11%。

### 8.2 Client 端成本（用户承担）

| 资源 | 用户控制 | 说明 |
|------|---------|------|
| Agent LLM 调用 | 完全自控 | 用户选择模型和 API key |
| 网络流量 | 极低 | 工具结果文本为主 |

### 8.3 计费模式选项

| 模式 | 适合场景 |
|------|---------|
| **免费（限量）** | 每月 N 个 session，用于论文复现和初步评估 |
| **按 session 计费** | 每个 session $1-2（覆盖 Docker + 评估 LLM 成本） |
| **自部署** | 用户自己部署 Server（开源），只需自己的 LLM API key |

---

## 九、开放问题

### 9.1 学生模拟器的 API key 谁提供？

学生模拟器需要调 LLM（当前是 GPT-5.2 via OpenRouter）。在 Client-Server 下：
- 如果用户自部署 Server → 用户自己的 API key
- 如果我们提供托管 Server → 我们的 API key（成本计入 session 费用）

### 9.2 评估模型的选择权

当前评估用 Sonnet judge。在 Client-Server 下：
- 用户能否选择 eval judge model？→ 建议不允许（评分标准应统一）
- 如果允许，排行榜按 judge 分别展示

### 9.3 排行榜和结果公开

Client-Server 天然支持排行榜：
- 每个 session 产出标准化评分
- 用户提交 model 名称和 metadata
- 按 OAS / QR / QP / Tutor 分别排名
- 可选：公开对话内容（需用户同意）

### 9.4 人类 Client 的 UX

人类通过 Web UI 做 Client 时需要：
- 看到学生消息
- 浏览可用工具列表和参数说明
- 填写工具参数并发送 tool_call
- 看到工具返回结果
- 编写文本回复

这是一个完整的 IDE 级别的界面——工具参数填写、结果预览、Markdown 编辑器。工程量不小但价值极高：**人类做完几个 task，就是最好的 human calibration 数据**。

---

## 十、与 BaseAgent 方案的关系

Client-Server 不替代 BaseAgent——两者共存：

```
用户选择 1：Client-Server（远程、任何语言、支持人类）
  → 通过 WebSocket 协议交互
  → Server 内部用 server_callback 包装为 model_callback

用户选择 2：BaseAgent（本地、Python、进程内）
  → pip install quanttutorbench
  → 实现 BaseAgent.generate_response
  → 框架内部用 _UserAgentWrapper 包装为 BaseAgentAdapter

两种方式产出相同的数据 → 相同的评估流水线 → 相同的评分
```

BaseAgent 是 Client-Server 的**特化版**——当 Client 和 Server 在同一进程中时，WebSocket 退化为函数调用。

---

## 十一、实施优先级建议

| Phase | 内容 | 前置条件 | 价值 |
|-------|------|---------|------|
| **0** | 协议规范文档 (BENCHMARK_SPEC.md) | 无 | 论文审稿必需 |
| **1a** | BaseAgent Facade API（Python 进程内） | Phase 0 | 最快可用的用户接入方式 |
| **1b** | Session Manager + WebSocket 基础 | Phase 0 | 支持远程 Client |
| **2** | Web UI（人类 Client） | Phase 1b | 人类标定数据 + 演示 |
| **3** | 容器池 + 排行榜 + 计费 | Phase 1b | 生产化 |
