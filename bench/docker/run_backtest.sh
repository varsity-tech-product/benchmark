#!/bin/bash
# run_backtest — Compile and run a LEAN C# algorithm, then extract results.
#
# Usage: run_backtest /workspace/Algorithm.cs
#
# Workflow:
#   1. Copies Algorithm.cs into the LEAN project structure
#   2. Auto-detects the algorithm class name (inheriting QCAlgorithm)
#   3. Builds the C# project via dotnet build
#   4. Runs the LEAN engine via the compiled Launcher DLL
#   5. Extracts results to /workspace/results/:
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

# Per-backtest timeout in seconds (default 5 min, overridable via env var).
# Exit code 124 = timeout killed.
LEAN_RUN_TIMEOUT="${LEAN_RUN_TIMEOUT:-300}"

# ── Usage check ────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: run_backtest <Algorithm.cs path> [--params '{\"key\":\"value\"}'] [--run-id NAME]"
    echo "  e.g. run_backtest /workspace/Algorithm.cs"
    echo "  e.g. run_backtest /workspace/Algorithm.cs --params '{\"risk_config\":\"builtin\"}' --run-id builtin"
    exit 1
fi

ALGO_FILE="$1"
shift

# Parse optional arguments
PARAMS_JSON=""
RUN_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --params)
            PARAMS_JSON="$2"
            shift 2
            ;;
        --run-id)
            RUN_ID="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [ ! -f "$ALGO_FILE" ]; then
    echo "ERROR: Algorithm file not found: $ALGO_FILE"
    exit 1
fi

# Adjust results directory for run-id
if [ -n "$RUN_ID" ]; then
    RESULTS_DIR="/workspace/results/${RUN_ID}"
fi

echo "=== LEAN Backtest Runner ==="
echo "  Algorithm: $ALGO_FILE"
echo "  LEAN root: $LEAN_ROOT"
if [ -n "$PARAMS_JSON" ]; then
    echo "  Parameters: $PARAMS_JSON"
fi
if [ -n "$RUN_ID" ]; then
    echo "  Run ID: $RUN_ID"
    echo "  Results dir: $RESULTS_DIR"
fi
echo ""

# ── Step 1: Copy algorithm into LEAN project ──────────────────────────
echo "[1/4] Copying algorithm into LEAN project..."
cp "$ALGO_FILE" "${LEAN_ALGO_DIR}/Algorithm.cs"
echo "  -> Copied to ${LEAN_ALGO_DIR}/Algorithm.cs"

# ── Step 1b: Auto-detect algorithm class name ──────────────────────────
# Look for "class <Name> : QCAlgorithm" (handles various spacings)
ALGO_CLASS=$(grep -oP 'class\s+\K\w+(?=\s*:\s*QCAlgorithm)' "$ALGO_FILE" | head -1 || true)
if [ -z "$ALGO_CLASS" ]; then
    # Fallback: look for any public class
    ALGO_CLASS=$(grep -oP 'public\s+class\s+\K\w+' "$ALGO_FILE" | head -1 || true)
fi
if [ -z "$ALGO_CLASS" ]; then
    ALGO_CLASS="Algorithm"
fi
echo "  -> Detected algorithm class: $ALGO_CLASS"

# Update config.json with the detected class name
python3 -c "
import json
with open('$LEAN_CONFIG') as f:
    cfg = json.load(f)
cfg['algorithm-type-name'] = '$ALGO_CLASS'
with open('$LEAN_CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
print('  -> Updated config algorithm-type-name: $ALGO_CLASS')
" 2>&1

# ── Step 2: Build the C# project ──────────────────────────────────────
echo "[2/4] Building LEAN project..."
cd "$LEAN_ROOT"

# Build only Algorithm.CSharp (not the whole solution) — the rest was
# pre-built in the Docker image. This is faster and avoids needing write
# permissions to every project's obj/ directory.
if ! dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug --no-restore 2>&1; then
    echo ""
    echo "ERROR: Build failed. Check the C# code for compilation errors."
    echo "Common issues:"
    echo "  - Missing 'using' statements"
    echo "  - Incorrect LEAN API usage"
    echo "  - C# syntax errors"
    exit 2
fi
# Copy the freshly built DLL to the Launcher output directory so LEAN
# loads the updated algorithm (the solution-wide build already placed
# other dependencies there during Docker image creation).
cp "${LEAN_ALGO_DIR}/bin/Debug/QuantConnect.Algorithm.CSharp.dll" \
   "${LEAN_LAUNCHER}/bin/Debug/QuantConnect.Algorithm.CSharp.dll"
echo "  -> Build succeeded"

# ── Step 2b: Inject parameters into config.json (if --params provided) ──
if [ -n "$PARAMS_JSON" ]; then
    echo "[2b/4] Injecting parameters into config.json..."
    python3 -c "
import json, sys
with open('$LEAN_CONFIG') as f:
    cfg = json.load(f)
params = json.loads('$PARAMS_JSON')
cfg.setdefault('parameters', {}).update(params)
with open('$LEAN_CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
print(f'  -> Injected {len(params)} parameter(s)')
" 2>&1
fi

# ── Step 3: Run the LEAN engine ───────────────────────────────────────
echo "[3/4] Running LEAN engine..."

# Clean previous results
rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

# Find the Launcher DLL (handles any TFM directory like net10.0)
LAUNCHER_DLL=$(find "$LEAN_LAUNCHER/bin/Debug" -name "QuantConnect.Lean.Launcher.dll" -type f 2>/dev/null | head -1)

if [ -z "$LAUNCHER_DLL" ]; then
    echo "ERROR: Could not find QuantConnect.Lean.Launcher.dll"
    echo "  Searched in: $LEAN_LAUNCHER/bin/Debug/"
    echo "  Available files:"
    find "$LEAN_LAUNCHER/bin/Debug" -name "*.dll" -type f 2>/dev/null | head -10
    exit 3
fi
echo "  -> Using Launcher DLL: $LAUNCHER_DLL"

# LEAN reads config.json from AppDomain.BaseDirectory (= DLL directory).
# Copy our config (with any injected params / class name updates) there.
LAUNCHER_BIN_DIR=$(dirname "$LAUNCHER_DLL")
cp "$LEAN_CONFIG" "$LAUNCHER_BIN_DIR/config.json"

# Run from the DLL directory so all relative paths resolve correctly.
cd "$LAUNCHER_BIN_DIR"

RUN_EXIT=0
timeout "$LEAN_RUN_TIMEOUT" dotnet "$LAUNCHER_DLL" 2>&1 | tee "$RESULTS_DIR/log.txt" || RUN_EXIT=${PIPESTATUS[0]}

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
    "$LAUNCHER_BIN_DIR"
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
