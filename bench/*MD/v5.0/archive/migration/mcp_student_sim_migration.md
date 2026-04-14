# MCP 路径学生模拟器迁移方案

> 日期：2026-04-09
> 状态：待实施
> 前置：PR #8（mcp-unification）已合并
> 目标：将 MCP 路径的 StudentSimulator 与 Legacy（DeepEval ConversationSimulator）路径在 server 侧行为完全对齐，消除跨路径验证需求

---

## 一、架构认知前提

### 1.1 两条路径的控制流是同构的

两条路径中 **对话控制权都在 server 侧**。Agent 只控制：(a) 每次 send_message 的内容 (b) send_message 之间调多少工具。

```
Legacy 路径:
  DeepEval while loop {
    ① stop_conversation(turns, golden)         ← server 侧终止判定
    ② generate_next_user_input(golden, turns)   ← server 侧学生消息生成
    ③ model_callback(student_msg)               ← agent 处理一轮（调工具+回复文本）
  }

MCP 路径:
  Agent BetaToolRunner loop {
    agent 调 send_message(text) → TutoringSession.handle_send_message() {
      ① tc_checker.check(conversation)          ← server 侧终止判定
      ② student_sim.generate_message(conv)       ← server 侧学生消息生成
      → 返回 {student_reply, status}             ← agent 读取，决定下一步
    }
  }
```

### 1.2 迁移范围明确

新架构的骨架（agent 自驱工具循环 + send_message 即 MCP tool call）不变。迁移的是骨架内部的 **策略层**：prompt template、历史格式、输出解析、终止机制、错误处理。这些都是可插拔的实现细节，不涉及架构变更。

### 1.3 对齐后不需要跨路径验证

因为迁移后两条路径在 server 侧对学生模拟器使用的是 **完全相同的 prompt 文本、完全相同的历史格式、完全相同的终止判定逻辑**，学生模拟器的输入分布 bit-exact 相同。差异仅在外层循环由谁驱动——这不影响学生消息的生成分布。

---

## 二、7 类别终止机制全景（Legacy 现状）

### 2.1 两组终止策略

| 组 | 类别 | Task 数 | 终止机制 | 学生 prompt 特殊段 |
|---|---|---|---|---|
| **A: 增量 TC** | strategy, backtest, implementation, debug | 32 | `_EfficientSimulator.stop_conversation()` — 增量 bitmap + 每轮检查最新 exchange | TOPIC FLOW（轻量级）、CODE EXPECTATION |
| **B: Native Checker** | data_analysis, end_to_end, adversarial | 33 | DeepEval `stop_conversation()` — 每轮 LLM 判 expected_outcome | COMPLETION、COVERAGE TRACKING、TURN BUDGET、ACTION EXPECTATION |

### 2.2 expected_outcome 构建（Group B）

来源：`simulation.py:build_conversational_golden()` 第 438-450 行

```python
if task.ground_truth.termination_criteria:
    if task.category.value in ("implementation", "end_to_end", "debug"):
        stop_outcome = f"{EO}\n\nObservable completion criteria:\n{TC}"
    else:  # data_analysis, adversarial
        stop_outcome = task.ground_truth.termination_criteria
else:
    stop_outcome = task.ground_truth.expected_outcome
```

### 2.3 DeepEval stop_simulation prompt（Group B 使用）

来源：`deepeval/simulator/template.py:105-140`

```
You are a Conversation Completion Checker.
Your task is to determine whether the conversation has achieved
the expected outcome and should be terminated.

Guidelines:
1. Review the entire conversation and decide if the expected outcome
   has been met and the conversation has ended.
2. If the expected outcome has been met, mark the conversation as complete.
3. If not, mark it as incomplete and briefly describe what remains.

Expected Outcome: "{expected_outcome}"
Conversation History: {previous_conversation}
JSON Output: {"is_complete": bool, "reason": str}
```

---

## 三、逐模块现状 → 迁移 → 对齐

### 模块 1：学生消息 Prompt Template

**文件：** `bench/mcp_servers/student_sim.py`

#### 现状

```python
_MESSAGE_PROMPT = (
    "You are a simulated student in a tutoring conversation about "
    "quantitative finance. Stay in character at all times.\n\n"
    "{user_description}\n\n"
    "SCENARIO:\n{scenario}\n\n"
    "Conversation so far:\n{transcript}\n\n"
    "Generate only the student's next message. Do NOT include any "
    "metadata, role labels, or stage directions — just the student's "
    "words as they would type them in a chat."
)
```

- 首轮与后续共用同一 prompt
- 无长度控制、语气一致性、对话相关性指导
- 无 JSON 输出要求

#### 迁移目标

替换为 DeepEval template.py 的两个 prompt，完全保留其措辞：

```python
_FIRST_MESSAGE_PROMPT = textwrap.dedent("""\
    Pretend you are a user of an LLM app. Your goal is to start a conversation
    in English based on a scenario and user profile. The scenario defines your
    context and motivation for interacting with the LLM, while the user profile
    provides additional personal details to make the conversation realistic.

    Guidelines:
    1. The opening message should clearly convey the user's intent or need.
    2. Keep the tone warm, conversational, and natural, as if it's from
       a real person seeking assistance.
    3. Avoid providing excessive details upfront; the goal is to initiate
       the conversation and build rapport, not to solve it in the first message.
    4. The message should be concise, ideally no more than 1-3 sentences.

    IMPORTANT: The output must be formatted as a JSON object with a single
    key `simulated_input`, where the value is the generated opening message.

    User Profile: "{user_description}"
    Scenario: "{scenario}"
    JSON Output:
""")

_NEXT_MESSAGE_PROMPT = textwrap.dedent("""\
    Pretend you are a user of an LLM app. Your task is to generate the next
    user input in English based on the provided scenario, user profile, and
    the previous conversation.

    Guidelines:
    1. Use the scenario and user profile as the guiding context for the
       user's next input.
    2. Ensure the next input feels natural, conversational, and relevant
       to the last assistant reply in the conversation.
    3. Keep the tone consistent with the previous user inputs.
    4. The generated user input should be concise, ideally no more than
       1-2 sentences.

    IMPORTANT: The output must be formatted as a JSON object with a single
    key `simulated_input`, where the value is the generated user input.

    User Profile: "{user_description}"
    Scenario: "{scenario}"
    Previous Conversation:
    {transcript}

    JSON Output:
""")
```

`_CLOSING_PROMPT` 保留现有内容（与 Legacy `_generate_closing` 的 prompt 等价）。

#### 对齐的 Legacy 组件

| MCP 修改后 | Legacy 对标 |
|---|---|
| `_FIRST_MESSAGE_PROMPT` | `template.py:simulate_first_user_turn()` 第 22-51 行 |
| `_NEXT_MESSAGE_PROMPT` | `template.py:simulate_user_turn()` 第 64-102 行 |

---

### 模块 2：对话历史格式

**文件：** `bench/mcp_servers/student_sim.py`

#### 现状

```python
def _format_transcript(conversation, max_chars=None):
    lines = []
    for turn in conversation:
        label = "Student" if turn["role"] == "user" else "Tutor"
        content = turn["content"][:max_chars] if max_chars else turn["content"]
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines)
```

输出 `"Student: ...\n\nTutor: ..."` 文本格式。

#### 迁移目标

改为 JSON array 格式，与 DeepEval 一致：

```python
def _format_transcript(conversation, max_chars=None):
    trimmed = [
        {
            "role": t["role"],
            "content": t["content"][:max_chars] if max_chars else t["content"],
        }
        for t in conversation
    ]
    return json.dumps(trimmed, indent=4, ensure_ascii=False)
```

#### 对齐的 Legacy 组件

`conversation_simulator.py:59-63`：`json.dumps([t.model_dump() for t in turns], indent=4, ensure_ascii=False)`

---

### 模块 3：输出解析 + Cost Tracking

**文件：** `bench/mcp_servers/student_sim.py`

#### 现状

```python
def _extract_text(result) -> str:
    text = result[0] if isinstance(result, tuple) else result
    return (text or "").strip()
```

- 不使用结构化输出
- Cost 被丢弃

#### 迁移目标

复用 `model.generate(prompt, schema=SimulatedInput)` 模式，带 fallback：

```python
from pydantic import BaseModel

class SimulatedInput(BaseModel):
    simulated_input: str

class StudentSimulator:
    def __init__(self, ...):
        ...
        self.total_cost: float = 0.0

    def _generate_parsed(self, prompt: str) -> str:
        """对齐 DeepEval generate_schema() 行为：结构化输出 + cost 追踪 + fallback。"""
        try:
            result = self.model.generate(prompt, schema=SimulatedInput)
            if isinstance(result, tuple):
                obj, cost = result
                if cost is not None:
                    self.total_cost += cost
            else:
                obj = result
            return obj.simulated_input.strip()
        except (TypeError, AttributeError):
            # Fallback: 模型不支持 schema → 纯文本 + JSON 提取
            result = self.model.generate(prompt)
            text = result[0] if isinstance(result, tuple) else result
            cost = result[1] if isinstance(result, tuple) and len(result) > 1 else None
            if cost is not None:
                self.total_cost += cost
            match = re.search(r'\{.*\}', text or "", re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return data.get("simulated_input", text or "").strip()
                except json.JSONDecodeError:
                    pass
            return (text or "").strip()

    def generate_message(self, conversation):
        is_first = not any(t["role"] == "assistant" for t in conversation)
        if is_first:
            prompt = _FIRST_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
            )
        else:
            prompt = _NEXT_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
                transcript=_format_transcript(conversation),
            )
        return self._generate_parsed(prompt)
```

#### 对齐的 Legacy 组件

| MCP 修改后 | Legacy 对标 |
|---|---|
| `_generate_parsed()` 主路径 | `conversation_simulator.py:generate_schema()` 第 610-615 行 |
| `_generate_parsed()` fallback | `conversation_simulator.py:generate_schema()` 第 617-623 行 (`trimAndLoadJson`) |
| `self.total_cost` 累积 | `conversation_simulator.simulation_cost` 累积 |

---

### 模块 4：终止机制 — TC 类别（strategy/backtest/implementation/debug）

**文件：** `bench/mcp_servers/tc_checker.py` + `bench/mcp_servers/session.py`

#### 现状

TCChecker 已经从 `_EfficientSimulator` 提取，逻辑高度一致：
- 增量 bitmap ✓
- 三遍检查策略（head/tail/code blocks）✓
- checker prompt 相同 ✓
- `_call_checker()` 有 try/except ✓

差异仅两点：
1. Checker 调用方式：Legacy 用 DeepEval model.generate()，MCP 用 openai.OpenAI 直调 → 底层都走 OpenRouter，**结果等价**
2. `session.py:114-115` 对 `tc_checker.check()` 和 `generate_closing()` 缺 try/except

#### 迁移目标

仅补充错误处理。不改 TCChecker 内部逻辑。

```python
# session.py handle_send_message() 中

# TC check（加 try/except 保护 client 初始化等非 LLM 异常）
try:
    tc_met = self._tc_checker is not None and self._tc_checker.check(self._conversation)
except Exception as exc:
    logger.warning("TC check failed: %s", exc)
    tc_met = False

if tc_met:
    try:
        closing = self._student_sim.generate_closing(self._conversation)
    except Exception as exc:
        logger.warning("Failed to generate closing: %s", exc)
        closing = (
            "Thanks for walking me through all of this — "
            "I have a much clearer picture now. "
            "I'll try applying these techniques to my own data."
        )
    ...
```

#### 对齐的 Legacy 组件

| MCP 修改后 | Legacy 对标 |
|---|---|
| tc_checker.check() try/except | `_EfficientSimulator.stop_conversation()` 内部异常隔离 |
| closing fallback 硬编码文本 | `_generate_closing()` 第 401-405 行 fallback |

---

### 模块 5：终止机制 — 非 TC 类别（data_analysis/end_to_end/adversarial）

**文件：** `bench/mcp_servers/session.py`（新增 GoalChecker 类）

#### 现状

MCP 路径对这 33 个 task **无 goal-based 终止**，只有 max_turns 和 deadline。

#### 迁移目标

新增 `GoalChecker` 类，完全复制 DeepEval `stop_simulation` 的 prompt 和判定逻辑。

```python
class ConversationCompletion(BaseModel):
    is_complete: bool
    reason: str

class GoalChecker:
    """对齐 DeepEval stop_conversation() — 用于非增量 TC 类别。

    每次 send_message 后，将全对话历史 + expected_outcome 发给 LLM，
    判定是否达成目标。完全复制 deepeval/simulator/template.py:stop_simulation 的 prompt。
    """

    _STOP_PROMPT = textwrap.dedent("""\
        You are a Conversation Completion Checker.
        Your task is to determine whether the conversation has achieved
        the expected outcome and should be terminated.

        Guidelines:
        1. Review the entire conversation and decide if the expected outcome
           has been met and the conversation has ended.
        2. If the expected outcome has been met, mark the conversation as complete.
        3. If not, mark it as incomplete and briefly describe what remains
           to be done.

        IMPORTANT: The output must be formatted as a JSON object with two keys:
        `is_complete` (a boolean) and `reason` (a string).

        Expected Outcome: "{expected_outcome}"
        Conversation History:
        {previous_conversation}
        JSON Output:
    """)

    def __init__(self, expected_outcome: str, model):
        self.expected_outcome = expected_outcome
        self._model = model

    def check(self, conversation: list[dict]) -> bool:
        if not self.expected_outcome:
            return False
        conv_json = json.dumps(conversation, indent=4, ensure_ascii=False)
        prompt = self._STOP_PROMPT.format(
            expected_outcome=self.expected_outcome,
            previous_conversation=conv_json,
        )
        try:
            result = self._model.generate(prompt, schema=ConversationCompletion)
            obj = result[0] if isinstance(result, tuple) else result
            return obj.is_complete
        except Exception as exc:
            logger.debug("GoalChecker failed: %s", exc)
            return False
```

**expected_outcome 构建**从 `build_conversational_golden()` 搬到 server 初始化：

```python
# mcp_server.py _build_standalone_server() 中
if tc_items is None and task.ground_truth:
    if task.ground_truth.termination_criteria:
        if task.category.value in ("implementation", "end_to_end", "debug"):
            expected_outcome = (
                f"{task.ground_truth.expected_outcome}\n\n"
                f"Observable completion criteria:\n"
                f"{task.ground_truth.termination_criteria}"
            )
        else:
            expected_outcome = task.ground_truth.termination_criteria
    else:
        expected_outcome = task.ground_truth.expected_outcome
    goal_checker = GoalChecker(expected_outcome, resolve_deepeval_model(SIMULATOR_DEFAULT_MODEL))
else:
    goal_checker = None
```

**TutoringSession 集成：**

```python
# session.py handle_send_message() 中，tc_checker 段之后新增
if self._goal_checker is not None:
    try:
        goals_met = self._goal_checker.check(self._conversation)
    except Exception as exc:
        logger.warning("GoalChecker failed: %s", exc)
        goals_met = False
    if goals_met:
        try:
            closing = self._student_sim.generate_closing(self._conversation)
        except Exception:
            closing = "Thanks for walking me through all of this — ..."
        self._conversation.append({"role": "user", "content": closing})
        self._done = True
        return json.dumps({
            "student_reply": closing,
            "status": "completed",
            "reason": "goals_met",
            ...
        })
```

#### 对齐的 Legacy 组件

| MCP 修改后 | Legacy 对标 |
|---|---|
| `GoalChecker._STOP_PROMPT` | `template.py:stop_simulation()` 第 108-140 行（**逐字复制**） |
| `GoalChecker.check()` | `conversation_simulator.py:stop_conversation()` 第 515-535 行 |
| expected_outcome 构建逻辑 | `build_conversational_golden()` 第 438-450 行 |
| goals_met 后追加 closing | `_append_student_closing()` 第 642-678 行 |

---

### 模块 6：错误处理与防御层

**文件：** `bench/mcp_servers/session.py`

#### 6.1 student_sim.generate_message() 异常捕获

```python
# session.py handle_send_message() 最后段
try:
    reply = self._student_sim.generate_message(self._conversation)
except Exception as exc:
    logger.warning("StudentSimulator.generate_message failed: %s", exc)
    reply = "Could you explain that in a bit more detail?"
self._conversation.append({"role": "user", "content": reply})
```

**对齐：** `model_callback:567-586` — agent generate_response 失败时 fallback "Let me continue..."

#### 6.2 超时优雅 wrap-up

```python
# session.py handle_send_message() deadline 段
if self._deadline is not None and time.time() > self._deadline:
    self._done = True
    # 对齐 model_callback:523-531 的 wrap-up 消息
    try:
        closing = self._student_sim.generate_closing(self._conversation)
    except Exception:
        closing = ""
    if closing:
        self._conversation.append({"role": "user", "content": closing})
    return json.dumps({
        "student_reply": closing,
        "status": "completed",
        "reason": "timeout",
        ...
    })
```

**对齐：** `model_callback:517-537` — 首次超时返回有意义的 wrap-up

#### 6.3 重复检测

```python
# session.py __init__ 新增
self._last_agent_msg: str = ""
self._repeat_count: int = 0
_MAX_REPEATS: int = 2

# handle_send_message() 入口处
if text == self._last_agent_msg:
    self._repeat_count += 1
    if self._repeat_count >= _MAX_REPEATS:
        self._done = True
        return json.dumps({
            "student_reply": "",
            "status": "completed",
            "reason": "agent_stuck",
            ...
        })
else:
    self._repeat_count = 0
self._last_agent_msg = text
```

**对齐：** `model_callback:606-624` — `_repeat_count >= _MAX_REPEATS` → force-stop

#### 6.4 max_turns 后追加 closing

```python
# session.py handle_send_message() max_turns 段
if self._turn >= self._max_turns:
    self._done = True
    # 对齐 _append_student_closing() 行为
    closing = ""
    try:
        closing = self._student_sim.generate_closing(self._conversation)
    except Exception:
        pass
    if closing:
        self._conversation.append({"role": "user", "content": closing})
    return json.dumps({
        "student_reply": closing,
        "status": "completed",
        "reason": "max_turns",
        ...
    })
```

**对齐：** `_append_student_closing()` 第 642-678 行

---

## 四、修改后对齐验证矩阵

| 行为 | Legacy 实现 | MCP 修改后实现 | 等价性 |
|------|-----------|-------------|--------|
| 首轮学生消息 prompt | `simulate_first_user_turn()` | `_FIRST_MESSAGE_PROMPT`（同文本） | **bit-exact** |
| 后续学生消息 prompt | `simulate_user_turn()` | `_NEXT_MESSAGE_PROMPT`（同文本） | **bit-exact** |
| 对话历史格式 | `json.dumps([t.model_dump()])` | `json.dumps([{role, content}])` | **bit-exact** |
| 输出解析 | `generate_schema(SimulatedInput)` | `model.generate(schema=SimulatedInput)` + fallback | **等价** |
| TC 类别终止判定 | `_EfficientSimulator.stop_conversation()` | `TCChecker.check()` | **等价**（同源代码） |
| 非 TC 类别终止判定 | DeepEval `stop_conversation()` + `stop_simulation` prompt | `GoalChecker.check()` + 同 prompt | **bit-exact prompt** |
| expected_outcome 构建 | `build_conversational_golden():438-450` | 同逻辑搬到 server 初始化 | **bit-exact** |
| Closing（TC met） | `_generate_closing()` + fallback | `generate_closing()` + 同 fallback 文本 | **等价** |
| Closing（max_turns） | `_append_student_closing()` | handle_send_message max_turns 段 | **等价** |
| Closing（timeout） | model_callback wrap-up → closing | handle_send_message timeout 段 | **等价** |
| 重复检测 | `_repeat_count >= 2` → force-stop | 同逻辑同阈值 | **等价** |
| Cost 追踪 | `simulator.simulation_cost` | `student_sim.total_cost` | **等价** |
| scenario 内容 | `build_scenario()` | `build_scenario()`（同一函数） | **identical** |
| user_description 内容 | `build_user_description()` | `build_user_description()`（同一函数） | **identical** |

---

## 五、完整修改文件清单

| # | 文件 | 修改内容 | 对齐 Legacy 组件 | 估算行数 |
|---|------|---------|-----------------|---------|
| 1 | `student_sim.py` | `_MESSAGE_PROMPT` → `_FIRST_MESSAGE_PROMPT` + `_NEXT_MESSAGE_PROMPT`（DeepEval 原文） | template.py:18-102 | ~40 |
| 2 | `student_sim.py` | `_format_transcript()` 改为 JSON array 输出 | conversation_simulator.py:59-63 | ~5 |
| 3 | `student_sim.py` | 新增 `SimulatedInput` schema + `_generate_parsed()` + cost 累积 + fallback | generate_schema():606-623 | ~35 |
| 4 | `student_sim.py` | `generate_message()` 区分首轮/后续 | generate_first_user_input vs generate_next_user_input | ~10 |
| 5 | `session.py` | 新增 `GoalChecker` + `ConversationCompletion` schema（复制 stop_simulation prompt） | template.py:105-140 + stop_conversation:508-535 | ~55 |
| 6 | `session.py` | `__init__` 增加 `_goal_checker` 参数 | build_conversational_golden:438-450 | ~5 |
| 7 | `session.py` | `handle_send_message()` 集成 GoalChecker 判定 | conversation_simulator.py:228-235 | ~20 |
| 8 | `session.py` | student_sim 调用加 try/except + fallback | model_callback:567-586 | ~10 |
| 9 | `session.py` | closing 生成加 try/except + 硬编码 fallback | _generate_closing:397-405 | ~8 |
| 10 | `session.py` | tc_checker.check() 加 try/except | _EfficientSimulator.stop_conversation 异常隔离 | ~5 |
| 11 | `session.py` | timeout 返回优雅 wrap-up + closing | model_callback:517-537 | ~12 |
| 12 | `session.py` | 新增重复检测 (`_last_agent_msg` + `_repeat_count`) | model_callback:606-624 | ~15 |
| 13 | `session.py` | max_turns 后追加 closing | _append_student_closing:642-678 | ~10 |
| 14 | `mcp_server.py` | `_build_standalone_server()` 构建 expected_outcome + GoalChecker | build_conversational_golden:438-450 | ~20 |
| **合计** | **3 个文件** | | | **~250 行** |

---

## 六、分批实施计划

### Batch 1：Prompt + 格式 + 解析对齐（student_sim.py 独立改动）

**改动文件：** 仅 `student_sim.py`
**涉及清单项：** #1, #2, #3, #4
**估算行数：** ~90 行

**具体步骤：**
1. 替换 `_MESSAGE_PROMPT` 为 `_FIRST_MESSAGE_PROMPT` + `_NEXT_MESSAGE_PROMPT`
2. `_format_transcript()` 改为 JSON array 输出
3. 新增 `SimulatedInput` Pydantic schema
4. 新增 `_generate_parsed()` 方法（structured output + cost tracking + fallback）
5. `generate_message()` 内部区分首轮/后续，调用对应 prompt
6. `generate_closing()` 也改用 `_generate_parsed` 风格（但保留纯文本 prompt，不要求 JSON 输出——closing 不需要结构化解析）

**验证方法：**
```python
# 单元验证：构造一段对话历史，调 generate_message()
# 确认输出是纯净的学生消息文本（无 JSON wrapper、无角色标签）
# 确认 total_cost > 0
```

**上下文管理：** 本批只需 `student_sim.py`（119 行）+ DeepEval template.py（141 行）作为参考。总上下文 ~260 行。

---

### Batch 2：错误处理 + 防御层（session.py 非结构性改动）

**改动文件：** 仅 `session.py`
**涉及清单项：** #8, #9, #10, #11, #12, #13
**估算行数：** ~60 行

**具体步骤：**
1. `__init__` 新增 `_last_agent_msg`、`_repeat_count` 属性
2. `handle_send_message()` 入口处加重复检测
3. deadline 段改为生成 closing（而非返回空）
4. tc_checker.check() 外层加 try/except
5. closing 生成加 try/except + 硬编码 fallback
6. student_sim.generate_message() 加 try/except + fallback
7. max_turns 段追加 closing 生成

**验证方法：**
```python
# 异常注入验证：mock student_sim.generate_message 抛异常
# 确认 session 不崩溃，返回 fallback 消息
# 确认 _repeat_count 连续 2 次相同消息后 status=completed
```

**上下文管理：** 本批只需 `session.py`（190 行）。Batch 1 的改动不影响此批接口。

---

### Batch 3：GoalChecker + 集成（session.py + mcp_server.py 结构性改动）

**改动文件：** `session.py` + `mcp_server.py`
**涉及清单项：** #5, #6, #7, #14
**估算行数：** ~100 行

**具体步骤：**
1. `session.py` 新增 `ConversationCompletion` + `GoalChecker` 类
2. `TutoringSession.__init__` 增加 `goal_checker` 参数
3. `handle_send_message()` 在 tc_checker 段之后、max_turns 之前插入 GoalChecker 判定
4. `mcp_server.py:_build_standalone_server()` 构建 expected_outcome + 实例化 GoalChecker
5. 将 GoalChecker 传入 TutoringSession

**验证方法：**
```python
# 功能验证：用一个 data_analysis task 跑 MCP session
# 确认对话在 goal 达成时提前终止（而非跑满 max_turns）
# 对比 Legacy 路径同 task 的终止轮数
```

**上下文管理：** 本批需要 `session.py`（Batch 2 后 ~250 行）+ `mcp_server.py`（241 行）+ `build_conversational_golden()` 作为逻辑参考。总上下文 ~530 行。

---

### Batch 4：端到端验证

**改动文件：** 无代码改动
**目的：** 确认三批改动集成后 MCP 路径行为与 Legacy 一致

**验证步骤：**

1. **冒烟测试（3 个 task）：** 每组选 1 个代表性 task
   - TC 类别：`S01_ma_crossover`（strategy）
   - 非 TC 类别：`D01_load_inspect_ohlcv`（data_analysis）
   - Adversarial：`A01_*`（adversarial）
   - 用 MCP 路径跑 1 次，确认无报错、对话正常终止

2. **对齐验证（可选，高置信度时跳过）：**
   - 同上 3 个 task，Legacy + MCP 各跑 1 次
   - 对比终止轮数（±2 轮以内可接受——LLM 非确定性）
   - 对比学生消息平均长度（±20% 以内可接受）

**上下文管理：** 无代码上下文需求，只需命令行操作。

---

### 批次依赖关系

```
Batch 1 (student_sim.py)
    │
    ▼
Batch 2 (session.py 防御层)  ← 可与 Batch 1 并行（无接口依赖）
    │
    ▼
Batch 3 (GoalChecker 集成)   ← 依赖 Batch 2（session.py 基础改动）
    │
    ▼
Batch 4 (端到端验证)          ← 依赖 Batch 1+2+3 全部完成
```

**注：Batch 1 和 Batch 2 可并行执行。** 两者改动的是不同文件的不同部分，不存在合并冲突。并行执行可将总实施时间从 4 轮缩减到 3 轮。

---

## 七、handle_send_message 修改后完整执行顺序

供实施时参考，标注每一步对齐的 Legacy 组件：

```python
def handle_send_message(self, text: str) -> str:
    # ── 前置检查 ──
    if self._done:                    → 返回 completed
    if not text.strip():              → 返回 error

    # ── 重复检测 ──                    对齐 model_callback:606-624
    if text == self._last_agent_msg:
        self._repeat_count += 1
        if >= _MAX_REPEATS:           → 返回 completed (reason=agent_stuck)
    else:
        self._repeat_count = 0
    self._last_agent_msg = text

    # ── 记录 + 推进 ──
    conversation.append(assistant msg)
    turn += 1
    proxy.set_turn(turn)

    # ── Deadline 检查 ──               对齐 model_callback:517-537
    if deadline exceeded:
        generate closing (try/except)  → 返回 completed (reason=timeout)

    # ── TC 检查 ──                     对齐 _EfficientSimulator.stop_conversation
    try: tc_met = tc_checker.check()
    except: tc_met = False
    if tc_met:
        generate closing (try/except + fallback)
                                       → 返回 completed (reason=objectives_met)

    # ── Goal 检查 ──                   对齐 DeepEval stop_conversation + stop_simulation
    try: goals_met = goal_checker.check()
    except: goals_met = False
    if goals_met:
        generate closing (try/except + fallback)
                                       → 返回 completed (reason=goals_met)

    # ── Max turns 检查 ──              对齐 _append_student_closing
    if turn >= max_turns:
        generate closing (try/except)  → 返回 completed (reason=max_turns)

    # ── 生成学生消息 ──                 对齐 generate_next_user_input
    try: reply = student_sim.generate_message()
    except: reply = fallback
    conversation.append(user reply)
                                       → 返回 active
```
