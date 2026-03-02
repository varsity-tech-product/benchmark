"""Code Execution QR — Three-layer code quality evaluation.

Evaluates agent-produced code without re-execution by analyzing workspace
files, tool logs, and (optionally) reference data.

Layer A: Static Analysis (20%)
    AST-parse all .py files; check syntax, structure, dangerous patterns.

Layer B: Execution Result Analysis (40%)
    Parse recorded shell_exec results for execution success/failure.
    Uses the LAST execution per script to reflect iterative debugging.

Layer C: Output Verification (40%)
    Compare numerical outputs to reference["key_results"].
    Skipped when no reference is available; weights renormalized.
"""

import ast
import json
import os
import re
from typing import Optional

# ===================================================================
# Layer A: Static Analysis (20% of Code QR)
# ===================================================================

# Patterns considered dangerous in tutoring context
_DANGEROUS_PATTERNS = {
    ast.Call: lambda node: (
        isinstance(node.func, ast.Name)
        and node.func.id in ("exec", "eval", "__import__")
    ),
}


def evaluate_code_static(workspace_path: str, tool_logs: list) -> dict:
    """Layer A: Static analysis of agent-produced Python code.

    Returns:
        has_code: Whether any Python code was found.
        syntax_valid: Whether all code parses without SyntaxError.
        structure_score: Basic structure quality (functions/classes).
        dangerous_count: Number of dangerous pattern instances.
        score: Weighted Layer A score (0-1).
    """
    code_sources: list[tuple[str, str]] = []  # (label, source_code)

    # Source 1: .py files in workspace
    if workspace_path and os.path.isdir(workspace_path):
        for fname in sorted(os.listdir(workspace_path)):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    code_sources.append((fname, f.read()))
            except (IOError, UnicodeDecodeError):
                pass

    # Source 2: code written via file_write tool
    for log in tool_logs or []:
        if log.name != "file_write":
            continue
        path = log.args.get("path", "")
        content = log.args.get("content", "")
        if path.endswith(".py") and content:
            label = os.path.basename(path)
            # Avoid duplicating workspace files
            if not any(label == existing for existing, _ in code_sources):
                code_sources.append((label, content))

    # Source 3: inline python -c code from shell_exec
    for log in tool_logs or []:
        if log.name != "shell_exec":
            continue
        cmd = log.args.get("command", "")
        match = re.search(r"""python3?\s+-c\s+['"](.*?)['"]""", cmd, re.DOTALL)
        if match and len(match.group(1)) > 20:
            code_sources.append(("inline_exec", match.group(1)))

    if not code_sources:
        return {
            "has_code": False,
            "syntax_valid": True,
            "structure_score": 0.0,
            "dangerous_count": 0,
            "score": 0.0,
        }

    # Analyze each code source
    syntax_results = []
    total_functions = 0
    total_classes = 0
    total_imports = 0
    dangerous_count = 0

    for label, source in code_sources:
        try:
            tree = ast.parse(source)
            syntax_results.append(True)
        except SyntaxError:
            syntax_results.append(False)
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                total_functions += 1
            elif isinstance(node, ast.ClassDef):
                total_classes += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                total_imports += 1
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                dangerous_count += 1  # bare except
            # Check dangerous function calls
            if isinstance(node, ast.Call):
                for pat_type, checker in _DANGEROUS_PATTERNS.items():
                    if isinstance(node, pat_type) and checker(node):
                        dangerous_count += 1

    all_valid = all(syntax_results)

    # Structure score: having functions/classes shows modular thinking
    if total_functions >= 3 or (total_functions >= 1 and total_classes >= 1):
        structure_score = 1.0
    elif total_functions >= 1:
        structure_score = 0.6
    elif total_imports >= 2:
        structure_score = 0.3
    else:
        structure_score = 0.1

    # Composite Layer A score
    syntax_component = 1.0 if all_valid else 0.0  # 50% weight
    structure_component = structure_score  # 30% weight
    safety_component = (
        1.0 if dangerous_count == 0 else max(0.0, 1.0 - 0.25 * dangerous_count)
    )  # 20% weight

    score = (
        0.50 * syntax_component + 0.30 * structure_component + 0.20 * safety_component
    )

    return {
        "has_code": True,
        "syntax_valid": all_valid,
        "structure_score": round(structure_score, 4),
        "dangerous_count": dangerous_count,
        "files_analyzed": len(code_sources),
        "total_functions": total_functions,
        "total_classes": total_classes,
        "score": round(score, 4),
    }


# ===================================================================
# Layer B: Execution Result Analysis (40% of Code QR)
# ===================================================================

_PYTHON_CMD_RE = re.compile(
    r"python3?\s+"  # python or python3
    r"(?:-c\s+|"  # -c flag
    r"[\w./\\-]+\.py)"  # or filename.py
)


def _classify_exec_result(log) -> float:
    """Classify a shell_exec result into a score.

    Uses error type hierarchy:
    - Clean success: 1.0
    - Success with warnings: 0.8
    - ImportError/ModuleNotFoundError: 0.3 (environment issue, not agent's fault)
    - RuntimeError/TypeError/ValueError: 0.1 (logic error)
    - SyntaxError: 0.0
    - Timeout: 0.0
    """
    result = str(log.result or "")
    success = log.success
    lower = result.lower()

    if "timed out" in lower or "timeout" in lower:
        return 0.0
    if "syntaxerror" in lower:
        return 0.0
    if "importerror" in lower or "modulenotfounderror" in lower:
        return 0.3
    if "traceback (most recent call last)" in lower:
        # Generic error — check for specific types
        if any(
            e in lower
            for e in (
                "typeerror",
                "valueerror",
                "runtimeerror",
                "indexerror",
                "keyerror",
                "attributeerror",
            )
        ):
            return 0.1
        return 0.1
    if success and "error" not in lower:
        return 1.0
    if success:
        return 0.8  # success=True but "error" appears (warnings, etc.)
    return 0.2  # unknown failure


def evaluate_code_execution(tool_logs: list, workspace_path: str) -> dict:
    """Layer B: Analyze execution results from tool logs (no re-execution).

    Returns:
        exec_calls_found: Number of Python execution calls detected.
        success_rate: Weighted average of final execution scores per script.
        untested_files: List of .py files written but never executed.
        score: Layer B score (0-1).
    """
    # Find all shell_exec calls that run Python
    exec_calls = []
    for log in tool_logs or []:
        if log.name != "shell_exec":
            continue
        cmd = log.args.get("command", "")
        if _PYTHON_CMD_RE.search(cmd):
            exec_calls.append(log)

    # Group by script name; use LAST execution per script
    script_results: dict[str, float] = {}
    for log in exec_calls:
        cmd = log.args.get("command", "")
        # Extract script name
        match = re.search(r"([\w./\\-]+\.py)", cmd)
        script_name = match.group(1) if match else "inline"
        script_results[script_name] = _classify_exec_result(log)

    # Detect untested files: .py files written but never executed
    written_files: set[str] = set()
    for log in tool_logs or []:
        if log.name == "file_write":
            path = log.args.get("path", "")
            if path.endswith(".py"):
                written_files.add(os.path.basename(path))

    # Also check workspace for .py files
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith(".py"):
                written_files.add(fname)

    executed_scripts = set()
    for name in script_results:
        executed_scripts.add(os.path.basename(name))

    untested = sorted(written_files - executed_scripts)

    if not exec_calls and not written_files:
        # No code execution and no code files — Layer B not applicable
        return {
            "exec_calls_found": 0,
            "success_rate": 0.0,
            "untested_files": [],
            "score": 0.0,
            "applicable": False,
        }

    if not exec_calls and written_files:
        # Code was written but never executed
        return {
            "exec_calls_found": 0,
            "success_rate": 0.0,
            "untested_files": untested,
            "score": 0.0,
            "applicable": True,
        }

    # Score = average of final execution scores per script
    scores = list(script_results.values())
    success_rate = sum(scores) / len(scores) if scores else 0.0

    # Penalty for untested files (each untested file reduces score by 0.1)
    untested_penalty = min(0.3, len(untested) * 0.1)
    score = max(0.0, success_rate - untested_penalty)

    return {
        "exec_calls_found": len(exec_calls),
        "success_rate": round(success_rate, 4),
        "untested_files": untested,
        "script_results": {k: round(v, 2) for k, v in script_results.items()},
        "score": round(score, 4),
        "applicable": True,
    }


# ===================================================================
# Layer C: Output Verification (40% of Code QR)
# ===================================================================

_RELATIVE_ERROR_THRESHOLDS = [
    (0.05, 1.0),  # <5% error → 1.0
    (0.15, 0.75),  # 5-15% → 0.75
    (0.30, 0.50),  # 15-30% → 0.5
    (float("inf"), 0.25),  # >30% → 0.25
]


def _relative_error_score(actual: float, expected: float) -> float:
    """Score a single metric by relative error vs reference."""
    if expected == 0:
        return 1.0 if abs(actual) < 1e-6 else 0.25
    rel_error = abs(actual - expected) / abs(expected)
    for threshold, score in _RELATIVE_ERROR_THRESHOLDS:
        if rel_error < threshold:
            return score
    return 0.25


def _extract_numerical_outputs(
    workspace_path: str, tool_logs: list
) -> dict[str, float]:
    """Extract numerical results from workspace JSON files and tool outputs."""
    results: dict[str, float] = {}

    # Source 1: workspace JSON files
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, (int, float)):
                            results[k] = v
            except Exception:
                pass

    # Source 2: metric tool outputs
    _metric_tools = {"run_backtest", "analyze_backtest_results", "compute_statistics"}
    for log in tool_logs or []:
        if log.name not in _metric_tools:
            continue
        try:
            data = json.loads(log.result or "")
            if isinstance(data, dict):
                metrics = data.get("metrics", data)
                if isinstance(metrics, dict):
                    for k, v in metrics.items():
                        if isinstance(v, (int, float)) and k not in results:
                            results[k] = v
        except (json.JSONDecodeError, TypeError):
            pass

    return results


def evaluate_code_output(
    workspace_path: str,
    reference: Optional[dict],
    tool_logs: list,
) -> dict:
    """Layer C: Compare agent outputs to reference key_results.

    Returns:
        numerical_accuracy: Average per-metric accuracy score.
        output_completeness: Fraction of reference workspace_files present.
        metrics_compared: Number of metrics compared.
        score: Layer C score (0-1).
    """
    if reference is None:
        return {"applicable": False, "score": 0.0}

    ref_key_results = reference.get("key_results", {})
    ref_workspace_files = reference.get("workspace_files", [])

    # --- Numerical accuracy ---
    agent_outputs = _extract_numerical_outputs(workspace_path, tool_logs)

    metric_scores = []
    for metric_name, ref_value in ref_key_results.items():
        if not isinstance(ref_value, (int, float)):
            continue
        if metric_name in agent_outputs:
            s = _relative_error_score(agent_outputs[metric_name], ref_value)
            metric_scores.append(s)
        else:
            metric_scores.append(0.0)  # metric missing entirely

    numerical_accuracy = (
        sum(metric_scores) / len(metric_scores) if metric_scores else 0.0
    )

    # --- Artifact completeness ---
    if ref_workspace_files:
        workspace_files = set()
        if workspace_path and os.path.isdir(workspace_path):
            workspace_files = set(os.listdir(workspace_path))
        present = sum(1 for f in ref_workspace_files if f in workspace_files)
        completeness = present / len(ref_workspace_files)
    else:
        completeness = 1.0  # no reference files to compare

    # Composite: 70% numerical accuracy + 30% completeness
    score = 0.70 * numerical_accuracy + 0.30 * completeness

    return {
        "applicable": True,
        "numerical_accuracy": round(numerical_accuracy, 4),
        "output_completeness": round(completeness, 4),
        "metrics_compared": len(metric_scores),
        "agent_metrics_found": len(agent_outputs),
        "score": round(score, 4),
    }


# ===================================================================
# Combined evaluation
# ===================================================================


def evaluate_code_combined(
    workspace_path: str,
    tool_logs: list,
    reference: Optional[dict] = None,
    task_requires_code: bool = False,
) -> dict:
    """Combined Code Execution QR evaluation.

    Runs all three layers and produces a weighted score:
    - Layer A: Static Analysis — 20%
    - Layer B: Execution Result Analysis — 40%
    - Layer C: Output Verification — 40% (skipped if no reference)

    When Layer C is skipped, weights renormalize to A=33%, B=67%.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: Tool call logs (ToolCallLog objects from proxy.get_logs()).
        reference: Reference data from ReferenceStore.load() (may be None).
        task_requires_code: Whether the task definition requires code.

    Returns:
        Dict with per-layer results, applicability flag, and combined score.
    """
    # Layer A: Static Analysis
    layer_a = evaluate_code_static(workspace_path, tool_logs)

    if not layer_a["has_code"] and not task_requires_code:
        return {
            "applicable": False,
            "score": 0.0,
            "reason": "no code produced and task does not require code",
            "static_analysis": layer_a,
            "execution": None,
            "output_verification": None,
        }

    # Layer B: Execution Result Analysis
    layer_b = evaluate_code_execution(tool_logs, workspace_path)

    # Layer C: Output Verification (requires reference)
    layer_c = evaluate_code_output(workspace_path, reference, tool_logs)

    # Combine scores
    if layer_c.get("applicable", False):
        # Full 3-layer: A=20%, B=40%, C=40%
        score = (
            0.20 * layer_a["score"] + 0.40 * layer_b["score"] + 0.40 * layer_c["score"]
        )
    else:
        # No reference: renormalize A=33%, B=67%
        score = (1 / 3) * layer_a["score"] + (2 / 3) * layer_b["score"]

    return {
        "applicable": True,
        "score": round(score, 4),
        "static_analysis": layer_a,
        "execution": layer_b,
        "output_verification": layer_c if layer_c.get("applicable") else None,
    }
