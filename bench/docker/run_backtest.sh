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
#   0    — backtest completed successfully
#   1    — usage error or missing file
#   2    — build failure
#   3    — LEAN engine runtime failure
#   4    — results extraction failure
#   124  — timeout killed the LEAN process

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

# ── Step 0: Clean previous results ───────────────────────────────────
# Must happen BEFORE build so that a compile failure never leaves stale
# results from a prior trial in the results directory.
rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

# ── Step 1: Copy algorithm into LEAN project ──────────────────────────
echo "[1/4] Copying algorithm into LEAN project..."
cp "$ALGO_FILE" "${LEAN_ALGO_DIR}/Algorithm.cs"
echo "  -> Copied to ${LEAN_ALGO_DIR}/Algorithm.cs"

# ── Step 2: Build the C# project ──────────────────────────────────────
echo "[2/4] Building LEAN project..."
cd "$LEAN_ROOT"

# Build only Algorithm.CSharp (not the full solution) for faster
# incremental builds.  The warm-up compilation at container creation
# already built the full solution; subsequent runs only need to
# recompile the user's algorithm.  Fall back to full-sln build if
# the project-level build fails (e.g. missing project references).
if ! dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug --no-restore 2>&1; then
    echo ""
    echo "ERROR: Build failed. Check the C# code for compilation errors."
    echo "Common issues:"
    echo "  - Missing 'using' statements"
    echo "  - Incorrect LEAN API usage"
    echo "  - C# syntax errors"
    exit 2
fi
echo "  -> Build succeeded"

# Copy the compiled DLL to the Launcher directory where LEAN expects it.
# dotnet build outputs to Algorithm.CSharp/bin/Debug/ but LEAN's JobQueue
# loads from Launcher/QuantConnect.Algorithm.CSharp.dll.
ALGO_DLL="${LEAN_ALGO_DIR}/bin/Debug/QuantConnect.Algorithm.CSharp.dll"
if [ -f "$ALGO_DLL" ]; then
    cp "$ALGO_DLL" "${LEAN_LAUNCHER}/QuantConnect.Algorithm.CSharp.dll"
    # LEAN's Composer loads assemblies from Launcher/bin/Debug/, not Launcher/.
    # Both locations must have the freshly compiled DLL.
    cp "$ALGO_DLL" "${LEAN_LAUNCHER}/bin/Debug/QuantConnect.Algorithm.CSharp.dll"
    echo "  -> Copied DLL to Launcher directory"
else
    echo "WARNING: Compiled DLL not found at $ALGO_DLL"
fi

# ── Step 2b: Reset and inject parameters into config.json ─────────────
echo "[2b/4] Preparing config.json parameters..."
# Write PARAMS_JSON to a temp file to avoid shell injection via heredoc
_PARAMS_TMPFILE=$(mktemp /tmp/params_XXXXXX.json)
printf '%s' "$PARAMS_JSON" > "$_PARAMS_TMPFILE"
export _PARAMS_TMPFILE
python3 -c "
import json, os

with open('$LEAN_CONFIG') as f:
    cfg = json.load(f)

params_file = os.environ.get('_PARAMS_TMPFILE', '')
if params_file and os.path.isfile(params_file):
    raw = open(params_file).read().strip()
    params = json.loads(raw) if raw else {}
else:
    params = {}
cfg['parameters'] = params

# Point LEAN results to the run-specific directory
cfg['results-destination-folder'] = '$RESULTS_DIR'

# Ensure algorithm-type-name is fully qualified so LEAN can resolve
# the user's class among 500+ QCAlgorithm subclasses in the project.
cfg['algorithm-type-name'] = 'QuantConnect.Algorithm.CSharp.Algorithm'

with open('$LEAN_CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)

print(f'  -> Set {len(params)} parameter(s)')
print(f'  -> Results folder: $RESULTS_DIR')
" 2>&1
rm -f "$_PARAMS_TMPFILE"

# ── Step 3: Run the LEAN engine ───────────────────────────────────────
echo "[3/4] Running LEAN engine..."

cd "$LEAN_LAUNCHER"

RUN_EXIT=0
timeout "$LEAN_RUN_TIMEOUT" dotnet run --no-build -c Debug < /dev/null 2>&1 | tee "$RESULTS_DIR/log.txt" || RUN_EXIT=${PIPESTATUS[0]}

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
# LEAN names files after the algorithm-type-name.  Common suffixes:
#   -order-events.json, -summary.json (newer LEAN), -statistics.json (older LEAN)
copy_result "*-trades.json" "trades.json"
copy_result "*-order-events.json" "orders.json"
# Try -summary.json first (current LEAN), fallback to -statistics.json (legacy)
copy_result "*-summary.json" "summary.json"
if [ ! -f "$RESULTS_DIR/summary.json" ]; then
    copy_result "*-statistics.json" "summary.json"
fi

# Copy the main LEAN output JSON (contains Orders, closedTrades, charts)
# to a deterministic name.  This is the largest JSON that isn't a sidecar.
for search_dir in "${LEAN_RESULTS_SEARCH_DIRS[@]}"; do
    found=$(find "$search_dir" -maxdepth 1 -name "*.json" \
        ! -name "*-summary.json" ! -name "*-order-events.json" \
        ! -name "*-log*" ! -name "data-monitor*" ! -name "*data-requests*" \
        ! -name "summary.json" ! -name "orders.json" ! -name "trades.json" \
        -size +100k -type f -printf '%s %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -n "$found" ] && [ "$found" != "$RESULTS_DIR/result.json" ]; then
        cp "$found" "$RESULTS_DIR/result.json"
        echo "  -> result.json (from $found)"
        break
    fi
done

# Extract closedTrades to trades.json (only when LEAN actually provides them)
if [ ! -f "$RESULTS_DIR/trades.json" ] && [ -f "$RESULTS_DIR/result.json" ]; then
    python3 -c "
import json
with open('$RESULTS_DIR/result.json') as f:
    data = json.load(f)
ct = data.get('totalPerformance', {}).get('closedTrades', [])
if ct:
    with open('$RESULTS_DIR/trades.json', 'w') as f:
        json.dump(ct, f)
    print(f'  -> trades.json ({len(ct)} closed trades extracted)')
else:
    print('  -> trades.json (no closedTrades in LEAN output)')
" 2>/dev/null || true
fi

# The log was already captured by tee above
if [ -f "$RESULTS_DIR/log.txt" ]; then
    echo "  -> log.txt (captured during run)"
fi

if [ ! -f "$RESULTS_DIR/summary.json" ]; then
    echo ""
    echo "ERROR: LEAN run finished but summary.json could not be extracted."
    echo "Check $RESULTS_DIR/log.txt and the LEAN output directories for details."
    exit 4
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
