# 未提交修改总结报告

## 范围说明

- 统计范围：`bench/` 目录下当前未提交修改。
- 明确排除：`bench/server/web/`。
- 本报告重点介绍：
  - 新架构的功能能力与边界
  - 新老架构的职责差异
  - 当前工作区里仍然存在的残留问题与风险
- 本报告尽量使用“功能语言”描述，不展开具体实现细节。

## 总体结论

当前未提交修改已经把项目从原来的单体式 benchmark runner，推进到了一个可以独立运行的 **client-server 新架构**：

- `server/` 已具备会话管理、双协议接入、student 驱动、TC 判定、结果保存、评分触发等 benchmark 核心能力。
- `client/` 已具备 baseline agent driver 能力，可以作为新架构的验证客户端。
- `spec/`、`tooling/`、`server/eval/` 等新模块，说明这次变更不仅是把旧代码搬家，而是在补齐协议、工具注入、评测和可观测性这些新架构必要能力。

但与此同时，当前工作区并不是“只新增了 `server/client` 目录”：

- 仍有较多 legacy / shared 路径发生修改，例如 `mcp_servers/`、`orchestrator/`、`config/`、`tasks/`、`evaluation/`。
- 这意味着新架构已经成型，但**与 legacy 的彻底隔离仍未完成**。

## 改动全景

### 1. 新架构核心目录

新增或显著扩展的核心区域主要包括：

- `bench/server/`
- `bench/client/`
- `bench/spec/`
- `bench/server/tooling/`
- `bench/server/eval/`
- `bench/tests/test_server_session_runtime.py`

这批目录对应的是新架构的主链路，不再依赖 legacy orchestrator 作为唯一入口。

### 2. 共享任务与文档

共享任务定义也有明显变化，尤其是：

- `bench/tasks/layer2/data_analysis/D01-D05*.json`
- 多个 `debug / implementation / end_to_end` 任务 JSON

另外还有一批文档被新增或更新，例如：

- `bench/*MD/v5.0/architecture/architecture_implementation_plan.md`
- `bench/*MD/v5.0/architecture/dual_protocol_design.md`
- `bench/*MD/v4.0/tc_design_guidelines.md`
- `bench/spec/PROTOCOL.md`
- `bench/spec/TASKS.md`

这些文档说明项目已经从“代码试验”进入“协议、架构、任务规范一起演进”的阶段。

### 3. legacy / shared 路径仍有改动

当前未提交修改里，仍有一批 legacy 或共享路径被直接修改：

- `bench/mcp_servers/*`
- `bench/orchestrator/orchestrator.py`
- `bench/config/llm_config.py`
- `bench/scripts/data_manager.py`
- `bench/evaluation/*`

因此，当前状态更准确地说是：

**新架构主链已经形成，但整个仓库仍处于新旧并行演进阶段。**

## 新架构新增的功能能力

### 1. 显式会话生命周期

新架构把 benchmark 执行过程明确拆成会话阶段，而不是像 legacy 那样主要依赖内部调用链隐式推进。

当前 `server` 已经具备：

- 注册任务
- 启动 session
- 多轮消息往返
- 会话完成
- 结果查询
- 评分查询

这意味着 benchmark 的“运行状态”已经从内部控制流变成了显式可观察对象。

### 2. 双协议接入

新架构已经不再只靠一条内部路径驱动 benchmark，而是对外提供两种等价入口：

- `MCP`
- `REST`

这使得：

- agent 可以继续按 MCP 工具协议接入
- 同时 benchmark 也能被更普通的 HTTP 调用方式控制、排查和测试

这一步的意义很大，因为它把 benchmark 从“只能依赖特定运行器”推进到了“有明确服务边界”的形态。

### 3. server 成为 benchmark 权威端

新架构里，benchmark 核心状态已经收敛到 `server`：

- sandbox / container 生命周期
- student simulator
- termination criteria 判断
- 结果落盘
- 评测触发
- 会话清理

这意味着 benchmark 的核心职责不再依赖 client 保持一致，client 只需要扮演 agent driver。

### 4. baseline client 明确降级为验证客户端

`client/` 的角色已经从“benchmark 内部流程的一部分”变成了“用于验证 server 能否稳定承载 agent”的 baseline。

它当前的主要作用是：

- 连接 server
- 启动任务
- 驱动一轮轮 tutor 回复
- 保存 client trace 与成本信息

也就是说，benchmark 的核心已经不再要求 client 和 server 深度耦合。

### 5. 工具注入规范化

新架构新增了独立的工具规范层：

- 目标是统一工具描述、参数 schema、泛化 guidance、示例形式
- 让不同 client / 不同 SDK / 不同协议看到尽量一致的工具契约

这一点很关键，因为它代表系统开始用“协议化工具能力”替代“依赖 prompt 教 agent 怎么用工具”。

### 6. server 侧 TC 证据归一化

新架构已经开始从“只看 tutor 自由文本”转向“读 server 自己掌握的执行证据”。

现在 server 至少已经具备两层证据能力：

- per-turn tool evidence
- artifact visibility evidence

这一步的目标不是替代 tutor 对话，而是降低完成判定对某种特定 client 文风的依赖。

### 7. artifact 可见性桥

这次修改里，一个很重要的新能力是：

**server 会尝试判断：tutor 是否已经生成了代码/输出产物，但没有把关键内容真正贴到对话里。**

它的意义在于：

- 不让 student 直接读整个 workspace
- 不退回 legacy 那种环境自由游走模式
- 只通过极小的 server-side 信号，把 follow-up 引导回“请直接贴代码/输出”

这是一种更受控的“可见性桥”，而不是把工作区暴露给 student。

### 8. 更强的结果可观测性

`run_state` 已经不只是聊天记录和工具日志，还开始保存：

- TC 覆盖摘要
- TC 调试历史
- artifact steering 调试历史
- 结束原因
- 会话状态

这让 benchmark 从“跑完看感觉”变成“可以排查为什么没完成、在哪一轮偏离了目标”。

### 9. 可重复回归测试能力

新架构现在支持显式指定 `persona_id`，这意味着：

- 可以固定 student persona 复跑同一任务
- 避免把“persona 随机变化”误判成系统行为变化

这对后续评估新架构稳定性非常重要。

## 新老架构的关键差异

### 1. 单体编排 vs 会话化服务

legacy 更像是：

- `orchestrator + mcp_servers + evaluation` 的单体式执行链

新架构更像是：

- `server` 负责 benchmark 权威状态
- `client` 负责 agent 驱动

核心差异在于：

**新架构把 benchmark 从“内部流程”变成了“可外部调用的服务化会话系统”。**

### 2. 隐式流程 vs 显式协议

legacy 主要通过内部对象与调用链维持执行语义。
新架构则把核心操作明确成协议能力，例如：

- `register_session`
- `start_session`
- `send_message`
- `request_evaluation`
- `get_results`
- `get_scores`

这带来的变化是：

- 权限边界更清楚
- 状态机更清楚
- 客户端更可替换

### 3. agent 看到什么

legacy 路径里，agent loop 和 benchmark session 语义耦合更深。
新架构里，baseline client 已经明确把协议工具从 agent 工具集中排除，agent 只看领域工具。

这使得：

- `send_message` 变成 runner 与 server 的握手
- agent 的职责更像“在本轮内完成分析与回复”

### 4. 完成判定来源不同

legacy 更偏向：

- conversation 驱动
- prompt 驱动
- 内部 simulator 逻辑驱动

新架构则在尝试走向：

- chat
- tool evidence
- artifact visibility
- TC coverage

的组合判定方式。

这意味着新架构正试图把“完成态”从某个特定 client 的说话方式中解耦出来。

### 5. 结果与评分的边界更清晰

legacy 中，结果保存、运行、评分之间的边界相对更紧耦合。
新架构则已经开始形成：

- `server/storage/*`
- `server/eval/*`
- `client trace / cost`

这套更清晰的边界。

## 当前残留问题

### 1. 新旧路径仍未彻底隔离

这是当前最明确的结构性问题。

虽然新架构主体已经落在 `server/` 和 `client/` 下，但未提交修改仍涉及：

- `mcp_servers/`
- `orchestrator/`
- `config/`
- `scripts/`
- `evaluation/`

这说明当前仓库还没有完成“新架构只在新目录里演进，legacy 保持冻结”的隔离目标。

### 2. 共享任务定义成为新旧耦合点

当前多份任务 JSON 被直接修改。
这本身没错，但意味着：

- task schema 和 TC 文案已经是共享资产
- 即使 legacy 没直接走新 server 的逻辑，共享任务定义仍会影响两边理解

后续如果继续推进隔离，需要明确哪些字段是新旧共享，哪些字段只服务新架构。

### 3. TC 与 student steering 仍处于调优期

从当前改动和最近回归现象看：

- `D01` 已能自然完成
- `D02` 的 follow-up 已明显被 artifact visibility 拉回正题
- 但 `D02-D04` 这类任务的自然收口仍未完全稳定

这说明：

- 新架构的 server 已经具备调节对话方向的能力
- 但还没有完全证明“跨多类任务都能稳定自然完成”

### 4. artifact visibility 仍是启发式机制

当前 artifact bridge 的优点是：

- 不需要把 workspace 全暴露给 student
- 不依赖逐题关键词覆盖
- 不会把文件直接当作任务完成证据

但它仍然是启发式信号，不是强语义理解。
因此它更适合作为“student follow-up 引导”，而不是直接替代 TC。

### 5. 评测迁移尚未完全收口

当前仓库里同时存在：

- 旧评测路径
- 新 `server/eval` 路径
- legacy 兼容目录

这说明评测体系也还在过渡期。
后续如果要把新架构作为默认主链，需要进一步统一：

- 哪条路径是权威评测
- 哪条路径是 legacy 兼容
- 哪些结果字段是稳定外部契约

### 6. 运行期稳定性仍需继续验证

从近期真实运行现象看，已经出现过一些不是设计层面的问题，而是运行期信号，例如：

- deadline force-complete 语义曾出现状态写入不一致，现已修复
- `D03` 运行中出现过 `BrokenPipeError`，来自容器执行器路径

这类问题不否定新架构方向，但说明：

**当前系统已经进入“需要靠真实 session 回归去找稳定性缺口”的阶段。**

### 7. 基准结论已经可以转向 server 视角

经过这轮改动后，一个重要变化是：

- 已经不应该再主要评价 baseline tutor 的风格优劣
- 更应该关注 server 是否提供了足够条件，让不同 client 能稳定把任务跑通

从这个角度看，新架构已经迈出了关键一步，但还没有完全收敛。

## 当前建议

### 1. 把 `server/client/spec/tooling` 继续作为主演进面

后续如果继续推进新架构，建议优先把新增能力继续收敛在：

- `server/`
- `client/`
- `spec/`
- `server/tooling/`

尽量减少对 legacy 目录的进一步直接侵入。

### 2. 继续用固定 persona 做回归

后续所有关键任务回归，建议优先固定 persona。
否则会把：

- persona 差异
- student opening 差异
- session 长度差异

误判成系统行为变化。

### 3. 把接下来的验证重点放在跨任务稳定性

当前最值得继续验证的不是“还能不能跑起来”，而是：

- `D03 / D04`
- `debug`
- `implementation`
- `end_to_end`

这些不同类别任务，是否都能在新 server 机制下进入合理完成态。

### 4. 尽快明确新旧隔离策略

当前仓库已经出现一个很清晰的管理问题：

- 新架构在成长
- 但 legacy / shared 路径也在一起变化

如果不尽快明确边界，后面会越来越难回答：

- 哪些行为是新架构能力
- 哪些只是共享路径顺手改出来的副作用

## 一句话总结

当前未提交修改已经把项目推进到：

**“以 `server/client` 为核心、具备双协议、统一工具注入、server-side TC 证据与 artifact 可见性的新 benchmark 主链”**

这个阶段。

但它还没有完全完成三件事：

- 与 legacy 的彻底隔离
- TC / student steering 的跨任务稳定收口
- 新旧评测与共享资产的最终收敛
