"""Tool Usage scoring — mathematical (no LLM).

Evaluates how well the agent selected tools from the available set:
- Expected tools (must-use data gates): neutral base, penalty if missing
- Convenient tools (optional shortcuts): bonus if used
- Distractor tools (domain-relevant decoys): penalty if called

Formula:
    base = 1.0 if no convenient_tools, else 0.8
    bonus = sum(0.2 / n_convenient for each convenient tool used)
    penalty = 0.15 per missing expected + 0.10 per distractor called
    score = clamp(base + bonus - penalties, 0, 1)
"""

_NON_SUBSTANTIVE_TOOLS = frozenset({"send_message", "get_environment_info"})


def evaluate_tool_usage(
    proxy_logs: list,
    expected_tools: list[str],
    convenient_tools: list[str],
    distractor_names: list[str],
    is_adversarial: bool = False,
) -> dict:
    """Evaluate tool selection quality (pure math, no LLM call).

    Args:
        proxy_logs: Tool call logs (ToolCallLog objects).
        expected_tools: Must-use tools from ground_truth.expected_mcp_tools.
        convenient_tools: Bonus-eligible shortcuts from ground_truth.convenient_tools.
        distractor_names: Names of distractor tools registered in the proxy.
        is_adversarial: Whether the task is adversarial category.

    Returns:
        Dict with score, breakdown, and diagnostic lists.
    """
    called = {log.name for log in proxy_logs if log.name not in _NON_SUBSTANTIVE_TOOLS}

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

    # ── Adversarial edge case ──
    if is_adversarial and not expected_tools and not convenient_tools:
        score = 1.0 - penalty_distractor
    else:
        score = base + bonus - penalty_expected - penalty_distractor

    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 4),
        "base": base,
        "bonus": round(bonus, 4),
        "penalty_expected": round(penalty_expected, 4),
        "penalty_distractor": round(penalty_distractor, 4),
        "missing_expected": missing_expected,
        "called_distractors": called_distractors,
        "called_convenient": called_convenient,
    }
