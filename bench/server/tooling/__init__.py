"""Tool-spec layer for the client-server architecture.

This package is intentionally isolated from the legacy benchmark stack.
It defines a canonical tool-spec format that can later be rendered into
MCP / REST / SDK-facing schemas without leaking benchmark-only metadata.
"""

from .catalog import (
    BASE_TOOL_SPECS,
    get_agent_visible_task_tool_specs,
    get_agent_visible_tool_spec,
    get_canonical_tool_spec,
    get_canonical_tool_specs,
    get_task_tool_specs,
    normalize_input_schema,
)
from .draft_catalog import DRAFT_TOOL_SPECS
from .spec_types import (
    ToolExample,
    ToolGuidance,
    ToolSpec,
    render_agent_tool_description,
)

__all__ = [
    "BASE_TOOL_SPECS",
    "DRAFT_TOOL_SPECS",
    "ToolExample",
    "ToolGuidance",
    "ToolSpec",
    "get_agent_visible_task_tool_specs",
    "get_agent_visible_tool_spec",
    "get_canonical_tool_spec",
    "get_canonical_tool_specs",
    "get_task_tool_specs",
    "normalize_input_schema",
    "render_agent_tool_description",
]
