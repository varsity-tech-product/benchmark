"""Compute judge-qualification reliability and sensitivity reports."""

from __future__ import annotations

import argparse
import html
import itertools
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from experiments.student_sim_stability.core.contracts import CONTRACT_VERSION
from experiments.student_sim_stability.core.io_utils import (
    atomic_write_json,
    input_payload_hash,
    load_json,
)
from experiments.student_sim_stability.core.numerics import safe_mean
from experiments.student_sim_stability.core.paths import resolve_results_dir
from experiments.student_sim_stability.core.rubrics import (
    RUBRIC_VERSION,
    primary_score_field,
)
from experiments.student_sim_stability.judge_qualification.render import (
    DEFAULT_CORPUS,
    _coerce_gate_dir,
    load_corpus,
)

DEFAULT_GATES = {
    "within_one_score_rate": 0.90,
    "pass_fail_flip_rate_max": 0.05,
    "prompt_format_within_one_rate": 0.90,
    "sensitivity_pass_rate": 0.90,
    "failure_tag_hit_rate": 0.80,
    "b1_identity_match_rate": 1.0,
}

REPORT_FILENAMES = {
    "stats_json": "judge_qualification_stats.json",
    "markdown": "judge_qualification_report.md",
    "html": "judge_qualification_report.html",
}


def _score(row: dict[str, Any]) -> float | None:
    try:
        field = primary_score_field(str(row.get("dimension")))
    except KeyError:
        return None
    value = (row.get("scores") or {}).get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pass(score: float, threshold: float) -> bool:
    return score >= threshold


def _pairwise_deltas(values: list[float]) -> list[float]:
    return [abs(a - b) for a, b in itertools.combinations(values, 2)]


def _output_sources(
    output_dir: Path,
    by_model_output_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    sources = [("primary", output_dir)]
    if by_model_output_dir and by_model_output_dir.exists():
        sources.extend(
            (path.name, path)
            for path in sorted(by_model_output_dir.iterdir())
            if path.is_dir()
        )
    return sources


def _load_joined_records(
    input_dir: Path,
    output_dir: Path,
    by_model_output_dir: Path | None = None,
) -> tuple[list[dict], list[str]]:
    records: list[dict[str, Any]] = []
    output_issues: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    sources = _output_sources(output_dir, by_model_output_dir)
    for input_path in sorted(input_dir.glob("*.json")):
        input_payload = load_json(input_path)
        input_hash = input_payload_hash(input_payload)
        metadata = input_payload.get("metadata") or {}
        for source_id, source_dir in sources:
            output_path = source_dir / input_path.name
            issue_id = f"{source_id}/{input_path.name}"
            if not output_path.exists():
                output_issues.append(issue_id)
                continue
            output_payload = load_json(output_path)
            output_hash = str(output_payload.get("input_sha256") or "")
            if output_hash != input_hash:
                output_issues.append(f"{issue_id}: stale_input_sha256")
                continue
            dedupe_key = (
                input_path.name,
                str(output_payload.get("judge_model", "")),
                output_hash,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            records.append(
                {
                    **output_payload,
                    "input_file": input_path.name,
                    "output_source": source_id,
                    "sample_id": metadata.get("sample_id"),
                    "prompt_variant_id": metadata.get("prompt_variant_id", "baseline"),
                    "repeat_index": metadata.get("repeat_index"),
                    "expected_score_band": metadata.get("expected_score_band"),
                    "expected_failure_types": metadata.get("expected_failure_types")
                    or [],
                    "expected_persona_id": metadata.get("expected_persona_id"),
                    "set_a_is_persona": metadata.get("set_a_is_persona"),
                    "input_metadata": metadata,
                }
            )
    return records, output_issues


def _records_by_sample(
    records: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_sample[str(record.get("sample_id"))].append(record)
    return by_sample


def _scores_for_sample(
    by_sample: dict[str, list[dict[str, Any]]],
    *,
    sample_id: str,
    dimension: str,
) -> list[float]:
    scores = []
    for record in by_sample.get(sample_id, []):
        if record.get("dimension") != dimension:
            continue
        score = _score(record)
        if score is not None:
            scores.append(score)
    return scores


def _failure_tags(record: dict[str, Any]) -> set[str]:
    scores = record.get("scores") or {}
    tags = scores.get("failure_types") or []
    if isinstance(tags, str):
        tags = [tags]
    result = {str(item) for item in tags if item}
    dominant = scores.get("dominant_failure_type")
    if dominant:
        result.add(str(dominant))
    return result


def _b1_identity_match(record: dict[str, Any]) -> bool | None:
    if record.get("dimension") != "S4":
        return None
    expected = record.get("expected_persona_id")
    if not expected:
        return None
    observed = (record.get("scores") or {}).get("identified_persona")
    return str(observed or "").strip() == str(expected).strip()


def _should_check_b1_identity(record: dict[str, Any], *, threshold: float) -> bool:
    if record.get("dimension") != "S4":
        return False
    if str(record.get("expected_score_band") or "").lower() == "high":
        return True
    score = _score(record)
    return score is not None and score >= threshold


def compute_judge_qualification_stats(
    *,
    corpus: dict[str, Any],
    records: list[dict[str, Any]],
    missing_outputs: list[str] | None = None,
    gates: dict[str, float] | None = None,
) -> dict[str, Any]:
    threshold = float(corpus.get("pass_threshold") or 3)
    gate_thresholds = {**DEFAULT_GATES, **(gates or {})}
    valid_records = [record for record in records if _score(record) is not None]
    by_sample = _records_by_sample(valid_records)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in valid_records:
        grouped[
            (
                str(record.get("sample_id")),
                str(record.get("dimension")),
                str(record.get("prompt_variant_id", "baseline")),
            )
        ].append(record)

    stability_groups: list[dict[str, Any]] = []
    stability_deltas: list[float] = []
    flip_groups = 0
    for (sample_id, dimension, variant), group in sorted(grouped.items()):
        scores = [
            score for score in (_score(record) for record in group) if score is not None
        ]
        if len(scores) < 2:
            continue
        deltas = _pairwise_deltas(scores)
        pass_values = {_pass(score, threshold) for score in scores}
        flip = len(pass_values) > 1
        if flip:
            flip_groups += 1
        stability_deltas.extend(deltas)
        stability_groups.append(
            {
                "sample_id": sample_id,
                "dimension": dimension,
                "prompt_variant_id": variant,
                "runs": len(scores),
                "scores": scores,
                "mean_score": safe_mean(scores),
                "max_delta": round(max(deltas), 4),
                "mean_delta": safe_mean(deltas),
                "within_one": all(delta <= 1 for delta in deltas),
                "pass_fail_flip": flip,
            }
        )

    variant_groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for record in valid_records:
        variant_groups[(str(record.get("sample_id")), str(record.get("dimension")))][
            str(record.get("prompt_variant_id", "baseline"))
        ].append(record)

    prompt_rows: list[dict[str, Any]] = []
    prompt_deltas: list[float] = []
    prompt_flip_groups = 0
    for (sample_id, dimension), variants in sorted(variant_groups.items()):
        if len(variants) < 2:
            continue
        variant_means = {}
        for variant, rows in variants.items():
            scores = [
                score for score in (_score(row) for row in rows) if score is not None
            ]
            mean_score = safe_mean(scores)
            if mean_score is not None:
                variant_means[variant] = mean_score
        if len(variant_means) < 2:
            continue
        deltas = _pairwise_deltas(list(variant_means.values()))
        prompt_deltas.extend(deltas)
        flip = len({_pass(score, threshold) for score in variant_means.values()}) > 1
        if flip:
            prompt_flip_groups += 1
        prompt_rows.append(
            {
                "sample_id": sample_id,
                "dimension": dimension,
                "variant_means": dict(sorted(variant_means.items())),
                "max_variant_delta": round(max(deltas), 4),
                "mean_variant_delta": safe_mean(deltas),
                "within_one": all(delta <= 1 for delta in deltas),
                "pass_fail_flip": flip,
            }
        )

    sensitivity_rows: list[dict[str, Any]] = []
    for case in corpus.get("sensitivity_cases", []):
        dimension = str(case.get("dimension", ""))
        baseline_id = str(case.get("baseline_sample_id", ""))
        perturbed_id = str(case.get("perturbed_sample_id", ""))
        baseline_scores = _scores_for_sample(
            by_sample, sample_id=baseline_id, dimension=dimension
        )
        perturbed_scores = _scores_for_sample(
            by_sample, sample_id=perturbed_id, dimension=dimension
        )
        baseline_mean = safe_mean(baseline_scores)
        perturbed_mean = safe_mean(perturbed_scores)
        margin = (
            round(baseline_mean - perturbed_mean, 4)
            if baseline_mean is not None and perturbed_mean is not None
            else None
        )
        minimum_margin = float(case.get("minimum_margin", 0))
        expected_direction = str(case.get("expected_direction", "baseline_higher"))
        if margin is None:
            status = "missing"
        elif expected_direction == "perturbed_higher":
            status = "pass" if margin <= -minimum_margin else "fail"
        else:
            status = "pass" if margin >= minimum_margin else "fail"
        sensitivity_rows.append(
            {
                "case_id": case.get("case_id"),
                "factor": case.get("factor"),
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
    passed_sensitivity = [
        row for row in comparable_sensitivity if row["status"] == "pass"
    ]

    failure_rows: list[dict[str, Any]] = []
    for item in corpus.get("items", []):
        expected = set(item.get("expected_failure_types") or [])
        if not expected:
            continue
        sample_id = str(item.get("sample_id", ""))
        matches = []
        observed_all: set[str] = set()
        for record in by_sample.get(sample_id, []):
            observed = _failure_tags(record)
            observed_all |= observed
            matches.append(bool(expected & observed))
        hit_rate = safe_mean([1.0 if value else 0.0 for value in matches])
        failure_rows.append(
            {
                "sample_id": sample_id,
                "dimension": item.get("dimension"),
                "expected_failure_types": sorted(expected),
                "observed_failure_types": sorted(observed_all),
                "hit_rate": hit_rate,
                "status": (
                    "pass"
                    if matches and hit_rate >= gate_thresholds["failure_tag_hit_rate"]
                    else ("missing" if not matches else "fail")
                ),
            }
        )

    comparable_failure_rows = [
        row for row in failure_rows if row["status"] in {"pass", "fail"}
    ]
    passed_failure_rows = [
        row for row in comparable_failure_rows if row["status"] == "pass"
    ]

    stability_within_one = (
        round(
            sum(1 for delta in stability_deltas if delta <= 1) / len(stability_deltas),
            4,
        )
        if stability_deltas
        else None
    )
    pass_fail_flip_rate = (
        round(flip_groups / len(stability_groups), 4) if stability_groups else None
    )
    prompt_within_one = (
        round(sum(1 for delta in prompt_deltas if delta <= 1) / len(prompt_deltas), 4)
        if prompt_deltas
        else None
    )
    prompt_flip_rate = (
        round(prompt_flip_groups / len(prompt_rows), 4) if prompt_rows else None
    )
    sensitivity_pass_rate = (
        round(len(passed_sensitivity) / len(comparable_sensitivity), 4)
        if comparable_sensitivity
        else None
    )
    failure_tag_hit_rate = (
        round(len(passed_failure_rows) / len(comparable_failure_rows), 4)
        if comparable_failure_rows
        else None
    )

    b1_identity_rows: list[dict[str, Any]] = []
    b1_match_values: list[bool] = []
    b1_grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in valid_records:
        if _should_check_b1_identity(record, threshold=threshold):
            b1_grouped[
                (
                    str(record.get("sample_id")),
                    str(record.get("expected_persona_id") or ""),
                )
            ].append(record)
    for (sample_id, expected_persona_id), group in sorted(b1_grouped.items()):
        matches = [_b1_identity_match(record) for record in group]
        comparable_matches = [value for value in matches if value is not None]
        b1_match_values.extend(comparable_matches)
        observed = sorted(
            {
                str((record.get("scores") or {}).get("identified_persona") or "")
                for record in group
                if (record.get("scores") or {}).get("identified_persona")
            }
        )
        match_rate = safe_mean([1.0 if value else 0.0 for value in comparable_matches])
        b1_identity_rows.append(
            {
                "sample_id": sample_id,
                "expected_persona_id": expected_persona_id,
                "observed_persona_ids": observed,
                "match_rate": match_rate,
                "status": (
                    "pass"
                    if comparable_matches
                    and match_rate >= gate_thresholds["b1_identity_match_rate"]
                    else ("missing" if not comparable_matches else "fail")
                ),
            }
        )
    b1_identity_match_rate = safe_mean(
        [1.0 if value else 0.0 for value in b1_match_values]
    )

    # Absolute band enforcement: ensure the judge respects the corpus's
    # expected_score_band labels, not just the relative sensitivity margin.
    # high-band samples must mean >= threshold; low-band samples must mean
    # < threshold. Without this check, a judge that scored good=5 and bad=4
    # (both above threshold=3) passes the old sensitivity check but still
    # flags the bad sample as a "pass", which defeats the gate.
    band_rows: list[dict[str, Any]] = []
    band_violations: list[dict[str, Any]] = []
    for item in corpus.get("items", []):
        sample_id = str(item.get("sample_id", ""))
        band = str(item.get("expected_score_band", "")).lower()
        dimension = str(item.get("dimension", ""))
        scores = _scores_for_sample(by_sample, sample_id=sample_id, dimension=dimension)
        if not scores or band not in {"high", "low"}:
            continue
        mean_score = round(mean(scores), 4)
        if band == "high":
            ok = mean_score >= threshold
        else:  # low
            ok = mean_score < threshold
        row = {
            "sample_id": sample_id,
            "dimension": dimension,
            "expected_score_band": band,
            "mean_score": mean_score,
            "threshold": threshold,
            "status": "pass" if ok else "fail",
        }
        band_rows.append(row)
        if not ok:
            band_violations.append(row)
    band_all_ok = bool(band_rows) and not band_violations

    gate_checks = {
        "no_missing_outputs": not missing_outputs,
        "same_prompt_within_one_rate": (
            stability_within_one is not None
            and stability_within_one >= gate_thresholds["within_one_score_rate"]
        ),
        "pass_fail_flip_rate": (
            pass_fail_flip_rate is not None
            and pass_fail_flip_rate <= gate_thresholds["pass_fail_flip_rate_max"]
        ),
        "prompt_format_within_one_rate": (
            prompt_within_one is not None
            and prompt_within_one >= gate_thresholds["prompt_format_within_one_rate"]
        ),
        "sensitivity_pass_rate": (
            sensitivity_pass_rate is not None
            and sensitivity_pass_rate >= gate_thresholds["sensitivity_pass_rate"]
        ),
        "failure_tag_hit_rate": (
            failure_tag_hit_rate is not None
            and failure_tag_hit_rate >= gate_thresholds["failure_tag_hit_rate"]
        ),
        "b1_identity_match_rate": (
            b1_identity_match_rate is not None
            and b1_identity_match_rate >= gate_thresholds["b1_identity_match_rate"]
        ),
        "expected_band_respected": band_all_ok,
    }

    return {
        "version": "judge_qualification_stats_v3",
        "corpus_version": corpus.get("version"),
        "rubric_version": RUBRIC_VERSION,
        "contract_version": CONTRACT_VERSION,
        "pass_threshold": threshold,
        "gate_thresholds": gate_thresholds,
        "ok": all(gate_checks.values()),
        "gate_checks": gate_checks,
        "counts": {
            "corpus_items": len(corpus.get("items", [])),
            "records": len(records),
            "valid_records": len(valid_records),
            "missing_outputs": len(missing_outputs or []),
            "stability_groups": len(stability_groups),
            "prompt_variant_groups": len(prompt_rows),
            "sensitivity_cases": len(corpus.get("sensitivity_cases", [])),
            "comparable_sensitivity_cases": len(comparable_sensitivity),
            "failure_tag_cases": len(failure_rows),
            "b1_identity_cases": len(b1_identity_rows),
        },
        "missing_outputs_sample": (missing_outputs or [])[:25],
        "stability": {
            "mean_absolute_score_delta": safe_mean(stability_deltas),
            "within_one_score_rate": stability_within_one,
            "pass_fail_flip_rate": pass_fail_flip_rate,
            "groups": stability_groups,
        },
        "prompt_format": {
            "mean_absolute_variant_delta": safe_mean(prompt_deltas),
            "within_one_variant_rate": prompt_within_one,
            "pass_fail_variant_flip_rate": prompt_flip_rate,
            "groups": prompt_rows,
        },
        "sensitivity": {
            "pass_rate": sensitivity_pass_rate,
            "cases": sensitivity_rows,
            "failure_examples": [
                row for row in sensitivity_rows if row["status"] != "pass"
            ][:25],
        },
        "failure_tags": {
            "hit_rate": failure_tag_hit_rate,
            "cases": failure_rows,
            "failure_examples": [
                row for row in failure_rows if row["status"] != "pass"
            ][:25],
        },
        "b1_identity": {
            "match_rate": b1_identity_match_rate,
            "cases": b1_identity_rows,
            "failure_examples": [
                row for row in b1_identity_rows if row["status"] != "pass"
            ][:25],
        },
        "expected_band": {
            "ok": band_all_ok,
            "cases": band_rows,
            "violations": band_violations[:25],
        },
    }


def markdown_report(stats: dict[str, Any]) -> str:
    lines = [
        "# Issue83 Judge Qualification Report",
        "",
        f"- Corpus version: {stats.get('corpus_version')}",
        f"- Overall status: {'pass' if stats.get('ok') else 'fail'}",
        f"- Records: {stats['counts']['valid_records']}/{stats['counts']['records']}",
        f"- Same-prompt mean delta: {stats['stability']['mean_absolute_score_delta']}",
        f"- Same-prompt within-one rate: {stats['stability']['within_one_score_rate']}",
        f"- Pass/fail flip rate: {stats['stability']['pass_fail_flip_rate']}",
        f"- Prompt-format within-one rate: {stats['prompt_format']['within_one_variant_rate']}",
        f"- Sensitivity pass rate: {stats['sensitivity']['pass_rate']}",
        f"- Failure-tag hit rate: {stats['failure_tags']['hit_rate']}",
        f"- S4 identity match rate: {stats['b1_identity']['match_rate']}",
        "",
        "## Gate Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in stats["gate_checks"].items():
        lines.append(f"| `{key}` | {'pass' if value else 'fail'} |")
    lines.extend(
        [
            "",
            "## Sensitivity Cases",
            "",
            "| Case | Dimension | Baseline | Perturbed | Margin | Status |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in stats["sensitivity"]["cases"]:
        lines.append(
            f"| `{row['case_id']}` | {row['dimension']} | {row['baseline_mean']} | "
            f"{row['perturbed_mean']} | {row['score_margin']} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## Prompt Format",
            "",
            "| Sample | Dimension | Variant Means | Max Delta | Flip |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in stats["prompt_format"]["groups"]:
        means = json.dumps(row["variant_means"], sort_keys=True)
        lines.append(
            f"| `{row['sample_id']}` | {row['dimension']} | `{means}` | "
            f"{row['max_variant_delta']} | {row['pass_fail_flip']} |"
        )
    lines.extend(
        [
            "",
            "## Failure Tags",
            "",
            "| Sample | Expected | Observed | Hit Rate | Status |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in stats["failure_tags"]["cases"]:
        lines.append(
            f"| `{row['sample_id']}` | `{row['expected_failure_types']}` | "
            f"`{row['observed_failure_types']}` | {row['hit_rate']} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## S4 Identity",
            "",
            "| Sample | Expected | Observed | Match Rate | Status |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in stats["b1_identity"]["cases"]:
        lines.append(
            f"| `{row['sample_id']}` | `{row['expected_persona_id']}` | "
            f"`{row['observed_persona_ids']}` | {row['match_rate']} | "
            f"{row['status']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _html(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _status_text(value: bool | None) -> str:
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    return "missing"


def _status_class(value: bool | None) -> str:
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    return "warn"


def _metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return _html(value)


def html_report(stats: dict[str, Any]) -> str:
    checks = "\n".join(
        "<tr>"
        f"<td><code>{_html(key)}</code></td>"
        f'<td><span class="status {_status_class(value)}">'
        f"{_status_text(value)}</span></td>"
        "</tr>"
        for key, value in stats.get("gate_checks", {}).items()
    )
    sensitivity_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html(row.get('case_id'))}</code></td>"
        f"<td>{_html(row.get('dimension'))}</td>"
        f"<td>{_metric(row.get('baseline_mean'))}</td>"
        f"<td>{_metric(row.get('perturbed_mean'))}</td>"
        f"<td>{_metric(row.get('score_margin'))}</td>"
        f"<td><span class=\"status {_status_class(row.get('status') == 'pass')}\">"
        f"{_html(row.get('status'))}</span></td>"
        "</tr>"
        for row in stats.get("sensitivity", {}).get("cases", [])
    )
    prompt_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html(row.get('sample_id'))}</code></td>"
        f"<td>{_html(row.get('dimension'))}</td>"
        f"<td><code>{_html(json.dumps(row.get('variant_means', {}), sort_keys=True))}</code></td>"
        f"<td>{_metric(row.get('max_variant_delta'))}</td>"
        f"<td>{_html(row.get('pass_fail_flip'))}</td>"
        "</tr>"
        for row in stats.get("prompt_format", {}).get("groups", [])
    )
    failure_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html(row.get('sample_id'))}</code></td>"
        f"<td><code>{_html(row.get('expected_failure_types'))}</code></td>"
        f"<td><code>{_html(row.get('observed_failure_types'))}</code></td>"
        f"<td>{_metric(row.get('hit_rate'))}</td>"
        f"<td><span class=\"status {_status_class(row.get('status') == 'pass')}\">"
        f"{_html(row.get('status'))}</span></td>"
        "</tr>"
        for row in stats.get("failure_tags", {}).get("cases", [])
    )
    b1_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html(row.get('sample_id'))}</code></td>"
        f"<td><code>{_html(row.get('expected_persona_id'))}</code></td>"
        f"<td><code>{_html(row.get('observed_persona_ids'))}</code></td>"
        f"<td>{_metric(row.get('match_rate'))}</td>"
        f"<td><span class=\"status {_status_class(row.get('status') == 'pass')}\">"
        f"{_html(row.get('status'))}</span></td>"
        "</tr>"
        for row in stats.get("b1_identity", {}).get("cases", [])
    )
    residual_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html(row.get('sample_id'))}</code></td>"
        f"<td>{_html(row.get('dimension'))}</td>"
        f"<td>{_html(row.get('prompt_variant_id'))}</td>"
        f"<td>{_metric(row.get('max_delta'))}</td>"
        f"<td>{_html(row.get('pass_fail_flip'))}</td>"
        f"<td><code>{_html(row.get('scores'))}</code></td>"
        "</tr>"
        for row in stats.get("stability", {}).get("groups", [])
        if row.get("pass_fail_flip") or not row.get("within_one")
    )
    counts = stats.get("counts", {})
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Issue83 Judge Qualification Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1100px; margin: 36px auto; padding: 0 20px; color: #252525; line-height: 1.55; }}
  h1 {{ color: #16213e; border-bottom: 3px solid #16213e; padding-bottom: 8px; }}
  h2 {{ color: #16213e; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 9px 11px; text-align: left; vertical-align: top; }}
  th {{ background: #16213e; color: white; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  code {{ overflow-wrap: anywhere; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 14px; background: #fff; }}
  .val {{ font-size: 1.7em; font-weight: 700; color: #16213e; }}
  .lbl {{ color: #666; font-size: 0.9em; }}
  .status {{ display: inline-block; border-radius: 999px; padding: 2px 9px; font-size: 0.82em; font-weight: 700; text-transform: uppercase; }}
  .pass {{ background: #e8f6ef; color: #176f3d; }}
  .fail {{ background: #fdecea; color: #a93226; }}
  .warn {{ background: #fff4d6; color: #8a6500; }}
  .note {{ background: #f0f7ff; border-left: 4px solid #16213e; border-radius: 4px; padding: 13px 15px; margin: 15px 0; }}
</style></head><body>
<h1>Issue83 Judge Qualification Report</h1>
<p><span class="status {_status_class(bool(stats.get('ok')))}">{_status_text(bool(stats.get('ok')))}</span>
Corpus version: <code>{_html(stats.get('corpus_version'))}</code></p>
<div class="cards">
  <div class="card"><div class="val">{_html(counts.get('valid_records'))}/{_html(counts.get('records'))}</div><div class="lbl">Valid records</div></div>
  <div class="card"><div class="val">{_html(counts.get('missing_outputs'))}</div><div class="lbl">Missing outputs</div></div>
  <div class="card"><div class="val">{_metric(stats.get('stability', {}).get('within_one_score_rate'))}</div><div class="lbl">Same-prompt within-one</div></div>
  <div class="card"><div class="val">{_metric(stats.get('stability', {}).get('pass_fail_flip_rate'))}</div><div class="lbl">Pass/fail flip rate</div></div>
  <div class="card"><div class="val">{_metric(stats.get('prompt_format', {}).get('within_one_variant_rate'))}</div><div class="lbl">Prompt-format within-one</div></div>
  <div class="card"><div class="val">{_metric(stats.get('sensitivity', {}).get('pass_rate'))}</div><div class="lbl">Sensitivity pass rate</div></div>
</div>
<h2>Gate Checks</h2>
<table><tr><th>Check</th><th>Status</th></tr>{checks}</table>
<h2>Sensitivity Cases</h2>
<table><tr><th>Case</th><th>Dimension</th><th>Baseline</th><th>Perturbed</th><th>Margin</th><th>Status</th></tr>{sensitivity_rows}</table>
<h2>Prompt Format Robustness</h2>
<table><tr><th>Sample</th><th>Dimension</th><th>Variant means</th><th>Max delta</th><th>Flip</th></tr>{prompt_rows}</table>
<h2>Failure Tags</h2>
<table><tr><th>Sample</th><th>Expected</th><th>Observed</th><th>Hit rate</th><th>Status</th></tr>{failure_rows}</table>
<h2>S4 Identity</h2>
<table><tr><th>Sample</th><th>Expected</th><th>Observed</th><th>Match rate</th><th>Status</th></tr>{b1_rows}</table>
<h2>Residual Numeric Deltas</h2>
<div class="note">These rows do not necessarily fail the gate. They identify groups with score deltas above one point or pass/fail flips for follow-up numeric calibration.</div>
<table><tr><th>Sample</th><th>Dimension</th><th>Variant</th><th>Max delta</th><th>Flip</th><th>Scores</th></tr>{residual_rows}</table>
</body></html>"""


def write_judge_qualification_report(
    *,
    gate_dir: Path | None = None,
    results_dir: Path | None = None,
    corpus_path: Path | None = None,
) -> dict[str, Any]:
    gate_dir = _coerce_gate_dir(gate_dir=gate_dir, results_dir=results_dir)
    input_dir = gate_dir / "judge_inputs"
    output_dir = gate_dir / "judge_outputs"
    by_model_output_dir = gate_dir / "judge_outputs_by_model"
    report_dir = gate_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    corpus_snapshot = report_dir / "corpus_snapshot.json"
    resolved_corpus = corpus_path or (
        corpus_snapshot if corpus_snapshot.exists() else DEFAULT_CORPUS
    )
    corpus = load_corpus(resolved_corpus)
    records, missing = _load_joined_records(
        input_dir,
        output_dir,
        by_model_output_dir=by_model_output_dir,
    )
    stats = compute_judge_qualification_stats(
        corpus=corpus,
        records=records,
        missing_outputs=missing,
    )
    report_paths = {
        key: str(report_dir / filename) for key, filename in REPORT_FILENAMES.items()
    }
    stats["report_paths"] = report_paths
    atomic_write_json(report_dir / REPORT_FILENAMES["stats_json"], stats)
    with open(report_dir / REPORT_FILENAMES["markdown"], "w", encoding="utf-8") as fh:
        fh.write(markdown_report(stats))
    with open(report_dir / REPORT_FILENAMES["html"], "w", encoding="utf-8") as fh:
        fh.write(html_report(stats))
    return stats


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--corpus", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    stats = write_judge_qualification_report(
        gate_dir=resolve_results_dir(args.results_dir),
        corpus_path=args.corpus,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
