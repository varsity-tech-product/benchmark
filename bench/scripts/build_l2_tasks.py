#!/usr/bin/env python3
"""
Build L2 task JSONs by porting from _legacy_v22_layer2/.

Mostly mechanical: bumps version, adds layer/subcategory fields, retargets
sandbox_image and eval_script path, drops the deprecated max_turns field.

Skips L2_ADV_01 (already produced as a hand-written sample for schema review).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_TASKS = PROJECT_ROOT / "bench" / "tasks" / "_legacy_v22_layer2"
LEGACY_SCRIPTS = PROJECT_ROOT / "bench" / "tasks" / "_legacy_v22_test_scripts"
L2_DIR = PROJECT_ROOT / "bench" / "tasks" / "L2"
L2_SCRIPTS_DIR = PROJECT_ROOT / "bench" / "tasks" / "test_scripts" / "L2"

# Mapping: (src subdir, src filename stem, new task_id, category, subcategory)
PLAN = [
    # Adversarial (11) — A01 already produced as sample, skip
    (
        "adversarial",
        "A02_just_give_me_code",
        "L2_ADV_02_just_give_me_code",
        "adversarial",
        "education_vs_compliance",
    ),
    (
        "adversarial",
        "A04_emotional_discouragement",
        "L2_ADV_03_emotional_discouragement",
        "adversarial",
        "emotional_support",
    ),
    (
        "adversarial",
        "A06_fiction_wrapped_manipulation",
        "L2_ADV_04_fiction_wrapped_manipulation",
        "adversarial",
        "obfuscated_market_manipulation",
    ),
    (
        "adversarial",
        "A07_data_mining_fallacy",
        "L2_ADV_05_data_mining_fallacy",
        "adversarial",
        "statistical_misconception",
    ),
    (
        "adversarial",
        "A09_insider_trading",
        "L2_ADV_06_insider_trading",
        "adversarial",
        "illegal_trading",
    ),
    (
        "adversarial",
        "A10_pump_and_dump",
        "L2_ADV_07_pump_and_dump",
        "adversarial",
        "securities_fraud",
    ),
    (
        "adversarial",
        "A12_window_dressing",
        "L2_ADV_08_window_dressing",
        "adversarial",
        "ethics_violation",
    ),
    (
        "adversarial",
        "A13_destructive_command",
        "L2_ADV_09_destructive_command",
        "adversarial",
        "sandbox_attack",
    ),
    (
        "adversarial",
        "A14_obfuscated_malicious_code",
        "L2_ADV_10_obfuscated_malicious_code",
        "adversarial",
        "sandbox_attack",
    ),
    (
        "adversarial",
        "A16_prompt_injection_csv",
        "L2_ADV_11_prompt_injection_csv",
        "adversarial",
        "sandbox_attack",
    ),
    # Diagnostic (multi-turn debug) — 2
    (
        "debug",
        "X06_overfit_single",
        "L2_DIA_01_overfit_diagnosis",
        "diagnostic",
        "conceptual_diagnosis",
    ),
    (
        "debug",
        "X09_alpha_conflict",
        "L2_DIA_02_alpha_framework_conflict",
        "diagnostic",
        "framework_diagnosis",
    ),
    # End-to-end (2)
    (
        "end_to_end",
        "E03_strategy_validation",
        "L2_E2E_01_strategy_validation",
        "end_to_end",
        "validation_methodology",
    ),
    (
        "end_to_end",
        "E04_production_debugging",
        "L2_E2E_02_production_debugging",
        "end_to_end",
        "multi_bug_diagnosis",
    ),
]


def transform(
    src: dict, new_task_id: str, new_category: str, new_subcategory: str
) -> dict:
    """Apply L2 schema bump to a legacy layer2 task dict."""
    out = dict(src)  # shallow copy preserves field order

    # Required L2 schema changes
    out["task_id"] = new_task_id
    out["version"] = "3.0"
    out["layer"] = "L2"
    out["category"] = new_category
    out["subcategory"] = new_subcategory
    out["task_type"] = "multi_turn_dialog"

    # max_turns is deprecated (per user decision)
    out.pop("max_turns", None)

    # Sandbox image rename
    env = dict(out.get("environment", {}))
    img = env.get("sandbox_image", "")
    if img.startswith("quant-tutor-env:v2.2"):
        # Preserve any suffix like ':v2.2-lean'
        suffix = img[len("quant-tutor-env:v2.2") :]
        env["sandbox_image"] = "quant-bench-env:v3.0" + suffix
    out["environment"] = env

    # Eval script path
    gt = dict(out.get("ground_truth", {}))
    qv = dict(gt.get("quant_validation", {}))
    qv["eval_script"] = f"tasks/test_scripts/L2/{new_task_id}.py"
    gt["quant_validation"] = qv
    out["ground_truth"] = gt

    # Reorder keys for readability (schema-stable order)
    key_order = [
        "task_id",
        "version",
        "layer",
        "category",
        "subcategory",
        "task_type",
        "difficulty",
        "description",
        "persona_id",
        "user_opening",
        "environment",
        "ground_truth",
        "requires_code",
        "sample_code",
        "timeout_minutes",
    ]
    reordered = {k: out[k] for k in key_order if k in out}
    for k, v in out.items():
        if k not in reordered:
            reordered[k] = v
    return reordered


def main() -> None:
    if not LEGACY_TASKS.exists():
        print(f"ERROR: legacy tasks dir missing: {LEGACY_TASKS}", file=sys.stderr)
        sys.exit(1)

    L2_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    written_tasks = 0
    written_scripts = 0
    missing_scripts = []

    for src_subdir, src_stem, new_id, new_cat, new_sub in PLAN:
        src_task_path = LEGACY_TASKS / src_subdir / f"{src_stem}.json"
        if not src_task_path.exists():
            print(f"ERROR: missing source {src_task_path}", file=sys.stderr)
            sys.exit(1)

        src = json.loads(src_task_path.read_text())
        new_task = transform(src, new_id, new_cat, new_sub)

        out_dir = L2_DIR / new_cat
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{new_id}.json"
        out_path.write_text(json.dumps(new_task, indent=2, ensure_ascii=False) + "\n")
        written_tasks += 1

        # Copy eval script if it exists in legacy
        # Some scripts live in I**.py at the top level, others under subdir/
        candidates = [
            LEGACY_SCRIPTS / src_subdir / f"{src_stem}.py",
            LEGACY_SCRIPTS / f"{src_stem}.py",
        ]
        copied = False
        for cand in candidates:
            if cand.exists():
                shutil.copy(cand, L2_SCRIPTS_DIR / f"{new_id}.py")
                written_scripts += 1
                copied = True
                break
        if not copied:
            missing_scripts.append((new_id, src_stem))

        print(
            f"  + {new_id:50s}  <- {src_subdir}/{src_stem}{' (script copied)' if copied else ''}"
        )

    print()
    print(f"Tasks written: {written_tasks}")
    print(f"Scripts copied: {written_scripts}")
    if missing_scripts:
        print("Missing eval scripts:")
        for nid, stem in missing_scripts:
            print(f"  - {nid} (legacy stem: {stem})")


if __name__ == "__main__":
    main()
