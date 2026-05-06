#!/usr/bin/env bash
# Live test: run a small v3 L2 task list through the API batch driver.
#
# Usage: bash bench/scripts/test_i_series_live.sh
#        MAX_PARALLEL=3 bash bench/scripts/test_i_series_live.sh
#        MAX_PARALLEL=all bash bench/scripts/test_i_series_live.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$BENCH_DIR")"

cd "$PROJECT_DIR"

# ── Activate virtualenv if present ──
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
fi

# ── Configuration ──
SERVER="${QTB_BASELINE_SERVER:-http://127.0.0.1:8000}"
API_KEY="${QTB_CLIENT_API_KEY:-}"
AGENTS="${AGENTS:-claude_haiku_4_5}"
CONDITIONS="${CONDITIONS:-agent}"
PROTOCOL="${PROTOCOL:-mcp}"
MAX_PARALLEL="${MAX_PARALLEL:-all}"

TASKS=(
    L2_ADV_01_investment_advice
    L2_ADV_11_prompt_injection_csv
    L2_DIA_01_overfit_diagnosis
    L2_E2E_01_strategy_validation
    L2_E2E_04_strategy_ab_testing
)

# ── Results tracking ──
LOG_DIR="$BENCH_DIR/results/baseline-run/l2-smoke"
mkdir -p "$LOG_DIR"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/l2-live-status.XXXXXX")"
trap 'rm -rf "$STATUS_DIR"' EXIT

PASSED=0
FAILED=0
ERRORS=()

echo "============================================"
echo "  v3 L2 Live Test  (${#TASKS[@]} tasks)"
echo "============================================"
echo "Server:     $SERVER"
echo "Agents:     $AGENTS"
echo "Conditions: $CONDITIONS"
echo "Protocol:   $PROTOCOL"
echo ""

if [ "$MAX_PARALLEL" = "all" ]; then
    MAX_PARALLEL="${#TASKS[@]}"
elif ! [[ "$MAX_PARALLEL" =~ ^[1-9][0-9]*$ ]]; then
    echo "MAX_PARALLEL must be a positive integer or 'all', got: $MAX_PARALLEL" >&2
    exit 2
elif [ "$MAX_PARALLEL" -gt "${#TASKS[@]}" ]; then
    MAX_PARALLEL="${#TASKS[@]}"
fi

echo "Running with up to $MAX_PARALLEL concurrent tasks."
echo ""

run_task() {
    local task="$1"
    local task_output_dir="$LOG_DIR/$task"
    local task_log="$LOG_DIR/${task}.log"
    local status_file="$STATUS_DIR/${task}.status"

    echo "── [$task] started ───────────────────────────"
    echo "   log: $task_log"

    (
        set +e
        python3 bench/scripts/baseline_run.py \
            --output-dir "$task_output_dir" \
            --docs-dir "$task_output_dir/docs" \
            run \
            --server "$SERVER" \
            --api-key "$API_KEY" \
            --protocol "$PROTOCOL" \
            --tasks "$task" \
            --agents "$AGENTS" \
            --conditions "$CONDITIONS" \
            --workers 1 \
            --force \
            2>&1 | tee "$task_log" | sed -u "s/^/[$task] /"
        local exit_code=${PIPESTATUS[0]}
        printf '%s\n' "$exit_code" > "$status_file"
        exit "$exit_code"
    ) &
}

ACTIVE_JOBS=0
for TASK in "${TASKS[@]}"; do
    run_task "$TASK"
    ACTIVE_JOBS=$((ACTIVE_JOBS + 1))

    if [ "$ACTIVE_JOBS" -ge "$MAX_PARALLEL" ]; then
        if ! wait -n; then
            :
        fi
        ACTIVE_JOBS=$((ACTIVE_JOBS - 1))
    fi
done

while [ "$ACTIVE_JOBS" -gt 0 ]; do
    if ! wait -n; then
        :
    fi
    ACTIVE_JOBS=$((ACTIVE_JOBS - 1))
done

for TASK in "${TASKS[@]}"; do
    STATUS_FILE="$STATUS_DIR/${TASK}.status"

    if [ ! -f "$STATUS_FILE" ]; then
        FAILED=$((FAILED + 1))
        ERRORS+=("$TASK (missing status)")
        echo "── [$TASK] FAIL (missing status) ────────────"
        echo ""
        continue
    fi

    EXIT_CODE="$(cat "$STATUS_FILE")"
    if [ "$EXIT_CODE" -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "── [$TASK] PASS (exit 0) ────────────────────"
    else
        FAILED=$((FAILED + 1))
        ERRORS+=("$TASK (exit $EXIT_CODE)")
        echo "── [$TASK] FAIL (exit $EXIT_CODE) ───────────"
    fi
    echo ""
done

# ── Summary ──
echo "============================================"
echo "  Summary: $PASSED passed, $FAILED failed / ${#TASKS[@]} total"
echo "============================================"

if [ $FAILED -gt 0 ]; then
    echo "Failed tasks:"
    for ERR in "${ERRORS[@]}"; do
        echo "  - $ERR"
    done
    exit 1
fi

echo "All ${#TASKS[@]} tasks completed successfully."
