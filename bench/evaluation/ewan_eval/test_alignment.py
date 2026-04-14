"""A/B alignment test: new metrics vs legacy deepeval_metrics.

Runs both implementations on the same conversation + criteria and
compares scores.  Validates that the DeepEval-free replacement produces
equivalent results.

Usage:
    cd bench
    python -m evaluation.ewan_eval.test_alignment [--model MODEL] [--verbose]

Requires OPENROUTER_API_KEY environment variable.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Load .env from project root
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(_env_path)

# ──────────────────────────────────────────────────────────────
# Test fixture: a short tutoring conversation
# ──────────────────────────────────────────────────────────────

_TEST_CONVERSATION = [
    {
        "role": "user",
        "content": (
            "Hi, I'm trying to build a simple moving average crossover strategy "
            "but I'm not sure how to handle the warm-up period. My SMA(20) keeps "
            "generating signals from day 1 which doesn't seem right."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Great question! You're right to be concerned about the warm-up period. "
            "An SMA(20) needs at least 20 data points before it can produce a "
            "meaningful value. Before that, the calculation is based on fewer "
            "observations and can be misleading.\n\n"
            "Here's what happens: if you start trading from day 1, the SMA(20) on "
            "day 5 is really just an SMA(5) — it's using all available data but "
            "that's only 5 points, not 20. This leads to false crossover signals.\n\n"
            "The fix is straightforward:\n"
            "```python\n"
            "# Skip the first N bars where N = max lookback period\n"
            "warmup = max(fast_period, slow_period)\n"
            "signals = signals.iloc[warmup:]\n"
            "```\n\n"
            "Would you like me to show you how this looks with your specific parameters?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Oh I see, so I need to skip those early bars. But what if I'm using "
            "two SMAs — SMA(10) and SMA(50)? Do I need to wait for 50 bars?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Exactly! When you're using multiple indicators, the warm-up period "
            "should be the maximum lookback across all of them. So with SMA(10) "
            "and SMA(50), you'd wait 50 bars before generating any trading signals.\n\n"
            "Think of it this way: your crossover signal depends on BOTH SMAs being "
            "accurate. Even though SMA(10) is ready by bar 10, SMA(50) won't be "
            "meaningful until bar 50. A crossover between a 'real' SMA(10) and a "
            "'partial' SMA(50) would be comparing apples and oranges.\n\n"
            "Let me check your data to make sure we set this up correctly."
        ),
    },
]

_TEST_PERSONA = "intermediate"
_TEST_DIMENSION = "D3_scaffolding_calibration"


# ──────────────────────────────────────────────────────────────
# Test runner
# ──────────────────────────────────────────────────────────────


async def _run_new_metrics(conversation, persona_level, dimension, model_name):
    """Run the new DeepEval-free implementation."""
    from evaluation.ewan_eval.conv_geval import (
        ConversationalTestCase,
        EwanConvGEval,
        Turn,
    )
    from evaluation.ewan_eval.model_resolver import resolve_ewan_model

    # Load rubric criteria
    rubric_path = (
        Path(__file__).parent.parent / "rubrics" / f"rubric_{persona_level}.json"
    )
    rubric = json.loads(rubric_path.read_text())

    # Build criteria (same logic as tutor_conv_geval._build_criteria_from_rubric)
    dim_data = rubric["dimensions"][dimension]
    scoring_lines = []
    for score, desc in sorted(
        dim_data["scoring_guidance"].items(), key=lambda x: int(x[0])
    ):
        scoring_lines.append(f"Score {score}: {desc}")

    criteria = (
        f"=== STUDENT PERSONA CONTEXT ===\n"
        f"Persona level: {persona_level.upper()}\n"
        f"{rubric.get('description', '')}\n\n"
        f"=== DIMENSION EVALUATION: {dimension} ===\n"
        f"CRITERIA: {dim_data['criteria']}\n\n"
        f"SCORING RUBRIC (1-10 scale):\n"
        f"{chr(10).join(scoring_lines)}\n\n"
        f"EVALUATION INSTRUCTIONS:\n"
        f"- Focus ONLY on the tutor's (assistant's) messages.\n"
        f"- Consider the ENTIRE conversation.\n"
        f"- A score of 5 is baseline adequate; reserve 9-10 for truly exceptional.\n"
    )

    model = resolve_ewan_model(model_name)
    metric = EwanConvGEval(
        name=dimension, criteria=criteria, threshold=0.5, model=model
    )

    tc = ConversationalTestCase(
        turns=[Turn(role=t["role"], content=t["content"]) for t in conversation],
    )

    # Phase 1
    steps = await metric._a_generate_evaluation_steps()
    # Phase 2
    score = await metric.a_measure(tc)

    return {
        "score": metric.score,
        "reason": metric.reason,
        "cost": metric.evaluation_cost,
        "steps": steps,
    }


async def _run_legacy_metrics(conversation, persona_level, dimension, model_name):
    """Run the legacy DeepEval-based implementation."""
    try:
        from deepeval.metrics import ConversationalGEval
        from deepeval.test_case import ConversationalTestCase, Turn
    except ImportError:
        return {"error": "deepeval not installed"}

    from config.model_resolver import resolve_deepeval_model

    # Same rubric loading
    rubric_path = (
        Path(__file__).parent.parent / "rubrics" / f"rubric_{persona_level}.json"
    )
    rubric = json.loads(rubric_path.read_text())
    dim_data = rubric["dimensions"][dimension]
    scoring_lines = []
    for score, desc in sorted(
        dim_data["scoring_guidance"].items(), key=lambda x: int(x[0])
    ):
        scoring_lines.append(f"Score {score}: {desc}")
    criteria = (
        f"=== STUDENT PERSONA CONTEXT ===\n"
        f"Persona level: {persona_level.upper()}\n"
        f"{rubric.get('description', '')}\n\n"
        f"=== DIMENSION EVALUATION: {dimension} ===\n"
        f"CRITERIA: {dim_data['criteria']}\n\n"
        f"SCORING RUBRIC (1-10 scale):\n"
        f"{chr(10).join(scoring_lines)}\n\n"
        f"EVALUATION INSTRUCTIONS:\n"
        f"- Focus ONLY on the tutor's (assistant's) messages.\n"
        f"- Consider the ENTIRE conversation.\n"
        f"- A score of 5 is baseline adequate; reserve 9-10 for truly exceptional.\n"
    )

    # Patch logprobs fallback (same as production)
    # Use original deepeval_metrics/ (not the _legacy copy) since its
    # internal imports reference evaluation.deepeval_metrics.*
    from evaluation.deepeval_metrics.tutor_conv_geval import (
        _patch_gptmodel_logprobs_fallback,
        _patch_trim_and_load_json,
    )

    _patch_gptmodel_logprobs_fallback()
    _patch_trim_and_load_json()

    model = resolve_deepeval_model(model_name)
    metric = ConversationalGEval(
        name=dimension,
        criteria=criteria,
        threshold=0.5,
        model=model,
    )

    tc = ConversationalTestCase(
        turns=[Turn(role=t["role"], content=t["content"]) for t in conversation],
    )

    # Phase 1
    steps = await metric._a_generate_evaluation_steps()
    # Phase 2
    score = await metric.a_measure(tc)

    raw_score = metric.score
    if raw_score > 1.0:
        raw_score = raw_score / 10.0

    return {
        "score": max(0.0, min(1.0, raw_score)),
        "reason": getattr(metric, "reason", ""),
        "cost": getattr(metric, "evaluation_cost", 0.0),
        "steps": steps,
    }


async def run_alignment_test(model_name: str, verbose: bool = False):
    """Run both implementations and compare."""
    print(f"\n{'=' * 60}")
    print("  A/B Alignment Test: new metrics vs legacy deepeval_metrics")
    print(f"  Model: {model_name}")
    print(f"  Dimension: {_TEST_DIMENSION}")
    print(f"  Persona: {_TEST_PERSONA}")
    print(f"{'=' * 60}\n")

    # ── Run new implementation ──
    print("[NEW] Running DeepEval-free implementation...")
    t0 = time.time()
    new_result = await _run_new_metrics(
        _TEST_CONVERSATION, _TEST_PERSONA, _TEST_DIMENSION, model_name
    )
    new_time = time.time() - t0
    print(f"[NEW] Done in {new_time:.1f}s — score: {new_result['score']:.4f}")

    # ── Run legacy implementation ──
    print("\n[LEGACY] Running DeepEval-based implementation...")
    t0 = time.time()
    legacy_result = await _run_legacy_metrics(
        _TEST_CONVERSATION, _TEST_PERSONA, _TEST_DIMENSION, model_name
    )
    legacy_time = time.time() - t0
    if "error" in legacy_result:
        print(f"[LEGACY] Skipped: {legacy_result['error']}")
        legacy_result = None
    else:
        print(
            f"[LEGACY] Done in {legacy_time:.1f}s — score: {legacy_result['score']:.4f}"
        )

    # ── Compare ──
    print(f"\n{'─' * 60}")
    print("  COMPARISON")
    print(f"{'─' * 60}")

    print(f"\n  {'':20s} {'NEW':>10s}  {'LEGACY':>10s}  {'DELTA':>10s}")
    print(f"  {'─' * 52}")

    if legacy_result:
        delta = abs(new_result["score"] - legacy_result["score"])
        status = "PASS" if delta < 0.15 else "WARN" if delta < 0.25 else "FAIL"
        print(
            f"  {'Score':20s} {new_result['score']:10.4f}  {legacy_result['score']:10.4f}  {delta:10.4f}  [{status}]"
        )
        print(
            f"  {'Cost ($)':20s} {new_result['cost']:10.4f}  {legacy_result['cost']:10.4f}"
        )
    else:
        print(f"  {'Score':20s} {new_result['score']:10.4f}  {'N/A':>10s}")
        print(f"  {'Cost ($)':20s} {new_result['cost']:10.4f}  {'N/A':>10s}")

    if verbose:
        print("\n  [NEW] Steps:")
        for s in new_result.get("steps", []):
            print(f"    - {s}")
        print(f"\n  [NEW] Reason: {new_result['reason'][:300]}")
        if legacy_result:
            print("\n  [LEGACY] Steps:")
            for s in legacy_result.get("steps", []):
                print(f"    - {s}")
            print(f"\n  [LEGACY] Reason: {legacy_result['reason'][:300]}")

    print(f"\n{'=' * 60}")
    if legacy_result:
        delta = abs(new_result["score"] - legacy_result["score"])
        if delta < 0.15:
            print("  RESULT: ALIGNED (delta < 0.15)")
        elif delta < 0.25:
            print(f"  RESULT: MARGINAL (delta = {delta:.4f}, threshold = 0.25)")
        else:
            print(f"  RESULT: DIVERGENT (delta = {delta:.4f})")
    else:
        print("  RESULT: Legacy not available — new-only validation")
    print(f"{'=' * 60}\n")


def run_7d_test(model_name: str, verbose: bool = False):
    """Run the full 7D orchestrator (new implementation only).

    Tests Phase 1 caching + 3x shuffled runs + score aggregation.
    """
    print(f"\n{'=' * 60}")
    print("  7D Orchestrator Test (DeepEval-free)")
    print(f"  Model: {model_name}")
    print(f"  Persona: {_TEST_PERSONA}")
    print("  Runs: 3 shuffled × 7 dims = 21 judge calls")
    print(f"{'=' * 60}\n")

    from evaluation.ewan_eval.tutor_conv_geval import (
        compute_tutor_score,
        evaluate_tutor_dimensions,
    )

    t0 = time.time()
    dim_scores = evaluate_tutor_dimensions(
        conversation_turns=_TEST_CONVERSATION,
        persona_level=_TEST_PERSONA,
        model=model_name,
        num_judge_runs=3,
    )
    elapsed = time.time() - t0

    print(f"\n{'─' * 60}")
    print("  7D DIMENSION SCORES")
    print(f"{'─' * 60}")
    for dim in [
        "D1_level_detection",
        "D2_language_adaptation",
        "D3_scaffolding_calibration",
        "D4_domain_accuracy",
        "D5_code_teaching",
        "D6_empathetic_response",
        "D7_safety_boundaries",
    ]:
        score = dim_scores.get(dim)
        if score is not None:
            print(f"  {dim:35s} {score:.4f}")
        else:
            print(f"  {dim:35s} (skipped)")

    aggregate = compute_tutor_score(dim_scores)
    cost = dim_scores.get("_eval_cost", 0.0)
    fallbacks = dim_scores.get("_fallback_count", 0)

    print(f"{'─' * 60}")
    print(f"  {'AGGREGATE':35s} {aggregate:.4f}")
    print(f"  {'Cost ($)':35s} {cost:.4f}")
    print(f"  {'Time (s)':35s} {elapsed:.1f}")
    print(f"  {'Fallbacks':35s} {fallbacks}")

    if verbose:
        reasons = dim_scores.get("_dim_reasons", {})
        if reasons:
            print("\n  REASONS:")
            for dim, reason in reasons.items():
                print(f"    {dim[:2]}: {reason[:200]}")
        per_run = dim_scores.get("_per_run_scores", {})
        if per_run:
            print("\n  PER-RUN SCORES:")
            for dim, models in per_run.items():
                for mname, runs in models.items():
                    scores = [r["score"] for r in runs.values()]
                    print(f"    {dim[:2]} [{mname}]: {scores}")

    print(f"\n{'=' * 60}")
    print(f"  RESULT: 7D evaluation complete — aggregate = {aggregate:.4f}")
    print(f"{'=' * 60}\n")

    return dim_scores


def main():
    parser = argparse.ArgumentParser(description="A/B alignment test")
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (OpenRouter format, e.g. anthropic/claude-sonnet-4.6)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--mode",
        choices=["single", "7d", "both"],
        default="single",
        help="single: A/B on 1 dimension; 7d: full 7D orchestrator; both: run both",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get(
        "ANTHROPIC_OAUTH_TOKEN"
    ):
        print("ERROR: Set OPENROUTER_API_KEY or ANTHROPIC_OAUTH_TOKEN")
        sys.exit(1)

    if args.mode in ("single", "both"):
        asyncio.run(run_alignment_test(args.model, verbose=args.verbose))

    if args.mode in ("7d", "both"):
        run_7d_test(args.model, verbose=args.verbose)


if __name__ == "__main__":
    main()
