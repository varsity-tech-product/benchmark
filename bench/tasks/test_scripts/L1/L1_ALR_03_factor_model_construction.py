"""Eval script for L1_ALR_03_factor_model_construction.

Delegates file-presence + structured-spec checks to
``eval.programmatic.l1_verifier``. The QR track passes the task's
``ground_truth.expected_outputs`` via the ``expected_outputs`` kwarg.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.dirname(_HERE),                                    # bench/tasks/test_scripts/
    os.path.abspath(os.path.join(_HERE, "..", "..", "..")),    # bench/
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.evidence_helpers import apply_data_source_cap  # noqa: E402
from eval.programmatic.l1_verifier import verify_l1  # noqa: E402


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
    expected_outputs: list = None,
) -> dict:
    spec = expected_outputs or []
    structured = [s for s in spec if isinstance(s, dict)]
    detail = (
        verify_l1(workspace_path, structured)
        if structured
        else {
            "score": 0.0,
            "n_total": 0,
            "n_passed": 0,
            "per_spec": [],
            "note": "expected_outputs has no structured spec yet",
        }
    )
    results: dict = {"score": round(detail.get("score", 0.0), 4), "_verifier": detail}

    if data_files:
        results["score"] = round(
            apply_data_source_cap(results["score"], results, tool_logs, data_files),
            4,
        )

    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
