"""Reliability metrics and reports for judge validation."""

from __future__ import annotations

import html
import itertools
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _record_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    metadata = record.get("judge_metadata") or {}
    return (
        str(record.get("sample_id", "")),
        _record_rubric_id(record),
        _record_dimension(record),
        str(record.get("judge_model") or metadata.get("judge_model") or ""),
        _record_prompt_variant(record),
    )


def _record_rubric_id(record: dict[str, Any]) -> str:
    metadata = record.get("judge_metadata") or {}
    return str(record.get("registry_rubric_id") or metadata.get("rubric_id") or "")


def _record_dimension(record: dict[str, Any]) -> str:
    metadata = record.get("judge_metadata") or {}
    return str(record.get("dimension") or metadata.get("dimension") or "")


def _record_prompt_variant(record: dict[str, Any]) -> str:
    metadata = record.get("judge_metadata") or {}
    return str(
        record.get("prompt_variant_id")
        or metadata.get("prompt_variant_id")
        or "baseline"
    )


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


def _text_tokens(record: dict[str, Any]) -> set[str]:
    evidence = record.get("evidence") or []
    if isinstance(evidence, list):
        evidence_text = " ".join(str(item) for item in evidence)
    else:
        evidence_text = str(evidence)
    text = f"{evidence_text} {record.get('reason') or ''}".lower()
    return set(re.findall(r"[a-z0-9_]+", text))


def _jaccard(left: set[str], right: set[str]) -> float | None:
    if not left and not right:
        return None
    union = left | right
    if not union:
        return None
    return len(left & right) / len(union)


def _mean_scores_by_sample(
    records: list[dict[str, Any]],
    *,
    sample_id: str,
    rubric_id: str,
    dimension: str,
) -> list[float]:
    return [
        float(score)
        for score in (
            _raw_score(record)
            for record in records
            if str(record.get("sample_id", "")) == sample_id
            and _record_rubric_id(record) == rubric_id
            and _record_dimension(record) == dimension
        )
        if score is not None
    ]


def compute_reliability_stats(
    *,
    corpus: dict[str, Any],
    records: list[dict[str, Any]],
    pass_threshold: float | None = None,
) -> dict[str, Any]:
    """Compute judge reliability metrics from repeated score records."""

    threshold = float(pass_threshold or corpus.get("pass_threshold") or 3)
    successful = [
        record
        for record in records
        if record.get("status") == "success" and _raw_score(record) is not None
    ]

    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
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
        sample_id, rubric_id, dimension, judge_model, prompt_variant_id = key
        stability_groups.append(
            {
                "sample_id": sample_id,
                "rubric_id": rubric_id,
                "dimension": dimension,
                "judge_model": judge_model,
                "prompt_variant_id": prompt_variant_id,
                "runs": len(clean_scores),
                "scores": clean_scores,
                "mean_score": _safe_mean(clean_scores),
                "max_delta": round(max(pair_deltas), 4),
                "mean_delta": _safe_mean(pair_deltas),
                "within_one": all(delta <= 1 for delta in pair_deltas),
                "pass_fail_flip": flip,
            }
        )

    variant_grouped: dict[
        tuple[str, str, str, str], dict[str, list[dict[str, Any]]]
    ] = defaultdict(lambda: defaultdict(list))
    for record in successful:
        variant_grouped[
            (
                str(record.get("sample_id", "")),
                _record_rubric_id(record),
                _record_dimension(record),
                str(
                    record.get("judge_model")
                    or (record.get("judge_metadata") or {}).get("judge_model")
                    or ""
                ),
            )
        ][_record_prompt_variant(record)].append(record)

    prompt_variant_rows: list[dict[str, Any]] = []
    prompt_variant_deltas: list[float] = []
    prompt_variant_flip_groups = 0
    for key, variants in sorted(variant_grouped.items()):
        if len(variants) < 2:
            continue
        variant_means: dict[str, float] = {}
        for variant_id, variant_records in variants.items():
            scores = [
                score
                for score in (_raw_score(record) for record in variant_records)
                if score is not None
            ]
            mean_score = _safe_mean([float(score) for score in scores])
            if mean_score is not None:
                variant_means[variant_id] = mean_score
        if len(variant_means) < 2:
            continue
        pair_deltas = [
            abs(a - b) for a, b in itertools.combinations(variant_means.values(), 2)
        ]
        prompt_variant_deltas.extend(pair_deltas)
        pass_values = {_pass(score, threshold) for score in variant_means.values()}
        flip = len(pass_values) > 1
        if flip:
            prompt_variant_flip_groups += 1
        sample_id, rubric_id, dimension, judge_model = key
        prompt_variant_rows.append(
            {
                "sample_id": sample_id,
                "rubric_id": rubric_id,
                "dimension": dimension,
                "judge_model": judge_model,
                "variant_means": dict(sorted(variant_means.items())),
                "max_variant_delta": round(max(pair_deltas), 4),
                "mean_variant_delta": _safe_mean(pair_deltas),
                "within_one": all(delta <= 1 for delta in pair_deltas),
                "pass_fail_flip": flip,
            }
        )

    consistency_groups: list[dict[str, Any]] = []
    consistency_scores: list[float] = []
    for key, group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        pair_scores = [
            score
            for score in (
                _jaccard(_text_tokens(a), _text_tokens(b))
                for a, b in itertools.combinations(group, 2)
            )
            if score is not None
        ]
        if not pair_scores:
            continue
        consistency_scores.extend(pair_scores)
        sample_id, rubric_id, dimension, judge_model, prompt_variant_id = key
        consistency_groups.append(
            {
                "sample_id": sample_id,
                "rubric_id": rubric_id,
                "dimension": dimension,
                "judge_model": judge_model,
                "prompt_variant_id": prompt_variant_id,
                "runs": len(group),
                "mean_pairwise_text_jaccard": _safe_mean(pair_scores),
                "min_pairwise_text_jaccard": round(min(pair_scores), 4),
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

    sensitivity_rows: list[dict[str, Any]] = []
    for case in corpus.get("sensitivity_cases", []):
        baseline_id = str(case.get("baseline_sample_id", ""))
        perturbed_id = str(case.get("perturbed_sample_id", ""))
        rubric_id = str(case.get("registry_rubric_id", ""))
        dimension = str(case.get("dimension", ""))
        minimum_margin = float(case.get("minimum_margin", 0))
        baseline_scores = _mean_scores_by_sample(
            successful,
            sample_id=baseline_id,
            rubric_id=rubric_id,
            dimension=dimension,
        )
        perturbed_scores = _mean_scores_by_sample(
            successful,
            sample_id=perturbed_id,
            rubric_id=rubric_id,
            dimension=dimension,
        )
        baseline_mean = _safe_mean(baseline_scores)
        perturbed_mean = _safe_mean(perturbed_scores)
        comparable = baseline_mean is not None and perturbed_mean is not None
        expected_direction = str(case.get("expected_direction", "baseline_higher"))
        margin = round(baseline_mean - perturbed_mean, 4) if comparable else None
        if not comparable:
            status = "missing"
        elif expected_direction == "perturbed_higher":
            status = "pass" if margin <= -minimum_margin else "fail"
        else:
            status = "pass" if margin >= minimum_margin else "fail"
        sensitivity_rows.append(
            {
                "case_id": case.get("case_id"),
                "factor": case.get("factor"),
                "registry_rubric_id": rubric_id,
                "dimension": dimension,
                "baseline_sample_id": baseline_id,
                "perturbed_sample_id": perturbed_id,
                "baseline_mean": baseline_mean,
                "perturbed_mean": perturbed_mean,
                "score_margin": margin,
                "minimum_margin": minimum_margin,
                "expected_direction": expected_direction,
                "status": status,
            }
        )

    comparable_sensitivity = [
        row for row in sensitivity_rows if row["status"] in {"pass", "fail"}
    ]
    pass_sensitivity = [
        row for row in comparable_sensitivity if row["status"] == "pass"
    ]

    large_disagreements = [
        row for row in stability_groups if row["max_delta"] >= 2
    ]
    adversarial_failures = [
        row for row in adversarial_rows if row["status"] != "pass"
    ]
    sensitivity_failures = [
        row for row in sensitivity_rows if row["status"] != "pass"
    ]
    low_consistency_examples = [
        row
        for row in consistency_groups
        if (
            row["mean_pairwise_text_jaccard"] is not None
            and row["mean_pairwise_text_jaccard"] < 0.2
        )
    ]

    return {
        "version": "judge_validation_stats_v2",
        "counts": {
            "corpus_items": len(corpus.get("items", [])),
            "records": len(records),
            "successful_records": len(successful),
            "stability_groups": len(stability_groups),
            "prompt_variant_groups": len(prompt_variant_rows),
            "adversarial_pairs": len(corpus.get("adversarial_pairs", [])),
            "comparable_adversarial_pairs": len(comparable_pairs),
            "sensitivity_cases": len(corpus.get("sensitivity_cases", [])),
            "comparable_sensitivity_cases": len(comparable_sensitivity),
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
        "prompt_format": {
            "mean_absolute_variant_delta": _safe_mean(prompt_variant_deltas),
            "within_one_variant_rate": (
                round(
                    sum(1 for delta in prompt_variant_deltas if delta <= 1)
                    / len(prompt_variant_deltas),
                    4,
                )
                if prompt_variant_deltas
                else None
            ),
            "pass_fail_variant_flip_rate": (
                round(prompt_variant_flip_groups / len(prompt_variant_rows), 4)
                if prompt_variant_rows
                else None
            ),
            "groups": prompt_variant_rows,
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
        "sensitivity": {
            "pass_rate": (
                round(len(pass_sensitivity) / len(comparable_sensitivity), 4)
                if comparable_sensitivity
                else None
            ),
            "cases": sensitivity_rows,
            "failure_examples": sensitivity_failures[:25],
        },
        "evidence_consistency": {
            "evidence_coverage_rate": (
                round(
                    sum(1 for record in successful if record.get("evidence"))
                    / len(successful),
                    4,
                )
                if successful
                else None
            ),
            "reason_coverage_rate": (
                round(
                    sum(1 for record in successful if record.get("reason"))
                    / len(successful),
                    4,
                )
                if successful
                else None
            ),
            "mean_pairwise_text_jaccard": _safe_mean(consistency_scores),
            "groups": consistency_groups,
            "low_consistency_examples": low_consistency_examples[:25],
        },
        "residual_risks": [
            "Real-run excerpts are a curated cut rather than a random sample of completed sessions; broader sampling across agents and personas is a next step.",
            "Evidence consistency uses lexical overlap as a lightweight proxy for explanation stability.",
            "Human quant expert alignment belongs to the next validation stage after automated robustness artifacts are stable.",
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
    prompt_format = stats["prompt_format"]
    adversarial = stats["adversarial"]
    sensitivity = stats["sensitivity"]
    evidence = stats["evidence_consistency"]
    counts = stats["counts"]
    lines = [
        "# Judge Reliability Report",
        "",
        f"- Run ID: {run_id or 'unrecorded'}",
        f"- Corpus items: {counts['corpus_items']}",
        f"- Successful judge records: {counts['successful_records']}",
        f"- Stability groups: {counts['stability_groups']}",
        f"- Mean absolute score delta: {stability['mean_absolute_score_delta']}",
        f"- Within-one score rate: {stability['within_one_score_rate']}",
        f"- Pass/fail flip rate: {stability['pass_fail_flip_rate']}",
        f"- Prompt-format mean variant delta: {prompt_format['mean_absolute_variant_delta']}",
        f"- Prompt-format within-one rate: {prompt_format['within_one_variant_rate']}",
        f"- Prompt-format pass/fail flip rate: {prompt_format['pass_fail_variant_flip_rate']}",
        f"- Adversarial ranking pass rate: {adversarial['ranking_pass_rate']}",
        f"- Sensitivity pass rate: {sensitivity['pass_rate']}",
        f"- Evidence coverage rate: {evidence['evidence_coverage_rate']}",
        f"- Reason coverage rate: {evidence['reason_coverage_rate']}",
        f"- Evidence/reason consistency: {evidence['mean_pairwise_text_jaccard']}",
        "",
        "## Prompt Format Robustness",
        "",
        "| Sample | Rubric | Variant Means | Max Delta | Flip |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in prompt_format["groups"]:
        lines.append(
            "| {sample_id} | {rubric} | {means} | {delta} | {flip} |".format(
                sample_id=row.get("sample_id"),
                rubric=row.get("rubric_id"),
                means=json.dumps(row.get("variant_means"), sort_keys=True),
                delta=row.get("max_variant_delta"),
                flip=row.get("pass_fail_flip"),
            )
        )
    lines.extend(
        [
            "",
            "## Sensitivity Cases",
            "",
            "| Case | Factor | Baseline Mean | Perturbed Mean | Margin | Status |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in sensitivity["cases"]:
        lines.append(
            "| {case_id} | {factor} | {baseline} | {perturbed} | {margin} | {status} |".format(
                case_id=row.get("case_id"),
                factor=row.get("factor"),
                baseline=row.get("baseline_mean"),
                perturbed=row.get("perturbed_mean"),
                margin=row.get("score_margin"),
                status=row.get("status"),
            )
        )
    lines.extend(
        [
            "",
            "## Evidence Consistency",
            "",
            "| Sample | Rubric | Variant | Mean Jaccard | Min Jaccard |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in evidence["low_consistency_examples"]:
        lines.append(
            "| {sample_id} | {rubric} | {variant} | {mean_score} | {min_score} |".format(
                sample_id=row.get("sample_id"),
                rubric=row.get("rubric_id"),
                variant=row.get("prompt_variant_id"),
                mean_score=row.get("mean_pairwise_text_jaccard"),
                min_score=row.get("min_pairwise_text_jaccard"),
            )
        )
    lines.extend(
        [
            "",
            "## Adversarial Pairs",
            "",
            "| Pair | Rubric | Stronger Mean | Weaker Mean | Margin | Status |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
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
    prompt_format = stats["prompt_format"]
    adversarial = stats["adversarial"]
    sensitivity = stats["sensitivity"]
    evidence = stats["evidence_consistency"]
    counts = stats["counts"]
    variant_rows = [
        [
            row.get("sample_id"),
            row.get("rubric_id"),
            json.dumps(row.get("variant_means"), sort_keys=True),
            row.get("max_variant_delta"),
            row.get("pass_fail_flip"),
        ]
        for row in prompt_format["groups"]
    ]
    sensitivity_rows = [
        [
            row.get("case_id"),
            row.get("factor"),
            row.get("baseline_mean"),
            row.get("perturbed_mean"),
            row.get("score_margin"),
            row.get("status"),
        ]
        for row in sensitivity["cases"]
    ]
    consistency_rows = [
        [
            row.get("sample_id"),
            row.get("rubric_id"),
            row.get("prompt_variant_id"),
            row.get("mean_pairwise_text_jaccard"),
            row.get("min_pairwise_text_jaccard"),
        ]
        for row in evidence["low_consistency_examples"]
    ]
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
  <title>Judge Reliability</title>
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
  <h1>Judge Reliability</h1>
  <p>Run ID: <code>{html.escape(run_id or "unrecorded")}</code></p>
  <div class="cards">
    <div class="card"><div>Corpus Items</div><div class="metric">{counts["corpus_items"]}</div></div>
    <div class="card"><div>Successful Records</div><div class="metric">{counts["successful_records"]}</div></div>
    <div class="card"><div>Mean Delta</div><div class="metric">{stability["mean_absolute_score_delta"]}</div></div>
    <div class="card"><div>Within-One</div><div class="metric">{stability["within_one_score_rate"]}</div></div>
    <div class="card"><div>Flip Rate</div><div class="metric">{stability["pass_fail_flip_rate"]}</div></div>
    <div class="card"><div>Prompt Variant Delta</div><div class="metric">{prompt_format["mean_absolute_variant_delta"]}</div></div>
    <div class="card"><div>Pair Pass Rate</div><div class="metric">{adversarial["ranking_pass_rate"]}</div></div>
    <div class="card"><div>Sensitivity Pass Rate</div><div class="metric">{sensitivity["pass_rate"]}</div></div>
    <div class="card"><div>Evidence Coverage</div><div class="metric">{evidence["evidence_coverage_rate"]}</div></div>
    <div class="card"><div>Reason Coverage</div><div class="metric">{evidence["reason_coverage_rate"]}</div></div>
  </div>
  <h2>Prompt Format Robustness</h2>
  {_table(["Sample", "Rubric", "Variant Means", "Max Delta", "Flip"], variant_rows)}
  <h2>Sensitivity Cases</h2>
  {_table(["Case", "Factor", "Baseline Mean", "Perturbed Mean", "Margin", "Status"], sensitivity_rows)}
  <h2>Evidence Consistency</h2>
  {_table(["Sample", "Rubric", "Variant", "Mean Jaccard", "Min Jaccard"], consistency_rows)}
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
