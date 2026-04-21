"""Render judge prompts from conversation files for manual or agent evaluation.

Reads conversations from a directory, applies evaluator prompt templates,
and writes rendered prompts + metadata to judge_inputs/ for batch processing.

Usage:
    python -m experiments.student_sim_stability.render_judge_prompts \
        --conv-dir pilot --output-dir pilot/judge_inputs --dimension D1
"""

import argparse
import json
import random
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import REPEATS
from experiments.student_sim_stability.evaluator import (
    _D1_PROMPT,
    _D2_PROMPT,
    _D3_PROMPT,
    _D4_PROMPT,
    _DISTINGUISH_PROMPT,
    _SYSTEM_LABELS,
)
from server.schemas import QuantTutorTask, StudentPersona


def _load_persona(persona_id: str) -> StudentPersona:
    path = BENCH_ROOT / "personas" / f"{persona_id}.json"
    with open(path) as f:
        return StudentPersona(**json.load(f))


def _load_task(task_id: str) -> QuantTutorTask:
    """Find and load a task JSON by task_id."""
    tasks_dir = BENCH_ROOT / "tasks" / "layer2"
    for cat_dir in tasks_dir.iterdir():
        if cat_dir.is_dir():
            for f in cat_dir.glob("*.json"):
                if task_id in f.stem:
                    with open(f) as fh:
                        return QuantTutorTask(**json.load(fh))
    raise FileNotFoundError(f"Task {task_id} not found")


def _parse_conv_filename(name: str) -> dict:
    """Extract metadata from conversation filename."""
    parts = name.replace(".json", "").split("__")
    meta = {"phase": parts[0]}
    if len(parts) >= 4:
        meta["task_id"] = parts[1]
        meta["persona_id"] = parts[2]
        meta["model"] = parts[3] if len(parts) > 3 else ""
    if len(parts) >= 5:
        meta["repeat_tag"] = parts[4]
    return meta


def render_d1(conv_dir: Path, output_dir: Path, sample_policy: str = "all"):
    """Render D1 prompts: one per student message."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for conv_file in sorted(conv_dir.glob("*.json")):
        meta = _parse_conv_filename(conv_file.name)
        if sample_policy == "live-r0-tt0" and not (
            meta.get("phase") == "live" and meta.get("repeat_tag") == "r0_tt0"
        ):
            continue

        pid = meta.get("persona_id", "")
        if not pid:
            continue

        try:
            persona = _load_persona(pid)
        except FileNotFoundError:
            continue

        with open(conv_file) as f:
            conv = json.load(f)

        student_turns = [(i, t) for i, t in enumerate(conv) if t["role"] == "user"]
        total = len(student_turns)

        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)
        rules = "\n".join(f"  - {r}" for r in persona.behavioral_rules) or "  (none)"

        for turn_idx, (conv_idx, turn) in enumerate(student_turns):
            tutor_msg = ""
            if conv_idx > 0 and conv[conv_idx - 1]["role"] == "assistant":
                tutor_msg = conv[conv_idx - 1]["content"]

            prompt = _D1_PROMPT.format(
                persona_description=persona.description,
                emotional_profile=persona.emotional_profile,
                known_concepts=known,
                unknown_concepts=unknown,
                behavioral_rules=rules,
                turn_number=turn_idx + 1,
                total_turns=total,
                tutor_message=tutor_msg[:500],
                student_message=turn["content"][:500],
            )

            eval_id = f"D1__{conv_file.stem}__turn{turn_idx}"
            out = {
                "eval_id": eval_id,
                "dimension": "D1",
                "prompt": prompt,
                "metadata": {
                    **meta,
                    "turn_index": turn_idx,
                    "source_file": conv_file.name,
                },
            }
            with open(output_dir / f"{eval_id}.json", "w") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            count += 1

    print(f"D1: rendered {count} prompts")


def render_d4(conv_dir: Path, output_dir: Path):
    """Render D4 prompts: one per conversation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for conv_file in sorted(conv_dir.glob("*.json")):
        meta = _parse_conv_filename(conv_file.name)
        pid = meta.get("persona_id", "")
        if not pid:
            continue

        try:
            persona = _load_persona(pid)
        except FileNotFoundError:
            continue

        with open(conv_file) as f:
            conv = json.load(f)

        student_msgs = [t["content"] for t in conv if t["role"] == "user"]
        if not student_msgs:
            continue

        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)
        msgs_text = "\n".join(
            f'Turn {i+1}: "{m[:300]}"' for i, m in enumerate(student_msgs)
        )

        prompt = _D4_PROMPT.format(
            persona_description=persona.description,
            known_concepts=known,
            unknown_concepts=unknown,
            emotional_profile=persona.emotional_profile,
            student_messages_text=msgs_text,
            total_turns=len(student_msgs),
        )

        eval_id = f"D4__{conv_file.stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "D4",
            "prompt": prompt,
            "metadata": {**meta, "source_file": conv_file.name},
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    print(f"D4: rendered {count} prompts")


def render_d2(conv_dir: Path, output_dir: Path):
    """Render D2 prompts: one per (task, persona, model, tutor_t) group of 3 repeats."""
    import re
    from collections import defaultdict

    output_dir.mkdir(parents=True, exist_ok=True)

    # Group conversations by (task, persona, model, tutor_t)
    groups: dict[str, list[tuple[str, list]]] = defaultdict(list)
    for conv_file in sorted(conv_dir.glob("live__*.json")):
        parts = conv_file.stem.split("__")
        # live__TASK__PERSONA__MODEL__rN_ttX
        if len(parts) < 5:
            continue
        task_id = parts[1]
        persona_id = parts[2]
        model = parts[3]
        repeat_tag = parts[4]  # e.g. "r0_tt0"
        tt_match = re.search(r"_tt(\d)", repeat_tag)
        tutor_t = f"tt{tt_match.group(1)}" if tt_match else "tt0"
        group_key = f"{task_id}__{persona_id}__{model}__{tutor_t}"

        with open(conv_file) as f:
            conv = json.load(f)
        groups[group_key].append((conv_file.name, conv))

    count = 0
    for group_key, convs in sorted(groups.items()):
        if len(convs) < REPEATS:
            continue  # need all repeats for the D2 rubric

        parts = group_key.split("__")
        task_id, persona_id, model, tutor_t = parts[0], parts[1], parts[2], parts[3]

        try:
            persona = _load_persona(persona_id)
            task = _load_task(task_id)
        except FileNotFoundError:
            continue

        runs_parts = []
        for i, (fname, conv) in enumerate(convs):
            student_msgs = [t["content"] for t in conv if t["role"] == "user"]
            runs_parts.append(
                f"### Run {i+1}\n"
                + "\n".join(
                    f'  Student turn {j+1}: "{m[:200]}"'
                    for j, m in enumerate(student_msgs)
                )
            )

        prompt = _D2_PROMPT.format(
            persona_description=persona.description,
            task_description=task.description,
            runs_text="\n\n".join(runs_parts),
        )

        eval_id = f"D2__{group_key}"
        out = {
            "eval_id": eval_id,
            "dimension": "D2",
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                "model": model,
                "tutor_temperature": tutor_t,
                "n_runs": len(convs),
                "source_files": [fname for fname, _ in convs],
            },
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    print(f"D2: rendered {count} prompts")


def render_d3(conv_dir: Path, output_dir: Path):
    """Render D3 prompts: one per (task, persona, tutor_t, repeat) comparing 3 models.

    Models are anonymized as System A/B/C with randomized order.
    """
    from collections import defaultdict

    output_dir.mkdir(parents=True, exist_ok=True)

    # Group by (task, persona, repeat_idx, tutor_t)
    # Each group should have up to 3 conversations (one per model)
    groups: dict[str, dict[str, tuple[str, list]]] = defaultdict(dict)
    for conv_file in sorted(conv_dir.glob("live__*.json")):
        parts = conv_file.stem.split("__")
        if len(parts) < 5:
            continue
        task_id = parts[1]
        persona_id = parts[2]
        model = parts[3]
        repeat_tag = parts[4]  # e.g. "r0_tt0"

        group_key = f"{task_id}__{persona_id}__{repeat_tag}"
        with open(conv_file) as f:
            conv = json.load(f)
        groups[group_key][model] = (conv_file.name, conv)

    count = 0
    for group_key, model_convs in sorted(groups.items()):
        if len(model_convs) < 3:
            continue

        parts = group_key.split("__")
        task_id, persona_id, repeat_tag = parts[0], parts[1], parts[2]

        try:
            persona = _load_persona(persona_id)
            task = _load_task(task_id)
        except FileNotFoundError:
            continue

        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)

        # Randomize model order for anonymization
        model_items = list(model_convs.items())
        random.Random(group_key).shuffle(model_items)

        label_to_model = {}
        models_parts = []
        for idx, (model_name, (fname, conv)) in enumerate(model_items):
            label = _SYSTEM_LABELS[idx]
            label_to_model[label] = model_name
            student_msgs = [t["content"] for t in conv if t["role"] == "user"]
            models_parts.append(
                f"### {label}\n"
                + "\n".join(
                    f'  Student turn {j+1}: "{m[:200]}"'
                    for j, m in enumerate(student_msgs)
                )
            )

        prompt = _D3_PROMPT.format(
            persona_description=persona.description,
            emotional_profile=persona.emotional_profile,
            known_concepts=known,
            unknown_concepts=unknown,
            task_description=task.description,
            models_text="\n\n".join(models_parts),
        )

        eval_id = f"D3__{group_key}"
        out = {
            "eval_id": eval_id,
            "dimension": "D3",
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                "repeat_tag": repeat_tag,
                "label_to_model": label_to_model,
                "source_files": [fname for _, (fname, _) in model_items],
            },
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    print(f"D3: rendered {count} prompts")


def _student_messages_text(conv: list[dict]) -> str:
    student_msgs = [t["content"] for t in conv if t["role"] == "user"]
    return "\n".join(
        f'  Turn {i+1}: "{message[:200]}"' for i, message in enumerate(student_msgs)
    )


def render_control(conv_dir: Path, output_dir: Path):
    """Render persona-vs-generic distinguishability prompts.

    Each control conversation is paired with the matching live r0/tutor-t0
    conversation for the same task, persona, and model.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for control_file in sorted(conv_dir.glob("control__*.json")):
        meta = _parse_conv_filename(control_file.name)
        task_id = meta.get("task_id", "")
        persona_id = meta.get("persona_id", "")
        model = meta.get("model", "")
        if not (task_id and persona_id and model):
            continue

        persona_file = conv_dir / (
            f"live__{task_id}__{persona_id}__{model}__r0_tt0.json"
        )
        if not persona_file.exists():
            matches = sorted(
                conv_dir.glob(f"live__{task_id}__{persona_id}__{model}__r*_tt0.json")
            )
            if not matches:
                continue
            persona_file = matches[0]

        with open(persona_file) as f:
            persona_conv = json.load(f)
        with open(control_file) as f:
            control_conv = json.load(f)

        eval_id = f"control__{task_id}__{persona_id}__{model}__r0_tt0"
        persona_is_set_a = random.Random(eval_id).choice([True, False])
        persona_msgs = _student_messages_text(persona_conv)
        control_msgs = _student_messages_text(control_conv)
        if persona_is_set_a:
            set_a, set_b = persona_msgs, control_msgs
        else:
            set_a, set_b = control_msgs, persona_msgs

        prompt = _DISTINGUISH_PROMPT.format(
            set_description=(
                "One set was produced with a detailed persona definition. "
                "The other used a generic student description. "
                "You do not know which is which."
            ),
            set_a_conversation=set_a,
            set_b_conversation=set_b,
        )

        out = {
            "eval_id": eval_id,
            "dimension": "control",
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                "model": model,
                "repeat_tag": "r0_tt0",
                "persona_source_file": persona_file.name,
                "control_source_file": control_file.name,
                "persona_is_set_a": persona_is_set_a,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    print(f"control: rendered {count} prompts")


def clean_rendered_prompts(output_dir: Path, dimension: str) -> int:
    """Remove previously rendered prompt files for a dimension."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefixes = (
        ["D1", "D2", "D3", "D4", "control"] if dimension == "all" else [dimension]
    )
    removed = 0
    for prefix in prefixes:
        for path in output_dir.glob(f"{prefix}__*.json"):
            path.unlink()
            removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--conv-dir", required=True, help="Conversation files directory"
    )
    parser.add_argument(
        "--output-dir", required=True, help="Output directory for rendered prompts"
    )
    parser.add_argument(
        "--dimension",
        default="all",
        choices=["D1", "D2", "D3", "D4", "control", "all"],
    )
    parser.add_argument(
        "--d1-sample-policy",
        default="all",
        choices=["all", "live-r0-tt0"],
        help="D1 prompt sampling policy. Use live-r0-tt0 for the report sample.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing rendered prompts for the selected dimension(s) first.",
    )
    args = parser.parse_args()

    base = Path(__file__).parent
    conv_dir = base / args.conv_dir
    output_dir = base / args.output_dir

    if args.clean:
        removed = clean_rendered_prompts(output_dir, args.dimension)
        print(f"clean: removed {removed} old prompts")

    if args.dimension in ("D1", "all"):
        render_d1(conv_dir, output_dir, sample_policy=args.d1_sample_policy)
    if args.dimension in ("D2", "all"):
        render_d2(conv_dir, output_dir)
    if args.dimension in ("D3", "all"):
        render_d3(conv_dir, output_dir)
    if args.dimension in ("D4", "all"):
        render_d4(conv_dir, output_dir)
    if args.dimension in ("control", "all"):
        render_control(conv_dir, output_dir)


if __name__ == "__main__":
    main()
