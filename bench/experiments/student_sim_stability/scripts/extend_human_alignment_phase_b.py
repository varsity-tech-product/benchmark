"""Phase B: extend the human-alignment pilot from N=39 to N≈156.

Preserves all 39 existing labels (rows 1-39 in human_label_template.csv).
Adds ~117 new samples via stratified random draw from (dim, persona, model)
cells, with random S4 (no disagreement-priority).

Outputs (overwrites/appends in place):
- results/main/human_alignment/sample_manifest.json (combined manifest)
- results/main/human_alignment/human_label_template.csv (appended new rows)
- results/main/human_alignment/llm_judge_labels.json (re-snapshotted union)
"""

from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

BENCH = Path("/Users/richsion/Desktop/benchmark/bench")
RESULTS = BENCH / "experiments/student_sim_stability/results/main"
INPUT_DIR = RESULTS / "judge_inputs"
ALIGN_DIR = RESULTS / "human_alignment"

PERSONAS = [
    "developer_crossover",
    "double_novice",
    "finance_veteran",
    "fullstack_practitioner",
]
MODELS = ["claude-sonnet-4-6", "gpt-5.4", "gemini-3.1-pro-preview"]

# Phase B target per (dim, persona, model) cell.
PHASE_B_TARGET = {
    "S1": 3,  # 4 personas x 3 models x 3 = 36
    "S3": 1,  # 4 x 3 x 1 = 12
    "S2": 3,  # 36
    "S5": 2,  # 4 x 3 x 2 = 24
    "S4": 3,  # 36 (random, no disagreement priority)
    "S6": 1,  # 12
}

# 36 + 12 + 36 + 24 + 36 + 12 = 156 target total

SEED = 2026


def main() -> int:
    if not ALIGN_DIR.exists():
        print(f"missing {ALIGN_DIR}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(BENCH))
    from experiments.student_sim_stability.analysis.human_alignment import (
        _write_llm_label_snapshot,
    )

    manifest_path = ALIGN_DIR / "sample_manifest.json"
    csv_path = ALIGN_DIR / "human_label_template.csv"
    manifest = json.load(open(manifest_path))
    existing_samples = list(manifest.get("samples", []))
    existing_ids = {s["eval_id"] for s in existing_samples}

    print(f"Loaded existing manifest: {len(existing_samples)} samples")

    # Existing distribution per (dim, persona, model)
    existing_count: dict[tuple[str, str, str], int] = defaultdict(int)
    for s in existing_samples:
        key = (s["dimension"], s["persona_id"], s["model"])
        existing_count[key] += 1

    # Read all judge inputs as candidate pool
    candidates: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for path in sorted(INPUT_DIR.glob("*.json")):
        try:
            payload = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        eid = payload.get("eval_id") or path.stem
        if eid in existing_ids:
            continue
        dim = payload.get("dimension")
        md = payload.get("metadata") or {}
        persona = md.get("persona_id")
        model = md.get("model")
        if not (dim and persona and model):
            continue
        candidates[(dim, persona, model)].append(
            {
                "eval_id": eid,
                "dimension": dim,
                "source_file": md.get("source_file")
                or md.get("persona_source_file")
                or "",
                "persona_id": persona,
                "task_id": md.get("task_id") or "",
                "model": model,
                "judge_input_file": path.name,
            }
        )

    # Random sample to fill deficits
    rng = random.Random(SEED)
    new_samples: list[dict] = []
    coverage_log: list[str] = []
    for dim, target in PHASE_B_TARGET.items():
        for persona in PERSONAS:
            for model in MODELS:
                key = (dim, persona, model)
                existing_n = existing_count[key]
                needed = max(0, target - existing_n)
                pool = candidates.get(key, [])
                if needed == 0:
                    continue
                if not pool:
                    coverage_log.append(
                        f"  WARN: no candidates for {key} (existing={existing_n}, "
                        f"need {needed} more)"
                    )
                    continue
                shuffled = list(pool)
                rng.shuffle(shuffled)
                chosen = shuffled[: min(needed, len(shuffled))]
                if len(chosen) < needed:
                    coverage_log.append(
                        f"  WARN: pool shortfall for {key}: "
                        f"got {len(chosen)} of {needed} (pool size {len(pool)})"
                    )
                new_samples.extend(chosen)

    print(f"New samples to add: {len(new_samples)}")
    if coverage_log:
        print("Coverage warnings:")
        for line in coverage_log:
            print(line)

    # Combined manifest
    combined_samples = existing_samples + new_samples
    new_manifest = {
        "human_alignment_status": "sampled",
        "schema_version": "human_alignment_v2",
        "sample_limit": len(combined_samples),
        "sample_count": len(combined_samples),
        "sampling_policy": (
            "Phase A (rows 0-{phase_a}): stratified per (dimension, persona); "
            "S4 disagreement-prioritized. Phase B (rows {phase_a}-{combined}): "
            "stratified per (dimension, persona, model) with random S4. "
            "Existing labels preserved; new rows have empty label fields."
        ).format(phase_a=len(existing_samples) - 1, combined=len(combined_samples) - 1),
        "phase_a_count": len(existing_samples),
        "phase_b_added": len(new_samples),
        "phase_b_target_per_cell": PHASE_B_TARGET,
        "phase_b_seed": SEED,
        "samples": combined_samples,
    }
    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(new_manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    shutil.move(tmp, manifest_path)
    print(f"Wrote {manifest_path}: {len(combined_samples)} total samples")

    # Append new rows to CSV — read existing header to preserve any user-added cols
    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        existing_csv_eids = {row[0] for row in reader if row}
    print(
        f"Existing CSV: {len(existing_csv_eids)} eval_ids; header has "
        f"{len(header)} columns"
    )

    appended = 0
    with open(csv_path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        for s in new_samples:
            if s["eval_id"] in existing_csv_eids:
                continue
            row = {col: "" for col in header}
            row["eval_id"] = s["eval_id"]
            row["dimension"] = s["dimension"]
            row["source_file"] = s.get("source_file", "")
            row["persona_id"] = s["persona_id"]
            row["task_id"] = s.get("task_id", "")
            row["model"] = s["model"]
            writer.writerow(row)
            appended += 1
    print(f"Appended {appended} new rows to {csv_path}")

    # Re-snap LLM labels for the combined set
    snap = _write_llm_label_snapshot(RESULTS, combined_samples)
    print(
        f"Re-snapped llm_judge_labels.json: {snap['llm_label_count']} labels, "
        f"missing={snap['missing_judge_outputs']}"
    )

    # Final per-cell coverage summary
    final_count: dict[tuple[str, str, str], int] = defaultdict(int)
    for s in combined_samples:
        final_count[(s["dimension"], s["persona_id"], s["model"])] += 1

    print()
    print("=== Final per (dim, persona, model) cell coverage ===")
    print(f"{'cell':<60} | {'n':>3} | target")
    for dim in sorted(PHASE_B_TARGET):
        target = PHASE_B_TARGET[dim]
        for persona in PERSONAS:
            for model in MODELS:
                n = final_count.get((dim, persona, model), 0)
                tag = "OK" if n >= target else "SHORT"
                print(f"  {dim} / {persona} / {model:<25} | {n:>3} | {target} {tag}")

    # Per-dim totals
    print()
    print("=== Per-dim totals ===")
    by_dim: dict[str, int] = defaultdict(int)
    for s in combined_samples:
        by_dim[s["dimension"]] += 1
    for dim in sorted(by_dim):
        print(f"  {dim}: {by_dim[dim]}")
    print(f"TOTAL: {len(combined_samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
