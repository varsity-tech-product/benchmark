#!/usr/bin/env bash
# Test runner for QuantTutorBench server tests.
#
# Usage:
#   ./tests/run_tests.sh              # Run all tests
#   ./tests/run_tests.sh unit         # Unit tests only
#   ./tests/run_tests.sh api          # API endpoint tests only
#   ./tests/run_tests.sh integration  # Integration tests only
#   ./tests/run_tests.sh -k "attach"  # Filter by keyword

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BENCH_DIR")"

# Find Python
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        PYTHON="$REPO_ROOT/.venv/bin/python"
    elif command -v python3 &>/dev/null; then
        PYTHON=python3
    else
        echo "Error: No Python found. Set PYTHON env var." >&2
        exit 1
    fi
fi

cd "$BENCH_DIR"

PYTEST_ARGS=(-x -q)

# Parse first argument as category or pass through to pytest
case "${1:-all}" in
    unit)
        PYTEST_ARGS+=(tests/unit/)
        shift
        ;;
    api)
        PYTEST_ARGS+=(tests/api/)
        shift
        ;;
    integration)
        PYTEST_ARGS+=(tests/integration/)
        shift
        ;;
    all)
        PYTEST_ARGS+=(tests/unit/ tests/api/ tests/integration/)
        shift 2>/dev/null || true
        ;;
    *)
        # Pass everything to pytest (e.g. -k "attach" or -v)
        PYTEST_ARGS+=(tests/unit/ tests/api/ tests/integration/)
        ;;
esac

# Append remaining args
PYTEST_ARGS+=("$@")

exec "$PYTHON" -m pytest "${PYTEST_ARGS[@]}"
