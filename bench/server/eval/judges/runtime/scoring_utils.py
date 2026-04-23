"""Shared scoring utilities for LLM-as-judge evaluators.

Provides:
- extract_json_from_response(): Parse JSON from LLM output (handles markdown fences)
"""

import json
import re


def extract_json_from_response(text: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences.

    Returns {} on failure (never raises).
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}
