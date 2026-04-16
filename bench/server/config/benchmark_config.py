"""Server-scoped reproducibility configuration.

Only constants used by bench/server/ live here.
LEAN_IMAGE, BENCH_START/END, RANDOM_SEED live in bench/config/.
"""

# ── Dataset ──
# HuggingFace dataset repo and pinned commit hash.
# To update: push new data, then update this hash.
DATASET_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"
DATASET_REVISION = "793bca3f8dc70d379d423358f4159eca2d8be83f"
