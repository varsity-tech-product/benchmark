"""Validate student simulator stability experiment artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (  # noqa: E402
    CONTROL_OPENING_SOURCE,
    FIXED_TURNS,
    FIXTURE_OPENING_SOURCE,
    GENERATED_STUDENT_TURNS,
    JUDGE_MODELS,
    NEUTRAL_CONTROL_OPENING,
    OUTPUT_DIR,
    REPEATS,
    STUDENT_MODEL_SOURCE,
    STUDENT_MODELS,
    TASK_PERSONA_MAP,
    TUTOR_MODEL_SOURCE,
    TUTOR_TEMPERATURES,
    compute_trial_count,
)
from experiments.student_sim_stability.core.contracts import (  # noqa: E402
    load_persona_contract,
)
from experiments.student_sim_stability.core.rubrics import (  # noqa: E402
    required_score_keys,
    rubric_metadata,
)
from experiments.student_sim_stability.pipeline.probes import PROBES  # noqa: E402
from experiments.student_sim_stability.pipeline.scripted_dialogues import (
    SCRIPTS,
)


@dataclass
class Check:
    name: str
    ok: bool
    message: str
    required: bool = True


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def _count_files(path: Path, pattern: str = "*.json") -> int:
    return len(list(path.glob(pattern))) if path.exists() else 0


def _load_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _safe_model_dir(model: str) -> str:
    return model.replace("/", "__").replace(".", "_")


def _expected_counts() -> dict[str, int]:
    combos = sum(len(v) for v in TASK_PERSONA_MAP.values())
    n_models = len(STUDENT_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    n_personas = len({pid for ids in TASK_PERSONA_MAP.values() for pid in ids})
    live = combos * n_models * REPEATS * n_temps
    control = combos * n_models
    return {
        "conversations": live + control,
        "live_conversations": live,
        "control_conversations": control,
        "d1_sample": combos * n_models * GENERATED_STUDENT_TURNS,
        "d1_full": (live + control) * GENERATED_STUDENT_TURNS,
        "d2": combos * n_models * n_temps,
        "d3": combos * REPEATS * n_temps,
        "d4": live + control,
        "control": control,
        "aggregate_d4": live,
        "p1": n_personas * len(PROBES) * n_models,
        "b1": n_personas * len(SCRIPTS) * n_models,
    }


def _parse_conversation_name(path: Path) -> dict[str, str]:
    parts = path.stem.split("__")
    if len(parts) < 5:
        return {}
    return {
        "phase": parts[0],
        "task_id": parts[1],
        "persona_id": parts[2],
        "model": parts[3],
        "repeat_tag": parts[4],
    }


def _validate_conversation_provenance(conv_dir: Path) -> list[Check]:
    checks: list[Check] = []
    if not conv_dir.exists():
        return [
            Check(
                "conversation_provenance",
                False,
                f"missing conversation directory {conv_dir}",
            )
        ]

    failures: list[str] = []
    for conv_file in sorted(conv_dir.glob("*.json")):
        try:
            conv = _load_json(conv_file)
        except Exception as exc:  # pragma: no cover - validation diagnostic path
            failures.append(f"{conv_file.name}: cannot load JSON ({exc})")
            continue

        if not isinstance(conv, list):
            failures.append(f"{conv_file.name}: conversation is not a list")
            continue

        phase = conv_file.stem.split("__", 1)[0]
        if phase not in {"live", "control"}:
            failures.append(f"{conv_file.name}: unknown conversation phase {phase!r}")
            continue
        expected_opening_source = (
            CONTROL_OPENING_SOURCE if phase == "control" else FIXTURE_OPENING_SOURCE
        )
        user_turns = [turn for turn in conv if turn.get("role") == "user"]
        tutor_turns = [turn for turn in conv if turn.get("role") == "assistant"]
        generated_turns = [
            turn for turn in user_turns if turn.get("source") == STUDENT_MODEL_SOURCE
        ]

        if not user_turns:
            failures.append(f"{conv_file.name}: no user turns")
            continue
        first_source = user_turns[0].get("source")
        if first_source != expected_opening_source:
            failures.append(
                f"{conv_file.name}: first user source={first_source!r}, "
                f"expected {expected_opening_source!r}"
            )
        if phase == "control":
            first_content = str(user_turns[0].get("content", ""))
            if first_content != NEUTRAL_CONTROL_OPENING:
                failures.append(
                    f"{conv_file.name}: control opening content={first_content!r}, "
                    f"expected {NEUTRAL_CONTROL_OPENING!r}"
                )
        bad_user_sources = [
            turn.get("source")
            for turn in user_turns[1:]
            if turn.get("source") != STUDENT_MODEL_SOURCE
        ]
        if bad_user_sources:
            failures.append(
                f"{conv_file.name}: non-generated student sources after opener "
                f"{bad_user_sources!r}"
            )
        if len(generated_turns) != GENERATED_STUDENT_TURNS:
            failures.append(
                f"{conv_file.name}: generated student turns="
                f"{len(generated_turns)}/{GENERATED_STUDENT_TURNS}"
            )
        empty_generated = [
            idx
            for idx, turn in enumerate(generated_turns)
            if not str(turn.get("content", "")).strip()
        ]
        if empty_generated:
            failures.append(
                f"{conv_file.name}: empty generated student turns {empty_generated}"
            )
        if len(tutor_turns) != FIXED_TURNS:
            failures.append(
                f"{conv_file.name}: tutor turns={len(tutor_turns)}/{FIXED_TURNS}"
            )
        bad_tutor_sources = [
            turn.get("source")
            for turn in tutor_turns
            if turn.get("source") != TUTOR_MODEL_SOURCE
        ]
        if bad_tutor_sources:
            failures.append(
                f"{conv_file.name}: tutor source values {bad_tutor_sources!r}"
            )

    sample = "; ".join(failures[:5])
    checks.append(
        Check(
            "conversation_turn_provenance",
            not failures,
            sample if failures else "all conversations have explicit turn sources",
        )
    )
    return checks


def _score_signature(output: dict) -> str:
    payload = json.dumps(output.get("scores", {}), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_judge_output_quality(output_dir: Path) -> list[Check]:
    checks: list[Check] = []
    if not output_dir.exists():
        return [
            Check(
                "judge_output_quality",
                False,
                f"missing judge output directory {output_dir}",
            )
        ]

    d4_outputs = []
    outputs_by_dimension: dict[str, list[dict]] = defaultdict(list)
    malformed: list[str] = []
    missing_score_keys: list[str] = []
    for path in sorted(output_dir.glob("*.json")):
        try:
            output = _load_json(path)
        except Exception as exc:  # pragma: no cover - validation diagnostic path
            malformed.append(f"{path.name}: cannot load JSON ({exc})")
            continue
        scores = output.get("scores")
        if not isinstance(scores, dict):
            malformed.append(f"{path.name}: missing scores object")
            continue
        dimension = output.get("dimension") or path.stem.split("__", 1)[0]
        try:
            required = required_score_keys(dimension)
        except ValueError:
            malformed.append(f"{path.name}: unknown dimension {dimension!r}")
            continue
        missing = sorted(required - set(scores))
        if missing:
            missing_score_keys.append(f"{path.name}: missing score keys {missing}")
        outputs_by_dimension[dimension].append(output)
        if dimension == "D4":
            d4_outputs.append(output)

    duplicate_payloads: list[str] = []
    for dimension, outputs in sorted(outputs_by_dimension.items()):
        counts = Counter(_score_signature(output) for output in outputs)
        repeated = sorted(count for count in counts.values() if count > 1)
        if repeated:
            duplicate_payloads.append(
                f"{dimension}: duplicate score clusters {repeated}"
            )

    checks.append(
        Check(
            "judge_no_duplicate_score_payloads",
            not duplicate_payloads,
            (
                "; ".join(duplicate_payloads[:5])
                if duplicate_payloads
                else "no exact duplicate judge score payloads"
            ),
        )
    )

    duplicate_counts = Counter(_score_signature(output) for output in d4_outputs)
    template_like = [count for count in duplicate_counts.values() if count > 1]
    checks.append(
        Check(
            "d4_no_template_duplicate_score_clusters",
            not template_like,
            (
                "no D4 score signature appears more than once"
                if not template_like
                else f"D4 duplicate score clusters detected: {sorted(template_like)}"
            ),
        )
    )
    checks.append(
        Check(
            "d4_output_scores_present",
            not malformed,
            "; ".join(malformed[:5]) if malformed else "all judge outputs have scores",
        )
    )
    checks.append(
        Check(
            "judge_output_required_score_fields",
            not missing_score_keys,
            (
                "; ".join(missing_score_keys[:5])
                if missing_score_keys
                else "all judge outputs include rubric-required score fields"
            ),
        )
    )
    return checks


def _dimension_counts(path: Path) -> dict[str, int]:
    return {
        "D1": _count_files(path, "D1__*.json"),
        "D2": _count_files(path, "D2__*.json"),
        "D3": _count_files(path, "D3__*.json"),
        "D4": _count_files(path, "D4__*.json"),
        "control": _count_files(path, "control__*.json"),
        "P1": _count_files(path, "P1__*.json"),
        "B1": _count_files(path, "B1__*.json"),
    }


def _validate_static_snapshots(results_dir: Path) -> list[Check]:
    checks: list[Check] = []
    manifest_path = results_dir / "report" / "static_artifact_manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.exists() else {}
    expected_snapshots = {
        "contracts": "contracts_snapshot",
        "rubrics": "rubrics_snapshot",
        "policies": "policies_snapshot",
    }
    for manifest_key, snapshot_name in expected_snapshots.items():
        path = results_dir / snapshot_name
        exists = path.exists() and any(path.rglob("*"))
        checks.append(
            Check(
                f"snapshot_{snapshot_name}",
                exists,
                f"{path} {'exists' if path.exists() else 'missing'}",
            )
        )
        if not exists:
            continue
        expected_hashes = (manifest.get("snapshots") or {}).get(manifest_key)
        snapshot_hashes = _hash_tree(path)
        checks.append(
            Check(
                f"snapshot_{snapshot_name}_manifest",
                expected_hashes == snapshot_hashes,
                (
                    "snapshot matches result-local static artifact manifest"
                    if expected_hashes == snapshot_hashes
                    else f"snapshot differs from result-local manifest at {manifest_path}"
                ),
            )
        )
    checks.append(
        Check(
            "static_artifact_manifest",
            manifest_path.exists(),
            f"{manifest_path} {'exists' if manifest_path.exists() else 'missing'}",
        )
    )
    return checks


def _hash_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _validate_status_artifacts(
    results_dir: Path, *, require_audit: bool = True
) -> list[Check]:
    required_paths = {
        "report_stability_metadata": results_dir / "report" / "stability_metadata.json",
        "report_judge_agreement_status": results_dir
        / "report"
        / "judge_agreement.json",
        "human_alignment_status": results_dir
        / "human_alignment"
        / "agreement_report.json",
        "human_alignment_sample_manifest": results_dir
        / "human_alignment"
        / "sample_manifest.json",
        "human_alignment_label_template": results_dir
        / "human_alignment"
        / "human_label_template.csv",
        "human_alignment_disagreement_examples": results_dir
        / "human_alignment"
        / "disagreement_examples.md",
        "model_metadata": results_dir / "report" / "model_metadata.json",
    }
    if require_audit:
        required_paths.update(
            {
                "data_quality_audit_json": results_dir
                / "report"
                / "data_quality_audit.json",
                "data_quality_audit_md": results_dir
                / "report"
                / "data_quality_audit.md",
            }
        )
    checks = [
        Check(
            name,
            path.exists(),
            f"{path} {'exists' if path.exists() else 'missing'}",
        )
        for name, path in required_paths.items()
    ]
    audit_path = results_dir / "report" / "data_quality_audit.json"
    if require_audit and audit_path.exists():
        audit = _load_json(audit_path)
        checks.append(
            Check(
                "data_quality_audit_passed",
                audit.get("ok") is True,
                f"audit ok={audit.get('ok')}",
            )
        )
    model_metadata_path = results_dir / "report" / "model_metadata.json"
    if model_metadata_path.exists():
        text = model_metadata_path.read_text(encoding="utf-8")
        checks.append(
            Check(
                "model_metadata_no_placeholders",
                "not_recorded_in_this_experiment" not in text,
                "model metadata contains no not_recorded placeholders",
            )
        )
        try:
            metadata = json.loads(text)
        except json.JSONDecodeError:
            checks.append(
                Check(
                    "model_metadata_required_fields",
                    False,
                    "model metadata is invalid JSON",
                )
            )
        else:
            required = {
                "model",
                "model_name",
                "provider",
                "roles",
                "context_length_tokens",
                "context_length_status",
                "price_band",
                "capability_tier",
                "published_parameter_count",
                "active_parameter_count",
                "model_card_or_source_url",
                "metadata_status",
                "comparison_label",
            }
            missing: list[str] = []
            incomplete: list[str] = []
            bad_labels: list[str] = []
            for idx, item in enumerate(metadata.get("models", [])):
                missing_fields = sorted(required - set(item))
                if missing_fields:
                    missing.append(
                        f"models[{idx}] {item.get('model')}: {missing_fields}"
                    )
                    continue
                if not item.get("model_card_or_source_url"):
                    incomplete.append(f"{item.get('model')}: missing model source URL")
                if item.get("context_length_tokens") is None and item.get(
                    "context_length_status"
                ) not in {
                    "unavailable_in_local_repository",
                    "not_disclosed_or_not_used",
                }:
                    incomplete.append(
                        f"{item.get('model')}: context length missing without explicit status"
                    )
                if (
                    item.get("published_parameter_count") is None
                    and item.get("comparison_label")
                    != "cross-vendor candidate selection"
                    and "student_simulator_candidate" in item.get("roles", [])
                ):
                    bad_labels.append(
                        f"{item.get('model')}: candidate model lacks parameter count but is not cross-vendor labeled"
                    )
            checks.append(
                Check(
                    "model_metadata_required_fields",
                    not missing,
                    (
                        "; ".join(missing[:5])
                        if missing
                        else "model metadata includes issue83 comparison fields"
                    ),
                )
            )
            checks.append(
                Check(
                    "model_metadata_source_and_unavailable_status",
                    not incomplete,
                    (
                        "; ".join(incomplete[:5])
                        if incomplete
                        else "unavailable model metadata is explicitly labeled"
                    ),
                )
            )
            checks.append(
                Check(
                    "model_metadata_comparison_labels",
                    not bad_labels,
                    (
                        "; ".join(bad_labels[:5])
                        if bad_labels
                        else "closed/undisclosed student models are labeled as cross-vendor candidate selection"
                    ),
                )
            )
    return checks


def _validate_judge_input_metadata(input_dir: Path) -> list[Check]:
    if not input_dir.exists():
        return [
            Check(
                "judge_input_metadata",
                False,
                f"missing judge input directory {input_dir}",
            )
        ]
    missing: list[str] = []
    for path in sorted(input_dir.glob("*.json")):
        payload = _load_json(path)
        metadata = payload.get("metadata", {})
        for field in ["rubric_id", "rubric_version"]:
            if not metadata.get(field):
                missing.append(f"{path.name}: missing {field}")
        dimension = payload.get("dimension")
        if dimension:
            try:
                expected_rubric = rubric_metadata(dimension)
                if metadata.get("rubric_id") != expected_rubric["rubric_id"]:
                    missing.append(f"{path.name}: rubric_id mismatch")
                if metadata.get("rubric_version") != expected_rubric["rubric_version"]:
                    missing.append(f"{path.name}: rubric_version mismatch")
            except ValueError:
                missing.append(f"{path.name}: unknown rubric dimension {dimension}")
        if payload.get("dimension") != "control":
            for field in ["persona_contract_id", "persona_contract_version"]:
                if not metadata.get(field):
                    missing.append(f"{path.name}: missing {field}")
            persona_id = metadata.get("persona_id")
            if persona_id:
                try:
                    contract = load_persona_contract(persona_id)
                    if (
                        metadata.get("persona_contract_version")
                        != contract["contract_version"]
                    ):
                        missing.append(
                            f"{path.name}: persona_contract_version mismatch"
                        )
                except Exception as exc:  # noqa: BLE001 - validation diagnostic
                    missing.append(f"{path.name}: cannot load persona contract ({exc})")
        rubric_id = metadata.get("rubric_id") or payload.get("rubric_id")
        if rubric_id and rubric_id not in payload.get("prompt", ""):
            missing.append(f"{path.name}: prompt missing rubric_id text")
    return [
        Check(
            "judge_input_rubric_contract_metadata",
            not missing,
            (
                "; ".join(missing[:5])
                if missing
                else "judge inputs include rubric and contract metadata"
            ),
        )
    ]


def _input_payload_hash(payload: dict) -> str:
    relevant = {
        "eval_id": payload.get("eval_id"),
        "dimension": payload.get("dimension"),
        "rubric_id": payload.get("rubric_id"),
        "rubric_version": payload.get("rubric_version"),
        "prompt": payload.get("prompt"),
        "metadata": payload.get("metadata", {}),
    }
    encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_judge_output_metadata(
    input_dir: Path,
    output_dir: Path,
    *,
    expected_judge_model: str | None = None,
) -> list[Check]:
    if not output_dir.exists():
        return [
            Check(
                "judge_output_metadata",
                False,
                f"missing judge output directory {output_dir}",
            )
        ]
    missing: list[str] = []
    input_names = (
        {path.name for path in input_dir.glob("*.json")}
        if input_dir.exists()
        else set()
    )
    output_names = {path.name for path in output_dir.glob("*.json")}
    missing_outputs = sorted(input_names - output_names)
    extra_outputs = sorted(output_names - input_names)
    if missing_outputs:
        missing.append(f"missing judge outputs for inputs {missing_outputs[:5]}")
    if extra_outputs:
        missing.append(f"judge outputs without matching inputs {extra_outputs[:5]}")
    for path in sorted(output_dir.glob("*.json")):
        payload = _load_json(path)
        for field in ["rubric_id", "rubric_version", "judge_model", "input_sha256"]:
            if not payload.get(field):
                missing.append(f"{path.name}: missing {field}")
        if expected_judge_model and payload.get("judge_model") != expected_judge_model:
            missing.append(
                f"{path.name}: judge_model={payload.get('judge_model')!r}, "
                f"expected {expected_judge_model!r}"
            )
        input_path = input_dir / path.name
        if input_path.exists() and payload.get("input_sha256"):
            input_hash = _input_payload_hash(_load_json(input_path))
            if payload["input_sha256"] != input_hash:
                missing.append(f"{path.name}: input_sha256 mismatch")
        elif not input_path.exists():
            missing.append(f"{path.name}: missing matching judge input")
    return [
        Check(
            "judge_output_rubric_metadata",
            not missing,
            (
                "; ".join(missing[:5])
                if missing
                else "judge outputs include rubric metadata"
            ),
        )
    ]


def _prefix_checks(
    checks: list[Check], prefix: str, *, required: bool | None = None
) -> list[Check]:
    return [
        Check(
            f"{prefix}_{check.name}",
            check.ok,
            check.message,
            check.required if required is None else required,
        )
        for check in checks
    ]


def _validate_judge_panel_outputs(
    results_dir: Path,
    input_counts: dict[str, int],
    *,
    profile: str,
) -> list[Check]:
    checks: list[Check] = []
    by_model_dir = results_dir / "judge_outputs_by_model"
    total_inputs = sum(input_counts.values())
    if profile == "pilot" and total_inputs == 0 and not by_model_dir.exists():
        return checks

    for model in JUDGE_MODELS:
        model_dir = by_model_dir / _safe_model_dir(model)
        model_counts = _dimension_counts(model_dir)
        model_total = sum(model_counts.values())
        expected_total = total_inputs
        required = profile == "full"
        checks.append(
            Check(
                f"judge_panel_outputs_{_safe_model_dir(model)}",
                model_dir.exists() and model_total == expected_total,
                (
                    f"{model_total}/{expected_total} outputs for {model}"
                    if model_dir.exists()
                    else f"missing judge panel output dir for {model}"
                ),
                required=required,
            )
        )
        if model_dir.exists():
            checks.extend(
                _prefix_checks(
                    _validate_judge_output_quality(model_dir),
                    f"judge_panel_{_safe_model_dir(model)}",
                    required=True,
                )
            )
            checks.extend(
                _prefix_checks(
                    _validate_judge_output_metadata(
                        results_dir / "judge_inputs",
                        model_dir,
                        expected_judge_model=model,
                    ),
                    f"judge_panel_{_safe_model_dir(model)}",
                    required=True,
                )
            )

    agreement_path = results_dir / "report" / "judge_agreement.json"
    if agreement_path.exists():
        agreement = _load_json(agreement_path)
        computed = (
            agreement.get("multi_judge_status") == "computed"
            and not agreement.get("missing_model_dirs")
            and agreement.get("missing_outputs_count", 0) == 0
            and agreement.get("invalid_score_count", 0) == 0
            and agreement.get("extra_outputs_count", 0) == 0
        )
        status_ok = (
            computed
            if profile == "full"
            else agreement.get("multi_judge_status") in {"not_run", "computed"}
            and (
                agreement.get("multi_judge_status") != "computed"
                or (
                    not agreement.get("missing_model_dirs")
                    and agreement.get("missing_outputs_count", 0) == 0
                    and agreement.get("invalid_score_count", 0) == 0
                    and agreement.get("extra_outputs_count", 0) == 0
                )
            )
        )
        checks.append(
            Check(
                "multi_judge_agreement_status",
                status_ok,
                (
                    f"{agreement.get('multi_judge_status', 'missing')}; "
                    f"missing_dirs={agreement.get('missing_model_dirs', [])}; "
                    f"missing_outputs={agreement.get('missing_outputs_count', 0)}; "
                    f"invalid_scores={agreement.get('invalid_score_count', 0)}; "
                    f"extra_outputs={agreement.get('extra_outputs_count', 0)}"
                ),
                required=profile == "full",
            )
        )
    else:
        checks.append(
            Check(
                "multi_judge_agreement_status",
                False,
                f"missing {agreement_path}",
                required=profile == "full",
            )
        )
    return checks


def validate(
    results_dir: Path,
    *,
    profile: str = "full",
    require_audit: bool = True,
) -> tuple[list[Check], dict]:
    if profile not in {"full", "pilot"}:
        raise ValueError(f"Unknown validation profile: {profile}")
    expected = _expected_counts()
    counts = compute_trial_count()
    checks: list[Check] = []

    conv_dir = results_dir / "conversations"
    input_dir = results_dir / "judge_inputs"
    output_dir = results_dir / "judge_outputs"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"
    report_path = results_dir / "report" / "stability_report.html"

    conversations = _count_files(conv_dir)
    live_conversations = _count_files(conv_dir, "live__*.json")
    control_conversations = _count_files(conv_dir, "control__*.json")
    conversation_ok = (
        conversations == expected["conversations"]
        if profile == "full"
        else live_conversations > 0 and control_conversations > 0
    )
    checks.append(
        Check(
            "conversation_count",
            conversation_ok,
            (
                f"{conversations}/{expected['conversations']} conversation files"
                if profile == "full"
                else f"{conversations} pilot conversation files"
            ),
        )
    )
    if profile == "pilot":
        live_metas = [
            _parse_conversation_name(path)
            for path in sorted(conv_dir.glob("live__*.json"))
        ]
        control_metas = [
            _parse_conversation_name(path)
            for path in sorted(conv_dir.glob("control__*.json"))
        ]
        live_tasks = {meta.get("task_id") for meta in live_metas if meta}
        live_personas = {meta.get("persona_id") for meta in live_metas if meta}
        live_models = {meta.get("model") for meta in live_metas if meta}
        control_personas = {meta.get("persona_id") for meta in control_metas if meta}
        checks.append(
            Check(
                "pilot_live_conversations_nonzero",
                live_conversations > 0,
                f"{live_conversations} pilot live conversation files",
            )
        )
        checks.append(
            Check(
                "pilot_control_conversations_nonzero",
                control_conversations > 0,
                f"{control_conversations} pilot control conversation files",
            )
        )
        checks.append(
            Check(
                "pilot_balanced_scope",
                len(live_tasks) >= 2
                and len(live_personas) >= 4
                and len(live_models) == 1
                and control_personas >= live_personas,
                (
                    f"tasks={sorted(live_tasks)}, personas={sorted(live_personas)}, "
                    f"models={sorted(live_models)}, control_personas={sorted(control_personas)}"
                ),
            )
        )
        probe_manifest_path = results_dir / "probes" / "manifest.json"
        scripted_manifest_path = results_dir / "scripted" / "manifest.json"
        probe_written = (
            _load_json(probe_manifest_path).get("written", 0)
            if probe_manifest_path.exists()
            else 0
        )
        scripted_written = (
            _load_json(scripted_manifest_path).get("written", 0)
            if scripted_manifest_path.exists()
            else 0
        )
        checks.append(
            Check(
                "pilot_targeted_probes_all_personas",
                probe_written >= 4 * len(PROBES),
                f"probe responses={probe_written}/{4 * len(PROBES)}",
            )
        )
        checks.append(
            Check(
                "pilot_scripted_dialogues_all_personas",
                scripted_written >= 4 * len(SCRIPTS),
                f"scripted conversations={scripted_written}/{4 * len(SCRIPTS)}",
            )
        )
    checks.extend(_validate_static_snapshots(results_dir))
    checks.extend(_validate_status_artifacts(results_dir, require_audit=require_audit))
    checks.extend(_validate_conversation_provenance(conv_dir))

    input_counts = _dimension_counts(input_dir)
    d1_input_ok = (
        input_counts["D1"] in {expected["d1_sample"], expected["d1_full"]}
        if profile == "full"
        else input_counts["D1"] > 0
    )
    checks.append(
        Check(
            "judge_input_d1_policy",
            d1_input_ok,
            (
                f"D1 inputs={input_counts['D1']} "
                f"(expected sample {expected['d1_sample']} or full {expected['d1_full']})"
            ),
        )
    )
    for dim, key in [
        ("D2", "d2"),
        ("D3", "d3"),
        ("D4", "d4"),
        ("P1", "p1"),
        ("B1", "b1"),
    ]:
        if profile == "pilot" and dim == "P1":
            input_ok = input_counts[dim] >= 4 * len(PROBES)
        elif profile == "pilot" and dim == "B1":
            input_ok = input_counts[dim] >= 4 * len(SCRIPTS)
        elif profile == "pilot" and dim == "D4":
            input_ok = input_counts[dim] > 0
        elif profile == "pilot":
            input_ok = True
        else:
            input_ok = input_counts[dim] == expected[key]
        checks.append(
            Check(
                f"judge_input_{dim.lower()}",
                input_ok,
                (
                    f"{dim} inputs={input_counts[dim]}/{expected[key]}"
                    if profile == "full"
                    else f"{dim} pilot inputs={input_counts[dim]}"
                ),
            )
        )
    checks.append(
        Check(
            "judge_input_control",
            (
                input_counts["control"] == expected["control"]
                if profile == "full"
                else input_counts["control"] > 0
            ),
            (
                f"control inputs={input_counts['control']}/{expected['control']}"
                if profile == "full"
                else f"control pilot inputs={input_counts['control']}"
            ),
        )
    )
    checks.extend(_validate_judge_input_metadata(input_dir))

    output_counts = _dimension_counts(output_dir)
    d1_output_ok = (
        output_counts["D1"] == input_counts["D1"]
        and input_counts["D1"] in {expected["d1_sample"], expected["d1_full"]}
        if profile == "full"
        else output_counts["D1"] == input_counts["D1"] and input_counts["D1"] > 0
    )
    checks.append(
        Check(
            "judge_output_d1_sample",
            d1_output_ok,
            (
                f"D1 outputs={output_counts['D1']}/{input_counts['D1']}"
                if profile == "full"
                else f"D1 outputs={output_counts['D1']}/{input_counts['D1']} pilot inputs"
            ),
        )
    )
    for dim, key in [
        ("D2", "d2"),
        ("D3", "d3"),
        ("D4", "d4"),
        ("P1", "p1"),
        ("B1", "b1"),
    ]:
        expected_output = expected[key] if profile == "full" else input_counts[dim]
        output_ok = output_counts[dim] == expected_output
        if profile == "pilot" and dim == "P1":
            output_ok = output_ok and input_counts[dim] >= 4 * len(PROBES)
        elif profile == "pilot" and dim == "B1":
            output_ok = output_ok and input_counts[dim] >= 4 * len(SCRIPTS)
        elif profile == "pilot" and dim == "D4":
            output_ok = output_ok and input_counts[dim] > 0
        checks.append(
            Check(
                f"judge_output_{dim.lower()}",
                output_ok,
                f"{dim} outputs={output_counts[dim]}/{expected_output}",
            )
        )
    checks.append(
        Check(
            "judge_output_control",
            (
                output_counts["control"] == expected["control"]
                if profile == "full"
                else output_counts["control"] == input_counts["control"]
                and input_counts["control"] > 0
            ),
            f"control outputs={output_counts['control']}/"
            f"{expected['control'] if profile == 'full' else input_counts['control']}",
        )
    )
    checks.extend(_validate_judge_output_quality(output_dir))
    checks.extend(_validate_judge_output_metadata(input_dir, output_dir))
    checks.extend(
        _validate_judge_panel_outputs(results_dir, input_counts, profile=profile)
    )

    aggregate_counts: dict[str, int] = {}
    if eval_path.exists():
        aggregate = _load_json(eval_path)
        aggregate_counts = {
            key: len(aggregate.get(key, []))
            for key in ["D1", "D2", "D3", "D4", "control", "P1", "B1"]
        }
        expected_aggregate = {
            "D1": expected["d1_sample"],
            "D2": expected["d2"],
            "D3": expected["d3"],
            "D4": expected["aggregate_d4"],
            "control": expected["control"],
            "P1": expected["p1"],
            "B1": expected["b1"],
        }
        for key, expected_count in expected_aggregate.items():
            actual_expected = (
                output_counts["D1"]
                if profile == "full" and key == "D1"
                else (
                    expected_count
                    if profile == "full"
                    else (
                        output_counts[key]
                        if key != "D4"
                        else len(
                            [
                                path
                                for path in output_dir.glob("D4__*.json")
                                if "D4__control__" not in path.name
                            ]
                        )
                    )
                )
            )
            checks.append(
                Check(
                    f"aggregate_{key.lower()}",
                    aggregate_counts.get(key, 0) == actual_expected,
                    f"{key} aggregate={aggregate_counts.get(key, 0)}/{actual_expected}",
                )
            )
    else:
        checks.append(
            Check("aggregate_file", False, f"missing {eval_path}", required=False)
        )

    checks.append(
        Check(
            "report_html",
            report_path.exists(),
            f"{report_path} {'exists' if report_path.exists() else 'missing'}",
            required=False,
        )
    )

    return checks, {
        "expected": expected,
        "compute_trial_count": counts,
        "judge_inputs": input_counts,
        "judge_outputs": output_counts,
        "aggregate": aggregate_counts,
        "profile": profile,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument(
        "--strict", action="store_true", help="Treat optional warnings as failures"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable summary"
    )
    parser.add_argument("--profile", choices=["full", "pilot"], default="full")
    parser.add_argument(
        "--no-require-audit",
        action="store_true",
        help="Internal use: do not require data_quality_audit artifacts",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    results_dir = Path(args.results_dir) if args.results_dir else default_results_dir()
    checks, summary = validate(
        results_dir,
        profile=args.profile,
        require_audit=not args.no_require_audit,
    )
    failures = [c for c in checks if not c.ok and (c.required or args.strict)]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "checks": [c.__dict__ for c in checks],
                    "summary": summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        for check in checks:
            status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{status}] {check.name}: {check.message}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
