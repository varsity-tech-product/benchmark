"""Compatibility wrapper for shared I-series implementation eval helpers.

This module exists so legacy imports like ``from _implementation_check import ...``
continue to work, while the implementation lives in the shared helper module.
"""

from __future__ import annotations

try:
    # Package import path used by pytest / module execution.
    from .common.implementation_check import *  # noqa: F401,F403
except ImportError:  # pragma: no cover - standalone script fallback
    import os
    import sys

    _COMMON_DIR = os.path.join(os.path.dirname(__file__), "common")
    if _COMMON_DIR not in sys.path:
        sys.path.insert(0, _COMMON_DIR)
    from implementation_check import *  # type: ignore # noqa: F401,F403
