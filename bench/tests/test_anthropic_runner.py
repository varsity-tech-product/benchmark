"""Integration test for Anthropic adapter BetaToolRunner migration.

Tests:
1. DynamicTool creation and schema conversion
2. Adapter initialization (OAuth + API key modes)
3. set_task_context / _get_full_system_prompt / reset
4. Cross-turn context persistence (_input_history)
5. Live API call with tool execution (requires ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)
6. Multi-turn context retention (agent remembers tool results across turns)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def test_dynamic_tool_creation():
    """DynamicTool correctly converts MCP schema to Anthropic format."""
    from orchestrator.agent_adapters.anthropic_adapter import DynamicTool

    schema = {
        "name": "test_tool",
        "description": "A test tool",
        "parameters": {
            "command": {
                "type": "string",
                "description": "Command to run",
                "required": True,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
            },
        },
    }

    calls = []

    def callback(name, **kwargs):
        calls.append((name, kwargs))
        return f"result for {name}"

    tool = DynamicTool(schema, callback)

    # Check name
    assert tool.name == "test_tool"

    # Check to_dict output
    d = tool.to_dict()
    assert d["name"] == "test_tool"
    assert d["description"] == "A test tool"
    assert d["input_schema"]["properties"]["command"]["type"] == "string"
    assert d["input_schema"]["properties"]["timeout"]["type"] == "integer"
    assert "command" in d["input_schema"].get("required", [])

    # Check call routing
    result = tool.call({"command": "ls -la", "timeout": "30"})
    assert result == "result for test_tool"
    assert calls == [("test_tool", {"command": "ls -la", "timeout": "30"})]

    # Check error handling in call
    def bad_callback(name, **kwargs):
        raise ValueError("boom")

    bad_tool = DynamicTool(schema, bad_callback)
    try:
        bad_tool.call({"command": "x"})
        assert False, "Should have raised"
    except ValueError as e:
        assert "boom" in str(e)

    print("  PASS: DynamicTool creation and schema conversion")


def test_build_runner_tools():
    """_build_runner_tools converts list of schemas to DynamicTool list."""
    from orchestrator.agent_adapters.anthropic_adapter import _build_runner_tools

    schemas = [
        {"name": "tool_a", "description": "A", "parameters": {}},
        {"name": "tool_b", "description": "B", "parameters": {"x": {"type": "string"}}},
    ]

    def cb(name, **kw):
        return "ok"

    tools = _build_runner_tools(schemas, cb)
    assert len(tools) == 2
    assert tools[0].name == "tool_a"
    assert tools[1].name == "tool_b"

    # Verify they're BetaBuiltinFunctionTool instances
    from anthropic.lib.tools._beta_functions import BetaBuiltinFunctionTool

    for t in tools:
        assert isinstance(t, BetaBuiltinFunctionTool)

    print("  PASS: _build_runner_tools")


def test_adapter_init_and_state():
    """Adapter initializes with correct state and methods."""
    from config.llm_config import ANTHROPIC_USE_SDK
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    if ANTHROPIC_USE_SDK:
        print("  SKIP: ANTHROPIC_USE_SDK=True, testing direct mode only")
        return

    adapter = ClaudeAgentAdapter()

    # Check initial state
    assert adapter._input_history == []
    assert adapter._task_context == ""
    assert adapter._client is not None

    # Check set_task_context
    adapter.set_task_context("You are teaching about moving averages.")
    assert adapter._task_context == "You are teaching about moving averages."
    assert adapter._input_history == []  # Reset on context change

    # Simulate some history
    adapter._input_history = [{"role": "user", "content": "hello"}]
    adapter.set_task_context("New task")
    assert adapter._input_history == []  # History cleared

    # Check _get_full_system_prompt
    adapter._task_context = "TASK CONTEXT"
    prompt = adapter._get_full_system_prompt()
    assert "TASK CONTEXT" in prompt
    assert adapter.system_prompt in prompt

    # Check reset
    adapter._input_history = [{"role": "user", "content": "x"}]
    adapter._task_context = "ctx"
    adapter.reset()
    assert adapter._input_history == []
    assert adapter._task_context == ""

    print("  PASS: Adapter init and state management")


def test_adapter_has_set_task_context():
    """Orchestrator's hasattr check will now find set_task_context."""
    from config.llm_config import ANTHROPIC_USE_SDK
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    if ANTHROPIC_USE_SDK:
        print("  SKIP: ANTHROPIC_USE_SDK=True")
        return

    adapter = ClaudeAgentAdapter()
    assert hasattr(adapter, "set_task_context")
    assert callable(adapter.set_task_context)
    print("  PASS: hasattr(adapter, 'set_task_context') is True")


def test_live_single_turn():
    """Live API test: single turn with tool execution."""
    from config.llm_config import ANTHROPIC_USE_SDK
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    if ANTHROPIC_USE_SDK:
        print("  SKIP: ANTHROPIC_USE_SDK=True")
        return

    adapter = ClaudeAgentAdapter(max_agent_turns=5)

    tool_calls = []

    def tool_callback(name, **kwargs):
        tool_calls.append({"name": name, "args": kwargs})
        if name == "get_environment_info":
            return "Python 3.13, pandas 2.2, numpy 1.26"
        return f"Tool {name} called with {kwargs}"

    tools = [
        {
            "name": "get_environment_info",
            "description": "Get information about the available environment",
            "parameters": {},
        },
    ]

    messages = [
        {
            "role": "user",
            "content": "What Python version is available? Use the get_environment_info tool to check.",
        }
    ]

    print("  Calling API (single turn)...")
    response = adapter.generate_response(messages, tools, tool_callback)

    assert response, "Response should not be empty"
    assert "[Anthropic API error" not in response, f"API error: {response}"

    # Tool should have been called
    assert len(tool_calls) > 0, "Tool should have been called"
    assert tool_calls[0]["name"] == "get_environment_info"

    # Response should mention Python
    assert (
        "python" in response.lower() or "3.13" in response
    ), f"Response: {response[:200]}"

    # Input history should be populated
    assert (
        len(adapter._input_history) > 1
    ), f"Input history should have >1 entries, got {len(adapter._input_history)}"

    # Check token tracking
    records = adapter.get_token_records()
    assert len(records) > 0, "Should have token records"
    assert records[0].input_tokens > 0
    assert records[0].output_tokens > 0

    print(
        f"  PASS: Single turn (tool_calls={len(tool_calls)}, "
        f"history={len(adapter._input_history)}, "
        f"tokens={records[0].input_tokens}+{records[0].output_tokens})"
    )


def test_live_multi_turn_context():
    """Live API test: verify cross-turn context persistence.

    Turn 1: Ask agent to call a tool that returns a secret value.
    Turn 2: Ask agent what the secret value was (WITHOUT calling the tool again).
    If context is preserved, agent should remember. If not, it won't know.
    """
    from config.llm_config import ANTHROPIC_USE_SDK
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    if ANTHROPIC_USE_SDK:
        print("  SKIP: ANTHROPIC_USE_SDK=True")
        return

    adapter = ClaudeAgentAdapter(max_agent_turns=5)

    call_count = {"n": 0}

    def tool_callback(name, **kwargs):
        call_count["n"] += 1
        if name == "get_secret_value":
            return "The secret value is QUANTUM_ZEBRA_42"
        return "unknown tool"

    tools = [
        {
            "name": "get_secret_value",
            "description": "Returns a secret value for testing",
            "parameters": {},
        },
    ]

    # Turn 1: Ask to fetch the secret
    messages_t1 = [
        {"role": "user", "content": "Please call get_secret_value to find the secret."}
    ]
    print("  Turn 1: Fetching secret...")
    resp1 = adapter.generate_response(messages_t1, tools, tool_callback)
    assert "[Anthropic API error" not in resp1, f"Turn 1 error: {resp1}"
    assert call_count["n"] >= 1, "Tool should be called in turn 1"
    turn1_calls = call_count["n"]

    # Turn 2: Ask what the secret was — should NOT need another tool call
    messages_t2 = [
        {"role": "user", "content": "Please call get_secret_value to find the secret."},
        {"role": "assistant", "content": resp1},
        {
            "role": "user",
            "content": "What was the secret value you just found? Just tell me, don't call any tools.",
        },
    ]
    print("  Turn 2: Recalling secret (no tool call expected)...")
    resp2 = adapter.generate_response(messages_t2, tools, tool_callback)
    assert "[Anthropic API error" not in resp2, f"Turn 2 error: {resp2}"

    # The secret should appear in turn 2 response (context preserved)
    assert "QUANTUM_ZEBRA_42" in resp2, (
        f"Cross-turn context FAILED: secret not in turn 2 response.\n"
        f"Turn 2 response: {resp2[:300]}"
    )

    # Ideally no additional tool call in turn 2
    turn2_new_calls = call_count["n"] - turn1_calls
    context_note = (
        f"(turn 2 made {turn2_new_calls} extra tool calls)"
        if turn2_new_calls > 0
        else "(no extra tool calls — context fully preserved)"
    )

    print(f"  PASS: Multi-turn context persistence {context_note}")
    print(f"    History length: {len(adapter._input_history)} entries")

    # Verify history contains tool_use and tool_result blocks
    has_tool_use = False
    has_tool_result = False
    for msg in adapter._input_history:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        has_tool_use = True
                    if block.get("type") == "tool_result":
                        has_tool_result = True
                elif hasattr(block, "type"):
                    if block.type == "tool_use":
                        has_tool_use = True

    assert has_tool_use, "History should contain tool_use blocks"
    assert has_tool_result, "History should contain tool_result blocks"
    print("  PASS: History contains tool_use + tool_result blocks")


def test_live_parallel_tools():
    """Live API test: verify multiple tool calls are executed."""
    from config.llm_config import ANTHROPIC_USE_SDK
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    if ANTHROPIC_USE_SDK:
        print("  SKIP: ANTHROPIC_USE_SDK=True")
        return

    adapter = ClaudeAgentAdapter(max_agent_turns=5)

    called_tools = []

    def tool_callback(name, **kwargs):
        called_tools.append(name)
        if name == "fetch_btc":
            return "BTC: $67,500"
        elif name == "fetch_eth":
            return "ETH: $3,200"
        return "unknown"

    tools = [
        {"name": "fetch_btc", "description": "Get Bitcoin price", "parameters": {}},
        {"name": "fetch_eth", "description": "Get Ethereum price", "parameters": {}},
    ]

    messages = [
        {
            "role": "user",
            "content": "Get me both BTC and ETH prices. Call both fetch_btc and fetch_eth tools.",
        }
    ]

    print("  Calling API (parallel tools test)...")
    response = adapter.generate_response(messages, tools, tool_callback)
    assert "[Anthropic API error" not in response, f"Error: {response}"

    assert "fetch_btc" in called_tools, "fetch_btc should be called"
    assert "fetch_eth" in called_tools, "fetch_eth should be called"
    assert (
        "67,500" in response or "67500" in response
    ), f"BTC price missing: {response[:200]}"
    assert (
        "3,200" in response or "3200" in response
    ), f"ETH price missing: {response[:200]}"

    print(f"  PASS: Multiple tools called: {called_tools}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Anthropic BetaToolRunner Integration Tests ===\n")

    # Unit tests (no API call)
    print("[1/7] DynamicTool creation")
    test_dynamic_tool_creation()

    print("[2/7] _build_runner_tools")
    test_build_runner_tools()

    print("[3/7] Adapter init and state")
    test_adapter_init_and_state()

    print("[4/7] hasattr set_task_context")
    test_adapter_has_set_task_context()

    # Live API tests
    print("\n--- Live API Tests (require credentials) ---\n")

    print("[5/7] Single turn with tool")
    test_live_single_turn()

    print("[6/7] Multi-turn context persistence")
    test_live_multi_turn_context()

    print("[7/7] Parallel tool execution")
    test_live_parallel_tools()

    print("\n=== ALL TESTS PASSED ===\n")
