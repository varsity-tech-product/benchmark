"""Verify BetaToolRunner crash recovery — accumulated text is preserved.

Run:  python -m tests.test_runner_crash_recovery
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_mock_message(text_content="", has_tool_use=False):
    """Create a mock BetaToolRunner message with content blocks."""
    blocks = []
    if text_content:
        text_block = MagicMock(spec=[])
        text_block.type = "text"
        text_block.text = text_content
        text_block.thinking = ""
        blocks.append(text_block)
    if has_tool_use:
        tool_block = MagicMock(spec=[])
        tool_block.type = "tool_use"
        tool_block.id = "tu_123"
        tool_block.name = "shell_exec"
        tool_block.input = {"command": "echo test"}
        blocks.append(tool_block)

    msg = MagicMock()
    msg.content = blocks
    msg.usage = MagicMock()
    msg.usage.input_tokens = 1000
    msg.usage.output_tokens = 100
    msg.usage.cache_read_input_tokens = 0
    msg.usage.cache_creation_input_tokens = 0
    return msg


def _make_crashing_runner(messages_before_crash):
    """Create a mock runner that yields N messages then raises TypeError."""

    class CrashingRunner:
        def __init__(self):
            self._params = {"messages": []}

        def __iter__(self):
            for msg in messages_before_crash:
                yield msg
            # Simulate SDK crash: response.content is None
            raise TypeError("'NoneType' object is not iterable")

    return CrashingRunner()


def test_crash_recovery_with_accumulated_text():
    """When runner crashes after yielding messages with text, text is recovered."""
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    with patch.dict(
        "os.environ", {"OPENROUTER_API_KEY": "test", "AGENT_USE_OPENROUTER": "1"}
    ):
        # Patch out the actual SDK client creation
        with patch(
            "orchestrator.agent_adapters.anthropic_adapter.ANTHROPIC_SDK_AVAILABLE",
            True,
        ):
            with patch(
                "orchestrator.agent_adapters.anthropic_adapter.anthropic"
            ) as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.Anthropic.return_value = mock_client

                adapter = ClaudeAgentAdapter.__new__(ClaudeAgentAdapter)
                adapter.agent_name = "test"
                adapter._token_records = []
                adapter._task_context = ""
                adapter._cancel_event = None
                adapter.system_prompt = "You are a tutor."
                adapter.model = "test-model"
                adapter.max_agent_turns = 10
                adapter._input_history = []
                adapter._thinking_trace = []
                adapter._turn_index = 0
                adapter._turn_content_blocks = {}
                adapter._current_turn_blocks = []
                adapter._captured_tool_results = {}
                adapter._client = mock_client
                adapter._betas = []

                # Create runner that yields 2 good messages then crashes
                msg1 = _make_mock_message(
                    "Here is the explanation of the bug.", has_tool_use=True
                )
                msg2 = _make_mock_message(
                    "The fix is to change rolling(19) to rolling(20)."
                )
                runner = _make_crashing_runner([msg1, msg2])

                # Mock the tool_runner to return our crashing runner
                mock_client.beta.messages.tool_runner.return_value = runner

                # Call generate_response
                messages = [{"role": "user", "content": "Help me debug my code"}]
                result = adapter.generate_response(messages, [], None)

                # Verify: should contain accumulated text, NOT error message
                assert (
                    "[Anthropic API error" not in result
                ), f"Should have recovered text, got error: {result}"
                assert (
                    "explanation of the bug" in result
                ), f"Should contain msg1 text, got: {result}"
                assert (
                    "rolling(19) to rolling(20)" in result
                ), f"Should contain msg2 text, got: {result}"

                print(f"✓ Crash recovery works: recovered {len(result)} chars")
                print(f"  Content: {result[:100]}...")


def test_crash_on_first_iteration_returns_error():
    """When runner crashes before any text, error message is returned."""
    from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test"}):
        with patch(
            "orchestrator.agent_adapters.anthropic_adapter.ANTHROPIC_SDK_AVAILABLE",
            True,
        ):
            with patch(
                "orchestrator.agent_adapters.anthropic_adapter.anthropic"
            ) as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.Anthropic.return_value = mock_client

                adapter = ClaudeAgentAdapter.__new__(ClaudeAgentAdapter)
                adapter.agent_name = "test"
                adapter._token_records = []
                adapter._task_context = ""
                adapter._cancel_event = None
                adapter.system_prompt = "You are a tutor."
                adapter.model = "test-model"
                adapter.max_agent_turns = 10
                adapter._input_history = []
                adapter._thinking_trace = []
                adapter._turn_index = 0
                adapter._turn_content_blocks = {}
                adapter._current_turn_blocks = []
                adapter._captured_tool_results = {}
                adapter._client = mock_client
                adapter._betas = []

                # Crash immediately — no messages yielded
                runner = _make_crashing_runner([])
                mock_client.beta.messages.tool_runner.return_value = runner

                messages = [{"role": "user", "content": "Help"}]
                result = adapter.generate_response(messages, [], None)

                assert (
                    "[Anthropic API error" in result
                ), f"Should return error when no text accumulated, got: {result}"

                print("✓ First-iteration crash correctly returns error message")


if __name__ == "__main__":
    test_crash_recovery_with_accumulated_text()
    test_crash_on_first_iteration_returns_error()
    print("\nAll crash recovery tests passed.")
