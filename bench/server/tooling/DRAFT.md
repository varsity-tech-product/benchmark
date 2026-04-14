# Tool Injection Draft

This directory contains the canonical tool-spec layer for the new
client-server architecture.

Goals:

- Keep the new architecture clearly isolated from legacy code paths.
- Define tool metadata once, then render it consistently into MCP, REST,
  and SDK-facing formats.
- Make tool usage more agent-native without relying on downstream prompt
  engineering.
- Avoid leaking benchmark-only metadata such as internal role labels,
  task-specific hints, concrete dataset filenames, real sandbox paths, or
  evaluation criteria.

Current scope:

- Canonical spec types in `spec_types.py`
- First-batch draft specs for high-impact tools in `draft_catalog.py`
- Full catalog builder in `catalog.py` that maps current server tools into
  canonical specs
- Agent-visible projection that strips benchmark-only metadata
- Task-level role overlay for `core` / `convenient` / `distractor`
  without exposing those labels to the agent

Now wired:

- Runtime MCP `list_tools`
- REST `/tools`
- Client bridge conversion

Still pending:

- Evaluation-side richer tool metadata consumption
- Optional resource-style tool guides beyond `description` + `inputSchema`

Open follow-ups:

1. Expand the hand-written draft coverage so more tools have richer
   family-specific guidance and examples.
2. Add structured error payloads for argument mistakes and recovery hints.
3. Add optional guide-resource discovery for richer, on-demand help.
4. Decide whether REST should expose richer tool metadata beyond the MCP
   `description` + `inputSchema` projection.
