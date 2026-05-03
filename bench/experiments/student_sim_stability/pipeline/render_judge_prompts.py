"""Render judge prompts from conversation files for manual or agent evaluation.

Reads conversations from a directory, applies rubric prompt artifacts,
and writes rendered prompts + metadata to judge_inputs/ for batch processing.

Usage:
    python -m experiments.student_sim_stability.pipeline.render_judge_prompts \
        --conv-dir results/main/conversations \
        --output-dir results/main/judge_inputs --dimension S1
"""

import argparse
import fnmatch
import functools
import json
import random
import re
import sys
from collections import defaultdict
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
from experiments.student_sim_stability.core.io_utils import load_json
from experiments.student_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
    rubric_metadata,
    rubric_prompt_template,
)
from experiments.student_sim_stability.pipeline._render_common import (
    TURN_EXCERPT_CHAR_LIMIT as _TURN_EXCERPT_CHAR_LIMIT,
)
from experiments.student_sim_stability.pipeline._render_common import (
    persona_block_kwargs as _persona_block_kwargs,
)
from experiments.student_sim_stability.pipeline._render_common import (
    truncate as _truncate,
)
from eval.contracts.schemas import QuantTutorTask, StudentPersona

_D1_PROMPT = rubric_prompt_template("S1")
_D2_PROMPT = rubric_prompt_template("S3")
_D3_PROMPT = rubric_prompt_template("S2")
_DISTINGUISH_PROMPT = rubric_prompt_template("S6")
_P1_PROMPT = rubric_prompt_template("S5")
_B1_PROMPT = rubric_prompt_template("S4")


def _load_persona(persona_id: str) -> StudentPersona:
    return load_student_persona(persona_id)


def load_conversations(conv_dir: Path) -> dict[str, list[dict]]:
    """Parse every conversation JSON once; key by filename."""
    return {path.name: load_json(path) for path in sorted(conv_dir.glob("*.json"))}


def _conversations_or_load(
    conv_dir: Path, conversations: dict[str, list[dict]] | None
) -> dict[str, list[dict]]:
    if conversations is None:
        return load_conversations(conv_dir)
    return conversations


@functools.lru_cache(maxsize=1)
def _task_path_by_id() -> dict[str, Path]:
    tasks_dir = BENCH_ROOT / "tasks" / "layer2"
    return {
        task_path.stem: task_path
        for cat_dir in sorted(tasks_dir.iterdir())
        if cat_dir.is_dir()
        for task_path in sorted(cat_dir.glob("*.json"))
    }


@functools.lru_cache(maxsize=None)
def _load_task(task_id: str) -> QuantTutorTask:
    """Find and load a task JSON by task_id. Cached for the duration of a run."""
    task_path = _task_path_by_id()[task_id]
    with open(task_path) as fh:
        return QuantTutorTask(**json.load(fh))


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


def _d3_conversation_context(
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

        lines.append(f"{label}: {_quoted_excerpt(content, _TURN_EXCERPT_CHAR_LIMIT)}")

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


def render_d1(
    conv_dir: Path,
    output_dir: Path,
    conversations: dict[str, list[dict]] | None = None,
    sample_policy: str = "all",
):
    """Render S1 prompts: one per student message."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    conversations = _conversations_or_load(conv_dir, conversations)

    for conv_name, conv in sorted(conversations.items()):
        meta = _parse_conv_filename(conv_name)
        if sample_policy == "live-r0-tt0" and not (
            meta.get("phase") == "live" and meta.get("repeat_tag") == "r0_tt0"
        ):
            continue

        pid = meta.get("persona_id", "")
        if not pid:
            continue

        try:
            persona_block = _persona_block_kwargs(pid)
        except FileNotFoundError:
            continue

        student_turns = _generated_student_turns(conv, conv_name)
        total = len(student_turns)

        for turn_idx, (conv_idx, turn) in enumerate(student_turns):
            tutor_msg = ""
            if conv_idx > 0 and conv[conv_idx - 1]["role"] == "assistant":
                tutor_msg = conv[conv_idx - 1]["content"]

            prompt = _D1_PROMPT.format(
                **persona_block,
                turn_number=turn_idx + 1,
                total_turns=total,
                tutor_message=_truncate(tutor_msg),
                student_message=_truncate(turn["content"]),
            )
            rubric_meta, prompt = _rubric_prompt("S1", prompt)

            eval_id = f"S1__{Path(conv_name).stem}__turn{turn_idx}"
            out = {
                "eval_id": eval_id,
                "dimension": "S1",
                "rubric_id": rubric_meta["rubric_id"],
                "rubric_version": rubric_meta["rubric_version"],
                "prompt": prompt,
                "metadata": {
                    **meta,
                    **rubric_meta,
                    **_contract_metadata(pid),
                    "turn_index": turn_idx,
                    "conversation_index": conv_idx,
                    "source_file": conv_name,
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

    print(f"S1: rendered {count} prompts")


def render_d3(
    conv_dir: Path,
    output_dir: Path,
    conversations: dict[str, list[dict]] | None = None,
):
    """Render S2 prompts: one per conversation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    conversations = _conversations_or_load(conv_dir, conversations)

    for conv_name, conv in sorted(conversations.items()):
        meta = _parse_conv_filename(conv_name)
        pid = meta.get("persona_id", "")
        if not pid:
            continue

        try:
            persona_block = _persona_block_kwargs(pid)
        except FileNotFoundError:
            continue

        context_text, scored_turn_count = _d3_conversation_context(conv, conv_name)
        if not scored_turn_count:
            continue

        prompt = _D3_PROMPT.format(
            **persona_block,
            student_messages_text=context_text,
            total_turns=scored_turn_count,
        )
        rubric_meta, prompt = _rubric_prompt("S2", prompt)

        eval_id = f"S2__{Path(conv_name).stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "S2",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                **meta,
                **rubric_meta,
                **_contract_metadata(pid),
                "source_file": conv_name,
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

    print(f"S2: rendered {count} prompts")


def render_d2(
    conv_dir: Path,
    output_dir: Path,
    conversations: dict[str, list[dict]] | None = None,
):
    """Render S3 prompts: one per (task, persona, model, tutor_t) group of 3 repeats."""
    output_dir.mkdir(parents=True, exist_ok=True)
    conversations = _conversations_or_load(conv_dir, conversations)

    groups: dict[str, list[tuple[str, list]]] = defaultdict(list)
    for conv_name, conv in sorted(conversations.items()):
        if not conv_name.startswith("live__"):
            continue
        parts = Path(conv_name).stem.split("__")
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

        groups[group_key].append((conv_name, conv))

    count = 0
    for group_key, convs in sorted(groups.items()):
        if len(convs) < REPEATS:
            continue  # need all repeats for the S3 rubric

        parts = group_key.split("__")
        task_id, persona_id, model, tutor_t = parts[0], parts[1], parts[2], parts[3]

        try:
            persona_block = _persona_block_kwargs(persona_id)
            task = _load_task(task_id)
        except FileNotFoundError:
            continue

        runs_parts = []
        for i, (fname, conv) in enumerate(convs):
            student_msgs = _generated_student_messages(conv, fname)
            runs_parts.append(
                f"### Run {i+1}\n"
                + "\n".join(
                    f'  Student turn {j+1}: "{_truncate(m)}"'
                    for j, m in enumerate(student_msgs)
                )
            )

        prompt = _D2_PROMPT.format(
            **persona_block,
            task_description=task.description,
            runs_text="\n\n".join(runs_parts),
        )
        rubric_meta, prompt = _rubric_prompt("S3", prompt)

        eval_id = f"S3__{group_key}"
        out = {
            "eval_id": eval_id,
            "dimension": "S3",
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

    print(f"S3: rendered {count} prompts")


def _student_messages_text(conv: list[dict]) -> str:
    student_msgs = _generated_student_messages(conv)
    return "\n".join(
        f'  Turn {i+1}: "{_truncate(message)}"'
        for i, message in enumerate(student_msgs)
    )


def render_control(
    conv_dir: Path,
    output_dir: Path,
    conversations: dict[str, list[dict]] | None = None,
):
    """Render persona-vs-generic distinguishability prompts.

    Each control conversation is paired with the matching live r0/tutor-t0
    conversation for the same task, persona, and model.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    missing_pairs: list[str] = []
    conversations = _conversations_or_load(conv_dir, conversations)

    for control_name, control_conv in sorted(conversations.items()):
        if not control_name.startswith("control__"):
            continue
        meta = _parse_conv_filename(control_name)
        task_id = meta.get("task_id", "")
        persona_id = meta.get("persona_id", "")
        model = meta.get("model", "")
        if not (task_id and persona_id and model):
            continue

        persona_name = f"live__{task_id}__{persona_id}__{model}__r0_tt0.json"
        if persona_name not in conversations:
            pattern = f"live__{task_id}__{persona_id}__{model}__r*_tt0.json"
            matches = sorted(
                name for name in conversations if fnmatch.fnmatchcase(name, pattern)
            )
            if not matches:
                missing_pairs.append(control_name)
                continue
            persona_name = matches[0]

        persona_conv = conversations[persona_name]

        eval_id = f"S6__{task_id}__{persona_id}__{model}__r0_tt0"
        persona_is_set_a = random.Random(eval_id).choice([True, False])
        persona_msgs = _student_messages_text(persona_conv)
        control_msgs = _student_messages_text(control_conv)
        if persona_is_set_a:
            set_a, set_b = persona_msgs, control_msgs
        else:
            set_a, set_b = control_msgs, persona_msgs

        prompt = _DISTINGUISH_PROMPT.format(
            **_persona_block_kwargs(persona_id),
            set_description=(
                "One set was produced with a detailed persona definition. "
                "The other used a generic student description. "
                "You do not know which set is which."
            ),
            set_a_conversation=set_a,
            set_b_conversation=set_b,
        )
        rubric_meta, prompt = _rubric_prompt("S6", prompt)

        out = {
            "eval_id": eval_id,
            "dimension": "S6",
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
                "persona_source_file": persona_name,
                "control_source_file": control_name,
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

    print(f"S6: rendered {count} prompts")


def render_p1(results_dir: Path, output_dir: Path):
    """Render S5 targeted-probe judge prompts.

    Threads each probe's ``expected_signals`` list (persona-specific indirect
    signal markers authored in ``probes.py``) into the judge prompt so the
    judge anchors ``facet_fit`` to the designed signal instead of free-form
    reasoning about what the facet means.
    """
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
        expected_signals = payload.get("expected_signals") or []
        expected_signals_text = (
            "\n".join(f"- {signal}" for signal in expected_signals)
            if expected_signals
            else "- (no persona-specific indirect signals authored)"
        )
        prompt = _P1_PROMPT.format(
            persona_contract=render_persona_contract_text(persona_id),
            probe_facet=payload.get("facet", ""),
            probe_message=_truncate(probe_message),
            student_response=_truncate(student_response),
            expected_signals=expected_signals_text,
        )
        rubric_meta, prompt = _rubric_prompt("S5", prompt)
        model = payload.get("student_model", "")
        eval_id = f"S5__{response_file.stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "S5",
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
                "expected_signals": expected_signals,
                "source_file": str(response_file.relative_to(results_dir)),
                "student_turn_source": STUDENT_MODEL_SOURCE,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        count += 1
    print(f"S5: rendered {count} prompts")


def render_b1(
    conv_dir: Path,
    output_dir: Path,
    conversations: dict[str, list[dict]] | None = None,
):
    """Render S4 blind persona-identification prompts from live conversations.

    Source: ``conversations/live__*.json`` produced by ``ExperimentRunner``.
    Only model-generated student turns (``STUDENT_MODEL_SOURCE``) are included in
    the transcript; the fixture opening (``FIXTURE_OPENING_SOURCE``) and tutor
    turns are filtered out, so the S4 judge never sees persona-labeling text
    from the scripted opener or adaptive tutor references.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_contracts = "\n\n".join(
        render_persona_contract_text(contract["persona_id"])
        for contract in list_persona_contracts()
    )
    count = 0
    conversations = _conversations_or_load(conv_dir, conversations)
    for live_name, conv in sorted(conversations.items()):
        if not live_name.startswith("live__"):
            continue
        meta = _parse_conv_filename(live_name)
        task_id = meta.get("task_id", "")
        persona_id = meta.get("persona_id", "")
        model = meta.get("model", "")
        repeat_tag = meta.get("repeat_tag", "")
        if not (task_id and persona_id and model):
            continue
        student_msgs = _generated_student_messages(conv, live_name)
        if not student_msgs:
            continue
        transcript = "\n".join(
            f'Student turn {idx + 1}: "{_truncate(text)}"'
            for idx, text in enumerate(student_msgs)
        )
        context_label = f"task={task_id}; repeat_tag={repeat_tag}"
        prompt = _B1_PROMPT.format(
            candidate_contracts=candidate_contracts,
            context_label=context_label,
            transcript=transcript,
        )
        rubric_meta, prompt = _rubric_prompt("S4", prompt)
        eval_id = f"S4__{Path(live_name).stem}"
        out = {
            "eval_id": eval_id,
            "dimension": "S4",
            "rubric_id": rubric_meta["rubric_id"],
            "rubric_version": rubric_meta["rubric_version"],
            "prompt": prompt,
            "metadata": {
                **rubric_meta,
                **_contract_metadata(persona_id),
                "persona_id": persona_id,
                "task_id": task_id,
                "model": model,
                "repeat_tag": repeat_tag,
                "source_file": str((conv_dir / live_name).relative_to(conv_dir.parent)),
                "student_turn_source": STUDENT_MODEL_SOURCE,
            },
        }
        with open(output_dir / f"{eval_id}.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        count += 1
    print(f"S4: rendered {count} prompts")


def clean_rendered_prompts(output_dir: Path, dimension: str) -> int:
    """Remove previously rendered prompt files for a dimension."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefixes = tuple(DIMENSION_TO_FILE) if dimension == "all" else (dimension,)
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
        choices=(*DIMENSION_TO_FILE, "all"),
    )
    parser.add_argument(
        "--s1-sample-policy",
        default="all",
        choices=["all", "live-r0-tt0"],
        help="S1 prompt sampling policy. Use live-r0-tt0 for the report sample.",
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

    conversations = None
    if args.dimension in ("S1", "S2", "S3", "S4", "S6", "all"):
        conversations = load_conversations(conv_dir)

    if args.dimension in ("S1", "all"):
        render_d1(
            conv_dir,
            output_dir,
            conversations,
            sample_policy=args.s1_sample_policy,
        )
    if args.dimension in ("S3", "all"):
        render_d2(conv_dir, output_dir, conversations)
    if args.dimension in ("S2", "all"):
        render_d3(conv_dir, output_dir, conversations)
    if args.dimension in ("S6", "all"):
        render_control(conv_dir, output_dir, conversations)
    results_dir = conv_dir.parent
    if args.dimension in ("S5", "all"):
        render_p1(results_dir, output_dir)
    if args.dimension in ("S4", "all"):
        render_b1(conv_dir, output_dir, conversations)


if __name__ == "__main__":
    main()
