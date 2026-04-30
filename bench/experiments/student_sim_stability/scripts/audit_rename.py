#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from rename_codes_to_sj import (
    DIMENSION_VALUE_MAP,
    EVAL_DIRS,
    PRE_COUNTS_PATH,
    PREFIX_TOKEN_MAP,
    REPO_ROOT,
    ROOT,
    RUBRIC_ID_MAP,
    transform_string,
)

RESULTS = ROOT / "results"
AGGREGATE_PATH = ROOT / "results/main/evaluations/multi_judge_aggregates.json"
PRE_AGGREGATE_PATH = Path("/tmp/multi_judge_aggregates_pre_rename.json")
OLD_LEADING_RE = re.compile(
    "^(?:" + "|".join(re.escape(k) for k in PREFIX_TOKEN_MAP) + ")__"
)
OLD_RUBRIC_RE = re.compile("^(?:" + "|".join(re.escape(k) for k in RUBRIC_ID_MAP) + ")")
SOURCE_EXTENSIONS = {".py", ".md", ".txt", ".json"}
CONTROL_TOKEN = next(k for k, v in PREFIX_TOKEN_MAP.items() if v == "S6")
OLD_CODE_TOKEN_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(k) for k in DIMENSION_VALUE_MAP if k != CONTROL_TOKEN)
    + r")\b"
)
CONTROL_DIMENSION_PATTERNS = [
    re.compile(
        r"""["']dimension["']\s*:\s*["']""" + re.escape(CONTROL_TOKEN) + r"""["']"""
    ),
    re.compile(
        r"""["']dimension["']\)\s*[!=]=\s*["']"""
        + re.escape(CONTROL_TOKEN)
        + r"""["']"""
    ),
    re.compile(
        r"""\bdimension\s*[!=]=\s*["']""" + re.escape(CONTROL_TOKEN) + r"""["']"""
    ),
    re.compile(
        r"""\brubric_prompt_template\(["']""" + re.escape(CONTROL_TOKEN) + r"""["']\)"""
    ),
    re.compile(r"""\b_rubric_prompt\(["']""" + re.escape(CONTROL_TOKEN) + r"""["']"""),
    re.compile(
        r"""\bprimary_score_field\(["']""" + re.escape(CONTROL_TOKEN) + r"""["']\)"""
    ),
    re.compile(r"""\b_iter_records\(["']""" + re.escape(CONTROL_TOKEN) + r"""["']\)"""),
]


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)
    print(f"FAIL: {message}")


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def source_files() -> list[Path]:
    files: list[Path] = []
    migration_script = ROOT / "scripts/rename_codes_to_sj.py"
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_EXTENSIONS:
            continue
        parts = set(path.relative_to(ROOT).parts)
        if "results" in parts or "__pycache__" in parts:
            continue
        if path == migration_script:
            continue
        files.append(path)
    return files


def check_source_tree(failures: list[str]) -> None:
    leaks: list[str] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        if OLD_CODE_TOKEN_RE.search(text):
            leaks.append(rel(path))
            continue
        if any(pattern.search(text) for pattern in CONTROL_DIMENSION_PATTERNS):
            leaks.append(rel(path))
    if leaks:
        for path in leaks[:50]:
            print(f"source leak: {path}")
        fail(f"source-tree leakage in {len(leaks)} files", failures)
    else:
        print("PASS: source-tree leakage check")


def check_eval_filenames(failures: list[str]) -> None:
    bad: list[str] = []
    for directory in EVAL_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            if OLD_LEADING_RE.match(path.name):
                bad.append(rel(path))
    if bad:
        for path in bad[:50]:
            print(f"old leading filename: {path}")
        fail(f"old leading prefixes in {len(bad)} eval filenames", failures)
    else:
        print("PASS: eval filename leading-prefix check")


def iter_json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def content_source_file(value: str) -> bool:
    if value.startswith("live__"):
        return True
    if value.startswith("probes/responses/"):
        return True
    if value.startswith(f"{CONTROL_TOKEN}__"):
        return "_tt" not in value
    return False


def walk_field_values(
    payload: Any,
    path: Path,
    failures: list[str],
    key_path: tuple[str, ...] = (),
) -> None:
    if isinstance(payload, list):
        for item in payload:
            walk_field_values(item, path, failures, key_path)
        return
    if not isinstance(payload, dict):
        return
    for key, value in payload.items():
        if isinstance(value, str):
            if key in {"eval_id", "source_file", "judge_input_file"}:
                if OLD_LEADING_RE.match(value) and not (
                    key == "source_file" and content_source_file(value)
                ):
                    fail(f"{rel(path)}: {key} has old leading prefix {value}", failures)
            elif key == "dimension":
                if value in DIMENSION_VALUE_MAP:
                    fail(f"{rel(path)}: dimension still old value {value}", failures)
            elif key == "rubric_id":
                if OLD_RUBRIC_RE.match(value):
                    fail(f"{rel(path)}: rubric_id still old value {value}", failures)
        walk_field_values(value, path, failures, (*key_path, str(key)))


def check_stored_json_fields(failures: list[str]) -> None:
    before = len(failures)
    for path in iter_json_files(RESULTS):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"{rel(path)}: invalid JSON: {exc}", failures)
            continue
        walk_field_values(payload, path, failures)
    if len(failures) == before:
        print("PASS: stored JSON field-value check")


def check_stored_csv_fields(failures: list[str]) -> None:
    before = len(failures)
    for path in sorted(RESULTS.rglob("*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row_number, row in enumerate(reader, start=2):
                for field in ("eval_id", "source_file", "judge_input_file"):
                    value = row.get(field)
                    if value and OLD_LEADING_RE.match(value):
                        fail(
                            f"{rel(path)}:{row_number}: {field} has old leading prefix {value}",
                            failures,
                        )
                value = row.get("dimension")
                if value in DIMENSION_VALUE_MAP:
                    fail(
                        f"{rel(path)}:{row_number}: dimension still old value {value}",
                        failures,
                    )
    if len(failures) == before:
        print("PASS: stored CSV field-value check")


def count_s2_control_eval_ids() -> int:
    count = 0
    for directory in EVAL_DIRS:
        if not directory.exists():
            continue
        count += len(list(directory.glob("S2__control__*.json")))
    for path in iter_json_files(RESULTS):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                eval_id = item.get("eval_id")
                if isinstance(eval_id, str) and eval_id.startswith("S2__control__"):
                    count += 1
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return count


def check_preserved_content_counts(failures: list[str]) -> None:
    s2_control = count_s2_control_eval_ids()
    print(f"S2__control__ eval/file references: {s2_control}")
    if s2_control <= 0:
        fail("no S2__control__ eval IDs found", failures)

    if not PRE_COUNTS_PATH.exists():
        fail(f"missing preserved-count snapshot {PRE_COUNTS_PATH}", failures)
        return
    expected = json.loads(PRE_COUNTS_PATH.read_text(encoding="utf-8"))
    conv = ROOT / "results/main/conversations"
    probes = ROOT / "results/main/probes/responses"
    actual = {
        "conversations_live": len(list(conv.glob("live__*.json"))),
        "conversations_control": len(list(conv.glob("control__*.json"))),
        "conversations_total": len(list(conv.glob("*.json"))),
        "probe_responses": len(list(probes.glob("*.json"))),
    }
    if actual != expected:
        fail(
            f"preserved content counts changed: expected {expected}, actual {actual}",
            failures,
        )
    else:
        print("PASS: preserved content file counts")


def run_command(args: list[str], failures: list[str], label: str) -> str:
    proc = subprocess.run(
        args,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        fail(f"{label} failed with exit code {proc.returncode}", failures)
    else:
        print(f"PASS: {label}")
    return proc.stdout


def compare_aggregates(failures: list[str]) -> None:
    if not PRE_AGGREGATE_PATH.exists():
        fail(f"missing pre-rename aggregate snapshot {PRE_AGGREGATE_PATH}", failures)
        return
    before = json.loads(PRE_AGGREGATE_PATH.read_text(encoding="utf-8"))
    after = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
    counts = {"numbers": 0, "strings_unchanged": 0, "strings_renamed": 0}

    def compare(a: Any, b: Any, pointer: str) -> None:
        if isinstance(a, dict):
            if not isinstance(b, dict):
                fail(
                    f"{pointer}: type changed from dict to {type(b).__name__}", failures
                )
                return
            transformed_keys = {DIMENSION_VALUE_MAP.get(k, k): k for k in a}
            if set(transformed_keys) != set(b):
                fail(f"{pointer}: dict keys differ", failures)
                return
            for new_key, old_key in transformed_keys.items():
                compare(a[old_key], b[new_key], f"{pointer}/{new_key}")
            return
        if isinstance(a, list):
            if not isinstance(b, list) or len(a) != len(b):
                fail(f"{pointer}: list shape differs", failures)
                return
            for idx, (left, right) in enumerate(zip(a, b)):
                compare(left, right, f"{pointer}/{idx}")
            return
        if isinstance(a, bool) or a is None:
            if a != b:
                fail(f"{pointer}: scalar changed {a!r} -> {b!r}", failures)
            return
        if isinstance(a, (int, float)):
            counts["numbers"] += 1
            if not isinstance(b, (int, float)) or isinstance(b, bool) or a != b:
                fail(f"{pointer}: numeric changed {a!r} -> {b!r}", failures)
            return
        if isinstance(a, str):
            expected = transform_string(a)
            if b == a:
                counts["strings_unchanged"] += 1
            elif b == expected:
                counts["strings_renamed"] += 1
            else:
                fail(f"{pointer}: string changed unexpectedly {a!r} -> {b!r}", failures)
            return
        if a != b:
            fail(f"{pointer}: value changed {a!r} -> {b!r}", failures)

    before_failures = len(failures)
    compare(before, after, "")
    print(
        "aggregate compare: "
        f"{counts['numbers']} numerical leaves, "
        f"{counts['strings_unchanged']} strings unchanged, "
        f"{counts['strings_renamed']} strings renamed"
    )
    if len(failures) == before_failures:
        print("PASS: aggregate numerical/string equivalence")


def check_idempotence(failures: list[str]) -> None:
    output = run_command(
        [sys.executable, str(ROOT / "scripts/rename_codes_to_sj.py")],
        failures,
        "migration idempotence run",
    )
    match = re.search(r"total changes:\s*(\d+)", output)
    if not match:
        fail("migration idempotence output missing total changes", failures)
    elif int(match.group(1)) != 0:
        fail(f"migration idempotence reported {match.group(1)} changes", failures)
    else:
        print("PASS: migration idempotence reported zero changes")


def main() -> int:
    failures: list[str] = []
    check_source_tree(failures)
    check_eval_filenames(failures)
    check_stored_json_fields(failures)
    check_stored_csv_fields(failures)
    check_preserved_content_counts(failures)
    run_command(
        [
            sys.executable,
            "-m",
            "bench.experiments.student_sim_stability.cli",
            "aggregate",
        ],
        failures,
        "cli aggregate",
    )
    compare_aggregates(failures)
    run_command(
        [sys.executable, "-m", "bench.experiments.student_sim_stability.cli", "audit"],
        failures,
        "cli audit",
    )
    check_idempotence(failures)

    if failures:
        print("audit_rename failed:")
        for failure in failures[:100]:
            print(f"  - {failure}")
        return 1
    print("audit_rename passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
