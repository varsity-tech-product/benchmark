"""Generic L1 task verifier.

L1 tasks (single-shot agent execution) declare their expected artifacts under
``ground_truth.expected_outputs`` as a list of structured specs:

    {
        "path": "output/comparison.json",        # relative to workspace_path
        "type": "json" | "csv" | "markdown" | "python" | "image" | "any",
        "required_keys": [...],                   # for json
        "required_columns": [...],                # for csv
        "min_rows": int,                          # for csv
        "constraints": [                          # optional numeric constraints
            {"key": "fixed_total_return", "op": "<", "ref": "buggy_total_return"},
            {"key": "fixed_short_days",   "op": ">", "value": 0},
        ],
    }

The verifier produces a dict with per-spec pass/fail and an aggregate score.
"""

from __future__ import annotations

import csv
import json
import operator
from pathlib import Path
from typing import Any

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _read_csv_header(path: Path) -> tuple[list[str] | None, int]:
    """Return (header columns, row count) for a CSV. Returns (None, 0) on error."""
    try:
        with path.open() as f:
            reader = csv.reader(f)
            header = next(reader, None)
            row_count = sum(1 for _ in reader)
        return header, row_count
    except Exception:
        return None, 0


def _check_constraint(payload: dict, constraint: dict) -> tuple[bool, str]:
    """Evaluate a single numeric constraint against a JSON payload."""
    key = constraint.get("key")
    op_name = constraint.get("op")
    op_fn = _OPS.get(op_name)
    if not op_fn or key not in payload:
        return False, f"missing key '{key}' or unknown op '{op_name}'"
    lhs = payload[key]
    if "ref" in constraint:
        ref = constraint["ref"]
        if ref not in payload:
            return False, f"missing ref key '{ref}'"
        rhs = payload[ref]
    elif "value" in constraint:
        rhs = constraint["value"]
    else:
        return False, "constraint has neither 'ref' nor 'value'"
    try:
        ok = bool(op_fn(lhs, rhs))
        return ok, f"{key}={lhs} {op_name} {rhs}"
    except TypeError as e:
        return False, f"type error: {e}"


def _verify_one(workspace_path: Path, spec: dict) -> dict:
    """Verify a single expected_outputs spec."""
    rel_path = spec.get("path")
    full_path = workspace_path / rel_path
    out: dict[str, Any] = {"path": rel_path, "exists": full_path.exists()}

    if not out["exists"]:
        out["passed"] = False
        return out

    file_type = spec.get("type", "any")

    if file_type == "json":
        payload = _read_json(full_path)
        out["parsed"] = payload is not None
        if payload is None:
            out["passed"] = False
            return out
        required_keys = spec.get("required_keys", [])
        missing = [k for k in required_keys if k not in payload]
        out["missing_keys"] = missing
        constraints = spec.get("constraints", [])
        constraint_results = []
        for c in constraints:
            ok, detail = _check_constraint(payload, c)
            constraint_results.append({"constraint": c, "ok": ok, "detail": detail})
        out["constraints"] = constraint_results
        out["passed"] = (
            not missing and all(r["ok"] for r in constraint_results)
        )

    elif file_type == "csv":
        header, n_rows = _read_csv_header(full_path)
        out["columns"] = header
        out["row_count"] = n_rows
        if header is None:
            out["passed"] = False
            return out
        required_cols = spec.get("required_columns", [])
        missing_cols = [c for c in required_cols if c not in header]
        out["missing_columns"] = missing_cols
        min_rows = spec.get("min_rows", 0)
        out["passed"] = not missing_cols and n_rows >= min_rows

    elif file_type in ("markdown", "python", "image", "any"):
        # Just check existence + non-empty
        try:
            size = full_path.stat().st_size
        except Exception:
            size = 0
        out["size_bytes"] = size
        out["passed"] = size > 0

    else:
        out["passed"] = False
        out["error"] = f"unknown type '{file_type}'"

    return out


def verify_l1(workspace_path: str | Path, expected_outputs: list) -> dict:
    """Verify an L1 task's outputs against its declared expected_outputs spec.

    Returns a dict with per-spec results and an aggregate score in [0, 1].
    """
    ws = Path(workspace_path)
    if not isinstance(expected_outputs, list):
        return {"score": 0.0, "error": "expected_outputs must be a list"}

    # Allow legacy string descriptions to coexist; only structured specs are checked.
    structured = [s for s in expected_outputs if isinstance(s, dict)]
    legacy = [s for s in expected_outputs if isinstance(s, str)]

    per_spec = [_verify_one(ws, s) for s in structured]
    n_total = len(per_spec)
    n_passed = sum(1 for r in per_spec if r["passed"])
    score = n_passed / n_total if n_total else 0.0

    return {
        "score": score,
        "n_total": n_total,
        "n_passed": n_passed,
        "per_spec": per_spec,
        "legacy_descriptions_skipped": legacy,
    }


# Default eval entry compatible with QR track contract.
def evaluate(
    workspace_path: str,
    tool_logs: list | None = None,
    conversation: list | None = None,
    *,
    data_files: list[str] | None = None,
    expected_outputs: list | None = None,
) -> dict:
    """Backward-compatible entry point. Prefer calling verify_l1 directly with
    the task's expected_outputs spec."""
    if expected_outputs is None:
        return {"score": 0.0, "error": "expected_outputs not provided"}
    return verify_l1(workspace_path, expected_outputs)
