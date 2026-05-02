"""Compatibility wrapper for shared data source verification helpers.

This module exists so legacy imports like ``from _data_source_check import ...``
continue to work, while the implementation lives in the shared helper module.
"""

from __future__ import annotations

try:
    # Package import path used by pytest / module execution.
    from .common.data_source_check import *  # noqa: F401,F403
    from .common.evidence_helpers import apply_data_source_cap  # noqa: F401
except ImportError:  # pragma: no cover - standalone script fallback
    import os
    import sys

    _COMMON_DIR = os.path.join(os.path.dirname(__file__), "common")
    if _COMMON_DIR not in sys.path:
        sys.path.insert(0, _COMMON_DIR)
    from data_source_check import *  # type: ignore # noqa: F401,F403
    from evidence_helpers import apply_data_source_cap  # type: ignore # noqa: F401
