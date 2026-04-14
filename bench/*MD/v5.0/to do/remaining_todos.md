# 代码审计遗留待办项

> 更新日期：2026-04-10
> 来源：code_audit_report.md / code_audit_v2.md / code_audit_final.md 合并整理

---

## P1：影响评分一致性

### TODO-1：generate_closing 不区分 TC / native mode

**位置：** mcp_servers/student_sim.py `generate_closing()`

**问题：** Legacy 有两个不同的 closing prompt：
- **TC mode**（`_EfficientSimulator._generate_closing`）— 包含完整 user_description + 对话历史
- **Native mode**（`_append_student_closing`）— 只包含 `scenario[:400]`，**不含对话历史**

当前实现不区分，所有终止路径都传 scenario + transcript。non-TC 类别（data_analysis / end_to_end / adversarial）的 closing 消息会比 Legacy 更长更具体，可能影响 Tutor D7 评分。

**修复方案：**
```python
def generate_closing(self, conversation, mode="tc"):
    if mode == "tc":
        prompt = _TC_CLOSING_PROMPT.format(...)   # 含 transcript + user_description
    else:
        prompt = _NATIVE_CLOSING_PROMPT.format(...)  # 仅 scenario，无 transcript
```

session.py 中根据终止原因传不同 mode：
- tc_met → `mode="tc"`
- goals_met / max_turns / timeout → `mode="native"`

---

## P2：功能完整性

### TODO-2：Registration 后未发 tools/list_changed 通知

**位置：** exam/exam_server.py

**问题：** MCP 协议中，部分 Client 在连接时获取一次 `list_tools` 后缓存。registration 后新增的 domain tools 不会自动通知 Client。当前 baseline client 会主动 re-list 所以不阻塞，但第三方 Client（如 Claude Desktop）可能看不到新工具。

**修复方案：** 在 registration 完成后调用 `ServerSession.send_tool_list_changed()`。需持有 server session 引用。

---

### TODO-3：eval_pipeline 缺少进度输出

**位置：** evaluation/eval_pipeline.py

**问题：**
1. 缺少 `emit("eval_step", {...})` 事件通知（live monitor 无法显示评分进度）
2. 缺少 `logger.info()` 进度日志（auto-eval 时控制台无输出）

Legacy 的 `_evaluate_task()` 有 ~20 条 print 和 emit 调用。

**修复方案：** 增加可选的 `progress_callback` 参数（默认 None），用 `logger.info()` 替代 print 输出关键进度节点。

---

## P3：数据完整性（不影响评分）

### TODO-4：result_writer 不保存 thinking_trace

**位置：** exam/result_writer.py

**问题：** Legacy 的 run_state.json 包含 `thinking_trace` 字段（Claude extended thinking 的 COT 内容）。Exam 版本未保存，影响 trace.md 完整性。

**修复方案：** 扩展 Exam 协议，增加可选的 `submit_metadata` tool，允许 Client 在 session 结束前提交 thinking trace。

---

### TODO-5：agent_cost 缺少 token 统计字段

**位置：** exam/result_writer.py

**问题：** Legacy 的 agent_cost 包含 `input_tokens`、`output_tokens`、`cost_usd`、`api_calls`。Exam 版本只有 `agent_name` 和 `model`，cost.md 报告中 agent 侧全 0。

这是解耦的必然结果——Server 不知道 Client 的 token 消耗。

**修复方案：** 增加可选的 `report_usage` tool，让 Client 在 session 结束前提交 token 统计。
