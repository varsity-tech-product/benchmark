"""Test LEAN code eval discrimination across different scenarios.

Run:  python -m tests.test_lean_code_eval_discrimination
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_log(name, result_text):
    log = MagicMock()
    log.name = name
    log.result = result_text
    log.args = {}
    log.success = "error" not in result_text.lower()
    return log


def _eval(trials_status_list):
    """Run LEAN code eval with given trial statuses."""
    from evaluation.code_eval import evaluate_code_combined

    logs = []
    for i, status in enumerate(trials_status_list):
        logs.append(
            _make_log(
                "run_lean_backtest",
                f"=== Trial {i+1} Result ===\nStatus: {status}\nTrades: {'1000' if status == 'success' else '0'}",
            )
        )
    return evaluate_code_combined(
        workspace_path="/tmp/fake",
        tool_logs=logs,
        reference=None,
        task_requires_code=True,
        is_lean_task=True,
    )


scenarios = [
    ("All compile errors", ["compile_error", "compile_error", "compile_error"]),
    ("Compile→Compile→Runtime", ["compile_error", "compile_error", "runtime_error"]),
    ("Compile→Runtime→Empty", ["compile_error", "runtime_error", "empty_trades"]),
    ("Compile→Empty→Success", ["compile_error", "empty_trades", "success"]),
    ("First try success", ["success"]),
    ("Empty→Empty→Success", ["empty_trades", "empty_trades", "success"]),
    ("All empty trades", ["empty_trades", "empty_trades", "empty_trades"]),
    ("Success→Compile (regression)", "success", "compile_error"),
]

if __name__ == "__main__":
    print(f"{'Scenario':<35} {'Trials':>8} {'A':>5} {'B':>5} {'Score':>6}")
    print("-" * 65)

    # Fix the tuple scenario
    scenarios_fixed = [
        ("All compile errors", ["compile_error"] * 3),
        (
            "Compile→Compile→Runtime",
            ["compile_error", "compile_error", "runtime_error"],
        ),
        ("Compile→Runtime→Empty", ["compile_error", "runtime_error", "empty_trades"]),
        ("Compile→Empty→Success", ["compile_error", "empty_trades", "success"]),
        ("First try success", ["success"]),
        ("5 failures then success", ["compile_error"] * 4 + ["success"]),
        ("Empty→Empty→Success", ["empty_trades", "empty_trades", "success"]),
        ("All empty trades", ["empty_trades"] * 3),
        ("Success→Compile (regress)", ["success", "compile_error"]),
        ("Runtime only", ["runtime_error"]),
    ]

    scores = []
    for name, trials in scenarios_fixed:
        r = _eval(trials)
        a = r["static_analysis"]["score"] if r["static_analysis"] else 0
        b = r["execution"]["score"] if r["execution"] else 0
        scores.append((name, len(trials), a, b, r["score"]))
        print(f"{name:<35} {len(trials):>8} {a:>5.2f} {b:>5.2f} {r['score']:>6.4f}")

    print()

    # Check discrimination
    unique_scores = set(s[4] for s in scores)
    print(f"Unique scores: {len(unique_scores)}/{len(scores)}")
    print(
        f"Score range: {min(s[4] for s in scores):.4f} - {max(s[4] for s in scores):.4f}"
    )

    # Key discrimination test
    first_try = [s for s in scores if s[0] == "First try success"][0][4]
    five_fail = [s for s in scores if s[0] == "5 failures then success"][0][4]
    print(f"\nFirst-try success vs 5-failures-then-success: {first_try} vs {five_fail}")
    if first_try == five_fail:
        print("  ⚠ No discrimination between efficient and inefficient agent")
    else:
        print(f"  Δ = {first_try - five_fail}")
