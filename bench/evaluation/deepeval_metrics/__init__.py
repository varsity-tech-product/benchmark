"""Compatibility package for v6 evaluation metrics.

Layer 2 runtime metrics live under ``eval.judges`` and
``eval.programmatic``. This package keeps old import paths working
without keeping a second implementation.
``quant_geval`` remains here because it belongs to Layer 1.
"""

from __future__ import annotations

import importlib
import sys

_ALIASES = {
    "_scoring_utils": "eval.judges.runtime.scoring_utils",
    "async_utils": "eval.judges.runtime.async_utils",
    "code_process": "eval.programmatic.code_process",
    "process_metrics": "eval.judges.process_metrics",
    "tool_usage": "eval.programmatic.tool_usage",
}

for _name, _target in _ALIASES.items():
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(_target)
