"""Reliability metrics and reports for judge validation."""

from __future__ import annotations

import html
import itertools
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _record_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    metadata = record.get("judge_metadata") or {}
    return (
        str(record.get("sample_id", "")),
        _record_rubric_id(record),
        _record_dimension(record),
        str(record.get("judge_model") or metadata.get("judge_model") or ""),
    )


def _record_rubric_id(record: dict[str, Any]) -> str:
    metadata = record.get("judge_metadata") or {}
    return str(record.get("registry_rubric_id") or metadata.get("rubric_id") or "")


def _record_dimension(record: dict[str, Any]) -> str:
    metadata = record.get("judge_metadata") or {}
    return str(record.get("dimension") or metadata.get("dimension") or "")


def _raw_score(record: dict[str, Any]) -> float | None:
    raw = record.get("raw_score")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    normalized = record.get("score")
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)):
        return None
    metadata = record.get("judge_metadata") or {}
    scale = metadata.get("score_scale") or {}
    lo = int(scale.get("min", 1))
    hi = int(scale.get("max", 5))
    return round(float(normalized) * (hi - lo) + lo, 4)


def _pass(score: float, threshold: float) -> bool:
    return score >= threshold


def _records_by_sample(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_sample[str(record.get("sample_id", ""))].append(record)
    return by_sample


def compute_reliability_stats(
    *,
    corpus: dict[str, Any],
    records: list[dict[str, Any]],
    pass_threshold: float | None = None,
) -> dict[str, Any]:
    """Compute Stage 1 judge reliability metrics from repeated score records."""

    threshold = float(pass_threshold or corpus.get("pass_threshold") or 3)
    successful = [
        record
        for record in records
        if record.get("status") == "success" and _raw_score(record) is not None
    ]

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in successful:
        grouped[_record_key(record)].append(record)

    stability_groups: list[dict[str, Any]] = []
    score_deltas: list[float] = []
    flip_groups = 0

    for key, group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        scores = [_raw_score(record) for record in group]
        clean_scores = [float(score) for score in scores if score is not None]
        if len(clean_scores) < 2:
            continue
        pair_deltas = [
            abs(a - b) for a, b in itertools.combinations(clean_scores, 2)
        ]
        pass_values = {_pass(score, threshold) for score in clean_scores}
        flip = len(pass_values) > 1
        if flip:
            flip_groups += 1
        score_deltas.extend(pair_deltas)
        sample_id, rubric_id, dimension, judge_model = key
        stability_groups.append(
            {
                "sample_id": sample_id,
                "rubric_id": rubric_id,
                "dimension": dimension,
                "judge_model": judge_model,
                "runs": len(clean_scores),
                "scores": clean_scores,
                "mean_score": _safe_mean(clean_scores),
                "max_delta": round(max(pair_deltas), 4),
                "mean_delta": _safe_mean(pair_deltas),
                "within_one": all(delta <= 1 for delta in pair_deltas),
                "pass_fail_flip": flip,
            }
        )

    by_sample = _records_by_sample(successful)
    adversarial_rows: list[dict[str, Any]] = []
    for pair in corpus.get("adversarial_pairs", []):
        stronger_id = str(pair.get("stronger_sample_id", ""))
        weaker_id = str(pair.get("weaker_sample_id", ""))
        pair_rubric_id = str(pair.get("registry_rubric_id", ""))
        pair_dimension = str(pair.get("dimension", ""))

        def matching_records(sample_id: str) -> list[dict[str, Any]]:
            return [
                record
                for record in by_sample.get(sample_id, [])
                if _record_rubric_id(record) == pair_rubric_id
                and _record_dimension(record) == pair_dimension
            ]

        stronger_scores = [
            score
            for score in (
                _raw_score(record) for record in matching_records(stronger_id)
            )
            if score is not None
        ]
        weaker_scores = [
            score
            for score in (
                _raw_score(record) for record in matching_records(weaker_id)
            )
            if score is not None
        ]
        stronger_mean = _safe_mean([float(score) for score in stronger_scores])
        weaker_mean = _safe_mean([float(score) for score in weaker_scores])
        comparable = stronger_mean is not None and weaker_mean is not None
        passed = bool(comparable and stronger_mean > weaker_mean)
        adversarial_rows.append(
            {
                "pair_id": pair.get("pair_id"),
                "registry_rubric_id": pair.get("registry_rubric_id"),
                "dimension": pair.get("dimension"),
                "stronger_sample_id": stronger_id,
                "weaker_sample_id": weaker_id,
                "stronger_mean": stronger_mean,
                "weaker_mean": weaker_mean,
                "score_margin": (
                    round(stronger_mean - weaker_mean, 4) if comparable else None
                ),
                "status": "pass" if passed else ("fail" if comparable else "missing"),
            }
        )

    comparable_pairs = [
        row for row in adversarial_rows if row["status"] in {"pass", "fail"}
    ]
    pass_pairs = [row for row in comparable_pairs if row["status"] == "pass"]

    large_disagreements = [
        row for row in stability_groups if row["max_delta"] >= 2
    ]
    adversarial_failures = [
        row for row in adversarial_rows if row["status"] != "pass"
    ]

    return {
        "version": "judge_validation_stats_v1",
        "counts": {
            "corpus_items": len(corpus.get("items", [])),
            "records": len(records),
            "successful_records": len(successful),
            "stability_groups": len(stability_groups),
            "adversarial_pairs": len(corpus.get("adversarial_pairs", [])),
            "comparable_adversarial_pairs": len(comparable_pairs),
        },
        "pass_threshold": threshold,
        "stability": {
            "mean_absolute_score_delta": _safe_mean(score_deltas),
            "within_one_score_rate": (
                round(
                    sum(1 for delta in score_deltas if delta <= 1)
                    / len(score_deltas),
                    4,
                )
                if score_deltas
                else None
            ),
            "pass_fail_flip_rate": (
                round(flip_groups / len(stability_groups), 4)
                if stability_groups
                else None
            ),
            "large_disagreement_examples": large_disagreements[:25],
            "groups": stability_groups,
        },
        "adversarial": {
            "ranking_pass_rate": (
                round(len(pass_pairs) / len(comparable_pairs), 4)
                if comparable_pairs
                else None
            ),
            "pairs": adversarial_rows,
            "failure_examples": adversarial_failures[:25],
        },
        "residual_risks": [
            "Stage 1 validates repeated same-prompt stability and obvious pair ranking only.",
            "Human quant expert alignment, prompt-order sensitivity, and multi-judge comparisons belong to later stages.",
            "Synthetic adversarial pairs catch clear failures and underrepresent ambiguous real transcripts.",
        ],
    }


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def markdown_report(stats: dict[str, Any], *, run_id: str = "") -> str:
    stability = stats["stability"]
    adversarial = stats["adversarial"]
    counts = stats["counts"]
    lines = [
        "# Judge Reliability Stage 1 Report",
        "",
        f"- Run ID: {run_id or 'unrecorded'}",
        f"- Corpus items: {counts['corpus_items']}",
        f"- Successful judge records: {counts['successful_records']}",
        f"- Stability groups: {counts['stability_groups']}",
        f"- Mean absolute score delta: {stability['mean_absolute_score_delta']}",
        f"- Within-one score rate: {stability['within_one_score_rate']}",
        f"- Pass/fail flip rate: {stability['pass_fail_flip_rate']}",
        f"- Adversarial ranking pass rate: {adversarial['ranking_pass_rate']}",
        "",
        "## Adversarial Pairs",
        "",
        "| Pair | Rubric | Stronger Mean | Weaker Mean | Margin | Status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in adversarial["pairs"]:
        lines.append(
            "| {pair_id} | {rubric} | {stronger} | {weaker} | {margin} | {status} |".format(
                pair_id=row.get("pair_id"),
                rubric=row.get("registry_rubric_id"),
                stronger=row.get("stronger_mean"),
                weaker=row.get("weaker_mean"),
                margin=row.get("score_margin"),
                status=row.get("status"),
            )
        )
    lines.extend(["", "## Residual Risks", ""])
    for risk in stats["residual_risks"]:
        lines.append(f"- {risk}")
    lines.append("")
    return "\n".join(lines)


def html_report(stats: dict[str, Any], *, run_id: str = "") -> str:
    stability = stats["stability"]
    adversarial = stats["adversarial"]
    counts = stats["counts"]
    pair_rows = [
        [
            row.get("pair_id"),
            row.get("registry_rubric_id"),
            row.get("stronger_mean"),
            row.get("weaker_mean"),
            row.get("score_margin"),
            row.get("status"),
        ]
        for row in adversarial["pairs"]
    ]
    disagreement_rows = [
        [
            row.get("sample_id"),
            row.get("rubric_id"),
            row.get("dimension"),
            row.get("judge_model"),
            row.get("scores"),
            row.get("max_delta"),
        ]
        for row in stability["large_disagreement_examples"]
    ]
    risks = "".join(f"<li>{html.escape(risk)}</li>" for risk in stats["residual_risks"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Judge Reliability Stage 1</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; }}
    h1, h2 {{ color: #102a43; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 8px; padding: 14px; background: #f8fafc; }}
    .metric {{ font-size: 26px; font-weight: 700; color: #1864ab; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 14px; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e7f5ff; }}
  </style>
</head>
<body>
  <h1>Judge Reliability Stage 1</h1>
  <p>Run ID: <code>{html.escape(run_id or "unrecorded")}</code></p>
  <div class="cards">
    <div class="card"><div>Corpus Items</div><div class="metric">{counts["corpus_items"]}</div></div>
    <div class="card"><div>Successful Records</div><div class="metric">{counts["successful_records"]}</div></div>
    <div class="card"><div>Mean Delta</div><div class="metric">{stability["mean_absolute_score_delta"]}</div></div>
    <div class="card"><div>Within-One</div><div class="metric">{stability["within_one_score_rate"]}</div></div>
    <div class="card"><div>Flip Rate</div><div class="metric">{stability["pass_fail_flip_rate"]}</div></div>
    <div class="card"><div>Pair Pass Rate</div><div class="metric">{adversarial["ranking_pass_rate"]}</div></div>
  </div>
  <h2>Adversarial Pair Ranking</h2>
  {_table(["Pair", "Rubric", "Stronger Mean", "Weaker Mean", "Margin", "Status"], pair_rows)}
  <h2>Large Stability Disagreements</h2>
  {_table(["Sample", "Rubric", "Dimension", "Judge Model", "Scores", "Max Delta"], disagreement_rows)}
  <h2>Residual Risks</h2>
  <ul>{risks}</ul>
</body>
</html>
"""


def write_reports(
    *,
    stats: dict[str, Any],
    run_id: str,
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats_path = output_dir / "judge_validation_stats.json"
    md_path = output_dir / "judge_reliability_report.md"
    html_path = output_dir / "judge_reliability_report.html"
    stats_path.write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(markdown_report(stats, run_id=run_id), encoding="utf-8")
    html_path.write_text(html_report(stats, run_id=run_id), encoding="utf-8")
    return {
        "stats": str(stats_path),
        "markdown": str(md_path),
        "html": str(html_path),
    }
