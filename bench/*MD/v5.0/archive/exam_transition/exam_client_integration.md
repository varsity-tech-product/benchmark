# Exam Client 接入报告：E2E 测试解析 + 真实 Agent 接入方案

> 日期：2026-04-09
> 目的：解析 E2E 测试如何运行，分析接入真实 Client（如 Anthropic adapter + Sonnet）的可行性和完整流程

---

## 一、E2E 测试是如何运行的

### 1.1 架构

```
test_exam_e2e.py（Client 进程）          exam server（子进程）
┌─────────────────────────┐              ┌──────────────────────────────┐
│ Python MCP Client       │              │ python -m exam               │
│                         │              │                              │
│ stdio_client(params)    │── stdin ──→  │ MCP Server (stdio transport) │
│   ↕ JSON-RPC            │← stdout ──  │   ├ MCPProxy                 │
│                         │              │   ├ TutoringSession          │
│ OpenRouter (Haiku 4.5)  │              │   ├ StudentSimulator         │
│   纯文本生成，无 tool use │              │   └ Docker sandbox           │
└─────────────────────────┘              └──────────────────────────────┘
```

### 1.2 执行流程

```
1. Client 通过 subprocess 启动 exam server
   → server_cmd = ["python", "-m", "exam", "--task", "X01", "--persona", "beginner", "--docker"]
   → mcp.client.stdio.stdio_client(params) 管理 stdin/stdout 管道

2. MCP 握手
   → client: initialize() → server: {name: "QuantTutorBench-Exam", version: "1.26.0"}
   → client: list_tools() → server: [list_tasks, get_task_info, get_system_prompt, register_session]

3. Phase 0-1: 任务发现
   → client: call_tool("list_tasks") → 65 tasks
   → client: call_tool("get_task_info", {task_id, persona_id}) → 任务详情

4. Phase 2: 注册
   → client: call_tool("register_session", {agent_name, model, max_steps_per_turn})
   → server: 创建 Docker 容器 + 注册 domain tools（2-5 秒）
   → client: list_tools() → 21 tools（含 shell_exec, send_message 等）

5. Phase 3: 对话循环
   每轮：
     a. Client 调 OpenRouter API 生成 tutor 回复（纯文本，无 tool use）
     b. Client 调 call_tool("send_message", {text: 回复内容})
     c. Server 内部：记录消息 → TC 检查 → 学生回复生成
     d. Client 读返回值：{student_reply, status, turn}
     e. status=="completed" → 退出

6. Phase 4-5: Server 保存 run_state.json → 销毁容器
```

### 1.3 E2E 测试的关键局限

| 局限 | 说明 | 对评分的影响 |
|------|------|------------|
| **无 tool use** | Haiku 只生成纯文本回复，不调用 shell_exec/file_read 等 | QP tool_usage=0, code_eval=0 |
| **无 system prompt** | 硬编码简短 prompt，不使用 Server 提供的推荐 prompt | Tutor 维度可能偏低 |
| **无自主决策** | 每轮固定：生成文本 → send_message，不会先调工具分析数据 | QP process_reasonableness 低 |
| **无跨轮上下文管理** | 只保留最近 3 轮对话 | 对话连贯性差 |

**本质：E2E 测试验证的是 Server 基础设施（MCP 通信、注册流程、TC 终止、结果保存），不是 agent 的教学能力。**

---

## 二、真实 Agent 的需求分析

### 2.1 真实 Agent 与 E2E 测试的核心差异

```
E2E 测试 Agent:
  每轮: LLM(对话历史) → 纯文本回复 → send_message

真实 Agent（如 Anthropic BetaToolRunner）:
  每轮: LLM(对话历史 + 工具列表) →
    [思考] → [tool_use: shell_exec("python analyze.py")] → [工具结果] →
    [思考] → [tool_use: compute_indicator(...)] → [工具结果] →
    [思考] → [文本: 综合分析回复] →
    send_message(拼接后的回复)
```

**关键区别：真实 agent 会在回复学生之前主动调用 domain tools 来准备教学内容。** 这正是 QP（行为质量）评分要评的。

### 2.2 新架构下 Adapter 的角色转变

| | Legacy 路径 | 新架构 |
|---|---|---|
| Adapter 由谁调用 | orchestrator.run_agent_session() | **Exam Client 自己调用** |
| 工具来源 | proxy.call_tool()（同进程内） | **MCP server 的 tool_call**（跨进程） |
| Adapter 感知 | 通过 tool_callback 参数获得工具 | **通过 MCP list_tools() 获取工具 schema** |
| Session 驱动 | adapter.generate_response() 一次调用跑完 | **Exam Client 控制循环，adapter 处理单轮** |

---

## 三、接入方案

### 3.1 核心问题：同步 Adapter vs 异步 MCP

Legacy adapter 的接口是**同步的**：

```python
# BaseAgentAdapter.generate_response()
def generate_response(self, messages, available_tools, tool_callback) -> str:
    # tool_callback 是同步函数: (name, **kwargs) -> str
```

MCP Client 是**异步的**：

```python
# mcp.ClientSession.call_tool()
async def call_tool(name, arguments) -> CallToolResult:
```

**解决方案：构建 MCP-aware Exam Client，内部桥接同步 adapter 到异步 MCP。**

### 3.2 推荐架构：Exam Client Wrapper

```
┌─ exam_client.py ──────────────────────────────────────────────┐
│                                                                │
│  async def run_exam(task_id, persona_id, adapter_factory):     │
│    │                                                           │
│    ├─ async with stdio_client(exam_server) as mcp_session:     │
│    │                                                           │
│    │  Phase 0-2: register_session(...)                         │
│    │                                                           │
│    │  # 桥接层：将 MCP async 工具包装为 sync callback            │
│    │  def sync_tool_callback(name, **kwargs) -> str:           │
│    │      future = asyncio.run_coroutine_threadsafe(           │
│    │          mcp_session.call_tool(name, kwargs),             │
│    │          loop                                             │
│    │      )                                                    │
│    │      return future.result(timeout=300)                    │
│    │                                                           │
│    │  # 获取工具列表（MCP schema → adapter schema）             │
│    │  tools = convert_mcp_tools_to_adapter_format(             │
│    │      await mcp_session.list_tools()                       │
│    │  )                                                        │
│    │                                                           │
│    │  # 创建 adapter 实例                                      │
│    │  adapter = adapter_factory()                               │
│    │  adapter.set_task_context(system_prompt)                   │
│    │  adapter.set_agent_max_steps(max_turns * 10)              │
│    │                                                           │
│    │  # 在线程中运行同步 adapter                                │
│    │  response = await asyncio.to_thread(                      │
│    │      adapter.generate_response,                           │
│    │      messages=[bootstrap],                                │
│    │      available_tools=tools,                               │
│    │      tool_callback=sync_tool_callback,                    │
│    │  )                                                        │
│    │                                                           │
│    └─ Server 检测 session.done → 保存结果                      │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**关键桥接：** `sync_tool_callback` 把 async MCP call 包装成 sync 函数，使用 `asyncio.run_coroutine_threadsafe` 将 coroutine 调度到主 event loop 上执行，然后 `.result()` 阻塞等待。

### 3.3 为什么这个方案可行

1. **Adapter 在线程中运行**（`asyncio.to_thread`）：不阻塞 MCP event loop
2. **sync_tool_callback 通过 `run_coroutine_threadsafe`** 将 MCP 调用委托回主 event loop
3. **BetaToolRunner 的 DynamicTool.call()** 是同步的，调 `sync_tool_callback` 无障碍
4. **MCP session 在主 event loop** 中处理实际的 JSON-RPC 通信

```
主线程 (async event loop):
  ├─ MCP stdio 读写
  ├─ 处理 sync_tool_callback 委托的 coroutine
  └─ 等待 adapter 线程完成

Adapter 线程 (sync):
  ├─ BetaToolRunner 循环
  │   ├─ Claude 生成 tool_use
  │   ├─ DynamicTool.call() → sync_tool_callback()
  │   │   └─ run_coroutine_threadsafe → 主线程执行 MCP call → 返回结果
  │   ├─ Claude 读取工具结果
  │   └─ 重复...
  └─ 返回最终文本
```

### 3.4 MCP Tool Schema 转换

MCP 返回的工具 schema 格式：
```json
{"name": "shell_exec", "description": "...", "inputSchema": {"type": "object", "properties": {...}}}
```

Legacy adapter 需要的格式：
```json
{"name": "shell_exec", "description": "...", "parameters": {"type": "object", "properties": {...}}}
```

差异仅在 `inputSchema` vs `parameters`，一行映射即可：

```python
def convert_mcp_tools(mcp_tools):
    return [
        {"name": t.name, "description": t.description, "parameters": t.inputSchema}
        for t in mcp_tools
    ]
```

---

## 四、完整实验流程

### 4.1 文件结构

```
bench/exam/
├── exam_client.py          ← 新建：MCP-aware Agent Client
├── exam_server.py          ← 已有：Benchmark Server
└── __main__.py             ← 已有：Server CLI
```

### 4.2 运行命令

```bash
# 方式 A：Client 自动启动 Server（推荐）
cd /Users/richsion/Desktop/benchmark/bench
python -m exam.client \
    --task S01_ma_crossover \
    --persona intermediate_developer \
    --agent anthropic \
    --model claude-sonnet-4-6 \
    --docker \
    --auto-eval

# 方式 B：分开启动
# Terminal 1:
python -m exam --task S01_ma_crossover --persona intermediate_developer --docker --auto-eval
# Terminal 2:
python -m exam.client --connect-stdio --agent anthropic --model claude-sonnet-4-6
```

### 4.3 Client 内部执行步骤

```
Step 1: 启动 exam server 子进程 + MCP 连接
Step 2: list_tools → register_session → list_tools（获取 domain tools）
Step 3: get_session_info → 获取任务 + 学生开场白
Step 4: get_system_prompt → 获取推荐 system prompt
Step 5: 创建 adapter 实例 + 注入 system prompt + task context
Step 6: 构建 sync_tool_callback 桥接
Step 7: adapter.generate_response(bootstrap, tools, sync_tool_callback)
        ↓
        Adapter 内部 BetaToolRunner 循环：
          Claude 思考 → tool_use(shell_exec) → sync_tool_callback → MCP call → 结果
          Claude 思考 → tool_use(compute_indicator) → sync_tool_callback → MCP call → 结果
          Claude 思考 → tool_use(send_message, text="...") → sync_tool_callback → MCP call
            → Server: 记录 + TC check + 学生回复
            → 返回 {student_reply, status}
          Claude 读取学生回复 → 继续下一轮...
          ...
          send_message 返回 status="completed" → Claude 停止 → BetaToolRunner 结束
        ↓
Step 8: generate_response 返回
Step 9: MCP 连接关闭 → Server 保存 run_state.json → 评分（如 --auto-eval）
Step 10: 验证结果
```

### 4.4 预期结果

与 Legacy 路径跑同任务同 persona 对比：

| 指标 | Legacy 路径 | Exam Server 路径 | 预期差异 |
|------|-----------|-----------------|---------|
| conversation 格式 | list[{role, content}] | 完全相同 | 无 |
| tool_logs 格式 | list[ToolCallLog] | 完全相同（同一 MCPProxy） | 无 |
| 终止机制 | TC/GoalChecker | 完全相同（同一 session.py） | 无 |
| 学生消息 | StudentSimulator (DeepEval prompt) | 完全相同 | 无 |
| run_state.json | Legacy 格式 | 兼容格式 | agent_cost 少 token 统计 |
| 评分 | orchestrator._evaluate_task | eval_pipeline.evaluate_task | 完全相同（enrichment 是同一函数） |
| **Agent 行为** | BetaToolRunner + proxy.call_tool | BetaToolRunner + MCP call_tool | **可能有微小差异**（网络延迟） |

### 4.5 对比验证方法

```bash
# 1. Legacy 跑一次
python run_benchmark.py run-single \
    --task S01_ma_crossover \
    --persona intermediate_developer \
    --agent anthropic --docker --save-result

# 2. Exam 跑一次（同 task, persona, model）
python -m exam.client \
    --task S01_ma_crossover \
    --persona intermediate_developer \
    --agent anthropic --docker --auto-eval

# 3. 对比 run_state.json
python -c "
import json
legacy = json.load(open('results/run-single/.../run_state.json'))
exam = json.load(open('results/exam/.../run_state.json'))
print(f'Legacy: {len(legacy[\"conversation\"])} msgs, {len(legacy[\"tool_logs\"])} tools')
print(f'Exam:   {len(exam[\"conversation\"])} msgs, {len(exam[\"tool_logs\"])} tools')
"
```

---

## 五、实施工作量

| 工作项 | 文件 | 估算行数 | 说明 |
|--------|------|---------|------|
| `exam_client.py` | 新建 | ~200 | MCP 连接 + sync_tool_callback 桥接 + adapter 驱动 |
| MCP tool schema 转换 | exam_client.py 内 | ~10 | inputSchema → parameters |
| CLI 入口 | `exam/client_main.py` 或扩展 `__main__.py` | ~40 | argparse + adapter factory |
| **合计** | | **~250** | |

**不改动的文件：** exam_server.py、所有 adapter、session.py、eval_pipeline.py。

---

## 六、风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `run_coroutine_threadsafe` 死锁 | adapter 线程和 MCP event loop 互相等待 | 确保 MCP session 不在 adapter 线程中使用 |
| MCP 延迟 vs 同进程调用延迟 | 每次 tool call 多 ~1ms（JSON-RPC 序列化） | 可忽略（LLM 调用 1-10 秒远大于此） |
| BetaToolRunner 上下文溢出 | 长 session 的 tool results 累积导致 context 过长 | adapter 已有 compaction 机制（40K token 阈值） |
| Docker 容器超时 | 长 tool call（如 run_backtest 190s）阻塞 | proxy 已有 deadline 检查 |
