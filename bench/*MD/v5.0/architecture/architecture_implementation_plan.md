# 新架构实施计划（重构 + 实现）

> 日期：2026-04-10
> 前置文档：`../archive/exam_transition/exam_http_execution_plan.md`（历史设计方案——定义了协议、流程、原则）
> 本文档：实施计划——定义了代码结构、文件迁移、执行顺序
> 关系：历史 `exam_http_execution_plan.md` 回答“做什么”，本文档回答“怎么做”

---

## 一、目标架构

```
bench/
│
├── server/                                ← 新架构 Bench Server（完全独立）
│   │
│   ├── __main__.py                        #  python -m server --port 8000 --docker
│   │
│   ├── core/                              ← 任务执行引擎
│   │   ├── session.py                     #  TutoringSession（对话状态 + 终止判定）
│   │   ├── student_sim.py                 #  StudentSimulator（学生消息生成）
│   │   ├── tc_checker.py                  #  TCChecker + GoalChecker
│   │   ├── container.py                   #  ContainerManager（Docker 生命周期）
│   │   ├── proxy.py                       #  ToolProxy（工具日志、deadline）
│   │   ├── registry.py                    #  工具注册（core + distractor 填充）
│   │   ├── staging.py                     #  数据目录准备
│   │   ├── tools/                         #  工具实现（shell_exec, file_read, ...）
│   │   └── distractors/                   #  distractor 工具池
│   │
│   ├── eval/                              ← 评分引擎
│   │   ├── pipeline.py                    #  evaluate_task()
│   │   ├── enrichment.py                  #  对话 + 工具活动拼接
│   │   ├── eval_helpers.py                #  populate_eval_results
│   │   ├── scoring.py                     #  OAS/QAI/TEI 公式
│   │   ├── score_report.py                #  scores.md 生成
│   │   ├── trace_report.py                #  trace.md 生成
│   │   ├── cost_report.py                 #  cost.md 生成
│   │   ├── code_eval.py                   #  代码评估
│   │   ├── trace_utils.py                 #  key_results / trace_summary 提取
│   │   └── deepeval_metrics/              #  LLM judge 评分
│   │       ├── result_judge.py
│   │       ├── process_metrics.py
│   │       ├── tutor_conv_geval.py
│   │       └── tool_usage.py
│   │
│   ├── api/                               ← HTTP 接口层
│   │   ├── http_app.py                    #  Starlette + SessionManager + REST
│   │   ├── session_api.py                 #  register/start/send_message/request_eval
│   │   └── protocol.py                    #  权限状态机 + 请求验证
│   │
│   ├── storage/                           ← 结果持久化
│   │   ├── result_writer.py               #  run_state.json + agent_files/
│   │   ├── eval_writer.py                 #  evaluations/ 目录管理
│   │   └── format_validator.py            #  格式校验
│   │
│   ├── schemas.py                         #  数据类定义
│   │
│   └── config/                            ← Server 专用配置
│       ├── llm_config.py                  #  SIMULATOR_MODEL, TC_CHECKER_MODEL, EVAL_MODEL
│       ├── model_resolver.py              #  resolve_deepeval_model
│       ├── benchmark_config.py            #  DATASET_REVISION, HF_REPO
│       ├── prompt_config.py               #  build_scenario, build_user_description
│       └── pricing.py                     #  模型费率
│
├── client/                                ← 新架构 Baseline Client（完全独立）
│   ├── __main__.py                        #  python -m client --server ... --task X01
│   ├── runner.py                          #  HTTP 连接 + adapter 驱动 + 并行调度
│   ├── tool_bridge.py                     #  sync adapter → async HTTP 桥接
│   ├── cost_tracker.py                    #  agent 计费
│   └── adapters/
│       ├── base_adapter.py                #  从 Legacy 精简复制
│       ├── anthropic_adapter.py           #  从 Legacy 精简复制
│       ├── prompts.py                     #  干净版 system prompt
│       └── config.py                      #  模型 + API key + 费率
│
├── spec/                                  ← 用户文档
│   ├── TASKS.md
│   └── PROTOCOL.md
│
├── orchestrator/                          ← Legacy（完全保留，不改动）
├── mcp_servers/                           ← Legacy（完全保留，不改动）
├── evaluation/                            ← Legacy（完全保留，不改动）
├── config/                                ← Legacy（完全保留，不改动）
└── run_benchmark.py                       ← Legacy CLI（完全保留，不改动）
```

---

## 二、依赖隔离规则

```
server/**  只 import:
  ├── server/** 内部（自引用）
  ├── scripts/data_manager（HF 数据下载，纯工具函数）
  └── 外部库（mcp, pydantic, anthropic, deepeval, starlette, uvicorn）

client/**  只 import:
  ├── client/** 内部
  └── 外部库（mcp, anthropic, openai）

禁止:
  server/ → orchestrator/, mcp_servers/, evaluation/, config/
  client/ → orchestrator/, mcp_servers/, evaluation/, config/, server/
  orchestrator/ → server/, client/
```

server/ 内部分层：

```
api/ → core/, eval/, storage/, schemas, config/
core/ → config/, schemas
eval/ → config/, schemas
storage/ → schemas
core/ ✕ eval/ ✕ storage/（互不 import）
```

---

## 三、现状盘点

### 已完成

| 内容 | 位置 | 状态 |
|------|------|------|
| StudentSimulator（DeepEval prompt 对齐） | mcp_servers/student_sim.py | ✅ 已验证 |
| TutoringSession（防御层 + GoalChecker） | mcp_servers/session.py | ✅ 已验证 |
| TCChecker | mcp_servers/tc_checker.py | ✅ 已验证 |
| eval_pipeline.py（评分提取） | evaluation/eval_pipeline.py | ✅ 已验证 |
| staging.py（数据目录） | mcp_servers/staging.py | ✅ 已验证 |
| E2E 测试（stdio 模式） | bench/test_exam_e2e.py | ✅ 通过 |
| 设计方案 | `../archive/exam_transition/exam_http_execution_plan.md` | ✅ 审核通过 |

### 未完成

| 内容 | 状态 |
|------|------|
| server/ 目录结构创建 | 未开始 |
| 文件迁移（从 Legacy 复制到 server/） | 未开始 |
| session.py 修改（返回格式、删 get_session_info、新增 start_session） | 未开始 |
| proxy.py 修改（删 live_monitor、删 step_limit） | 未开始 |
| eval_pipeline.py 修改（改 enrichment import） | 未开始 |
| HTTP 传输层（Starlette + StreamableHTTP） | 未开始 |
| 权限状态机实现 | 未开始 |
| 结果存储（session_id 路径 + 评分历史） | 未开始 |
| REST API | 未开始 |
| client/ 目录 | 未开始 |
| runner.py（HTTP 连接 + adapter 驱动） | 未开始 |
| tool_bridge.py（sync→async） | 未开始 |
| cost_tracker.py | 未开始 |
| adapters/ 精简复制 | 未开始 |
| spec/TASKS.md | 未开始 |
| spec/PROTOCOL.md | 未开始 |
| 端到端 HTTP 验证 | 未开始 |

---

## 四、执行顺序

### Phase 1: 骨架创建 + 文件迁移（无逻辑修改）

**目标：** 把所有需要的代码从 Legacy 复制到 server/ 和 client/ 下，确保 import 路径正确、语法通过。不改任何逻辑。

#### Step 1.1: 创建目录结构

```bash
mkdir -p server/{core,eval,api,storage,config}
mkdir -p server/core/{tools,distractors}
mkdir -p server/eval/deepeval_metrics
mkdir -p client/adapters
mkdir -p spec
```

#### Step 1.2: server/core/ 文件复制

| 源 | 目标 | 说明 |
|---|------|------|
| mcp_servers/session.py | server/core/session.py | 原样复制 |
| mcp_servers/student_sim.py | server/core/student_sim.py | 原样复制 |
| mcp_servers/tc_checker.py | server/core/tc_checker.py | 原样复制 |
| orchestrator/container_manager.py | server/core/container.py | 原样复制 |
| mcp_servers/proxy/mcp_proxy.py | server/core/proxy.py | 原样复制 |
| mcp_servers/registry.py | server/core/registry.py | 原样复制 |
| mcp_servers/staging.py | server/core/staging.py | 原样复制 |
| mcp_servers/core/ | server/core/tools/ | 整个目录复制 |
| mcp_servers/distractors/ | server/core/distractors/ | 整个目录复制 |

#### Step 1.3: server/eval/ 文件复制

| 源 | 目标 |
|---|------|
| evaluation/eval_pipeline.py | server/eval/pipeline.py |
| evaluation/scoring.py | server/eval/scoring.py |
| evaluation/score_report.py | server/eval/score_report.py |
| evaluation/trace_report.py | server/eval/trace_report.py |
| evaluation/cost_report.py | server/eval/cost_report.py |
| evaluation/code_eval.py | server/eval/code_eval.py |
| evaluation/trace_utils.py | server/eval/trace_utils.py |
| evaluation/deepeval_metrics/*.py | server/eval/deepeval_metrics/*.py |
| orchestrator/orchestrator.py（_enrich 函数） | server/eval/enrichment.py |
| orchestrator/eval_helpers.py | server/eval/eval_helpers.py |

#### Step 1.4: server/config/ 文件复制

| 源 | 目标 | 精简内容 |
|---|------|---------|
| config/llm_config.py | server/config/llm_config.py | 只保留 SIMULATOR_MODEL, TC_CHECKER_MODEL, EVAL_MODELS |
| config/model_resolver.py | server/config/model_resolver.py | 保留 resolve_deepeval_model |
| config/benchmark_config.py | server/config/benchmark_config.py | 只保留 DATASET_REVISION, HF_REPO |
| config/prompt_config.py | server/config/prompt_config.py | build_scenario, build_user_description（build_tutor_context 注释化）|
| config/pricing.py | server/config/pricing.py | 保留费率表 |

#### Step 1.5: server/ 其他文件

| 源 | 目标 |
|---|------|
| orchestrator/schemas.py（部分） | server/schemas.py（QuantTutorTask, StudentPersona, TaskResult, ConversationTurn）|
| exam/protocol.py | server/api/protocol.py |
| exam/format_validator.py | server/storage/format_validator.py |
| exam/result_writer.py | server/storage/result_writer.py |
| （新建） | server/storage/eval_writer.py |

#### Step 1.6: client/ 文件复制

| 源 | 目标 | 说明 |
|---|------|------|
| orchestrator/agent_adapters/base_adapter.py | client/adapters/base_adapter.py | 删多余注释 |
| orchestrator/agent_adapters/anthropic_adapter.py | client/adapters/anthropic_adapter.py | 删多余注释和 prompt 注释 |
| （新写） | client/adapters/prompts.py | 干净版 system prompt |
| config/llm_config.py + config/pricing.py | client/adapters/config.py | 合并精简 |

#### Step 1.7: 修复所有 import 路径

所有复制的文件中的 `from orchestrator.xxx import`、`from mcp_servers.xxx import`、`from evaluation.xxx import`、`from config.xxx import` 全部改为 server/ 内部相对引用或 `from server.xxx import`。

**验证方法：** `python -c "import server; import client"` 无报错。

---

### Phase 2: Server 逻辑修改（按设计方案调整）

在 Phase 1 的复制基础上修改逻辑，使其符合历史 `exam_http_execution_plan.md` 的设计。

#### Step 2.1: server/core/session.py 修改

| 修改 | 详情 |
|------|------|
| `_result()` 返回字段 | `student_reply` → `student_message` |
| `_result()` 删除字段 | 删除 `turn` 和 `max_turns` |
| 新增 `handle_start_session()` | 只返回 `{student_message}`，追加开场白到 conversation |
| `handle_get_session_info()` | 保留但标注 Legacy-only（server/api/ 不注册） |
| step_limit 相关代码 | 保留但不激活（server 不注入 step_check_fn） |

#### Step 2.2: server/core/proxy.py 修改

| 修改 | 详情 |
|------|------|
| 删除 `from orchestrator.live_monitor import emit` | 用 logger 替代 |
| 保留 `_step_check_fn` 属性 | 但 server 不注入（默认 None） |

#### Step 2.3: server/eval/pipeline.py 修改

| 修改 | 详情 |
|------|------|
| `from orchestrator.orchestrator import _enrich_conversation_with_tools` | → `from server.eval.enrichment import enrich_conversation_with_tools` |

#### Step 2.4: server/config/prompt_config.py 修改

| 修改 | 详情 |
|------|------|
| `build_tutor_context()` | 函数体注释化，返回空字符串 |

#### Step 2.5: E01-E05 task JSON 修改

| 修改 | 详情 |
|------|------|
| 5 个 end_to_end 任务 | core_mcp_tools 补充 `get_environment_info` |

**验证方法：** 单元测试——构造 mock session，调 handle_start_session 和 handle_send_message，验证返回格式。

---

### Phase 3: HTTP 传输层 + 权限状态机

#### Step 3.1: server/api/protocol.py

实现权限状态机：4 状态（未注册→已注册未开考→进行中→已完成），每个状态的允许/拒绝列表。

#### Step 3.2: server/api/session_api.py

实现 4 个 Session API 的 MCP tool handler：
- `register_session(task_id)` → 创建 sandbox + 随机 persona + 返回 session_id
- `start_session()` → 返回 student_message
- `send_message(text)` → 路由到 TutoringSession
- `request_evaluation()` → 触发评分

handler 内部通过 session_id（从 MCP request context 获取）路由到正确的 session 状态。

#### Step 3.3: server/api/http_app.py

Starlette app：
- MCP StreamableHTTP 端点（`/mcp`）
- REST API 端点（`/api/runs`、`/api/results`、`/api/scores`、`/api/evaluate`）
- SessionManager + lifespan 管理

#### Step 3.4: server/__main__.py

uvicorn 启动入口。

**验证方法：** 用 curl 或 Python httpx 手动调 register_session → start_session → send_message → request_evaluation。

---

### Phase 4: 结果存储

#### Step 4.1: server/storage/result_writer.py 修改

- 存储路径改为 `results/server/{task_id}/{session_id}/`
- run_state.json 包含 session_id
- 删除 agent_cost 字段

#### Step 4.2: server/storage/eval_writer.py 新建

- evaluations/ 子目录管理
- eval_meta.json 写入
- latest 软链接

**验证方法：** 创建 mock session 数据，调 result_writer + eval_writer，检查文件结构。

---

### Phase 5: Client 实现

#### Step 5.1: client/adapters/ 精简复制

从 Legacy 复制 base_adapter.py + anthropic_adapter.py，删多余注释。新写 prompts.py（干净 prompt）和 config.py。

#### Step 5.2: client/tool_bridge.py

sync adapter callback → async HTTP MCP call_tool 桥接。

#### Step 5.3: client/runner.py

单 task 完整流程：连接 → register → list_tools → start → adapter.generate_response → report cost。

多 task 并行调度（asyncio.gather + semaphore）。

#### Step 5.4: client/cost_tracker.py

从 adapter.get_token_records() 聚合 → 写 agent_cost.json。

#### Step 5.5: client/__main__.py

CLI 入口：--server, --task/--tasks/--group, --workers。

**验证方法：** 启动 Server → Client 跑 X01 → 检查 Server 结果 + Client agent_cost.json。

---

### Phase 6: 文档 + 端到端验证

#### Step 6.1: spec/TASKS.md

从 65 个 task JSON 提取 task_id + description。

#### Step 6.2: spec/PROTOCOL.md

从历史 `exam_http_execution_plan.md` §三 提取。

#### Step 6.3: 端到端验证

```bash
# Terminal 1
python -m server --port 8000 --docker

# Terminal 2
python -m client --server http://localhost:8000/mcp --task X01_ma_offbyone

# 验证
curl http://localhost:8000/api/runs?task_id=X01_ma_offbyone
curl http://localhost:8000/api/scores/{session_id}
```

#### Step 6.4: 与 Legacy 对比

同 task + 同 model 跑 Legacy 和新架构，对比：
- conversation 长度和质量
- tool_logs 模式
- 评分分数差异（预期新架构更低——无漏题）

---

## 五、Phase 间依赖

```
Phase 1（骨架 + 复制）
    │
    ├──→ Phase 2（Server 逻辑修改）
    │       │
    │       ├──→ Phase 3（HTTP + 权限）
    │       │       │
    │       │       └──→ Phase 4（存储）
    │       │               │
    │       │               └──→ Phase 6（文档 + 验证）
    │       │
    │       └──→（Phase 2 完成后可独立验证 core/ 和 eval/）
    │
    └──→ Phase 5（Client）
            │
            └──→ Phase 6（端到端验证需要 Server + Client 都完成）
```

**Phase 2 和 Phase 5 可并行**——Server 逻辑修改和 Client 实现互不依赖。

---

## 六、风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 复制文件量大（~30 个文件） | import 路径修复耗时 | Phase 1 不改逻辑，只改 import，可批量 sed |
| evaluation/ 下的文件互相引用复杂 | 复制到 server/eval/ 后 import 链可能断裂 | 逐文件验证 import，用 ast.parse 检查 |
| mcp_servers/core/tools/ 下工具实现引用 proxy | 需要确认所有 tool 函数的签名和依赖 | 工具函数是纯函数（接收参数返回字符串），依赖极少 |
| StreamableHTTP session_id 获取 | 已验证可行（request_context.request.headers） | Phase 3 优先验证此机制 |
