"""Numeric helpers — small, stdlib-only mean/std/bootstrap for aggregation paths.

The chart/table side of ``analysis/components/`` keeps its own numpy-based
helpers; this module is for the JSON-emitting aggregators that prefer
``None``-on-empty semantics over numpy's silent NaN/zero behavior, and for
the bootstrap CI attached to every per-cell aggregate in the report.
"""

from __future__ import annotations

import math
import random
from statistics import mean, stdev
from typing import Sequence


def safe_mean(values: Sequence[float]) -> float | None:
    return round(mean(values), 4) if values else None


def safe_std(values: Sequence[float]) -> float | None:
    return round(stdev(values), 4) if len(values) > 1 else None


def bootstrap_mean_ci(
    values: list[float],
    n_iter: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """Percentile bootstrap CI for the sample mean.

    Returns dict with ``mean``, ``ci_low``, ``ci_high``, ``n``, ``n_iter``.
    n=0 collapses to all-zero; n=1 to a degenerate CI at the single value.
    """
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": 0, "n_iter": 0}
    m = sum(values) / n
    if n == 1:
        return {"mean": m, "ci_low": m, "ci_high": m, "n": 1, "n_iter": 0}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_iter):
        s = 0.0
        for _j in range(n):
            s += values[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo_idx = int(math.floor((alpha / 2) * n_iter))
    hi_idx = int(math.ceil((1 - alpha / 2) * n_iter)) - 1
    lo_idx = max(0, min(n_iter - 1, lo_idx))
    hi_idx = max(0, min(n_iter - 1, hi_idx))
    return {
        "mean": float(m),
        "ci_low": float(means[lo_idx]),
        "ci_high": float(means[hi_idx]),
        "n": n,
        "n_iter": n_iter,
    }
