"""MCP metric configurations for QuantTutorBench.

Configures DeepEval's MCP-related metrics for evaluating tool usage.
Reference: https://github.com/confident-ai/deepeval

DeepEval API (v3.8+):
    from deepeval.metrics import ToolCorrectnessMetric
    from deepeval.test_case import LLMTestCase, ToolCall
"""

from typing import Optional

from config.llm_config import resolve_deepeval_model

try:
    from deepeval.metrics import ToolCorrectnessMetric
    from deepeval.test_case import LLMTestCase, ToolCall

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


def create_tool_correctness_metric(
    threshold: float = 0.5,
    model: Optional[str] = None,
) -> "ToolCorrectnessMetric":
    """Create a DeepEval ToolCorrectnessMetric instance.

    Measures precision/recall of tool selection against expected tools.

    Args:
        threshold: Minimum passing score.
        model: LLM model for optimality evaluation.

    Returns:
        Configured ToolCorrectnessMetric.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    kwargs = {
        "threshold": threshold,
        "should_consider_ordering": False,
        "should_exact_match": False,
    }
    if model:
        kwargs["model"] = resolve_deepeval_model(model)

    return ToolCorrectnessMetric(**kwargs)


def create_tool_test_case(
    input_text: str,
    actual_output: str,
    tools_called: list[dict],
    expected_tools: list[dict],
) -> "LLMTestCase":
    """Create an LLMTestCase with tool call information for ToolCorrectnessMetric.

    Args:
        input_text: The user's input.
        actual_output: The agent's response text.
        tools_called: List of dicts with 'name' and optional 'input_parameters', 'output'.
        expected_tools: List of dicts with 'name' and optional 'input_parameters', 'output'.

    Returns:
        Configured LLMTestCase with tool information.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    called = [
        ToolCall(
            name=t["name"],
            input_parameters=t.get("input_parameters"),
            output=t.get("output"),
        )
        for t in tools_called
    ]

    expected = [
        ToolCall(
            name=t["name"],
            input_parameters=t.get("input_parameters"),
            output=t.get("output"),
        )
        for t in expected_tools
    ]

    return LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        tools_called=called,
        expected_tools=expected,
    )


def evaluate_tool_correctness(
    input_text: str,
    actual_output: str,
    tools_called: list[dict],
    expected_tools: list[dict],
    threshold: float = 0.5,
    model: Optional[str] = None,
) -> dict:
    """Evaluate tool correctness using DeepEval.

    Args:
        input_text: The user's input.
        actual_output: The agent's response.
        tools_called: List of tool call dicts.
        expected_tools: List of expected tool dicts.
        threshold: Minimum passing score.
        model: LLM model for judge.

    Returns:
        Dict with score, reason, and passed status.
    """
    if not DEEPEVAL_AVAILABLE:
        # Fallback to manual computation
        metrics = compute_tool_precision_recall(
            called_tools=[t["name"] for t in tools_called],
            expected_tools=[t["name"] for t in expected_tools],
            distractor_tools=[],
        )
        return {
            "score": metrics["f1"],
            "reason": "Computed via manual precision/recall (deepeval not available)",
            "passed": metrics["f1"] >= threshold,
        }

    test_case = create_tool_test_case(
        input_text, actual_output, tools_called, expected_tools
    )
    metric = create_tool_correctness_metric(threshold=threshold, model=model)

    try:
        metric.measure(test_case)
        return {
            "score": metric.score,
            "reason": metric.reason if hasattr(metric, "reason") else "",
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        # Fallback
        metrics = compute_tool_precision_recall(
            called_tools=[t["name"] for t in tools_called],
            expected_tools=[t["name"] for t in expected_tools],
            distractor_tools=[],
        )
        return {
            "score": metrics["f1"],
            "reason": f"DeepEval failed ({e}), used manual computation",
            "passed": metrics["f1"] >= threshold,
        }


def create_tool_correctness_config(expected_tools: list[str]) -> dict:
    """Create config for ToolCorrectnessMetric (backward-compatible).

    Returns config dict for manual metric construction.
    """
    return {
        "threshold": 0.5,
        "should_consider_ordering": False,
        "should_exact_match": False,
        "expected_tools": expected_tools,
    }


def check_required_capabilities(
    called_tools: list[str],
    tool_call_outputs: dict[str, str],
    required_capabilities: list[dict],
) -> dict:
    """Check whether the agent fulfilled the required capabilities from the task ground truth.

    Design doc §6.1.2: Capability checks (not step-sequence checks — the agent
    can reach these in any order). Each capability has:
    - tool: a specific tool that must be called
    - tool_any_of: any one of these tools must be called
    - output_contains: regex pattern the tool output must match
    - evidence: free-text description (not machine-checkable)

    Args:
        called_tools: List of tool names the agent called.
        tool_call_outputs: Dict mapping tool_name -> concatenated output strings.
        required_capabilities: List of capability dicts from task ground_truth.

    Returns:
        Dict with capability_completion (0-1), per-capability results, and details.
    """
    import re

    if not required_capabilities:
        return {"capability_completion": 1.0, "capabilities": [], "total": 0, "met": 0}

    called_set = set(called_tools)
    results = []

    for cap in required_capabilities:
        cap_result = {
            "description": cap.get("description", ""),
            "met": False,
        }

        tool_met = True
        # Check tool requirement
        if "tool" in cap and cap["tool"]:
            if cap["tool"] not in called_set:
                tool_met = False
        elif "tool_any_of" in cap and cap["tool_any_of"]:
            if not any(t in called_set for t in cap["tool_any_of"]):
                tool_met = False

        # Check output_contains if specified
        output_met = True
        if "output_contains" in cap and cap["output_contains"] and tool_met:
            pattern = cap["output_contains"]
            # Check if any tool output matches the pattern
            matched = False
            for tool_name, output in tool_call_outputs.items():
                try:
                    if re.search(pattern, output, re.IGNORECASE):
                        matched = True
                        break
                except re.error:
                    if pattern.lower() in output.lower():
                        matched = True
                        break
            output_met = matched

        cap_result["met"] = tool_met and output_met
        results.append(cap_result)

    met_count = sum(1 for r in results if r["met"])
    total = len(results)

    return {
        "capability_completion": round(met_count / total, 4) if total > 0 else 1.0,
        "capabilities": results,
        "total": total,
        "met": met_count,
    }


def compute_tool_precision_recall(
    called_tools: list[str], expected_tools: list[str], distractor_tools: list[str]
) -> dict:
    """Compute tool selection precision and recall manually.

    This is a fallback when DeepEval metrics are not available.

    Args:
        called_tools: Tools the agent actually called.
        expected_tools: Tools that should have been called.
        distractor_tools: Tools that should NOT have been called.

    Returns:
        Dict with precision, recall, f1, distractor_calls.
    """
    called_set = set(called_tools)
    expected_set = set(expected_tools)
    distractor_set = set(distractor_tools)

    # Edge case: no expected tools (e.g. adversarial tasks where tool use is
    # optional).  Score based solely on distractor avoidance.
    if not expected_set:
        distractor_calls = list(called_set & distractor_set)
        return {
            "precision": 1.0 if not distractor_calls else 0.0,
            "recall": 1.0,
            "f1": 1.0 if not distractor_calls else 0.0,
            "distractor_calls": distractor_calls,
            "distractor_call_count": len(distractor_calls),
        }

    # True positives: expected tools that were called
    tp = len(called_set & expected_set)
    # False positives: non-expected tools that were called (including distractors)
    fp = len(called_set - expected_set)
    # False negatives: expected tools that were NOT called
    fn = len(expected_set - called_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    distractor_calls = list(called_set & distractor_set)

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "distractor_calls": distractor_calls,
        "distractor_call_count": len(distractor_calls),
    }


def compute_optional_tool_value(
    proxy_logs: list,
    core_tools: list[str],
    distractor_tools: list[str],
) -> dict:
    """Compute optional tool bonus/penalty for Track A tasks.

    In Track A, tools are optional: no expected-tool list and no ordering checks.
    This metric rewards useful tool usage while keeping the no-tool path valid.

    Returns:
        Dict with bonus, penalty, tool_value_score, and diagnostics.
    """
    if not proxy_logs:
        return {
            "bonus": 0.0,
            "penalty": 0.0,
            "tool_value_score": 0.0,
            "core_calls": 0,
            "successful_core_calls": 0,
            "failed_core_calls": 0,
            "distractor_calls": 0,
            "redundant_calls": 0,
            "used_tools": False,
        }

    core_set = set(core_tools or [])
    distractor_set = set(distractor_tools or [])

    total_calls = len(proxy_logs)
    core_calls = [log for log in proxy_logs if getattr(log, "name", "") in core_set]
    core_success = [log for log in core_calls if getattr(log, "success", False)]
    core_failed = [log for log in core_calls if not getattr(log, "success", False)]
    distractor_calls = [
        log for log in proxy_logs if getattr(log, "name", "") in distractor_set
    ]

    # Simple redundancy estimate: repeated (tool_name, normalized_args) tuples.
    # Normalize args via JSON to avoid unhashable nested values.
    import json

    def _normalize_args(args: dict) -> str:
        try:
            return json.dumps(args or {}, sort_keys=True, default=str)
        except Exception:
            return repr(args)

    seen = set()
    redundant_calls = 0
    for log in proxy_logs:
        args = getattr(log, "args", {}) or {}
        sig = (getattr(log, "name", ""), _normalize_args(args))
        if sig in seen:
            redundant_calls += 1
        else:
            seen.add(sig)

    bonus = 0.0
    # +0.05: any successful core tool call
    if len(core_success) >= 1:
        bonus += 0.05
    # +0.07: at least two successful core calls (typically evidence of grounding)
    if len(core_success) >= 2:
        bonus += 0.07
    # +0.03: efficient usage (low noise)
    if (
        len(distractor_calls) == 0
        and len(core_failed) <= 1
        and redundant_calls <= max(1, total_calls // 4)
    ):
        bonus += 0.03
    bonus = min(0.15, bonus)

    penalty = 0.0
    # Distractor tools are always misuse in this benchmark.
    penalty += min(0.15, 0.05 * len(distractor_calls))
    # Penalize repeated failed core calls after the first.
    if len(core_failed) > 1:
        penalty += min(0.06, 0.02 * (len(core_failed) - 1))
    # Mild spam penalty when call volume is high with little diversity.
    unique_tool_names = {getattr(log, "name", "") for log in proxy_logs}
    if total_calls > 8 and len(unique_tool_names) <= 2:
        penalty += 0.02
    penalty = min(0.15, penalty)

    tool_value_score = max(0.0, min(1.0, bonus - penalty))

    return {
        "bonus": round(bonus, 4),
        "penalty": round(penalty, 4),
        "tool_value_score": round(tool_value_score, 4),
        "core_calls": len(core_calls),
        "successful_core_calls": len(core_success),
        "failed_core_calls": len(core_failed),
        "distractor_calls": len(distractor_calls),
        "redundant_calls": redundant_calls,
        "used_tools": total_calls > 0,
    }
