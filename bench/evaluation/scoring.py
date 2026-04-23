"""Compatibility import for the v6 scoring implementation.

The canonical scoring logic lives in :mod:`server.eval.core.scoring`.  Keeping this
module as a thin re-export prevents old ``evaluation.scoring`` imports from
silently using a divergent aggregation policy.
"""

from server.eval.core.scoring import (  # noqa: F401
    compute_benchmark_kpis,
    compute_combined_benchmark_kpis,
    compute_overall,
    compute_pass_at_k,
    compute_pass_power_k,
    compute_task_score,
)
