# QuantTutorBench 新架构进展报告

> 日期：2026-04-14
> 分支：ewan
> 对标文档：overall_architecture.md, architecture_implementation_plan.md, stage_report.md

---

## 一、新架构概览：从"一体化 Harness"到"考场-考生分离"

### 1.1 架构转型的核心目标

QuantTutorBench 正在从 **Legacy 一体化 Harness**（orchestrator 内嵌 adapter + 评分）迁移到 **Client-Server 解耦架构**（Server 是考场，Client 是考生）。

```
旧架构（Legacy Harness）:
┌──────────────────────────────────────────────────────────────┐
│ orchestrator.py                                              │
│  持有 adapter 引用 → 调 generate_response → 管理 session → 评分  │
│  Agent 和 Benchmark 在同一个进程中                              │
└──────────────────────────────────────────────────────────────┘

新架构（Client-Server）:
┌── Server（bench/server/）──────────┐     ┌── Client（bench/client/）───────┐
│  考场设施（不可替换）:               │     │  任意 MCP/REST client:          │
│  ├ 任务定义 + 学生模拟器            │     │  ├ Baseline adapter（跑实验）    │
│  ├ Docker 沙箱 + domain tools      │◄─MCP/REST─►│  ├ Claude Code / GPT agent     │
│  ├ TC/Goal 终止判定                │     │  ├ 人类（Web UI）                │
│  ├ 结果保存                        │     │  └ 任何第三方 agent              │
│  └ 评分流水线                      │     └────────────────────────────────┘
└────────────────────────────────────┘
```

### 1.2 为什么要做这个转型

| 问题 | Legacy 的局限 | 新架构的解决方案 |
|------|-------------|----------------|
| **第三方接入** | 必须实现 Python adapter、嵌入到 orchestrator 中 | 任何能说 MCP 或 HTTP 的 agent 都可以直接接入 |
| **耦合度** | orchestrator 持有 adapter 引用，agent 和评分在同一进程 | Server 不知道 Client 是谁，不持有 adapter 引用 |
| **评分独立性** | 评分嵌在 orchestrator Phase 4 中 | 评分作为独立 pipeline，可异步触发 |
| **可观测性** | 只有 proxy 日志 | 完整的 run_state.json + Web UI 回放 |
| **DeepEval 依赖** | Student simulator、评分均依赖 DeepEval 类 | 全链路去 DeepEval 化（ewan_eval） |

---

## 二、新架构功能详解

### 2.1 Server：考场设施

Server 是 benchmark 的核心，提供考场的全部功能。通过 `python -m server --port 8000 --docker` 启动。

#### 2.1.1 双协议接入（MCP + REST）

Server 同时暴露两个等价的接入协议：

| 协议 | 端点 | 适用场景 |
|------|------|---------|
| **MCP** | `/mcp` (StreamableHTTP) | Claude Code / SDK agent 原生接入，工具自动发现 |
| **REST** | `/session/*` | curl / httpx / 非 MCP agent，更灵活 |

两个协议共享同一套 Server 逻辑、同一套权限规则、同一套评分流水线。

#### 2.1.2 会话状态机

Server 通过 4 阶段状态机严格管控会话生命周期：

```
UNREGISTERED ──register_session()──→ REGISTERED ──start_session()──→ IN_SESSION ──TC/Goal触发──→ COMPLETED
```

| 阶段 | 允许的操作 | 禁止的操作 |
|------|-----------|-----------|
| **UNREGISTERED** | register_session | 其他所有 |
| **REGISTERED** | start_session, list_tools | register, send, tools, evaluate |
| **IN_SESSION** | send_message, domain tools | register, start, evaluate |
| **COMPLETED** | request_evaluation, get_results, get_scores | register, start, send, tools |

违规操作返回 `{"error": "...", "allowed": ["permitted_operations"]}`。

#### 2.1.3 任务执行引擎

每个会话的生命周期：

1. **注册阶段**：加载任务定义、自动选择或指定学生 persona、创建 Docker 容器、注册 domain tools（core + convenient + distractor，最多 15 个）
2. **对话阶段**：学生模拟器生成开场白 → Agent 回复 → 学生回复 → 循环，直到终止条件触发
3. **终止判定**：
   - **增量 TC 检查**（strategy/backtest/implementation/debug/data_analysis 类别）：逐条检查学习目标覆盖
   - **Goal 检查**（end_to_end/adversarial 类别）：LLM 判断预期结果是否达成
   - 兜底：max_turns / timeout / agent 重复消息检测
4. **结果持久化**：对话、工具日志、工作区文件 → `run_state.json` + `run_state.md`
5. **异步评分**：后台线程运行评分流水线，结果写入 `eval_meta.json`

#### 2.1.4 工具管理与执行

**工具分类**：
- **Core tools**：任务必需的领域工具（shell_exec, run_backtest, file_read 等）
- **Convenient tools**：加分捷径，使用意味着更优解法
- **Distractor tools**：干扰项（最多填满 15 个槽位），测试 agent 的工具选择判断
- **Session tools**：基础设施（send_message, register_session 等，对 agent 不可见）

**Docker 执行**：
```
Agent tool call → MCPProxy（日志+截断12K） → ContainerManager → tool_executor daemon（stdin/stdout JSON-lines） → tools.py
```

**安全限制**：per-turn 步数限制、deadline enforcement、cancel event、结果截断

#### 2.1.5 学生模拟器

- Prompt 模板与 DeepEval ConversationSimulator **逐字对齐**，确保与 Legacy 行为一致
- 支持 3 个 persona 级别（beginner / intermediate / advanced）
- 多层 fallback：结构化 JSON 输出 → 纯文本 + JSON 提取 → 硬编码兜底
- 独立成本追踪

#### 2.1.6 评分流水线（独立运行）

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

**去 DeepEval 化**：Server 的评分引擎（`server/eval/ewan_eval/`）不依赖 DeepEval 类，使用自建的 `EwanLLMClient` + `OAuthAnthropicModel` 直接调用 LLM API。

#### 2.1.7 Web UI

Server 内嵌 Web UI（`/ui/*`），提供：
- 已完成会话的浏览和索引
- 对话回放（包括工具调用详情）
- 评分结果查看

#### 2.1.8 会话清理

后台 sweeper 任务定期检查空闲会话：
- UNREGISTERED/REGISTERED：5 分钟空闲后清理
- COMPLETED：1 小时后清理
- 含 Docker 容器的销毁和临时工作区清理

### 2.2 Client：Baseline Runner

Client 是一个轻量级的 agent 运行器，用于跑 baseline 实验。通过 `python -m client --server http://localhost:8000/mcp --task X01` 启动。

#### 2.2.1 核心职责

Client 只做一件事：**驱动 agent 与 Server 交互**。它不参与评分、不管理沙箱、不模拟学生。

```
Client 启动 → 连接 Server → register_session → start_session →
[Agent loop: generate_response → call tools → send_message] →
session completed → save client trace
```

#### 2.2.2 Adapter 体系

| 模式 | 实现 | 说明 |
|------|------|------|
| **Direct API** (默认) | Anthropic BetaToolRunner | 手动迭代，完整捕获中间 API 调用 |
| **SDK** | Claude Agent SDK | 黑盒 agent loop，SDK 自主管理工具调用 |

支持 OpenRouter 代理转发、Extended Thinking (COT)、自动上下文压缩（>40K tokens 时保留最后 6 个 tool_use）。

#### 2.2.3 Sync-Async 桥接

Agent adapter 是同步接口，Server 是异步 MCP。`ToolBridge` 通过 `asyncio.run_coroutine_threadsafe()` 在 worker 线程中桥接两者，600s 超时。

#### 2.2.4 产出物

- `client_trace.json` / `client_trace.md`：完整的 agent 行为记录（thinking blocks、content blocks、tool calls）
- `agent_cost.json`：token 用量和费用统计（每个 API 调用独立计费）

#### 2.2.5 并发支持

`--workers N` 参数支持多任务并行，使用 semaphore 控制并发数。

### 2.3 Spec：用户文档

| 文档 | 内容 |
|------|------|
| `spec/PROTOCOL.md` | 会话生命周期、MCP/REST 操作详解、错误格式 |
| `spec/TASKS.md` | 65 个任务的完整列表（ID、类别、难度、描述） |

---

## 三、新旧架构对比

### 3.1 架构层面

| 维度 | Legacy Harness | 新架构（Server + Client） |
|------|---------------|--------------------------|
| **入口** | `python run_benchmark.py run-single --task S01` | Server: `python -m server --port 8000`<br>Client: `python -m client --server ... --task S01` |
| **进程模型** | 单进程（agent + benchmark 同进程） | 双进程（Server 独立，Client 独立） |
| **通信** | 函数调用（adapter.generate_response） | MCP StreamableHTTP / REST API |
| **Agent 接入** | 必须实现 Python BaseAgentAdapter | 任何 MCP/HTTP client |
| **Agent 可见性** | Server 持有 adapter 引用，知道 agent 细节 | Server 完全不知道 Client 是谁 |
| **评分触发** | orchestrator Phase 4 内嵌 | 独立 pipeline，可异步触发 |
| **DeepEval 依赖** | 深度依赖（StudentSimulator、ConversationalGEval） | 完全去依赖（ewan_eval 自建） |
| **结果存储** | `results/{task_id}/{timestamp}/` | `results/server/{task_id}/{persona_id}/{timestamp}_{session_id}/` |

### 3.2 执行流程对比

**Legacy（5 Phase）**：
```
orchestrator.run_single_task()
  Phase 1: RESET（创建容器、配工具、注入 context）
  Phase 2: INTERACT（orchestrator 驱动 adapter.generate_response 循环）
  Phase 3: CAPTURE（收集 workspace + proxy logs + enrichment）
  Phase 4: EVALUATE（内嵌评分）
  Phase 5: TEARDOWN（清理容器）
```

**新架构（Client-Server 分离）**：
```
Server 侧:
  register → 创建容器 + 工具 + persona
  start → 学生开场白
  [send_message 循环] → 学生回复 + TC 检查
  completed → 保存 run_state.json
  request_evaluation → 后台评分 → eval_meta.json

Client 侧:
  connect → register → start →
  [adapter.generate_response → call tools → send_message] 循环
  → save client_trace.json
```

### 3.3 模块归属对比

| 功能模块 | Legacy 位置 | 新架构位置 | 变化 |
|---------|------------|-----------|------|
| 学生模拟器 | mcp_servers/student_sim.py | server/core/student_sim.py | 去 DeepEval 依赖 |
| 对话管理 | mcp_servers/session.py | server/core/session.py | 增加 GoalChecker |
| TC 检查 | orchestrator 内 | server/core/tc_checker.py | 独立模块，直接调 OpenRouter |
| 工具注册 | mcp_servers/registry.py | server/core/registry.py | 增加 populate 接口 |
| 工具代理 | mcp_servers/proxy/mcp_proxy.py | server/core/proxy.py | 删 live_monitor |
| 工具实现 | mcp_servers/core/tools.py | server/core/tools/tools.py | 增加 C# 入口推断 |
| 评分流水线 | evaluation/ (散布) | server/eval/pipeline.py（统一入口） | 独立可调用 |
| 评分指标 | evaluation/deepeval_metrics/ | server/eval/ewan_eval/ | 全部去 DeepEval 化 |
| Agent adapter | orchestrator/agent_adapters/ | client/adapters/ | 精简复制 |
| 结果存储 | orchestrator 内嵌 | server/storage/ | 独立模块 |

### 3.4 依赖隔离

新架构严格执行依赖隔离规则：

```
server/  只 import → server/ 内部 + scripts/data_manager + 外部库
client/  只 import → client/ 内部 + 外部库

禁止:
  server/ → orchestrator/, mcp_servers/, evaluation/, config/
  client/ → orchestrator/, mcp_servers/, evaluation/, config/, server/
  orchestrator/ → server/, client/
```

Server 内部分层：
```
api/     → core/, eval/, storage/, config/
core/    → config/
eval/    → config/
storage/ → (无内部依赖)
core/ ✕ eval/ ✕ storage/（互不 import）
```

---

## 四、已完成的工作

### 4.1 Server 核心功能（已实现）

| 模块 | 状态 | 说明 |
|------|------|------|
| `server/__main__.py` | ✅ 可运行 | Uvicorn 启动，支持 --port, --docker |
| `server/api/http_app.py` | ✅ 可运行 | Starlette app，MCP + REST 双协议，会话管理 |
| `server/api/protocol.py` | ✅ 可运行 | 4 阶段状态机，权限检查 |
| `server/api/session_api.py` | ✅ 可运行 | SessionState 完整生命周期 |
| `server/core/session.py` | ✅ 已验证 | TutoringSession + GoalChecker，防御层完整 |
| `server/core/student_sim.py` | ✅ 已验证 | 去 DeepEval，prompt 对齐 |
| `server/core/tc_checker.py` | ✅ 已验证 | 增量 TC + 直接 OpenRouter 调用 |
| `server/core/proxy.py` | ✅ 可运行 | 透明日志 + 截断 + 防御层 |
| `server/core/registry.py` | ✅ 可运行 | core/convenient/distractor 注册 |
| `server/core/container.py` | ✅ 可运行 | Docker 管理 + 本地 fallback |
| `server/core/tools/` | ✅ 可运行 | 50+ domain tools + C# 入口推断 |
| `server/eval/pipeline.py` | ✅ 可运行 | 独立评分入口 |
| `server/eval/ewan_eval/` | ✅ 可运行 | 去 DeepEval 评分全链路 |
| `server/storage/` | ✅ 可运行 | run_state.json + eval_meta.json |
| `server/web/` | ✅ 可运行 | Web UI 回放 |

### 4.2 Client 核心功能（已实现）

| 模块 | 状态 | 说明 |
|------|------|------|
| `client/__main__.py` | ✅ 可运行 | CLI 入口，支持 --task/--group/--workers |
| `client/runner.py` | ✅ 可运行 | MCP 连接 + 对话循环 + 并行调度 |
| `client/tool_bridge.py` | ✅ 可运行 | Sync-Async 桥接 |
| `client/adapters/anthropic_adapter.py` | ✅ 可运行 | Direct API + SDK 双模式 |
| `client/cost_tracker.py` | ✅ 可运行 | Token 计费 |
| `client/trace_writer.py` | ✅ 可运行 | 客户端 trace 输出 |

### 4.3 Spec 文档（已完成）

| 文档 | 状态 |
|------|------|
| `spec/PROTOCOL.md` | ✅ 完成 |
| `spec/TASKS.md` | ✅ 完成 |

### 4.4 Legacy 侧的同步修改（已完成）

在 Legacy 代码中同步进行的改进，保持新旧架构行为一致：

| 修改 | 文件 | 说明 |
|------|------|------|
| GoalChecker 集成 | mcp_servers/session.py, mcp_servers/mcp_server.py | 非 TC 类别的终止判定 |
| StudentSimulator 去 DeepEval | mcp_servers/student_sim.py | 独立 model resolver |
| 工具增强 | mcp_servers/core/tools.py | C# 入口推断、session context JSON |
| Proxy 增强 | mcp_servers/proxy/mcp_proxy.py | step exempt tools、step_check_fn |
| Registry 增强 | mcp_servers/registry.py | populate_proxy_for_task 接口 |

---

## 五、未完成的工作与残留问题

### 5.1 待验证项

| 项目 | 状态 | 风险 | 说明 |
|------|------|------|------|
| **新旧架构评分一致性验证** | 🔴 未开始 | 高 | 同一对话在 Legacy eval 和 Server eval（ewan_eval）下的评分是否一致？去 DeepEval 后可能引入偏差 |
| **端到端 HTTP 回归测试** | 🟡 部分完成 | 中 | 有 test_server_session_runtime.py 和 test_server_web_ui.py，但未覆盖全链路（register → send_message × N → evaluate → scores） |
| **65 任务全量验证** | 🔴 未开始 | 高 | 目前只在少量任务上验证过 Server 路径，需要确保所有任务都能正确运行和评分 |

### 5.2 已知问题

| 问题 | 严重性 | 说明 |
|------|--------|------|
| **Legacy 代码中混入了新架构准备代码** | 中 | mcp_servers/session.py 增加了 316 行（GoalChecker 等），这些本应只在 server/ 中。目前两边都有，存在维护同步风险 |
| **eval_pipeline.py 的 import 路径** | 低 | evaluation/eval_pipeline.py 是新文件，用于 Legacy + Server 共享的评分入口。但 server/eval/pipeline.py 也有独立实现。两个 pipeline 的关系需要理清 |
| **deepeval_metrics_legacy/ 是否需要保留** | 低 | 归档了旧的 DeepEval 评分代码，但如果 ewan_eval 已完全替代，可以在确认后删除 |
| **ewan_eval/ 在 Legacy 和 Server 中各有一份** | 中 | evaluation/ewan_eval/ (Legacy 侧) 和 server/eval/ewan_eval/ (Server 侧) 可能存在代码分叉。需要确认是否共用还是独立演化 |
| **X09 新架构回归** | 中 | v5.0/investigation/ 中有 x09_newarch_regression_investigation.md，说明 X09（alpha_conflict）任务在新架构下存在评分回归 |

### 5.3 待完成开发工作

| 项目 | 优先级 | 预估工时 | 说明 |
|------|--------|---------|------|
| 新旧评分一致性对比实验 | P0 | 2-3 天 | 选 8 个 ICC 任务，对比 Legacy eval vs ewan_eval 的评分差异 |
| 全量 65 任务 Server 路径验证 | P0 | 3-5 天 | 含 Docker 环境、工具可用性、TC 终止、评分完整性 |
| Legacy → Server 代码去重 | P1 | 2 天 | 确认 mcp_servers/ 和 server/core/ 的关系，消除重复 |
| Client 多 adapter 支持 | P2 | 2 天 | OpenAI / Google adapter 迁移到 client/adapters/ |
| BENCHMARK_SPEC.md（第三方接入文档） | P2 | 1 天 | 合并 PROTOCOL.md + TASKS.md + 接入示例 |

### 5.4 评分体系改进（独立于架构迁移）

以下改进记录在 `tutor review/tutor_scoring_enhancement_plan.md` 中，可独立推进：

| 方案 | 优先级 | 说明 |
|------|--------|------|
| Task-Specific Behavioral Checklist | P0 | 为 Tutor 引入程序化锚点，提升 cross-judge r |
| Human Calibration 实验 | P0 | 30-50 样本人类评分，验证 LLM-judge 有效性 |
| D3/D4 Rubric 修改 | P1 | 覆盖 Autonomy Preservation + Error Diagnosis |
| 对话长度退化分析 | P2 | 检测 tutor 在长对话中的质量退化 |

---

## 六、评分体系同步改进

在架构迁移过程中，评分体系也进行了多项改进：

### 6.1 评分公式与权重调整

**Implementation 类任务权重重平衡**：
- behavioral_score 从 0.60 降至 0.45（减少简单执行成功的权重）
- code_patterns 从 0.05 升至 0.10-0.15（增加代码架构质量的权重）
- 各任务特定指标权重上调（如 sweep_completed 0.15→0.20）

**Code eval 改进**：执行质量层（Layer B）从"最后一次试验"改为"最佳试验"，更准确反映 agent 的迭代能力。

**Result judge 新增准则**：Guideline 5 "CODE MUST BE EXECUTED TO COUNT" — 未执行的代码草稿不计入结果评分。

### 6.2 Tutor 评分优化

**Phase 1 缓存**：ConversationalGEval 的 evaluation steps 按 (model, dim) 缓存，节省 `(num_judge_runs - 1) × dims` 次 LLM 调用。

**Per-run raw scores 追踪**：记录每次 shuffled run 的原始分数，支持标准差分析和跨 run 方差诊断。

### 6.3 TC 扩展

增量 TC 检查类别从 `{strategy, backtest, implementation}` 扩展到包含 `debug` 和 `data_analysis`，非增量类别使用 GoalChecker。

### 6.4 消融实验

完成了评分粒度消融实验，证明 Tutor 的 Cohen's d=1.745 不是 10 档评分粒度造成的假象：
- 将 D1-D7 各维度 clamp 到 5 档后重新聚合 → d=1.816 (+4.1%)
- 结论：区分度来源于 Sonnet/Haiku 的真实教学能力差距

---

## 七、总结

### 7.1 当前状态一句话

> **新架构（Server + Client）的核心功能已全部实现且可运行，但尚未完成与 Legacy 架构的评分一致性验证和全量任务回归测试。**

### 7.2 关键里程碑

```
✅ 已完成:
   Server 双协议（MCP + REST）+ 会话状态机 + 全链路去 DeepEval
   Client baseline runner + Anthropic adapter
   Spec 文档（PROTOCOL.md + TASKS.md）
   Legacy 侧同步改进（GoalChecker、TC 扩展、评分优化）

🔄 进行中:
   新旧评分一致性验证
   X09 新架构回归调查

⬜ 待开始:
   65 任务全量 Server 路径验证
   Legacy → Server 代码去重
   第三方接入文档
   Human Calibration 实验
```

### 7.3 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| ewan_eval 与 DeepEval eval 评分不一致 | 中 | 高（影响论文数据可比性） | P0：8 任务对比实验 |
| 部分任务在 Server 路径下行为异常 | 中 | 中（需逐任务修复） | 全量验证 + 回归测试 |
| Legacy/Server 代码分叉导致维护成本 | 高 | 中（长期） | 代码去重，确定单一 source of truth |
