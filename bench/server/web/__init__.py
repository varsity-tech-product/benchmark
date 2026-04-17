"""Isolated web UI package for the new client-server architecture.

This package is intentionally separate from the legacy ``bench/web`` app.
"""

from .ui_app import ui_routes

__all__ = ["ui_routes"]
