#!/usr/bin/env bash
# Live test: run all 10 X-series debug tasks through the full run-single pipeline.
# Uses OpenAI direct API with gpt-4o-mini (cheapest capable model).
#
# Usage: bash bench/scripts/test_x_series_live.sh

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
AGENT="openai"
MODEL="gpt-4o-mini"
EVAL_MODEL="gpt-4o-mini"
SIMULATOR_MODEL="gpt-4o-mini"
PERSONA="intermediate_developer"
CONDITION="agent"
MAX_TURNS=10

TASKS=(
    X01_ma_offbyone
    X02_lookahead
    X03_position_bug
    X04_returns_diff
    X05_timezone_merge
    X06_overfit_single
    X07_warmup_bug
    X08_order_type_bug
    X09_alpha_conflict
    X10_universe_stale
)

# ── Results tracking ──
LOG_DIR="$BENCH_DIR/results/run-single/$AGENT/$MODEL"
mkdir -p "$LOG_DIR"

PASSED=0
FAILED=0
ERRORS=()

echo "============================================"
echo "  X-Series Live Test  (${#TASKS[@]} tasks)"
echo "============================================"
echo "Agent:     $AGENT"
echo "Model:     $MODEL"
echo "Eval:      $EVAL_MODEL"
echo "Simulator: $SIMULATOR_MODEL"
echo "Persona:   $PERSONA"
echo "Condition: $CONDITION"
echo "Max turns: $MAX_TURNS"
echo ""

for TASK in "${TASKS[@]}"; do
    echo "── [$TASK] ──────────────────────────────────"

    TASK_LOG="$LOG_DIR/${TASK}_${PERSONA}.log"

    set +e
    python3 bench/run_benchmark.py run-single \
        --task "$TASK" \
        --persona "$PERSONA" \
        --agent "$AGENT" \
        --model "$MODEL" \
        --condition "$CONDITION" \
        --eval-model "$EVAL_MODEL" \
        --simulator-model "$SIMULATOR_MODEL" \
        --max-turns "$MAX_TURNS" \
        --save-result \
        --docker \
        2>&1 | tee "$TASK_LOG"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e

    if [ $EXIT_CODE -eq 0 ]; then
        PASSED=$((PASSED + 1))
        echo "  => PASS (exit 0)"
    else
        FAILED=$((FAILED + 1))
        ERRORS+=("$TASK (exit $EXIT_CODE)")
        echo "  => FAIL (exit $EXIT_CODE)"
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
