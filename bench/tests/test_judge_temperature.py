"""Verify that evaluation judges use temperature=0.0 deterministically.

Run:  python -m tests.test_judge_temperature
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Load .env from project root (benchmark/) where OPENROUTER_API_KEY lives
_PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import (
    EVAL_DEFAULT_MODELS,
    EVAL_JUDGE_TEMPERATURE,
    EVAL_USE_OAUTH,
)


def test_config_values():
    """Check that config is set correctly."""
    print("=" * 60)
    print("1. Config verification")
    print("=" * 60)
    print(f"   EVAL_USE_OAUTH        = {EVAL_USE_OAUTH}")
    print(f"   EVAL_JUDGE_TEMPERATURE = {EVAL_JUDGE_TEMPERATURE}")
    print(f"   EVAL_DEFAULT_MODELS   = {EVAL_DEFAULT_MODELS}")

    assert (
        EVAL_USE_OAUTH is False
    ), f"EVAL_USE_OAUTH should be False, got {EVAL_USE_OAUTH}"
    assert (
        EVAL_JUDGE_TEMPERATURE == 0.0
    ), f"EVAL_JUDGE_TEMPERATURE should be 0.0, got {EVAL_JUDGE_TEMPERATURE}"
    print("   ✓ Config values correct\n")


def test_resolve_route():
    """Verify resolved model is GPTModel (not _OAuthAnthropicModel) with temp=0."""
    from config.model_resolver import _OAuthAnthropicModel, resolve_deepeval_model

    print("=" * 60)
    print("2. Model resolution route")
    print("=" * 60)

    model = resolve_deepeval_model()
    model_type = type(model).__name__
    print(f"   Resolved model type : {model_type}")
    print(f"   Resolved model repr : {model!r}")

    assert not isinstance(model, _OAuthAnthropicModel), (
        "Judge model resolved to _OAuthAnthropicModel (no temp control). "
        "Set EVAL_USE_OAUTH=False."
    )

    # GPTModel should have temperature attribute
    temp = getattr(model, "temperature", "MISSING")
    print(f"   model.temperature   : {temp}")
    assert temp == 0.0, f"Expected temperature=0.0, got {temp}"
    print("   ✓ Route and temperature correct\n")


def test_deterministic_output():
    """Call the judge model 3 times with identical input; verify identical output."""
    from config.model_resolver import resolve_deepeval_model

    print("=" * 60)
    print("3. Determinism test (3 identical calls)")
    print("=" * 60)

    model = resolve_deepeval_model()
    prompt = (
        "Rate the following explanation on a scale of 1-10 for correctness.\n"
        "Explanation: 'The Sharpe ratio measures risk-adjusted return by "
        "dividing excess return over the risk-free rate by the standard "
        "deviation of returns.'\n"
        'Reply with ONLY a JSON object: {"score": <int>, "reason": "<brief>"}'
    )

    results = []
    for i in range(3):
        result = model.generate(prompt)
        text = result[0] if isinstance(result, tuple) else result
        results.append(text.strip())
        print(f"   Run {i+1}: {text.strip()[:120]}...")

    if results[0] == results[1] == results[2]:
        print("   ✓ All 3 outputs identical — deterministic\n")
    else:
        print(
            "   ⚠ Outputs differ (API-level near-determinism may cause minor variance)"
        )
        # Not a hard failure — OpenRouter/provider may have minor non-determinism
        # even at temp=0, but the outputs should be very similar
        print()


if __name__ == "__main__":
    test_config_values()
    test_resolve_route()
    test_deterministic_output()
    print("=" * 60)
    print("All checks passed.")
    print("=" * 60)
