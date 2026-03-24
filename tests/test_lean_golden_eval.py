#!/usr/bin/env python3
"""Golden sanity tests for I-series behavioral evaluation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.evaluation.test_scripts.common.implementation_check import (  # noqa: E402
    compute_behavioral_score,
)


_GOLDENS = {
    "I01": {"resolution": "daily", "composite_min": 0.99, "position_min": 0.99, "performance_min": 0.99, "trade_min": 0.99},
    "I02": {"resolution": "daily", "composite_min": 0.60, "position_min": 0.35, "performance_min": 0.70, "trade_min": 0.99},
    "I03": {"resolution": "daily", "composite_min": 0.99, "position_min": 0.99, "performance_min": 0.99, "trade_min": 0.99},
    "I04": {"resolution": "hour", "composite_min": 0.99, "position_min": 0.99, "performance_min": 0.99, "trade_min": 0.99},
    "I05": {"resolution": "daily", "composite_min": 0.95, "position_min": 0.99, "performance_min": 0.90, "trade_min": 0.99},
    "I07": {"resolution": "daily", "composite_min": 0.69, "position_min": 0.45, "performance_min": 0.99, "trade_min": 0.80},
}


def test_golden_behavioral_scores():
    """Saved Golden workspaces should achieve strong behavioral scores."""
    for task, cfg in _GOLDENS.items():
        result = compute_behavioral_score(
            task,
            f"tests/results/{task}",
            resolution=cfg["resolution"],
        )

        assert result.layers_available == ["position", "performance", "trade"], (
            f"{task}: expected position/performance/trade layers, got {result.layers_available}"
        )
        assert result.position_score >= cfg["position_min"], (
            f"{task}: position_score {result.position_score:.3f} below {cfg['position_min']:.3f}"
        )
        assert result.performance_score >= cfg["performance_min"], (
            f"{task}: performance_score {result.performance_score:.3f} below {cfg['performance_min']:.3f}"
        )
        assert result.trade_score >= cfg["trade_min"], (
            f"{task}: trade_score {result.trade_score:.3f} below {cfg['trade_min']:.3f}"
        )
        assert result.composite_score >= cfg["composite_min"], (
            f"{task}: composite_score {result.composite_score:.3f} below {cfg['composite_min']:.3f}"
        )
