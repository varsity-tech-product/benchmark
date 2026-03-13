# Agent Adapter Construction Guide

## 1. Overview

This project uses the **Adapter pattern** to integrate agents from different SDKs into the benchmark evaluation framework. All adapters inherit from `BaseAgentAdapter` and expose a unified `generate_response()` interface, which is called by the orchestrator during the conversation loop.

The currently verified reference implementation is the **OpenAI Agent SDK adapter** (`openai_adapter.py`). Other SDK adapters (anthropic, google) are rough scaffolds that have not been validated. The repo also includes a **Claude Code CLI adapter** (`claude_code_adapter.py`), which is distinct from the SDK adapters because it shells out to the `claude` CLI and bridges benchmark MCP tools over stdio/socket transport.

Separately, DeepEval baseline-model wrappers live under `bench/orchestrator/deepeval_models/`. Those wrappers are not `BaseAgentAdapter` implementations and should not be mixed into the agent-adapter workflow described in this guide.

## 2. Architecture Overview

```
run_benchmark.py
  └── _create_agent(args) ← Instantiates adapter based on --agent argument
        └── OpenAIAgentAdapter / ClaudeAgentAdapter / ClaudeCodeAdapter / GoogleAdapter / ...

orchestrator.py
  └── run_single_task(task, persona, agent: BaseAgentAdapter, ...)
        ├── Phase 1: RESET — Create sandbox, configure MCP proxy
        ├── Phase 1.5: Inject dynamic context (calls agent.set_task_context())
        ├── Phase 2: INTERACT — Conversation loop, calls agent.generate_response() each turn
        ├── Phase 3: EVALUATE — Scoring
        └── Phase 4: TEARDOWN — Cleanup

During the conversation loop, the orchestrator provides three things to the adapter:
  1. messages: list[dict]       — Conversation history [{role, content}, ...]
  2. available_tools: list[dict] — List of MCP tool schemas
  3. tool_callback: callable    — Tool execution callback: tool_callback(name, **kwargs) -> str
```

## 3. BaseAgentAdapter Interface Specification

Located at `bench/orchestrator/agent_adapters/base_adapter.py`. **The following must be implemented or considered**:

### 3.1 Must Implement

```python
def generate_response(
    self,
    messages: list[dict],
    available_tools: list[dict],
    tool_callback: Optional[callable] = None,
) -> str:
```

- **Inputs**:
  - `messages` — `[{role: "user"/"assistant", content: "..."}]`, full conversation history
  - `available_tools` — List of benchmark MCP tool schemas (format described in §5 below)
  - `tool_callback` — Tool execution function provided by MCPProxy, signature: `tool_callback(tool_name: str, **kwargs) -> str`
- **Output**: Text string of the agent's reply to the student
- **Key requirement**: The agent may autonomously perform multi-step reasoning (multiple tool calls) within this method, ultimately returning a single text response

### 3.2 Recommended Override

```python
def set_agent_max_steps(self, n: int):
```
- The orchestrator calls this method at the start of each task, passing `task.agent_max_steps`
- Used to limit the maximum number of LLM invocations in the agent's internal loop (prevents infinite loops)

### 3.3 Should Provide

```python
def set_task_context(self, context: str):
```
- Although not part of the `BaseAgentAdapter` abstract interface, the orchestrator detects it via `hasattr` and uses it preferentially
- Used to inject per-task dynamic context (task description + student persona)
- If this method is not provided, the orchestrator falls back to directly mutating `agent.system_prompt`

### 3.4 Token Tracking

```python
self._token_records: list[TokenRecord] = []
```
- Initialized in `__init__` by the parent class
- The adapter should append `TokenRecord(model, input_tokens, output_tokens, cost_usd)` to this list after each API call
- Use `estimate_cost(model, inp, out)` from `config/pricing.py` to calculate cost
- The orchestrator reads this via `agent.get_token_records()`

## 4. Complete Steps to Build a New Adapter

### Step 1: Create the File

Create `xxx_adapter.py` under `bench/orchestrator/agent_adapters/`.

### Step 2: Basic Skeleton

```python
"""XxxAgent SDK adapter for QuantTutorBench.

Uses the Xxx SDK's agent loop for multi-step reasoning.

Install: pip install xxx-sdk
Reference: https://...
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from .base_adapter import BaseAgentAdapter, TokenRecord
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import XXX_AGENT_MODEL  # Must be added to llm_config.py

try:
    from xxx_sdk import SomeAgent, SomeRunner, SomeTool  # SDK imports
    XXX_SDK_AVAILABLE = True
except ImportError:
    XXX_SDK_AVAILABLE = False

DEFAULT_AGENT_MAX_TURNS = 15


class XxxAgentAdapter(BaseAgentAdapter):
    def __init__(
        self,
        model: str = XXX_AGENT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "xxx_agent_sdk",
        max_turns: int = DEFAULT_AGENT_MAX_TURNS,
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT
        self.max_turns = max_turns
        # ... API key handling, state initialization
```

### Step 3: Implement Four Core Responsibilities

#### Responsibility A: Tool Schema Conversion

The benchmark provides tool schemas in this format (from MCPProxy):
```json
{
  "name": "fetch_market_data",
  "description": "Fetch OHLCV market data...",
  "parameters": {
    "ticker": {
      "type": "string",
      "description": "Stock ticker symbol",
      "required": true
    },
    "start_date": {
      "type": "string",
      "description": "Start date in YYYY-MM-DD format",
      "required": true
    }
  }
}
```

You need to convert this into the target SDK's tool format. Refer to the OpenAI adapter's `_build_agent_tools()` method.

**Important notes**:
- `parameters` is a **flat dictionary** where each key is a parameter name and the value is a `{type, description, required}` object
- Most SDKs expect JSON Schema format (`{type: "object", properties: {...}, required: [...]}`), requiring manual conversion
- The `required` field is embedded inside each parameter and must be extracted to a top-level `required` array
- Handle the `items` field (for array-type parameters)

#### Responsibility B: Tool Callback Bridging

This is the most critical part. You need to connect the benchmark's `tool_callback` to the SDK's tool execution mechanism.

The benchmark's `tool_callback` signature:
```python
tool_callback(tool_name: str, **kwargs) -> str
```

Your SDK may require a different signature. Examples:
- OpenAI SDK: `async (ToolContext, args_json: str) -> str`
- Claude SDK: `async (args: dict) -> {content: [{type: "text", text: str}]}`
- Other SDKs may have other formats

**Key pattern** (from the OpenAI adapter): Use a **closure factory** to capture the tool name and adapter reference:

```python
def make_tool_fn(name, adapter_ref):
    async def tool_fn(ctx, args_json: str) -> str:
        args = json.loads(args_json) if args_json else {}
        result = adapter_ref._tool_callback(name, **args)
        return str(result)
    return tool_fn
```

**Important**: If the tool callback is updated between conversation turns (as in the OpenAI adapter where `_tool_callback` is updated each turn), do not capture `tool_callback` directly in the closure. Instead, use indirect reference via `adapter_ref._tool_callback`.

#### Responsibility C: Multi-Turn Conversation State Management

The orchestrator's conversation loop calls `generate_response()` multiple times. Each call represents a **conversation turn** (the student asks a question). Within a single turn, the agent may autonomously execute multiple tool calls.

You need to decide how to manage cross-turn state:

**Approach 1 (Recommended): Persistent SDK agent + history accumulation** (used by the OpenAI adapter)
- Agent instance is created once and reused across turns
- Each turn extracts only the latest user message from `messages` and appends it to internal history
- Uses the SDK's context serialization mechanism (e.g., `to_input_list()`) to preserve full intermediate state

```python
def generate_response(self, messages, available_tools, tool_callback):
    self._tool_callback = tool_callback
    agent = self._get_or_create_agent(available_tools)

    # Extract only the latest user message
    new_user_msg = None
    for msg in reversed(messages):
        if msg["role"] == "user":
            new_user_msg = msg["content"]
            break

    self._input_history.append({"role": "user", "content": new_user_msg})
    result = Runner.run_sync(agent, self._input_history, max_turns=self.max_turns)
    self._input_history = result.to_input_list()  # SDK preserves full context
    return result.final_output or ""
```

**Approach 2: Rebuild each turn** (suitable for stateless SDKs or simple APIs)
- Pass the full `messages` list directly each time
- No need to maintain internal state
- Downside: SDK's intermediate tool call process is not preserved between turns

**Approach 3: Hybrid** (used by the Claude SDK adapter)
- Format the full `messages` into a single prompt string and pass it to the SDK
- The SDK reasons autonomously within a single turn

Which approach to choose depends on the target SDK's capabilities. Prefer Approach 1.

#### Responsibility D: Token Usage Tracking

Extract and record token usage after each API call:

```python
def _record_usage(self, result):
    from config.pricing import estimate_cost

    # Extract usage from the SDK's response object
    usage = ...  # SDK-specific extraction
    inp = usage.input_tokens or 0
    out = usage.output_tokens or 0
    self._token_records.append(
        TokenRecord(
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=estimate_cost(self.model, inp, out),
        )
    )
```

**Note**: If the SDK does not expose token usage, you can estimate from character count (~3.5 chars/token) as a fallback. Refer to `anthropic_adapter.py` L270-283.

### Step 4: Error Handling

The following cases must be handled:
1. **SDK not installed**: Provide a fallback or clear error message
2. **Step limit exceeded**: Return a graceful fallback message (refer to the OpenAI adapter's `MaxTurnsExceeded` handling)
3. **API errors**: Catch and return `"[XxxSDK error: {e}]"` format error messages; do not leak tracebacks to the evaluation pipeline

### Step 5: Register with the System

#### 5a. Configure Model Names

Add the following to `bench/config/llm_config.py`:

```python
# Native API format model name
XXX_AGENT_MODEL = "xxx-model-v1"
# OpenRouter format model name (for baseline comparison)
XXX_AGENT_MODEL_OR = "xxx/xxx-model-v1"

# Add to AGENT_MODEL_MAP
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    # ...existing...
    "xxx": (XXX_AGENT_MODEL, XXX_AGENT_MODEL_OR),
}
```

#### 5b. Register with CLI

Add a branch in the `_create_agent()` function in `bench/run_benchmark.py`:

```python
elif agent_type == "xxx":
    from orchestrator.agent_adapters.xxx_adapter import XxxAgentAdapter
    model = model_override or get_model_for_agent("xxx")
    return XxxAgentAdapter(
        model=model, system_prompt=system_prompt, agent_name=agent_name
    )
```

#### 5c. Add Pricing Information (Optional)

If cost tracking is needed, add to `bench/config/pricing.py`:

```python
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ...existing...
    "xxx/xxx-model-v1": (input_price, output_price),
}
```

## 5. Tool Schema Format Reference

Each tool schema in the `available_tools` list passed to the adapter has the following format:

```python
{
    "name": "shell_exec",
    "description": "Execute a shell command in the sandbox",
    "parameters": {
        "command": {
            "type": "string",
            "description": "The shell command to execute",
            "required": True
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds",
            "required": False
        }
    }
}
```

This is generated by MCPProxy's `get_tool_schemas()` method. Note:
- `parameters` is a **flat dictionary**, not standard JSON Schema
- Each parameter's `required` field is embedded inside the parameter object
- May contain an `items` field (when `type` is `"array"`)

## 6. Validation Checklist

After completing a new adapter, validate with the following commands:

```bash
# Single-task end-to-end test
python bench/run_benchmark.py run-single \
    --task D01_load_inspect_ohlcv \
    --persona beginner_no_finance \
    --agent xxx \
    --save-trace --save-scores

# Check output:
# 1. trace.md should show the complete conversation + tool call chain
# 2. Score report values should be reasonable (not all zeros)
# 3. Pay attention to tool_usage score: is the agent correctly using core tools?

# Multi-task run
python bench/run_benchmark.py run --agent xxx --condition agent --layer 2
```

Key things to verify:
- Can the agent autonomously chain multiple tool calls within a single turn?
- Is context preserved across turns (can turn 2 reference tool call results from turn 1)?
- Does the agent degrade gracefully when `MaxTurns` is exceeded (no crash)?
- Is token usage correctly recorded (check the Cost Breakdown section in trace.md)?

## 7. Common Pitfalls

1. **Closure trap**: Use a `make_tool_fn(name, ref)` factory function; do not write lambda/closure directly in a for loop that captures loop variables
2. **Async/sync mixing**: Most SDKs are async, but `generate_response()` is a synchronous interface. You need to bridge inside the adapter using `asyncio.run()` or `Runner.run_sync()`
3. **Environment variable conflicts**: Different SDKs may read the same environment variables (e.g., `OPENAI_API_KEY`); be careful to isolate them
4. **Context window**: Accumulated `_input_history` may exceed the model's context window; consider a truncation strategy
5. **`tool_callback` update timing**: The `tool_callback` may be a different object on each `generate_response()` call. If tool closures directly capture an old callback, tool calls will be routed to the wrong proxy instance
