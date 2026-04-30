#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]
PRE_COUNTS_PATH = Path("/tmp/rename_codes_to_sj_pre_counts.json")

PREFIX_TOKEN_MAP = {
    "D1": "S1",
    "D2": "S3",
    "D3": "S2",
    "B1": "S4",
    "P1": "S5",
    "control": "S6",
}

DIMENSION_VALUE_MAP = {
    **PREFIX_TOKEN_MAP,
    "C1": "S6",
}

DIMENSION_KEY_MAP = {
    key: value for key, value in DIMENSION_VALUE_MAP.items() if key != "control"
}
CONTROL_DIMENSION_KEY_PARENTS = {
    "aggregate",
    "dimensions",
    "judge_inputs",
    "judge_outputs",
    "records",
}

RUBRIC_ID_MAP = {
    "D1_persona_adherence": "S1_persona_adherence",
    "D3_drift_detection": "S2_persona_drift",
    "D2_cross_run_reproducibility": "S3_cross_run_reproducibility",
    "B1_blind_persona_identification": "S4_blind_identifiability",
    "P1_targeted_probe": "S5_targeted_probes",
    "C1_persona_distinguishability": "S6_generic_falsifier",
}

RUBRIC_FILENAME_MAP = {
    f"{old}.json": f"{new}.json" for old, new in RUBRIC_ID_MAP.items()
} | {f"{old}.txt": f"{new}.txt" for old, new in RUBRIC_ID_MAP.items()}

EVAL_DIRS = [
    ROOT / "results/main/judge_inputs",
    ROOT / "results/main/judge_outputs",
    ROOT / "results/main/judge_outputs_by_model/anthropic__claude-sonnet-4-6",
    ROOT / "results/main/judge_outputs_by_model/openai__gpt-5_4",
    ROOT / "results/main/judge_outputs_by_model/google__gemini-3_1-pro-preview",
    ROOT / "results/judge_qualification/judge_inputs",
    ROOT / "results/judge_qualification/judge_outputs",
    ROOT
    / "results/judge_qualification/judge_outputs_by_model/anthropic__claude-sonnet-4-6",
    ROOT / "results/judge_qualification/judge_outputs_by_model/openai__gpt-5_4",
    ROOT
    / "results/judge_qualification/judge_outputs_by_model/google__gemini-3_1-pro-preview",
]

FIELD_ONLY_PATHS = [
    ROOT / "results/main/evaluations",
    ROOT / "results/main/human_alignment",
    ROOT / "results/main/report",
    ROOT / "results/main/probes/manifest.json",
    ROOT / "results/judge_qualification/report",
    ROOT / "resources/judge_qualification/judge_qualification_corpus.json",
]

SKIP_DIRS = [
    ROOT / "results/main/conversations",
    ROOT / "results/main/probes/responses",
    ROOT / "results/main/contracts_snapshot",
    ROOT / "results/main/policies_snapshot",
]

RUBRIC_SNAPSHOT_DIRS = [
    ROOT / "resources/rubrics",
    ROOT / "resources/rubrics/prompts",
    ROOT / "results/main/rubrics_snapshot",
    ROOT / "results/main/rubrics_snapshot/prompts",
]

MARKDOWN_PATHS = [
    ROOT / "README.md",
    ROOT / "docs/HUMAN_ALIGNMENT.md",
    ROOT / "resources/policies/control_policy.md",
]


def transform_prefixed(value: str) -> str:
    parts = value.split("__", 1)
    replacement = PREFIX_TOKEN_MAP.get(parts[0])
    if replacement is None:
        return value
    return replacement if len(parts) == 1 else f"{replacement}__{parts[1]}"


def transform_string(value: str) -> str:
    if value in DIMENSION_VALUE_MAP:
        return DIMENSION_VALUE_MAP[value]
    if value in RUBRIC_ID_MAP:
        return RUBRIC_ID_MAP[value]
    prefixed = transform_prefixed(value)
    if prefixed != value:
        return prefixed
    for old, new in RUBRIC_ID_MAP.items():
        value = value.replace(old, new)
    return value


def tracked(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT)
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def rename_file(path: Path, new_name: str, summary: Counter[str]) -> Path:
    if path.name == new_name:
        return path
    target = path.with_name(new_name)
    if not path.exists():
        return target
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing path: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if tracked(path):
        subprocess.run(
            [
                "git",
                "mv",
                str(path.relative_to(REPO_ROOT)),
                str(target.relative_to(REPO_ROOT)),
            ],
            cwd=REPO_ROOT,
            check=True,
        )
    else:
        path.rename(target)
    summary["files_renamed"] += 1
    return target


def rename_prefixed_file(path: Path, summary: Counter[str]) -> Path:
    new_name = transform_prefixed(path.name)
    return rename_file(path, new_name, summary)


def rename_rubric_file(path: Path, summary: Counter[str]) -> Path:
    return rename_file(path, RUBRIC_FILENAME_MAP.get(path.name, path.name), summary)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def input_payload_hash(payload: dict[str, Any]) -> str:
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


def replace_value(value: str, key: str, *, eval_file: bool, field_only: bool) -> str:
    if key in {"eval_id", "judge_input_file"}:
        return transform_prefixed(value)
    if key == "dimension":
        return DIMENSION_VALUE_MAP.get(value, value)
    if key == "rubric_id":
        return RUBRIC_ID_MAP.get(value, value)
    if key == "source_file":
        if field_only:
            return transform_prefixed(value)
        if eval_file:
            return transform_prefixed(value)
    return value


def transform_json(
    payload: Any,
    summary: Counter[str],
    *,
    eval_file: bool = False,
    field_only: bool = False,
    top_level: bool = True,
    key_path: tuple[str, ...] = (),
) -> Any:
    if isinstance(payload, list):
        return [
            transform_json(
                item,
                summary,
                eval_file=eval_file,
                field_only=field_only,
                top_level=False,
                key_path=key_path,
            )
            for item in payload
        ]
    if not isinstance(payload, dict):
        return payload

    transformed: dict[Any, Any] = {}
    for key, value in payload.items():
        if field_only and key == "control":
            new_key = (
                "S6"
                if key_path and key_path[-1] in CONTROL_DIMENSION_KEY_PARENTS
                else key
            )
        elif field_only:
            new_key = DIMENSION_KEY_MAP.get(key, key)
        else:
            new_key = key
        if new_key != key:
            summary["field_substitutions"] += 1

        replace_this_source = key == "source_file" and (
            field_only or (eval_file and top_level)
        )
        if isinstance(value, str) and (
            key in {"eval_id", "dimension", "rubric_id", "judge_input_file"}
            or replace_this_source
        ):
            new_value = replace_value(
                value,
                key,
                eval_file=eval_file and top_level,
                field_only=field_only,
            )
            if new_value != value:
                summary["field_substitutions"] += 1
            transformed[new_key] = new_value
        else:
            transformed[new_key] = transform_json(
                value,
                summary,
                eval_file=eval_file,
                field_only=field_only,
                top_level=False,
                key_path=(*key_path, str(new_key)),
            )
    return transformed


def process_json_file(
    path: Path,
    summary: Counter[str],
    *,
    eval_file: bool = False,
    field_only: bool = False,
) -> None:
    original = path.read_text(encoding="utf-8")
    payload = json.loads(original)
    transformed = transform_json(
        payload, summary, eval_file=eval_file, field_only=field_only
    )
    if transformed != payload:
        write_json(path, transformed)
        summary["files_edited"] += 1


def process_csv_file(path: Path, summary: Counter[str]) -> None:
    original = path.read_text(encoding="utf-8")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    changed = False
    for row in rows:
        for field in ("eval_id", "source_file", "judge_input_file"):
            if field in row:
                new_value = transform_prefixed(row[field])
                if new_value != row[field]:
                    row[field] = new_value
                    summary["field_substitutions"] += 1
                    changed = True
        if "dimension" in row:
            new_value = DIMENSION_VALUE_MAP.get(row["dimension"], row["dimension"])
            if new_value != row["dimension"]:
                row["dimension"] = new_value
                summary["field_substitutions"] += 1
                changed = True
    if not changed:
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary["files_edited"] += 1
    if path.read_text(encoding="utf-8") == original:
        summary["files_edited"] -= 1


def process_text_file(path: Path, summary: Counter[str]) -> None:
    text = path.read_text(encoding="utf-8")
    new_text = transform_string(text)
    if new_text == text:
        return
    path.write_text(new_text, encoding="utf-8")
    summary["field_substitutions"] += 1
    summary["files_edited"] += 1


def iter_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file())


def snapshot_preserved_counts() -> None:
    if PRE_COUNTS_PATH.exists():
        return
    conv = ROOT / "results/main/conversations"
    probes = ROOT / "results/main/probes/responses"
    payload = {
        "conversations_live": len(list(conv.glob("live__*.json"))),
        "conversations_control": len(list(conv.glob("control__*.json"))),
        "conversations_total": len(list(conv.glob("*.json"))),
        "probe_responses": len(list(probes.glob("*.json"))),
    }
    PRE_COUNTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def process_eval_dirs(summary: Counter[str]) -> None:
    for directory in EVAL_DIRS:
        if not directory.exists():
            summary["missing_dirs"] += 1
            continue
        for path in sorted(directory.glob("*.json")):
            renamed = rename_prefixed_file(path, summary)
            process_json_file(renamed, summary, eval_file=True)


def process_field_only(summary: Counter[str]) -> None:
    for path in FIELD_ONLY_PATHS:
        for file_path in iter_files(path):
            if file_path.suffix == ".json":
                process_json_file(file_path, summary, field_only=True)
            elif file_path.suffix == ".csv":
                process_csv_file(file_path, summary)
            elif file_path.suffix in {".html", ".md", ".tex"}:
                process_text_file(file_path, summary)


def process_rubrics(summary: Counter[str]) -> None:
    for directory in RUBRIC_SNAPSHOT_DIRS:
        if not directory.exists():
            summary["missing_dirs"] += 1
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            renamed = rename_rubric_file(path, summary)
            if renamed.suffix == ".json":
                process_json_file(renamed, summary, field_only=True)


def process_markdown(summary: Counter[str]) -> None:
    for path in MARKDOWN_PATHS:
        if path.exists():
            process_text_file(path, summary)
        else:
            summary["missing_files"] += 1


def report_skips(summary: Counter[str]) -> None:
    for directory in SKIP_DIRS:
        if not directory.exists():
            summary["missing_dirs"] += 1
            continue
        count = len([p for p in directory.rglob("*") if p.is_file()])
        summary["files_skipped"] += count
        print(f"skip {directory.relative_to(ROOT)}: {count} files")


def sha256_directory(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hashes[str(path.relative_to(root))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashes


def refresh_static_manifest(summary: Counter[str]) -> None:
    manifest_path = ROOT / "results/main/report/static_artifact_manifest.json"
    if not manifest_path.exists():
        return
    manifest = load_json(manifest_path)
    snapshots = manifest.setdefault("snapshots", {})
    old = snapshots.get("rubrics")
    new = sha256_directory(ROOT / "results/main/rubrics_snapshot")
    if old == new:
        return
    snapshots["rubrics"] = new
    write_json(manifest_path, manifest)
    summary["field_substitutions"] += 1
    summary["files_edited"] += 1


def refresh_input_hashes(summary: Counter[str]) -> None:
    pairs = [
        (
            ROOT / "results/main/judge_inputs",
            [
                ROOT / "results/main/judge_outputs",
                ROOT
                / "results/main/judge_outputs_by_model/anthropic__claude-sonnet-4-6",
                ROOT / "results/main/judge_outputs_by_model/openai__gpt-5_4",
                ROOT
                / "results/main/judge_outputs_by_model/google__gemini-3_1-pro-preview",
            ],
        ),
        (
            ROOT / "results/judge_qualification/judge_inputs",
            [
                ROOT / "results/judge_qualification/judge_outputs",
                ROOT
                / "results/judge_qualification/judge_outputs_by_model/anthropic__claude-sonnet-4-6",
                ROOT
                / "results/judge_qualification/judge_outputs_by_model/openai__gpt-5_4",
                ROOT
                / "results/judge_qualification/judge_outputs_by_model/google__gemini-3_1-pro-preview",
            ],
        ),
    ]
    for input_dir, output_dirs in pairs:
        if not input_dir.exists():
            continue
        hashes = {
            path.name: input_payload_hash(load_json(path))
            for path in sorted(input_dir.glob("*.json"))
        }
        for output_dir in output_dirs:
            if not output_dir.exists():
                continue
            for output_path in sorted(output_dir.glob("*.json")):
                expected = hashes.get(output_path.name)
                if not expected:
                    continue
                payload = load_json(output_path)
                if payload.get("input_sha256") == expected:
                    continue
                payload["input_sha256"] = expected
                write_json(output_path, payload)
                summary["field_substitutions"] += 1
                summary["files_edited"] += 1


def main() -> int:
    snapshot_preserved_counts()
    summary: Counter[str] = Counter()
    process_rubrics(summary)
    process_eval_dirs(summary)
    process_field_only(summary)
    refresh_input_hashes(summary)
    refresh_static_manifest(summary)
    process_markdown(summary)
    report_skips(summary)

    print("rename_codes_to_sj summary:")
    print(f"  files renamed: {summary['files_renamed']}")
    print(f"  files edited: {summary['files_edited']}")
    print(f"  field substitutions: {summary['field_substitutions']}")
    print(f"  files skipped: {summary['files_skipped']}")
    if summary["missing_dirs"] or summary["missing_files"]:
        print(f"  missing dirs: {summary['missing_dirs']}")
        print(f"  missing files: {summary['missing_files']}")
    total_changes = (
        summary["files_renamed"]
        + summary["files_edited"]
        + summary["field_substitutions"]
    )
    print(f"  total changes: {total_changes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
