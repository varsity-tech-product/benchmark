#!/bin/bash
# ============================================================
# Test the report pipeline locally (without Docker/OpenClaw).
#
# Runs the orchestrator directly to verify:
#   - AKShare data fetching works
#   - LLM calls via OpenRouter succeed
#   - Report is generated correctly
#
# Prerequisites:
#   1. pip install -r requirements.txt
#   2. export OPENROUTER_API_KEY=sk-or-v1-...
#
# Usage: bash scripts/test_pipeline.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Check API key
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "ERROR: OPENROUTER_API_KEY not set."
    echo "Run: export OPENROUTER_API_KEY=sk-or-v1-..."
    exit 1
fi

echo "=== Testing Evening Report Pipeline ==="
echo "Project dir: $PROJECT_DIR"
echo ""

python orchestrator.py

echo ""
echo "=== Test complete ==="
