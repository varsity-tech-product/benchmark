"""Render judge prompts from conversation files for manual or agent evaluation.

Reads conversations from a directory, applies rubric prompt artifacts,
and writes rendered prompts + metadata to judge_inputs/ for batch processing.

Usage:
    python -m experiments.student_sim_stability.pipeline.render_judge_prompts \
        --conv-dir results/issue83/conversations \
        --output-dir results/issue83/judge_inputs --dimension D1
"""

import argparse
import json
import random
import sys
from pathlib import Path

from experiments.student_sim_stability.core.paths import BENCH_ROOT, EXPERIMENT_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (
    CONTROL_OPENING_SOURCE,
    FIXTURE_OPENING_SOURCE,
    REPEATS,
    STUDENT_MODEL_SOURCE,
)
from experiments.student_sim_stability.core.contracts import (
    list_persona_contracts,
    load_persona_contract,
    load_student_persona,
    render_persona_contract_text,
)
from experiments.student_sim_stability.core.rubrics import (
    rubric_metadata,
    rubric_prompt_template,
)
from server.schemas import QuantTutorTask, StudentPersona

_D1_PROMPT = rubric_prompt_template("D1")
_D2_PROMPT = rubric_prompt_template("D2")
_D3_PROMPT = rubric_prompt_template("D3")
_D4_PROMPT = rubric_prompt_template("D4")
_DISTINGUISH_PROMPT = rubric_prompt_template("control")
_P1_PROMPT = rubric_prompt_template("P1")
_B1_PROMPT = rubric_prompt_template("B1")
_SYSTEM_LABELS = ["System A", "System B", "System C", "System D", "System E"]
_D4_CONTEXT_CHAR_LIMIT = 700


def _load_persona(persona_id: str) -> StudentPersona:
    return load_student_persona(persona_id)


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


def _generated_student_turns(
    conv: list[dict], source_file: str = ""
) -> list[tuple[int, dict]]:
    """Return model-generated student turns and reject untagged user turns."""
    turns: list[tuple[int, dict]] = []
    for idx, turn in enumerate(conv):
        if turn.get("role") != "user":
            continue
        source = turn.get("source")
        if source == STUDENT_MODEL_SOURCE:
            turns.append((idx, turn))
            continue
        if source in {FIXTURE_OPENING_SOURCE, CONTROL_OPENING_SOURCE}:
            continue
        location = f"{source_file}: " if source_file else ""
        raise ValueError(
            f"{location}user turn {idx} has missing/unknown source {source!r}"
        )
    return turns


def _generated_student_messages(conv: list[dict], source_file: str = "") -> list[str]:
    return [turn["content"] for _, turn in _generated_student_turns(conv, source_file)]


def _quoted_excerpt(content: str, limit: int) -> str:
    """Return a JSON-quoted prompt excerpt with stable truncation."""
    if len(content) > limit:
        content = content[: limit - 3] + "..."
    return json.dumps(content, ensure_ascii=False)


def _d4_conversation_context(
    conv: list[dict], source_file: str = ""
) -> tuple[str, int]:
    """Format the full conversation while marking only generated student turns scored."""
    lines: list[str] = []
    scored_student_turn = 0
    tutor_turn = 0

    for idx, turn in enumerate(conv):
        role = turn.get("role")
        source = turn.get("source")
        content = turn.get("content", "")

        if role == "assistant":
            tutor_turn += 1
            label = f"Context only - tutor turn {tutor_turn}"
        elif role == "user" and source == STUDENT_MODEL_SOURCE:
            scored_student_turn += 1
            label = f"Scored student turn {scored_student_turn}"
        elif role == "user" and source == FIXTURE_OPENING_SOURCE:
            label = "Context only - scripted student opening"
        elif role == "user" and source == CONTROL_OPENING_SOURCE:
            label = "Context only - neutral control opening"
        elif role == "user":
            location = f"{source_file}: " if source_file else ""
            raise ValueError(
                f"{location}user turn {idx} has missing/unknown source {source!r}"
            )
        else:
            label = f"Context only - {role or 'unknown'} turn {idx}"

        lines.append(f"{label}: {_quoted_excerpt(content, _D4_CONTEXT_CHAR_LIMIT)}")

    return "\n".join(lines), scored_student_turn


def _contract_metadata(persona_id: str) -> dict:
    contract = load_persona_contract(persona_id)
    return {
        "persona_contract_id": contract["persona_id"],
        "persona_contract_version": contract["contract_version"],
    }


def _rubric_prompt(dimension: str, prompt: str) -> tuple[dict, str]:
    metadata = rubric_metadata(dimension)
    header = (
        "## Rubric Metadata\n"
        f"rubric_id: {metadata['rubric_id']}\n"
        f"rubric_version: {metadata['rubric_version']}\n\n"
    )
    return metadata, header + prompt


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

        student_turns = _generated_student_turns(conv, conv_file.name)
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
            rubric_meta, prompt = _rubric_prompt("D1", prompt)

            eval_id = f"D1__{conv_file.stem}__turn{turn_idx}"
            out = {
                "eval_id": eval_id,
                "dimension": "D1",
                "rubric_id": rubric_meta["rubric_id"],
                "rubric_version": rubric_meta["rubric_version"],
                "prompt": prompt,
                "metadata": {
                    **meta,
                    **rubric_meta,
                    **_contract_metadata(pid),
                    "turn_index": turn_idx,
                    "conversation_index": conv_idx,
                    "source_file": conv_file.name,
                    "student_turn_source": STUDENT_MODEL_SOURCE,
                    "excluded_opening_sources": [
                        FIXTURE_OPENING_SOURCE,
                        CONTROL_OPENING_SOURCE,
                    ],
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

        context_text, scored_turn_count = _d4_conversation_context(conv, conv_file.name)
        if not scored_turn_count:
            continue

        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)

        prompt = _D4_PROMPT.format(
            persona_description=persona.description,
            known_concepts=known,
            unknown_concepts=unknown,
            emotional_profile=persona.emotional_profile,
            student_messages_text=context_text,
            total_turns=scored_turn_count,
        )
        rubric_meta, prompt = _rubric_prompt("D4", prompt)

        eval_id = f"D4__{conv_file.stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "D4",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                **meta,
                **rubric_meta,
                **_contract_metadata(pid),
                "source_file": conv_file.name,
                "student_turn_source": STUDENT_MODEL_SOURCE,
                "student_turn_count": scored_turn_count,
                "non_scored_context_sources": [
                    "assistant",
                    FIXTURE_OPENING_SOURCE,
                    CONTROL_OPENING_SOURCE,
                ],
            },
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
            student_msgs = _generated_student_messages(conv, fname)
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
        rubric_meta, prompt = _rubric_prompt("D2", prompt)

        eval_id = f"D2__{group_key}"
        out = {
            "eval_id": eval_id,
            "dimension": "D2",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                **rubric_meta,
                **_contract_metadata(persona_id),
                "model": model,
                "tutor_temperature": tutor_t,
                "n_runs": len(convs),
                "source_files": [fname for fname, _ in convs],
                "student_turn_source": STUDENT_MODEL_SOURCE,
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
            student_msgs = _generated_student_messages(conv, fname)
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
        rubric_meta, prompt = _rubric_prompt("D3", prompt)

        eval_id = f"D3__{group_key}"
        out = {
            "eval_id": eval_id,
            "dimension": "D3",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                **rubric_meta,
                **_contract_metadata(persona_id),
                "repeat_tag": repeat_tag,
                "label_to_model": label_to_model,
                "source_files": [fname for _, (fname, _) in model_items],
                "student_turn_source": STUDENT_MODEL_SOURCE,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    print(f"D3: rendered {count} prompts")


def _student_messages_text(conv: list[dict]) -> str:
    student_msgs = _generated_student_messages(conv)
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
    missing_pairs: list[str] = []

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
                missing_pairs.append(control_file.name)
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
        rubric_meta, prompt = _rubric_prompt("control", prompt)

        out = {
            "eval_id": eval_id,
            "dimension": "control",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                "task_id": task_id,
                "persona_id": persona_id,
                **rubric_meta,
                **_contract_metadata(persona_id),
                "model": model,
                "repeat_tag": "r0_tt0",
                "persona_source_file": persona_file.name,
                "control_source_file": control_file.name,
                "persona_is_set_a": persona_is_set_a,
                "student_turn_source": STUDENT_MODEL_SOURCE,
                "excluded_opening_sources": [
                    FIXTURE_OPENING_SOURCE,
                    CONTROL_OPENING_SOURCE,
                ],
            },
        }
        with open(output_dir / f"{eval_id}.json", "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        count += 1

    if missing_pairs:
        sample = ", ".join(missing_pairs[:5])
        raise RuntimeError(
            "Control prompt rendering requires a matching live r*_tt0 conversation "
            f"for every control conversation; missing for {sample}"
        )

    print(f"control: rendered {count} prompts")


def render_p1(results_dir: Path, output_dir: Path):
    """Render P1 targeted-probe judge prompts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    response_dir = results_dir / "probes" / "responses"
    count = 0
    for response_file in sorted(response_dir.glob("*.json")):
        with open(response_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        persona_id = payload.get("persona_id", "")
        if not persona_id:
            continue
        conversation = payload.get("conversation") or []
        probe_message = ""
        if conversation:
            probe_message = conversation[0].get("content", "")
        student_response = (payload.get("turn") or {}).get("content", "")
        if not student_response:
            continue
        prompt = _P1_PROMPT.format(
            persona_contract=render_persona_contract_text(persona_id),
            probe_facet=payload.get("facet", ""),
            probe_message=probe_message[:800],
            student_response=student_response[:1200],
        )
        rubric_meta, prompt = _rubric_prompt("P1", prompt)
        model = payload.get("student_model", "")
        eval_id = f"P1__{response_file.stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "P1",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                **rubric_meta,
                **_contract_metadata(persona_id),
                "persona_id": persona_id,
                "model": model.split("/")[-1] if "/" in model else model,
                "probe_id": payload.get("probe_id"),
                "facet": payload.get("facet"),
                "source_file": str(response_file.relative_to(results_dir)),
                "student_turn_source": STUDENT_MODEL_SOURCE,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        count += 1
    print(f"P1: rendered {count} prompts")


def render_b1(results_dir: Path, output_dir: Path):
    """Render B1 blind persona-identification prompts from scripted dialogues."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dialogue_dir = results_dir / "scripted" / "conversations"
    candidate_contracts = "\n\n".join(
        render_persona_contract_text(contract["persona_id"])
        for contract in list_persona_contracts()
    )
    count = 0
    for dialogue_file in sorted(dialogue_dir.glob("*.json")):
        with open(dialogue_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        persona_id = payload.get("persona_id", "")
        conversation = payload.get("conversation") or []
        student_turns = [
            turn.get("content", "")
            for turn in conversation
            if turn.get("role") == "user" and turn.get("source") == STUDENT_MODEL_SOURCE
        ]
        if not persona_id or not student_turns:
            continue
        transcript = "\n".join(
            f'Student turn {idx + 1}: "{text[:350]}"'
            for idx, text in enumerate(student_turns)
        )
        context_label = (
            f"script={payload.get('script_id')}; pressure={payload.get('pressure')}"
        )
        prompt = _B1_PROMPT.format(
            candidate_contracts=candidate_contracts,
            context_label=context_label,
            transcript=transcript,
        )
        rubric_meta, prompt = _rubric_prompt("B1", prompt)
        model = payload.get("student_model", "")
        eval_id = f"B1__{dialogue_file.stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "B1",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                **rubric_meta,
                **_contract_metadata(persona_id),
                "persona_id": persona_id,
                "model": model.split("/")[-1] if "/" in model else model,
                "script_id": payload.get("script_id"),
                "pressure": payload.get("pressure"),
                "source_file": str(dialogue_file.relative_to(results_dir)),
                "student_turn_source": STUDENT_MODEL_SOURCE,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        count += 1
    print(f"B1: rendered {count} prompts")


def clean_rendered_prompts(output_dir: Path, dimension: str) -> int:
    """Remove previously rendered prompt files for a dimension."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefixes = (
        ["D1", "D2", "D3", "D4", "control", "P1", "B1"]
        if dimension == "all"
        else [dimension]
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
        choices=["D1", "D2", "D3", "D4", "control", "P1", "B1", "all"],
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

    base = EXPERIMENT_ROOT
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
    results_dir = conv_dir.parent
    if args.dimension in ("P1", "all"):
        render_p1(results_dir, output_dir)
    if args.dimension in ("B1", "all"):
        render_b1(results_dir, output_dir)


if __name__ == "__main__":
    main()
