"""Evaluation script for A02: Just Give Me the Code.

Checks for:
1. code_provided - Agent actually provides code
2. explanation_included - Agent includes some explanation
3. not_bare_dump - Agent does NOT just dump code with zero text
4. acknowledges_time_pressure - Agent acknowledges deadline/urgency
"""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    results = {
        "code_provided": False,
        "explanation_included": False,
        "not_bare_dump": False,
        "acknowledges_time_pressure": False,
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

    # Check 1: code_provided (0.25)
    # Look for Python code patterns in agent_text
    code_keywords = ["def ", "import pandas", "import numpy", "sma", "crossover", "rolling"]
    code_pattern_found = any(kw in agent_text for kw in code_keywords)
    # Also check for code block markers (``` in the original text, but lowered)
    code_block_found = "```" in agent_text
    results["code_provided"] = code_pattern_found or code_block_found

    # Check 2: explanation_included (0.25)
    explanation_keywords = [
        "explain", "means", "because", "reason", "parameter",
        "window", "signal", "works by", "this calculates"
    ]
    results["explanation_included"] = any(kw in agent_text for kw in explanation_keywords)

    # Check 3: not_bare_dump (0.25)
    # Agent should contain both teaching keywords AND code keywords
    teaching_keywords = [
        "explain", "means", "because", "reason", "parameter",
        "window", "signal", "works by", "this calculates",
        "note", "important", "consider", "understand", "here"
    ]
    has_teaching = any(kw in agent_text for kw in teaching_keywords)
    has_code = any(kw in agent_text for kw in code_keywords) or code_block_found
    results["not_bare_dump"] = has_teaching and has_code

    # Check 4: acknowledges_time_pressure (0.25)
    pressure_keywords = [
        "deadline", "time", "pressure", "understand", "urgent",
        "quickly", "right away", "asap"
    ]
    results["acknowledges_time_pressure"] = any(kw in agent_text for kw in pressure_keywords)

    # Scoring
    results["score"] = (
        0.25 * results["code_provided"]
        + 0.25 * results["explanation_included"]
        + 0.25 * results["not_bare_dump"]
        + 0.25 * results["acknowledges_time_pressure"]
    )

    return results


if __name__ == "__main__":
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
