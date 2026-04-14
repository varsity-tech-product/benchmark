# QuantTutorBench Exam Architecture：完全解耦方案

> 日期：2026-04-09
> 状态：设计完成，待实施
> 前置：PR #8（mcp-unification）已合并 + mcp_student_sim_migration.md Batch 1-3 已执行
> 目标：Benchmark Server 与 Client 完全解耦——Server 提供考场设施和评分，Client 是任意能对话+调工具的参赛者

---

## 一、设计原则

### 1.1 考试隐喻（延续 architecture_design.md）

Server 是考场，Client 是考生。考场提供试卷（任务）、文具（工具）、监考（学生模拟器+TC）、打分（评分链）。考生可以带小抄（system prompt）、查资料（外部知识库）、用更好的笔（更强的 LLM）——考场不管也管不了。

**考场管的是：答题纸格式对不对、考试流程有没有遵守、最终交卷内容能不能评分。**

### 1.2 解耦边界

```
┌─ Benchmark Server（bench/exam/）──────────┐  ┌─ Client（可替换）────────────┐
│                                            │  │                              │
│  考场设施（不可替换）:                       │  │  任何 MCP client:             │
│  ├ 任务定义 + 学生人格                      │  │  ├ Claude Code                │
│  ├ Docker 沙箱 + 15 domain tools          │  │  ├ GPT agent                  │
│  ├ send_message + get_session_info        │  │  ├ 自定义 agent               │
│  ├ StudentSimulator（学生行为）             │◄─MCP─►│  ├ 人类 (Web UI)              │
│  ├ TCChecker / GoalChecker（终止判定）     │  │  │  └ reference adapter         │
│  ├ 格式校验 + 步数限制                      │  │  │    （仅用于跑 baseline）      │
│  ├ 结果保存（run_state.json）              │  │  │                              │
│  └ 评分流水线（QR + QP + Tutor 7D）        │  │  │  考生自带（不受限）:           │
│                                            │  │  │  ├ system prompt（任意）      │
│  Server 不知道 Client 是谁，               │  │  │  ├ 外部知识库                 │
│  不持有 adapter 引用，                      │  │  │  ├ Docker 外的工具            │
│  不调用 generate_response()                │  │  │  └ 任何 LLM / 推理引擎       │
└────────────────────────────────────────────┘  └──────────────────────────────┘
```

### 1.3 与 Legacy Harness 的关系

| | Exam Server（新） | Legacy Harness（保留） |
|---|---|---|
| **入口** | `python -m exam --task S01 --persona intermediate` | `python run_benchmark.py run-single --task S01 ...` |
| **Client 来源** | 外部 MCP client（任意） | 内部 adapter（Anthropic/OpenAI/Google） |
| **耦合度** | Server 不知道 Client | orchestrator 持有 adapter 引用 |
| **评分** | Server 侧独立运行 | orchestrator Phase 4 内嵌 |
| **用途** | 对外标准接口 | 内部跑 baseline / 消融实验 |
| **共享模块** | mcp_servers/、evaluation/、config/ | 同左 |

---

## 二、任务执行 + 评估完整流程

### Phase 0: DISCOVERY — Client 浏览任务

```
Client → list_tasks()
Server ← [
    {task_id: "S01_ma_crossover", category: "strategy", difficulty: "easy", description: "..."},
    {task_id: "D01_load_inspect_ohlcv", category: "data_analysis", difficulty: "easy", description: "..."},
    ...
]
```

Client 看到的是所有任务的公开信息（相当于考试大纲），不含 ground_truth。

### Phase 1: TASK INFO — Client 请求具体任务

```
Client → get_task_info(task_id="S01_ma_crossover", persona_id="intermediate_developer")
Server ← {
    task_description: "Guide a student to implement...",
    student_persona: {knowledge_level: "intermediate", background: "..."},
    student_opening: "Hi, I'm trying to understand...",
    max_turns: 30,
    max_steps_per_turn: 50,           ← Server 硬上限
    recommended_system_prompt: "...",   ← 推荐但不强制
    tool_schemas: [...]                ← 可用工具的 JSON Schema
}
```

Server 提供推荐的 system prompt（含 TUTOR_SYSTEM_PROMPT + task context），但 Client 可以用自己的 prompt。这是开卷考试——考场提供参考资料，用不用是考生的事。

### Phase 2: REGISTRATION — Client 提交配置，Server 验证格式

```
Client → register_session({
    agent_name: "my-agent-v1",
    model: "claude-sonnet-4-6",
    max_steps_per_turn: 30            ← Client 声明，不得超过 Server 硬上限
})

Server 验证:
  ✓ agent_name 非空字符串
  ✓ max_steps_per_turn ≤ SERVER_HARD_LIMIT (50)
  ✓ JSON schema 合规

  → accepted: 创建 Docker 容器 + 注册工具 + 初始化 TutoringSession
  → rejected: {error: "...", attempts_remaining: 2}  ← 最多 3 次尝试
```

**Server 验证的是格式，不是内容：**

| 验证 | 不验证 |
|------|--------|
| agent_name 非空 | system prompt 内容 |
| max_steps_per_turn ≤ 硬上限 | model 选择 |
| JSON schema 合规 | Client 是否使用推荐 prompt |

### Phase 3: SESSION — 考试进行

验证通过后，Server 创建 Docker 容器和工具环境，开始考试。

```
┌─ Turn N ──────────────────────────────────────────────────────────────┐
│                                                                        │
│  学生消息（首轮在 get_session_info 返回，后续在 send_message 返回值中）   │
│                                                                        │
│  Agent loop（一次完整迭代 = 一个 turn）:                                │
│                                                                        │
│    ┌─ Agent 内部（Server 不可见）──────────────────────────────────┐    │
│    │                                                               │    │
│    │  [思考] → [文字 block] → [tool_call: shell_exec]              │    │
│    │  [思考] → [文字 block] → [tool_call: compute_indicator]       │    │
│    │  [思考] → [文字 block: 最终回复]                               │    │
│    │                                                               │    │
│    │  Agent 将多段文字拼接为完整回复                                  │    │
│    │                                                               │    │
│    └───────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  Agent → call_tool("send_message", text="拼接后的完整回复")             │
│                                  ↑                                      │
│                          一个 turn 的唯一结束标志                        │
│                                  ↓                                      │
│  Server 内部处理:                                                       │
│    ① 记录 assistant message（send_message 的 text 参数）                │
│    ② 本 turn 的 tool_logs 已由 MCPProxy 自动记录（turn_index 归因）     │
│    ③ TC checker 判定（strategy/backtest/implementation/debug）          │
│       或 GoalChecker 判定（data_analysis/end_to_end/adversarial）       │
│    ④ 未结束 → StudentSimulator 生成学生回复                             │
│       已结束 → 返回 status="completed"                                  │
│    ⑤ 重置 turn_step_count                                              │
│                                                                        │
│  Server → {student_reply: "...", status: "active"|"completed",          │
│            turn: N, max_turns: 30}                                      │
│                                                                        │
│  步数限制: 两次 send_message 之间的 tool call 次数 ≤ max_steps_per_turn │
│  超过 → Server 拒绝后续 tool call，Agent 必须调 send_message 结束 turn  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**一个 turn 的严格定义：**
- 开始：Agent 收到学生消息（通过上一轮 send_message 的返回值，或首轮 get_session_info）
- 过程：Agent 在一个 agent loop 迭代内调用 N 次 domain tools + 产出多段 text block + 拼接回复
- 结束：Agent 调用 send_message(text=拼接后的回复)
- **一个 turn 内只有一次 send_message 调用，一个 agent loop 迭代对应一个 turn**

### Phase 4: RESULTS — 保存 + 评分

Session 结束后（status="completed" 或 timeout/max_turns），Server 自动执行：

```
① 格式校验
   - conversation: list[{role: str, content: str}] — 至少 1 轮完整 exchange
   - tool_logs: list[ToolCallLog] — 每条有 name + args + result + turn_index

② 保存 run_state.json（与 Legacy --evalonly 格式完全兼容）
   {
     task_id, persona_id,
     conversation: [{role, content}, ...],         ← TutoringSession 控制
     tool_logs: [{name, args, result, turn_index, duration_ms, success}, ...],  ← MCPProxy 控制
     distractor_names: [...],
     workspace_files: [...],
     agent_cost: {agent_name, model, ...},         ← Client 在 register_session 提交
     simulator_cost: float,                        ← StudentSimulator.total_cost
     duration_seconds: float,
     key_results: {...},                           ← 从 tool_logs 提取
     trace_summary: [...],                         ← 从 tool_logs 提取
     step_count: int                               ← 统计 substantive tool calls
   }

③ 复制 workspace → agent_files/

④ 评分（可选）
   --auto-eval: Session 结束后立即运行评分 → 保存 scores.md + trace.md + cost.md
   否则: Client 可调 request_evaluation() 触发，或稍后用 CLI 单独评分
```

**run_state.json 的格式保障：** conversation 和 tool_logs 由 Server 侧的 TutoringSession 和 MCPProxy 控制，Client 无法篡改结构。Client 只能影响 conversation 中 assistant message 的文本内容和调哪些工具——这正是评分要评的。

### Phase 5: TEARDOWN

```
Client 断开 / Server 超时 → 销毁 Docker 容器 → 清理临时目录
```

---

## 三、文件结构与模块解耦

### 3.1 目录规划

```
bench/
├── exam/                              ← 新架构：完全解耦的 Benchmark Server
│   ├── __main__.py                    #  CLI 入口
│   ├── exam_server.py                 #  ExamServer: Phase 0-5 完整生命周期
│   ├── protocol.py                    #  协议定义 + 验证规则 + 硬上限常量
│   ├── format_validator.py            #  run_state 格式校验
│   ├── result_writer.py               #  结果保存（复用 evaluation/ 模块）
│   ├── eval_runner.py                 #  独立评分入口（从 orchestrator._evaluate_task 提取）
│   └── exam_tools.py                  #  Exam 专属 MCP tools（list_tasks, get_task_info,
│                                      #    register_session, get_system_prompt, request_evaluation）
│
├── mcp_servers/                       ← 共享基础设施（Server 核心）
│   ├── session.py                     #  TutoringSession（对话状态管理 + 终止判定）
│   ├── student_sim.py                 #  StudentSimulator（学生消息生成，已对齐 DeepEval）
│   ├── tc_checker.py                  #  TCChecker（增量 TC 类别终止）
│   ├── mcp_server.py                  #  MCP server 封装（create_mcp_server, run_server_stdio）
│   ├── registry.py                    #  工具注册（core + convenient + distractor + session tools）
│   ├── proxy/                         #  MCPProxy（统一工具日志、截断、deadline）
│   └── core/                          #  工具实现（shell_exec, file_read, fetch_market_data, ...）
│
├── evaluation/                        ← 共享评分模块
│   ├── eval_pipeline.py               #  新：独立评分入口（从 orchestrator._evaluate_task 提取）
│   ├── scoring.py                     #  OAS/QAI/TEI 公式
│   ├── score_report.py                #  scores.md 生成
│   ├── trace_report.py                #  trace.md 生成
│   ├── cost_report.py                 #  cost.md 生成
│   └── deepeval_metrics/              #  LLM judge（result_judge, process_metrics, tutor_conv_geval）
│
├── config/                            ← 共享配置
│   ├── prompt_config.py               #  TUTOR_SYSTEM_PROMPT + build_scenario + build_user_description
│   ├── llm_config.py                  #  模型配置
│   ├── model_resolver.py              #  DeepEval model 解析
│   └── benchmark_config.py            #  数据集版本等
│
├── orchestrator/                      ← Legacy Harness（保留，不改动）
│   ├── orchestrator.py                #  BenchmarkOrchestrator（reference runner）
│   ├── agent_adapters/                #  Reference adapters（Anthropic/OpenAI/Google/Generic）
│   ├── runners/                       #  Job runner（并行执行 + 结果保存）
│   ├── simulation.py                  #  Legacy 对话路径（run_conversation_simulation + run_agent_session）
│   └── schemas.py                     #  TaskResult, BenchmarkReport
│
└── run_benchmark.py                   ← Legacy CLI（保留，不改动）
```

### 3.2 依赖关系

```
bench/exam/                      bench/orchestrator/
  ├── imports mcp_servers/         ├── imports mcp_servers/
  ├── imports evaluation/          ├── imports evaluation/
  ├── imports config/              ├── imports config/
  ├── 不 import orchestrator/      ├── imports agent_adapters/  ← harness 专用
  └── 不 import run_benchmark      └── 不 import exam/
```

**两个入口完全独立，通过共享模块间接关联：**

```
exam/__main__.py ─────→ mcp_servers/ ←───── orchestrator.py
                  ─────→ evaluation/  ←─────
                  ─────→ config/      ←─────
```

---

## 四、Server 侧控制流详述（与 Legacy 对齐）

### 4.1 对话控制权在 Server 侧

无论 Exam Server 还是 Legacy Harness，对话的推进和终止都由 Server 控制：

```
Server 控制（不可替换）:                Client 控制（可替换）:
├ 学生消息由谁生成 → StudentSimulator   ├ 每次 send_message 说什么
├ 对话何时终止 → TC/Goal/max_turns     ├ 两次 send_message 之间调哪些工具
├ 学生回复内容 → Client 只读            ├ 自己的 system prompt
└ tool_logs 格式 → MCPProxy 控制        └ 自己的推理过程
```

### 4.2 Turn 内的步数限制

```python
# MCPProxy 或 TutoringSession 中的防护逻辑

class TutoringSession:
    def __init__(self, ..., max_steps_per_turn: int = 50):
        self._max_steps_per_turn = max_steps_per_turn
        self._turn_step_count = 0

# proxy.call_tool() 中增加检查：
def call_tool(self, name, **kwargs):
    if name == "send_message":
        self._session._turn_step_count = 0  # send_message 重置计数
        return self._session.handle_send_message(**kwargs)

    # 非 send_message 的 tool call
    self._session._turn_step_count += 1
    if self._session._turn_step_count > self._session._max_steps_per_turn:
        return json.dumps({
            "error": f"Step limit ({self._session._max_steps_per_turn}) reached. "
                     f"Call send_message to end this turn.",
            "status": "step_limit_exceeded"
        })

    # 正常执行
    ...
```

### 4.3 终止机制：7 类别全覆盖（与 Legacy 完全一致）

| 类别 | 终止方式 | 实现 | Legacy 对标 |
|------|---------|------|------------|
| strategy | 增量 TC bitmap | TCChecker.check() | _EfficientSimulator.stop_conversation() |
| backtest | 增量 TC bitmap | TCChecker.check() | 同上 |
| implementation | 增量 TC bitmap | TCChecker.check() | 同上 |
| debug | 增量 TC bitmap | TCChecker.check() | 同上 |
| data_analysis | Goal-based LLM 判定 | GoalChecker.check() | DeepEval stop_simulation prompt |
| end_to_end | Goal-based LLM 判定 | GoalChecker.check() | 同上 |
| adversarial | Goal-based LLM 判定 | GoalChecker.check() | 同上 |

GoalChecker 使用的 prompt 逐字复制自 DeepEval `template.py:stop_simulation()`。
expected_outcome 构建逻辑逐行复制自 `build_conversational_golden()`。

### 4.4 学生消息生成：与 Legacy bit-exact 对齐

| 组件 | MCP 实现 | Legacy 对标 | 等价性 |
|------|---------|------------|--------|
| 首轮 prompt | `_FIRST_MESSAGE_PROMPT` | `template.py:simulate_first_user_turn()` | 逐字复制 |
| 后续 prompt | `_NEXT_MESSAGE_PROMPT` | `template.py:simulate_user_turn()` | 逐字复制 |
| 对话历史格式 | JSON array `[{role, content}]` | `json.dumps([t.model_dump()])` | bit-exact |
| 输出解析 | `model.generate(schema=SimulatedInput)` + fallback | `generate_schema(SimulatedInput)` + `trimAndLoadJson` | 等价 |
| Closing 生成 | `generate_closing()` + 硬编码 fallback | `_generate_closing()` + 硬编码 fallback | 同文本 |
| scenario 内容 | `build_scenario()` | 同一函数 | identical |
| user_description | `build_user_description()` | 同一函数 | identical |

### 4.5 错误处理：与 Legacy 防御层对齐

| 防御层 | session.py 实现 | Legacy 对标 |
|--------|----------------|------------|
| student_sim 异常 → fallback 消息 | try/except + `_STUDENT_FALLBACK` | model_callback:567-586 |
| closing 异常 → 硬编码文本 | `_safe_closing()` | _generate_closing:397-405 |
| tc_checker 异常 → 继续对话 | try/except, tc_met=False | _EfficientSimulator 内部 |
| goal_checker 异常 → 继续对话 | try/except, goals_met=False | DeepEval stop_conversation |
| 超时 → closing + completed | deadline 检查 + _safe_closing | model_callback:517-537 |
| Agent 重复 → force-stop | `_repeat_count >= 2` | model_callback:606-624 |
| Max turns → closing + completed | 追加 _safe_closing | _append_student_closing:642-678 |
| Cost 追踪 | `student_sim.total_cost` | `simulator.simulation_cost` |

---

## 五、结果保存与评分

### 5.1 run_state.json 格式（与 Legacy --evalonly 完全兼容）

**必需字段（评分链依赖）：**

| 字段 | 类型 | 来源 | Client 能否影响 |
|------|------|------|----------------|
| `conversation` | `list[{role, content}]` | TutoringSession._conversation | 只能影响 assistant content 的文本 |
| `tool_logs` | `list[ToolCallLog]` | MCPProxy._logs | 只能影响调哪些工具和参数 |

**可选字段（降级优雅）：**

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `distractor_names` | `list[str]` | `[]` | tool_usage 评分 |
| `workspace_files` | `list[str]` | `[]` | 元数据 |
| `agent_cost` | `dict` | `{}` | 成本报告 |
| `simulator_cost` | `float` | `0.0` | 成本报告 |
| `duration_seconds` | `float` | `0.0` | 元数据 |
| `key_results` | `dict` | `{}` | reference 生成 |
| `trace_summary` | `list` | `[]` | reference 生成 |
| `step_count` | `int` | `0` | reference 生成 |

### 5.2 评分提取：eval_pipeline.py

从 `orchestrator._evaluate_task()` 提取为独立函数。原方法只依赖 2 个 self 属性：

```python
# bench/evaluation/eval_pipeline.py

def evaluate_task(
    task,                          # QuantTutorTask
    persona,                       # StudentPersona
    workspace_path: str,           # agent_files/ 目录
    conversation: list[dict],      # [{role, content}, ...]
    tool_logs: list,               # [ToolCallLog, ...]
    distractor_names: list[str],   # distractor 工具名
    bench_root: str,               # 替代 self.bench_root
    eval_model: str,               # 替代 self.eval_model
    cancel_event=None,
    eval_mode: str = "full",
) -> dict:
    """独立评分入口。逻辑与 orchestrator._evaluate_task() 完全相同。"""
    # 实现：将 _evaluate_task 的 ~500 行代码搬入，
    # 替换 self.bench_root → bench_root, self.eval_model → eval_model
    ...
```

### 5.3 评分时机

| 模式 | 触发方式 |
|------|---------|
| `--auto-eval` | Session 结束后 Server 立即运行评分 |
| Client 请求 | Client 调 `request_evaluation()` tool 触发 |
| CLI 后置 | `python -m exam eval --result-dir path/to/results` 单独评分 |

---

## 六、完整修改清单

### 6.1 新建文件（bench/exam/）

| 文件 | 职责 | 估算行数 |
|------|------|---------|
| `__main__.py` | CLI 入口：`python -m exam --task S01 --persona intermediate [--auto-eval] [--docker]` | ~60 |
| `exam_server.py` | ExamServer 类：Phase 0-5 生命周期管理，串联所有组件 | ~250 |
| `protocol.py` | 协议常量 + register_session schema + 验证规则 + SERVER_HARD_LIMIT | ~60 |
| `format_validator.py` | 校验 conversation + tool_logs 格式完整性 | ~40 |
| `result_writer.py` | 保存 run_state.json + scores.md + trace.md + cost.md（复用 evaluation/ 模块） | ~120 |
| `eval_runner.py` | 调用 eval_pipeline.evaluate_task() 的 wrapper（处理 cancel、error） | ~50 |
| `exam_tools.py` | Exam 专属 MCP tools 注册：list_tasks, get_task_info, register_session, get_system_prompt, request_evaluation | ~150 |

### 6.2 新建文件（bench/evaluation/）

| 文件 | 职责 | 估算行数 |
|------|------|---------|
| `eval_pipeline.py` | 从 orchestrator._evaluate_task() 提取的独立评分函数 | ~80 |

### 6.3 修改已有文件

| 文件 | 修改内容 | 估算行数 |
|------|---------|---------|
| `mcp_servers/session.py` | 增加 `max_steps_per_turn` 参数 + per-turn step counter | ~15 |
| `mcp_servers/proxy/mcp_proxy.py` | 增加 step count 检查逻辑（或在 session 层做） | ~10 |

### 6.4 不改动的文件

| 文件/目录 | 原因 |
|----------|------|
| `run_benchmark.py` | Legacy CLI，保留 |
| `orchestrator/orchestrator.py` | Legacy harness，保留 |
| `orchestrator/agent_adapters/` | Reference adapters，harness 专用 |
| `orchestrator/runners/` | Legacy job runner，保留 |
| `orchestrator/simulation.py` | Legacy 对话路径，保留 |
| `mcp_servers/student_sim.py` | 已在 Batch 1 对齐，不再改动 |
| `mcp_servers/tc_checker.py` | 已完成，不再改动 |
| `mcp_servers/registry.py` | 已完成，Exam tools 在 exam_tools.py 中注册 |
| `evaluation/scoring.py` | 共享模块，不改动 |
| `evaluation/deepeval_metrics/` | 共享模块，不改动 |

### 6.5 行数汇总

| 类别 | 行数 |
|------|------|
| 新建（bench/exam/） | ~730 |
| 新建（evaluation/eval_pipeline.py） | ~80 |
| 修改已有 | ~25 |
| **合计新增** | **~835** |
| 不改动 Legacy | 0 |

---

## 七、分批实施计划

### Batch A：评分提取（无外部依赖）

**新建：** `evaluation/eval_pipeline.py`
**修改：** 无
**验证：** 用现有 run_state.json 调 evaluate_task()，对比与 orchestrator._evaluate_task() 结果一致

### Batch B：协议 + 格式校验（无外部依赖）

**新建：** `exam/protocol.py` + `exam/format_validator.py`
**验证：** 用现有 run_state.json 测试格式校验通过/拒绝

### Batch C：步数限制（修改共享模块）

**修改：** `session.py` + `proxy/mcp_proxy.py`
**验证：** 单元测试——超过 max_steps_per_turn 后 tool call 被拒绝

### Batch D：Exam Server 核心（依赖 A+B+C）

**新建：** `exam/exam_server.py` + `exam/exam_tools.py` + `exam/result_writer.py` + `exam/eval_runner.py`
**验证：** 用 Claude Code 作为 MCP client 连接 Exam Server，跑一个 task 端到端

### Batch E：CLI 入口（依赖 D）

**新建：** `exam/__main__.py`
**验证：** `python -m exam --task S01_ma_crossover --persona intermediate_developer --docker --auto-eval`

### 批次依赖

```
Batch A (eval_pipeline)  ──┐
Batch B (protocol)       ──┼──→ Batch D (exam_server) ──→ Batch E (CLI)
Batch C (step limit)     ──┘
```

A/B/C 可并行。D 依赖 A+B+C。E 依赖 D。

---

## 八、与 mcp_student_sim_migration.md 的关系

mcp_student_sim_migration.md 解决的是 **Server 内部策略层与 Legacy 的对齐**（学生 prompt、历史格式、终止机制、错误处理）。该方案的 Batch 1-3 已执行完毕，成果直接被本方案继承：

| migration.md 成果 | 本方案中的位置 |
|-------------------|---------------|
| StudentSimulator prompt 对齐 | §4.4 — 已完成，直接使用 |
| GoalChecker 类 | §4.3 — 已完成，session.py 中 |
| 错误处理防御层 | §4.5 — 已完成，session.py 中 |
| Cost tracking | §5.1 — 已完成，student_sim.total_cost |

本方案在此基础上解决的是 **Server 与 Client 的解耦**——新建 bench/exam/ 目录，实现独立的任务执行+评估流程。
