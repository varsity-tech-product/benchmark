# Run 架构执行方案

> 日期：2026-04-16（初稿）→ 2026-04-16（v6 终版）
> 目标：为 QuantTutorBench 引入 Run 层，支持外部 agent 通过 public label 参加 benchmark（场景 1）和 Web UI 发起测试（场景 2）。

---

## 1. 为什么需要 Run

当前架构是 **client-driven**：client 知道完整 `task_id`（如 `X01_ma_offbyone`），自行决定跑什么任务。`SessionState` 是唯一的顶层对象。

**场景 1：外部 agent 参加 benchmark**
完整 task_id 泄漏任务内容。需要只给 public label（`X01`），由 server 内部解析。Client 知道有哪些任务可选（label + category），Client 决定跑哪个任务，server 负责注册。

**场景 2：Web UI 发起测试**
UI 点击"测试 D01"→ 后端生成 token → 页面显示连接信息 → client 连上执行。需要一个"已创建但还没开始"的中间状态。

```text
RunAssignment (Control Plane)      SessionState (Protocol Plane)
─────────────────────────────      ───────────────────────────────
谁来考、考什么、token 绑定          怎么考、对话管理、工具执行
run_id, public_label, token        session_id, conversation, proxy
status: waiting → active → done    phase: UNREGISTERED → COMPLETED
```

---

## 2. 全局架构

### 2.1 接入路径总览

```text
                    ┌─ Web UI 点击 ──── POST /ui/runs ──────────┐
                    │                                            │
创建 Run ──────────┤─ CLI 命令 ─────── POST /client/runs/start ──├──→ token + mcp_url
                    │                                            │
                    └─ MCP Skill/平台 ─ POST /client/runs/start ─┘

                    ┌─ 用户自己的 MCP agent ──┐
                    │                          │
连接 Session ──────┤─ 用户自己的 REST client ──├──→ register → start → 对话 → 评估
                    │                          │
                    └─ 我们的 baseline client ─┘
```

上半部分（创建 Run）和下半部分（连接 Session）**完全解耦**。

### 2.2 双协议边界

```text
时间线：
  REST: 创建 run / claim ──→  MCP 或 REST: register → start → 对话 ──→  REST: 查看结果
  ◄── Run 操作 (REST) ──►     ◄──── Session 操作 (MCP + REST) ────►
```

- **Run 层（Control Plane）**：REST only。
- **Session 层（Protocol Plane）**：MCP + REST 双协议。已实现。

### 2.3 Token 是连接凭证

所有执行都经过 Run 层，都需要 token。Token 把"创建 Run"和"执行 Session"绑定起来。

- **My Agent（UI 发起）**：token 是浏览器和 agent 的桥梁，用户手动复制
- **Client 直接发起**：token 自动生成自动消费，用户不感知
- **MCP Skill/平台接入**：token 由平台自动获取并传递

### 2.4 Token 验证统一入口

MCP 和 REST 使用相同的 token 验证逻辑：

```python
def extract_bearer_token(request: Request) -> str | None:
    """从 Authorization: Bearer <token> header 提取 token。"""

def resolve_run_from_token(run_service: RunService, raw_token: str) -> RunAssignment:
    """hash(token) → 查找 RunAssignment → 验证状态和过期时间。"""
```

两个入口点调用同一对函数：
- `/mcp` POST（新连接）→ `extract_bearer_token` → `resolve_run_from_token` → 设置 `SessionState.run_id`
- `/session/register` POST → `extract_bearer_token` → `resolve_run_from_token` → 设置 `SessionState.run_id` + 读取 `run.task_id`

无 token → HTTP 401。Token 无效或过期 → HTTP 401。Run 状态不是 `claimed` → HTTP 409。

---

## 3. 用户使用流程

### 3.1 网站首页

用户看到的**不是任务详情**，只是分类和编号：

```text
Data Analysis (D01–D11)  │  Strategy (S01–S06)  │  Debug (X01–X10)
Implementation (I01–I10) │  Backtest (B01–B06)  │  End-to-End (E01–E05)
Adversarial (A01–A17)
```

三种使用方式：

| 按钮 | 用户意图 | 需要 Run 层？ |
|------|---------|-------------|
| **My Agent** | 用自己的 agent 测试 | 是 |
| **Try it myself** | 在浏览器里手动辅导 | 否（直接 REST session） |
| **Watch Baseline** | 看 baseline agent 跑一遍 | 是（server 启动 client） |

### 3.2 "My Agent" 流程

```text
1. 用户点击 "My Agent" → 前端调用 POST /ui/runs {task: "D01"}
2. 页面显示连接信息：

   ┌─ MCP ──────────────────────────────────────────────────┐
   │ Server URL:  https://quanttutorbench.com/mcp           │
   │ Auth Token:  qtb_xYz123aBcDeF...                  [📋] │
   └────────────────────────────────────────────────────────┘
   ┌─ REST ─────────────────────────────────────────────────┐
   │ Base URL:    https://quanttutorbench.com               │
   │ Run Token:   qtb_xYz123aBcDeF...                  [📋] │
   │ See spec/PROTOCOL.md for API details.                  │
   └────────────────────────────────────────────────────────┘

3. 用户配置 agent → agent 连接 → 页面实时显示
4. 完成 → 跳转 Results │ 任何阶段可 Cancel
```

MCP-native agent 零代码对接：
```json
{
  "mcpServers": {
    "quanttutorbench": {
      "url": "https://quanttutorbench.com/mcp",
      "headers": {"Authorization": "Bearer qtb_xYz123..."}
    }
  }
}
```

### 3.3 本地开源用户

```bash
python -m server --port 8000 --no-docker
python -m client run --server http://localhost:8000 --task D01
```

### 3.4 MCP Skill / 平台接入（绕过 Web）

```text
Skill: POST /client/runs/start {task: "D01"} → token + mcp_url
Agent: 连接 mcp_url (Bearer <token>) → 自动发现 tools → 自主完成
```

---

## 4. 当前完成度

### 4.1 已完成

| 能力 | 位置 |
|------|------|
| Server 双协议（MCP + REST，10 个 REST endpoints） | `http_app.py` |
| Session 完整生命周期 | `session_api.py` |
| Domain tools, Student simulator, TC/Goal checker | `core/` |
| 评估 pipeline | `eval/` |
| Results 可视化（6 个 `/ui/results/*` endpoints + 完整前端） | `web/` |
| Human Test 前端 | `app.js:717-835` |
| Cancel (session 级, `DELETE /session/{sid}` + `_cancel_event`) | `http_app.py:828` |
| 协议文档 | `spec/PROTOCOL.md`, `spec/TASKS.md` |
| Baseline client + 批量执行 | `client/` |
| Sweeper + Session 恢复 | `http_app.py`, `session_api.py` |

### 4.2 部分完成

| 能力 | 差什么 |
|------|--------|
| Public label | Server `register_session` 不接受 `D01`（spec/TASKS.md 承诺未兑现） |
| Agent Test 前端 | 骨架存在但禁用 |
| conftest.py | 过时 `ui_app` stub 未移除 |

### 4.3 完全缺失

TaskCatalog, Run 层全部类, Run REST API, `/live` endpoint, Run 级 cancel, MCP Authorization 解析, `run_id` 写入 `run_state.json`, Client attach/run + Transport 抽象, Server baseline 自动启动, Run 前端页面。

---

## 5. 设计原则

1. **Session 不改，Run 在外面包。** `SessionState` 只新增 `run_id` 字段。
2. **实时数据读内存。** 不建事件系统，`/live` 直接读 `MCPProxy._logs` + `session.conversation`。
3. **所有执行经过 Run 层。** 删除旧 `--task` 入口，无绕过路径。
4. **任何时刻可取消。** `cancel_run()` 串联 `_cancel_event` + `cleanup(persist_partial=True)`。
5. **Token 验证统一。** MCP `/mcp` 和 REST `/session/register` 使用相同的 `extract_bearer_token()` + `resolve_run_from_token()`。

---

## 6. 目标架构

### 6.1 TaskCatalog

```python
class TaskCatalog:
    """rglob('*.json') 扫描 bench/tasks/，建立 public_label -> task_id 映射。"""
    def resolve(self, label_or_id: str) -> TaskEntry | None: ...
    def list_public(self) -> list[dict]: ...

@dataclass
class TaskEntry:
    public_label: str       # "D01"
    task_id: str            # "D01_load_inspect_ohlcv"
    category: str
    difficulty: str
    persona_ids: list[str]
    max_turns: int
    timeout_minutes: int
    source_path: Path
```

提取规则：`re.match(r'^([A-Z]\d{2})_', task_id)`。冲突 label 启动时 fail fast。

### 6.2 RunAssignment

```python
class RunStatus(str, Enum):
    WAITING   = "waiting"
    CLAIMED   = "claimed"
    ACTIVE    = "active"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"

@dataclass
class RunAssignment:
    run_id: str                        # "run_{uuid4().hex}"
    mode: str                          # "agent"
    public_task_label: str             # "D01"
    task_id: str                       # "D01_load_inspect_ohlcv" (内部)
    status: RunStatus
    token_hint: str = ""               # token 前 12 位
    token_hash: str = ""               # SHA-256 hash
    token_expires_at: str = ""         # ISO 8601
    client_info: dict | None = None
    session_id: str | None = None
    persona_policy: str = "auto"
    persona_id: str | None = None
    result_dir: str | None = None
    error: str | None = None
    eval_status: str = "pending"
    created_at: str = ""
    updated_at: str = ""
    claimed_at: str | None = None
    completed_at: str | None = None
```

### 6.3 RunStore

`bench/results/runs/{run_id}/run.json`。内部 `threading.Lock()` 保护写操作。

```python
class RunStore:
    def save(self, assignment: RunAssignment) -> None: ...
    def get(self, run_id: str) -> RunAssignment | None: ...
    def find_by_token_hash(self, token_hash: str) -> RunAssignment | None: ...
    def list_runs(self, status: RunStatus | None = None) -> list[RunAssignment]: ...
```

### 6.4 RunService

```python
class RunService:
    def create_run(self, task, mode, persona_policy, token_ttl_minutes) -> (RunAssignment, str): ...
    def create_and_claim(self, task, client_info, mode, persona_policy) -> (RunAssignment, str): ...
    def claim_run(self, raw_token, client_info) -> RunAssignment: ...
    def bind_session(self, run_id, session_id) -> RunAssignment: ...
    def mark_completed(self, run_id, result_dir) -> RunAssignment: ...
    def mark_failed(self, run_id, error) -> RunAssignment: ...
    def cancel_run(self, run_id) -> RunAssignment: ...
    def get_run(self, run_id) -> RunAssignment | None: ...
    def list_runs(self, status) -> list[RunAssignment]: ...
```

Token：`qtb_{secrets.token_urlsafe(24)}`，存 SHA-256 hash，明文只返回一次。

### 6.5 Session 绑定

```text
路径 A（UI → client attach）：
  UI:     POST /ui/runs {task:"D01"} → run_id + token
  Client: POST /client/runs/claim {token} → run_id + mcp_url
  Client: 连接 /mcp (Authorization: Bearer <token>)
  Server: resolve_run_from_token → SessionState.run_id = run.run_id
  Client: register_session() → server 读取 run.task_id

路径 B（client 直接发起）：
  Client: POST /client/runs/start {task:"D01"} → run_id + token + mcp_url
          （create_and_claim 一步完成，后续同路径 A）
```

### 6.6 MCP Token Binding 详细流程

```python
async def handle_mcp_request(self, scope, receive, send):
    request = Request(scope, receive)

    # 新连接（POST，无 mcp-session-id）
    if request.method == "POST" and not session_id:
        raw_token = extract_bearer_token(request)
        if not raw_token:
            return Response(status_code=401, content="Authorization required")

        run = resolve_run_from_token(self._run_service, raw_token)
        if not run:
            return Response(status_code=401, content="Invalid or expired token")
        if run.status != RunStatus.CLAIMED:
            return Response(status_code=409, content="Run not in claimed state")

        # 创建 session，绑定 run
        state = SessionState(session_id=new_id, ...)
        state.run_id = run.run_id
        self._sessions[new_id] = state
        # ... 创建 MCP server + transport（现有逻辑）

    # 已有连接（PUT/GET/DELETE，有 mcp-session-id）
    # ... 现有逻辑不变
```

### 6.7 REST register Token Binding

```python
async def rest_register(request: Request) -> JSONResponse:
    raw_token = extract_bearer_token(request)
    if not raw_token:
        return JSONResponse({"error": "Authorization required"}, 401)

    run = resolve_run_from_token(manager._run_service, raw_token)
    if not run:
        return JSONResponse({"error": "Invalid or expired token"}, 401)
    if run.status != RunStatus.CLAIMED:
        return JSONResponse({"error": "Run not in claimed state"}, 409)

    body = await request.json()
    persona_id = body.get("persona_id")  # task_id 从 run 读取，不从 body

    state = manager.create_rest_session()
    state.run_id = run.run_id
    result = await asyncio.to_thread(state.register, run.task_id, persona_id)

    if "session_id" in result:
        manager.register_rest_session(state)
        manager._run_service.bind_session(run.run_id, state.session_id)
    return JSONResponse(result)
```

### 6.8 register_session MCP Tool Schema 变更

`task_id` 从 required 改为 optional。Run-bound 模式下 server 从 RunAssignment 读取 task_id，client 传入的 task_id 被忽略。

```python
REGISTER_SESSION_TOOL = Tool(
    name="register_session",
    description=(
        "Register a task for benchmarking. "
        "When connected via run token, task_id is optional "
        "(server resolves from the run assignment). "
        "When provided, server validates it matches the assignment."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Task identifier (e.g. D01). Optional when connected via run token.",
            },
            "persona_id": {
                "type": "string",
                "description": "Optional explicit student persona ID.",
            },
        },
        "required": [],  # 改为空：Run-bound 模式下 task_id 不需要传
    },
)
```

`handle_tool_call` 中 `register_session` 路由逻辑：
```python
if name == "register_session":
    task_id = arguments.get("task_id", "")
    if self.run_id:
        # Run-bound：从 RunAssignment 读取 task_id
        run = run_service.get_run(self.run_id)
        task_id = run.task_id
    if not task_id:
        return error("task_id required when not connected via run token")
    result = await asyncio.to_thread(self.register, task_id, persona_id)
    if "session_id" in result:
        run_service.bind_session(self.run_id, self.session_id)
    ...
```

### 6.9 Cancel 详细流程

```python
# BenchSessionManager
async def cancel_run(self, run_id: str):
    run = self._run_service.get_run(run_id)

    if run.status == RunStatus.ACTIVE and run.session_id:
        state = self.get_session(run.session_id)
        if state and state.proxy:
            state.proxy._cancel_event.set()           # 立即阻止下一个 tool call
        await self._cleanup_session(                  # 保存部分结果 + 销毁容器
            run.session_id, persist_partial=True
        )

    self._run_service.cancel_run(run_id)              # 更新 run.json → cancelled
```

执行顺序：先 `_cancel_event.set()`（同步立即），再 cleanup（async），最后更新 run。

### 6.10 Sweeper 扩展：Run 级超时

在现有 `_session_sweeper` 循环中增加 run 级检查：

```python
async def _session_sweeper(self):
    while True:
        await anyio.sleep(_SWEEPER_INTERVAL)
        now = time.time()

        # ... 现有 session 清理逻辑 ...

        # Run 级超时
        for run in self._run_service.list_runs():
            if run.status == RunStatus.WAITING and token_expired(run.token_expires_at):
                self._run_service.mark_failed(run.run_id, "Token expired")

            elif run.status == RunStatus.CLAIMED and run.claimed_at:
                claimed_age = now - parse_iso(run.claimed_at)
                if claimed_age > _CLAIMED_IDLE_TIMEOUT:      # 5 min
                    self._run_service.mark_failed(run.run_id, "Client did not connect")
```

### 6.11 session_id suffix 处理

`_storage_session_id()` 在 `run_id` 存在时返回裸 UUID：

```python
def _storage_session_id(self) -> str:
    if self.run_id:
        return self.session_id          # 有 run_id 作为索引，不需要 suffix
    prefix = self.task_id.split("_")[0] if self.task_id else ""
    return f"{self.session_id}_{prefix}" if prefix else self.session_id
```

### 6.12 实时数据 endpoint

```python
async def get_run_live(request: Request) -> JSONResponse:
    run = run_service.get_run(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, 404)

    if run.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        return JSONResponse({"run_status": run.status.value, "session_phase": None})

    if run.status != RunStatus.ACTIVE or not run.session_id:
        return JSONResponse({"run_status": run.status.value, "session_phase": None})

    session = manager.get_session(run.session_id)
    if not session or not session.session:
        return JSONResponse({"run_status": run.status.value, "session_phase": session.phase.value if session else None})

    return JSONResponse({
        "run_status": run.status.value,
        "session_phase": session.phase.value,
        "turn": session.session.turn,
        "conversation": session.session.conversation,
        "recent_tool_logs": [asdict(l) for l in session.proxy.get_logs()[-20:]],
    })
```

前端在 `run_status` 为终态时停止轮询，显示 completed/failed/cancelled + Results 链接。

---

## 7. Client 设计

### 7.1 两条命令（删除旧入口）

现有 `python -m client --server .../mcp --task X01_ma_offbyone` **删除**。

**`attach`**：连接已有 run（来自网站/curl/Skill）

```bash
python -m client attach --server http://localhost:8000 --run-token qtb_xYz123...
```

```text
POST /client/runs/claim {token, client_info} → run_id, mcp_url
连接 mcp_url (Authorization: Bearer <token>)
register_session() → server 从 run 读取 task_id
start_session() → adapter.generate_response() → save_client_trace
```

**`run`**：创建 + 连接一步完成（**开箱即用**入口）

```bash
python -m client run --server http://localhost:8000 --task D01
python -m client run --server http://localhost:8000 --tasks D01 D02 X01 --workers 3
```

```text
POST /client/runs/start {task, client_info} → run_id, token, mcp_url
attach(token) → 同上
```

### 7.2 Transport 抽象

```text
client/transports/
├── base.py              # SessionTransport ABC
├── mcp_transport.py     # MCP：streamablehttp_client + tool calls
└── rest_transport.py    # REST：HTTP /session/* endpoints
```

```python
class SessionTransport(ABC):
    async def connect(self, url: str, headers: dict | None = None) -> None: ...
    async def register_session(self, task_id: str = "") -> dict: ...
    async def start_session(self) -> dict: ...
    async def list_tools(self) -> list[dict]: ...
    async def call_tool(self, name: str, arguments: dict) -> str: ...
    async def send_message(self, text: str, attachments: list = []) -> dict: ...
    async def close(self) -> None: ...
```

`--protocol mcp` (默认) 或 `--protocol rest`。

### 7.3 本地测试矩阵

```bash
python -m server --port 8000 --no-docker

# run + MCP
python -m client run --server http://localhost:8000 --task D01
# run + REST
python -m client run --server http://localhost:8000 --task D01 --protocol rest
# attach + MCP
curl -X POST http://localhost:8000/ui/runs -d '{"task":"D01"}' -H 'Content-Type: application/json'
python -m client attach --server http://localhost:8000 --run-token qtb_xxx
# attach + REST
python -m client attach --server http://localhost:8000 --run-token qtb_xxx --protocol rest
```

---

## 8. REST API 规格

### 8.1 UI/Admin

| Endpoint | 说明 |
|----------|------|
| `GET /ui/tasks/catalog` | `[{label, category, difficulty}]`，不含内部信息 |
| `POST /ui/runs` | 创建 run → `{run_id, token, mcp_url, launch_command}` |
| `GET /ui/runs[?status=&task=]` | 列出 runs |
| `GET /ui/runs/{run_id}` | 查询状态（不返回 token、task_id） |
| `GET /ui/runs/{run_id}/live` | 实时 conversation + tool_logs |
| `POST /ui/runs/{run_id}/cancel` | 取消（任何非终态） |

### 8.2 Client

| Endpoint | 说明 |
|----------|------|
| `POST /client/runs/claim` | `{run_token, client}` → `{run_id, mcp_url, public_task_label}` |
| `POST /client/runs/start` | `{task, client}` → `{run_id, token, mcp_url}` |
| `POST /client/runs/{run_id}/trace` | 可选上传 client trace |

### 8.3 Session（已有，token 验证新增）

| Endpoint | 变更 |
|----------|------|
| `POST /session/register` | 新增：要求 `Authorization: Bearer <token>`，`task_id` 从 run 读取 |
| 其他 `/session/*` | 不变（已通过 session_id 隔离） |

---

## 9. 前端架构

### 9.1 现有结构

```text
bench/server/web/
├── templates/index.html           # 32 行
├── static/css/styles.css          # 2514 行
├── static/js/
│   ├── app.js                     # 1765 行
│   ├── chat.js                    # 571 行（对话渲染）
│   ├── render.js                  # 82 行（Markdown/KaTeX）
│   └── tools.js                   # 274 行（tool 面板）
├── ui_app.py                      # 97 行（路由工厂）
└── ui_indexer.py                  # 965 行（ResultIndexer）
```

### 9.2 改造

| 按钮 | 实现 |
|------|------|
| **My Agent** | 新增 `run-agent.js`：创建 run → 连接信息 → 轮询 `/live` → Cancel → Results |
| **Try it myself** | 复用现有 `renderHumanRunPage()` |
| **Watch Baseline** | 复用 `run-agent.js`（只读模式，无连接信息） |

Vanilla JS，不引入框架。`chat.js` / `tools.js` 作为渲染组件复用。

---

## 10. 结果存储

`save_run_state()` 新增 `run_id` + `public_task_label` 参数。`ResultIndexer` 保留旧兼容，新增 `run_id` 索引。

---

## 11. 测试适配

### 11.1 测试 helpers 改造

`tests/helpers.py` 的 `register_session()` 改为先创建 run + claim，再带 token 注册：

```python
async def register_session(client, task_id=DEFAULT_TASK_ID, persona_id=DEFAULT_PERSONA_ID):
    # 1. 创建 run + claim
    resp = await client.post("/client/runs/start", json={"task": task_id})
    assert resp.status_code == 200
    data = resp.json()
    token = data["token"]

    # 2. 带 token 注册 session
    resp = await client.post(
        "/session/register",
        json={"persona_id": persona_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()["session_id"]
```

现有测试调用 `register_session()` 的地方**不需要逐个改**——helper 内部切换了流程。

### 11.2 conftest.py 变更

- 移除过时的 `server.web.ui_app` stub（行 82-97）
- `app` fixture 的 `create_app()` 调用不变（RunService 在 app 初始化时自动创建）

---

## 12. 分阶段执行计划

### Phase 1：后端核心

**目标**：TaskCatalog + Run 层 + REST API + Token 验证 + Cancel 链路。curl 可验证全部操作。

**新增文件**：
```text
bench/server/run/
  __init__.py
  catalog.py
  models.py
  store.py
  service.py
```

**修改文件**：

| 文件 | 改动 |
|------|------|
| `http_app.py` | BenchSessionManager 持有 RunService + TaskCatalog；`handle_mcp_request` token 验证（6.6）；`cancel_run()` 串联（6.9）；sweeper run 级超时（6.10） |
| `session_api.py` | 新增 `run_id` 字段；`register()` Run-bound 路径；`_storage_session_id()` 改为 run_id 存在时裸 UUID（6.11） |
| `protocol.py` | `REGISTER_SESSION_TOOL` 的 `required` 改为 `[]`，description 更新（6.8） |
| `result_writer.py` | `save_run_state()` 新增 `run_id` + `public_task_label` |
| `ui_app.py` | 新增 9 个 REST endpoints；`rest_register()` token 验证（6.7）；新增 `extract_bearer_token()` / `resolve_run_from_token()` 共用函数（2.4） |
| `conftest.py` | 移除过时 `ui_app` stub |
| `tests/helpers.py` | `register_session()` 改为先创建 run 再注册（11.1） |

**验证**：
- `POST /ui/runs {task: "D01"}` → run_id + token
- `POST /client/runs/claim {token}` → claimed
- `POST /client/runs/start {task: "D01"}` → create + claim
- MCP 带 Authorization → SessionState.run_id 被设置
- MCP 无 Authorization → 401
- REST `/session/register` 带 Authorization → 正常注册，task_id 从 run 读取
- REST `/session/register` 无 Authorization → 401
- Run-bound `register_session()` MCP tool 无需传 task_id
- Cancel 在 waiting/claimed/active 都成功
- Active cancel：`_cancel_event` 生效 + partial results 保存
- `/live` active 时返回 conversation + tool_logs
- `/live` 终态返回 `run_status` + `session_phase: null`
- 并发 claim 同一 token 只有一个成功
- 过期 token → 401
- Claimed 超时 5 分钟 → sweeper 标为 failed
- Waiting + token 过期 → sweeper 标为 failed
- 新 `run_state.json` 含 `run_id` + `public_task_label`
- 现有测试适配后全部通过

### Phase 2：Client 重构 + Run 前端

**Client**：

| 文件 | 改动 |
|------|------|
| `client/__main__.py` | 删除旧入口，`attach` + `run` 子命令，`--protocol mcp\|rest` |
| `client/runner.py` | 重构为 SessionTransport 接口 |
| 新增 `client/transports/{base,mcp_transport,rest_transport}.py` | Transport 抽象 |

**前端**：

| 文件 | 改动 |
|------|------|
| `app.js` | 三按钮布局 |
| 新增 `run-agent.js` | My Agent 流程 |
| `styles.css` | 连接信息面板样式 |
| `spec/PROTOCOL.md` | Run 层协议文档 |
| `ui_indexer.py` | run_id 索引 |

**验证**：4 种组合（attach/run × mcp/rest）+ 批量执行 + 前端 My Agent 完整流程。

### Phase 3：Human Test 完善 + 一键 Baseline

| 文件 | 改动 |
|------|------|
| `app.js` | Try it myself 整合 |
| `run-agent.js` | Watch Baseline 只读模式 |
| `http_app.py` / `service.py` | `auto_baseline=true` spawn client |

---

## 13. 反模式

- 不要让浏览器启动 subprocess
- 不要让前端知道完整 task_id
- 不要新建事件系统
- 不要把 Run 操作塞进 MCP tool system
- 不要保留绕过 Run 层的路径
- 不要引入前端框架
- 不要混淆 token 和 auth（token 是连接凭证，rate-limit 独立）
