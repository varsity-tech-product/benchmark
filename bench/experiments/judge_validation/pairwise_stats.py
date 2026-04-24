"""Pairwise judge statistics — aggregates judge_pairwise_runs.json.

Metrics:
    stronger_preferred_rate: fraction of successful records where the
        judge preferred the stronger response (ties count as 0.5).
        Target ≥ 0.9.
    swap_consistency: fraction of (pair_id, run_index) triplets where
        the two swap orderings agreed on the stronger side. Target ≥ 0.9.
    a_side_preference_rate: fraction of records where the judge picked
        slot "A". Ideal ~0.5 on balanced swap data; deviation evidences
        position bias.
    tie_rate: diagnostic — fraction of records where the judge returned
        "tie".

Per-pair and per-dimension slices are included for drill-down.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

PASS_THRESHOLD = {
    "stronger_preferred_rate": 0.9,
    "swap_consistency": 0.9,
    "a_side_preference_tolerance": 0.15,
}


def _overall(records: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [r for r in records if r.get("status") == "success"]
    total = len(successes)
    if total == 0:
        return {
            "records": 0,
            "stronger_preferred_rate": 0.0,
            "tie_rate": 0.0,
            "a_side_preference_rate": 0.0,
        }
    stronger_picks = sum(1 for r in successes if r.get("preferred_side") == "stronger")
    ties = sum(1 for r in successes if r.get("preferred_side") == "tie")
    a_picks = sum(1 for r in successes if r.get("preferred_slot") == "A")
    return {
        "records": total,
        "stronger_preferred_rate": round(
            (stronger_picks + 0.5 * ties) / total, 4
        ),
        "tie_rate": round(ties / total, 4),
        "a_side_preference_rate": round(a_picks / total, 4),
    }


def _swap_consistency(records: list[dict[str, Any]]) -> float:
    """For each (pair_id, run_index), check both swap orderings picked the
    same actual sample (or both tied). Skip triplets missing a side."""

    by_key: dict[tuple[str, int], dict[str, str]] = {}
    for record in records:
        if record.get("status") != "success":
            continue
        key = (str(record.get("pair_id", "")), int(record.get("run_index", 0)))
        side = str(record.get("a_side", ""))
        preferred_side = record.get("preferred_side")
        if preferred_side is None:
            continue
        by_key.setdefault(key, {})[side] = str(preferred_side)

    comparable = [
        sides
        for sides in by_key.values()
        if "stronger" in sides and "weaker" in sides
    ]
    if not comparable:
        return 0.0
    consistent = sum(1 for sides in comparable if sides["stronger"] == sides["weaker"])
    return round(consistent / len(comparable), 4)


def _per_pair(
    records: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        pair_id = pair["pair_id"]
        pair_records = [r for r in records if r.get("pair_id") == pair_id]
        overall = _overall(pair_records)
        overall.update(
            {
                "pair_id": pair_id,
                "dimension": pair.get("dimension"),
                "registry_rubric_id": pair.get("registry_rubric_id"),
                "swap_consistency": _swap_consistency(pair_records),
            }
        )
        rows.append(overall)
    return rows


def _per_dimension(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_dim: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        dim = str(record.get("dimension", ""))
        by_dim.setdefault(dim, []).append(record)
    rows = []
    for dim, dim_records in sorted(by_dim.items()):
        overall = _overall(dim_records)
        overall.update(
            {
                "dimension": dim,
                "swap_consistency": _swap_consistency(dim_records),
            }
        )
        rows.append(overall)
    return rows


def _gate(overall: dict[str, Any], swap_consistency: float) -> dict[str, Any]:
    failures: list[str] = []
    status = "pass"
    if overall["records"] == 0:
        failures.append("no_successful_records")
        status = "fail"
    if overall["stronger_preferred_rate"] < PASS_THRESHOLD["stronger_preferred_rate"]:
        failures.append("stronger_preferred_rate_below_target")
        status = "needs_review" if status == "pass" else status
    if swap_consistency < PASS_THRESHOLD["swap_consistency"]:
        failures.append("swap_consistency_below_target")
        status = "needs_review" if status == "pass" else status
    a_side_rate = overall["a_side_preference_rate"]
    if abs(a_side_rate - 0.5) > PASS_THRESHOLD["a_side_preference_tolerance"]:
        failures.append("a_side_preference_drift")
        status = "needs_review" if status == "pass" else status
    return {
        "status": status,
        "failures": failures or None,
        "thresholds": dict(PASS_THRESHOLD),
    }


def compute_pairwise_stats(
    *,
    corpus: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    pairs = corpus.get("adversarial_pairs", [])
    overall = _overall(records)
    swap = _swap_consistency(records)
    overall_out = dict(overall)
    overall_out["swap_consistency"] = swap
    per_pair = _per_pair(records, pairs)
    per_dimension = _per_dimension(records)
    return {
        "version": "judge_pairwise_stats_v1",
        "counts": {
            "pairs": len(pairs),
            "records": len(records),
            "successful_records": overall["records"],
        },
        "overall": overall_out,
        "per_pair": per_pair,
        "per_dimension": per_dimension,
        "gate": _gate(overall_out, swap),
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        tmp = Path(fh.name)
    tmp.replace(path)


def _markdown(stats: dict[str, Any], run_id: str) -> str:
    o = stats["overall"]
    gate = stats["gate"]
    lines = [
        f"# Pairwise Judge Reliability — {run_id}",
        "",
        "## Overall",
        "",
        "| Metric | Value | Target |",
        "| --- | --- | --- |",
        f"| Records | {o['records']} | — |",
        (
            "| Stronger preferred rate "
            f"| {o['stronger_preferred_rate']:.4f} "
            f"| ≥ {PASS_THRESHOLD['stronger_preferred_rate']} |"
        ),
        (
            "| Swap consistency "
            f"| {o['swap_consistency']:.4f} "
            f"| ≥ {PASS_THRESHOLD['swap_consistency']} |"
        ),
        (
            "| A-side preference rate "
            f"| {o['a_side_preference_rate']:.4f} "
            f"| 0.50 ± {PASS_THRESHOLD['a_side_preference_tolerance']} |"
        ),
        f"| Tie rate | {o['tie_rate']:.4f} | — |",
        "",
        f"Gate: **{gate['status']}**",
    ]
    if gate["failures"]:
        lines.append("Failures: " + ", ".join(gate["failures"]))
    lines.extend(
        [
            "",
            "## Per pair",
            "",
            "| pair_id | dimension | records | stronger_preferred | swap_consistency | ties |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in stats["per_pair"]:
        lines.append(
            "| {pair} | {dim} | {recs} | {sp:.4f} | {sc:.4f} | {tr:.4f} |".format(
                pair=row["pair_id"],
                dim=row.get("dimension", ""),
                recs=row["records"],
                sp=row["stronger_preferred_rate"],
                sc=row["swap_consistency"],
                tr=row["tie_rate"],
            )
        )
    lines.extend(
        [
            "",
            "## Per dimension",
            "",
            "| dimension | records | stronger_preferred | swap_consistency | ties |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in stats["per_dimension"]:
        lines.append(
            "| {dim} | {recs} | {sp:.4f} | {sc:.4f} | {tr:.4f} |".format(
                dim=row["dimension"],
                recs=row["records"],
                sp=row["stronger_preferred_rate"],
                sc=row["swap_consistency"],
                tr=row["tie_rate"],
            )
        )
    return "\n".join(lines) + "\n"


def write_pairwise_reports(
    *,
    stats: dict[str, Any],
    run_id: str,
    output_dir: Path,
) -> dict[str, str]:
    stats_path = output_dir / "judge_pairwise_stats.json"
    md_path = output_dir / "judge_pairwise_report.md"
    payload = dict(stats)
    payload["run_id"] = run_id
    _atomic_write(stats_path, payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_markdown(stats, run_id), encoding="utf-8")
    return {"stats": str(stats_path), "markdown": str(md_path)}
