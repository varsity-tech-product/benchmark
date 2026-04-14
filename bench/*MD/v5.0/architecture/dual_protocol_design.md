# 双协议支持设计（MCP + REST）

> 日期：2026-04-10
> 前提：Phase 1-6 已完成，server 当前仅 MCP 支持 benchmark 交互

---

## 一、设计目标

Server 启动后，用户通过 MCP **或** REST 均可完成全部操作，处理逻辑和效果完全一致。
两种协议各自发挥优势：REST 用最简单的 HTTP 调用，MCP 用标准工具发现协议。

---

## 二、统一路由结构

所有 session 交互统一到 `/session/` 路径下。现有 `/api/*` 查询端点移除。
MCP 保持 `/mcp` 独立入口。

```
/mcp                                   ← MCP StreamableHTTP（不变）
/session/register                      ← POST   注册
/session/{sid}/start                   ← POST   开始
/session/{sid}/tools                   ← GET    工具列表
/session/{sid}/tool/{name}             ← POST   调用 domain tool（禁止 session API 工具名）
/session/{sid}/send                    ← POST   发送消息
/session/{sid}/evaluate[?force=true]   ← POST   请求评分
/session/{sid}/results                 ← GET    获取运行结果（run_state.json）
/session/{sid}/scores[?history=true]   ← GET    获取评分
/session/{sid}                         ← GET    状态查询 / DELETE 取消
/session/list[?task_id=X01]            ← GET    查询 session 列表
```

旧路由 `/api/*` 移除。

**路由注册顺序**：Starlette Route 按注册顺序匹配。`/session/register` 和
`/session/list` 必须注册在 `/session/{sid}` 和 `/session/{sid}/*` 之前，
否则 `register` 和 `list` 会被误匹配为 `{sid}` 的值。

```python
rest_routes = [
    # 精确路径优先
    Route("/session/register", rest_register, methods=["POST"]),
    Route("/session/list", rest_list, methods=["GET"]),
    # 带参数路径在后
    Route("/session/{sid}", rest_session_status, methods=["GET", "DELETE"]),
    Route("/session/{sid}/start", rest_start, methods=["POST"]),
    Route("/session/{sid}/tools", rest_tools, methods=["GET"]),
    Route("/session/{sid}/tool/{name}", rest_tool_call, methods=["POST"]),
    Route("/session/{sid}/send", rest_send, methods=["POST"]),
    Route("/session/{sid}/evaluate", rest_evaluate, methods=["POST"]),
    Route("/session/{sid}/results", rest_results, methods=["GET"]),
    Route("/session/{sid}/scores", rest_scores, methods=["GET"]),
]
```

---

## 三、REST 端点详细定义

### 3.1 `POST /session/register`

```json
请求: {"task_id": "X01_ma_offbyone"}
200:  {"accepted": true, "session_id": "a1b2c3d4e5f6"}
400:  {"accepted": false, "error": "Missing required field: task_id"}
404:  {"accepted": false, "error": "Task not found: INVALID_ID"}
```

**原子操作**：一次请求完成"创建 SessionState + 调用 register"。
成功时 session 进入 REGISTERED 阶段并存入 `_sessions`。
失败时 SessionState 不保留、资源立即清理。

与 MCP 路径的差异：MCP 在 `initialize` 时创建 UNREGISTERED session，
再等 client 调 `register_session` 工具。REST 跳过 UNREGISTERED 阶段。

### 3.2 `POST /session/{sid}/start`

```json
200:  {"student_message": "I wrote a moving average crossover strategy but..."}
403:  {"error": "Session not started. Call start_session first.", "allowed": ["start_session"]}
404:  {"error": "Session not found"}
```

### 3.3 `GET /session/{sid}/tools`

```json
200: {
  "tools": [
    {"name": "shell_exec", "description": "...", "inputSchema": {...}},
    {"name": "send_message", "description": "...", "inputSchema": {...}},
    ...
  ]
}
404: {"error": "Session not found"}
```

工具列表随 phase 变化（与 MCP `list_tools` 一致）。
**不经过 `check_permission`**——只读查询，任何阶段可调。

### 3.4 `POST /session/{sid}/tool/{name}`

```json
请求: {"command": "ls /workspace"}
200:  {"result": "strategy.py\ndata/\n..."}
400:  {"error": "Use the dedicated /session/{sid}/send endpoint instead."}
403:  {"error": "Cannot call 'shell_exec' during ...", "allowed": ["..."]}
404:  {"error": "Session not found"}
```

请求体 = 工具参数本身（REST 风格，不嵌套 arguments）。

**session API 工具名被拦截**：使用 `SESSION_API_TOOLS` 集合（`protocol.py` 中已定义）
加上 `COMPLETED_TOOLS` 集合（`get_results`、`get_scores`）的并集进行过滤。
新增 session API 工具时只需更新集合，无需修改过滤逻辑。

```python
_TOOL_ENDPOINT_BLOCKED = SESSION_API_TOOLS | {"get_results", "get_scores"}

async def rest_tool_call(request):
    name = request.path_params["name"]
    if name in _TOOL_ENDPOINT_BLOCKED:
        return JSONResponse({"error": f"Use /session/{{sid}}/{name} instead."}, 400)
    ...
```

### 3.5 `POST /session/{sid}/send`

```json
请求: {"text": "Let me help you debug this..."}
200:  {"student_message": "Oh I see...", "status": "active"}
  或: {"student_message": "Thanks!", "status": "completed", "reason": "objectives_met"}
400:  {"error": "Empty message. Provide text to send to the student."}
403:  {"error": "Cannot call 'send_message' during ...", "allowed": ["..."]}
404:  {"error": "Session not found"}
```

### 3.6 `POST /session/{sid}/evaluate[?force=true]`

```json
200:  {"status": "running", "message": "Evaluation started."}
  或: {"status": "completed", "scores": {"overall": 0.72, ...}}
403:  {"error": "Session not yet completed"}
404:  {"error": "Session not found"}
```

`?force=true` 重置状态后重新评分。

### 3.7 `GET /session/{sid}/results`

```json
200:  {"task_id": "X01", "session_id": "abc", "conversation": [...], ...}
404:  {"error": "Results not found"}
```

返回 run_state.json 内容。**不经过 `check_permission`**——只读查询。没有结果时返回 404。

### 3.8 `GET /session/{sid}/scores[?history=true]`

```json
默认: {"status": "completed", "scores": {"overall": 0.72, ...}}
  或: {"status": "pending"}
  或: {"status": "running"}
  或: {"status": "failed", "error": "..."}
历史: {"session_id": "abc", "evaluations": [{...}, {...}]}
404:  {"error": "Session not found"}
```

**不经过 `check_permission`**——只读查询。未评分时返回 `{"status": "pending"}`。

### 3.9 `GET /session/{sid}`

```json
200:  {"session_id": "abc", "task_id": "X01", "phase": "in_session", "persona_id": "beginner_no_finance"}
404:  {"error": "Session not found"}
```

**不经过 `check_permission`**——只读查询。

### 3.10 `DELETE /session/{sid}`

```json
200:  {"status": "cancelled"}
404:  {"error": "Session not found"}
```

销毁环境，不保存结果。

### 3.11 `GET /session/list[?task_id=X01]`

```json
200:  {"sessions": [{"session_id": "abc", "task_id": "X01", "phase": "completed", ...}]}
```

---

## 四、MCP 工具补充

COMPLETED 阶段增加查询工具：

| 工具 | 参数 | 说明 |
|------|------|------|
| `get_results` | 无 | 返回 run_state.json 内容 |
| `get_scores` | `history: bool = false` | 默认返回最新评分，history=true 返回全部评分历史 |

`check_permission` COMPLETED 阶段允许列表扩展为：
`request_evaluation`、`get_results`、`get_scores`。

```python
# protocol.py
_COMPLETED_TOOLS = frozenset({"request_evaluation", "get_results", "get_scores"})

if phase == SessionPhase.COMPLETED:
    if tool_name in _COMPLETED_TOOLS:
        return True, "", []
    return False, "Session completed.", list(_COMPLETED_TOOLS)
```

不通过 MCP 暴露 `list_runs`——它跨 session 查询，不属于单个 session 工具。

---

## 五、Server 侧 auto_eval 开关

### 5.1 设计

自动评估是 **Server 侧配置**，不由 client 控制。通过 Server 启动参数设置：

```bash
python -m server --port 8000 --docker                    # auto_eval=False（默认）
python -m server --port 8000 --docker --auto-eval        # auto_eval=True
```

Server 启动时确定 `auto_eval` 值，应用于所有后续 session。

### 5.2 行为

| auto_eval | send_message 返回 completed 时 |
|-----------|-------------------------------|
| `False`（默认） | 保存 run_state.json + 销毁容器。评分需 client 显式调用 `request_evaluation`（MCP）或 `POST /session/{sid}/evaluate`（REST） |
| `True` | 保存 run_state.json + 销毁容器 + **自动启动后台评分线程**。client 通过 `get_scores` / `GET /session/{sid}/scores` 查询结果 |

### 5.3 为什么不让 client 请求 auto_eval？

- Server 定义了通信格式（PROTOCOL.md），client 按格式发请求
- `register_session` 的参数是 `{task_id}`，这是 protocol 规定的
- 是否自动评估是 Server 运维决策（涉及 LLM 费用、评估模型选择），不属于 benchmark 交互协议
- client 无论如何都可以显式调用 evaluate，auto_eval 只是省一步

### 5.4 实现

`BenchSessionManager` 初始化时接收 `auto_eval` 参数，传递给每个 `SessionState`。
`SessionState.handle_send_message` 检测 `completed` 时的执行顺序：

```python
# handle_send_message 内部（MCP 和 REST 共用）
if data.get("status") == "completed":
    self.phase = SessionPhase.COMPLETED
    self._save_results()           # 1. 保存 run_state.json + 复制 agent_files
    self._destroy_container()      # 2. 销毁 Docker 容器（agent_files 已在磁盘）
    if self.auto_eval:             # 3. 自动评分（读取磁盘上的 agent_files，不依赖容器）
        with self._eval_lock:
            if self._eval_status == "pending":
                self._eval_status = "running"
                threading.Thread(target=self._run_evaluation, daemon=True).start()
```

`_eval_lock` 保护确保：即使 client 同时手动调用 `request_evaluation`，
也不会启动两个评分线程。先到的设置 `"running"`，后到的看到 `"running"` 直接返回等待提示。

---

## 六、并发请求安全

### 6.1 问题

同一个 session 可能被 MCP 和 REST 同时访问（用户拿到 session_id 后可通过
任意协议操作）。`proxy.call_tool` 不是线程安全的——并发执行会导致 tool_logs
和 turn_index 混乱。

### 6.2 解决方案

`SessionState` 新增 `_request_lock: asyncio.Lock`。

**所有写操作（MCP + REST）** 统一加锁：

```python
# MCP handler（_create_mcp_server 内）
@server.call_tool()
async def handle_call_tool(name, arguments):
    async with state._request_lock:
        return await state.handle_tool_call(name, arguments)

# REST handler
async def rest_send(request):
    state = manager.get_session(sid)
    async with state._request_lock:
        result = await asyncio.to_thread(state.handle_send_message, text)
    return JSONResponse(json.loads(result))
```

**开销分析**：
- 纯 MCP：transport 天然串行 → lock 永远无竞争 → 零实际开销
- 纯 REST：REST 写操作互斥
- MCP + REST 混合：lock 正确串行化两种协议的写操作

**只读 GET 请求**（tools/results/scores/status/list）**不获取锁**，安全性分析：

- `GET /results`：读取 `_result_dir / "run_state.json"` 磁盘文件。`_save_results`
  在 `handle_send_message` 的 `to_thread` 中同步完成后才返回，此时 phase 已转为
  COMPLETED，后续 GET 读到的是最终完整文件。不会读到半写状态。
- `GET /scores`：读取 `state._eval_results`（dict）和 `_eval_status`（string）。
  评分线程在 `_eval_lock` 内原子赋值。Python GIL 保证引用赋值原子性。
  最坏情况是读到旧值（`"running"` 而非刚完成的 `"completed"`），下次查询即可见。
- `GET /tools`、`GET /status`：读取 `state.phase` 和 `proxy.get_available_tools()`，
  均为不可变值或快照读取。

**register 不需要锁**——SessionState 创建后尚未加入 `_sessions`，无并发竞争。
register 成功后才将 state 存入 `_sessions`，此后其他请求才能通过 sid 访问到。

---

## 七、共享逻辑

```
REST handler  ──┐
                 ├──→ [Lock（REST写操作）] ──→ check_permission() ──→ SessionState 方法
MCP handler   ──┘                              （同一实例、同一方法）
```

两种协议调用同一个 `SessionState` 实例的同一套方法：
- `state.register(task_id)` → dict
- `state.start()` → dict
- `state.get_visible_tools()` → list[Tool]
- `state.call_domain_tool(name, **kwargs)` → str
- `state.handle_send_message(text)` → str
- `state.request_evaluation()` → dict
- `state.get_eval_scores(history)` → dict（新增）

MCP 的 `get_results` 工具和 REST 的 `GET /results` 端点共用同一个读取逻辑：
直接读取 `_result_dir / "run_state.json"` 并返回内容。无需新增 SessionState 方法。

---

## 八、Session 管理与 Sweeper

### 8.1 Session 存储

`BenchSessionManager._sessions` 存储所有 session（MCP + REST 共用）。

| 类型 | _sessions | _transports | MCP Server | _request_lock |
|------|:---------:|:-----------:|:----------:|:-------------:|
| MCP session | o | o | o | 写操作使用 |
| REST session | o | — | — | 写操作使用 |

REST session 不创建 MCP Server、不创建 Transport、不启动 anyio task。

### 8.2 断连检测

- MCP：持久连接断开 → transport 感知 → `run_server` finally → cleanup
- REST：无持久连接 → sweeper 兜底

### 8.3 Sweeper 策略（Server 侧）

sweeper 定期扫描 `_sessions`，四个清理策略：

| 策略 | 条件 | 动作 |
|------|------|------|
| **Unregistered idle** | UNREGISTERED + idle > 5 min | 移除 session（MCP initialize 后未 register） |
| **Registered idle** | REGISTERED + idle > 5 min | 销毁容器 → 移除 session |
| **Deadline** | IN_SESSION + 超过 `proxy._deadline` | 保存结果 → COMPLETED → 销毁容器 |
| **Completed idle** | COMPLETED + idle > 1 hour | 移除 session（结果已持久化到磁盘） |

**注意**：UNREGISTERED session 只存在于 MCP 路径（`initialize` 时创建但尚未
`register_session`）。REST 路径不会产生 UNREGISTERED session（register 是原子操作）。

COMPLETED session 的结果（run_state.json、evaluations/）已保存在磁盘上，
移除内存中的 SessionState 不影响数据。后续查询可从磁盘读取（当前实现依赖
内存中的 state，移除后需要 fallback 到磁盘读取或返回 404）。

**简化方案**：COMPLETED 移除后，REST `GET /session/{sid}/results` 和
`GET /session/{sid}/scores` 返回 404。用户需在 session 存活期间查询。
这是合理的——benchmark 结果已在 `results/server/{task_id}/{session_id}/` 目录中持久化。

---

## 九、HTTP 状态码规范

REST 端点统一状态码：

| 情况 | 状态码 | 示例 |
|------|--------|------|
| 成功 | 200 | 正常返回 |
| 格式/参数错误 | 400 | 缺 task_id、空 text、session API 工具名走 /tool |
| 权限错误 | 403 | wrong phase（如 IN_SESSION 调 evaluate） |
| 资源不存在 | 404 | session_id 不存在、task_id 找不到、results 未生成 |
| Server 内部错误 | 500 | 容器创建失败、评分流水线异常 |

---

## 十、协议差异说明（PROTOCOL.md 中注明）

| 差异点 | MCP | REST |
|--------|-----|------|
| 返回值封装 | JSON 字符串包裹在 TextContent 中，client 需 `json.loads` | 直接 JSON 对象 |
| DELETE 响应 | 由 MCP 规范决定 | `{"status": "cancelled"}` (HTTP 200) |
| Session 创建时机 | `initialize` 时创建（UNREGISTERED），register 是 tool call | `POST /session/register` 原子创建（直接 REGISTERED） |
| 工具发现推送 | `tools/list_changed` 通知 | 无推送，client 按需 `GET /tools` |
| 错误码 | MCP 协议层面无 HTTP status code，错误在 JSON-RPC error 中 | 标准 HTTP 状态码（§九） |

这些是协议层面的固有差异，不影响 benchmark 结果——相同的 SessionState 方法、
相同的权限检查、相同的评分流水线。

---

## 十一、PROTOCOL.md 更新

重写为双协议格式。每个操作同时列出 MCP 和 REST 调用方式及响应格式。
追加：状态码规范、协议差异说明、`/tool/{name}` 禁止 session API 工具名的规则。

---

## 十二、文件改动清单

| 文件 | 改动 |
|------|------|
| `server/api/protocol.py` | `check_permission` COMPLETED 阶段扩展为 3 个工具；新增 `GET_RESULTS_TOOL`/`GET_SCORES_TOOL` schema；新增 `_COMPLETED_TOOLS` 集合 |
| `server/api/session_api.py` | 新增 `_request_lock: asyncio.Lock`；新增 `auto_eval` 属性；`handle_send_message` 中 auto_eval + `_eval_lock` 触发逻辑；`get_visible_tools` COMPLETED 阶段增加 2 个工具；新增 `get_eval_scores(history)` 方法 |
| `server/api/http_app.py` | 新增 11 个 REST handler + 路由注册；移除旧 `/api/*` 路由；`_ServerApp` 路由不变（REST 走 Starlette）；sweeper 增加 COMPLETED idle 清理策略 |
| `server/__main__.py` | 新增 `--auto-eval` 启动参数，传递给 `create_app` → `BenchSessionManager` |
| `spec/PROTOCOL.md` | 重写为双协议格式 |

---

## 十三、各协议优势发挥

| 特性 | MCP | REST |
|------|-----|------|
| 工具发现 | `list_tools` 原生 + `tools/list_changed` 主动通知 | `GET /session/{sid}/tools` 按需查询；phase 转换从响应语义判断 |
| 请求格式 | JSON-RPC envelope | 纯 JSON body（curl 直接可用） |
| Session 管理 | `Mcp-Session-Id` header 自动 | `session_id` 在 URL 路径中，显式控制 |
| 工具调用 | `tools/call` 统一入口 | `POST /session/{sid}/tool/{name}` 独立 URL，body 即参数 |
| 取消 | `DELETE /mcp` + header | `DELETE /session/{sid}` |
| 连接模型 | 持久 SSE，适合 server push | 无状态 HTTP，适合简单集成 |
| 并发安全 | `_request_lock`（transport 额外保证串行） | `_request_lock` 保护写操作 |
| 断连检测 | transport 感知 + sweeper | sweeper idle timeout 兜底 |

---

## 十四、分批实施计划

分 3 批对话实施。每批结束时可独立验证、独立运行。

### Batch 1：Server 核心改造

改动 `protocol.py`、`session_api.py`、`http_app.py`、`__main__.py`。
完成后 MCP 功能增强（COMPLETED 阶段新工具 + auto_eval + 通用锁）+ REST 全部端点可用。

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1.1 | `protocol.py` | `_COMPLETED_TOOLS` 集合；`check_permission` COMPLETED 扩展；`GET_RESULTS_TOOL`/`GET_SCORES_TOOL` schema；`_TOOL_ENDPOINT_BLOCKED` 集合 |
| 1.2 | `session_api.py` | `_request_lock: asyncio.Lock`；`auto_eval` 属性；`handle_send_message` auto_eval 触发；`get_visible_tools` COMPLETED 增加 2 工具；`get_eval_scores(history)` 方法；MCP `handle_tool_call` 加锁 + 路由 get_results/get_scores |
| 1.3 | `http_app.py` | 11 个 REST handler + 路由注册（注意注册顺序）；移除 `/api/*`；MCP `_create_mcp_server` call_tool 加 `_request_lock`；sweeper 增加 UNREGISTERED idle + COMPLETED idle 策略 |
| 1.4 | `__main__.py` | `--auto-eval` 参数，传递给 `create_app` |

**验证**：
- MCP 路径不变（现有测试通过）
- REST curl 调用 register → start → tools → tool/shell_exec → send → evaluate → results → scores → delete
- MCP + REST 混合访问同一 session 不 crash

### Batch 2：PROTOCOL.md 重写

| 步骤 | 文件 | 内容 |
|------|------|------|
| 2.1 | `spec/PROTOCOL.md` | 重写为双协议格式：每个操作列 MCP + REST；状态码规范；协议差异说明；`/tool/{name}` 禁止规则 |

**验证**：文档覆盖所有端点、所有状态码、所有边界情况。

### Batch 3：Client 适配 + E2E 验证

| 步骤 | 文件 | 内容 |
|------|------|------|
| 3.1 | `client/runner.py` | 移除 auto_eval 逻辑（已迁移到 server 侧）；移除 `--auto-eval` CLI flag |
| 3.2 | E2E | MCP 路径：`python -m client --server .../mcp --task X01`；REST 路径：curl 脚本走完整流程；验证两种路径产出相同的 run_state.json 和 scores |

### 批次依赖

```
Batch 1（Server 核心）
    │
    ├──→ Batch 2（文档）
    │
    └──→ Batch 3（Client + E2E）
```

Batch 2 和 Batch 3 可并行。Batch 1 是前置条件。
