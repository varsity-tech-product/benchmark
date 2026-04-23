"""Shared filesystem paths for the student simulator stability experiment."""

from __future__ import annotations

from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = EXPERIMENT_ROOT.parent.parent
RESOURCE_ROOT = EXPERIMENT_ROOT / "resources"
