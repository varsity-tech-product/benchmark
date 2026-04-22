"""Test OpenAI Direct API mode cross-turn context persistence.

Verifies that _input_history correctly preserves tool_calls and tool
results across generate_response() calls, fixing the context-loss bug.

Uses a cheap model (gpt-4o-mini) via OpenRouter to minimize cost.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def test_direct_api_context_persistence():
    """Multi-turn test: agent should remember tool results from turn 1 in turn 2."""
    from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

    # Use cheap model via OpenRouter
    adapter = OpenAIAgentAdapter(
        model="openai/gpt-4.1-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        max_turns=5,
    )

    # Force Direct API mode for this test
    call_count = {"n": 0}

    def tool_callback(name, **kwargs):
        call_count["n"] += 1
        if name == "get_secret_value":
            return "The secret value is QUANTUM_ZEBRA_42"
        return f"Tool {name} called"

    tools = [
        {
            "name": "get_secret_value",
            "description": "Returns a secret value for testing",
            "parameters": {},
        },
    ]

    # --- Turn 1: Ask to fetch the secret ---
    messages_t1 = [
        {"role": "user", "content": "Please call get_secret_value to find the secret."}
    ]
    print("  Turn 1: Fetching secret...")
    resp1 = adapter._generate_direct(messages_t1, tools, tool_callback)
    print(f"    Response: {resp1[:300]}")
    assert "[OpenAI API error" not in resp1, f"Turn 1 error: {resp1}"
    assert (
        call_count["n"] >= 1
    ), f"Tool should be called in turn 1 (got {call_count['n']})"
    turn1_calls = call_count["n"]
    print(f"    Response: {resp1[:150]}")
    print(f"    Tool calls: {turn1_calls}")
    print(f"    History length: {len(adapter._input_history)}")

    # Verify history contains tool_calls and tool results
    has_tool_calls = False
    has_tool_result = False
    for msg in adapter._input_history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            has_tool_calls = True
        if msg.get("role") == "tool":
            has_tool_result = True

    assert has_tool_calls, "History should contain assistant messages with tool_calls"
    assert has_tool_result, "History should contain tool result messages"
    print("    History has tool_calls + tool results ✓")

    # --- Turn 2: Ask what the secret was (NO tool call expected) ---
    messages_t2 = [
        {"role": "user", "content": "Please call get_secret_value to find the secret."},
        {"role": "assistant", "content": resp1},
        {
            "role": "user",
            "content": "What was the secret value you just found? Just tell me, don't call any tools.",
        },
    ]
    print("  Turn 2: Recalling secret (no tool call expected)...")
    resp2 = adapter._generate_direct(messages_t2, tools, tool_callback)
    assert "[OpenAI API error" not in resp2, f"Turn 2 error: {resp2}"

    # The secret should appear in turn 2 response
    assert "QUANTUM_ZEBRA_42" in resp2, (
        f"Cross-turn context FAILED: secret not in turn 2 response.\n"
        f"Turn 2 response: {resp2[:300]}"
    )

    turn2_new_calls = call_count["n"] - turn1_calls
    print(f"    Response: {resp2[:150]}")
    print(f"    Extra tool calls in turn 2: {turn2_new_calls}")
    print(f"    History length: {len(adapter._input_history)}")
    print("  PASS: Cross-turn context persistence ✓")


def test_direct_api_multi_tool():
    """Verify multiple tool calls work and results persist."""
    from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

    adapter = OpenAIAgentAdapter(
        model="openai/gpt-4.1-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        max_turns=5,
    )

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

    print("  Calling API (multi-tool test)...")
    response = adapter._generate_direct(messages, tools, tool_callback)
    assert "[OpenAI API error" not in response, f"Error: {response}"

    assert "fetch_btc" in called_tools, "fetch_btc should be called"
    assert "fetch_eth" in called_tools, "fetch_eth should be called"
    print(f"    Tools called: {called_tools}")
    print(f"    Response: {response[:150]}")
    print(f"    History length: {len(adapter._input_history)}")
    print("  PASS: Multi-tool execution ✓")


def test_reset_clears_state():
    """Verify reset() clears _input_history and _task_context."""
    from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

    adapter = OpenAIAgentAdapter(
        model="openai/gpt-4.1-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )

    adapter._input_history = [{"role": "user", "content": "hello"}]
    adapter._task_context = "some context"

    adapter.reset()

    assert adapter._input_history == [], "reset should clear _input_history"
    assert adapter._task_context == "", "reset should clear _task_context"
    print("  PASS: reset() clears state ✓")


def test_set_task_context_resets_history():
    """Verify set_task_context clears history for new task."""
    from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

    adapter = OpenAIAgentAdapter(
        model="openai/gpt-4.1-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )

    adapter._input_history = [{"role": "user", "content": "old msg"}]
    adapter.set_task_context("New task context")

    assert adapter._input_history == [], "set_task_context should clear history"
    assert adapter._task_context == "New task context"
    print("  PASS: set_task_context resets history ✓")


if __name__ == "__main__":
    print("\n=== OpenAI Direct API Context Persistence Tests ===\n")

    # Unit tests (no API call)
    print("[1/4] reset() clears state")
    test_reset_clears_state()

    print("[2/4] set_task_context resets history")
    test_set_task_context_resets_history()

    # Live API tests (cheap model)
    print("\n--- Live API Tests (gpt-4o-mini via OpenRouter) ---\n")

    print("[3/4] Multi-turn context persistence")
    test_direct_api_context_persistence()

    print("\n[4/4] Multi-tool execution")
    test_direct_api_multi_tool()

    print("\n=== ALL TESTS PASSED ===\n")
