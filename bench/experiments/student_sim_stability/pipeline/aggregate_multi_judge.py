"""Multi-judge aggregator — produces per-eval four-view breakdown.

For every judge eval, reads all three judges' scores from
``judge_outputs_by_model/{model}/{eval_id}.json`` and emits four views:

1. ``sonnet``    — Claude Sonnet (primary)
2. ``gpt54``     — GPT-5.4
3. ``gemini``    — Gemini 3.1 Pro
4. ``panel_3``   — mean of all three

Produces ``evaluations/multi_judge_aggregates.json`` with per-eval rows keyed
by dimension. The main ``all_evaluations.json`` (primary-only) stays untouched
so existing reporters/tests keep working.

Designed to be idempotent and re-runnable: reads only persistent on-disk
judge outputs, no LLM calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from experiments.student_sim_stability.core.io_utils import load_json, safe_model_dir
from experiments.student_sim_stability.core.numerics import safe_mean, safe_std
from experiments.student_sim_stability.core.paths import BENCH_ROOT, default_results_dir

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.rubrics import (  # noqa: E402
    DIMENSION_TO_FILE,
    numeric_score_fields,
)

logger = logging.getLogger(__name__)

# Judge model identifiers we expect under judge_outputs_by_model/
SONNET_KEY = "anthropic/claude-sonnet-4-6"
GPT54_KEY = "openai/gpt-5.4"
GEMINI_KEY = "google/gemini-3.1-pro-preview"


# The on-disk directories use `safe_model_dir` encoding from core/io_utils.
SONNET_DIR = safe_model_dir(SONNET_KEY)  # anthropic__claude-sonnet-4-6
GPT54_DIR = safe_model_dir(GPT54_KEY)  # openai__gpt-5_4
GEMINI_DIR = safe_model_dir(GEMINI_KEY)  # google__gemini-3_1-pro-preview

VIEW_ORDER = ["sonnet", "gpt54", "gemini", "panel_3"]


def _score_fields_for(dimension: str) -> list[str]:
    try:
        return list(numeric_score_fields(dimension))
    except KeyError:
        return []


def _by_eval_across_judges(
    judge_output_root: Path,
    dimension: str,
) -> dict[str, dict[str, dict]]:
    """Return ``{eval_id: {judge_dir: output_payload}}`` for a dimension."""
    by_eval: dict[str, dict[str, dict]] = defaultdict(dict)
    for judge_dir in [SONNET_DIR, GPT54_DIR, GEMINI_DIR]:
        ddir = judge_output_root / judge_dir
        if not ddir.exists():
            continue
        for path in ddir.glob(f"{dimension}__*.json"):
            try:
                payload = load_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skip malformed %s: %s", path, exc)
                continue
            if not payload:
                continue
            eval_id = payload.get("eval_id") or path.stem
            by_eval[eval_id][judge_dir] = payload
    return by_eval


def _aggregate_eval(
    eval_id: str,
    judge_payloads: dict[str, dict],
    input_metadata: dict,
    numeric_fields: list[str],
) -> dict[str, Any]:
    """Compute the five views for a single eval across its numeric score fields.

    ``input_metadata`` is the ``metadata`` block from the judge *input* file
    (``judge_inputs/<eval_id>.json``). It carries task_id / persona_id / model
    / repeat_tag / facet etc. that the judge outputs do not persist, so the
    multi-judge aggregate becomes the single source of truth for grouping —
    no downstream consumer needs to parse eval_id filenames.
    """
    scores_by_judge = {}
    for view_key, judge_dir in (
        ("sonnet", SONNET_DIR),
        ("gpt54", GPT54_DIR),
        ("gemini", GEMINI_DIR),
    ):
        payload = judge_payloads.get(judge_dir) or {}
        scores = payload.get("scores") or {}
        row = {}
        for f in numeric_fields:
            v = scores.get(f)
            row[f] = float(v) if isinstance(v, (int, float)) else None
        scores_by_judge[view_key] = row

    aggregates = {"panel_3": {}}
    for f in numeric_fields:
        v_sonnet = scores_by_judge["sonnet"].get(f)
        v_gpt54 = scores_by_judge["gpt54"].get(f)
        v_gemini = scores_by_judge["gemini"].get(f)

        panel_3_vals = [v for v in (v_sonnet, v_gpt54, v_gemini) if v is not None]

        aggregates["panel_3"][f] = safe_mean(panel_3_vals)
        aggregates["panel_3"][f"{f}__std"] = safe_std(panel_3_vals)

    # Use any judge's metadata (they should all have the same metadata since
    # judge inputs were identical). Prefer Sonnet, fall back to others.
    meta_source = (
        judge_payloads.get(SONNET_DIR)
        or judge_payloads.get(GPT54_DIR)
        or judge_payloads.get(GEMINI_DIR)
        or {}
    )
    model_raw = input_metadata.get("model") or ""
    model_short = model_raw.split("/")[-1] if "/" in model_raw else model_raw

    return {
        "eval_id": eval_id,
        "dimension": meta_source.get("dimension"),
        "metadata": {
            "persona_id": input_metadata.get("persona_id")
            or meta_source.get("persona_contract_id")
            or meta_source.get("persona_id"),
            "task_id": input_metadata.get("task_id"),
            "model": model_short or None,
            "repeat_tag": input_metadata.get("repeat_tag"),
            "facet": input_metadata.get("facet"),
            "persona_contract_version": meta_source.get("persona_contract_version"),
            "rubric_id": meta_source.get("rubric_id"),
            "rubric_version": meta_source.get("rubric_version"),
            "source_file": meta_source.get("source_file"),
        },
        "scores_by_judge": scores_by_judge,
        "aggregates": aggregates,
        "n_judges_scored": sum(
            1
            for view in ("sonnet", "gpt54", "gemini")
            if any(scores_by_judge[view].get(f) is not None for f in numeric_fields)
        ),
    }


def aggregate_multi_judge(
    results_dir: Path,
    *,
    output_path: Path | None = None,
    strict: bool = False,
) -> dict:
    """Produce ``evaluations/multi_judge_aggregates.json``.

    Reads ``judge_outputs_by_model/<judge>/*.json`` for all 3 judges and every
    rubric dimension. Output schema:

    .. code-block:: json

        {
          "version": "multi_judge_v3",
          "views": ["sonnet", "gpt54", "gemini", "panel_3"],
          "judge_model_map": {"sonnet": "anthropic/claude-sonnet-4-6", ...},
          "dimensions": {"S1": {"per_eval": [...], "n": 252, ...}, ...}
        }
    """
    out = output_path or results_dir / "evaluations" / "multi_judge_aggregates.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    judge_root = results_dir / "judge_outputs_by_model"
    if not judge_root.exists():
        raise FileNotFoundError(f"{judge_root} missing; run the judge panel first.")
    input_root = results_dir / "judge_inputs"
    if not input_root.exists():
        raise FileNotFoundError(
            f"{input_root} missing; cannot resolve task_id/model/repeat_tag "
            "for multi-judge rows without the original judge inputs."
        )

    # Pre-load every input metadata block once so the per-eval loop is a dict
    # lookup instead of an exists()+load_json round trip per row.
    metadata_by_eval: dict[str, dict[str, Any]] = {}
    for path in sorted(input_root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("skip malformed input %s: %s", path, exc)
            continue
        metadata_by_eval[path.stem] = (payload or {}).get("metadata") or {}

    dimensions_out: dict[str, Any] = {}
    total_rows = 0
    warnings: list[str] = []
    for dim in DIMENSION_TO_FILE:
        numeric_fields = _score_fields_for(dim)
        if not numeric_fields:
            continue
        by_eval = _by_eval_across_judges(judge_root, dim)
        rows: list[dict] = []
        missing_judge_counts: dict[str, int] = defaultdict(int)
        for eval_id in sorted(by_eval):
            payloads = by_eval[eval_id]
            for jd in (SONNET_DIR, GPT54_DIR, GEMINI_DIR):
                if jd not in payloads:
                    missing_judge_counts[jd] += 1
            if eval_id in metadata_by_eval:
                input_metadata = metadata_by_eval[eval_id]
            else:
                input_metadata = {}
                msg = f"{dim}: input file missing for {eval_id} (metadata unavailable)"
                warnings.append(msg)
                logger.warning(msg)
            row = _aggregate_eval(eval_id, payloads, input_metadata, numeric_fields)
            rows.append(row)
        dimensions_out[dim] = {
            "n": len(rows),
            "score_fields": numeric_fields,
            "per_eval": rows,
            "missing_judge_counts": dict(missing_judge_counts),
        }
        total_rows += len(rows)
        for jd, c in missing_judge_counts.items():
            if c > 0:
                msg = f"{dim}: judge {jd} missing {c}/{len(rows)} evals"
                warnings.append(msg)
                logger.warning(msg)

    if strict and warnings:
        raise RuntimeError(
            "multi-judge aggregation had missing outputs: " + "; ".join(warnings)
        )

    summary = {
        "version": "multi_judge_v3",
        "views": VIEW_ORDER,
        "judge_model_map": {
            "sonnet": SONNET_KEY,
            "gpt54": GPT54_KEY,
            "gemini": GEMINI_KEY,
        },
        "dimensions": dimensions_out,
        "totals": {
            "rows": total_rows,
            "warnings": warnings,
        },
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    logger.info(
        "wrote multi-judge aggregate to %s (%d rows across %d dimensions)",
        out,
        total_rows,
        len(dimensions_out),
    )
    return {
        "version": summary["version"],
        "output_path": str(out),
        "rows": total_rows,
        "dimensions": sorted(dimensions_out),
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    results_dir = Path(args.results_dir) if args.results_dir else default_results_dir()
    if not results_dir.is_absolute():
        results_dir = BENCH_ROOT / results_dir
    output = Path(args.output) if args.output else None
    summary = aggregate_multi_judge(results_dir, output_path=output, strict=args.strict)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
