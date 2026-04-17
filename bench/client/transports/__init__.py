"""Transport layer for QuantTutorBench client.

Two implementations of the same interface:
- MCPTransport: MCP StreamableHTTP (tool calls)
- RESTTransport: Plain HTTP (REST endpoints)
"""

from .base import SessionTransport
from .mcp_transport import MCPTransport
from .rest_transport import RESTTransport

__all__ = ["SessionTransport", "MCPTransport", "RESTTransport"]
