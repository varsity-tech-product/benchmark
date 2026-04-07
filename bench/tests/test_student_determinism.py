"""Test student simulator determinism: same history → same output.

Proves that conversation forking comes from agent behavior, not student randomness.

Run:  python -m tests.test_student_determinism
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def _find_run_states():
    """Find 3 diverse run_state.json files for testing."""
    base = Path("results/run-single/anthropic/claude-sonnet-4-6")
    candidates = []
    for scores_path in sorted(base.rglob("scores.md")):
        state_path = scores_path.parent / "run_state.json"
        if state_path.exists():
            # Get task category from path
            rel = str(scores_path.relative_to(base))
            category = rel.split("/")[0]
            task = rel.split("/")[1]
            candidates.append((category, task, str(state_path)))

    # Pick 1 from each category type
    selected = {}
    for cat, task, path in candidates:
        if cat not in selected:
            selected[cat] = (task, path)
        if len(selected) >= 3:
            break
    return list(selected.values())


def test_determinism(task_name, state_path, n_repeats=5, truncate_after_turn=2):
    """Fixed conversation history → generate next student message N times."""
    from config.llm_config import SIMULATOR_DEFAULT_MODEL
    from config.model_resolver import resolve_deepeval_model
    from config.prompt_config import build_scenario, build_user_description
    from deepeval.dataset import ConversationalGolden
    from deepeval.test_case import Turn
    from orchestrator.schemas import QuantTutorTask, StudentPersona

    # Load state
    with open(state_path) as f:
        state = json.load(f)

    conversation = state["conversation"]

    # We need the task and persona to rebuild the golden
    task_id = state.get("task_id", "")
    persona_id = state.get("persona_id", "intermediate_developer")

    # Find and load task JSON
    task = None
    for task_path in Path("tasks/layer2").rglob("*.json"):
        with open(task_path) as f:
            d = json.load(f)
        if d.get("task_id") == task_id:
            task = QuantTutorTask(**d)
            break

    if not task:
        print(f"  ⚠ Task {task_id} not found, skipping")
        return None

    # Load persona
    persona_path = Path(f"personas/{persona_id}.json")
    if not persona_path.exists():
        print(f"  ⚠ Persona {persona_id} not found, skipping")
        return None
    with open(persona_path) as f:
        persona = StudentPersona(**json.load(f))

    # Build golden (same as simulation.py)
    golden = ConversationalGolden(
        scenario=build_scenario(task, persona_id, has_incremental_tc=True),
        expected_outcome=(
            task.ground_truth.expected_outcome if task.ground_truth else ""
        ),
        user_description=build_user_description(persona, has_incremental_tc=True),
    )

    # Truncate conversation to fixed history
    # truncate_after_turn=2 means keep: student1, tutor1, student2, tutor2
    turns_to_keep = truncate_after_turn * 2  # each turn = 1 student + 1 tutor
    if len(conversation) < turns_to_keep + 1:
        print(f"  ⚠ Conversation too short ({len(conversation)} msgs), skipping")
        return None

    fixed_history = [
        Turn(role=msg["role"], content=msg["content"])
        for msg in conversation[:turns_to_keep]
    ]

    # Resolve model
    model = resolve_deepeval_model(SIMULATOR_DEFAULT_MODEL)

    # Create simulator template
    from deepeval.simulator.template import ConversationSimulatorTemplate

    template = ConversationSimulatorTemplate()

    # Generate next student message N times
    print(f"  Task: {task_id}, truncated after turn {truncate_after_turn}")
    print(f"  Fixed history: {len(fixed_history)} messages")
    print(f"  Model: {SIMULATOR_DEFAULT_MODEL}")
    print(f"  Generating {n_repeats} times...")

    outputs = []
    for i in range(n_repeats):
        prompt = template.simulate_user_turn(golden, fixed_history, "english")
        # Use model.generate directly (same as ConversationSimulator)
        result = model.generate(prompt)
        text = result[0] if isinstance(result, tuple) else result

        # Extract simulated_input from JSON response
        import re

        match = re.search(r'"simulated_input"\s*:\s*"(.*?)"', text, re.DOTALL)
        if match:
            output = match.group(1)
        else:
            # Try to extract from non-JSON response
            output = text.strip()[:200]

        outputs.append(output)
        print(f"    Run {i+1}: {output[:80]}{'...' if len(output) > 80 else ''}")

    # Compare
    all_same = all(o == outputs[0] for o in outputs)

    if all_same:
        print(f"  ✓ All {n_repeats} outputs IDENTICAL — student is deterministic")
    else:
        # Calculate pairwise similarity
        unique = set(outputs)
        print(f"  ⚠ {len(unique)} unique outputs out of {n_repeats}")
        # Edit distance between first and each other
        for i in range(1, len(outputs)):
            if outputs[i] != outputs[0]:
                # Simple char-level diff
                common = sum(a == b for a, b in zip(outputs[0], outputs[i]))
                similarity = common / max(len(outputs[0]), len(outputs[i])) * 100
                print(f"    Run 1 vs Run {i+1}: {similarity:.1f}% char similarity")

    return all_same


if __name__ == "__main__":
    print("=" * 60)
    print("STUDENT SIMULATOR DETERMINISM TEST")
    print("=" * 60)
    print()

    run_states = _find_run_states()
    if not run_states:
        print("No run_state.json files found!")
        sys.exit(1)

    results = []
    for task_name, state_path in run_states:
        print(f"\n--- Testing: {task_name} ---")
        result = test_determinism(
            task_name, state_path, n_repeats=5, truncate_after_turn=2
        )
        if result is not None:
            results.append((task_name, result))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    deterministic = sum(1 for _, r in results if r)
    total = len(results)
    print(f"Deterministic: {deterministic}/{total} tasks")
    if deterministic == total:
        print("✓ Student simulator is fully deterministic at temp=0")
        print("  → All conversation forking comes from agent behavior")
    else:
        print("⚠ Some non-determinism detected — investigate OpenRouter/model temp")
