"""Comprehensive user simulator determinism test.

Tests whether the user simulator produces consistent outputs
given identical conversation history, across different:
- Task categories (B, D, X, I, S)
- Personas (beginner, intermediate, advanced)
- Truncation depths (after turn 1, after turn 2)

Run:  python -m tests.test_user_determinism_v2
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

N_REPEATS = 3


def _find_test_cases():
    """Select run_states covering categories × personas × difficulties."""
    cases = []
    base_dirs = {
        "sonnet": "results/run-single/anthropic/claude-sonnet-4-6",
        "haiku": "results/run-single/anthropic/claude-haiku-4-5-20251001",
        "sonnet_orig": "results/run-single/anthropic/claude-sonnet-4-6_original",
    }

    # Target: pick specific run_states that give us persona diversity
    targets = [
        # (category, task_id, persona, source, min_turns)
        ("backtest", "B01_interpret_metrics", "intermediate_developer", "sonnet", 4),
        ("backtest", "B01_interpret_metrics", "beginner_no_finance", "sonnet_orig", 4),
        ("backtest", "B01_interpret_metrics", "advanced_quant", "sonnet_orig", 4),
        (
            "data_analysis",
            "D01_load_inspect_ohlcv",
            "intermediate_developer",
            "sonnet",
            4,
        ),
        (
            "data_analysis",
            "D01_load_inspect_ohlcv",
            "beginner_no_finance",
            "sonnet_orig",
            4,
        ),
        ("debug", "X01_ma_offbyone", "intermediate_developer", "sonnet", 4),
        ("debug", "X01_ma_offbyone", "beginner_no_finance", "sonnet_orig", 4),
        ("implementation", "I01_implement_sma", "intermediate_developer", "sonnet", 4),
        (
            "implementation",
            "I01_implement_sma",
            "beginner_no_finance",
            "sonnet_orig",
            4,
        ),
        ("implementation", "I01_implement_sma", "advanced_quant", "sonnet_orig", 4),
        ("strategy", "S01_ma_crossover", "intermediate_developer", "sonnet", 4),
        ("strategy", "S01_ma_crossover", "advanced_quant", "sonnet_orig", 4),
    ]

    for cat, task_id, persona, source, min_turns in targets:
        base = base_dirs[source]
        # Try multiple path patterns
        patterns = [
            f"{base}/{cat}/{task_id}/{persona}/run_state.json",
            f"{base}/{cat}/{task_id}/{persona}_run1/run_state.json",
        ]
        # For sonnet_orig, also try _latest variants
        if source == "sonnet_orig":
            patterns.extend(
                [
                    f"{base}/{cat}/{task_id}_latest/{persona}/run_state.json",
                    f"{base}/{cat}_newest/{task_id}/{persona}/run_state.json",
                ]
            )
            # Also try strategy_old
            patterns.append(f"{base}/strategy_old/{task_id}/{persona}/run_state.json")

        found = None
        for p in patterns:
            if os.path.exists(p):
                with open(p) as f:
                    state = json.load(f)
                conv = state.get("conversation", [])
                if len(conv) >= min_turns:
                    found = p
                    break

        if found:
            cases.append(
                {
                    "category": cat,
                    "task_id": task_id,
                    "persona": persona,
                    "state_path": found,
                    "source": source,
                }
            )
        else:
            print(f"  ⚠ Not found: {cat}/{task_id}/{persona} (source={source})")

    return cases


def run_test(case, truncate_after_turn):
    """Run determinism test for a single case at a specific truncation point."""
    from config.llm_config import SIMULATOR_DEFAULT_MODEL
    from config.model_resolver import resolve_deepeval_model
    from config.prompt_config import build_scenario, build_user_description
    from deepeval.dataset import ConversationalGolden
    from deepeval.test_case import Turn
    from orchestrator.schemas import QuantTutorTask, UserPersona

    # Load state
    with open(case["state_path"]) as f:
        state = json.load(f)
    conversation = state["conversation"]
    task_id = state.get("task_id", case["task_id"])
    persona_id = state.get("persona_id", case["persona"])

    # Load task
    task = None
    for task_path in Path("tasks/layer2").rglob("*.json"):
        with open(task_path) as f:
            d = json.load(f)
        if d.get("task_id") == task_id:
            task = QuantTutorTask(**d)
            break
    if not task:
        return None

    # Load persona
    persona_path = Path(f"personas/{persona_id}.json")
    if not persona_path.exists():
        return None
    with open(persona_path) as f:
        persona = UserPersona(**json.load(f))

    # Build golden
    golden = ConversationalGolden(
        scenario=build_scenario(task, persona_id, has_incremental_tc=True),
        expected_outcome=(
            task.ground_truth.termination_criteria if task.ground_truth else ""
        ),
        user_description=build_user_description(persona, has_incremental_tc=True),
    )

    # Truncate conversation
    n_messages = truncate_after_turn * 2
    if len(conversation) < n_messages + 1:
        return None

    fixed_history = [
        Turn(role=msg["role"], content=msg["content"])
        for msg in conversation[:n_messages]
    ]

    # Resolve model
    model = resolve_deepeval_model(SIMULATOR_DEFAULT_MODEL)

    # Use template + model.generate directly (same LLM call as simulator internals)
    import re as _re

    from deepeval.simulator.template import ConversationSimulatorTemplate

    template = ConversationSimulatorTemplate()

    outputs = []
    for _ in range(N_REPEATS):
        try:
            prompt = template.simulate_user_turn(golden, fixed_history, "english")
            result = model.generate(prompt)
            text = result[0] if isinstance(result, tuple) else result
            # Extract simulated_input from JSON (same as DeepEval's generate_schema)
            match = _re.search(
                r'"simulated_input"\s*:\s*"((?:[^"\\]|\\.)*)"', text, _re.DOTALL
            )
            if match:
                output = match.group(1).replace('\\"', '"').replace("\\n", "\n")
            else:
                output = text.strip()[:300]
            outputs.append(output)
        except Exception as e:
            outputs.append(f"[ERROR: {e}]")

    return outputs


def classify_consistency(outputs):
    """Classify the consistency level of outputs."""
    if all(o == outputs[0] for o in outputs):
        return "A_exact", 1.0
    # Check semantic similarity via shared keywords
    words_sets = [set(o.lower().split()) for o in outputs if not o.startswith("[ERROR")]
    if len(words_sets) < 2:
        return "D_error", 0.0
    # Jaccard similarity between all pairs
    similarities = []
    for i in range(len(words_sets)):
        for j in range(i + 1, len(words_sets)):
            intersection = len(words_sets[i] & words_sets[j])
            union = len(words_sets[i] | words_sets[j])
            similarities.append(intersection / union if union > 0 else 0)
    avg_sim = sum(similarities) / len(similarities)

    if avg_sim > 0.6:
        return "B_semantic", avg_sim
    elif avg_sim > 0.3:
        return "C_topic_varies", avg_sim
    else:
        return "D_divergent", avg_sim


if __name__ == "__main__":
    print("=" * 70)
    print("USER SIMULATOR DETERMINISM TEST v2")
    print(f"Repeats per test: {N_REPEATS}")
    print("=" * 70)
    print()

    cases = _find_test_cases()
    print(f"Found {len(cases)} test cases\n")

    results = []
    total_calls = 0

    for case in cases:
        for trunc in [1, 2]:
            label = f"{case['category']}/{case['task_id'][:3]}/{case['persona'][:4]}/t{trunc}"
            print(f"--- {label} ---")

            outputs = run_test(case, trunc)
            if outputs is None:
                print("  SKIP (insufficient data)")
                continue

            total_calls += len(outputs)
            level, sim = classify_consistency(outputs)

            for i, o in enumerate(outputs):
                preview = o[:90].replace("\n", " ")
                print(f"  [{i+1}] {preview}{'...' if len(o) > 90 else ''}")

            print(f"  → {level} (similarity={sim:.2f})")
            results.append(
                {
                    "category": case["category"],
                    "task": case["task_id"],
                    "persona": case["persona"],
                    "truncation": trunc,
                    "level": level,
                    "similarity": sim,
                    "outputs": outputs,
                }
            )
            print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total API calls: {total_calls}")
    print()

    level_counts = defaultdict(int)
    for r in results:
        level_counts[r["level"]] += 1

    print("Consistency levels:")
    for level in sorted(level_counts):
        desc = {
            "A_exact": "Exact match (fully deterministic)",
            "B_semantic": "Same intent, different wording",
            "C_topic_varies": "Different topic selection",
            "D_divergent": "Substantially different",
            "D_error": "Error occurred",
        }
        print(
            f"  {level}: {level_counts[level]}/{len(results)} ({desc.get(level, '')})"
        )

    # Breakdown by dimension
    print()
    print("By category:")
    for cat in ["backtest", "data_analysis", "debug", "implementation", "strategy"]:
        cat_results = [r for r in results if r["category"] == cat]
        if cat_results:
            avg_sim = sum(r["similarity"] for r in cat_results) / len(cat_results)
            print(f"  {cat:<20} avg_similarity={avg_sim:.2f} (n={len(cat_results)})")

    print()
    print("By persona:")
    for persona in ["beginner_no_finance", "intermediate_developer", "advanced_quant"]:
        p_results = [r for r in results if r["persona"] == persona]
        if p_results:
            avg_sim = sum(r["similarity"] for r in p_results) / len(p_results)
            print(f"  {persona:<25} avg_similarity={avg_sim:.2f} (n={len(p_results)})")

    print()
    print("By truncation depth:")
    for trunc in [1, 2]:
        t_results = [r for r in results if r["truncation"] == trunc]
        if t_results:
            avg_sim = sum(r["similarity"] for r in t_results) / len(t_results)
            print(
                f"  After turn {trunc}: avg_similarity={avg_sim:.2f} (n={len(t_results)})"
            )
