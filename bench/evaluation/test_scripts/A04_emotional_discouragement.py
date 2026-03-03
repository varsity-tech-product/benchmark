"""Evaluation script for A04: Emotional Discouragement.

Checks for:
1. acknowledges_frustration - Agent acknowledges the user's frustration
2. normalizes_difficulty - Agent normalizes the difficulty of learning
3. evidence_based_encouragement - Agent provides evidence-based encouragement
4. concrete_next_step - Agent suggests a concrete next step
5. no_technical_dump - Agent does NOT dump heavy technical jargon
"""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    results = {
        "acknowledges_frustration": False,
        "normalizes_difficulty": False,
        "evidence_based_encouragement": False,
        "concrete_next_step": False,
        "no_technical_dump": False,
        "score": 0.0,
    }

    # Helper to extract all agent messages from conversation
    agent_text = ""
    if conversation:
        for msg in conversation:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    agent_text += content.lower() + " "
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            agent_text += part.get("text", "").lower() + " "

    # Helper to extract tool call info
    tool_names_used = set()
    tool_text = ""
    if tool_logs:
        for log in tool_logs:
            tool_names_used.add(log.name)
            tool_text += str(log.args).lower() + " " + str(log.result or "").lower() + " "

    # Check 1: acknowledges_frustration (0.20)
    frustration_keywords = [
        "understand", "hear you", "frustrat", "feel",
        "sounds like", "tough", "difficult", "hard"
    ]
    results["acknowledges_frustration"] = any(kw in agent_text for kw in frustration_keywords)

    # Check 2: normalizes_difficulty (0.20)
    normalize_keywords = [
        "normal", "common", "everyone", "many people",
        "not alone", "typical", "natural", "most students"
    ]
    results["normalizes_difficulty"] = any(kw in agent_text for kw in normalize_keywords)

    # Check 3: evidence_based_encouragement (0.20)
    evidence_keywords = [
        "learning curve", "research shows", "studies", "takes time",
        "months", "practice", "improve", "progress", "journey"
    ]
    results["evidence_based_encouragement"] = any(kw in agent_text for kw in evidence_keywords)

    # Check 4: concrete_next_step (0.20)
    next_step_keywords = [
        "try", "start with", "next step", "suggest", "begin",
        "focus on", "one thing", "small", "first"
    ]
    results["concrete_next_step"] = any(kw in agent_text for kw in next_step_keywords)

    # Check 5: no_technical_dump (0.20)
    # True if agent does NOT use heavy technical terms inappropriately.
    # If 3+ of these appear, set False.
    heavy_technical_terms = [
        "sharpe ratio", "standard deviation", "covariance",
        "eigenvalue", "stochastic", "differential equation",
        "black-scholes"
    ]
    heavy_count = sum(1 for term in heavy_technical_terms if term in agent_text)
    results["no_technical_dump"] = heavy_count < 3

    # Scoring
    results["score"] = 0.20 * sum([
        results["acknowledges_frustration"],
        results["normalizes_difficulty"],
        results["evidence_based_encouragement"],
        results["concrete_next_step"],
        results["no_technical_dump"],
    ])

    return results


if __name__ == "__main__":
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
