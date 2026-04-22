"""HTML report generation for Tutor scoring validation."""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _distribution(values: list[int]) -> dict[int, int]:
    return {i: values.count(i) for i in range(1, 6)}


def _label_key(label: dict) -> tuple[str, str]:
    return (str(label.get("session_id", "")), str(label.get("dimension", "")))


def _score_key(row: dict) -> tuple[str, str]:
    return (str(row.get("session_id", "")), str(row.get("dimension", "")))


def compute_stats(score_rows: list[dict], labels: list[dict]) -> dict:
    active_rows = [r for r in score_rows if r.get("active") and r.get("score_raw")]

    by_dim: dict[str, list[int]] = defaultdict(list)
    by_category_dim: dict[str, list[int]] = defaultdict(list)
    for row in active_rows:
        raw = int(row["score_raw"])
        dim = row["dimension_short"]
        by_dim[dim].append(raw)
        by_category_dim[f"{row['category']}__{dim}"].append(raw)

    repeat_groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in active_rows:
        key = (row["task_id"], row["persona_id"], row["dimension_short"])
        repeat_groups[key].append(row)

    repeat_deltas = []
    for (task_id, persona_id, dim), group in repeat_groups.items():
        if len(group) != 2:
            continue
        ordered = sorted(group, key=lambda r: int(r.get("repeat_index") or 0))
        scores = [int(r["score_raw"]) for r in ordered]
        repeat_deltas.append(
            {
                "task_id": task_id,
                "persona_id": persona_id,
                "dimension": dim,
                "scores": scores,
                "delta": abs(scores[0] - scores[1]),
            }
        )

    label_by_key = {_label_key(label): label for label in labels}
    agreement_rows = []
    for row in active_rows:
        label = label_by_key.get(_score_key(row))
        if not label or label.get("score_raw") in ("", None):
            continue
        judge_score = int(row["score_raw"])
        omni_score = int(label["score_raw"])
        agreement_rows.append(
            {
                "session_id": row["session_id"],
                "task_id": row["task_id"],
                "category": row["category"],
                "persona_id": row["persona_id"],
                "dimension": row["dimension_short"],
                "judge_score": judge_score,
                "omniscient_score": omni_score,
                "delta": abs(judge_score - omni_score),
                "confidence": label.get("confidence", ""),
                "reason": label.get("reason", ""),
            }
        )

    d6_labels = [
        label
        for label in labels
        if label.get("dimension") == "D6_safety_boundaries"
        and label.get("score_raw") not in ("", None)
    ]

    return {
        "dimension_distribution": {
            dim: {
                "n": len(values),
                "mean": _safe_mean(values),
                "distribution": _distribution(values),
            }
            for dim, values in sorted(by_dim.items())
        },
        "category_dimension_mean": {
            key: {"n": len(values), "mean": _safe_mean(values)}
            for key, values in sorted(by_category_dim.items())
        },
        "repeat_stability": {
            "groups": len(repeat_deltas),
            "mean_delta": _safe_mean([r["delta"] for r in repeat_deltas]),
            "within_one_rate": (
                round(
                    sum(1 for r in repeat_deltas if r["delta"] <= 1)
                    / len(repeat_deltas),
                    4,
                )
                if repeat_deltas
                else None
            ),
            "large_disagreements": [r for r in repeat_deltas if r["delta"] >= 2],
        },
        "omniscient_agreement": {
            "n": len(agreement_rows),
            "exact_rate": (
                round(
                    sum(1 for r in agreement_rows if r["delta"] == 0)
                    / len(agreement_rows),
                    4,
                )
                if agreement_rows
                else None
            ),
            "within_one_rate": (
                round(
                    sum(1 for r in agreement_rows if r["delta"] <= 1)
                    / len(agreement_rows),
                    4,
                )
                if agreement_rows
                else None
            ),
            "mean_delta": _safe_mean([r["delta"] for r in agreement_rows]),
            "large_disagreements": [r for r in agreement_rows if r["delta"] >= 2],
        },
        "d6": {
            "labels": len(d6_labels),
            "trigger_counts": Counter(
                str((label.get("d6") or {}).get("trigger_present"))
                for label in d6_labels
            ),
            "boundary_counts": Counter(
                str((label.get("d6") or {}).get("boundary_result"))
                for label in d6_labels
            ),
        },
    }


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _dist_table(stats: dict) -> str:
    rows = []
    for dim, entry in stats["dimension_distribution"].items():
        dist = entry["distribution"]
        rows.append(
            [
                dim,
                entry["n"],
                entry["mean"],
                dist[1],
                dist[2],
                dist[3],
                dist[4],
                dist[5],
            ]
        )
    return _table(["Dimension", "n", "Mean", "1", "2", "3", "4", "5"], rows)


def _large_disagreement_table(stats: dict) -> str:
    rows = [
        [
            r["task_id"],
            r["persona_id"],
            r["dimension"],
            r["judge_score"],
            r["omniscient_score"],
            r["delta"],
            r["confidence"],
        ]
        for r in stats["omniscient_agreement"]["large_disagreements"][:50]
    ]
    if not rows:
        return "<p>No large judge-vs-omniscient disagreements recorded.</p>"
    return _table(
        ["Task", "Persona", "Dim", "Judge", "Omniscient", "Delta", "Confidence"],
        rows,
    )


def generate_html_report(
    *,
    score_rows: list[dict],
    labels: list[dict],
    output_path: Path,
    stats_path: Path,
) -> dict:
    stats = compute_stats(score_rows, labels)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(stats, indent=2, ensure_ascii=False, default=dict), encoding="utf-8"
    )

    agreement = stats["omniscient_agreement"]
    stability = stats["repeat_stability"]
    d6 = stats["d6"]

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tutor Scoring Validation</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; }}
    h1, h2 {{ color: #102a43; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 8px; padding: 14px; background: #f8fafc; }}
    .metric {{ font-size: 28px; font-weight: 700; color: #1864ab; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 14px; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e7f5ff; }}
    .note {{ background: #fff7e6; border: 1px solid #ffd8a8; padding: 12px; border-radius: 8px; }}
    code {{ background: #eef2f7; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Tutor Scoring Validation</h1>
  <p>This report validates the scoring system, not Haiku 4.5 model quality.</p>

  <div class="cards">
    <div class="card"><div>Scored Rows</div><div class="metric">{len([r for r in score_rows if r.get("active")])}</div></div>
    <div class="card"><div>Omniscient Labels</div><div class="metric">{agreement["n"]}</div></div>
    <div class="card"><div>Exact Agreement</div><div class="metric">{agreement["exact_rate"]}</div></div>
    <div class="card"><div>Within-One Agreement</div><div class="metric">{agreement["within_one_rate"]}</div></div>
    <div class="card"><div>Repeat Within-One</div><div class="metric">{stability["within_one_rate"]}</div></div>
  </div>

  <h2>Dimension Distribution</h2>
  {_dist_table(stats)}

  <h2>Repeat Stability</h2>
  <p>Groups: {stability["groups"]}; mean raw-score delta: {stability["mean_delta"]}; large disagreements: {len(stability["large_disagreements"])}.</p>

  <h2>Omniscient Calibration Agreement</h2>
  <p>Compared Sonnet 4.6 Tutor judge scores against Codex omniscient labels when labels are present.</p>
  {_large_disagreement_table(stats)}

  <h2>D6 Safety Boundary Notes</h2>
  <div class="note">
    <p>D6 is not expected to cover the full 1-5 range on natural no-trigger samples. No-trigger cases should cluster near 3 by rubric design.</p>
    <p>D6 labeled rows: {d6["labels"]}</p>
    <p>Trigger counts: <code>{html.escape(json.dumps(dict(d6["trigger_counts"]), ensure_ascii=False))}</code></p>
    <p>Boundary counts: <code>{html.escape(json.dumps(dict(d6["boundary_counts"]), ensure_ascii=False))}</code></p>
  </div>
</body>
</html>
"""
    output_path.write_text(html_text, encoding="utf-8")
    return stats
