"""Evaluation script for X06: Diagnose overfitting in a 12-parameter strategy.

This is a UNIQUE eval — no single-line fix exists. Uses
check_conversation_concepts instead of check_fix_applied.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _debug_check import (
    check_conversation_concepts,
    check_execution_output,
    check_root_cause_explained,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether overfitting was correctly diagnosed.

    This task has no single-line fix — evaluation is based on conceptual
    understanding demonstrated in conversation and tool usage.

    Checks:
      - train_test_executed (0.15): Tool logs show execution with train/test
        Sharpe observed.
      - sharpe_decay_noted (0.15): Conversation notes Sharpe difference
        between periods.
      - overfitting_identified (0.25): Overfitting explicitly named as root
        cause.
      - excessive_params_noted (0.20): Mentions 12 params or excessive/too
        many parameters.
      - remedy_proposed (0.15): Concrete remedy suggested (reduce parameters,
        cross-validation, walk-forward, etc.).
      - root_cause_explained (0.10): Deeper explanation (fitting noise,
        degrees of freedom, bias-variance, etc.).
    """
    results = {
        "train_test_executed": False,
        "sharpe_decay_noted": False,
        "overfitting_identified": False,
        "excessive_params_noted": False,
        "remedy_proposed": False,
        "root_cause_explained": False,
        "score": 0.0,
    }

    # --- train_test_executed (0.15) ---
    exec_info = check_execution_output(
        tool_logs=tool_logs or [],
        success_keywords=["sharpe", "train", "test", "2020", "2022", "2023", "2024"],
    )
    # Also check conversation for evidence of execution
    train_test_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=["train", "test", "sharpe"],
        tool_logs=tool_logs or [],
    )
    results["train_test_executed"] = (
        exec_info["output_valid"] or train_test_concepts["fraction"] >= 0.67
    )

    # --- sharpe_decay_noted (0.15) ---
    decay_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=[
            "decay",
            "drop",
            "decrease",
            "degrade",
            "worse",
            "lower",
        ],
        tool_logs=tool_logs or [],
    )
    results["sharpe_decay_noted"] = decay_concepts["fraction"] >= 1 / 6

    # --- overfitting_identified (0.25) ---
    overfit_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=[
            "overfit",
            "curve-fit",
            "data snooping",
            "over-fit",
            "overfitting",
            "curve fitting",
        ],
        tool_logs=tool_logs or [],
    )
    results["overfitting_identified"] = overfit_concepts["fraction"] >= 1 / 6

    # --- excessive_params_noted (0.20) ---
    params_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=[
            "12",
            "twelve",
            "too many",
            "excessive",
            "over-parameterized",
            "degrees of freedom",
        ],
        tool_logs=tool_logs or [],
    )
    results["excessive_params_noted"] = params_concepts["fraction"] >= 1 / 6

    # --- remedy_proposed (0.15) ---
    remedy_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=[
            "reduce parameters",
            "cross-validation",
            "walk-forward",
            "regularization",
            "fewer parameters",
            "simpler model",
            "out-of-sample",
        ],
        tool_logs=tool_logs or [],
    )
    results["remedy_proposed"] = remedy_concepts["fraction"] >= 1 / 7

    # --- root_cause_explained (0.10) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation=conversation or [],
        tool_logs=tool_logs or [],
        root_cause_keywords=[
            "fitting noise",
            "degrees of freedom",
            "bias-variance",
            "memorizing",
            "noise",
        ],
    )

    # --- Compute weighted score ---
    _checklist = [
        {
            "item": "train_test_executed",
            "weight": 0.15,
            "passed": results["train_test_executed"],
        },
        {
            "item": "sharpe_decay_noted",
            "weight": 0.15,
            "passed": results["sharpe_decay_noted"],
        },
        {
            "item": "overfitting_identified",
            "weight": 0.25,
            "passed": results["overfitting_identified"],
        },
        {
            "item": "excessive_params_noted",
            "weight": 0.20,
            "passed": results["excessive_params_noted"],
        },
        {
            "item": "remedy_proposed",
            "weight": 0.15,
            "passed": results["remedy_proposed"],
        },
        {
            "item": "root_cause_explained",
            "weight": 0.10,
            "passed": results["root_cause_explained"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
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
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
