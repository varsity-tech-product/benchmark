"""Test C# LEAN code eval and debug judge guideline.

Run:  python -m tests.test_lean_code_eval
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_log(name, result_text, args=None):
    log = MagicMock()
    log.name = name
    log.result = result_text
    log.args = args or {}
    log.success = "error" not in result_text.lower()
    return log


# ================================================================
# Test 1: LEAN code eval with successful trial
# ================================================================


def test_lean_code_eval_success():
    from evaluation.code_eval import evaluate_code_combined

    logs = [
        _make_log(
            "run_lean_backtest",
            "=== Trial 1 Result ===\nStatus: compile_error\nRemaining trials: 4/5",
        ),
        _make_log(
            "run_lean_backtest",
            "=== Trial 2 Result ===\nStatus: success\nTrades: 1484\nSharpe: -0.22\nRemaining trials: 3/5",
        ),
    ]

    result = evaluate_code_combined(
        workspace_path="/tmp/fake_workspace",
        tool_logs=logs,
        reference=None,
        task_requires_code=True,
        is_lean_task=True,
    )

    assert result["applicable"] is True, f"Should be applicable, got {result}"
    assert result["static_analysis"]["syntax_valid"] is True, "Last trial compiled"
    assert result["static_analysis"]["compile_errors"] == 1, "One compile error"
    assert result["execution"]["last_status"] == "success"
    # Layer C is skipped when reference is unavailable; remaining layers are
    # re-normalized instead of injecting a fake zero.
    assert result["score"] == 1.0, f"Expected 1.0, got {result['score']}"

    print(
        f"✓ LEAN success: score={result['score']} (A={result['static_analysis']['score']}, B={result['execution']['score']})"
    )


# ================================================================
# Test 2: LEAN code eval with all compile errors
# ================================================================


def test_lean_code_eval_all_compile_errors():
    from evaluation.code_eval import evaluate_code_combined

    logs = [
        _make_log("run_lean_backtest", "=== Trial 1 Result ===\nStatus: compile_error"),
        _make_log("run_lean_backtest", "=== Trial 2 Result ===\nStatus: compile_error"),
    ]

    result = evaluate_code_combined(
        workspace_path="/tmp/fake_workspace",
        tool_logs=logs,
        reference=None,
        task_requires_code=True,
        is_lean_task=True,
    )

    assert result["applicable"] is True
    assert result["static_analysis"]["syntax_valid"] is False
    assert (
        result["score"] == 0.0
    ), f"All compile errors should score 0, got {result['score']}"

    print(f"✓ LEAN all compile errors: score={result['score']}")


# ================================================================
# Test 3: LEAN code eval with empty trades
# ================================================================


def test_lean_code_eval_empty_trades():
    from evaluation.code_eval import evaluate_code_combined

    logs = [
        _make_log(
            "run_lean_backtest",
            "=== Trial 1 Result ===\nStatus: empty_trades\nTrades: 0",
        ),
    ]

    result = evaluate_code_combined(
        workspace_path="/tmp/fake_workspace",
        tool_logs=logs,
        reference=None,
        task_requires_code=True,
        is_lean_task=True,
    )

    assert result["applicable"] is True
    # Layer C is skipped when reference is unavailable; Layer A/B are
    # re-normalized: (0.15*1.0 + 0.35*0.5) / 0.50 = 0.65.
    assert result["score"] == 0.65, f"Expected 0.65, got {result['score']}"

    print(f"✓ LEAN empty trades: score={result['score']}")


# ================================================================
# Test 4: No run_lean_backtest calls → not applicable
# ================================================================


def test_lean_code_eval_no_trials():
    from evaluation.code_eval import evaluate_code_combined

    logs = [
        _make_log("shell_exec", "echo hello"),
        _make_log("file_write", "written", args={"path": "test.cs"}),
    ]

    result = evaluate_code_combined(
        workspace_path="/tmp/fake_workspace",
        tool_logs=logs,
        reference=None,
        task_requires_code=True,
        is_lean_task=True,
    )

    assert result["applicable"] is False, "No trials should be not applicable"

    print(f"✓ LEAN no trials: applicable={result['applicable']}")


# ================================================================
# Test 5: Python task unchanged (regression)
# ================================================================


def test_python_task_unchanged():
    from evaluation.code_eval import evaluate_code_combined

    logs = []  # No tool logs, no code

    result = evaluate_code_combined(
        workspace_path="/tmp/fake_workspace",
        tool_logs=logs,
        reference=None,
        task_requires_code=False,
        is_lean_task=False,
    )

    assert (
        result["applicable"] is False
    ), "No code + requires_code=False → not applicable"

    print(f"✓ Python regression: applicable={result['applicable']}")


# ================================================================
# Test 6: Debug judge guideline
# ================================================================


def test_debug_judge_guideline():
    from evaluation.deepeval_metrics.result_judge import _build_result_judge_prompt

    prompt_debug = _build_result_judge_prompt(
        task_description="Fix alpha conflict bug",
        category="debug",
        agent_key_outputs="Trades: 1484",
        agent_workspace_files=["Algorithm.cs"],
        agent_summary="Fixed the PCM.",
        reference=None,
    )

    prompt_strategy = _build_result_judge_prompt(
        task_description="Research MA crossover",
        category="strategy",
        agent_key_outputs="Sharpe: 1.2",
        agent_workspace_files=["results.csv"],
        agent_summary="Computed metrics.",
        reference=None,
    )

    assert "DEBUG TASKS" in prompt_debug, "Debug guideline should be in debug prompt"
    assert "bug was resolved, not whether the strategy is profitable" in prompt_debug
    assert (
        "DEBUG TASKS" not in prompt_strategy
    ), "Debug guideline should NOT be in strategy prompt"

    print("✓ Debug judge guideline injected for debug category only")


if __name__ == "__main__":
    test_lean_code_eval_success()
    test_lean_code_eval_all_compile_errors()
    test_lean_code_eval_empty_trades()
    test_lean_code_eval_no_trials()
    test_python_task_unchanged()
    test_debug_judge_guideline()
    print("\nAll tests passed.")
