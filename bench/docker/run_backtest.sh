#!/bin/bash
# run_backtest — Compile and run a LEAN C# algorithm, then extract results.
#
# Usage: run_backtest /workspace/Algorithm.cs
#
# Workflow:
#   1. Copies Algorithm.cs into the LEAN project structure
#   2. Builds the C# project via dotnet build
#   3. Runs the LEAN engine
#   4. Extracts results to /workspace/results/:
#      - trades.json    (closed trades from the backtest)
#      - summary.json   (performance metrics / statistics)
#      - orders.json    (all order events)
#      - log.txt        (algorithm log output)
#
# Exit codes:
#   0  — backtest completed successfully
#   1  — usage error or missing file
#   2  — build failure
#   3  — LEAN engine runtime failure
#   4  — results extraction failure

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────
LEAN_ROOT="/lean"
LEAN_LAUNCHER="${LEAN_ROOT}/Launcher"
LEAN_ALGO_DIR="${LEAN_ROOT}/Algorithm.CSharp"
LEAN_CONFIG="${LEAN_LAUNCHER}/config.json"
RESULTS_DIR="/workspace/results"
LEAN_OUTPUT_DIR="${LEAN_LAUNCHER}/bin/Debug"

# Per-backtest timeout in seconds (default 5 min, overridable via env var).
# Exit code 124 = timeout killed.
LEAN_RUN_TIMEOUT="${LEAN_RUN_TIMEOUT:-300}"

# ── Usage check ────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: run_backtest <Algorithm.cs path>"
    echo "  e.g. run_backtest /workspace/Algorithm.cs"
    exit 1
fi

ALGO_FILE="$1"

if [ ! -f "$ALGO_FILE" ]; then
    echo "ERROR: Algorithm file not found: $ALGO_FILE"
    exit 1
fi

echo "=== LEAN Backtest Runner ==="
echo "  Algorithm: $ALGO_FILE"
echo "  LEAN root: $LEAN_ROOT"
echo ""

# ── Step 1: Copy algorithm into LEAN project ──────────────────────────
echo "[1/4] Copying algorithm into LEAN project..."
cp "$ALGO_FILE" "${LEAN_ALGO_DIR}/Algorithm.cs"
echo "  -> Copied to ${LEAN_ALGO_DIR}/Algorithm.cs"

# ── Step 2: Build the C# project ──────────────────────────────────────
echo "[2/4] Building LEAN project..."
cd "$LEAN_ROOT"

if ! dotnet build QuantConnect.Lean.sln -c Debug --no-restore 2>&1; then
    echo ""
    echo "ERROR: Build failed. Check the C# code for compilation errors."
    echo "Common issues:"
    echo "  - Missing 'using' statements"
    echo "  - Incorrect LEAN API usage"
    echo "  - C# syntax errors"
    exit 2
fi
echo "  -> Build succeeded"

# ── Step 3: Run the LEAN engine ───────────────────────────────────────
echo "[3/4] Running LEAN engine..."

# Clean previous results
rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

cd "$LEAN_LAUNCHER"

RUN_EXIT=0
timeout "$LEAN_RUN_TIMEOUT" dotnet run --no-build -c Debug 2>&1 | tee "$RESULTS_DIR/log.txt" || RUN_EXIT=${PIPESTATUS[0]}

if [ "$RUN_EXIT" -eq 124 ]; then
    echo ""
    echo "ERROR: LEAN engine timed out after ${LEAN_RUN_TIMEOUT}s."
    echo "Increase LEAN_RUN_TIMEOUT env var if the algorithm needs more time."
    exit 124
elif [ "$RUN_EXIT" -ne 0 ]; then
    echo ""
    echo "ERROR: LEAN engine failed at runtime (exit code $RUN_EXIT)."
    echo "Check $RESULTS_DIR/log.txt for details."
    echo "Common issues:"
    echo "  - Data not found for requested symbols/dates"
    echo "  - Algorithm runtime exceptions"
    echo "  - Incorrect date ranges"
    exit 3
fi
echo "  -> LEAN engine completed"

# ── Step 4: Extract results ───────────────────────────────────────────
echo "[4/4] Extracting results..."

# LEAN writes results to the configured results-destination-folder or
# to the Launcher output directory. Check both locations.
LEAN_RESULTS_SEARCH_DIRS=(
    "$RESULTS_DIR"
    "$LEAN_OUTPUT_DIR"
    "${LEAN_LAUNCHER}"
)

# Function to find and copy a result file
copy_result() {
    local pattern="$1"
    local dest_name="$2"

    for search_dir in "${LEAN_RESULTS_SEARCH_DIRS[@]}"; do
        # Look for files matching the pattern (case-insensitive)
        local found
        found=$(find "$search_dir" -maxdepth 3 -iname "$pattern" -type f 2>/dev/null | head -1)
        if [ -n "$found" ] && [ "$found" != "$RESULTS_DIR/$dest_name" ]; then
            cp "$found" "$RESULTS_DIR/$dest_name"
            echo "  -> $dest_name (from $found)"
            return 0
        fi
    done
    echo "  -> $dest_name (not found)"
    return 0
}

# Extract key result files
copy_result "*-trades.json" "trades.json"
copy_result "*-order-events.json" "orders.json"
copy_result "*-statistics.json" "summary.json"

# The log was already captured by tee above
if [ -f "$RESULTS_DIR/log.txt" ]; then
    echo "  -> log.txt (captured during run)"
fi

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "=== Backtest Complete ==="
echo "Results directory: $RESULTS_DIR"
echo ""

# List result files with sizes
if [ -d "$RESULTS_DIR" ]; then
    ls -lh "$RESULTS_DIR/" 2>/dev/null || true
fi

# Quick summary if statistics file exists
if [ -f "$RESULTS_DIR/summary.json" ]; then
    echo ""
    echo "--- Performance Summary ---"
    # Print key metrics if python3 is available
    python3 -c "
import json, sys
try:
    with open('$RESULTS_DIR/summary.json') as f:
        stats = json.load(f)
    for key in ['Total Trades', 'Net Profit', 'Sharpe Ratio', 'Win Rate',
                'Average Win', 'Average Loss', 'Compounding Annual Return']:
        if key in stats:
            print(f'  {key}: {stats[key]}')
except Exception:
    pass
" 2>/dev/null || true
fi

exit 0
