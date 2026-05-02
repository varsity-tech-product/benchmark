"""Evaluation script for B01: Interpret basic backtest metrics (requires_code=true).

QR checks (7 items, equal weight):
1. code_artifact_present  — Python metrics script exists in workspace or tool logs
2. backtest_executed      — script was executed with metric output evidence
3. metrics_artifact_saved — JSON/CSV/TXT/MD metrics file saved
4. sharpe_present         — Sharpe ratio with numeric value
5. return_present         — total/cumulative return with numeric value
6. drawdown_present       — max drawdown with numeric value
7. interpretation_present — tutor explained metric meaning or assessed strategy

Hard caps:
- no metrics at all              → score <= 0.20
- no code_artifact_present       → score <= 0.45
- no backtest_executed           → score <= 0.60
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import python_source_records
from common.evidence_helpers import apply_data_source_cap
from common.shared_utils import (
    collect_artifact_text,
    conversation_text,
    has_any,
    workspace_files,
)

# Equal weight for all 7 items; stored as fraction so sum == 1.0 exactly.
_ITEM_WEIGHT = 1.0 / 7


def _tool_log_code_text(tool_logs: list) -> str:
    """Extract code/commands from tool logs (shell heredoc, inline, file_write)."""
    parts = []
    for log in tool_logs or []:
        name = log.name or ""
        if name == "shell_exec":
            parts.append(str(log.args.get("command", "")).lower())
            parts.append(str(log.result or "").lower())
        elif name == "file_write":
            parts.append(str(log.args.get("content", "")).lower())
    return "\n".join(parts)


def _check_code_artifact(workspace_path: str, tool_logs: list, tool_code: str) -> bool:
    """Detect Python metrics-analysis code.

    Uses python_source_records() so that data docs (risk_metrics.md,
    backtesting_101.md) in artifact_text cannot trigger a false positive.
    """
    _metrics_kws = [
        "sharpe",
        "drawdown",
        "total_return",
        "cumulative_return",
        "max_drawdown",
    ]
    _name_kws = ["metric", "backtest", "analysis", "performance", "b01"]

    # 1. Workspace .py files — match on filename OR code content (>=2 metric kws)
    py_records = python_source_records(workspace_path)
    for rec in py_records:
        if any(kw in rec["name"] for kw in _name_kws):
            return True
        if sum(1 for kw in _metrics_kws if kw in rec["code_lower"]) >= 2:
            return True

    # 2. file_write or shell heredoc — Python code containing metric keywords
    _py_indicators = ["import pandas", "import numpy", "def ", "pd.read_csv", ".py"]
    if any(kw in tool_code for kw in _metrics_kws) and any(
        kw in tool_code for kw in _py_indicators
    ):
        return True

    return False


def _check_metrics_artifact(workspace_path: str, tool_logs: list) -> bool:
    """Detect a saved metrics artifact (JSON/CSV/TXT/MD, not a .py file)."""
    _name_kws = ["metric", "summary", "performance", "backtest", "result"]
    _content_kws = ["sharpe", "drawdown", "return"]
    _suffixes = (".json", ".csv", ".txt", ".md")

    for fpath in workspace_files(workspace_path):
        base = os.path.basename(fpath).lower()
        if not any(base.endswith(s) for s in _suffixes):
            continue
        if base.endswith(".py"):
            continue
        name_match = any(kw in base for kw in _name_kws)
        try:
            with open(fpath) as fh:
                content = fh.read(4000).lower()
            content_match = sum(1 for kw in _content_kws if kw in content) >= 2
        except (IOError, UnicodeDecodeError):
            content_match = False
        if name_match or content_match:
            return True

    for log in tool_logs or []:
        if (log.name or "") == "file_write":
            fname = str(log.args.get("path", "")).lower()
            content = str(log.args.get("content", "")).lower()
            if (
                any(fname.endswith(s) for s in _suffixes)
                and not fname.endswith(".py")
                and sum(1 for kw in _content_kws if kw in content) >= 2
            ):
                return True

    return False


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether backtest metrics are computed via code and explained."""
    results = {
        "code_artifact_present": False,
        "backtest_executed": False,
        "metrics_artifact_saved": False,
        "sharpe_present": False,
        "return_present": False,
        "drawdown_present": False,
        "interpretation_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")
    tool_code = _tool_log_code_text(tool_logs)

    # --- code_artifact_present ---
    results["code_artifact_present"] = _check_code_artifact(
        workspace_path, tool_logs, tool_code
    )

    # --- backtest_executed ---
    if tool_logs:
        for log in tool_logs:
            name = log.name or ""
            if name in ("run_backtest", "analyze_backtest_results"):
                results["backtest_executed"] = True
                break
            if name == "shell_exec":
                cmd = str(log.args.get("command", "")).lower()
                result_text = str(log.result or "").lower()
                # Command contains explicit metric/backtest keyword
                if any(
                    kw in cmd for kw in ["backtest", "sharpe", "drawdown", "return"]
                ):
                    results["backtest_executed"] = True
                    break
                # Running any .py file whose output contains metric values
                if re.search(r"python\s+\S+\.py", cmd) and re.search(
                    r"sharpe|drawdown|total.return|cumulative.return", result_text
                ):
                    results["backtest_executed"] = True
                    break
                # Metric values visible in shell result
                if re.search(r"sharpe.*-?\d+\.?\d*|return.*-?\d+\.?\d*", result_text):
                    results["backtest_executed"] = True
                    break

    # --- metrics_artifact_saved ---
    results["metrics_artifact_saved"] = _check_metrics_artifact(
        workspace_path, tool_logs
    )

    # --- metric value checks (keyword + nearby number) ---
    def _has_metric(text: str, keywords: list) -> bool:
        for kw in keywords:
            if re.search(
                rf"{re.escape(kw)}[^0-9\-]{{0,60}}-?\d+\.?\d*", text, re.IGNORECASE
            ):
                return True
            if re.search(
                rf"-?\d+\.?\d*[^0-9]{{0,60}}{re.escape(kw)}", text, re.IGNORECASE
            ):
                return True
        return False

    combined = artifact_text + "\n" + assistant_text

    results["sharpe_present"] = _has_metric(
        combined, ["sharpe", "sharpe_ratio", "sharpe ratio"]
    )
    results["return_present"] = _has_metric(
        combined,
        [
            "total return",
            "annual return",
            "annualized return",
            "total_return",
            "annual_return",
            "net profit",
            "cumulative return",
            "cagr",
        ],
    )
    results["drawdown_present"] = _has_metric(
        combined, ["drawdown", "max_drawdown", "max drawdown", "maximum drawdown"]
    )

    # --- interpretation_present ---
    results["interpretation_present"] = has_any(
        assistant_text,
        [
            "good",
            "bad",
            "strong",
            "weak",
            "promising",
            "problematic",
            "overfitting",
            "overfit",
            "risk-adjusted",
            "risk adjusted",
            "benchmark",
            "rule of thumb",
            "above 1",
            "above 2",
            "greater than",
            "means that",
            "indicates",
            "suggests",
            "tells us",
            "interpret",
            "measures",
            "represents",
        ],
    )

    # --- scoring ---
    _checklist = [
        {
            "item": "code_artifact_present",
            "weight": _ITEM_WEIGHT,
            "passed": results["code_artifact_present"],
        },
        {
            "item": "backtest_executed",
            "weight": _ITEM_WEIGHT,
            "passed": results["backtest_executed"],
        },
        {
            "item": "metrics_artifact_saved",
            "weight": _ITEM_WEIGHT,
            "passed": results["metrics_artifact_saved"],
        },
        {
            "item": "sharpe_present",
            "weight": _ITEM_WEIGHT,
            "passed": results["sharpe_present"],
        },
        {
            "item": "return_present",
            "weight": _ITEM_WEIGHT,
            "passed": results["return_present"],
        },
        {
            "item": "drawdown_present",
            "weight": _ITEM_WEIGHT,
            "passed": results["drawdown_present"],
        },
        {
            "item": "interpretation_present",
            "weight": _ITEM_WEIGHT,
            "passed": results["interpretation_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Hard cap: no metric values at all
    metrics_found = sum(
        1
        for k in ("sharpe_present", "return_present", "drawdown_present")
        if results[k]
    )
    if metrics_found == 0:
        score = min(score, 0.20)

    # Hard cap: no Python code artifact (core requirement of a code task)
    if not results["code_artifact_present"]:
        score = min(score, 0.45)

    # Hard cap: no execution evidence
    if not results["backtest_executed"]:
        score = min(score, 0.60)

    # Data source verification
    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 4)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
