"""Failure-case candidate picker (Phase 3, post-hoc, no LLM calls).

Reads
- ``results/<run>/report/failure_taxonomy_stats.json``
- ``results/<run>/evaluations/all_evaluations.json``
- ``results/<run>/conversations/<source_file>.json`` (best-effort transcript)

Selection rules (in order):

1. For every ``failure_type`` whose total count across the dataset is
   ``>= MIN_COUNT_THRESHOLD``, include exactly one candidate.
2. Within each type, pick the record whose severity is closest to the
   median of all severities for that type. Tie-break alphabetically by
   ``eval_id`` so the choice is deterministic.
3. Ensure the final candidate set spans ``>= MIN_DISTINCT_MODELS``
   distinct student models and ``>= MIN_DISTINCT_DIMENSIONS`` distinct
   dimensions. If not, swap candidates within their type group for ones
   that introduce the missing axis.
4. Trim to at most ``MAX_CANDIDATES`` (kept by descending type frequency).

Run:

    python -m experiments.student_sim_stability.analysis.failure_case_picker

Output is written to ``report/failure_cases_candidates.json`` and is
intended to be reviewed by a human. The user then curates a subset and
saves it as ``report/failure_cases_curated.json``; the report renderer
prefers the curated file when present.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import default_results_dir
from experiments.student_sim_stability.core.rubrics import primary_score_field

logger = logging.getLogger(__name__)


MIN_COUNT_THRESHOLD = 5
MIN_DISTINCT_MODELS = 2
MIN_DISTINCT_DIMENSIONS = 2
MAX_CANDIDATES = 7
TRANSCRIPT_MAX_CHARS = 600


def _build_records_by_type(all_evaluations: dict) -> dict[str, list[dict]]:
    """Scan the aggregate evaluation file and group every (record × failure_type)
    pair into ``{failure_type: [record_summary, ...]}``.

    Each summary includes the fields needed for downstream selection plus
    the ``judge_evidence`` text. Severity is computed as ``5 - primary_score``
    so higher = worse, matching ``analysis.report._aggregate_failure_taxonomy``.
    """
    records_by_type: dict[str, list[dict]] = defaultdict(list)
    for dimension, records in all_evaluations.items():
        if not isinstance(records, list):
            continue
        try:
            score_field: str | None = primary_score_field(dimension)
        except KeyError:
            score_field = None
        for record in records:
            scores = record.get("scores") or {}
            metadata = record.get("metadata") or {}
            score = scores.get(score_field) if score_field else None
            severity = float(5 - score) if isinstance(score, (int, float)) else None
            failure_types = scores.get("failure_types") or []
            if isinstance(failure_types, str):
                failure_types = [failure_types]
            for ft in failure_types:
                if not ft:
                    continue
                records_by_type[ft].append(
                    {
                        "eval_id": record.get("eval_id"),
                        "dimension": dimension,
                        "model": (metadata.get("model") or "").split("/")[-1],
                        "persona_id": metadata.get("persona_id"),
                        "task_id": metadata.get("task_id"),
                        "failure_type": ft,
                        "severity": severity,
                        "judge_evidence": scores.get("failure_evidence") or "",
                        "metadata": metadata,
                    }
                )
    return records_by_type


def _pick_median_for_type(records: list[dict]) -> dict | None:
    """Pick the record whose severity is closest to the median.

    Records with ``severity is None`` are excluded. Among remaining records
    sort by ``(|severity - median|, eval_id)`` and take the first. The
    explicit alphabetical tie-break makes the choice reproducible.
    """
    valid = [r for r in records if r.get("severity") is not None]
    if not valid:
        return None
    severities = sorted(r["severity"] for r in valid)
    median = statistics.median(severities)
    valid.sort(key=lambda r: (abs(r["severity"] - median), r.get("eval_id") or ""))
    chosen = dict(valid[0])  # shallow copy so we can mutate selection_reason
    chosen["selection_reason"] = (
        f"median severity {chosen['severity']:.2f} for failure_type "
        f"'{chosen['failure_type']}' (n={len(valid)}); "
        f"dimension {chosen['dimension']}; model {chosen['model']}"
    )
    return chosen


def _ensure_diversity(
    candidates: list[dict],
    records_by_type: dict[str, list[dict]],
    min_models: int,
    min_dimensions: int,
) -> list[dict]:
    """If the candidate set does not span enough models / dimensions, swap
    one candidate's selection within its type group for an alternate that
    introduces the missing axis. Walks through types in order; stops once
    both diversity floors are satisfied (or no swap improves coverage).
    """
    by_index = list(candidates)
    for _ in range(min_models + min_dimensions + 1):
        models = {c["model"] for c in by_index if c.get("model")}
        dims = {c["dimension"] for c in by_index if c.get("dimension")}
        if len(models) >= min_models and len(dims) >= min_dimensions:
            return by_index
        # Find a swap that improves whichever axis is short
        for idx, current in enumerate(by_index):
            ft = current.get("failure_type")
            if not ft:
                continue
            alternates = [
                r
                for r in records_by_type.get(ft, [])
                if r.get("severity") is not None
                and r.get("eval_id") != current.get("eval_id")
            ]
            if not alternates:
                continue
            severities = sorted(r["severity"] for r in alternates)
            median = statistics.median(severities) if severities else None
            alternates.sort(
                key=lambda r: (
                    abs((r["severity"] or 0) - (median or 0)),
                    r.get("eval_id") or "",
                )
            )
            for alt in alternates:
                trial = list(by_index)
                trial[idx] = dict(alt)
                trial[idx]["selection_reason"] = (
                    f"alt-pick for failure_type '{ft}' to add "
                    f"{'model' if len({c['model'] for c in trial}) > len(models) else 'dimension'} "
                    f"diversity; severity {trial[idx]['severity']:.2f}; "
                    f"dimension {trial[idx]['dimension']}; model {trial[idx]['model']}"
                )
                trial_models = {c["model"] for c in trial if c.get("model")}
                trial_dims = {c["dimension"] for c in trial if c.get("dimension")}
                if (
                    len(trial_models) >= len(models)
                    and len(trial_dims) >= len(dims)
                    and (len(trial_models) > len(models) or len(trial_dims) > len(dims))
                ):
                    by_index = trial
                    break
            else:
                continue
            break
        else:
            break
    return by_index


def _load_transcript_excerpt(
    results_dir: Path, record: dict, max_chars: int = TRANSCRIPT_MAX_CHARS
) -> str:
    """Best-effort load of the conversation file referenced by this record.

    Tries, in order:
      1. ``metadata.source_file`` (the canonical pointer for S1/S3/S2/S4/S5)
      2. ``metadata.persona_source_file`` (canonical for control records)
      3. eval_id with leading ``<DIM>__`` prefix stripped, ``.json`` suffix
      4. eval_id verbatim
    Returns empty string on any miss / parse error.
    """
    metadata = record.get("metadata") or {}
    eval_id = record.get("eval_id") or ""
    candidates: list[Path] = []
    for key in ("source_file", "persona_source_file"):
        value = metadata.get(key)
        if value:
            candidates.append(results_dir / "conversations" / value)
    parts = eval_id.split("__", 1)
    if len(parts) == 2:
        candidates.append(results_dir / "conversations" / f"{parts[1]}.json")
    candidates.append(results_dir / "conversations" / f"{eval_id}.json")

    for path in candidates:
        if not path.exists():
            continue
        try:
            conv = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("failed to load %s: %s", path, exc)
            continue
        if not isinstance(conv, list):
            continue
        turns: list[str] = []
        for msg in conv:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "?"
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            turns.append(f"[{role}] {content}")
            if len(turns) >= 4:
                break
        text = "\n".join(turns)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text
    return ""


def select_candidates(
    taxonomy_stats: dict,
    all_evaluations: dict,
    results_dir: Path,
    *,
    min_count_threshold: int = MIN_COUNT_THRESHOLD,
    min_models: int = MIN_DISTINCT_MODELS,
    min_dimensions: int = MIN_DISTINCT_DIMENSIONS,
    max_candidates: int = MAX_CANDIDATES,
) -> list[dict]:
    """Apply the selection rules and return the candidate list."""
    records_by_type = _build_records_by_type(all_evaluations)

    # Cross-check: the taxonomy_stats counts should match what we built
    # locally. Use the local counts (records_by_type) as authoritative.
    qualifying = [
        (ft, len(recs))
        for ft, recs in records_by_type.items()
        if len(recs) >= min_count_threshold
    ]
    qualifying.sort(key=lambda x: (-x[1], x[0]))

    candidates: list[dict] = []
    for ft, _count in qualifying:
        chosen = _pick_median_for_type(records_by_type[ft])
        if chosen is not None:
            candidates.append(chosen)

    # Trim to max_candidates, keeping the most-frequent failure_types
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]

    # Ensure model / dimension diversity by swapping within type groups
    candidates = _ensure_diversity(
        candidates, records_by_type, min_models, min_dimensions
    )

    # Populate transcript_excerpt and drop the bulky raw metadata blob
    for entry in candidates:
        entry["transcript_excerpt"] = _load_transcript_excerpt(results_dir, entry)
        entry.pop("metadata", None)

    return candidates


def write_candidates(
    candidates: list[dict],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(candidates, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="failure_case_picker",
        description="Select representative failure cases for the appendix.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=default_results_dir(),
        help="Run results directory (default: results/main).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: <results-dir>/report/failure_cases_candidates.json).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    results_dir: Path = args.results_dir
    if not results_dir.is_absolute():
        results_dir = results_dir.resolve()
    out_path: Path = args.out or (
        results_dir / "report" / "failure_cases_candidates.json"
    )

    taxonomy_path = results_dir / "report" / "failure_taxonomy_stats.json"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"
    if not eval_path.exists():
        print(f"missing: {eval_path}", file=sys.stderr)
        return 2
    if not taxonomy_path.exists():
        print(
            f"missing: {taxonomy_path} — run `cli report` first to generate it",
            file=sys.stderr,
        )
        return 2

    with open(taxonomy_path, encoding="utf-8") as fh:
        taxonomy = json.load(fh)
    with open(eval_path, encoding="utf-8") as fh:
        all_evaluations = json.load(fh)

    candidates = select_candidates(taxonomy, all_evaluations, results_dir)
    write_candidates(candidates, out_path)

    n_models = len({c["model"] for c in candidates if c.get("model")})
    n_dims = len({c["dimension"] for c in candidates if c.get("dimension")})
    print(
        f"wrote {len(candidates)} candidates to {out_path} "
        f"(models={n_models}, dimensions={n_dims})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
