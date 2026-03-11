"""Reproducibility configuration for benchmark runs.

All version-pinned constants live here. Change these values to update
the benchmark environment, then regenerate references.
"""

# ── LEAN Engine ──
# Pin by tag; verified image ID sha256:bd1d43f32021f62ddb338da0ef319890d32520ee386dd0d5334fb10a9cd32ba7
# Note: @sha256: digest notation requires remote registry pull; use tag for local builds.
# To update: docker pull quantconnect/lean:latest && docker inspect --format '{{.Id}}' quantconnect/lean:latest
LEAN_IMAGE = "quantconnect/lean:latest"

# ── Dataset ──
# HuggingFace dataset repo and pinned commit hash.
# To update: push new data, then update this hash.
DATASET_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"
DATASET_REVISION = "8db9ca109ba27c0e80b926c3300353d634c7d959"

# ── Benchmark Window ──
# Re-exported from benchmark_dates.py for convenience.
from .benchmark_dates import BENCH_END, BENCH_START  # noqa: F401, E402

# ── Reproducibility Seed ──
# Used for any stochastic operations (pair selection ordering, etc.)
RANDOM_SEED = 42
