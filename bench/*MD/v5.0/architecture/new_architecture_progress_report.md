# QuantTutorBench 新架构文档

> 更新日期：2026-04-16
> 分支：ewan
> 本文档是新架构的唯一权威文档，合并了设计原则、功能详解、实现状态。

---

## 一、设计原则

### 1.1 考试隐喻

Server 是考场，Client 是考生。考场提供试卷（任务）、文具（工具）、监考（学生模拟器+TC）、打分（评分链）。考生可以带小抄（system prompt）、查资料（外部知识库）、用更好的笔（更强的 LLM）——考场不管也管不了。

**考场管的是：答题纸格式对不对、考试流程有没有遵守、最终交卷内容能不能评分。**

### 1.2 解耦边界

```
┌─ Server（bench/server/）─────────────┐  ┌─ Client（可替换）────────────────┐
│                                       │  │                                 │
│  考场设施（不可替换）:                  │  │  任何 MCP/REST client:            │
│  ├ 任务定义 + 学生人格                 │  │  ├ Baseline adapter（跑实验）      │
│  ├ Docker 沙箱 + domain tools        │  │  ├ Claude Code / GPT agent       │
│  ├ send_message + get_background     │◄─MCP/REST─►├ 人类（Web UI）         │
│  ├ StudentSimulator（学生行为）        │  │  └ 任何第三方 agent               │
│  ├ TC/GoalChecker（终止判定）         │  │                                 │
│  ├ 结果保存（run_state.json）         │  │  考生自带（不受限）:               │
│  └ 评分流水线（QR + QP + Tutor 7D）   │  │  ├ system prompt（任意）          │
│                                       │  │  ├ 外部知识库                     │
│  Server 不知道 Client 是谁，          │  │  ├ Docker 外的工具                │
│  不持有 adapter 引用，                 │  │  └ 任何 LLM / 推理引擎            │
│  不调用 generate_response()           │  │                                 │
└───────────────────────────────────────┘  └─────────────────────────────────┘
```

### 1.3 控制权划分

```
Server 控制（不可替换）:                Client 控制（可替换）:
├ 学生消息由谁生成 → StudentSimulator   ├ 每次 send_message 说什么
├ 对话何时终止 → TC/Goal/max_turns     ├ 两次 send_message 之间调哪些工具
├ 学生回复内容 → Client 只读            ├ 自己的 system prompt
├ tool_logs 格式 → MCPProxy 控制        └ 自己的推理过程
└ run_state.json 结构 → Client 无法篡改
```

### 1.4 三条核心设计原则

**原则一：Server 提供环境事实，不提供行为指导。** 如果 Agent 在收到环境背景后仍然不探索环境、不使用回测工具、不回复学生，这是 Agent（Client）的能力问题——这正是 benchmark 要测量的。Server 不应通过更积极的引导来弥补 Agent 的能力缺陷，否则会泄漏评分标准、降低 benchmark 的区分度。

**原则二：Server 验证格式，不验证内容。** Server 检查 agent_name 非空、max_steps 不超硬上限、JSON schema 合规——但不检查 system prompt 内容、不限制 model 选择、不审查 Client 是否使用了推荐 prompt。这是开卷考试——考场提供参考资料，用不用是考生的事。

**原则三：对话推进和终止由 Server 控制，Client 只决定说什么和做什么。** 无论通过 MCP 还是 REST，conversation 和 tool_logs 由 Server 侧的 TutoringSession 和 MCPProxy 控制，Client 只能影响 assistant message 的文本内容和调哪些工具——这正是评分要评的。

---

## 二、架构概览

### 2.1 架构转型

QuantTutorBench 已从 **Legacy 一体化 Harness**（orchestrator 内嵌 adapter + 评分）迁移到 **Client-Server 解耦架构**（Server 是考场，Client 是考生）。

### 2.2 为什么要做这个转型

| 问题 | Legacy 的局限 | 新架构的解决方案 |
|------|-------------|----------------|
| **第三方接入** | 必须实现 Python adapter、嵌入到 orchestrator 中 | 任何能说 MCP 或 HTTP 的 agent 都可以直接接入 |
| **耦合度** | orchestrator 持有 adapter 引用，agent 和评分在同一进程 | Server 不知道 Client 是谁，不持有 adapter 引用 |
| **评分独立性** | 评分嵌在 orchestrator Phase 4 中 | 评分作为独立 pipeline，可异步触发 |
| **可观测性** | 只有 proxy 日志 | 完整的 run_state.json + Web UI 回放 |
| **DeepEval 依赖** | Student simulator、评分均依赖 DeepEval 类 | 全链路去 DeepEval 化（ewan_eval） |

---

## 三、功能详解

### 3.1 Server：考场设施

Server 是 benchmark 的核心，提供考场的全部功能。通过 `python -m server --port 8000 --docker` 启动。

#### 3.1.1 双协议接入（MCP + REST）

Server 同时暴露两个等价的接入协议：

| 协议 | 端点 | 适用场景 |
|------|------|---------|
| **MCP** | `/mcp` (StreamableHTTP) | Claude Code / SDK agent 原生接入，工具自动发现 |
| **REST** | `/session/*` | curl / httpx / 非 MCP agent，更灵活 |

两个协议共享同一套 Server 逻辑、同一套权限规则、同一套评分流水线。

#### 3.1.2 会话状态机

Server 通过 4 阶段状态机严格管控会话生命周期：

```
UNREGISTERED ──register_session()──→ REGISTERED ──start_session()──→ IN_SESSION ──TC/Goal触发──→ COMPLETED
```

| 阶段 | 允许的操作 | 禁止的操作 |
|------|-----------|-----------|
| **UNREGISTERED** | register_session | 其他所有 |
| **REGISTERED** | start_session, list_tools | register, send, tools, evaluate |
| **IN_SESSION** | send_message, get_background, domain tools | register, start, evaluate |
| **COMPLETED** | request_evaluation, get_results, get_scores | register, start, send, tools |

#### 3.1.3 Session Background（环境背景）

Server 在 `start_session` 时**强制返回**一份环境背景描述（`background` 字段），内容根据任务环境动态生成。同时提供 `get_background` 工具供 Agent 在对话中随时重读。

**固定内容（所有任务一致）：**
- 沙箱环境说明（Python 运行时可用）
- 通信约束：**必须通过 `send_message` 工具与学生通信**，文本输出对学生不可见

**动态内容（按任务环境拼接）：**

| 段落 | 触发条件 | 内容 |
|------|---------|------|
| 算法回测引擎 | sandbox_image 含 "lean" | C# 编译/回测/结果查看，试用预算有限 |
| 学生代码 | task.sample_code 非空 | 挂载位置 /student_code/ |
| 参考文档 | docs_available 非空 | 挂载位置 /docs/ |
| 市场数据 | data_files 非空 | 挂载位置 /data/ |

Background **不包含**：任务类别、学生水平、回测预算数字、行为指导、评分标准。Agent 需要通过 `get_environment_info` 和对话来发现这些细节。

#### 3.1.4 工具管理与执行

**工具分类**：
- **Core tools**：任务必需的领域工具（shell_exec, file_read, run_lean_backtest 等）
- **Convenient tools**：加分捷径（run_backtest, compute_indicator 等）
- **Distractor tools**：干扰项（最多填满 15 个槽位），测试 agent 的工具选择判断
- **Session tools**：`send_message`（与学生通信的唯一通道）和 `get_background`（重读环境背景），Agent 可见可用

`send_message` 的工具描述中明确声明："This is the ONLY way to communicate with the student. Your text output is NOT delivered to the student."

**Docker 执行**：
```
Agent tool call → MCPProxy（日志+截断12K） → ContainerManager → tool_executor daemon（stdin/stdout JSON-lines） → tools.py
```

**安全限制**：per-turn 步数限制、deadline enforcement、cancel event、结果截断

#### 3.1.5 学生模拟器

- Prompt 模板与 DeepEval ConversationSimulator **逐字对齐**，确保与 Legacy 行为一致
- 当前支持 3 个 persona 级别（beginner / intermediate / advanced）；人格体系正在重构为 {Finance, Code} × {听说未实践, 精通} 四象限矩阵（[Issue #12](https://github.com/varsity-tech-product/benchmark/issues/12)）
- 多层 fallback：结构化 JSON 输出 → 纯文本 + JSON 提取 → 硬编码兜底
- 独立成本追踪

#### 3.1.6 评分流水线（独立运行）

新架构的评分流水线完全独立于 orchestrator，可以异步触发：

```
evaluate_task(run_state)
  ├── QR（Quant Result）
  │    ├── Programmatic eval（test scripts，100% 确定性）
  │    ├── Code eval（Static + Execution + Output verification）
  │    └── Result judge（LLM）
  ├── QP（Quant Process）
  │    ├── tool_usage（纯数学）
  │    ├── step_efficiency（混合）
  │    ├── process_reasonableness（LLM）
  │    ├── code_process（混合）
  │    ├── process_alignment（LLM）
  │    ├── role_adherence（LLM）
  │    └── topic_adherence（LLM）
  └── Tutor 7D（纯 LLM，ConversationalGEval）
       ├── D1-D7 × 3 shuffled runs × N models
       └── Persona-aware rubric
```

**去 DeepEval 化**：Server 的评分引擎（`server/eval/ewan_eval/`）不依赖 DeepEval 类，使用 `EwanLLMClient` 通过 OpenRouter 调用 LLM API（已删除 OAuth 路径）。

#### 3.1.7 Web UI

Server 内嵌 Web UI（`/ui/*`），提供：
- 已完成会话的浏览和索引
- 对话回放（包括工具调用详情）
- Client trace 的 best-effort 加载：若 `results/client/{session_id}/client_trace.json` 存在则合并展示（thinking blocks 等），不存在则纯展示 Server 侧对话
- 评分结果查看

#### 3.1.8 会话清理

后台 sweeper 任务定期检查空闲会话：
- UNREGISTERED/REGISTERED：5 分钟空闲后清理
- COMPLETED：1 小时后清理
- 含 Docker 容器的销毁和临时工作区清理

### 3.2 Client：Baseline Runner

Client 是一个轻量级的 baseline 运行器。通过 `python -m client --server http://localhost:8000/mcp --task X01` 启动。

#### 3.2.1 核心架构

Client 只做 session setup，整个教学对话由 Anthropic BetaToolRunner 在单次调用中自主完成：

```
Runner: connect → register → start_session（获取 background）→ list_tools
        → 注入 background 到 conversation
        → 单次 adapter.generate_response()
            → BetaToolRunner 内部自主：
              调工具、调 send_message 与学生对话、接收学生回复、
              继续调工具和对话...直到 session completed
        → 保存 client trace
```

**没有外层对话循环**。Agent 通过 `send_message` 工具自主控制对话节奏，BetaToolRunner 管理完整的 tool-use loop。

#### 3.2.2 Adapter

| 模式 | 实现 | 说明 |
|------|------|------|
| **Direct API** (默认) | Anthropic BetaToolRunner | 单次调用管理完整 session，SDK 内置 compaction 和 context management |
| **SDK** | Claude Agent SDK | 黑盒 agent loop |

支持 OpenRouter 代理转发、Extended Thinking (COT)。SDK 内置的 compaction（40K token 阈值）和 context management（保留最近 6 个 tool_use、1 个 thinking turn）由 SDK 原生管理。

#### 3.2.3 产出物

`client_trace.json` / `client_trace.md`：完整的 agent 行为记录（thinking blocks、content blocks、tool calls、agent cost）

#### 3.2.4 并发支持

`--workers N` 参数支持多任务并行，使用 semaphore 控制并发数。默认 `--agent-max-steps 200`（单次 BetaToolRunner 的 iteration 上限）。

### 3.3 Spec：用户文档

| 文档 | 内容 |
|------|------|
| `spec/PROTOCOL.md` | 会话生命周期、MCP/REST 操作详解、错误格式 |
| `spec/TASKS.md` | 65 个任务 ID 列表（按类别分组，含类别简介，不含任务名后缀/难度/描述） |

---

## 四、新旧架构对比

### 4.1 架构层面

| 维度 | Legacy Harness | 新架构（Server + Client） |
|------|---------------|--------------------------|
| **入口** | `python run_benchmark.py run-single --task S01` | Server: `python -m server --port 8000`<br>Client: `python -m client --server ... --task S01` |
| **进程模型** | 单进程（agent + benchmark 同进程） | 双进程（Server 独立，Client 独立） |
| **通信** | 函数调用（adapter.generate_response） | MCP StreamableHTTP / REST API |
| **Agent 接入** | 必须实现 Python BaseAgentAdapter | 任何 MCP/HTTP client |
| **Agent 可见性** | Server 持有 adapter 引用，知道 agent 细节 | Server 完全不知道 Client 是谁 |
| **send_message** | Runner 代理调用，Agent 不可见 | **Agent 可见的 domain tool，自主调用** |
| **环境背景** | 通过 system prompt 注入完整任务上下文 | **Server 通过 background 提供事实性环境描述，不含行为指导** |
| **对话管理** | Runner 外层循环控制多轮对话 | **Agent 在 BetaToolRunner 内自主控制** |
| **评分触发** | orchestrator Phase 4 内嵌 | 独立 pipeline，可异步触发 |
| **DeepEval 依赖** | 深度依赖 | 完全去依赖（ewan_eval，OpenRouter） |
| **结果存储** | `results/{task_id}/{timestamp}/` | `results/server/{task_id}/{persona_id}/{timestamp}_{session_id}/` |

### 4.2 执行流程对比

**Legacy（5 Phase）**：
```
orchestrator.run_single_task()
  Phase 1: RESET（创建容器、配工具、注入丰富的 system prompt）
  Phase 2: INTERACT（Runner 外层循环：generate_response → send_message → 学生回复）
  Phase 3: CAPTURE（收集 workspace + proxy logs + enrichment）
  Phase 4: EVALUATE（内嵌评分）
  Phase 5: TEARDOWN（清理容器）
```

**新架构（Client-Server 分离）**：
```
Server 侧:
  register → 创建容器 + 工具 + persona
  start → background + 学生开场白
  [Agent 自主调 send_message + domain tools] → 学生回复 + TC 检查
  completed → 保存 run_state.json
  request_evaluation → 后台评分 → eval_meta.json

Client 侧:
  connect → register → start（获取 background）→ list_tools
  → 单次 adapter.generate_response（BetaToolRunner 管理完整 session）
  → 保存 client_trace.json
```

### 4.3 依赖隔离

```
server/  只 import → server/ 内部 + scripts/data_manager + 外部库
client/  只 import → client/ 内部 + 外部库

禁止:
  server/ → orchestrator/, mcp_servers/, evaluation/, config/
  client/ → orchestrator/, mcp_servers/, evaluation/, config/, server/
  orchestrator/ → server/, client/
```

---

## 五、已完成的工作

### 5.1 Server 核心功能

| 模块 | 状态 | 说明 |
|------|------|------|
| `server/__main__.py` | ✅ | Uvicorn 启动，支持 --port, --docker |
| `server/api/http_app.py` | ✅ | Starlette app，MCP + REST 双协议，会话管理 |
| `server/api/protocol.py` | ✅ | 4 阶段状态机，权限检查，send_message/get_background 工具定义 |
| `server/api/session_api.py` | ✅ | SessionState 完整生命周期，get_background 路由 |
| `server/core/session.py` | ✅ | TutoringSession + GoalChecker + `build_background()` 动态生成 |
| `server/core/student_sim.py` | ✅ | 去 DeepEval，prompt 对齐 |
| `server/core/tc_checker.py` | ✅ | 增量 TC + 直接 OpenRouter 调用 |
| `server/core/proxy.py` | ✅ | 透明日志 + 截断 + 防御层 |
| `server/core/registry.py` | ✅ | core/convenient/distractor 注册 |
| `server/core/container.py` | ✅ | Docker 管理 + 本地 fallback |
| `server/core/tools/` | ✅ | 50+ domain tools + C# 入口推断 |
| `server/eval/pipeline.py` | ✅ | 独立评分入口 |
| `server/eval/ewan_eval/` | ✅ | 去 DeepEval + 去 OAuth，纯 OpenRouter |
| `server/storage/` | ✅ | run_state.json + eval_meta.json |
| `server/web/` | ✅ | Web UI 回放，client trace best-effort 合并 |

### 5.2 Client 核心功能

| 模块 | 状态 | 说明 |
|------|------|------|
| `client/__main__.py` | ✅ | CLI 入口，默认 --agent-max-steps 200 |
| `client/runner.py` | ✅ | 精简架构：setup → 单次 generate_response → 保存 trace |
| `client/tool_bridge.py` | ✅ | Sync-Async 桥接 |
| `client/adapters/anthropic_adapter.py` | ✅ | 单次调用，无 `_input_history`，SDK 原生 compaction/context management |
| `client/cost_tracker.py` | ✅ | Token 计费 |
| `client/trace_writer.py` | ✅ | 客户端 trace 输出 |

### 5.3 Spec 文档

| 文档 | 状态 | 说明 |
|------|------|------|
| `spec/PROTOCOL.md` | ✅ | 会话生命周期、操作详解 |
| `spec/TASKS.md` | ✅ 已更新 | 65 任务 ID 列表，按类别分组 + 类别简介，不含任务名后缀/难度/描述 |

---

## 六、待验证与残留问题

### 6.1 核心验证项

| 项目 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| **协议约束传达有效性** | P0 | 🟡 待验证 | Agent 是否在 background + 工具描述的约束下正确使用 send_message 通信。这是新架构从"runner 代理"转向"agent 自主"后的关键验证 |
| **单次 agent loop 稳定性** | P0 | 🟡 待验证 | 单次 BetaToolRunner 管理完整 session（可能 100+ iterations），需要验证在多种任务类型上的稳定性（compaction、timeout、session 终止） |
| **新旧评分一致性** | P0 | 🔴 未开始 | ewan_eval（OpenRouter）与 Legacy eval（DeepEval）在同一对话上的评分是否一致 |
| **全量任务覆盖** | P0 | 🔴 未开始 | 65 个任务在 Server 路径下是否都能正常运行和评分 |

### 6.2 工程收尾

| 项目 | 优先级 | 说明 |
|------|--------|------|
| 非正常终止时的结果保存 | P1 | session 被人工中止或 sweeper 清理时，应尝试 flush run_state |
| Legacy / Server 代码去重 | P1 | mcp_servers/ 和 server/core/ 存在功能重叠，需确定 source of truth |
| BENCHMARK_SPEC.md | P2 | 合并 PROTOCOL.md + TASKS.md + 接入示例 |

---

## 七、评分体系同步改进

### 7.1 评分公式与权重调整

**Implementation 类任务权重重平衡**：behavioral_score 0.60→0.45，code_patterns 0.05→0.10-0.15。

**Code eval 改进**：执行质量层（Layer B）从"最后一次试验"改为"最佳试验"。

**Result judge 新增准则**：Guideline 5 "CODE MUST BE EXECUTED TO COUNT"。

### 7.2 Tutor 评分优化

**Phase 1 缓存**：ConversationalGEval 的 evaluation steps 按 (model, dim) 缓存，节省 `(num_judge_runs - 1) × dims` 次 LLM 调用。

**Per-run raw scores 追踪**：记录每次 shuffled run 的原始分数。

### 7.3 TC 扩展

增量 TC 检查类别从 `{strategy, backtest, implementation}` 扩展到包含 `debug` 和 `data_analysis`。

### 7.4 消融实验

Tutor 评分粒度消融：D1-D7 clamp 到 5 档后 Cohen's d 从 1.745 变为 1.816（+4.1%），证明区分度来源于真实能力差距。

---

## 八、总结

### 8.1 当前状态

> **新架构核心功能和基础设施已全部实现。Server 工程稳定性已通过多轮实际执行确认。当前阶段的核心工作是验证：在 Server 仅提供环境事实（不提供行为指导）的设计下，Agent 能否通过协议传达的约束正确完成任务。**

### 8.2 关键里程碑

```
✅ 已完成:
   Server 双协议 + 状态机 + 去 DeepEval/OAuth + background 机制
   Client 单次调用架构 + send_message 作为 agent 自主 domain tool
   Web UI + client trace best-effort 合并
   Spec 文档（PROTOCOL.md + TASKS.md）
   Server 工程稳定性确认（多轮多任务实际执行验证）

⬜ 待验证/待开始:
   协议约束传达有效性（send_message 通信行为）
   单次 agent loop 全 session 稳定性
   新旧评分引擎一致性
   65 任务全量覆盖
```
