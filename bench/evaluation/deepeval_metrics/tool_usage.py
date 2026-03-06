"""Tool Usage scoring — mathematical (no LLM).

Evaluates how well the agent selected AND effectively used tools:

Selection (60%):
- Expected tools (must-use data gates): neutral base, penalty if missing
- Convenient tools (optional shortcuts): bonus if used
- Distractor tools (domain-relevant decoys): penalty if called

Effectiveness (40%):
- For each expected tool that was called, check if it produced valid results
- A call is "effective" if it didn't return an error string or traceback
- effectiveness = effective_expected_calls / total_expected_tools

Formula:
    selection_score = clamp(base + bonus - penalties, 0, 1)
    effectiveness   = effective_expected / len(expected_tools) if expected_tools else 1.0
    score           = 0.60 * selection_score + 0.40 * effectiveness
"""


def _tool_call_effective(log) -> bool:
    """Check if a tool call produced valid (non-error) results.

    MCPProxy sets log.success=True when the Python function returns without
    exception — even if the returned string is "Error: Traceback...".
    So we inspect log.result content instead.
    """
    if not log.success:
        return False
    result = str(log.result or "")
    if result.startswith("Error:"):
        return False
    if "Traceback (most recent call last)" in result:
        return False
    if result.strip() == "":
        return False
    return True


def evaluate_tool_usage(
    proxy_logs: list,
    expected_tools: list[str],
    convenient_tools: list[str],
    distractor_names: list[str],
    is_adversarial: bool = False,
) -> dict:
    """Evaluate tool selection and effectiveness (pure math, no LLM call).

    Args:
        proxy_logs: Tool call logs (ToolCallLog objects).
        expected_tools: Must-use tools from ground_truth.expected_mcp_tools.
        convenient_tools: Bonus-eligible shortcuts from ground_truth.convenient_tools.
        distractor_names: Names of distractor tools registered in the proxy.
        is_adversarial: Whether the task is adversarial category.

    Returns:
        Dict with score, breakdown, and diagnostic lists.
    """
    called = {log.name for log in proxy_logs}

    n_convenient = len(convenient_tools)

    # ── Base score ──
    if not convenient_tools:
        base = 1.0
    else:
        base = 0.8

    # ── Bonus: +0.2/n per convenient tool used ──
    called_convenient = [t for t in convenient_tools if t in called]
    bonus = sum(0.2 / n_convenient for _ in called_convenient) if n_convenient else 0.0

    # ── Penalties ──
    missing_expected = [t for t in expected_tools if t not in called]
    penalty_expected = len(missing_expected) * 0.15

    called_distractors = [t for t in distractor_names if t in called]
    penalty_distractor = len(called_distractors) * 0.10

    # ── Selection score ──
    if is_adversarial and not expected_tools and not convenient_tools:
        selection_score = 1.0 - penalty_distractor
    else:
        selection_score = base + bonus - penalty_expected - penalty_distractor
    selection_score = max(0.0, min(1.0, selection_score))

    # ── Effectiveness: did expected tool calls actually succeed? ──
    if expected_tools:
        effective_count = 0
        ineffective_expected = []
        for tool_name in expected_tools:
            # Find all calls to this tool, check if ANY was effective
            calls_for_tool = [log for log in proxy_logs if log.name == tool_name]
            if not calls_for_tool:
                # Missing entirely — already penalized in selection, count as ineffective
                ineffective_expected.append(tool_name)
                continue
            if any(_tool_call_effective(log) for log in calls_for_tool):
                effective_count += 1
            else:
                ineffective_expected.append(tool_name)
        effectiveness = effective_count / len(expected_tools)
    else:
        effectiveness = 1.0  # no expected tools → effectiveness is perfect
        ineffective_expected = []

    # ── Composite score ──
    score = 0.60 * selection_score + 0.40 * effectiveness
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 4),
        "passed": score >= 0.5,
        "selection_score": round(selection_score, 4),
        "effectiveness": round(effectiveness, 4),
        "base": base,
        "bonus": round(bonus, 4),
        "penalty_expected": round(penalty_expected, 4),
        "penalty_distractor": round(penalty_distractor, 4),
        "missing_expected": missing_expected,
        "ineffective_expected": ineffective_expected,
        "called_distractors": called_distractors,
        "called_convenient": called_convenient,
    }
