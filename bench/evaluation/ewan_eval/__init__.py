# DeepEval-free evaluation metrics.
"""Compatibility package for canonical v6 Ewan eval modules."""

from __future__ import annotations

import importlib
import sys

_ALIASES = {
    "async_utils": "server.eval.judges.runtime.async_utils",
    "conv_geval": "server.eval.judges.runtime.conv_geval",
    "llm_client": "server.eval.judges.runtime.llm_client",
    "model_resolver": "server.eval.judges.runtime.model_resolver",
    "tutor_conv_geval": "server.eval.judges.tutor_6d",
}

for _name, _target in _ALIASES.items():
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(_target)
