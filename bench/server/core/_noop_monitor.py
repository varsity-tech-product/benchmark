"""No-op live monitor stub.

Replaces orchestrator.live_monitor.emit for server/ independence.
The new HTTP server uses logging instead of live WebSocket monitor.
"""

import logging

logger = logging.getLogger(__name__)


def emit(event_type: str, data: dict = None):
    """No-op emit — server/ doesn't use live WebSocket monitor."""
    pass
