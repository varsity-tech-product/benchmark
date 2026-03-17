"""Evaluation script for B06: Walk-forward validation framework."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import (
    collect_artifact_text,
    conversation_text,
    has_any,
    has_metric_numbers,
    has_regex,
    python_code_text,
    python_source_records,
    tool_called,
)
from common.data_source_check import verify_data_source


def _tool_log_code_text(tool_logs: list) -> str:
    """Extract Python code from tool logs (heredoc, inline, file_write)."""
    parts = []
    for log in tool_logs or []:
        name = log.name or ""
        if name == "shell_exec":
            cmd = str(log.args.get("command", ""))
            parts.append(cmd.lower())
            result = str(log.result or "")
            parts.append(result.lower())
        elif name == "file_write":
            content = str(log.args.get("content", ""))
            parts.append(content.lower())
    return "\n".join(parts)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "rolling_windows_present": False,
        "parameter_search_present": False,
        "best_params_recorded": False,
        "is_oos_separated": False,
        "aggregate_oos_metrics_present": False,
        "overfitting_gap_discussed": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")
    py_records = python_source_records(workspace_path)
    code_text = python_code_text(py_records)

    # Also extract code from tool logs (for heredoc/inline Python)
    tool_code = _tool_log_code_text(tool_logs)
    all_code = code_text + "\n" + tool_code

    results["rolling_windows_present"] = (
        has_any(
            all_code,
            [
                "walk_forward",
                "walkforward",
                "rolling window",
                "train_start",
                "test_start",
                "window_results",
                "windows",
            ],
        )
        and has_regex(
            all_code,
            [
                r"for\s+\w+\s+in\s+\w*windows",
                r"for\s+\w+\s+in\s+range\(",
                r"train_.*test_",
            ],
        )
    ) or (
        # Agent used split_walkforward_windows tool and produced fold artifacts
        tool_called(tool_logs, "split_walkforward_windows")
        and has_any(artifact_text, ["fold", "train", "test"])
    )

    results["parameter_search_present"] = has_any(
        all_code,
        [
            "param_grid",
            "best_params",
            "itertools.product",
            "fast_windows",
            "slow_windows",
            "parameter_grid",
        ],
    ) or has_regex(
        all_code,
        [
            r"for\s+\w+\s+in\s+\w*fast",
            r"for\s+\w+\s+in\s+\w*slow",
            r"for\s+\w+,\s*\w+\s+in\s+.*product",
        ],
    )

    results["best_params_recorded"] = has_any(
        artifact_text,
        [
            "best_params",
            "best params",
            "optimal_params",
            "optimal params",
            "selected_params",
        ],
    )

    results["is_oos_separated"] = has_any(
        artifact_text,
        [
            "in_sample",
            "out_of_sample",
            "out-of-sample",
            "oos",
            "train_metrics",
            "test_metrics",
        ],
    ) and has_metric_numbers(
        artifact_text,
        [
            ["in_sample", "train", "is"],
            ["out_of_sample", "out-of-sample", "oos", "test"],
        ],
    )

    results["aggregate_oos_metrics_present"] = has_any(
        artifact_text,
        ["aggregated", "aggregate", "combined oos", "oos summary", "out_of_sample"],
    ) and has_metric_numbers(
        artifact_text,
        [
            ["oos", "out_of_sample", "out-of-sample"],
            ["sharpe", "total return", "annualized return", "max drawdown"],
        ],
    )

    results["overfitting_gap_discussed"] = has_any(
        assistant_text,
        ["overfit", "overfitting", "data snooping", "selection bias"],
    ) or (
        has_any(assistant_text, ["in-sample", "in sample", "train", "training window"])
        and has_any(assistant_text, ["out-of-sample", "out of sample", "oos", "test"])
        and has_any(
            assistant_text,
            [
                "degrade",
                "decay",
                "drop",
                "worse",
                "generalize",
                "generalization",
                "shrink",
            ],
        )
    )

    _checklist = [
        {
            "item": "rolling_windows_present",
            "weight": 0.20,
            "passed": results["rolling_windows_present"],
        },
        {
            "item": "parameter_search_present",
            "weight": 0.20,
            "passed": results["parameter_search_present"],
        },
        {
            "item": "best_params_recorded",
            "weight": 0.15,
            "passed": results["best_params_recorded"],
        },
        {
            "item": "is_oos_separated",
            "weight": 0.20,
            "passed": results["is_oos_separated"],
        },
        {
            "item": "aggregate_oos_metrics_present",
            "weight": 0.15,
            "passed": results["aggregate_oos_metrics_present"],
        },
        {
            "item": "overfitting_gap_discussed",
            "weight": 0.10,
            "passed": results["overfitting_gap_discussed"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not results["rolling_windows_present"] or not results["is_oos_separated"]:
        score = min(score, 0.25)
    if (
        not results["parameter_search_present"]
        or not results["aggregate_oos_metrics_present"]
    ):
        score = min(score, 0.50)

    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
