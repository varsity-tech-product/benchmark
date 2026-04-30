"""Statistical report generator for student simulator stability experiment.

Generates an HTML report with embedded matplotlib charts (base64 PNG). Charts
and inline tables are owned by Components under ``analysis/components/``;
sections compose Component HTML rather than concatenating raw markup.
"""

import functools
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import matplotlib

matplotlib.use("Agg")
import numpy as np
from experiments.student_sim_stability.analysis.components import Component
from experiments.student_sim_stability.analysis.components.base import (
    _html,
    _score_class,
)
from experiments.student_sim_stability.analysis.components.charts.b1_identification import (
    B1Identification,
)
from experiments.student_sim_stability.analysis.components.charts.control_bars import (
    ControlBars,
)
from experiments.student_sim_stability.analysis.components.charts.d1_heatmap import (
    D1Heatmap,
)
from experiments.student_sim_stability.analysis.components.charts.d2_bars import D2Bars
from experiments.student_sim_stability.analysis.components.charts.d3_curves import (
    D3Curves,
)
from experiments.student_sim_stability.analysis.components.charts.overview_radar import (
    OverviewRadar,
)
from experiments.student_sim_stability.analysis.components.sections.data_quality_audit_section import (
    DataQualityAuditSection,
)
from experiments.student_sim_stability.analysis.components.sections.failure_cases_section import (
    FailureCasesSection,
)
from experiments.student_sim_stability.analysis.components.sections.judge_configuration_section import (
    JudgeConfigurationSection,
)
from experiments.student_sim_stability.analysis.components.sections.judge_qualification_section import (
    JudgeQualificationSection,
)
from experiments.student_sim_stability.analysis.components.tables.d1_by_model import (
    D1ByModel,
)
from experiments.student_sim_stability.analysis.components.tables.d1_by_model_persona import (
    D1ByModelPersona,
)
from experiments.student_sim_stability.analysis.components.tables.d1_by_persona import (
    D1ByPersona,
)
from experiments.student_sim_stability.analysis.components.tables.d1_by_task import (
    D1ByTask,
)
from experiments.student_sim_stability.analysis.components.tables.d2_by_model import (
    D2ByModel,
)
from experiments.student_sim_stability.analysis.components.tables.d2_by_model_temp import (
    D2ByModelTemp,
)
from experiments.student_sim_stability.analysis.components.tables.d3_drift import (
    D3Drift,
)
from experiments.student_sim_stability.analysis.components.tables.data_quality_audit import (
    DataQualityAudit,
)
from experiments.student_sim_stability.analysis.components.tables.failure_by_dimension import (
    FailureByDimension,
)
from experiments.student_sim_stability.analysis.components.tables.failure_inline import (
    FailureInline,
)
from experiments.student_sim_stability.analysis.components.tables.failure_taxonomy import (
    FailureTaxonomy,
)
from experiments.student_sim_stability.analysis.components.tables.human_alignment_b1_breakdown import (
    HumanAlignmentB1Breakdown,
)
from experiments.student_sim_stability.analysis.components.tables.human_alignment_b1_per_judge import (
    HumanAlignmentB1PerJudge,
)
from experiments.student_sim_stability.analysis.components.tables.human_alignment_disagreements import (
    HumanAlignmentDisagreements,
)
from experiments.student_sim_stability.analysis.components.tables.human_alignment_metrics import (
    HumanAlignmentMetrics,
)
from experiments.student_sim_stability.analysis.components.tables.judge_configuration import (
    JudgeConfiguration,
)
from experiments.student_sim_stability.analysis.components.tables.judge_qualification import (
    JudgeQualification,
)
from experiments.student_sim_stability.analysis.components.tables.multi_judge_view import (
    MultiJudgeView,
)
from experiments.student_sim_stability.analysis.components.tables.ranking_table import (
    RankingTable,
)
from experiments.student_sim_stability.core.io_utils import load_json
from experiments.student_sim_stability.core.numerics import (
    bootstrap_mean_ci,
    safe_mean,
    safe_std,
)
from experiments.student_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
    all_rubrics,
    primary_score_field,
)
from experiments.student_sim_stability.judge_qualification.render import (
    DEFAULT_GATE_RESULTS_DIR,
)

logger = logging.getLogger(__name__)


def _cell(values: list[float]) -> dict:
    """Standard per-cell aggregate: mean, std, n, ci_low, ci_high.

    Empty inputs collapse to all-zero so callers do not need to special-case.
    Bootstrap is seeded so reruns produce identical CIs for the same input.
    """
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "n": 0, "ci_low": 0.0, "ci_high": 0.0}
    ci = bootstrap_mean_ci(values)
    return {
        "mean": safe_mean(values),
        "std": safe_std(values),
        "n": n,
        "ci_low": ci["ci_low"],
        "ci_high": ci["ci_high"],
    }


_FAILURE_RECOMMENDATIONS = {
    "knowledge_leak": "Tighten known/unknown concept boundaries in the persona contract and add targeted boundary probes.",
    "under_competence": "Clarify minimum expected baseline knowledge and add examples of acceptable partial understanding.",
    "emotional_mismatch": "Strengthen emotional profile examples and add scripted pressure cases.",
    "generic_student_behavior": "Add more persona-specific question style and confusion style examples.",
    "co_teacher_drift": "Add explicit anti-teaching behavioral rules and S2 examples.",
    "task_forgetting": "Add task-retention reminders and redirect behavior examples.",
    "persona_contract_contradiction": "Resolve inconsistent contract clauses or add explicit precedence rules.",
}

_FAILURE_RECOMMENDATION_DEFAULT = (
    "Review the matching persona contract and judge evidence."
)


@dataclass(frozen=True)
class _AggregateRecord:
    """One record yielded by :meth:`ReportGenerator._iter_records`.

    Bundles the raw ``scores``/``metadata`` dicts together with the four
    metadata fields every aggregator looks up the same way (model short form,
    persona, task, eval_id) so the per-aggregator loops stay focused on the
    field-specific extraction and grouping.
    """

    scores: dict
    metadata: dict
    model: str
    persona_id: str
    task_id: str
    eval_id: str


class ReportGenerator:
    """Generate statistical report from evaluation results."""

    def __init__(self, eval_path: str, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir = self.output_dir.parent
        self.raw = load_json(Path(eval_path))
        self.rubrics = {rubric["dimension"]: rubric for rubric in all_rubrics()}
        # Multi-judge aggregates (optional). If present, each per-dim chart
        # and table will include 4 views: sonnet / gpt54 / gemini /
        # panel_3 mean. If absent, the report falls back to primary-only.
        multi_path = self.results_dir / "evaluations" / "multi_judge_aggregates.json"
        self.multi: dict | None = None
        if multi_path.exists():
            try:
                self.multi = load_json(multi_path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("failed to load multi-judge aggregates: %s", exc)
                self.multi = None

    def generate(self) -> str:
        """Generate full report. Returns path to output HTML.

        Report layout — headline first, drill-down later:
          1. Headline conclusion (TL;DR) + judge qualification gate status
          2. Cross-vendor candidate ranking (S1+S3+S2 composite) +
             judge-invariance check across Sonnet/GPT-5.4/Gemini/panel-3
          3. Stability per dimension: S1 / S3 / S2
          4. Temperature ablation (S3 × tutor temperature)
          5. Multi-judge 5-view comparison table
          6. Validity diagnostics: control + S5 + S4 on live data
          7. Human alignment (post-hoc)
          8. Failure taxonomy
          9. Appendix: judge configuration + data quality audit
        """
        stats = self._compute_all_stats()
        # Loaded once and reused across sections + Component instantiation.
        self._human = self._load_json_file(
            self.results_dir / "human_alignment" / "agreement_report.json"
        )
        self._agreement = self._load_json_file(
            self.results_dir / "report" / "judge_agreement.json"
        )
        self._metadata = self._load_json_file(
            self.results_dir / "report" / "stability_metadata.json"
        )
        self._audit = self._load_json_file(
            self.results_dir / "report" / "data_quality_audit.json"
        )
        self._failure_cases, self._failure_cases_source = self._load_failure_cases()
        self._components: dict[str, Component] = self._build_components(stats)

        sections = [
            self._header(),
            self._section_scope_header(stats),
            self._section_premise_check(stats),
            self._section_d1(stats),
            self._section_d2(stats),
            self._section_d3(stats),
            self._section_multi_judge_view(stats),
            self._section_human_alignment(),
            self._section_model_selection(stats),
            self._section_conclusion(stats),
            self._section_methodology_appendix_header(),
            self._section_judge_qualification(stats),
            self._section_judge_configuration(),
            self._section_data_quality_audit(),
            self._section_appendix_failure_cases(stats),
            self._footer(),
        ]
        html = "\n".join(sections)
        path = self.output_dir / "stability_report.html"
        with open(path, "w") as f:
            f.write(html)

        stats_path = self.output_dir / "stability_stats.json"
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        with open(self.output_dir / "failure_taxonomy_stats.json", "w") as f:
            json.dump(stats["failure_taxonomy"], f, indent=2, ensure_ascii=False)

        components_dir = self.output_dir / "components"
        for component in self._components.values():
            component.dump_to(components_dir)

        logger.info("Report saved to %s", path)
        return str(path)

    def _build_components(self, stats: dict) -> dict[str, Component]:
        """Instantiate every Component used in the report.

        The same instances are consumed by ``_section_*`` methods and dumped
        under ``results/<run>/report/components/``. Building them once keeps
        section composition and disk export in lock-step.
        """
        d1 = stats["d1"]
        d2 = stats["d2"]
        d3 = stats["d3"]
        human_metrics = self._human.get("agreement_metrics") or {}
        by_dimension_agreement = (self._agreement.get("agreement_metrics") or {}).get(
            "by_dimension"
        ) or {}
        return {
            "overview_radar": OverviewRadar(stats["model_ranking"]),
            "d1_heatmap": D1Heatmap(d1, self.multi),
            "d2_bars": D2Bars(d2["by_model_temp"]),
            "d3_curves": D3Curves(d3.get("avg_fidelity_curves", {})),
            "control_bars": ControlBars(
                stats["control"]["by_persona"],
                by_persona_judge=stats["control"].get("by_persona_judge"),
                high_score_ratio=stats["control"].get("high_score_ratio"),
                high_score_threshold=stats["control"].get("high_score_threshold"),
                standardized_effect_vs_baseline=stats["control"].get(
                    "standardized_effect_vs_baseline"
                ),
                baseline_unrecognizable=stats["control"].get("baseline_unrecognizable"),
                n=stats["control"].get("n"),
            ),
            "b1_identification": B1Identification(stats["b1"]),
            "ranking_table": RankingTable(stats["model_ranking"]),
            "d1_by_persona": D1ByPersona(d1["by_persona"]),
            "d1_by_model": D1ByModel(d1["by_model"]),
            "d1_by_task": D1ByTask(d1["by_task"]),
            "d1_by_model_persona": D1ByModelPersona(d1["by_model_persona"]),
            "d2_by_model": D2ByModel(d2["by_model"]),
            "d2_by_model_temp": D2ByModelTemp(d2["by_model_temp"]),
            "d3_drift": D3Drift(d3["by_model"]),
            "human_alignment_metrics": HumanAlignmentMetrics(human_metrics),
            "human_alignment_b1_per_judge": HumanAlignmentB1PerJudge(
                human_metrics.get("b1_identification")
            ),
            "human_alignment_b1_breakdown": HumanAlignmentB1Breakdown(
                self._human.get("b1_breakdown_by_persona")
            ),
            "human_alignment_disagreements": HumanAlignmentDisagreements(
                self._human.get("disagreement_examples")
            ),
            "failure_taxonomy": FailureTaxonomy(stats["failure_taxonomy"]),
            "failure_by_dimension": FailureByDimension(stats["failure_taxonomy"]),
            "failure_inline_d1": FailureInline(stats["failure_taxonomy"], "S1"),
            "failure_inline_d2": FailureInline(stats["failure_taxonomy"], "S3"),
            "failure_inline_d3": FailureInline(stats["failure_taxonomy"], "S2"),
            "judge_qualification": JudgeQualification(stats["judge_qualification"]),
            "judge_configuration": JudgeConfiguration(by_dimension_agreement),
            "data_quality_audit": DataQualityAudit(self._audit),
            "multi_judge_view": MultiJudgeView(self.multi),
            "judge_qualification_section": JudgeQualificationSection(
                stats["judge_qualification"], self.output_dir
            ),
            "judge_configuration_section": JudgeConfigurationSection(
                self._metadata, self._agreement
            ),
            "data_quality_audit_section": DataQualityAuditSection(self._audit),
            "failure_cases_section": FailureCasesSection(
                stats["failure_taxonomy"],
                self._failure_cases,
                self._failure_cases_source,
            ),
        }

    @staticmethod
    def _try_load_list(path: Path) -> list[dict] | None:
        """Read a JSON list from ``path``; return None on missing / malformed.

        Logs a warning when the file exists but cannot be parsed. Used by
        ``_load_failure_cases`` to fall through curated → candidates → empty.
        """
        if not path.exists():
            return None
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to read %s: %s", path, exc)
            return None
        return data if isinstance(data, list) else None

    def _load_failure_cases(self) -> tuple[list[dict], str]:
        """Read curated failure-case JSON if present, else picker candidates.

        Returns ``(cases, source)`` where ``source`` is one of
        ``"curated"`` / ``"candidates"`` / ``"missing"``. Phase 3 produces
        ``failure_cases_candidates.json`` via the standalone picker; the
        user is expected to write ``failure_cases_curated.json`` with the
        4-5 cases they want to keep.
        """
        curated = self._try_load_list(
            self.results_dir / "report" / "failure_cases_curated.json"
        )
        if curated is not None:
            return curated, "curated"
        candidates = self._try_load_list(
            self.results_dir / "report" / "failure_cases_candidates.json"
        )
        if candidates is not None:
            return candidates, "candidates"
        return [], "missing"

    # ----- Stats computation -----

    def _compute_all_stats(self) -> dict:
        d1 = self._aggregate_d1()
        d2 = self._aggregate_d2()
        d3 = self._aggregate_d3()
        stats = {
            "d1": d1,
            "d2": d2,
            "d3": d3,
            "control": self._aggregate_control(),
            "p1": self._aggregate_p1(),
            "b1": self._aggregate_b1(),
            "model_ranking": self._compute_model_ranking(d1, d2, d3),
            "failure_taxonomy": self._aggregate_failure_taxonomy(),
            "judge_qualification": self._load_judge_qualification_stats(),
        }
        return stats

    def _load_judge_qualification_stats(self) -> dict:
        reference_path = (
            self.results_dir / "report" / "judge_qualification_reference.json"
        )
        reference = self._load_json_file(reference_path)
        candidates: list[Path] = []
        if reference.get("stats_path"):
            candidates.append(Path(reference["stats_path"]))
        if reference.get("gate_dir"):
            candidates.append(
                Path(reference["gate_dir"])
                / "report"
                / "judge_qualification_stats.json"
            )
        candidates.extend(
            [
                DEFAULT_GATE_RESULTS_DIR / "report" / "judge_qualification_stats.json",
                self.results_dir
                / "judge_qualification"
                / "report"
                / "judge_qualification_stats.json",
            ]
        )

        path = next((candidate for candidate in candidates if candidate.exists()), None)
        expected = (
            candidates[0]
            if candidates
            else DEFAULT_GATE_RESULTS_DIR / "report" / "judge_qualification_stats.json"
        )
        if path is None:
            return {
                "available": False,
                "status": "not_run",
                "path": str(expected),
            }
        try:
            stats = self._load_json_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "available": False,
                "status": "error",
                "path": str(path),
                "error": str(exc),
            }
        stats["available"] = True
        stats["path"] = str(path)
        stats["gate_dir"] = str(path.parents[1])
        if reference:
            stats["reference_path"] = str(reference_path)
        return stats

    def _iter_records(self, dimension: str) -> Iterator["_AggregateRecord"]:
        """Yield one ``_AggregateRecord`` per record in ``self.raw[dimension]``.

        Centralizes the boilerplate that the six dimension aggregators each
        used to inline (extract scores+metadata, split model into short form,
        pull persona_id/task_id/eval_id). Callers apply their own field-level
        score filtering on top.
        """
        for r in self.raw.get(dimension, []):
            if not isinstance(r, dict):
                continue
            scores = r.get("scores") or {}
            metadata = r.get("metadata") or {}
            model_raw = str(metadata.get("model", ""))
            yield _AggregateRecord(
                scores=scores,
                metadata=metadata,
                model=model_raw.split("/")[-1] if "/" in model_raw else model_raw,
                persona_id=str(metadata.get("persona_id", "")),
                task_id=str(metadata.get("task_id", "")),
                eval_id=str(r.get("eval_id", "")),
            )

    def _aggregate_d1(self) -> dict:
        by_model: dict[str, list] = defaultdict(list)
        by_persona: dict[str, list] = defaultdict(list)
        by_task: dict[str, list] = defaultdict(list)
        by_model_persona: dict[str, list] = defaultdict(list)

        score_field = primary_score_field("S1")
        for rec in self._iter_records("S1"):
            score = rec.scores.get(score_field, 0)
            if score <= 0:
                continue
            by_model[rec.model].append(score)
            by_persona[rec.persona_id].append(score)
            by_task[rec.task_id].append(score)
            by_model_persona[f"{rec.model}__{rec.persona_id}"].append(score)

        return {
            "by_model": {k: _cell(v) for k, v in by_model.items()},
            "by_persona": {k: _cell(v) for k, v in by_persona.items()},
            "by_task": {k: _cell(v) for k, v in by_task.items()},
            "by_model_persona": {k: _cell(v) for k, v in by_model_persona.items()},
        }

    def _aggregate_d2(self) -> dict:
        by_model: dict[str, list] = defaultdict(list)
        by_model_temp: dict[str, list] = defaultdict(list)

        score_field = primary_score_field("S3")
        for rec in self._iter_records("S3"):
            score = rec.scores.get(score_field, 0)
            if score <= 0:
                continue
            tutor_t = rec.metadata.get("tutor_temperature", "?")
            by_model[rec.model].append(score)
            by_model_temp[f"{rec.model}__{tutor_t}"].append(score)

        return {
            "by_model": {k: _cell(v) for k, v in by_model.items()},
            "by_model_temp": {k: _cell(v) for k, v in by_model_temp.items()},
        }

    def _aggregate_d3(self) -> dict:
        by_model: dict[str, list] = defaultdict(list)
        drift_onsets: list = []
        all_curves: dict[str, list[list]] = defaultdict(list)

        score_field = primary_score_field("S2")
        # Canonical conversation length is seven scored student turns
        # (see method.tex / matrix). One judge output occasionally emits an
        # extra trailing per-turn entry; truncate to the canonical length so
        # downstream curves are well-defined and the chart x-axis is stable.
        canonical_turns = 7
        for rec in self._iter_records("S2"):
            score = rec.scores.get(score_field, 0)
            if score <= 0:
                continue
            by_model[rec.model].append(score)
            onset = rec.metadata.get(
                "drift_onset_turn", rec.scores.get("drift_onset_turn")
            )
            if onset is not None:
                drift_onsets.append(onset)
            fidelity = rec.scores.get("per_turn_fidelity", [])
            if fidelity:
                all_curves[rec.model].append(list(fidelity)[:canonical_turns])

        avg_curves: dict[str, list[dict]] = {}
        for model, curves in all_curves.items():
            if not curves:
                continue
            per_turn_stats: list[dict] = []
            for t in range(canonical_turns):
                values = [c[t] for c in curves if t < len(c)]
                if not values:
                    continue
                arr = np.asarray(values, dtype=float)
                mean = float(arr.mean())
                n = int(arr.size)
                if n >= 2:
                    se = float(arr.std(ddof=1)) / float(np.sqrt(n))
                    ci_lo = mean - 1.96 * se
                    ci_hi = mean + 1.96 * se
                else:
                    ci_lo = ci_hi = mean
                per_turn_stats.append(
                    {
                        "turn": t + 1,
                        "mean": mean,
                        "ci_lo": ci_lo,
                        "ci_hi": ci_hi,
                        "n": n,
                    }
                )
            avg_curves[model] = per_turn_stats

        return {
            "by_model": {k: _cell(v) for k, v in by_model.items()},
            "drift_onset_mean": safe_mean(drift_onsets) or 0.0,
            "avg_fidelity_curves": avg_curves,
        }

    def _aggregate_failure_taxonomy(self) -> dict:
        by_type: dict[str, int] = defaultdict(int)
        by_dimension: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_persona: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_task: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_phase: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_rubric: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        severity_values: dict[str, list[float]] = defaultdict(list)
        top_examples: list[dict] = []
        d3_numeric = {
            "knowledge_leak_events": 0,
            "co_teacher_drift_events": 0,
        }

        for dimension, records in self.raw.items():
            if not isinstance(records, list):
                continue
            for record in records:
                scores = record.get("scores", {})
                metadata = record.get("metadata", {})
                model = metadata.get("model", "unknown").split("/")[-1]
                persona = metadata.get("persona_id", "unknown")
                task = metadata.get("task_id", "unknown")
                phase = metadata.get("phase") or dimension
                rubric = metadata.get("rubric_id") or dimension
                try:
                    score_field = primary_score_field(dimension)
                except KeyError:
                    score_field = None
                score = scores.get(score_field) if score_field else None
                severity = 5 - score if isinstance(score, (int, float)) else None
                failure_types = scores.get("failure_types") or []
                if isinstance(failure_types, str):
                    failure_types = [failure_types]
                for failure_type in failure_types:
                    if not failure_type:
                        continue
                    by_type[failure_type] += 1
                    by_dimension[dimension][failure_type] += 1
                    by_model[model][failure_type] += 1
                    by_persona[persona][failure_type] += 1
                    by_task[task][failure_type] += 1
                    by_phase[phase][failure_type] += 1
                    by_rubric[rubric][failure_type] += 1
                    if severity is not None:
                        severity_values[failure_type].append(float(severity))
                    if len(top_examples) < 50:
                        top_examples.append(
                            {
                                "eval_id": record.get("eval_id"),
                                "dimension": dimension,
                                "rubric": rubric,
                                "model": model,
                                "persona_id": persona,
                                "task_id": task,
                                "phase": phase,
                                "failure_type": failure_type,
                                "dominant_failure_type": scores.get(
                                    "dominant_failure_type"
                                ),
                                "failure_evidence": scores.get("failure_evidence", ""),
                                "severity": severity,
                            }
                        )
                if dimension == "S2":
                    leaks = scores.get("per_turn_knowledge_leak", [])
                    drifts = scores.get("per_turn_co_teacher_drift", [])
                    d3_numeric["knowledge_leak_events"] += sum(1 for x in leaks if x)
                    d3_numeric["co_teacher_drift_events"] += sum(1 for x in drifts if x)

        return {
            "by_type": dict(by_type),
            "by_dimension": {k: dict(v) for k, v in by_dimension.items()},
            "by_model": {k: dict(v) for k, v in by_model.items()},
            "by_persona": {k: dict(v) for k, v in by_persona.items()},
            "by_task": {k: dict(v) for k, v in by_task.items()},
            "by_phase": {k: dict(v) for k, v in by_phase.items()},
            "by_rubric": {k: dict(v) for k, v in by_rubric.items()},
            "severity": {
                failure_type: {
                    "mean": safe_mean(values) or 0.0,
                    "max": max(values) if values else 0,
                    "n": len(values),
                }
                for failure_type, values in severity_values.items()
            },
            "top_examples": sorted(
                top_examples,
                key=lambda item: (
                    item.get("severity") is not None,
                    item.get("severity") or 0,
                ),
                reverse=True,
            )[:20],
            "recommendations": {
                key: _FAILURE_RECOMMENDATIONS.get(key, _FAILURE_RECOMMENDATION_DEFAULT)
                for key in by_type
            },
            "d3_numeric": d3_numeric,
        }

    def _control_by_persona_judge(self) -> dict[str, dict[str, dict]]:
        """Compute persona × judge mean of the S6 distinctiveness score.

        Reads ``self.multi`` (per-eval scores by judge) so the paper-side
        ``control_bars`` figure can break the S6 mean down by judge. Returns
        ``{persona: {judge: {mean, n}}}``; empty when multi-judge data is
        unavailable.
        """
        out: dict[str, dict[str, dict]] = {}
        if not self.multi:
            return out
        block = (self.multi.get("dimensions") or {}).get("control") or {}
        try:
            field = primary_score_field("S6")
        except KeyError:
            return out
        per_persona: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: {"sonnet": [], "gpt54": [], "gemini": []}
        )
        for row in block.get("per_eval", []):
            persona = (row.get("metadata") or {}).get("persona_id")
            if not persona:
                continue
            for judge in ("sonnet", "gpt54", "gemini"):
                v = (row.get("scores_by_judge", {}).get(judge) or {}).get(field)
                if isinstance(v, (int, float)):
                    per_persona[persona][judge].append(float(v))
        for persona, judges in per_persona.items():
            out[persona] = {
                judge: {
                    "mean": safe_mean(vals) or 0.0,
                    "n": len(vals),
                }
                for judge, vals in judges.items()
            }
        return out

    def _aggregate_control(self) -> dict:
        """Aggregate control / persona-vs-placebo evidence.

        ``distinctiveness`` is the S6 rubric's per-record score (1-5) for how
        clearly the persona-conditioned conversation differs from the
        no-persona placebo, judged side-by-side. The rubric prescribes the
        aggregation: ``mean(distinctiveness)`` and ``high_score_ratio =
        count(distinctiveness >= 4) / n``. We additionally report the
        standardized effect against the rubric's "1 = no detectable
        difference" baseline — that is the persona-vs-placebo effect size on
        the rubric's own scale, and avoids the cross-rubric comparison that
        a Cohen's d against S1 ``overall`` would imply (S1 measures fit to
        contract on the persona-on side only; it has no placebo counterpart
        in the existing artifacts).
        """
        by_persona: dict[str, list] = defaultdict(list)
        score_field = primary_score_field("S6")
        for rec in self._iter_records("S6"):
            s = rec.scores.get(score_field, 0)
            if s <= 0:
                continue
            by_persona[rec.persona_id].append(s)

        all_scores = [s for v in by_persona.values() for s in v]
        n = len(all_scores)
        overall_ci = bootstrap_mean_ci(all_scores)
        mean = safe_mean(all_scores) or 0.0
        std = safe_std(all_scores) or 0.0
        high_threshold = 4.0
        high_count = sum(1 for s in all_scores if s >= high_threshold)
        high_score_ratio = (high_count / n) if n else 0.0
        baseline_unrecognizable = 1.0
        standardized_effect_vs_baseline: float | None
        if std > 0:
            standardized_effect_vs_baseline = (mean - baseline_unrecognizable) / std
        else:
            standardized_effect_vs_baseline = None

        # Per-persona high-score ratio (matches S6 ``aggregation_formula``).
        by_persona_evidence: dict[str, dict] = {}
        for pid, vals in by_persona.items():
            cell = _cell(vals)
            cell["high_score_ratio"] = (
                sum(1 for v in vals if v >= high_threshold) / len(vals) if vals else 0.0
            )
            by_persona_evidence[pid] = cell

        return {
            "overall_mean": mean,
            "overall_std": std,
            "overall_ci_low": overall_ci["ci_low"],
            "overall_ci_high": overall_ci["ci_high"],
            "n": n,
            "by_persona": by_persona_evidence,
            "by_persona_judge": self._control_by_persona_judge(),
            # Persona-vs-placebo evidence on the rubric's own scale:
            "high_score_ratio": high_score_ratio,
            "high_score_threshold": high_threshold,
            "high_score_count": high_count,
            "baseline_unrecognizable": baseline_unrecognizable,
            "standardized_effect_vs_baseline": standardized_effect_vs_baseline,
        }

    def _aggregate_p1(self) -> dict:
        by_persona: dict[str, list] = defaultdict(list)
        by_facet: dict[str, list] = defaultdict(list)
        score_field = primary_score_field("S5")
        for rec in self._iter_records("S5"):
            score = rec.scores.get(score_field, 0)
            if score <= 0:
                continue
            by_persona[rec.persona_id].append(score)
            by_facet[rec.metadata.get("facet", "")].append(score)
        all_scores = [score for values in by_persona.values() for score in values]
        return {
            "overall_mean": safe_mean(all_scores) or 0.0,
            "n": len(all_scores),
            "by_persona": {
                key: {"mean": safe_mean(values) or 0.0, "n": len(values)}
                for key, values in by_persona.items()
            },
            "by_facet": {
                key: {"mean": safe_mean(values) or 0.0, "n": len(values)}
                for key, values in by_facet.items()
            },
        }

    def _aggregate_b1(self) -> dict:
        correct = 0
        compared = 0
        confidences: list[float] = []
        by_persona_hits: dict[str, list[int]] = defaultdict(list)
        by_persona_model_hits: dict[str, list[int]] = defaultdict(list)
        by_task_hits: dict[str, list[int]] = defaultdict(list)
        # S3-pair consistency: same (persona, task, model, repeat_tag-family)
        # across multiple runs should identify the same persona every time.
        pair_groups: dict[str, list[str]] = defaultdict(list)
        # Panel-3 per-judge accuracy. ``identified_persona_by_judge`` is
        # populated by ``aggregate._merge_panel_3_scores`` when judge_view
        # ='panel_3'. Falls back to single-judge metrics when absent.
        sonnet_hits: list[int] = []
        gpt54_hits: list[int] = []
        gemini_hits: list[int] = []
        all_correct: list[int] = []
        any_correct: list[int] = []
        per_persona_judge: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: {"sonnet": [], "gpt54": [], "gemini": []}
        )
        for rec in self._iter_records("S4"):
            scores = rec.scores
            identified = str(scores.get("identified_persona", "")).strip()
            expected = rec.persona_id.strip()
            if identified and expected:
                compared += 1
                hit = int(identified == expected)
                correct += hit
                by_persona_hits[expected].append(hit)
                by_task_hits[rec.task_id].append(hit)
                if rec.model:
                    by_persona_model_hits[f"{expected}__{rec.model}"].append(hit)
            # Panel-3 per-judge accuracy breakdown
            by_judge = scores.get("identified_persona_by_judge") or {}
            if expected and by_judge:
                s_id = str(by_judge.get("sonnet", "")).strip()
                g_id = str(by_judge.get("gpt54", "")).strip()
                m_id = str(by_judge.get("gemini", "")).strip()
                s_hit = int(s_id == expected)
                g_hit = int(g_id == expected)
                m_hit = int(m_id == expected)
                sonnet_hits.append(s_hit)
                gpt54_hits.append(g_hit)
                gemini_hits.append(m_hit)
                all_correct.append(int(s_hit and g_hit and m_hit))
                any_correct.append(int(s_hit or g_hit or m_hit))
                per_persona_judge[expected]["sonnet"].append(s_hit)
                per_persona_judge[expected]["gpt54"].append(g_hit)
                per_persona_judge[expected]["gemini"].append(m_hit)
            confidence = scores.get("confidence")
            if isinstance(confidence, (int, float)):
                confidences.append(confidence)
            if expected and rec.task_id and rec.model:
                repeat_tag = str(rec.metadata.get("repeat_tag", ""))
                tt = repeat_tag.split("_", 1)[1] if "_" in repeat_tag else "?"
                pair_groups[f"{expected}__{rec.task_id}__{rec.model}__{tt}"].append(
                    identified or "<none>"
                )

        by_persona = {
            pid: {
                "accuracy": safe_mean(hits) or 0.0,
                "n": len(hits),
            }
            for pid, hits in by_persona_hits.items()
        }
        by_persona_model = {
            key: {"accuracy": safe_mean(hits) or 0.0, "n": len(hits)}
            for key, hits in by_persona_model_hits.items()
        }
        by_task = {
            task: {"accuracy": safe_mean(hits) or 0.0, "n": len(hits)}
            for task, hits in by_task_hits.items()
        }
        # Cross-run identification consistency: for each (persona, task, model)
        # group with >= 2 S4 samples, did every sample produce the same
        # identified_persona? Reports as fraction of groups that were fully
        # consistent. This is the S4 analogue of S3 reproducibility.
        consistent = 0
        total_multi_run_groups = 0
        for group, identifications in pair_groups.items():
            if len(identifications) < 2:
                continue
            total_multi_run_groups += 1
            if len(set(identifications)) == 1:
                consistent += 1
        d2_pair_consistency = (
            consistent / total_multi_run_groups if total_multi_run_groups else None
        )
        # Panel-3 accuracy semantics. Three flavors:
        #   - by_judge: each judge's individual accuracy
        #   - mean_accuracy: simple average of the three judges (the "panel-3
        #     mean" analog of numeric averaging)
        #   - strict (all three correct) / lenient (any correct): consensus options
        panel_3_block: dict | None = None
        if sonnet_hits:
            panel_3_block = {
                "by_judge": {
                    "sonnet": safe_mean(sonnet_hits) or 0.0,
                    "gpt54": safe_mean(gpt54_hits) or 0.0,
                    "gemini": safe_mean(gemini_hits) or 0.0,
                },
                "mean_accuracy": (
                    safe_mean(sonnet_hits + gpt54_hits + gemini_hits) or 0.0
                ),
                "all_correct_accuracy": safe_mean(all_correct) or 0.0,
                "any_correct_accuracy": safe_mean(any_correct) or 0.0,
                "n": len(sonnet_hits),
            }
        by_persona_judge = {
            persona: {
                judge: {
                    "accuracy": safe_mean(hits) or 0.0,
                    "n": len(hits),
                }
                for judge, hits in judges.items()
            }
            for persona, judges in per_persona_judge.items()
        }
        return {
            "accuracy": correct / compared if compared else 0.0,
            "n": compared,
            "mean_confidence": safe_mean(confidences) or 0.0,
            "by_persona": by_persona,
            "by_persona_model": by_persona_model,
            "by_persona_judge": by_persona_judge,
            "by_task": by_task,
            "d2_pair_consistency": d2_pair_consistency,
            "d2_pair_consistent_groups": consistent,
            "d2_pair_total_groups": total_multi_run_groups,
            "panel_3": panel_3_block,
        }

    def _compute_model_ranking(
        self, d1_stats: dict, d2_stats: dict, d3_stats: dict
    ) -> list[dict]:
        d1 = d1_stats["by_model"]
        d2 = d2_stats["by_model"]
        d3 = d3_stats["by_model"]
        models = set(d1.keys()) | set(d2.keys()) | set(d3.keys())

        # Per-record raw scores per model, used to bootstrap a CI for the
        # composite mean. Each record contributes one observation; the
        # composite-CI bootstrap therefore reflects the joint sampling
        # variability across all three dimensions for that model.
        per_record_by_model: dict[str, list[float]] = defaultdict(list)
        for dim in ("S1", "S3", "S2"):
            field = primary_score_field(dim)
            for r in self.raw.get(dim, []):
                score = r.get("scores", {}).get(field, 0)
                if score and score > 0:
                    model = r.get("metadata", {}).get("model", "").split("/")[-1]
                    if model:
                        per_record_by_model[model].append(float(score))

        rankings = []
        for m in models:
            scores = {
                "S1": d1.get(m, {}).get("mean"),
                "S3": d2.get(m, {}).get("mean"),
                "S2": d3.get(m, {}).get("mean"),
            }
            scores_ci = {
                "S1": {
                    "low": d1.get(m, {}).get("ci_low"),
                    "high": d1.get(m, {}).get("ci_high"),
                    "n": d1.get(m, {}).get("n"),
                },
                "S3": {
                    "low": d2.get(m, {}).get("ci_low"),
                    "high": d2.get(m, {}).get("ci_high"),
                    "n": d2.get(m, {}).get("n"),
                },
                "S2": {
                    "low": d3.get(m, {}).get("ci_low"),
                    "high": d3.get(m, {}).get("ci_high"),
                    "n": d3.get(m, {}).get("n"),
                },
            }
            available_dimensions = [
                dimension
                for dimension, score in scores.items()
                if isinstance(score, (int, float))
            ]
            composite = safe_mean(list(scores.values())) or 0.0
            composite_ci = bootstrap_mean_ci(per_record_by_model.get(m, []))
            rankings.append(
                {
                    "model": m,
                    "scores": scores,
                    "scores_ci": scores_ci,
                    "composite": composite,
                    "composite_ci_low": composite_ci["ci_low"],
                    "composite_ci_high": composite_ci["ci_high"],
                    "composite_n": composite_ci["n"],
                    "available_dimensions": available_dimensions,
                }
            )
        rankings.sort(key=lambda x: x["composite"], reverse=True)
        return rankings

    # ----- HTML sections -----

    def _header(self) -> str:
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Student Simulator Stability Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #333; line-height: 1.6; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #16213e; padding-bottom: 10px; }}
  h2 {{ color: #16213e; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
  h3 {{ color: #0f3460; }}
  table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 10px 14px; text-align: center; }}
  th {{ background: #16213e; color: white; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  .table-wrap {{ width: 100%; overflow-x: auto; margin: 15px 0; }}
  .audit-table {{ table-layout: fixed; min-width: 760px; }}
  .audit-table th:nth-child(1) {{ width: 90px; }}
  .audit-table th:nth-child(2) {{ width: 230px; }}
  .audit-table td {{ text-align: left; vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }}
  .status-pill {{ display: inline-block; border-radius: 999px; padding: 2px 9px; font-size: 0.82em; font-weight: 700; text-transform: uppercase; }}
  .status-pass {{ background: #e8f6ef; color: #176f3d; }}
  .status-fail {{ background: #fdecea; color: #a93226; }}
  .status-warn {{ background: #fff4d6; color: #8a6500; }}
  .high {{ color: #27ae60; font-weight: bold; }}
  .mid {{ color: #f39c12; font-weight: bold; }}
  .low {{ color: #e74c3c; font-weight: bold; }}
  .card-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 15px 0; }}
  .card {{ background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; text-align: center; }}
  .card .val {{ font-size: 2em; font-weight: bold; color: #16213e; }}
  .card .lbl {{ font-size: 0.9em; color: #666; margin-top: 5px; }}
  .insight {{ background: #f0f7ff; border-left: 4px solid #16213e; border-radius: 4px; padding: 15px; margin: 15px 0; }}
  .rubric {{ background: #fffdf5; border: 1px solid #ead9a2; border-radius: 6px; padding: 14px 16px; margin: 14px 0; }}
  .rubric code {{ background: #f6f1df; padding: 1px 4px; border-radius: 3px; }}
  .example-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; margin: 15px 0; }}
  .example-card {{ border: 1px solid #d8dee9; border-left: 5px solid #e67e22; border-radius: 8px; padding: 14px 16px; background: #fff; display: flex; flex-direction: column; gap: 10px; }}
  .example-card.priority {{ border-left-color: #c0392b; background: #fffafa; }}
  .example-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .example-rank {{ font-weight: 700; color: #16213e; font-size: 1.05em; letter-spacing: 0.02em; }}
  .example-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .chip {{ display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: 0.78em; font-weight: 700; white-space: nowrap; letter-spacing: 0.02em; }}
  .chip-dim {{ background: #e8eef8; color: #0f3460; }}
  .chip-failure {{ background: #f6e7db; color: #8a3b12; }}
  .chip-severity {{ background: #fdecea; color: #a93226; }}
  .chip-severity.low {{ background: #e8f6ef; color: #176f3d; }}
  .example-meta {{ color: #444; font-size: 0.92em; line-height: 1.5; display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; }}
  .example-meta dt {{ color: #888; font-weight: 600; text-transform: uppercase; font-size: 0.78em; letter-spacing: 0.04em; }}
  .example-meta dd {{ margin: 0; color: #222; overflow-wrap: anywhere; }}
  .example-evidence {{ color: #222; font-size: 0.94em; line-height: 1.55; background: #fafbfc; border-left: 3px solid #cfd8e3; padding: 9px 12px; border-radius: 4px; overflow-wrap: anywhere; }}
  .example-evidence.empty {{ color: #888; font-style: italic; }}
  .example-footer {{ color: #9aa0a6; font-size: 0.76em; font-family: SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
  .example-links {{ color: #555; font-size: 0.82em; margin-top: 2px; }}
  .example-links a {{ color: #0f3460; text-decoration: none; border-bottom: 1px dotted #0f3460; }}
  .example-links a:hover {{ color: #16213e; border-bottom-style: solid; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.85em; font-weight: 600; }}
  .badge-gold {{ background: #ffd700; color: #333; }}
  .badge-silver {{ background: #c0c0c0; color: #333; }}
  .badge-bronze {{ background: #cd7f32; color: white; }}
  .conclusion {{ background: #eef5ff; border: 1px solid #b3d4fc; border-radius: 8px; padding: 20px; margin: 20px 0; }}
  .muted {{ color: #888; font-weight: normal; font-size: 0.85em; }}
  table.multi-view {{ font-size: 0.95em; }}
  table.multi-view th {{ font-size: 0.88em; padding: 8px 10px; }}
  table.multi-view td {{ padding: 8px 10px; }}
</style></head><body>
<h1>Student Simulator Stability Report</h1>
<p><em>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>
"""

    def _footer(self) -> str:
        return "</body></html>"

    def _rubric_block(self, dimension: str) -> str:
        rubric = self.rubrics.get(dimension)
        if not rubric:
            return ""
        fields = ", ".join(f"<code>{field}</code>" for field in rubric["score_fields"])
        inputs = ", ".join(rubric.get("judge_context_inputs", []))
        scales = "; ".join(
            f"{score}: {text}" for score, text in rubric.get("score_scales", {}).items()
        )
        return f"""<div class="rubric">
<strong>Rubric:</strong> {rubric['rubric_id']} ({rubric['version']})<br>
<strong>Definition:</strong> {rubric['definition']}<br>
<strong>Judge context:</strong> {inputs}<br>
<strong>Score fields:</strong> {fields}<br>
<strong>Scale:</strong> {scales}<br>
<strong>Aggregation:</strong> {rubric['aggregation_formula']}
</div>"""

    def _load_json_file(self, path: Path) -> dict:
        return load_json(path) if path.exists() else {}

    def _artifact_href(self, path: str | Path | None) -> str:
        if not path:
            return "#"
        artifact = Path(path)
        if not artifact.is_absolute():
            artifact = self.output_dir / artifact
        return Path(os.path.relpath(artifact, start=self.output_dir)).as_posix()

    def _status_block(self) -> str:
        def _load_or_empty(path: Path) -> dict:
            return load_json(path) if path.exists() else {}

        metadata = _load_or_empty(
            self.results_dir / "report" / "stability_metadata.json"
        )
        judge = _load_or_empty(self.results_dir / "report" / "judge_agreement.json")
        human = _load_or_empty(
            self.results_dir / "human_alignment" / "agreement_report.json"
        )
        comparison = metadata.get(
            "model_comparison_label", "cross-vendor candidate selection"
        )
        return f"""<div class="insight">
<strong>Validation flow:</strong> persona contract → generated conversation → rendered judge prompt/context → judge JSON score → aggregate metric → chart → interpretation.<br>
<strong>Tutor/student/judge:</strong> tutor stimulus = {metadata.get('tutor_model', 'unknown')}; student candidates = {', '.join(metadata.get('student_models', []))}; primary judge = {metadata.get('primary_judge', 'unknown')} at temperature {metadata.get('judge_temperature', 'unknown')}.<br>
<strong>Human alignment status:</strong> {human.get('human_alignment_status', 'not_run')}. <strong>Multi-judge status:</strong> {judge.get('multi_judge_status', 'not_run')}.<br>
<strong>Model comparison policy:</strong> {comparison}; rankings are candidate-selection signals, not same-level parameter-matched claims.
</div>"""

    # ------------------------------------------------------------------
    # Multi-judge 4-view comparison (Section 1.5)
    # ------------------------------------------------------------------

    _MULTI_VIEW_LABELS = {
        "sonnet": "Sonnet",
        "gpt54": "GPT-5.4",
        "gemini": "Gemini",
        "panel_3": "Panel-3 mean",
    }

    def _section_multi_judge_view(self, stats: dict) -> str:
        """Render the 4-view comparison block. Shows up when
        ``evaluations/multi_judge_aggregates.json`` is present."""
        if not self.multi:
            return ""
        component_html = self._components["multi_judge_view"].render_html()
        if not component_html:
            return ""

        views = self.multi.get("judge_model_map", {})
        judge_line = ", ".join(
            f"<code>{self._MULTI_VIEW_LABELS[k]}</code> = {_html(v)}"
            for k, v in views.items()
        )
        return f"""<h2>5. Cross-judge Invariance — 4-view Comparison</h2>
<div class="rubric">
<strong>What this shows:</strong> every aggregate score computed under four
parallel judge views so you can see the impact of judge selection on model
rankings.<br>
<strong>Views:</strong> {judge_line}; <code>Panel-3 mean</code> averages
all three judges per eval.<br>
<strong>Cell format:</strong> mean ±std across the group's evals.<br>
<strong>Rule of thumb:</strong> a large gap between an individual judge and
the Panel-3 mean on a dimension flags that judge as pulling the average;
treat the Panel-3 mean as the canonical aggregate.
</div>
{component_html}"""

    def _section_judge_qualification(self, stats: dict) -> str:
        return self._components["judge_qualification_section"].render_html()

    def _section_scope_header(self, stats: dict) -> str:
        """§0: Scope statement + Claim A/B/C verdict triple + caveat box.

        Each claim card surfaces its load-bearing evidence (means + 95% CI
        for Claim A, composite + judge-invariance for Claim B, within-one-
        point rate for Claim C). No threshold-based pass/fail language.
        """
        ranking = stats.get("model_ranking") or []
        best = ranking[0] if ranking else None
        scope = """<div class="rubric">
<strong>Scope:</strong> This experiment supports three independent paper claims about the StudentSimulator.
<ul>
<li><strong>Claim A — Stability:</strong> StudentSimulator preserves persona behavior under task / repeat / model / tutor-temperature perturbations (sections S1, S3, S2).</li>
<li><strong>Claim B — Model selection:</strong> Of three candidate student backbones, GPT-5.4 is selected for the QTB main benchmark (Model Selection section).</li>
<li><strong>Claim C — Metric calibration:</strong> The LLM-judge numbers underlying Claims A and B agree with a human quant expert within 1 point at a high rate on the sampled alignment study (Human-LLM Alignment section).</li>
</ul>
<strong>Out of scope:</strong> QTB main benchmark judge selection; persona contract design ablations; tutor model selection.
</div>"""
        cards = (
            '<div class="card-row" style="grid-template-columns:repeat(3,1fr)">'
            f"{self._claim_a_card(best)}"
            f"{self._claim_b_card(stats, ranking, best)}"
            f"{self._claim_c_card()}"
            "</div>"
        )
        caveat = self._scope_caveat()
        return f"""<h2>0. Scope &amp; Claim Verdicts</h2>
{scope}
{cards}
{caveat}"""

    def _claim_a_card(self, best: dict | None) -> str:
        if not best:
            return (
                '<div class="card" style="text-align:left">'
                "<strong>Claim A — Stability</strong>"
                "<p>No model ranking available.</p></div>"
            )
        scores = best.get("scores") or {}
        scores_ci = best.get("scores_ci") or {}

        def cell(dim: str) -> str:
            mean = scores.get(dim)
            ci = scores_ci.get(dim) or {}
            lo, hi = ci.get("low"), ci.get("high")
            if (
                isinstance(mean, (int, float))
                and isinstance(lo, (int, float))
                and isinstance(hi, (int, float))
            ):
                return f"{dim} = {mean:.2f} [{lo:.2f}, {hi:.2f}]"
            if isinstance(mean, (int, float)):
                return f"{dim} = {mean:.2f}"
            return f"{dim} = n/a"

        evidence_lines = "<br>".join(cell(d) for d in ("S1", "S3", "S2"))
        return (
            '<div class="card" style="text-align:left">'
            "<strong>Claim A — Stability</strong>"
            f"<p>Top student model <strong>{_html(best['model'])}</strong>:<br>{evidence_lines}</p>"
            '<p class="muted">Bootstrap 95% CI in brackets.</p>'
            "</div>"
        )

    def _claim_b_card(self, stats: dict, ranking: list[dict], best: dict | None) -> str:
        if not best:
            return (
                '<div class="card" style="text-align:left">'
                "<strong>Claim B — Model Selection</strong>"
                "<p>No model ranking available.</p></div>"
            )
        invariant = self._composite_judge_invariance
        if invariant is not None:
            n_views = len(invariant.get("rankings", {}))
            ranks_match = invariant.get("rankings_identical_across_judges")
            spread = invariant.get("max_pairwise_composite_spread", 0)
            verdict = "identical" if ranks_match else "DIFFERS"
            invariance_line = (
                f"Rank order {verdict} across {n_views} judge views "
                f"(max composite spread {spread:.3f})."
            )
        else:
            invariance_line = (
                "Judge-invariance not computable (multi-judge data missing)."
            )
        composite_lo = best.get("composite_ci_low")
        composite_hi = best.get("composite_ci_high")
        if isinstance(composite_lo, (int, float)) and isinstance(
            composite_hi, (int, float)
        ):
            comp_line = (
                f"Composite = {best['composite']:.2f} "
                f"[{composite_lo:.2f}, {composite_hi:.2f}]"
            )
        else:
            comp_line = f"Composite = {best['composite']:.2f}"
        return (
            '<div class="card" style="text-align:left">'
            "<strong>Claim B — Model Selection</strong>"
            f"<p>Selected: <strong>{_html(best['model'])}</strong> ({len(ranking)} candidates evaluated).</p>"
            f"<p>{comp_line}<br>{invariance_line}</p>"
            "</div>"
        )

    def _claim_c_card(self) -> str:
        metrics = self._human.get("agreement_metrics") or {}
        # Use persona_fidelity as the headline calibration metric; fall
        # back to whatever numeric block is populated.
        candidates = (
            "persona_fidelity",
            "knowledge_boundary_pass",
            "emotional_match",
        )
        chosen_name: str | None = None
        chosen: dict | None = None
        for name in candidates:
            value = metrics.get(name)
            if value:
                chosen = value
                chosen_name = name
                break
        if not chosen:
            return (
                '<div class="card" style="text-align:left">'
                "<strong>Claim C — Metric Calibration</strong>"
                "<p>Human-LLM alignment not yet scored.</p></div>"
            )
        rate = chosen.get("within_one_point_rate", 0)
        n = chosen.get("n", 0)
        mad = chosen.get("mean_absolute_difference", 0)
        return (
            '<div class="card" style="text-align:left">'
            "<strong>Claim C — Metric Calibration</strong>"
            f"<p>Within-1-point rate on <code>{_html(chosen_name)}</code>: "
            f"<strong>{rate:.1%}</strong> (n={n}, MAD={mad:.2f}).</p>"
            '<p class="muted">Sampled human-LLM alignment study.</p>'
            "</div>"
        )

    def _scope_caveat(self) -> str:
        audit = self._audit or {}
        checks = audit.get("checks") or []
        failing = [c for c in checks if not c.get("ok")]
        if failing:
            failed_names = ", ".join(
                f"<code>{_html(c.get('name'))}</code>" for c in failing[:6]
            )
            extra = "" if len(failing) <= 6 else f" (+{len(failing) - 6} more)"
            caveat_body = (
                f"Pre-existing data-quality items currently failing: {failed_names}{extra}."
                " Numbers in this report are computed against the artifacts as-is."
            )
        else:
            caveat_body = (
                "Data-quality audit reports all checks pass; report numbers are "
                "computed against the current artifacts."
            )
        return (
            '<div class="insight">'
            "<strong>Caveat — known data limitations:</strong> "
            f"{caveat_body} See Methodology Appendix &sect;C for the full audit."
            "</div>"
        )

    def _section_model_selection(self, stats: dict) -> str:
        """§7: Cross-vendor candidate selection ranking + radar + judge-invariance."""
        ranking = stats["model_ranking"]
        total_evals = sum(len(self.raw.get(k, [])) for k in DIMENSION_TO_FILE)

        composite_block = """<div class="rubric">
<strong>Composite metric policy:</strong> cross-vendor candidate selection, not parameter-matched same-level ranking.<br>
<strong>Context:</strong> primary table uses the Panel-3 mean (Sonnet + GPT-5.4 + Gemini per-eval mean). S1 persona adherence, S3 cross-run reproducibility, and S2 anti-drift scores are aggregated per student model.<br>
<strong>Aggregation:</strong> Composite = mean of available S1, S3, and S2 scores. Lower-priority diagnostic dimensions (control, S5, S4) are reported separately in their own section.
</div>"""

        cards = f"""<h2>7. Model Selection (Claim B)</h2>
{self._status_block()}
{composite_block}
<div class="card-row">
  <div class="card"><div class="val">{total_evals}</div><div class="lbl">Total Evaluations</div></div>
  <div class="card"><div class="val">{len(ranking)}</div><div class="lbl">Models Tested</div></div>
  <div class="card"><div class="val">{len(self.raw.get('control', []))}</div><div class="lbl">Control Comparisons</div></div>
</div>"""

        radar = self._components["overview_radar"].render_html()
        ranking_table = self._components["ranking_table"].render_html()
        invariance_block = self._panel_invariance_block(stats)

        return (
            cards
            + radar
            + f"""
<h3>Cross-vendor Candidate Selection Ranking (Panel-3 mean)</h3>
{ranking_table}
<p><em>Composite = mean of available S1, S3, and S2 scores under the Panel-3 mean (Sonnet + GPT-5.4 + Gemini).</em></p>
{invariance_block}"""
        )

    def _headline_block(self, stats: dict, ranking: list[dict]) -> str:
        """One-paragraph TL;DR. Evidence-based: S1 mean + 95% CI for the
        top model, judge-invariance verdict, and Cohen's d for persona vs
        placebo. No threshold-based pass/fail language."""
        if not ranking:
            return ""
        best = ranking[0]
        n_models = len(ranking)
        d1_mean = best.get("scores", {}).get("S1")
        d1_ci = (best.get("scores_ci") or {}).get("S1") or {}
        d1_lo = d1_ci.get("low")
        d1_hi = d1_ci.get("high")
        if (
            isinstance(d1_mean, (int, float))
            and isinstance(d1_lo, (int, float))
            and isinstance(d1_hi, (int, float))
        ):
            d1_phrase = (
                f"<strong>{best['model']}</strong> S1 = {d1_mean:.2f} "
                f"[{d1_lo:.2f}, {d1_hi:.2f}]"
            )
        elif isinstance(d1_mean, (int, float)):
            d1_phrase = f"<strong>{best['model']}</strong> S1 = {d1_mean:.2f}"
        else:
            d1_phrase = f"<strong>{best['model']}</strong>"

        invariant = self._composite_judge_invariance
        if invariant is not None:
            n_views = len(invariant.get("rankings", {}))
            ranks_match = invariant.get("rankings_identical_across_judges")
            spread = invariant.get("max_pairwise_composite_spread", 0)
            if ranks_match:
                invariance_msg = (
                    f"rank order identical across {n_views} judge views "
                    f"(max composite spread {spread:.3f})"
                )
            else:
                invariance_msg = (
                    f"rank order DIFFERS across {n_views} judge views "
                    f"(max composite spread {spread:.3f}; see invariance table)"
                )
        else:
            invariance_msg = (
                "judge-invariance not computable (multi_judge_aggregates.json missing)"
            )

        control = stats.get("control") or {}
        ctrl_n = control.get("n") or 0
        ctrl_mean = control.get("overall_mean")
        ctrl_lo = control.get("overall_ci_low")
        ctrl_hi = control.get("overall_ci_high")
        ratio = control.get("high_score_ratio")
        eff = control.get("standardized_effect_vs_baseline")
        if ctrl_n and isinstance(ctrl_mean, (int, float)):
            persona_msg_parts = [f"S6 distinctiveness = {ctrl_mean:.2f}"]
            if isinstance(ctrl_lo, (int, float)) and isinstance(ctrl_hi, (int, float)):
                persona_msg_parts[0] += f" [{ctrl_lo:.2f}, {ctrl_hi:.2f}]"
            persona_msg_parts[0] += f" (n={ctrl_n})"
            if isinstance(ratio, (int, float)):
                persona_msg_parts.append(f"high-score ratio = {ratio:.0%}")
            if isinstance(eff, (int, float)):
                persona_msg_parts.append(
                    f"standardized effect vs baseline 1 = {eff:.1f}σ"
                )
            persona_msg = "persona-vs-placebo: " + "; ".join(persona_msg_parts)
        else:
            persona_msg = "persona-vs-placebo evidence unavailable"

        return f"""<div class="insight">
<strong>Evidence summary:</strong> {d1_phrase}; {invariance_msg};
{persona_msg}. Composite {best['composite']:.2f}/5.0 across S1+S3+S2 of
{n_models} candidates evaluated.
</div>"""

    @functools.cached_property
    def _composite_judge_invariance(self) -> dict | None:
        """Compute S1+S3+S2 composite per student model under each judge view.
        Returns dict with per-view rankings + invariance verdict, or None if
        multi-judge data not loaded."""
        if not self.multi:
            return None
        from statistics import mean

        # by_view: { view: { model: [scores across all S1+S3+S2 evals] } }
        by_view: dict[str, dict[str, list[float]]] = {
            v: defaultdict(list) for v in ("sonnet", "gpt54", "gemini", "panel_3")
        }
        for dim in ("S1", "S3", "S2"):
            field = primary_score_field(dim)
            block = self.multi.get("dimensions", {}).get(dim, {})
            for row in block.get("per_eval", []):
                model = (row.get("metadata") or {}).get("model")
                if not model:
                    continue
                for view in ("sonnet", "gpt54", "gemini"):
                    v = (row.get("scores_by_judge", {}).get(view) or {}).get(field)
                    if isinstance(v, (int, float)):
                        by_view[view][model].append(float(v))
                v_p3 = (row.get("aggregates", {}).get("panel_3") or {}).get(field)
                if isinstance(v_p3, (int, float)):
                    by_view["panel_3"][model].append(float(v_p3))
        composites: dict[str, dict[str, float]] = {}
        for view, mm in by_view.items():
            composites[view] = {
                model: float(mean(vals)) if vals else 0.0 for model, vals in mm.items()
            }
        rankings = {
            view: [m for m, _ in sorted(c.items(), key=lambda x: -x[1])]
            for view, c in composites.items()
        }
        ranks_match = len(rankings) >= 2 and all(
            rankings[v] == rankings["panel_3"] for v in rankings
        )
        all_models = set().union(*(c.keys() for c in composites.values()))
        max_spread = 0.0
        for m in all_models:
            vals = [c[m] for c in composites.values() if m in c]
            if len(vals) >= 2:
                max_spread = max(max_spread, max(vals) - min(vals))
        return {
            "composites": composites,
            "rankings": rankings,
            "rankings_identical_across_judges": ranks_match,
            "max_pairwise_composite_spread": round(max_spread, 4),
        }

    def _panel_invariance_block(self, stats: dict) -> str:
        """Render the per-judge composite ranking table beneath the primary
        Cross-vendor ranking. Demonstrates whether the conclusion holds
        regardless of which judge model we use."""
        invariant = self._composite_judge_invariance
        if invariant is None:
            return ""
        composites = invariant["composites"]
        all_models = sorted(
            set().union(*(c.keys() for c in composites.values())),
            key=lambda m: -composites["panel_3"].get(m, 0),
        )
        view_label = {
            "sonnet": "Sonnet only",
            "gpt54": "GPT-5.4 only",
            "gemini": "Gemini only",
            "panel_3": "Panel-3 mean",
        }
        view_columns = ("sonnet", "gpt54", "gemini", "panel_3")
        head = (
            "<tr><th>Student model</th>"
            + "".join(f"<th>{view_label[v]}</th>" for v in view_columns)
            + "<th>Spread</th></tr>"
        )
        rows_html = ""
        for m in all_models:
            cells = []
            for v in view_columns:
                val = composites[v].get(m, 0)
                cells.append(f"<td>{val:.3f}</td>")
            spread = max(composites[v].get(m, 0) for v in composites) - min(
                composites[v].get(m, 0) for v in composites
            )
            rows_html += (
                f"<tr><td><strong>{m}</strong></td>"
                + "".join(cells)
                + f"<td>{spread:.3f}</td></tr>"
            )
        ranks_match = invariant["rankings_identical_across_judges"]
        verdict = (
            '<span class="status-pill status-pass">RANK ORDER IDENTICAL ACROSS JUDGE VIEWS</span>'
            if ranks_match
            else '<span class="status-pill status-fail">RANK ORDER DIFFERS BY JUDGE</span>'
        )
        return f"""
<h3>Judge-invariance check — does the ranking hold regardless of judge?</h3>
<p>{verdict}. The table below shows the composite (S1+S3+S2 mean) under
each judge view. If rankings match across views, the model-selection conclusion
is robust to judge choice.</p>
<table>{head}{rows_html}</table>
<div class="insight">
<strong>Interpretation:</strong> A composite spread &lt; 0.10 means the three judge
views agree on the absolute quality of each model within ±0.05; a matching rank
order across all three views means the candidate-selection conclusion does not
depend on which judge we trust.
</div>"""

    def _section_premise_check(self, stats: dict) -> str:
        """§1: Premise check (control + S4 per-judge + S5).

        Verifies the experiment's premise: persona contract changes student
        output (control), targeted probes elicit expected signals (S5), and
        blind judges can identify the persona from live transcripts (S4).
        S4 is reported per-judge — the mixed-record overall accuracy is
        intentionally omitted (decisions §3, S4 mixed accuracy hides the
        per-judge spread).
        """
        control = stats["control"]
        p1 = stats["p1"]
        b1 = stats["b1"]
        control_chart = self._components["control_bars"].render_html()
        control_overall = control.get("overall_mean", 0)
        control_lo = control.get("overall_ci_low")
        control_hi = control.get("overall_ci_high")
        if isinstance(control_lo, (int, float)) and isinstance(
            control_hi, (int, float)
        ):
            control_ci = f"[{control_lo:.2f}, {control_hi:.2f}]"
        else:
            control_ci = ""
        ratio = control.get("high_score_ratio")
        thr = control.get("high_score_threshold")
        eff = control.get("standardized_effect_vs_baseline")
        n_ctrl = control.get("n")
        evidence_bits: list[str] = []
        if isinstance(ratio, (int, float)) and isinstance(thr, (int, float)):
            n_str = f" (n={n_ctrl})" if n_ctrl else ""
            evidence_bits.append(
                f"<strong>High-score ratio (≥{thr:g}):</strong> {ratio:.0%}{n_str}"
            )
        if isinstance(eff, (int, float)):
            evidence_bits.append(
                "<strong>Standardized effect vs unrecognizable baseline (1.0):</strong> "
                f"{eff:.1f}σ"
            )
        if evidence_bits:
            persona_evidence_line = (
                "<p>"
                + "<br>".join(evidence_bits)
                + " &mdash; per the S6 rubric's <code>aggregation_formula</code>; "
                "the standardized effect treats <code>distinctiveness=1</code> "
                "(no detectable difference) as the persona-absent null." + "</p>"
            )
        else:
            persona_evidence_line = ""
        panel_3 = b1.get("panel_3")
        if panel_3 and panel_3.get("n", 0) >= 1:
            by_judge = panel_3.get("by_judge") or {}
            n_panel = panel_3.get("n", 0)
            b1_rows = "".join(
                f"<tr><td>{label}</td><td>{rate:.2%}</td><td>{n_panel}</td></tr>"
                for label, rate in (
                    ("Sonnet (per-judge)", by_judge.get("sonnet", 0.0)),
                    ("GPT-5.4 (per-judge)", by_judge.get("gpt54", 0.0)),
                    ("Gemini (per-judge)", by_judge.get("gemini", 0.0)),
                    (
                        "All three correct (strict)",
                        panel_3.get("all_correct_accuracy", 0.0),
                    ),
                    (
                        "Any correct (lenient)",
                        panel_3.get("any_correct_accuracy", 0.0),
                    ),
                )
            )
            b1_table = (
                "<table><tr><th>Judge view</th><th>Accuracy</th><th>N</th></tr>"
                f"{b1_rows}</table>"
                f"<p class='muted'>Per-judge breakdown over {n_panel} Panel-3 records. "
                "Mixed-record overall accuracy intentionally omitted.</p>"
            )
        else:
            b1_table = (
                "<p>Panel-3 per-judge accuracy not available "
                "(records missing identified_persona_by_judge).</p>"
            )

        return f"""<h2>1. Premise Check (control + S4 per-judge + S5)</h2>
<p>These three diagnostics verify the experiment's premise: the persona
contract actually changes student output (<strong>control</strong>),
targeted probes elicit the expected persona signals (<strong>S5</strong>),
and a blind judge can identify the persona from live transcripts
(<strong>S4</strong>). They support — but do not gate — the S1-S2 stability
numbers in the dimension sections below.</p>

<h3>1.1 Control — Persona vs Placebo Distinguishability</h3>
{self._rubric_block("control")}
{control_chart}
<div class="card-row"><div class="card"><div class="val {_score_class(control_overall)}">{control_overall:.2f}</div>
<div class="lbl">Overall Distinctiveness (1-5) {control_ci}</div></div></div>
{persona_evidence_line}

<h3>1.2 S5 — Targeted Persona Probes</h3>
{self._rubric_block("S5")}
<table><tr><th>Metric</th><th>Value</th><th>N</th></tr>
<tr><td>Overall probe pass</td><td class="{_score_class(p1.get('overall_mean', 0))}">{p1.get('overall_mean', 0):.2f}</td><td>{p1.get('n', 0)}</td></tr>
</table>

<h3>1.3 S4 — Blind Persona Identification (per judge)</h3>
{self._rubric_block("S4")}
{b1_table}
<div class="insight">
<strong>Why per-judge only:</strong> S4's mixed-record overall accuracy
collapses the spread between judges. Reporting Sonnet, GPT-5.4, strict
(both-correct), and lenient (either-correct) separately preserves that
information for downstream interpretation.
</div>"""

    def _section_d1(self, stats: dict) -> str:
        heatmap = self._components["d1_heatmap"].render_html()
        failure_inline = self._components["failure_inline_d1"].render_html()
        return f"""<h2>2. S1 — Persona Adherence</h2>
{self._rubric_block("S1")}
{heatmap}
<div class="insight">
<strong>Insight guide:</strong> Low cells identify a persona/task/model combination where generated
student turns do not visibly match the contract. First check knowledge-boundary and emotional-tone
subscores, then tighten the copied persona contract rather than changing shared source personas.
</div>
<h3>2.1 S1 Failure Mix</h3>
{failure_inline}"""

    def _section_d2(self, stats: dict) -> str:
        chart = self._components["d2_bars"].render_html()
        temp_table = self._components["d2_by_model_temp"].render_html()
        failure_inline = self._components["failure_inline_d2"].render_html()
        return f"""<h2>3. S3 — Cross-run Reproducibility</h2>
{self._rubric_block("S3")}
{chart}
<div class="insight">
<strong>Ceiling effect note:</strong> At tutor t=0, both student and tutor are near-deterministic,
so high S3 scores are expected by design. The meaningful comparison is t=0 vs t=1:
a small gap means student stability is genuinely robust, not just an artifact of determinism.
</div>
<h3>3.1 Tutor-temperature Ablation</h3>
<p>Context: the same S3 reproducibility rubric is grouped by tutor temperature to separate
deterministic tutor effects from true student-simulator stability.</p>
{temp_table}
<div class="insight">
<strong>Interpretation:</strong> If S3 scores are similar across t=0 and t=1 for the same model,
the student simulator is robust to tutor variance — its persona behavior is driven by the prompt,
not by what the tutor says.
</div>
<h3>3.2 S3 Failure Mix</h3>
{failure_inline}"""

    def _section_d3(self, stats: dict) -> str:
        chart = self._components["d3_curves"].render_html()
        d3 = stats["d3"]
        onset = d3.get("drift_onset_mean", 0)
        onset_text = f"{onset:.1f}" if onset else "N/A"
        drift_table = self._components["d3_drift"].render_html()
        failure_inline = self._components["failure_inline_d3"].render_html()
        return f"""<h2>4. S2 — Drift Detection</h2>
{self._rubric_block("S2")}
{chart}
{drift_table}
<div class="insight"><strong>Average drift onset turn:</strong> {onset_text} (later = better)</div>
<h3>4.1 S2 Failure Mix</h3>
{failure_inline}"""

    def _section_human_alignment(self) -> str:
        human = self._human
        metrics_html = self._components["human_alignment_metrics"].render_html()
        b1_table = self._components["human_alignment_b1_per_judge"].render_html()
        breakdown_table = self._components["human_alignment_b1_breakdown"].render_html()
        disagreements_html = self._components[
            "human_alignment_disagreements"
        ].render_html()
        return f"""<h2>6. Human-LLM Judge Alignment (Claim C)</h2>
<div class="rubric">
<strong>Definition:</strong> Human quant-expert calibration compares sampled judge inputs against human labels across stability (S1/S3/S2), validity (control/S5), and S4 identification.<br>
<strong>Context:</strong> sample manifest, human label CSV, same-sample LLM judge label snapshot, aggregate judge scores, and disagreement notes.<br>
<strong>Fields:</strong> persona_fidelity, knowledge_boundary_pass, emotional_match, drift_onset_turn, failure_type, control_distinctiveness, control_persona_set_a, p1_facet_fit, p1_expected_signals_recall, S4 identified_persona (per-judge).<br>
<strong>Aggregation:</strong> numeric labels use mean absolute difference and within-one-point rate; failure_type uses exact/contained match rate; S4 identification reports per-judge accuracy vs human.
</div>
<p>Status: <code>{human.get('human_alignment_status', 'not_run')}</code></p>
{metrics_html}
{b1_table}
{breakdown_table}
{disagreements_html}"""

    def _section_judge_configuration(self) -> str:
        return self._components["judge_configuration_section"].render_html()

    def _section_data_quality_audit(self) -> str:
        return self._components["data_quality_audit_section"].render_html()

    def _section_appendix_failure_cases(self, stats: dict) -> str:
        return self._components["failure_cases_section"].render_html()

    def _section_methodology_appendix_header(self) -> str:
        """Single ``<h2>Methodology Appendix</h2>`` that opens the §A–§D block.

        Phase 2 verification looks for exactly one such heading; the four
        appendix sections that follow use ``<h2>A. ...</h2>`` … ``<h2>D. ...</h2>``
        sub-blocks underneath.
        """
        return (
            "<h2>Methodology Appendix</h2>\n"
            "<p>The appendix sections below document how the numbers above "
            "were produced — judge qualification, judge configuration, the "
            "data-quality audit, and failure case studies. They are reference "
            "material, not part of the main narrative.</p>"
        )

    def _section_conclusion(self, stats: dict) -> str:
        """§8: Restate Claim A/B/C verdicts in evidence-based phrasing,
        plus an explicit data-quality caveat."""
        ranking = stats.get("model_ranking") or []
        best = ranking[0] if ranking else None
        d3 = stats.get("d3") or {}
        control = stats.get("control") or {}

        if best:
            scores = best.get("scores") or {}
            scores_ci = best.get("scores_ci") or {}

            def fmt(dim: str) -> str:
                m = scores.get(dim)
                ci = scores_ci.get(dim) or {}
                lo, hi = ci.get("low"), ci.get("high")
                if (
                    isinstance(m, (int, float))
                    and isinstance(lo, (int, float))
                    and isinstance(hi, (int, float))
                ):
                    return f"{dim} = {m:.2f} [{lo:.2f}, {hi:.2f}]"
                if isinstance(m, (int, float)):
                    return f"{dim} = {m:.2f}"
                return f"{dim} = n/a"

            claim_a = (
                f"<p><strong>Claim A — Stability.</strong> Top student model "
                f"<strong>{_html(best['model'])}</strong>: "
                f"{fmt('S1')}; {fmt('S3')}; {fmt('S2')}. "
                f"Mean drift onset turn = "
                f"{d3.get('drift_onset_mean', 0):.1f}.</p>"
            )
        else:
            claim_a = (
                "<p><strong>Claim A — Stability.</strong> No model ranking "
                "available; cannot restate verdict.</p>"
            )

        if best:
            invariant = self._composite_judge_invariance
            if invariant is not None:
                n_views = len(invariant.get("rankings", {}))
                ranks_match = invariant.get("rankings_identical_across_judges")
                spread = invariant.get("max_pairwise_composite_spread", 0)
                inv_phrase = (
                    f"rank order {'identical' if ranks_match else 'DIFFERS'} across "
                    f"{n_views} judge views (max composite spread {spread:.3f})"
                )
            else:
                inv_phrase = "judge-invariance not computable"
            comp_lo = best.get("composite_ci_low")
            comp_hi = best.get("composite_ci_high")
            if isinstance(comp_lo, (int, float)) and isinstance(comp_hi, (int, float)):
                comp_phrase = (
                    f"composite {best['composite']:.2f} "
                    f"[{comp_lo:.2f}, {comp_hi:.2f}]"
                )
            else:
                comp_phrase = f"composite {best['composite']:.2f}"
            claim_b = (
                f"<p><strong>Claim B — Model selection.</strong> "
                f"<strong>{_html(best['model'])}</strong> selected "
                f"({len(ranking)} candidates); {comp_phrase}; {inv_phrase}.</p>"
            )
        else:
            claim_b = (
                "<p><strong>Claim B — Model selection.</strong> No model "
                "ranking available.</p>"
            )

        metrics = self._human.get("agreement_metrics") or {}
        chosen_name = None
        chosen = None
        for name in ("persona_fidelity", "knowledge_boundary_pass", "emotional_match"):
            value = metrics.get(name)
            if value:
                chosen, chosen_name = value, name
                break
        if chosen and chosen_name:
            claim_c = (
                f"<p><strong>Claim C — Metric calibration.</strong> "
                f"Within-1-point rate on <code>{_html(chosen_name)}</code>: "
                f"<strong>{chosen.get('within_one_point_rate', 0):.1%}</strong> "
                f"(n={chosen.get('n', 0)}, MAD={chosen.get('mean_absolute_difference', 0):.2f}).</p>"
            )
        else:
            claim_c = (
                "<p><strong>Claim C — Metric calibration.</strong> "
                "Human-LLM alignment study not yet scored.</p>"
            )

        ratio = control.get("high_score_ratio")
        eff = control.get("standardized_effect_vs_baseline")
        n_ctrl = control.get("n")
        ctrl_mean = control.get("overall_mean")
        if isinstance(ratio, (int, float)) and isinstance(eff, (int, float)) and n_ctrl:
            persona_evidence_line = (
                "<p><em>Persona-vs-placebo evidence (S6 distinctiveness, n="
                f"{n_ctrl}): mean = {ctrl_mean:.2f}; high-score ratio "
                f"(≥4) = {ratio:.0%}; standardized effect vs unrecognizable "
                f"baseline = {eff:.1f}σ.</em></p>"
            )
        else:
            persona_evidence_line = ""

        audit = self._audit or {}
        checks = audit.get("checks") or []
        failing = [c for c in checks if not c.get("ok")]
        if failing:
            failed_names = ", ".join(
                f"<code>{_html(c.get('name'))}</code>" for c in failing[:6]
            )
            extra = "" if len(failing) <= 6 else f" (+{len(failing) - 6} more)"
            caveat_line = (
                f"<p><strong>Data-quality caveat:</strong> "
                f"{failed_names}{extra} are flagged as failing in Methodology Appendix &sect;C; "
                "all numbers above are computed against the artifacts as-is.</p>"
            )
        else:
            caveat_line = ""

        return f"""<h2>8. Conclusion — Claim Verdicts</h2>
<div class="conclusion">
{claim_a}
{claim_b}
{claim_c}
{persona_evidence_line}
{caveat_line}
</div>"""
