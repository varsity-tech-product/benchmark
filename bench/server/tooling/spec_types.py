"""Canonical tool-spec types for the new server architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

ToolFamily = Literal[
    "foundation",
    "docs",
    "analysis",
    "backtest",
    "lean_trials",
    "research",
    "risk",
    "portfolio",
]

BenchmarkRole = Literal["core", "convenient", "distractor"]
AbstractionLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ToolGuidance:
    """Agent-facing usage guidance that avoids benchmark-only hints."""

    family: ToolFamily
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    common_mistakes: tuple[str, ...] = ()
    returns: str = ""
    related_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolExample:
    """Minimal generic example that avoids real task or data leakage."""

    intent: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """Canonical tool spec.

    `benchmark_role` and other internal metadata are intentionally kept
    separate from the agent-visible projection.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    guidance: ToolGuidance
    examples: tuple[ToolExample, ...] = ()
    benchmark_role: BenchmarkRole = "core"
    domain_scope: tuple[str, ...] = field(default_factory=tuple)
    abstraction_level: AbstractionLevel = "medium"


def agent_visible_payload(spec: ToolSpec) -> dict[str, Any]:
    """Project a ToolSpec into the safe agent-visible shape.

    Benchmark-only fields such as role labels are intentionally excluded.
    """
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "guidance": {
            "family": spec.guidance.family,
            "use_when": list(spec.guidance.use_when),
            "avoid_when": list(spec.guidance.avoid_when),
            "common_mistakes": list(spec.guidance.common_mistakes),
            "returns": spec.guidance.returns,
            "related_tools": list(spec.guidance.related_tools),
        },
        "examples": [
            {"intent": example.intent, "args": example.args}
            for example in spec.examples
        ],
        "meta": {
            "domain_scope": list(spec.domain_scope),
            "abstraction_level": spec.abstraction_level,
        },
    }


def render_agent_tool_description(spec: ToolSpec) -> str:
    """Render compact agent-facing guidance into a single tool description.

    MCP tool definitions natively expose only ``name``, ``description``, and
    ``inputSchema``. To keep the runtime protocol simple while still giving
    agents richer, prompt-independent guidance, we fold selected canonical
    metadata into the description string.
    """
    lines = [spec.description.strip()]

    if spec.guidance.use_when:
        lines.append("Use when: " + "; ".join(spec.guidance.use_when[:2]))
    if spec.guidance.avoid_when:
        lines.append("Avoid when: " + "; ".join(spec.guidance.avoid_when[:2]))
    if spec.guidance.common_mistakes:
        lines.append("Common mistakes: " + "; ".join(spec.guidance.common_mistakes[:2]))
    if spec.guidance.returns:
        lines.append(f"Returns: {spec.guidance.returns}")
    if spec.guidance.related_tools:
        lines.append("Related tools: " + ", ".join(spec.guidance.related_tools[:4]))
    if spec.examples:
        example_args = json.dumps(
            spec.examples[0].args,
            ensure_ascii=True,
            separators=(", ", ": "),
            sort_keys=True,
        )
        lines.append(f"Example args: {example_args}")

    return "\n".join(line for line in lines if line)
