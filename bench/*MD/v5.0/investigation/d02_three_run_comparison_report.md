# D02 三轮执行对比总结

## 1. 目的与范围

本文总结 `D02_missing_data_detection_handling` 在 `persona=beginner_no_finance` 下的三轮代表性执行结果，用于回答两个问题：

1. 新架构下 `server` 是否已经具备稳定运行能力。
2. 当前任务无法自然完成，是否已经可以归因为“仅仅是 client 能力问题”。

本文对比的三轮分别是：

1. `702628cc`：基础设施修改前的基线轮次
2. `0074cb38`：基础设施修改后的稳定轮次
3. `23386e00`：最新轮次

说明：

- 前两轮有完整 server 结果；其中 `0074cb38` 也有完整 client trace。
- 最新轮次 `23386e00` 的 server 结果完整保存，但由于手动中断 client 进程，没有新的 `client_trace.json`，因此本轮分析以 server 侧 `run_state.json` 为准。

## 2. 三轮结果概览

| 轮次 | 性质 | session_id | 终止方式 | 时长(s) | assistant turns | tool logs | send_message | step_count | TC覆盖 |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| `702628cc` | 基础设施修改前基线 | `702628cca65a483799447b131299503e` | `timeout` | 741.02 | 7 | 33 | 7 | 26 | `2/3` |
| `0074cb38` | 基础设施修改后 | `0074cb3831d14ec0bda2ce05a4b67b26` | `timeout` | 742.98 | 11 | 18 | 11 | 6 | `2/3` |
| `23386e00` | 最新轮次 | `23386e00e1bc4758a35c478e286df7da` | `timeout` | 741.63 | 9 | 21 | 9 | 11 | `1/3` |

对应结果文件：

- `702628cc`：
  - [run_state.json](/Users/richsion/Desktop/benchmark/bench/results/server/D02_missing_data_detection_handling/beginner_no_finance/20260414_150655_702628cc/run_state.json)
- `0074cb38`：
  - [run_state.json](/Users/richsion/Desktop/benchmark/bench/results/server/D02_missing_data_detection_handling/beginner_no_finance/20260414_221959_0074cb38/run_state.json)
  - [client_trace.json](/Users/richsion/Desktop/benchmark/bench/results/client/0074cb3831d14ec0bda2ce05a4b67b26/client_trace.json)
- `23386e00`：
  - [run_state.json](/Users/richsion/Desktop/benchmark/bench/results/server/D02_missing_data_detection_handling/beginner_no_finance/20260414_225001_23386e00/run_state.json)

## 3. 各轮分析

### 3.1 `702628cc`：基础设施修改前基线

这轮的特点是：

- 工具调用非常重，`33` 次 tool logs 中包含：
  - `15` 次 `shell_exec`
  - `9` 次 `file_write`
  - `2` 次 `file_list`
- 失败调用 2 次：
  - `turn 1` 的 `shell_exec`
  - `turn 6` 的 `file_write`
- 虽然最终仍是 `timeout`，但 TC 达到 `2/3`

TC 进展：

- `turn 4` 一次性打中 `item 1` 和 `item 2`
- 之后未能完成 `item 3`

这说明在基础设施修改前：

- 任务主线相对贴近 D02 的核心目标
- 但工具噪音很大，执行代价高，且没有可见性引导、artifact 调试或更细的 runtime steering

### 3.2 `0074cb38`：基础设施修改后

这轮是三轮里最“平衡”的一次，特点是：

- tool logs 降到了 `18`
- `step_count` 只有 `6`
- `assistant turns` 增加到 `11`
- `TC` 仍然达到 `2/3`
- `artifact_debug_history = 11`
- `tc_debug_history = 11`

工具分布：

- `get_environment_info`: `1`
- `file_read`: `1`
- `shell_exec`: `3`
- `file_write`: `2`
- `send_message`: `11`

这表明基础设施修改后的 server 机制带来了两个积极变化：

1. 对话节奏更依赖 `send_message` 驱动，而不是大量 shell/file 操作堆砌。
2. `artifact visibility` 与 `TC` 调试链路已经开始稳定工作。

TC 进展：

- `turn 1` 命中 `item 1`
- `turn 5` 命中 `item 2`
- 后续始终没有完成 `item 3`

这一轮的意义在于：

- 它证明了基础设施修改没有破坏 session 主链路
- 同时也证明了新的 server 侧机制确实能帮助任务推进到 `2/3`
- 但仍然不能保证自然完成

### 3.3 `23386e00`：最新轮次

这轮的主要特征是：

- 运行稳定，但任务完成质量明显退步
- `assistant turns = 9`
- `tool_logs = 21`
- `step_count = 11`
- `TC` 只有 `1/3`

工具分布：

- `get_environment_info`: `1`
- `shell_exec`: `9`
- `file_write`: `1`
- `plot_chart`: `1`
- `send_message`: `9`

这轮最重要的现象不是“server 出错”，而是：

- 对话在完成基础 inspection 后，很早就漂向相邻话题
- 后续大量内容转向：
  - `OHLCV` 解释
  - plotting
  - cleaned Close price
  - returns
  - `.dt.date`
- 没有继续稳定逼近 D02 的 `item 2 / item 3`

TC 进展：

- `turn 1` 命中 `item 1`
- `turn 2-8` 没有任何新增覆盖

这说明：

- 最新轮次并不是“最后一轮没被 checker 看见”
- 也不是“保存/清理/完成态写错”
- 而是对话内容本身没有继续朝剩余目标推进

## 4. 三轮对比后的核心观察

### 4.1 server 主链路已经相当稳定

三轮中共同成立的部分：

- `register -> start -> tool calls -> send_message -> timeout/completion -> save -> cleanup`
- 都能正常完成
- `session_status` 与 `termination_reason` 能被正确写入
- 结果目录和 `run_state.json` 能稳定落盘

因此，从“工程运行稳定性”看，当前 server 已经具备较高稳定性。

### 4.2 任务完成表现并不稳定

虽然 server 链路稳定，但三轮对比也说明：

- 相同任务 `D02`
- 相同 persona `beginner_no_finance`
- 在新的 server 框架下

最终结果仍然在：

- `2/3`
- `2/3`
- `1/3`

之间波动。

这说明：

- 当前系统还不能说“任务完成已经稳定”
- 运行稳定不等于任务自然完成稳定

### 4.3 client 行为差异仍然是主要波动源

这三轮里，最明显的波动来自：

- agent 是否紧贴 D02 核心目标
- agent 是否转向相邻但非核心的话题
- agent 是否把精力放在更多解释、图表和附加产物上

从这个角度看，**client 能力和行为模式** 仍然是当前任务是否完成的主要变量。

### 4.4 但不能简单下结论说“只剩 client 问题”

如果只看结果，很容易得出：

> server 已经稳定，剩下都是 client 能力问题

这个结论只对了一半。

更准确的说法是：

- **server 的协议、生命周期和保存链路已经很稳定**
- 但 **server 侧的目标对齐能力还不够强**

具体体现在：

1. `artifact visibility` 目前主要解决“代码/输出是否被贴出来”
   - 它对“最后一个未完成目标如何被继续追问”帮助有限

2. `TC + student steering` 目前还不能稳定把对话拉回剩余未覆盖目标
   - 尤其在已经覆盖 `1/3` 或 `2/3` 后

3. 同一任务在相同 persona 下仍然会出现 `2/3 -> 1/3` 的波动
   - 说明 server 目前还没有把 client 的行为波动“收敛掉”

因此，当前更准确的判断应该是：

> 当前 server 已经在工程运行层面非常稳定；
> 但在“让任务稳定自然完成”这件事上，仍然既受 client 能力影响，也受到 server 侧目标对齐/引导不足的影响。

## 5. 现状总结

当前 D02 的整体状态可以概括为：

### 已经成立的结论

- 新架构 server 已经具备稳定运行能力
- MCP/会话/工具调用/结果保存/清理都已稳定
- 基础设施改动没有破坏主链路

### 仍未成立的结论

- 还不能说 D02 在新架构下已经稳定自然完成
- 还不能说“剩下完全只是 client 问题”

### 当前最接近事实的判断

- **server 稳定性：高**
- **任务完成稳定性：中低**
- **主要波动源：client 行为**
- **server 残留问题：目标对齐与后续引导能力不足**

## 6. 剩余问题

从这三轮对比看，当前仍有 4 个残留问题：

1. `D02` 的 `TC3` 仍然很难被自然命中
   当前后续对话容易滑向相邻问题，而不是回到“同一示例上的两种处理方案比较 + 额外输出/下游差异”。

2. `artifact visibility` 只能解决“展示出来没有”
   还不能解决“如何把 follow-up 引到最后未完成目标”。

3. student steering 仍然不够定向
   它可以帮助纠正“我看不到文件”，但还不能稳定把 conversation 收束到剩余 `TC`。

4. 新 client 的 trace 粒度问题仍在处理中
   当前 whole-session baseline 已经导致 client trace 的 per-turn 语义丢失；虽然这不影响 server 运行，但会影响 web 端按 turn 的过程展示与分析。

## 7. 结论

最后直接回答核心问题：

### 是否说明当前 server 其实很稳定？

**是。**
从运行链路、状态一致性、结果保存、session 清理来看，当前 server 已经相当稳定。

### 是否说明任务无法正常完成仅仅是 client 能力问题？

**不能这么简单下结论。**

更准确的结论是：

- **client 行为波动是当前 D02 无法稳定完成的主要原因**
- 但 **server 还没有强到足以把这种波动充分约束住**

因此目前最贴近事实的判断是：

> 当前 server 已经在工程层面稳定；
> 任务无法稳定自然完成，主要由 client 能力/行为驱动，
> 但 server 侧仍然存在“目标对齐与 follow-up 引导不足”的残留问题，
> 所以还不能说问题已经完全收敛为纯 client 问题。
