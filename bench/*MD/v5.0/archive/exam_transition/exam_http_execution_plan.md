# Bench Server HTTP 执行方案

> 日期：2026-04-10（最终版）
> 核心原则：**Server 只提供考场设施，不提供任何教学提示。Client 完全由用户自定义。**

---

## 一、设计原则

### 1.1 彻底解耦

- **Server 不知道 Client 是什么**——不记录模型、不记录 prompt、不记录 agent 费用
- **Client 不知道 Server 怎么评分**——评分公式是黑盒
- **Agent 不预知环境**——通过工具自行探索文件、文档、代码
- **Agent 不预知学生**——从对话中推断水平、需求、情感
- **用户从文档获取协议规范**——session API 格式、调用顺序、任务列表

### 1.2 考试隐喻

Server 是考场。考场提供：试卷（学生开场白）、文具（工具）、监考（学生模拟器 + 终止判定）、打分（评分流水线）。

考场**不提供**：答题思路、考题分类提示、学生背景资料、评分细则、具体工具列表（每次考试的文具不同，进考场才知道）。

---

## 二、文件结构

```
bench/
├── spec/                                  ← 用户文档（仅两份）
│   ├── TASKS.md                           #  任务列表：task_id + description
│   └── PROTOCOL.md                        #  通信协议：session API 格式 + 权限状态机
│
├── server/                                ← Bench Server
│   ├── __main__.py                        #  python -m server --port 8000 --docker
│   ├── http_app.py                        #  Starlette + SessionManager + REST API
│   ├── session_manager.py                 #  per-session 生命周期
│   ├── api_tools.py                       #  Session API 定义
│   ├── protocol.py                        #  协议常量 + 请求验证 + 权限状态机
│   ├── format_validator.py                #  run_state 格式校验
│   ├── result_writer.py                   #  结果保存（session_id 为单位）
│   └── eval_runner.py                     #  评分触发
│
├── client/                                ← 我们的 baseline client（非 benchmark 规范）
│   ├── __main__.py                        #  启动入口
│   ├── runner.py                          #  HTTP 连接 + adapter 驱动
│   ├── tool_bridge.py                     #  sync adapter → async HTTP 桥接
│   ├── cost_tracker.py                    #  agent 计费（本地保存）
│   └── adapters/                          #  从 Legacy 精简复制
│       ├── base_adapter.py                #  Legacy base_adapter 精简版
│       ├── anthropic_adapter.py           #  Legacy anthropic_adapter 精简版
│       ├── prompts.py                     #  干净版 system prompt
│       └── config.py                      #  模型配置 + 计费费率
│
├── mcp_servers/                           ← 共享基础设施（Server 内部）
├── evaluation/                            ← 共享评分（Server 内部）
├── config/                                ← 共享配置（Server 内部）
├── orchestrator/                          ← Legacy（完全保留，不改动）
└── run_benchmark.py                       ← Legacy CLI（完全保留，不改动）
```

---

## 三、通信协议（PROTOCOL.md 内容）

### 3.1 连接

通过 HTTP 连接 Bench Server（MCP StreamableHTTP 传输）。首次请求自动分配 session_id。

### 3.2 Session API

**以下 4 个 API 是 benchmark 定义的 session 管理接口，用户必须按顺序调用。**

#### `register_session`

注册任务。Server 创建沙箱环境。

```
请求: register_session({task_id: "X01_ma_offbyone"})
响应: {accepted: true, session_id: "a1b2c3d4e5f6"}
错误: {accepted: false, error: "Task not found: INVALID_ID"}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_id | string | 是 | 任务标识符，见 TASKS.md |

注册成功后，通过 MCP 标准操作 `list_tools()` 获取当前 session 可用的全部工具列表（名称 + 描述 + 参数 schema）。工具组合因 task 而异。list_tools 在注册成功后、start_session 前后均可调用。

#### `start_session`

开考。返回学生的第一条消息。此后 Agent 可调用工具和 send_message。

```
请求: start_session()
响应: {student_message: "I wrote a moving average crossover strategy but the numbers don't look right..."}
错误: {error: "Session already started"}
```

只能调用一次。

#### `send_message`

向学生发送消息。返回学生的下一条消息和 session 状态。

```
请求: send_message({text: "Let me help you debug this..."})
响应: {student_message: "Oh I see, so...", status: "active"}
     或 {student_message: "Thanks for the help!", status: "completed", reason: "objectives_met"}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | 是 | Agent 给学生的回复内容 |

| 返回字段 | 说明 |
|---------|------|
| student_message | 学生的下一条消息 |
| status | `"active"`（继续）或 `"completed"`（结束） |
| reason | 仅 completed 时出现：终止原因 |

当 `status == "completed"` 时，session 结束。Agent 应停止调用工具和 send_message。

#### `request_evaluation`

请求评分。仅在 session 完成后可调用。

```
请求: request_evaluation()
响应: {status: "completed", scores: {overall: 0.72, quant_result: 0.80, ...}}
     或 {status: "running", message: "Evaluation in progress"}
     或 {status: "pending", message: "Starting evaluation..."}
```

首次调用触发评分运行，后续调用返回已有结果。

### 3.3 权限状态机

**Server 在每个阶段只响应特定请求，其余返回错误并提示当前允许的操作。**

```
┌──────────┐  register_session   ┌──────────────┐  start_session  ┌──────────┐
│          │ ──────────────────→ │              │ ──────────────→ │          │
│  未注册   │                     │ 已注册未开考  │                 │  进行中   │
│          │                     │              │                 │          │
└──────────┘                     └──────────────┘                 └────┬─────┘
                                                                      │
                                                          status="completed"
                                                                      ▼
                                                                 ┌──────────┐
                                                                 │  已完成   │
                                                                 └──────────┘
```

| 状态 | 响应的请求 | 拒绝的请求 |
|------|----------|----------|
| **未注册** | register_session | start_session, send_message, 工具调用, request_evaluation |
| **已注册未开考** | start_session, list_tools | register_session, send_message, 工具调用, request_evaluation |
| **进行中** | 工具调用, send_message | register_session, start_session, request_evaluation |
| **已完成** | request_evaluation | send_message, 工具调用, start_session, register_session |

拒绝时返回：`{"error": "描述", "allowed": ["当前允许的操作"]}`

### 3.4 对话流程示例

```
1. register_session({task_id: "X01_ma_offbyone"})
   → {accepted: true, session_id: "a1b2c3d4e5f6"}

2. list_tools()
   → 获取当前 session 的全部工具（名称 + 描述 + 参数）

3. start_session()
   → {student_message: "I wrote a moving average crossover strategy but..."}

4. Agent 自由调用工具 + 和学生对话
   （调用 list_tools 中获取的工具探索环境、分析数据等）
   send_message({text: "..."})            → 和学生交流
   → {student_message: "...", status: "active"}

5. ...重复直到 status == "completed"...

6. request_evaluation()
   → {status: "completed", scores: {...}}
```

### 3.5 Session 终止

Session 在 Server 判定满足内部条件时结束（status="completed"）。Agent 无需知道具体条件——只需观察 send_message 返回的 status 字段。

### 3.6 取消

Client 发送 HTTP DELETE 请求终止 session。Server 立即销毁环境，不保存任何内容。

---

## 四、Server 侧设计（内部实施，不对用户公开）

### 4.1 register_session 内部处理

```
① 验证 task_id 存在
② 从 task.persona_ids 随机选一个 persona（Client 不知道选了哪个）
③ 下载数据（如未缓存）
④ 创建 Docker sandbox
⑤ 注册工具（core + convenient + distractor 填充，Agent 无法区分）
⑥ 创建 TutoringSession（StudentSimulator + TCChecker/GoalChecker）
⑦ Distractor seed = task.seed or hash(task_id + session_id)
⑧ 返回 {accepted: true, session_id}
```

### 4.2 build_tutor_context 注释化

整个函数体注释掉，返回空字符串。保留函数签名用于消融实验。

### 4.3 E01-E05 补充 get_environment_info

5 个 end_to_end 任务的 core_mcp_tools 补充 `get_environment_info`，确保所有 65 个任务都支持环境自主探索。

### 4.4 无步数限制

不限制 Agent per-turn 工具调用次数。QP 评分中的 step_efficiency 自然反映效率。

### 4.5 max_turns 保留但不告知

Server 内部强制 max_turns。Agent 从 send_message 返回 status="completed" 得知结束。

### 4.6 list_tools 动态过滤

MCP 标准的 list_tools 操作在每次调用时动态返回当前状态下可见的工具：

| 状态 | list_tools 返回 |
|------|----------------|
| 未注册 | `[register_session]` |
| 已注册未开考 | session API + 所有工具 schema（但工具调用仍被权限拦截） |
| 进行中 | 所有工具 |
| 已完成 | `[request_evaluation]` |

---

## 五、结果存储

### 5.1 存储路径

以 session_id 为单位（Client 不知道 persona，不使用 persona_id 做路径）：

```
results/server/{task_id}/
├── {session_id_1}/
│   ├── run_state.json
│   ├── agent_files/
│   └── evaluations/
│       ├── eval_20260410_110000/
│       │   ├── scores.md
│       │   ├── trace.md
│       │   ├── cost.md
│       │   └── eval_meta.json
│       └── latest -> eval_20260410_110000/
├── {session_id_2}/
│   └── ...
```

同一 task 的多次运行各自独立——即使随机到相同 persona 也不覆盖。

### 5.2 run_state.json

```json
{
  "task_id": "X01_ma_offbyone",
  "session_id": "a1b2c3d4e5f6",
  "persona_id": "beginner_no_finance",
  "conversation": [...],
  "tool_logs": [...],
  "distractor_names": [...],
  "workspace_files": [...],
  "simulator_cost": 0.0154,
  "duration_seconds": 42.0,
  "key_results": {...},
  "trace_summary": [...],
  "step_count": 12,
  "format_validation": {"passed": true, "errors": []},
  "evaluation_status": "pending"
}
```

**当前阶段全部字段开放**（含 persona_id、distractor_names），方便 debug。正式上线时在 Server REST API 返回端加脱敏层，隐藏内部字段。

### 5.3 评分

`request_evaluation()` 触发。逻辑：
- pending → 运行评分（含 trial auto-select 前置步骤）→ completed
- running → 返回等待提示
- completed → 返回最新评分
- failed → 重试

REST API：`POST /api/evaluate/{session_id}?force=true` 强制重新评分，创建新 evaluations/ 子目录。

### 5.4 cost.md（只含 Server 侧费用）

```
## Cost Report
### Simulator: $0.0154 (openai/gpt-5.2)
### Evaluation: $0.08 (anthropic/claude-sonnet-4-6)
  - result_judge: $0.02
  - process_metrics: $0.03
  - tutor_7d: $0.03
### Total (Server-side): $0.0954
```

### 5.5 结果查询

REST API：

```
GET /api/runs?task_id=X01
→ 返回该 task 的所有 session 列表：
  [{session_id, timestamp, status, duration_seconds}, ...]

GET /api/results/{session_id}
→ 返回 run_state.json（当前全字段开放，上线时加脱敏层）

GET /api/scores/{session_id}
→ 返回最新评分结果

GET /api/scores/{session_id}?history=true
→ 返回所有评分历史
```

---

## 六、我们的 Baseline Client

### 6.1 定位

我们团队的测试工具。用于跑 baseline 评分、与 Legacy 消融对比、验证 Server 功能。**不是 benchmark 规范。**

### 6.2 从 Legacy 精简复制

| Client 文件 | 来源 | 改动 |
|------------|------|------|
| `adapters/base_adapter.py` | orchestrator/agent_adapters/base_adapter.py | 删多余注释 |
| `adapters/anthropic_adapter.py` | orchestrator/agent_adapters/anthropic_adapter.py | 删多余注释和 prompt 注释 |
| `adapters/prompts.py` | 新写 | 干净版 system prompt |
| `adapters/config.py` | config/llm_config.py + config/pricing.py | 合并精简 |

核心逻辑一行不改：BetaToolRunner、DynamicTool、token tracking、cross-turn context、compaction、extended thinking 全部保留。

### 6.3 干净版 system prompt

```python
CLEAN_SYSTEM_PROMPT = (
    "You are a quantitative finance tutor. "
    "Your role is to teach — not to do the student's work. "
    "Adapt your teaching to the student's level based on their questions and responses. "
    "You have access to tools — use them to explore your environment, "
    "analyze data, and demonstrate concepts with real computations. "
    "To communicate with the student, use the send_message tool."
)
```

### 6.4 cost_tracker.py

```python
def aggregate_cost(token_records: list) -> dict:
    return {
        "input_tokens": sum(r.input_tokens for r in token_records),
        "output_tokens": sum(r.output_tokens for r in token_records),
        "cost_usd": sum(r.cost_usd for r in token_records),
        "api_calls": len(token_records),
        "model": token_records[0].model if token_records else "unknown",
    }

def save_agent_cost(result_dir: Path, cost: dict):
    (result_dir / "agent_cost.json").write_text(json.dumps(cost, indent=2))
```

Client 运行后在本地保存 `agent_cost.json`。Server 不知道。

### 6.5 runner.py 核心流程

```python
async def run_single_task(server_url, task_id, adapter_factory):
    adapter = adapter_factory()

    async with streamable_http_client(server_url) as (read, write, get_sid):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()

            # 注册 → 获取 session_id
            reg = await call_tool(mcp, "register_session", {"task_id": task_id})
            session_id = reg["session_id"]

            # 获取工具
            tools = convert_tools(await mcp.list_tools())

            # 开考
            info = await call_tool(mcp, "start_session")
            opening = info["student_message"]

            # 运行 adapter（bootstrap = 学生开场白原文，不添加任何指令）
            bridge = ToolBridge(mcp, asyncio.get_running_loop())
            await asyncio.to_thread(
                adapter.generate_response,
                messages=[{"role": "user", "content": opening}],
                available_tools=tools,
                tool_callback=bridge.call,
            )

    # Client 侧保存 agent 费用
    records = adapter.get_token_records()
    save_agent_cost(client_result_dir / session_id, aggregate_cost(records))
```

---

## 七、消融实验设计

| 实验 | System prompt | Task context | Persona 预知 | 路径 |
|------|-------------|-------------|-------------|------|
| **Legacy baseline** | 完整 TUTOR_SYSTEM_PROMPT | 完整 build_tutor_context | ✅ | Legacy harness |
| **新 baseline** | 干净 prompt | 无 | ❌ | 新 client + HTTP server |
| **消融 A** | 完整 prompt，无 context | 无 | ❌ | 需要时再设计 |
| **消融 B** | 干净 prompt，有 context | 完整 | ✅ | 需要时再设计 |

Legacy 结果已有——直接作为"漏题基准"。差值 = 漏题带来的分数膨胀。

---

## 八、运行命令

```bash
# 启动 Server（持久运行）
python -m server --port 8000 --docker

# 单 task
python -m client --server http://localhost:8000/mcp --task X01_ma_offbyone

# 多 task 并行
python -m client --server http://localhost:8000/mcp \
    --tasks S01 D01 X01 --workers 3

# 按 category
python -m client --server http://localhost:8000/mcp \
    --group strategy --workers 3

# 查看某 task 的所有运行
python -m client --server http://localhost:8000/mcp \
    --list-runs --task X01

# 请求评分
python -m client --server http://localhost:8000/mcp \
    --evaluate --session a1b2c3d4e5f6

# 查看评分历史
python -m client --server http://localhost:8000/mcp \
    --eval-history --session a1b2c3d4e5f6

# REST API
curl http://localhost:8000/api/runs?task_id=X01
curl http://localhost:8000/api/results/{session_id}
curl http://localhost:8000/api/scores/{session_id}
curl -X POST http://localhost:8000/api/evaluate/{session_id}?force=true
```

---

## 九、实施 Batch

### Batch 1: Server HTTP 化

| 文件 | 操作 |
|------|------|
| `server/http_app.py` | 新建：Starlette + SessionManager + REST API |
| `server/session_manager.py` | 从 exam_server.py 重构：极简 register（只需 task_id）、随机 persona、无 task_context |
| `server/api_tools.py` | 重写：register_session + start_session + send_message + request_evaluation |
| `server/protocol.py` | 重写：权限状态机 + 请求验证 |
| `server/result_writer.py` | 改：session_id 为单位存储 |
| `server/__main__.py` | 重写：uvicorn 启动 |
| `config/prompt_config.py` | 改：build_tutor_context 注释化 |
| `tasks/layer2/end_to_end/E01-E05.json` | 改：补充 get_environment_info |

### Batch 2: Baseline Client

| 文件 | 操作 |
|------|------|
| `client/__main__.py` | 新建：CLI 入口 |
| `client/runner.py` | 新建：HTTP 连接 + adapter 驱动 + 并行调度 |
| `client/tool_bridge.py` | 新建：sync→async 桥接 |
| `client/cost_tracker.py` | 新建：agent 计费 |
| `client/adapters/base_adapter.py` | 从 Legacy 复制精简 |
| `client/adapters/anthropic_adapter.py` | 从 Legacy 复制精简 |
| `client/adapters/prompts.py` | 新写：干净版 system prompt |
| `client/adapters/config.py` | 从 Legacy 合并精简 |

### Batch 3: 文档 + 验证

| 文件 | 操作 |
|------|------|
| `spec/TASKS.md` | 新建：65 个 task 的 task_id + description |
| `spec/PROTOCOL.md` | 新建：本文档 §三 的内容 |
| 端到端验证 | Server 启动 → Client 跑 X01 → 评分 → 与 Legacy 对比 |
