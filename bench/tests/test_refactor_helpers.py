"""Unit tests for refactored shared helpers.

Validates that the extracted utility functions in base_adapter and
evidence_helpers produce identical results to the original inline code.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# 1. base_adapter: normalize_tool_params
# ---------------------------------------------------------------------------


def test_normalize_tool_params_basic():
    """Standard MCP parameters are converted to JSON Schema properties."""
    from orchestrator.agent_adapters.base_adapter import normalize_tool_params

    params = {
        "command": {
            "type": "string",
            "description": "Shell command to run",
            "required": True,
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds",
        },
    }
    properties, required = normalize_tool_params(params)
    assert properties["command"]["type"] == "string"
    assert properties["command"]["description"] == "Shell command to run"
    assert properties["timeout"]["type"] == "integer"
    assert required == ["command"]
    assert "timeout" not in required
    print("  PASS: test_normalize_tool_params_basic")


def test_normalize_tool_params_items():
    """Array parameters with 'items' are preserved."""
    from orchestrator.agent_adapters.base_adapter import normalize_tool_params

    params = {
        "symbols": {
            "type": "array",
            "description": "List of symbols",
            "items": {"type": "string"},
            "required": True,
        },
    }
    properties, required = normalize_tool_params(params)
    assert properties["symbols"]["items"] == {"type": "string"}
    assert "symbols" in required
    print("  PASS: test_normalize_tool_params_items")


def test_normalize_tool_params_string_fallback():
    """Non-dict parameter values fall back to string type."""
    from orchestrator.agent_adapters.base_adapter import normalize_tool_params

    params = {"name": "just a string"}
    properties, required = normalize_tool_params(params)
    assert properties["name"]["type"] == "string"
    assert required == []
    print("  PASS: test_normalize_tool_params_string_fallback")


def test_normalize_tool_params_empty():
    """Empty parameters dict returns empty properties and required."""
    from orchestrator.agent_adapters.base_adapter import normalize_tool_params

    properties, required = normalize_tool_params({})
    assert properties == {}
    assert required == []
    print("  PASS: test_normalize_tool_params_empty")


# ---------------------------------------------------------------------------
# 2. base_adapter: extract_latest_user_message
# ---------------------------------------------------------------------------


def test_extract_latest_user_message():
    from orchestrator.agent_adapters.base_adapter import extract_latest_user_message

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "What is OHLCV?"},
    ]
    assert extract_latest_user_message(messages) == "What is OHLCV?"
    print("  PASS: test_extract_latest_user_message")


def test_extract_latest_user_message_none():
    from orchestrator.agent_adapters.base_adapter import extract_latest_user_message

    messages = [{"role": "assistant", "content": "Hi"}]
    assert extract_latest_user_message(messages) is None
    assert extract_latest_user_message([]) is None
    print("  PASS: test_extract_latest_user_message_none")


# ---------------------------------------------------------------------------
# 3. base_adapter: record_token_usage
# ---------------------------------------------------------------------------


def test_record_token_usage():
    from orchestrator.agent_adapters.base_adapter import record_token_usage

    records = []

    class FakeUsage:
        prompt_tokens = 100
        completion_tokens = 50

    record_token_usage(
        records,
        "test-model",
        FakeUsage(),
        cost_fn=lambda m, i, o: i * 0.01 + o * 0.03,
    )
    assert len(records) == 1
    assert records[0].input_tokens == 100
    assert records[0].output_tokens == 50
    assert records[0].cost_usd == 100 * 0.01 + 50 * 0.03
    print("  PASS: test_record_token_usage")


def test_record_token_usage_none():
    from orchestrator.agent_adapters.base_adapter import record_token_usage

    records = []
    record_token_usage(records, "test-model", None)
    assert len(records) == 0
    print("  PASS: test_record_token_usage_none")


def test_record_token_usage_custom_attrs():
    """Anthropic-style usage with input_tokens/output_tokens + cache."""
    from orchestrator.agent_adapters.base_adapter import record_token_usage

    records = []

    class AnthropicUsage:
        input_tokens = 80
        output_tokens = 40
        cache_read_input_tokens = 20

    record_token_usage(
        records,
        "claude",
        AnthropicUsage(),
        input_attr="input_tokens",
        output_attr="output_tokens",
        extra_input=20,  # cache
        cost_fn=lambda m, i, o: 0.0,
    )
    assert records[0].input_tokens == 100  # 80 + 20 extra
    assert records[0].output_tokens == 40
    print("  PASS: test_record_token_usage_custom_attrs")


# ---------------------------------------------------------------------------
# 4. evidence_helpers: collect_evidence, has_keywords, has_number
# ---------------------------------------------------------------------------


def test_collect_evidence_with_logs():
    """collect_evidence aggregates tool log data and workspace files."""
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "evaluation", "test_scripts")
    )
    from common.evidence_helpers import collect_evidence

    class FakeLog:
        def __init__(self, name, args, result):
            self.name = name
            self.args = args
            self.result = result

    logs = [
        FakeLog("shell_exec", {"command": "python analyze.py"}, "mean: 42.5"),
        FakeLog("fetch_market_data", {"symbol": "BTCUSDT"}, "rows: 1000"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a workspace file
        with open(os.path.join(tmpdir, "report.txt"), "w") as f:
            f.write("Summary: OHLCV analysis complete\nVolume spike detected")

        result = collect_evidence(tmpdir, logs)

        # Should be lowercased
        assert result == result.lower()
        # Should contain tool log data
        assert "shell_exec" in result
        assert "mean: 42.5" in result
        assert "fetch_market_data" in result
        # Should contain workspace file data
        assert "ohlcv analysis complete" in result
        assert "volume spike detected" in result

    print("  PASS: test_collect_evidence_with_logs")


def test_has_keywords():
    from common.evidence_helpers import has_keywords

    assert has_keywords("the mean is 42", ["mean", "std"]) is True
    assert has_keywords("the mean is 42", ["std", "var"]) is False
    assert has_keywords("", ["anything"]) is False
    print("  PASS: test_has_keywords")


def test_has_number():
    from common.evidence_helpers import has_number

    assert has_number("mean: 42.5") is True
    assert has_number("return: -0.03") is True
    assert has_number("no numbers here") is False
    print("  PASS: test_has_number")


# ---------------------------------------------------------------------------
# 5. evidence_helpers: checklist_score
# ---------------------------------------------------------------------------


def test_checklist_score():
    from common.evidence_helpers import checklist_score

    checklist = [
        {"item": "a", "weight": 0.4, "passed": True},
        {"item": "b", "weight": 0.3, "passed": False},
        {"item": "c", "weight": 0.3, "passed": True},
    ]
    score = checklist_score(checklist)
    assert abs(score - 0.7) < 1e-9
    print("  PASS: test_checklist_score")


def test_checklist_score_all_pass():
    from common.evidence_helpers import checklist_score

    checklist = [
        {"item": "a", "weight": 0.5, "passed": True},
        {"item": "b", "weight": 0.5, "passed": True},
    ]
    assert abs(checklist_score(checklist) - 1.0) < 1e-9
    print("  PASS: test_checklist_score_all_pass")


def test_checklist_score_none_pass():
    from common.evidence_helpers import checklist_score

    checklist = [
        {"item": "a", "weight": 0.5, "passed": False},
        {"item": "b", "weight": 0.5, "passed": False},
    ]
    assert checklist_score(checklist) == 0.0
    print("  PASS: test_checklist_score_none_pass")


# ---------------------------------------------------------------------------
# 6. DynamicTool.to_dict uses normalize_tool_params
# ---------------------------------------------------------------------------


def test_dynamic_tool_to_dict():
    """DynamicTool.to_dict uses shared normalize_tool_params correctly."""
    try:
        from orchestrator.agent_adapters.anthropic_adapter import DynamicTool
    except ImportError:
        print("  SKIP: test_dynamic_tool_to_dict (anthropic not installed)")
        return

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

    tool = DynamicTool(schema, lambda name, **kw: "result")
    d = tool.to_dict()

    assert d["name"] == "test_tool"
    assert d["description"] == "A test tool"
    assert d["input_schema"]["properties"]["command"]["type"] == "string"
    assert "command" in d["input_schema"]["required"]
    assert "timeout" not in d["input_schema"].get("required", [])
    print("  PASS: test_dynamic_tool_to_dict")


# ---------------------------------------------------------------------------
# 7. Adapter format_tools_openai uses normalize_tool_params
# ---------------------------------------------------------------------------


def test_generic_adapter_format_tools():
    """BaseAgentAdapter.format_tools_openai produces valid OpenAI schema."""
    try:
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter
    except Exception:
        print("  SKIP: test_generic_adapter_format_tools (deps not available)")
        return

    adapter = GenericLLMAdapter.__new__(GenericLLMAdapter)
    tools = [
        {
            "name": "shell_exec",
            "description": "Execute a shell command",
            "parameters": {
                "command": {
                    "type": "string",
                    "description": "The command",
                    "required": True,
                },
            },
        },
    ]
    formatted = adapter.format_tools_openai(tools)
    assert len(formatted) == 1
    assert formatted[0]["type"] == "function"
    func = formatted[0]["function"]
    assert func["name"] == "shell_exec"
    assert "command" in func["parameters"]["properties"]
    assert "command" in func["parameters"]["required"]
    print("  PASS: test_generic_adapter_format_tools")


# ---------------------------------------------------------------------------
# 8. D-series eval script end-to-end (smoke test)
# ---------------------------------------------------------------------------


def test_d02_eval_smoke():
    """D02 evaluation script runs end-to-end with mock data."""
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "evaluation", "test_scripts")
    )
    from data_analysis.D02_missing_data_detection_handling import evaluate

    class FakeLog:
        def __init__(self, name, args, result):
            self.name = name
            self.args = args
            self.result = result

    logs = [
        FakeLog(
            "shell_exec",
            {"command": "python check.py"},
            "isna().sum():\ncolumn1: 5\ncolumn2: 0\n"
            "diff() shows gaps at: 2024-01-05\n"
            "fillna(method='ffill') applied",
        ),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = evaluate(tmpdir, logs)
        assert "score" in result
        assert isinstance(result["score"], float)
        assert 0.0 <= result["score"] <= 1.0
        # Should detect all three criteria from the log output
        assert result["missing_values_profiled"] is True
        assert result["gap_analysis_performed"] is True
        assert result["handling_applied"] is True
        assert result["score"] == 1.0
    print("  PASS: test_d02_eval_smoke")


def test_d07_eval_smoke():
    """D07 evaluation script runs with mock data using shared has_keywords."""
    from data_analysis.D07_broken_data_feed_diagnosis import evaluate

    class FakeLog:
        def __init__(self, name, args, result):
            self.name = name
            self.args = args
            self.result = result

    logs = [
        FakeLog(
            "shell_exec",
            {"command": "python diagnose.py"},
            "Found 15 missing rows and 3 duplicate entries. "
            "Outlier spike detected at row 42.",
        ),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "report.json"), "w") as f:
            f.write('{"issues": ["missing", "duplicate"]}')

        result = evaluate(tmpdir, logs)
        assert result["anomalies_detected"] is True
        assert result["multiple_issue_types"] is True
        assert result["diagnostic_artifact"] is True
        assert result["score"] == 1.0
    print("  PASS: test_d07_eval_smoke")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== base_adapter: normalize_tool_params ===")
    test_normalize_tool_params_basic()
    test_normalize_tool_params_items()
    test_normalize_tool_params_string_fallback()
    test_normalize_tool_params_empty()

    print("\n=== base_adapter: extract_latest_user_message ===")
    test_extract_latest_user_message()
    test_extract_latest_user_message_none()

    print("\n=== base_adapter: record_token_usage ===")
    test_record_token_usage()
    test_record_token_usage_none()
    test_record_token_usage_custom_attrs()

    print("\n=== evidence_helpers: collect_evidence / has_keywords / has_number ===")
    test_collect_evidence_with_logs()
    test_has_keywords()
    test_has_number()

    print("\n=== evidence_helpers: checklist_score ===")
    test_checklist_score()
    test_checklist_score_all_pass()
    test_checklist_score_none_pass()

    print("\n=== DynamicTool.to_dict ===")
    test_dynamic_tool_to_dict()

    print("\n=== Adapter _format_tools ===")
    test_generic_adapter_format_tools()

    print("\n=== D-series eval smoke tests ===")
    test_d02_eval_smoke()
    test_d07_eval_smoke()

    print("\n✓ All tests passed!")
