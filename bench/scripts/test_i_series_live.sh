#!/usr/bin/env bash
# Live test: run all 10 I-series implementation tasks through the full run-single pipeline.
# Uses OpenAI direct API with gpt-4o-mini (cheapest capable model).
# All I-series tasks require --docker (LEAN sandbox).
#
# Usage: bash bench/scripts/test_i_series_live.sh

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
    I01_implement_sma
    I02_trend_following
    I03_mean_reversion
    I04_multi_timeframe
    I05_cross_asset
    I06_multi_signal_sweep
    I07_alpha_model
    I08_multi_alpha
    I09_risk_management
    I10_parameter_optimization
)

# ── Results tracking ──
LOG_DIR="$BENCH_DIR/results/run-single/$AGENT/$MODEL"
mkdir -p "$LOG_DIR"

PASSED=0
FAILED=0
ERRORS=()

echo "============================================"
echo "  I-Series Live Test  (${#TASKS[@]} tasks)"
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
