"""Abstract transport interface for benchmark session interaction.

Both MCP and REST transports implement this interface, so the runner
doesn't care which protocol is used underneath.
"""

from abc import ABC, abstractmethod
from typing import Optional


class SessionTransport(ABC):
    """Agent ↔ Server transport layer."""

    @abstractmethod
    async def connect(self, url: str, headers: Optional[dict] = None) -> None:
        """Establish connection to the server."""

    @abstractmethod
    async def register_session(self, task_id: str = "", persona_id: str = "") -> dict:
        """Register a session. Returns {"session_id": ...} or {"error": ...}."""

    @abstractmethod
    async def start_session(self) -> dict:
        """Start the session. Returns {"student_message": ..., "background": ...}."""

    @abstractmethod
    async def list_tools(self) -> list[dict]:
        """List available tools. Returns adapter-compatible tool dicts."""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a domain tool. Returns result string."""

    @abstractmethod
    async def send_message(self, text: str, attachments: Optional[list] = None) -> dict:
        """Send message to student. Returns {"student_message": ..., "status": ...}."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up connection."""

    @property
    def session_id(self) -> Optional[str]:
        """Session ID after register, or None."""
        return getattr(self, "_session_id", None)
