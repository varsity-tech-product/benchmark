"""Evaluation script for E03: Strategy Validation (D + S + B).

Checks: pipeline planning, exploratory analysis, signal formalization,
signal evaluation, train/test split, IS/OOS metrics separation,
robustness discussion, visualization.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _strategy_research_check import (
    collect_artifact_text,
    conversation_text,
    has_any,
    has_metric_numbers,
    has_signal_definition,
    has_metric_evidence,
)
from _data_source_check import verify_data_source


def _check_pipeline_structure(conversation: list | None) -> bool:
    """Detect whether the assistant communicated a structured plan."""
    text = conversation_text(conversation, role="assistant")
    plan_patterns = [
        r"step\s*[123]",
        r"phase\s*[123]",
        r"first.*then.*finally",
        r"pipeline.*:.*\n.*\d\.",
        r"1\.\s.*\n.*2\.\s.*\n.*3\.\s",
    ]
    return any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in plan_patterns)


def _check_train_test_split(artifact_text: str) -> bool:
    """Detect train/test or IS/OOS date-split patterns in code artifacts."""
    split_patterns = [
        r"train.*test",
        r"in.sample.*out.of.sample",
        r"is_data.*oos_data",
        r"train_df.*test_df",
        r"split_date",
        r"train_end",
        r"oos_start",
        r"\[\s*:.*split\s*\]",  # df[:split]
        r"\.loc\[.*<.*date",
        r"\.loc\[.*>.*date",
    ]
    return any(re.search(p, artifact_text, re.IGNORECASE) for p in split_patterns)


def _check_oos_metrics_separated(artifact_text: str, assistant_text: str) -> bool:
    """Check if both IS and OOS metrics are reported separately."""
    combined = artifact_text + "\n" + assistant_text
    has_is = has_any(combined, ["in-sample", "in_sample", "is_sharpe", "train sharpe", "training period"])
    has_oos = has_any(combined, ["out-of-sample", "out_of_sample", "oos_sharpe", "test sharpe", "validation period", "oos period"])
    return has_is and has_oos


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate E03: Strategy Validation."""
    results = {
        "pipeline_structure_present": False,
        "exploratory_analysis_performed": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "train_test_split_implemented": False,
        "is_oos_metrics_separated": False,
        "robustness_discussed": False,
        "visualization_produced": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")

    # ── 1. Pipeline structure (0.05) ──
    results["pipeline_structure_present"] = _check_pipeline_structure(conversation)

    # ── 2. Exploratory analysis (0.10) ──
    results["exploratory_analysis_performed"] = has_any(
        artifact_text,
        ["describe", "autocorr", "distribution", "hist", "value_counts", "skew", "kurtosis", "rolling_std"],
    )

    # ── 3. Signal formalized (0.15) ──
    has_def = has_signal_definition(artifact_text)
    has_momentum = has_any(artifact_text, ["momentum", "pct_change", "rocp", "roc(", "trailing_return", "lookback"])
    results["signal_formalized"] = has_def or has_momentum

    # ── 4. Signal evaluated (0.15) ──
    results["signal_evaluated"] = has_metric_evidence(artifact_text) or has_metric_numbers(
        artifact_text,
        [["ic", "information_coefficient", "spearman"], ["quantile", "hit_rate", "win_rate"]],
    )

    # ── 5. Train/test split (0.20) ──
    results["train_test_split_implemented"] = _check_train_test_split(artifact_text)

    # ── 6. IS/OOS metrics separated (0.20) ──
    results["is_oos_metrics_separated"] = _check_oos_metrics_separated(artifact_text, assistant_text)

    # ── 7. Robustness discussed (0.10) ──
    results["robustness_discussed"] = has_any(
        assistant_text,
        ["overfitting", "regime", "degradation", "parameter sensitivity", "robustness", "out-of-sample gap"],
    )

    # ── 8. Visualization (0.05) ──
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith((".png", ".jpg", ".svg", ".pdf")):
                results["visualization_produced"] = True
                break

    # ── Scoring ──
    _checklist = [
        {"item": "pipeline_structure_present",     "weight": 0.05, "passed": results["pipeline_structure_present"]},
        {"item": "exploratory_analysis_performed",  "weight": 0.10, "passed": results["exploratory_analysis_performed"]},
        {"item": "signal_formalized",               "weight": 0.15, "passed": results["signal_formalized"]},
        {"item": "signal_evaluated",                "weight": 0.15, "passed": results["signal_evaluated"]},
        {"item": "train_test_split_implemented",    "weight": 0.20, "passed": results["train_test_split_implemented"]},
        {"item": "is_oos_metrics_separated",        "weight": 0.20, "passed": results["is_oos_metrics_separated"]},
        {"item": "robustness_discussed",            "weight": 0.10, "passed": results["robustness_discussed"]},
        {"item": "visualization_produced",          "weight": 0.05, "passed": results["visualization_produced"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # ── Gates ──
    if not results["train_test_split_implemented"]:
        score = min(score, 0.25)
    if not results["signal_formalized"]:
        score = min(score, 0.30)
    if not results["is_oos_metrics_separated"]:
        score = min(score, 0.40)

    # ── Data source verification ──
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
