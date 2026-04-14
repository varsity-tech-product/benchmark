# Exam Server 传输层方案对比：Stdio vs HTTP

> 日期：2026-04-09
> 前置：exam server (stdio) 已实现并通过 E2E 验证
> 目标：对比两种传输层方案，为 baseline 并行执行和未来产品化选择合适路径

---

## 一、方案概述

### 路径 A：多 stdio 子进程

核心思路：**每个 task 独立启动一个 server 子进程，Client 主进程通过 ThreadPool 并行管理多个 task。**

```
Client 主进程 (exam.baseline)
├─ ThreadPoolExecutor(workers=3)
│
├─ Worker 1: ─── stdio ──→ Server 子进程 1 (--task S01, Docker 容器 A)
├─ Worker 2: ─── stdio ──→ Server 子进程 2 (--task D01, Docker 容器 B)
└─ Worker 3: ─── stdio ──→ Server 子进程 3 (--task X01, Docker 容器 C)

每个 Worker 内部:
  subprocess 启动 server → MCP 连接 → adapter.generate_response() → session 完成 → server 保存结果 → 子进程退出
```

### 路径 B：HTTP 服务

核心思路：**将 Exam Server 从 stdio 传输升级为 HTTP 传输，支持多 session 并行、独立进程部署。**

```
Server 启动一次 → 监听端口 → 多个 Client 连接 → 每个 Client 一个 session → 各自独立运行
```

路径 A（多 stdio 子进程）的本质问题：**Client 必须是 Server 的父进程**。这意味着：
- 不能独立部署——Client 和 Server 必须在同一台机器
- 不能持久化——每个 task 启动/销毁一个 server 进程
- 不能共享——N 个 task 需要 N 个 server 进程
- 不符合 "Server 是考场" 的隐喻——考场应该先开门，考生后进来

---

## 二、架构对比

### 2.1 路径 A 架构（Stdio）

隔离模型——不仅是对象隔离，而是**进程隔离**：

| 隔离维度 | Legacy（线程隔离） | 新架构（进程隔离） |
|---------|-------------------|-------------------|
| Agent 实例 | 每个线程新建 adapter 实例 | 每个 worker 线程新建 adapter 实例 |
| Server 实例 | 共用一个 orchestrator 进程 | **每个 task 独立的 server 子进程** |
| Docker 容器 | 每个 job 独立容器 | 每个 server 子进程独立容器 |
| MCPProxy | 每个 job 独立 proxy 实例（同进程） | 每个 server 子进程独立 proxy（跨进程） |
| 内存空间 | 共享进程内存，靠对象隔离 | **独立进程内存，OS 级隔离** |
| 崩溃影响 | 线程异常不影响其他线程 | **子进程崩溃完全不影响其他 worker** |

### 2.2 路径 B 架构（HTTP）

```
┌─ Exam HTTP Server（一个持久进程）──────────────────────────────┐
│                                                                │
│  uvicorn (ASGI server)                                         │
│    ├─ Starlette app                                            │
│    │   ├─ Route("/mcp") → StreamableHTTPSessionManager         │
│    │   │   ├─ Session A: MCPServer + Proxy + TutoringSession   │
│    │   │   │   └─ Docker 容器 A (S01_ma_crossover)             │
│    │   │   ├─ Session B: MCPServer + Proxy + TutoringSession   │
│    │   │   │   └─ Docker 容器 B (D01_load_inspect)             │
│    │   │   └─ Session C: MCPServer + Proxy + TutoringSession   │
│    │   │       └─ Docker 容器 C (X01_ma_offbyone)              │
│    │   │                                                       │
│    │   ├─ Route("/api/tasks") → 任务列表 (REST)                │
│    │   ├─ Route("/api/sessions") → session 状态查询 (REST)     │
│    │   └─ Route("/api/results/{session_id}") → 结果查询 (REST) │
│    │                                                           │
│    └─ 端口: localhost:8000                                      │
│                                                                │
└────────────────────────────────────────────────────────────────┘

Client 1 ── HTTP ──→ Session A (S01 + intermediate_developer + Sonnet)
Client 2 ── HTTP ──→ Session B (D01 + beginner_no_finance + Haiku)
Client 3 ── HTTP ──→ Session C (X01 + advanced_quant + Sonnet)
```

两种 API 共存：

| API | 协议 | 用途 |
|-----|------|------|
| `/mcp` | MCP over HTTP (StreamableHTTP) | Agent 交互——调工具、send_message、session 管理 |
| `/api/*` | 普通 REST | 管理操作——查看任务列表、session 状态、结果查询 |

### 2.3 MCP HTTP 传输层

MCP SDK 已内置完整的 HTTP 传输层：

| 组件 | 文件 | 说明 |
|------|------|------|
| `StreamableHTTPServerTransport` | mcp/server/streamable_http.py | 单个 session 的 HTTP 传输 |
| `StreamableHTTPSessionManager` | mcp/server/streamable_http_manager.py | 多 session 路由和管理 |
| `streamable_http_client()` | mcp/client/streamable_http.py | Client 端异步连接 |
| Starlette ASGI 集成 | 内置 | 直接产出 Starlette app |

依赖均已安装：starlette 0.52.1、uvicorn 0.41.0、httpx、sse-starlette。

HTTP 传输工作方式：

```
Client                                Server (HTTP)
  │                                     │
  ├─ POST /mcp (initialize)             │
  │   无 session-id header              │
  │                                     ├─ 生成 UUID session-id
  │                                     ├─ 创建 StreamableHTTPServerTransport
  │                                     ├─ 启动 MCP Server 后台任务
  │   ← 200 + mcp-session-id header ───┤
  │                                     │
  ├─ POST /mcp (call_tool)             │
  │   mcp-session-id: abc123           │
  │                                     ├─ 路由到 session abc123 的 transport
  │   ← SSE stream (tool result) ──────┤
  │                                     │
  ├─ DELETE /mcp                        │
  │   mcp-session-id: abc123           │
  │                                     ├─ 终止 session → 保存结果 → 清理容器
  │   ← 200 ───────────────────────────┤
```

---

## 三、Session 生命周期对比

### 3.1 路径 A（Stdio）

```
1. Client 构建 server 启动命令
   server_cmd = [python, -m, exam, --task, S01, --persona, intermediate]
2. MCP stdio_client 自动管理 subprocess 生命周期
3. register_session（agent_name, model, max_steps）
4. 获取工具 + system prompt + session info
5. adapter.generate_response() 循环
6. stdio_client 退出 → server 子进程收到 EOF → 保存结果 → 退出
```

### 3.2 路径 B（HTTP）

```
Phase 0: Server 启动
  uvicorn exam_http:app --port 8000
  → Server 持久运行，等待连接

Phase 1: Client 连接 + Session 创建
  Client POST /mcp (initialize)
  → SessionManager 创建新 session
  → 返回 mcp-session-id

Phase 2: 任务注册
  Client: call_tool("register_session", {task_id, persona_id, agent_name, model, max_steps})
  → Server: 加载 task/persona → 创建 Docker → 注册工具 → 返回 accepted
  （注意：task_id 和 persona_id 在注册时提交，不再是 server 启动参数）

Phase 3: 对话
  Client: call_tool("get_session_info") → call_tool("shell_exec") → ... → call_tool("send_message")
  → Server: 完整的 TutoringSession 流程（与 stdio 版本完全相同）

Phase 4: 结束
  session.done = True → Server 自动保存 run_state.json
  Client: DELETE /mcp（或断开连接）
  → Server: 销毁 Docker 容器 → 清理临时文件

Phase 5: 结果查询（可选）
  Client 或管理员: GET /api/results/{session_id}
  → 返回评分结果
```

### 3.3 关键差异

| 维度 | stdio 版本 | HTTP 版本 |
|------|-----------|----------|
| Server 启动 | 每个 task 一个子进程 | 一个持久进程 |
| task_id 指定 | Server 启动参数 | Client 在 register_session 时提交 |
| 多 session | 不支持 | SessionManager 自动管理 |
| Client 启动方式 | subprocess.Popen(server) | HTTP 连接 localhost:8000 |
| 连接建立 | 管道自动创建 | Client 发 POST /mcp |

---

## 四、核心实现

### 4.1 路径 A：单 Worker 完整生命周期

```python
async def run_single_exam_async(task_id, persona_id, model, use_docker, auto_eval):
    """一个 worker 的完整流程——启动 server 子进程 + MCP 通信 + adapter 运行。"""

    # 1. 构建 server 启动命令
    server_cmd = [sys.executable, "-m", "exam",
                  "--task", task_id, "--persona", persona_id]
    if use_docker:
        server_cmd.append("--docker")
    if auto_eval:
        server_cmd.append("--auto-eval")

    server_params = StdioServerParameters(
        command=server_cmd[0], args=server_cmd[1:],
        cwd=str(BENCH_DIR), env={**os.environ},
    )

    # 2. MCP 连接（自动管理 subprocess 生命周期）
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            loop = asyncio.get_running_loop()

            # 3. 注册
            await mcp.call_tool("register_session", arguments={
                "agent_name": f"baseline-{model.split('/')[-1]}",
                "model": model,
                "max_steps_per_turn": 30,
            })

            # 4. 获取工具 + system prompt
            tools_result = await mcp.list_tools()
            tools = [{"name": t.name, "description": t.description or "",
                       "parameters": t.inputSchema or {}}
                      for t in tools_result.tools]

            prompt_result = await mcp.call_tool("get_system_prompt")
            prompt_data = json.loads(extract_text(prompt_result))
            system_prompt = prompt_data["system_prompt"]
            task_context = prompt_data["task_context"]

            session_info = await mcp.call_tool("get_session_info")
            info = json.loads(extract_text(session_info))
            opening = info["student_opening"]

            # 5. 创建 adapter
            adapter = ClaudeAgentAdapter(system_prompt=system_prompt, model=model)
            adapter.set_task_context(task_context)
            adapter.set_agent_max_steps(info.get("max_turns", 30) * 10)

            # 6. 同步→异步桥接
            def sync_tool_callback(name, **kwargs):
                coro = mcp.call_tool(name, arguments=kwargs)
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                result = future.result(timeout=300)
                return "\n".join(b.text for b in result.content if hasattr(b, "text"))

            # 7. 在线程中运行 adapter（不阻塞 event loop）
            bootstrap = _build_bootstrap(opening)
            await asyncio.to_thread(
                adapter.generate_response,
                messages=[{"role": "user", "content": bootstrap}],
                available_tools=tools,
                tool_callback=sync_tool_callback,
            )

    # 8. stdio_client 退出 → server 子进程收到 EOF → 保存结果 → 退出
    return {"task_id": task_id, "persona_id": persona_id, "status": "completed"}


def run_single_exam(task_id, persona_id, model, use_docker, args):
    """同步包装——在 ThreadPool worker 中运行。"""
    return asyncio.run(
        run_single_exam_async(task_id, persona_id, model, use_docker, args.auto_eval)
    )
```

### 4.2 路径 A：主进程并行调度

```python
def main():
    args = parse_args()
    tasks = resolve_tasks(args)        # --tasks 列表 或 --group 展开
    personas = resolve_personas(args)   # 单个 或 task 默认的全部 persona

    # 构建 job 列表
    jobs = []
    for task_id in tasks:
        for persona_id in personas:
            jobs.append((task_id, persona_id, args.model, args.docker))

    print(f"Running {len(jobs)} jobs with {args.workers} workers")

    # 并行执行
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_single_exam, *job, args): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"  ✓ {job[0]}/{job[1]}: {result.status}")
            except Exception as e:
                results.append({"task_id": job[0], "error": str(e)})
                print(f"  ✗ {job[0]}/{job[1]}: {e}")

    # 汇总
    print_summary(results)
```

### 4.3 路径 B：Server 端（exam_http_server.py）

```python
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route

# 创建 MCP Server（所有 session 共享同一份 handler 定义）
mcp_server = Server(name="QuantTutorBench-Exam")

# 每个 session 的状态存储
sessions: dict[str, SessionState] = {}  # session_id → state

@mcp_server.list_tools()
async def list_tools():
    session_id = get_current_session_id()  # 从 request context 获取
    state = sessions.get(session_id)
    if state and state.registered:
        return state.all_tools()  # exam tools + domain tools + session tools
    else:
        return exam_tools_only()  # 仅 register_session 等

@mcp_server.call_tool()
async def call_tool(name, arguments):
    session_id = get_current_session_id()
    state = sessions.get(session_id)

    if name == "register_session":
        state = create_session_state(arguments)
        sessions[session_id] = state
        return state.registration_result()

    if state is None or not state.registered:
        return error("Session not registered")

    result = await asyncio.to_thread(state.proxy.call_tool, name, **arguments)
    return result

# Session Manager
session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=False)

# Starlette app
app = Starlette(
    routes=[
        Route("/mcp", session_manager_asgi_app),
        Route("/api/tasks", list_tasks_handler),
        Route("/api/sessions", list_sessions_handler),
        Route("/api/results/{session_id}", get_results_handler),
    ],
    lifespan=lambda app: session_manager.run(),
)
```

### 4.4 路径 B：Client 端（exam_baseline_client.py）

```python
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

async def run_baseline(server_url, task_id, persona_id, model):
    async with streamable_http_client(server_url) as (read, write, get_sid):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            session_id = get_sid()

            # 注册（提交 task + persona + agent 配置）
            await mcp.call_tool("register_session", {
                "task_id": task_id,
                "persona_id": persona_id,
                "agent_name": f"baseline-{model.split('/')[-1]}",
                "model": model,
                "max_steps_per_turn": 30,
            })

            # 获取工具 + prompt
            tools = convert_mcp_tools(await mcp.list_tools())
            prompt_data = await mcp.call_tool("get_system_prompt")
            session_info = await mcp.call_tool("get_session_info")

            # Adapter 桥接（与 stdio 版本完全相同）
            loop = asyncio.get_running_loop()
            adapter = ClaudeAgentAdapter(system_prompt=..., model=model)

            def sync_tool_callback(name, **kwargs):
                coro = mcp.call_tool(name, arguments=kwargs)
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                return extract_text(future.result(timeout=300))

            await asyncio.to_thread(
                adapter.generate_response,
                messages=[{"role": "user", "content": bootstrap}],
                available_tools=tools,
                tool_callback=sync_tool_callback,
            )

    # 连接关闭 → Server 自动保存结果
```

### 4.5 路径 B：并行 baseline 运行

```python
# 多 task 并行——每个 task 一个独立的 HTTP session
async def run_parallel_baselines(server_url, tasks, persona_id, model, workers):
    semaphore = asyncio.Semaphore(workers)

    async def run_one(task_id):
        async with semaphore:
            return await run_baseline(server_url, task_id, persona_id, model)

    results = await asyncio.gather(
        *[run_one(t) for t in tasks],
        return_exceptions=True,
    )
    return results
```

**注意：并行的是 Client 连接，不是 Server 进程。** 一个 Server 进程通过 SessionManager 同时管理多个 session。

---

## 五、关键设计决策（路径 B）

### 5.1 task_id 从 Server 启动参数移到 register_session

stdio 版本：`python -m exam --task S01 --persona intermediate`（一个 server = 一个 task）

HTTP 版本：`python -m exam.http --port 8000`（一个 server = 多个 task）

```python
# register_session 的新 schema
REGISTER_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},          # 新增
        "persona_id": {"type": "string"},        # 新增
        "agent_name": {"type": "string"},
        "model": {"type": "string"},
        "max_steps_per_turn": {"type": "integer"},
    },
    "required": ["task_id", "persona_id", "agent_name"],
}
```

### 5.2 per-session 状态隔离

每个 session 有自己的：
- MCPProxy（工具日志）
- TutoringSession（对话状态）
- Docker 容器（沙箱环境）
- StudentSimulator（学生模拟器实例）

### 5.3 MCP Server 实例模型

MCP SDK 的 SessionManager 为每个 session 创建独立的 `ServerSession`，但共享同一份 handler 定义。handler 内部通过 `request_context` 区分当前 session。`@mcp_server.call_tool()` handler 通过 session_id → state 字典路由到对应的 proxy/session 对象。

---

## 六、错误处理

### 6.1 路径 A

**Worker 崩溃：**
- adapter 线程异常 → `asyncio.to_thread` 传播 → `future.result()` 抛异常
- server 子进程收到 BrokenPipe → 进入 finally → 尝试保存已有对话 → 退出
- 其他 worker 不受影响

**Server 子进程崩溃：**
- Client 的 MCP session 收到 EOF → stdio_client context manager 退出
- adapter 的 sync_tool_callback 收到 BrokenPipe → 抛异常
- 其他 Worker 不受影响

**Docker 容器崩溃：**
- ContainerManager 有自动重启机制（container_manager.py:408-422）
- 不可恢复时 tool call 返回错误字符串，agent 可能选择停止

---

## 七、资源约束与并行度

### 7.1 每个 Worker 消耗

| 资源 | 消耗 | 来源 |
|------|------|------|
| Docker 容器 | 1 个 | server 子进程创建 |
| 内存（容器） | 768MB（标准）/ 1GB（LEAN） | container_manager.py 配置 |
| 内存（server 进程） | ~200MB | Python + MCP server |
| 内存（client 线程） | ~100MB | adapter + anthropic SDK |
| CPU | 1 核（容器）+ 共享 | Docker cpus=1 限制 |
| API 并发 | 1 个 LLM 请求 + 1 个 student sim 请求 | 串行，不并发 |

### 7.2 推荐并行度

| 环境 | 推荐 workers | 约束因素 |
|------|-------------|---------||
| 开发机（8GB RAM, 4 核） | 2-3 | 内存：3 容器 × 768MB ≈ 2.3GB |
| 工作站（16GB RAM, 8 核） | 3-5 | API rate limit + Docker CPU |
| 云 VM（32GB RAM, 16 核） | 5-8 | API rate limit 是瓶颈 |
| --no-docker 模式 | 1 | 本地模式不隔离，必须串行 |

### 7.3 API Rate Limit

所有 worker 共享同一个 OPENROUTER_API_KEY。3 workers 时峰值 ≈ 9 并发 API 请求。OpenRouter 限制通常 > 50 req/s，足够。

---

## 八、代码复用分析

| 组件 | stdio 版本 | HTTP 版本 | 复用 |
|------|-----------|----------|------|
| TutoringSession | session.py | 同 | ✅ 100% |
| StudentSimulator | student_sim.py | 同 | ✅ 100% |
| TCChecker / GoalChecker | tc_checker.py / session.py | 同 | ✅ 100% |
| MCPProxy | proxy/mcp_proxy.py | 同 | ✅ 100% |
| Tool 注册 | registry.py | 同 | ✅ 100% |
| 格式校验 | format_validator.py | 同 | ✅ 100% |
| 结果保存 | result_writer.py | 同 | ✅ 100% |
| 评分 | eval_pipeline.py + eval_runner.py | 同 | ✅ 100% |
| **Exam tools** | exam_tools.py | **需适配**（register_session 加 task_id/persona_id） | 🔧 小改 |
| **Server 入口** | exam_server.py (stdio) | **新建** exam_http_server.py (HTTP) | 🆕 新建 |
| **Client** | test_exam_e2e.py / baseline_runner.py | **新建** baseline_client.py (HTTP) | 🆕 新建 |

---

## 九、实现工作量

| 方案 | 文件 | 内容 | 行数 |
|------|------|------|------|
| **路径 A** | `exam/baseline_runner.py` | 单 task MCP 连接 + adapter 桥接 + 生命周期管理 | ~180 |
| | `exam/baseline_main.py` | CLI 入口 + 任务解析 + ThreadPool 并行调度 + 结果汇总 | ~120 |
| | **小计** | **不改动任何已有文件** | **~300** |
| **路径 B** | `exam/http_server.py` | HTTP Exam Server：Starlette app + session 管理 + REST API | ~250 |
| | `exam/baseline_client.py` | Baseline Client：HTTP 连接 + adapter 桥接 + 并行调度 | ~200 |
| | `exam/exam_tools.py` | 修改 register_session schema 增加 task_id/persona_id | ~20 行改动 |
| | **小计** | | **~470** |

---

## 十、CLI 运行方式对比

### 路径 A（Stdio）

```bash
# 指定具体 task 列表
python -m exam.baseline \
    --tasks S01_ma_crossover D01_load_inspect_ohlcv X01_ma_offbyone \
    --persona intermediate_developer \
    --model anthropic/claude-haiku-4-5 \
    --workers 3 \
    --docker --auto-eval

# 按 category 跑一组
python -m exam.baseline \
    --group strategy \
    --persona intermediate_developer \
    --model anthropic/claude-sonnet-4-6 \
    --workers 3 \
    --docker --auto-eval

# 单个 task
python -m exam.baseline \
    --tasks X01_ma_offbyone \
    --persona beginner_no_finance \
    --model anthropic/claude-haiku-4-5 \
    --docker
```

### 路径 B（HTTP）

```bash
# Step 1: 启动 Server（一次，持久运行）
python -m exam.http --port 8000 --docker

# Step 2: 运行 baseline（连接 Server，可多次运行不同 task）

# 单个 task
python -m exam.baseline_client \
    --server http://localhost:8000/mcp \
    --task S01_ma_crossover \
    --persona intermediate_developer \
    --model anthropic/claude-haiku-4-5

# 多 task 并行
python -m exam.baseline_client \
    --server http://localhost:8000/mcp \
    --tasks S01_ma_crossover D01_load_inspect_ohlcv X01_ma_offbyone \
    --persona intermediate_developer \
    --model anthropic/claude-sonnet-4-6 \
    --workers 3

# 按 category
python -m exam.baseline_client \
    --server http://localhost:8000/mcp \
    --group strategy \
    --persona intermediate_developer \
    --model anthropic/claude-sonnet-4-6 \
    --workers 3 --auto-eval

# Step 3: 查看结果
curl http://localhost:8000/api/sessions              # 所有 session 状态
curl http://localhost:8000/api/results/{session_id}   # 具体结果
```

---

## 十一、结果目录结构

```
bench/results/exam/
├── strategy/
│   └── S01_ma_crossover/
│       └── intermediate_developer/
│           ├── run_state.json
│           ├── agent_files/
│           ├── scores.md        (如 --auto-eval)
│           └── trace.md         (如 --auto-eval)
├── data_analysis/
│   └── D01_load_inspect_ohlcv/
│       └── intermediate_developer/
│           ├── run_state.json
│           └── agent_files/
└── debug/
    └── X01_ma_offbyone/
        └── beginner_no_finance/
            ├── run_state.json
            └── agent_files/
```

每个 task × persona 的结果由对应的 server 子进程独立保存，不存在竞争。

---

## 十二、综合对比与选择建议

### 优劣对比

| 维度 | 路径 A（多 stdio） | 路径 B（HTTP） |
|------|-------------------|---------------|
| Server 进程数 | N 个 task = N 个 server 进程 | 1 个持久 Server |
| Client-Server 关系 | Client 是 Server 父进程 | 完全独立 |
| 部署 | 同一台机器 | localhost（可扩展到远程） |
| 启动流程 | 1 条命令 | 2 条命令（先 server 后 client） |
| Session 管理 | 无（一进程一 session） | SessionManager 自动管理 |
| 监控 | 无（进程私有） | REST API 查询 session 状态 |
| 复用环境 | 不能（每个进程独立初始化） | 可能（共享数据缓存） |
| 实现复杂度 | 较低（~300 行） | 中等（~470 行） |
| 完全隔离 | ✅ 每个 task 独立进程 + 容器 | 共享进程，session 级隔离 |
| 端口冲突 | 无 | 需要管理端口分配 |
| 安全性 | 进程间 OS 管道，无网络暴露 | HTTP 暴露后需要 auth 机制 |
| 远程 Client | ❌ | ✅ |
| Web UI | ❌ | ✅ |
| 排行榜服务 | ❌ | ✅ |
| 实时监控 | ❌ | ✅ |
| **是否真正解耦** | 进程隔离但 Client 必须启动 Server | **完全独立** |

### 选择建议

| 阶段 | 选择 | 理由 |
|------|------|------|
| **当前（验证解耦 + 跑 baseline）** | **路径 A** | 实现简单，足以证明解耦有效性 |
| **论文提交前** | 路径 A | 够用——论文需要的是评分结果，不是服务化 |
| **产品化 / 排行榜** | 路径 B | 需要远程访问、Web UI、多用户并发 |
