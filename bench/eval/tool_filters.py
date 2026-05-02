"""Shared tool classification for server-side storage, reports, and evals."""

from __future__ import annotations

# Pure protocol handshake — not agent work product.
PROTOCOL_ONLY_TOOLS = frozenset({"send_message"})

# Non-substantive process events: protocol transport plus benign metadata reads.
NON_SUBSTANTIVE_TOOLS = frozenset(
    {
        "send_message",
        "get_environment_info",
    }
)
