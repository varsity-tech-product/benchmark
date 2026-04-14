# QuantTutorBench 架构设计文档

> 2026-04-08 | 综合多轮调研与讨论的最终设计方案

---

## 一、设计理念：考试隐喻

QuantTutorBench 是一场**标准化考试**。我们提供试卷、考场设施和打分标准。考生（Client）可以带小抄、查资料——我们不管也管不了——但所有在考场里做的动作都必须用我们的纸和笔，留下完整记录。

```
我们提供（不可替换）:
  试卷 ── 65 个任务定义 + 学生行为规范（persona + TC）
  文具 ── 15 个标准化工具（MCP tool API）
  考场 ── Docker 沙箱（数据 + 执行环境）
  监考 ── 学生模拟器（出题 + 追问 + 判定完成）
  打分 ── 评估流水线（QR + QP + Tutor 7D）

考生提供（可替换）:
  作答者 ── LLM Agent / 人类 / 混合 / 任何能对话的东西
```

**我们不假装能控制考生的外部行为**。考生可能有外部知识库、可能课前预习过、可能有更强的推理能力。我们只评估考生**在考场里的表现**——说了什么、做了什么、教得怎么样。

---

## 二、为什么"考场设施"不可替换

### 2.1 学生是试卷的一部分

学生模拟器不是"基础设施"——它是**试卷本身**。学生的追问决定了 tutor 教什么、怎么教。

| 如果学生可替换 | 后果 |
|-------------|------|
| Client 用"永远说 great 的假学生" | Tutor 分数虚高（没有真正的教学挑战） |
| Client 用只问简单问题的学生 | 降低了 D1_level_detection 的区分度 |
| Client 控制 TC（Termination Criteria） | 控制对话何时结束 = 控制评分难度 |

**学生行为 = 考题**。考题不能由考生出。

### 2.2 工具集定义了能力边界

15 个标准化工具是**受控变量**，不是限制：

- 所有考生面对相同的工具集 → 公平对比
- 工具调用日志由 Server 的 MCP Proxy 自动采集 → 100% 可观测
- 工具执行在 Docker 内 → 结果可审计、可复现

如果考生自带工具，我们无法保证工具行为一致性，也无法采集完整日志。

### 2.3 Docker 保护的是 Server，不是限制 Client

`--network none` 的真实作用：

| | 当前理解（纠正后） |
|---|---|
| **不是** | 阻止 Client 使用外部知识（做不到，Client 可以在自己侧查完再回来） |
| **是** | 阻止 Docker 容器内的代码访问外网（防止 shell_exec 调外部 API） |
| **是** | 保护 Server 基础设施安全（Client 的 tool_call 在隔离沙箱执行） |
| **是** | 确保工具执行环境一致（所有 Client 面对相同的容器环境） |

---

## 三、三维评估的重新定义

### 3.1 每个维度评的是什么（诚实表述）

| 维度 | 评的是 | 不评的是 |
|------|-------|---------|
| **QR（结果质量）** | 最终产出的正确性和完整性 | 答案怎么得到的（开卷闭卷无所谓，对就是对） |
| **QP（行为质量）** | Client 在考场里的可观测行为——工具调用的选择、顺序、效率；代码的迭代过程；错误恢复 | Client 的内部推理过程、外部知识来源 |
| **Tutor（教学质量）** | 对话中的教学行为——因材施教、知识脚手架、对话管理、情感回应 | Client 内部怎么组织思路的 |

### 3.2 QP 的精确定位

旧表述（隐含"过程评估"假设）：
> "QP evaluates the agent's analytical process quality"

新表述（诚实且准确）：
> "QP evaluates the observable behavioral quality of the agent within the benchmark environment — how effectively it selects and sequences tools, manages code development iterations, and recovers from errors. QP is agnostic to the agent's internal reasoning or external knowledge sources; it measures execution quality, not reasoning quality."

### 3.3 为什么这不削弱 QP 的价值

即使 Client 有外部知识，QP 仍然有区分度：

1. **必须动手**：不调工具就没有 QR programmatic 分数、没有 code_eval 分数。外部知识不能替代"在考场里执行"
2. **执行质量有差异**：知道目的地不等于走路不摔跤。一个好 agent 的工具调用有条理、代码迭代合理；一个差 agent 即使知道答案，执行仍然混乱
3. **数据已证明**：Sonnet 和 Haiku 都"开卷"（都有训练知识），但 QP Cohen's d=1.11（large effect），行为质量差异显著

### 3.4 三维度为什么不冗余

| 维度对 | 相关性 | 含义 |
|--------|--------|------|
| QR - Tutor | r=0.25（独立） | 做得对 ≠ 教得好 |
| QR - QP | r=0.36（弱相关） | 结果对 ≠ 过程好 |
| QP - Tutor | r=0.50（中等相关） | 行为好 ≈ 教学好（但不能替代） |

只看 QR（结果）→ Sonnet 和 Haiku 几乎不可区分（d=0.24）。
加入 QP + Tutor → 区分度跃升至 d=1.69（very large）。
消融实验证明区分度不来源于评分粒度差异（D1-D7 clamp 到 5 档后 d 不变）。

---

## 四、Client-Server 交互协议

### 4.1 架构

```
Client（考生）                    Server（考场）
┌──────────────┐                ┌──────────────────────────┐
│ LLM Agent    │                │ Session Manager          │
│ 人类 (Web UI) │ ←─MCP stdio─→ │ Student Simulator        │
│ 混合         │                │ MCP Proxy (tool logging) │
│ 任何         │                │ Docker Sandbox           │
└──────────────┘                │ Evaluation Pipeline      │
                                └──────────────────────────┘
```

> **注：** 当前 Reference Implementation 使用 MCP stdio（进程内通信）。未来产品化阶段可扩展为 WebSocket 或 HTTP SSE 等远程协议。

### 4.2 一次对话 Turn 的生命周期

```
Server 通过 MCP send_message tool 向 Agent 发送学生消息

Agent 的 action 阶段（0~N 次 MCP tool call）:
  Agent → Server: MCP tool_call (e.g., get_data, run_code, send_message)
  Server → Agent: MCP tool_result
  ... (可重复)

Agent 通过 send_message tool 回复学生，结束本轮 turn

Server 处理:
  记录对话 → TC Checker 判定 → 学生模拟器生成下一条消息 → 或结束
```

> **注：** 实际实现中，Agent 通过标准 MCP tool call 与学生交互（send_message）和调用分析工具，不存在自定义 JSON 消息类型。

### 4.3 Turn 边界规则

| 规则 | 说明 |
|------|------|
| `send_message` 是逻辑上的 turn 终止信号 | Agent 调用 send_message 向学生回复后，Server 进入下一轮 |
| Turn 边界由 Agent 自主管理 | MCP 模式下 Server 不强制阻止 send_message 后的 tool_call，但评分会自然惩罚无效调用 |
| 工具调用为单次串行 | MCPProxy 提供 call_tool(name, **kwargs) 接口，每次调用一个工具 |
| Metadata 在 adapter 层采集 | thinking、token_usage 由 agent adapter (base_adapter.py) 记录，不通过独立消息类型 |

### 4.4 Session 生命周期

当前 Reference Implementation 采用 5 阶段线性 pipeline（非状态机）：

```
RESET → INTERACT → CAPTURE → PRE-TEARDOWN HOOK → TEARDOWN
```

| 阶段 | 说明 |
|------|------|
| RESET | 初始化 Docker 环境、加载任务数据 |
| INTERACT | Agent 与学生模拟器多轮对话 |
| CAPTURE | 采集 workspace 文件、tool logs、对话记录 |
| PRE-TEARDOWN HOOK | 执行评估流水线（code_eval、LLM judge 等） |
| TEARDOWN | 清理 Docker 容器和临时资源 |

异常处理：
- Agent 超时不回复 → task 级别 timeout_minutes，超时注入 wrap-up 消息
- Docker 崩溃 → tool_executor 自动重启

> **注：** stdio MCP 为进程内通信，不存在 TCP 断连/重连概念。未来 WebSocket 实现可增加重连机制。

### 4.5 可选 Metadata

Metadata（thinking、token_usage）由 agent adapter 层（base_adapter.py）在每次 LLM 调用时自动采集，不通过独立的消息类型传递。

不提供 metadata 不影响评分。提供了则用于报告展示：

| 数据 | 采集方式 | 报告展示 |
|------|---------|---------|
| 什么都不采集 | — | scores 报告完整，cost 报告 Agent 行 "unknown"，trace 报告纯文本 |
| token_usage | adapter 自动记录每次 API 调用的 token 用量 | + 费用仪表盘 |
| thinking | adapter 采集 extended thinking / reasoning 内容 | + 思维链折叠面板 |

---

## 五、评分数据来源

### 5.1 Server 自动采集（不依赖 Client）

| 评分需要的数据 | 采集方式 | 可靠性 |
|-------------|---------|--------|
| **conversation** | Server 记录每次 student_message + client response | 100%（必须经过 Server） |
| **tool_logs** | MCP Proxy 拦截每次 tool_call | 100%（唯一的工具调用通道） |
| **workspace_files** | Docker 容器扫描 | 100%（所有文件操作在容器内） |
| **duration** | Server wall clock 计时 | 100% |

### 5.2 评分公式不变

```
OAS = 0.70 × QAI + 0.30 × TEI
QAI = 0.50 × QR  + 0.50 × QP
```

其中：
- QR = Blending(programmatic_eval, code_eval, result_judge) + dampening
- QP = weighted_avg(tool_usage, step_efficiency, process_reasonableness, code_process, process_alignment, role_adherence, topic_adherence)
- Tutor = weighted_avg(D1-D7, per-category weights)

---

## 六、作弊分析与防护

### 6.1 天然防护（Client-Server 架构提供）

| 防护 | 机制 |
|------|------|
| 工具结果不可篡改 | tool_result 从 Server 发出，Client 只读 |
| 工具日志不可遗漏 | 所有 tool_call 经 MCP Proxy，100% 记录 |
| 对话内容不可修改 | conversation 由 Server 维护 |
| 评估在 Server 侧 | eval scripts、scoring formula、LLM judge 都在 Server |
| Docker 保护 Server | 恶意代码在隔离沙箱执行 |

### 6.2 不防护的（设计上允许）

| Client 行为 | 是否阻止 | 理由 |
|-------------|---------|------|
| 查外部知识库 | 不阻止 | "开卷考试"设计 |
| 使用更强的 LLM | 不阻止 | benchmark 评输出质量，不限制输入 |
| 课前"备课" | 不阻止 | 真实 tutor 也会备课 |

### 6.3 评分体系自然惩罚不合作行为

| 不合作行为 | 自然后果 |
|-----------|---------|
| 不使用我们的工具 | QP tool_usage=0, code_eval=0, programmatic eval 大部分失败 |
| 只说不做（纯文本回复） | QR 极低（没有实际计算结果），QP 极低 |
| 给学生完整答案不教学 | Tutor D3_scaffolding 低，D1_level_detection 低 |

---

## 七、System Prompt 策略

System prompt 是**考试规则的一部分**，由 benchmark 注入，Client 不可修改。

实际注入方式：
1. **System prompt** — 由 agent adapter 在初始化时注入到 LLM 的 system message 中
2. **Task context** — Agent 通过 `get_session_info` MCP tool 获取任务描述、学生 persona、可用工具列表等
3. **运行参数** — `agent_max_steps`、`timeout_minutes` 等在 task JSON 和 session 配置中定义

```
Agent Adapter 注入 system_prompt → Agent 调用 get_session_info 获取任务上下文 → 开始对话
```

> **注：** 不存在独立的 `session_start` 消息类型。System prompt 通过 adapter 层注入，task 信息通过标准 MCP tool 获取。

Client 必须在回复学生时遵循 system_prompt 的角色设定。Tutor 7D 的 rubric 基于这个 prompt 定义的行为规范评分。

`agent_max_steps` 是**建议值**，不强制。Client 可以超过，但过多的工具调用会在 QP step_efficiency 中自然惩罚。

---

## 八、评估可靠性证据

### 8.1 跨 Judge 一致性（双 Judge 交叉验证）

| 维度 | Pearson r | 含义 |
|------|-----------|------|
| QR | 0.872 | 两个 judge 排名高度一致 |
| QP | 0.806 | 高度一致 |
| Tutor | 0.676 | 中等一致 |

Agent 排名在两个独立 judge 下 **100% 一致**（8/8 tasks sonnet > haiku）。

### 8.2 评分粒度消融

QP 用 5 档评分，Tutor 用 10 档评分。消融实验：将 Tutor D1-D7 各自 clamp 到 5 档后重新聚合，Cohen's d 从 1.745 变为 1.816（+4.1%）。区分度不来源于评分粒度。

### 8.3 Programmatic Eval 锚定效应

Programmatic eval 和 code_eval 跨 judge 完全一致（相同 run_state → 相同分数），为 QR 提供确定性锚点。

### 8.4 可复现性定位

> "QuantTutorBench achieves statistical reproducibility rather than bit-exact reproducibility. Model rankings are 100% consistent across 8 tasks, 3 runs, and 2 independent judge models."

---

## 九、Benchmark Specification 范围

### 9.1 属于 Benchmark Spec 的（论文贡献）

| 组件 | 内容 |
|------|------|
| **Task Definitions** | 65 tasks × JSON Schema（7 categories × difficulty levels） |
| **Student Protocol** | 3 personas × 追问模式 × TC 规范 |
| **Tool API** | 15 tools × name/description/params JSON Schema |
| **System Prompt** | Tutor 角色定义（评分标准的一部分） |
| **Evaluation Protocol** | QR Blending + QP 7D + Tutor 7D + OAS 公式 |
| **Scoring Rubrics** | 3 level × 7D rubric JSON |
| **Reference Data** | Reference traces for process alignment |

### 9.2 属于 Reference Implementation 的（工程贡献，可替换）

| 组件 | 内容 |
|------|------|
| **Agent Adapters** | Anthropic / OpenAI / Google / Generic |
| **Orchestrator** | 5-phase lifecycle |
| **Docker Images** | quant-tutor-env + lean-env |
| **Student Simulator** | MCP 路径：StudentSimulator + DeepEval GPTModel；Legacy 路径：ConversationSimulator |
| **Web UI** | 实时监控和可视化 |

### 9.3 边界声明

> "QuantTutorBench consists of a benchmark specification (task format, tool API, student protocol, evaluation rubrics) and a reference implementation (agent adapters, orchestrator, sandbox). The specification is agent-agnostic: any system that can respond to student messages and invoke the standardized tool API can be evaluated. We provide reference adapters for Anthropic, OpenAI, Google, and OpenRouter-compatible models as a convenience; these adapters include provider-specific optimizations (extended thinking, context management) that are not part of the benchmark specification."

---

## 十、实施路径

| Phase | 内容 | 交付物 | 价值 |
|-------|------|--------|------|
| **0** | 协议规范 | BENCHMARK_SPEC.md（Task Schema + Tool API + 交互协议 + 评分公式） | 论文审稿必需 |
| **1** | Client-Server 基础 | Session Manager + 远程通信协议（WebSocket/HTTP SSE） + Server Callback | 支持远程 Client |
| **2** | Web UI | 人类 Client 界面（对话 + 工具调用 + 评分展示） | 人类标定数据 + 产品演示 |
| **3** | 生产化 | 容器池 + 排行榜 + 认证 + 计费 | 公开服务 |

Phase 0 是论文提交前必须完成的。Phase 1-3 是产品化路径。

---

## 十一、一句话总结

> QuantTutorBench 提供标准化的考场环境（任务、工具、学生、评分），评估考生（Agent/人类/混合）在考场内的**可观测行为和教学表现**，不限制也不假装能限制考生的外部能力。三维评估（QR 结果质量 + QP 行为质量 + Tutor 教学质量）将模型区分度从 near-zero（d=0.24，仅看结果）提升到 very large（d=1.69，加入教学维度），且在两个独立 judge 模型下排名 100% 一致。
