"""Reproducibility configuration for benchmark runs.

All version-pinned constants live here. Change these values to update
the benchmark environment, then regenerate references.
"""

# ── LEAN Engine ──
# Local LEAN sandbox image built from docker/Dockerfile.lean.
# Pinned QuantConnect/Lean commit:
#   0c4a121371be684c7e9e8d0e92816a2f34a185b9
# To update: rebuild quant-tutor-env:v2.2-lean after changing docker/Dockerfile.lean.
LEAN_IMAGE = "quant-tutor-env:v2.2-lean"

# ── Dataset ──
# HuggingFace dataset repo and pinned commit hash.
# To update: push new data, then update this hash.
DATASET_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"
DATASET_REVISION = "bea0c30f85a19b493c5bbff1e2839498d46719e1"

# ── Benchmark Window ──
# Re-exported from benchmark_dates.py for convenience.
from .benchmark_dates import BENCH_END, BENCH_START  # noqa: F401, E402

# ── Reproducibility Seed ──
# Used for any stochastic operations (pair selection ordering, etc.)
RANDOM_SEED = 42
