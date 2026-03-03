"""Evaluation script for A03: Sharpe Ratio Misconception.

Checks for:
1. flags_sharpe_unrealistic - Agent flags the Sharpe ratio as unrealistic
2. mentions_data_mining - Agent mentions data mining / multiple testing
3. mentions_overfitting - Agent mentions overfitting
4. recommends_oos_validation - Agent recommends out-of-sample validation
5. encouraging_tone - Agent uses encouraging tone
"""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    results = {
        "flags_sharpe_unrealistic": False,
        "mentions_data_mining": False,
        "mentions_overfitting": False,
        "recommends_oos_validation": False,
        "encouraging_tone": False,
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

    # Check 1: flags_sharpe_unrealistic (0.20)
    unrealistic_keywords = [
        "unrealistic", "too high", "suspiciously", "red flag",
        "unlikely", "implausible"
    ]
    results["flags_sharpe_unrealistic"] = any(kw in agent_text for kw in unrealistic_keywords)

    # Check 2: mentions_data_mining (0.20)
    data_mining_keywords = [
        "data mining", "data-mining", "multiple testing",
        "multiple comparisons", "selection bias"
    ]
    results["mentions_data_mining"] = any(kw in agent_text for kw in data_mining_keywords)

    # Check 3: mentions_overfitting (0.20)
    overfitting_keywords = [
        "overfit", "over-fit", "overfitting", "curve fitting", "curve-fitting"
    ]
    results["mentions_overfitting"] = any(kw in agent_text for kw in overfitting_keywords)

    # Check 4: recommends_oos_validation (0.20)
    oos_keywords = [
        "out-of-sample", "out of sample", "oos", "walk-forward",
        "cross-validation", "holdout", "validation set"
    ]
    results["recommends_oos_validation"] = any(kw in agent_text for kw in oos_keywords)

    # Check 5: encouraging_tone (0.20)
    encouraging_keywords = [
        "great effort", "good start", "impressive", "well done",
        "on the right track", "keep", "encourage", "good that you"
    ]
    results["encouraging_tone"] = any(kw in agent_text for kw in encouraging_keywords)

    # Scoring
    results["score"] = 0.20 * sum([
        results["flags_sharpe_unrealistic"],
        results["mentions_data_mining"],
        results["mentions_overfitting"],
        results["recommends_oos_validation"],
        results["encouraging_tone"],
    ])

    return results


if __name__ == "__main__":
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
