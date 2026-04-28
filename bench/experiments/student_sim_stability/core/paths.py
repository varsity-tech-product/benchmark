"""Shared filesystem paths for the student simulator stability experiment."""

from __future__ import annotations

from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = EXPERIMENT_ROOT.parent.parent
RESOURCE_ROOT = EXPERIMENT_ROOT / "resources"


def default_results_dir() -> Path:
    from experiments.student_sim_stability.core.config import OUTPUT_DIR

    out = Path(OUTPUT_DIR)
    return out if out.is_absolute() else BENCH_ROOT / out


def resolve_results_dir(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BENCH_ROOT / path
