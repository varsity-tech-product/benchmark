"""Test condition definitions for QuantTutorBench.

Implements the 2x2 matrix design:

                  | Tools Enabled     | No Tools              |
    ------------- | ----------------- | --------------------- |
    Tutor prompt  | agent             | pure_llm              |
    Baseline      | baseline          | pure_llm_baseline     |

Each condition controls two dimensions:
- tools_enabled: whether MCP tools are passed to the agent
- prompt_mode: "tutor" (teaching) or "baseline" (dump-the-answer)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TestCondition:
    """A benchmark test condition (one cell of the 2x2 matrix)."""

    name: str
    tools_enabled: bool
    prompt_mode: str  # "tutor" | "baseline"
    description: str


CONDITIONS: dict[str, TestCondition] = {
    "agent": TestCondition(
        name="agent",
        tools_enabled=True,
        prompt_mode="tutor",
        description="Full agent: SDK + tools + tutor prompt",
    ),
    "baseline": TestCondition(
        name="baseline",
        tools_enabled=True,
        prompt_mode="baseline",
        description="Baseline: SDK + tools + dump-the-answer prompt",
    ),
    "pure_llm": TestCondition(
        name="pure_llm",
        tools_enabled=False,
        prompt_mode="tutor",
        description="Pure LLM: no tools + tutor prompt",
    ),
    "pure_llm_baseline": TestCondition(
        name="pure_llm_baseline",
        tools_enabled=False,
        prompt_mode="baseline",
        description="Pure LLM baseline: no tools + dump-the-answer prompt",
    ),
}

CONDITION_NAMES = list(CONDITIONS.keys())
