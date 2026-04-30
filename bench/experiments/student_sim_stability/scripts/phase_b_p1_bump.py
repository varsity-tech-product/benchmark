"""Phase B addendum: bump S5 from 24 to 36 (per (persona, model) cell 2 -> 3).

Adds 12 new S5 samples (one per cell), preserves existing 24 S5 + 39 Phase A
labels. Re-snapshots llm_judge_labels.json. Appends new rows to CSV with
empty label fields.
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

S5_TARGET_PER_CELL = 3  # was 2
SEED = 2027  # different seed from Phase B main extension


def main() -> int:
    sys.path.insert(0, str(BENCH))
    from experiments.student_sim_stability.analysis.human_alignment import (
        _write_llm_label_snapshot,
    )

    manifest_path = ALIGN_DIR / "sample_manifest.json"
    csv_path = ALIGN_DIR / "human_label_template.csv"
    manifest = json.load(open(manifest_path))
    existing = list(manifest["samples"])
    existing_ids = {s["eval_id"] for s in existing}

    # Existing S5 distribution per (persona, model)
    s5_count: dict[tuple[str, str], int] = defaultdict(int)
    for s in existing:
        if s["dimension"] == "S5":
            s5_count[(s["persona_id"], s["model"])] += 1

    # Candidate pool
    pool: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for path in sorted(INPUT_DIR.glob("S5__*.json")):
        try:
            payload = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        eid = payload.get("eval_id") or path.stem
        if eid in existing_ids:
            continue
        md = payload.get("metadata") or {}
        persona = md.get("persona_id")
        model = md.get("model")
        if not (persona and model):
            continue
        pool[(persona, model)].append(
            {
                "eval_id": eid,
                "dimension": "S5",
                "source_file": md.get("source_file") or "",
                "persona_id": persona,
                "task_id": md.get("task_id") or "",
                "model": model,
                "judge_input_file": path.name,
            }
        )

    rng = random.Random(SEED)
    new_samples: list[dict] = []
    for persona in PERSONAS:
        for model in MODELS:
            existing_n = s5_count[(persona, model)]
            need = max(0, S5_TARGET_PER_CELL - existing_n)
            cands = pool.get((persona, model), [])
            if need == 0 or not cands:
                continue
            shuffled = list(cands)
            rng.shuffle(shuffled)
            new_samples.extend(shuffled[:need])

    print(f"Existing total: {len(existing)}; adding {len(new_samples)} S5")

    combined = existing + new_samples

    new_manifest = dict(manifest)
    new_manifest["sample_count"] = len(combined)
    new_manifest["sample_limit"] = len(combined)
    new_manifest["samples"] = combined
    new_manifest["phase_b_addendum_s5_added"] = len(new_samples)
    new_manifest["phase_b_addendum_seed"] = SEED
    new_manifest["sampling_policy"] = (
        "Phase A (rows 0-38): stratified per (dim, persona); S4 disagreement-prio. "
        "Phase B (rows 39-156): stratified per (dim, persona, model); random S4. "
        "Phase B S5 addendum (rows 157+): bump S5 per-cell target 2 -> 3 to "
        "strengthen judge-alignment power on the dim where Gemini judge is most "
        "off (MAD vs human = 1.375 vs Sonnet/GPT 0.75 in Phase A)."
    )
    tmp = manifest_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(new_manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    shutil.move(tmp, manifest_path)
    print(f"Manifest now {len(combined)} samples")

    # Append to CSV
    with open(csv_path, encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    with open(csv_path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        for s in new_samples:
            row = {col: "" for col in header}
            row["eval_id"] = s["eval_id"]
            row["dimension"] = s["dimension"]
            row["source_file"] = s["source_file"]
            row["persona_id"] = s["persona_id"]
            row["task_id"] = s["task_id"]
            row["model"] = s["model"]
            writer.writerow(row)
    print(f"Appended {len(new_samples)} rows to {csv_path}")

    snap = _write_llm_label_snapshot(RESULTS, combined)
    print(f"Re-snapped llm_judge_labels.json: {snap['llm_label_count']} labels")

    # Verify S5 cells
    s5_after: dict[tuple[str, str], int] = defaultdict(int)
    for s in combined:
        if s["dimension"] == "S5":
            s5_after[(s["persona_id"], s["model"])] += 1
    print()
    print("S5 per (persona, model) cell after bump:")
    for persona in PERSONAS:
        for model in MODELS:
            print(f"  {persona:<26} / {model:<25} : {s5_after[(persona, model)]}")
    print(f"S5 total: {sum(s5_after.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
