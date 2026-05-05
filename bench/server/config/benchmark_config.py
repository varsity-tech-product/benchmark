"""Server-scoped reproducibility configuration.

Only constants used by bench/server/ live here.
LEAN_IMAGE, BENCH_START/END, RANDOM_SEED live in bench/config/.
"""

# ── Dataset ──
# HuggingFace dataset repo and pinned commit hash.
# To update: push new data, then update this hash.
DATASET_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"
DATASET_REVISION = "719c01e3bd5c303bc947fac1d1adadb8f7ad1e39"
