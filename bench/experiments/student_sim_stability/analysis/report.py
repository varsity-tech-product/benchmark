"""Statistical report generator for student simulator stability experiment.

Generates an HTML report with embedded matplotlib charts (base64 PNG).
"""

import base64
import html
import io
import json
import logging
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from experiments.student_sim_stability.core.config import STUDENT_MODELS
from experiments.student_sim_stability.core.rubrics import all_rubrics

logger = logging.getLogger(__name__)

# Consistent styling
_PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]
_MODEL_ORDER = [model.split("/")[-1] for model in STUDENT_MODELS]
_COLORS = {
    model: _PALETTE[idx % len(_PALETTE)] for idx, model in enumerate(_MODEL_ORDER)
}

_PRIMARY_SCORE_FIELD = {
    "D1": "overall",
    "D2": "overall_reproducibility",
    "D3": "overall_cross_model",
    "D4": "overall_drift_score",
    "control": "distinctiveness",
    "P1": "overall_probe_pass",
    "B1": "contract_fit",
}

_FAILURE_RECOMMENDATIONS = {
    "knowledge_leak": "Tighten known/unknown concept boundaries in the persona contract and add targeted boundary probes.",
    "under_competence": "Clarify minimum expected baseline knowledge and add examples of acceptable partial understanding.",
    "emotional_mismatch": "Strengthen emotional profile examples and add scripted pressure cases.",
    "generic_student_behavior": "Add more persona-specific question style and confusion style examples.",
    "co_teacher_drift": "Add explicit anti-teaching behavioral rules and D4 examples.",
    "task_forgetting": "Add task-retention reminders and redirect behavior examples.",
    "persona_contract_contradiction": "Resolve inconsistent contract clauses or add explicit precedence rules.",
}


def _safe_mean(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float))]
    return float(np.mean(nums)) if nums else 0.0


def _safe_std(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float))]
    return float(np.std(nums)) if len(nums) > 1 else 0.0


def _fmt_score(score: object) -> str:
    return f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _embed_img(b64: str, alt: str = "") -> str:
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;margin:10px 0;">'


def _html(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _color_for(model: str) -> str:
    short = model.split("/")[-1] if "/" in model else model
    return _COLORS.get(short, "#999999")


def _count_votes(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1
    return dict(counts)


def _wrap_label(label: str, width: int = 14) -> str:
    cleaned = str(label).replace("_", " ")
    return "\n".join(textwrap.wrap(cleaned, width=width)) or cleaned


class ReportGenerator:
    """Generate statistical report from evaluation results."""

    def __init__(self, eval_path: str, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir = self.output_dir.parent
        with open(eval_path) as f:
            self.raw = json.load(f)
        self.rubrics = {rubric["dimension"]: rubric for rubric in all_rubrics()}

    def generate(self) -> str:
        """Generate full report. Returns path to output HTML."""
        stats = self._compute_all_stats()
        sections = [
            self._header(),
            self._section_overview(stats),
            self._section_controlled_validation(stats),
            self._section_d1(stats),
            self._section_d2(stats),
            self._section_d3(stats),
            self._section_d4(stats),
            self._section_temperature_ablation(stats),
            self._section_control(stats),
            self._section_human_alignment(),
            self._section_judge_configuration(),
            self._section_data_quality_audit(),
            self._section_failure_taxonomy(stats),
            self._section_conclusion(stats),
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

        logger.info("Report saved to %s", path)
        return str(path)

    # ----- Stats computation -----

    def _compute_all_stats(self) -> dict:
        return {
            "d1": self._aggregate_d1(),
            "d2": self._aggregate_d2(),
            "d3": self._aggregate_d3(),
            "d4": self._aggregate_d4(),
            "control": self._aggregate_control(),
            "p1": self._aggregate_p1(),
            "b1": self._aggregate_b1(),
            "model_ranking": self._compute_model_ranking(),
            "failure_taxonomy": self._aggregate_failure_taxonomy(),
        }

    def _aggregate_d1(self) -> dict:
        results = self.raw.get("D1", [])
        by_model: dict[str, list] = defaultdict(list)
        by_persona: dict[str, list] = defaultdict(list)
        by_task: dict[str, list] = defaultdict(list)
        by_model_persona: dict[str, list] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            persona = m.get("persona_id", "")
            task = m.get("task_id", "")
            by_model[model].append(score)
            by_persona[persona].append(score)
            by_task[task].append(score)
            by_model_persona[f"{model}__{persona}"].append(score)

        return {
            "by_model": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model.items()
            },
            "by_persona": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_persona.items()
            },
            "by_task": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_task.items()
            },
            "by_model_persona": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model_persona.items()
            },
        }

    def _aggregate_d2(self) -> dict:
        results = self.raw.get("D2", [])
        by_model: dict[str, list] = defaultdict(list)
        by_model_temp: dict[str, list] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall_reproducibility", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            tutor_t = m.get("tutor_temperature", "?")
            by_model[model].append(score)
            by_model_temp[f"{model}__{tutor_t}"].append(score)

        return {
            "by_model": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model.items()
            },
            "by_model_temp": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model_temp.items()
            },
        }

    def _aggregate_d3(self) -> dict:
        results = self.raw.get("D3", [])
        by_task: dict[str, list] = defaultdict(list)
        best_models: list[str] = []
        worst_models: list[str] = []
        all_scores: list[dict] = []

        for r in results:
            score = r["scores"].get("overall_cross_model", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            by_task[m.get("task_id", "")].append(score)
            if m.get("best_model"):
                best_models.append(m["best_model"])
            if m.get("worst_model"):
                worst_models.append(m["worst_model"])
            all_scores.append(r["scores"])

        return {
            "by_task": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_task.items()
            },
            "best_model_votes": _count_votes(best_models),
            "worst_model_votes": _count_votes(worst_models),
            "all_scores": all_scores,
        }

    def _aggregate_d4(self) -> dict:
        results = self.raw.get("D4", [])
        by_model: dict[str, list] = defaultdict(list)
        drift_onsets: list = []
        all_curves: dict[str, list[list]] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall_drift_score", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            by_model[model].append(score)
            onset = m.get("drift_onset_turn", r["scores"].get("drift_onset_turn"))
            if onset is not None:
                drift_onsets.append(onset)
            fidelity = r["scores"].get("per_turn_fidelity", [])
            if fidelity:
                all_curves[model].append(fidelity)

        avg_curves = {}
        for model, curves in all_curves.items():
            if curves:
                max_len = max(len(c) for c in curves)
                padded = [c + [c[-1]] * (max_len - len(c)) for c in curves]
                avg_curves[model] = [
                    float(np.mean([p[i] for p in padded])) for i in range(max_len)
                ]

        return {
            "by_model": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model.items()
            },
            "drift_onset_mean": _safe_mean(drift_onsets),
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
        d4_numeric = {
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
                score_field = _PRIMARY_SCORE_FIELD.get(dimension)
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
                if dimension == "D4":
                    d4_numeric["knowledge_leak_events"] += sum(
                        1
                        for value in scores.get("per_turn_knowledge_leak", [])
                        if value
                    )
                    d4_numeric["co_teacher_drift_events"] += sum(
                        1
                        for value in scores.get("per_turn_co_teacher_drift", [])
                        if value
                    )

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
                    "mean": _safe_mean(values),
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
                key: _FAILURE_RECOMMENDATIONS.get(
                    key, "Review the matching persona contract and judge evidence."
                )
                for key in by_type
            },
            "d4_numeric": d4_numeric,
        }

    def _aggregate_control(self) -> dict:
        results = self.raw.get("control", [])
        by_persona: dict[str, list] = defaultdict(list)
        for r in results:
            pid = r["metadata"].get("persona_id", "")
            s = r["scores"].get("distinctiveness", 0)
            if s > 0:
                by_persona[pid].append(s)
        all_scores = [s for v in by_persona.values() for s in v]
        return {
            "overall_mean": _safe_mean(all_scores),
            "overall_std": _safe_std(all_scores),
            "by_persona": {
                k: {"mean": _safe_mean(v), "n": len(v)} for k, v in by_persona.items()
            },
        }

    def _aggregate_p1(self) -> dict:
        results = self.raw.get("P1", [])
        by_persona: dict[str, list] = defaultdict(list)
        by_facet: dict[str, list] = defaultdict(list)
        for r in results:
            score = r.get("scores", {}).get("overall_probe_pass", 0)
            if score <= 0:
                continue
            metadata = r.get("metadata", {})
            by_persona[metadata.get("persona_id", "")].append(score)
            by_facet[metadata.get("facet", "")].append(score)
        all_scores = [score for values in by_persona.values() for score in values]
        return {
            "overall_mean": _safe_mean(all_scores),
            "n": len(all_scores),
            "by_persona": {
                key: {"mean": _safe_mean(values), "n": len(values)}
                for key, values in by_persona.items()
            },
            "by_facet": {
                key: {"mean": _safe_mean(values), "n": len(values)}
                for key, values in by_facet.items()
            },
        }

    def _aggregate_b1(self) -> dict:
        results = self.raw.get("B1", [])
        correct = 0
        compared = 0
        confidences = []
        for r in results:
            scores = r.get("scores", {})
            metadata = r.get("metadata", {})
            identified = str(scores.get("identified_persona", "")).strip()
            expected = str(metadata.get("persona_id", "")).strip()
            if identified and expected:
                compared += 1
                correct += int(identified == expected)
            confidence = scores.get("confidence")
            if isinstance(confidence, (int, float)):
                confidences.append(confidence)
        return {
            "accuracy": correct / compared if compared else 0.0,
            "n": compared,
            "mean_confidence": _safe_mean(confidences),
        }

    def _compute_model_ranking(self) -> list[dict]:
        d1 = self._aggregate_d1()["by_model"]
        d2 = self._aggregate_d2()["by_model"]
        d4 = self._aggregate_d4()["by_model"]
        models = set(d1.keys()) | set(d2.keys()) | set(d4.keys())
        rankings = []
        for m in models:
            scores = {
                "D1": d1.get(m, {}).get("mean"),
                "D2": d2.get(m, {}).get("mean"),
                "D4": d4.get(m, {}).get("mean"),
            }
            available_dimensions = [
                dimension
                for dimension, score in scores.items()
                if isinstance(score, (int, float))
            ]
            composite = _safe_mean(list(scores.values()))
            rankings.append(
                {
                    "model": m,
                    "scores": scores,
                    "composite": composite,
                    "available_dimensions": available_dimensions,
                }
            )
        rankings.sort(key=lambda x: x["composite"], reverse=True)
        return rankings

    # ----- Chart generators -----

    def _chart_overview_radar(self, stats: dict) -> str:
        """Radar chart: D1/D2/D4 per model."""
        ranking = [
            r
            for r in stats["model_ranking"]
            if all(
                isinstance(r["scores"].get(dim), (int, float))
                for dim in ["D1", "D2", "D4"]
            )
        ]
        if not ranking:
            return ""
        categories = ["D1\nAdherence", "D2\nReproducibility", "D4\nAnti-drift"]
        n = len(categories)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
        for r in ranking:
            values = [r["scores"]["D1"], r["scores"]["D2"], r["scores"]["D4"]]
            values += values[:1]
            color = _color_for(r["model"])
            ax.plot(angles, values, "o-", linewidth=2, label=r["model"], color=color)
            ax.fill(angles, values, alpha=0.1, color=color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, size=11)
        ax.set_ylim(0, 5)
        ax.set_yticks([1, 2, 3, 4, 5])
        ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)
        ax.set_title("Model Stability Profile", size=14, pad=20)
        return _embed_img(_fig_to_base64(fig), "Model radar chart")

    def _chart_d1_heatmap(self, stats: dict) -> str:
        """Heatmap: model × persona D1 scores."""
        d1 = stats["d1"]
        models = sorted(set(k.split("__")[0] for k in d1["by_model_persona"]))
        personas = sorted(set(k.split("__")[1] for k in d1["by_model_persona"]))
        if not models or not personas:
            return ""

        data = np.zeros((len(models), len(personas)))
        for i, m in enumerate(models):
            for j, p in enumerate(personas):
                data[i, j] = d1["by_model_persona"].get(f"{m}__{p}", {}).get("mean", 0)

        fig, ax = plt.subplots(
            figsize=(max(6, len(personas) * 1.8), max(3, len(models) * 0.8))
        )
        im = ax.imshow(data, cmap="RdYlGn", vmin=1, vmax=5, aspect="auto")
        ax.set_xticks(range(len(personas)))
        ax.set_xticklabels(personas, rotation=30, ha="right", fontsize=9)
        ax.set_yticks(range(len(models)))
        ax.set_yticklabels(models, fontsize=10)
        for i in range(len(models)):
            for j in range(len(personas)):
                ax.text(
                    j,
                    i,
                    f"{data[i, j]:.2f}",
                    ha="center",
                    va="center",
                    fontsize=11,
                    fontweight="bold",
                )
        fig.colorbar(im, ax=ax, label="D1 Score (1-5)", shrink=0.8)
        ax.set_title("D1 Persona Adherence: Model × Persona", size=13, pad=10)
        return _embed_img(_fig_to_base64(fig), "D1 heatmap")

    def _chart_d2_bars(self, stats: dict) -> str:
        """Grouped bar chart: D2 by model, colored by tutor temperature."""
        d2 = stats["d2"]["by_model_temp"]
        if not d2:
            return ""

        models = [m for m in _MODEL_ORDER if any(m in k for k in d2)]
        temps = sorted(set(k.split("__")[1] for k in d2))

        fig, ax = plt.subplots(figsize=(8, 4.5))
        x = np.arange(len(models))
        width = 0.35
        for i, t in enumerate(temps):
            vals = [d2.get(f"{m}__{t}", {}).get("mean", 0) for m in models]
            stds = [d2.get(f"{m}__{t}", {}).get("std", 0) for m in models]
            offset = (i - (len(temps) - 1) / 2) * width
            bars = ax.bar(
                x + offset,
                vals,
                width * 0.9,
                yerr=stds,
                label=f"Tutor {t}",
                capsize=3,
                alpha=0.85,
            )
            for bar, val in zip(bars, vals):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.08,
                        f"{val:.2f}",
                        ha="center",
                        va="bottom",
                        fontsize=9,
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(models, fontsize=10)
        ax.set_ylim(0, 5.5)
        ax.set_ylabel("D2 Reproducibility Score", fontsize=11)
        ax.set_title("D2 Cross-run Reproducibility × Tutor Temperature", size=13)
        ax.legend()
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3, label="_nolegend_")
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3, label="_nolegend_")
        return _embed_img(_fig_to_base64(fig), "D2 bar chart")

    def _chart_d4_curves(self, stats: dict) -> str:
        """Line chart: per-turn fidelity curves."""
        curves = stats["d4"].get("avg_fidelity_curves", {})
        if not curves:
            return ""

        fig, ax = plt.subplots(figsize=(8, 4.5))
        for model in _MODEL_ORDER:
            if model in curves:
                y = curves[model]
                x = list(range(1, len(y) + 1))
                ax.plot(
                    x,
                    y,
                    "o-",
                    linewidth=2,
                    markersize=6,
                    label=model,
                    color=_color_for(model),
                )

        ax.set_xlabel("Turn", fontsize=11)
        ax.set_ylabel("Persona Fidelity (1-5)", fontsize=11)
        ax.set_ylim(0.5, 5.5)
        ax.set_title("D4 Per-turn Persona Fidelity", size=13)
        ax.legend(fontsize=9)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3)
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3)
        ax.grid(axis="y", alpha=0.3)
        return _embed_img(_fig_to_base64(fig), "D4 drift curves")

    def _chart_control_bars(self, stats: dict) -> str:
        """Bar chart: persona distinctiveness."""
        ctrl = stats["control"]["by_persona"]
        if not ctrl:
            return ""
        personas = sorted(ctrl.keys())
        vals = [ctrl[p]["mean"] for p in personas]

        fig_width = max(7.0, len(personas) * 1.7)
        fig, ax = plt.subplots(figsize=(fig_width, 4.6))
        bars = ax.bar(
            personas,
            vals,
            color=["#3498db", "#e67e22", "#2ecc71", "#9b59b6"][: len(personas)],
            alpha=0.85,
        )
        for bar, val in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.08,
                f"{val:.2f}",
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
            )
        ax.set_ylim(0, 5.5)
        ax.set_ylabel("Distinctiveness (1-5)", fontsize=11)
        ax.set_title("Control: Persona vs No-Persona Distinctiveness", size=13)
        ax.set_xticks(range(len(personas)))
        ax.set_xticklabels([_wrap_label(p) for p in personas], fontsize=9)
        ax.tick_params(axis="x", pad=8)
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3)
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3)
        fig.subplots_adjust(bottom=0.24)
        return _embed_img(_fig_to_base64(fig), "Control distinctiveness")

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
  .example-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin: 15px 0; }}
  .example-card {{ border: 1px solid #d8dee9; border-left: 5px solid #e67e22; border-radius: 8px; padding: 13px 14px; background: #fff; }}
  .example-card.priority {{ border-left-color: #c0392b; background: #fffafa; }}
  .example-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: start; margin-bottom: 8px; }}
  .example-title {{ font-weight: 700; color: #16213e; overflow-wrap: anywhere; }}
  .example-meta {{ color: #666; font-size: 0.88em; line-height: 1.45; margin: 4px 0 8px; overflow-wrap: anywhere; }}
  .example-evidence {{ color: #222; font-size: 0.95em; line-height: 1.5; overflow-wrap: anywhere; }}
  .failure-pill {{ display: inline-block; background: #f6e7db; color: #8a3b12; border-radius: 999px; padding: 2px 9px; font-size: 0.82em; font-weight: 700; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.85em; font-weight: 600; }}
  .badge-gold {{ background: #ffd700; color: #333; }}
  .badge-silver {{ background: #c0c0c0; color: #333; }}
  .badge-bronze {{ background: #cd7f32; color: white; }}
  .conclusion {{ background: #eef5ff; border: 1px solid #b3d4fc; border-radius: 8px; padding: 20px; margin: 20px 0; }}
</style></head><body>
<h1>Student Simulator Stability Report</h1>
<p><em>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>
"""

    def _footer(self) -> str:
        return "</body></html>"

    def _sc(self, score: float | None) -> str:
        if not isinstance(score, (int, float)):
            return ""
        if score >= 4.0:
            return "high"
        if score >= 3.0:
            return "mid"
        return "low"

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
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _status_block(self) -> str:
        def load(path: Path) -> dict:
            if not path.exists():
                return {}
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)

        metadata = load(self.results_dir / "report" / "stability_metadata.json")
        judge = load(self.results_dir / "report" / "judge_agreement.json")
        human = load(self.results_dir / "human_alignment" / "agreement_report.json")
        comparison = metadata.get(
            "model_comparison_label", "cross-vendor candidate selection"
        )
        return f"""<div class="insight">
<strong>Validation flow:</strong> persona contract → generated conversation → rendered judge prompt/context → judge JSON score → aggregate metric → chart → interpretation.<br>
<strong>Tutor/student/judge:</strong> tutor stimulus = {metadata.get('tutor_model', 'unknown')}; student candidates = {', '.join(metadata.get('student_models', []))}; primary judge = {metadata.get('primary_judge', 'unknown')} at temperature {metadata.get('judge_temperature', 'unknown')}.<br>
<strong>Human alignment status:</strong> {human.get('human_alignment_status', 'not_run')}. <strong>Multi-judge status:</strong> {judge.get('multi_judge_status', 'not_run')}.<br>
<strong>Model comparison policy:</strong> {comparison}; rankings are candidate-selection signals, not same-level parameter-matched claims.
</div>"""

    def _section_overview(self, stats: dict) -> str:
        ranking = stats["model_ranking"]
        total_evals = sum(
            len(self.raw.get(k, []))
            for k in ["D1", "D2", "D3", "D4", "control", "P1", "B1"]
        )

        composite_block = """<div class="rubric">
<strong>Composite metric policy:</strong> cross-vendor candidate selection, not parameter-matched same-level ranking.<br>
<strong>Context:</strong> D1 persona adherence, D2 cross-run reproducibility, and D4 anti-drift scores are aggregated per student model.<br>
<strong>Aggregation:</strong> Composite = mean of available D1, D2, and D4 scores. D3 is excluded because it is a group comparison over anonymized systems.
</div>"""

        cards = f"""<h2>1. Overview</h2>
{self._status_block()}
{composite_block}
<div class="card-row">
  <div class="card"><div class="val">{total_evals}</div><div class="lbl">Total Evaluations</div></div>
  <div class="card"><div class="val">{len(ranking)}</div><div class="lbl">Models Tested</div></div>
  <div class="card"><div class="val">{len(self.raw.get('control', []))}</div><div class="lbl">Control Comparisons</div></div>
</div>"""

        radar = self._chart_overview_radar(stats)

        # Ranking table
        rows = ""
        badges = ["badge-gold", "badge-silver", "badge-bronze"]
        for i, r in enumerate(ranking):
            badge = (
                f'<span class="badge {badges[i]}">{["1st","2nd","3rd"][i]}</span>'
                if i < 3
                else f"{i+1}th"
            )
            rows += f"""<tr><td>{badge}</td><td><strong>{r['model']}</strong></td>
  <td class="{self._sc(r['scores']['D1'])}">{_fmt_score(r['scores']['D1'])}</td>
  <td class="{self._sc(r['scores']['D2'])}">{_fmt_score(r['scores']['D2'])}</td>
  <td class="{self._sc(r['scores']['D4'])}">{_fmt_score(r['scores']['D4'])}</td>
  <td class="{self._sc(r['composite'])}">{r['composite']:.2f}</td></tr>"""

        return (
            cards
            + radar
            + f"""
<h3>Cross-vendor Candidate Selection Ranking</h3>
	<table><tr><th>Rank</th><th>Model</th><th>D1</th><th>D2</th><th>D4</th><th>Composite</th></tr>
	{rows}</table>
	<p><em>Composite = mean of available D1, D2, and D4 scores. D3 is a group metric, not per-model.</em></p>"""
        )

    def _section_controlled_validation(self, stats: dict) -> str:
        p1 = stats["p1"]
        b1 = stats["b1"]
        return f"""<h2>2. Controlled Persona Validation</h2>
<p>Validation flow: persona contract → targeted probes → scripted dialogues → blind persona identification → live tutor robustness.</p>
{self._rubric_block("P1")}
{self._rubric_block("B1")}
<table><tr><th>Check</th><th>Metric</th><th>N</th></tr>
<tr><td>P1 targeted probes</td><td class="{self._sc(p1.get('overall_mean', 0))}">{p1.get('overall_mean', 0):.2f}</td><td>{p1.get('n', 0)}</td></tr>
<tr><td>B1 blind persona identification</td><td>{b1.get('accuracy', 0):.1%} accuracy; confidence {b1.get('mean_confidence', 0):.2f}</td><td>{b1.get('n', 0)}</td></tr>
</table>
<div class="insight">
<strong>Action rule:</strong> If P1 falls below 3.0, revise persona contracts before live runs.
If B1 accuracy is weak, add more distinctive behavioral rules and scripted pressure cases.
</div>"""

    def _section_d1(self, stats: dict) -> str:
        heatmap = self._chart_d1_heatmap(stats)
        return f"""<h2>3. D1 — Persona Adherence</h2>
{self._rubric_block("D1")}
{heatmap}
<div class="insight">
<strong>Insight guide:</strong> Low cells identify a persona/task/model combination where generated
student turns do not visibly match the contract. First check knowledge-boundary and emotional-tone
subscores, then tighten the copied persona contract rather than changing shared source personas.
</div>"""

    def _section_d2(self, stats: dict) -> str:
        chart = self._chart_d2_bars(stats)
        return f"""<h2>4. D2 — Cross-run Reproducibility</h2>
{self._rubric_block("D2")}
{chart}
<div class="insight">
<strong>Ceiling effect note:</strong> At tutor t=0, both student and tutor are near-deterministic,
so high D2 scores are expected by design. The meaningful comparison is t=0 vs t=1:
a small gap means student stability is genuinely robust, not just an artifact of determinism.
</div>"""

    def _section_d3(self, stats: dict) -> str:
        d3 = stats["d3"]
        best = d3.get("best_model_votes", {})
        worst = d3.get("worst_model_votes", {})
        all_models = sorted(set(best.keys()) | set(worst.keys()))
        vote_rows = "".join(
            f"<tr><td>{m}</td><td>{best.get(m, 0)}</td><td>{worst.get(m, 0)}</td></tr>"
            for m in all_models
        )
        task_rows = "".join(
            f'<tr><td>{t}</td><td class="{self._sc(d["mean"])}">{d["mean"]:.2f}</td><td>{d["n"]}</td></tr>'
            for t, d in sorted(d3["by_task"].items())
        )
        return f"""<h2>5. D3 — Cross-model Consistency</h2>
{self._rubric_block("D3")}
<h3>By Task</h3>
<table><tr><th>Task</th><th>Mean Score</th><th>N</th></tr>{task_rows}</table>
<h3>Best/Worst Model Votes (judge assessment)</h3>
<table><tr><th>Model</th><th>Best Votes</th><th>Worst Votes</th></tr>{vote_rows}</table>
<div class="insight">
<strong>Action rule:</strong> Low D3 means the persona contract is underspecified across model families.
Avoid declaring a same-level model ranking; simplify or clarify the persona rules and rerun D1/D4.
</div>"""

    def _section_d4(self, stats: dict) -> str:
        chart = self._chart_d4_curves(stats)
        d4 = stats["d4"]
        onset = d4.get("drift_onset_mean", 0)
        onset_text = f"{onset:.1f}" if onset else "N/A"

        model_rows = "".join(
            f'<tr><td>{m}</td><td class="{self._sc(d["mean"])}">{d["mean"]:.2f}</td><td>{d["std"]:.2f}</td><td>{d["n"]}</td></tr>'
            for m, d in sorted(d4["by_model"].items())
        )
        return f"""<h2>6. D4 — Drift Detection</h2>
{self._rubric_block("D4")}
{chart}
<table><tr><th>Model</th><th>Mean Drift Score</th><th>Std</th><th>N</th></tr>{model_rows}</table>
<div class="insight"><strong>Average drift onset turn:</strong> {onset_text} (later = better)</div>"""

    def _section_temperature_ablation(self, stats: dict) -> str:
        d2 = stats["d2"]["by_model_temp"]
        if not d2:
            return ""
        rows = ""
        for key in sorted(d2.keys()):
            parts = key.split("__")
            model, temp = parts[0], parts[1] if len(parts) > 1 else "?"
            d = d2[key]
            rows += f'<tr><td>{model}</td><td>{temp}</td><td class="{self._sc(d["mean"])}">{d["mean"]:.2f}</td><td>{d["std"]:.2f}</td><td>{d["n"]}</td></tr>'

        return f"""<h2>7. Temperature Ablation</h2>
{self._rubric_block("D2")}
<p>Context: the same D2 reproducibility rubric is grouped by tutor temperature to separate deterministic tutor effects from true student-simulator stability.</p>
<table><tr><th>Model</th><th>Tutor Temp</th><th>D2 Score</th><th>Std</th><th>N</th></tr>{rows}</table>
<div class="insight">
<strong>Interpretation:</strong> If D2 scores are similar across t=0 and t=1 for the same model,
the student simulator is robust to tutor variance — its persona behavior is driven by the prompt,
not by what the tutor says.
</div>"""

    def _section_control(self, stats: dict) -> str:
        chart = self._chart_control_bars(stats)
        overall = stats["control"].get("overall_mean", 0)
        return f"""<h2>8. Control — Persona Distinguishability</h2>
{self._rubric_block("control")}
{chart}
<div class="card-row"><div class="card"><div class="val {self._sc(overall)}">{overall:.2f}</div>
<div class="lbl">Overall Distinctiveness (1-5)</div></div></div>
<div class="insight">
<strong>Action rule:</strong> If control distinctiveness is below 4.0, persona conditioning is not adding
enough observable behavior over a generic student; strengthen question style, confusion style, and emotional profile examples.
</div>"""

    def _section_human_alignment(self) -> str:
        human = self._load_json_file(
            self.results_dir / "human_alignment" / "agreement_report.json"
        )
        metrics = human.get("agreement_metrics") or {}
        metric_rows = ""
        for name, value in metrics.items():
            if not value:
                continue
            if "mean_absolute_difference" in value:
                metric = (
                    f"MAD {value.get('mean_absolute_difference', 0):.2f}; "
                    f"within one point {value.get('within_one_point_rate', 0):.1%}"
                )
            else:
                metric = f"match {value.get('exact_or_contained_match_rate', 0):.1%}"
            metric_rows += (
                f"<tr><td>{name}</td><td>{metric}</td><td>{value.get('n', 0)}</td></tr>"
            )
        if not metric_rows:
            metric_rows = (
                "<tr><td colspan='3'>No human labels have been scored yet.</td></tr>"
            )
        examples = "".join(
            f"<li><code>{item.get('eval_id')}</code> ({item.get('category', 'label')}): human={item.get('human_score')}, judge={item.get('judge_score')}, abs diff={item.get('abs_diff')}</li>"
            for item in human.get("disagreement_examples", [])[:5]
        )
        examples = examples or "<li>No disagreement examples available.</li>"
        return f"""<h2>9. Human Alignment</h2>
<div class="rubric">
<strong>Definition:</strong> Human quant-expert calibration compares sampled judge inputs against human labels.<br>
<strong>Context:</strong> sample manifest, human label CSV, same-sample LLM judge label snapshot, aggregate judge scores, and disagreement notes.<br>
<strong>Fields:</strong> persona_fidelity, knowledge_boundary_pass, emotional_match, drift_onset_turn, failure_type.<br>
<strong>Aggregation:</strong> numeric labels use mean absolute difference and within-one-point rate; failure type uses exact/contained match rate.
</div>
<p>Status: <code>{human.get('human_alignment_status', 'not_run')}</code></p>
<table><tr><th>Label Field</th><th>Agreement</th><th>N</th></tr>{metric_rows}</table>
<h3>Disagreement Examples</h3><ul>{examples}</ul>"""

    def _section_judge_configuration(self) -> str:
        metadata = self._load_json_file(
            self.results_dir / "report" / "stability_metadata.json"
        )
        agreement = self._load_json_file(
            self.results_dir / "report" / "judge_agreement.json"
        )
        by_dimension = (agreement.get("agreement_metrics") or {}).get(
            "by_dimension"
        ) or {}
        rows = "".join(
            f"<tr><td>{dim}</td><td>{item.get('n', 0)}</td><td>{item.get('mean_score_range', 0):.2f}</td><td>{item.get('within_one_point_rate', 0):.1%}</td></tr>"
            for dim, item in sorted(by_dimension.items())
        )
        if not rows:
            rows = "<tr><td colspan='4'>Multi-judge agreement has not been computed.</td></tr>"
        return f"""<h2>10. Judge Configuration</h2>
<div class="rubric">
<strong>Definition:</strong> Judge reliability status records the primary judge and optional judge panel agreement.<br>
<strong>Context:</strong> primary judge outputs, by-model judge output directories, input hashes, and rubric metadata.<br>
<strong>Fields:</strong> judge_model, judge_temperature, input_sha256, rubric_id, rubric_version.<br>
<strong>Aggregation:</strong> agreement uses score range, score standard deviation, and within-one-point rate across judge models.
</div>
<p>Primary judge: <code>{metadata.get('primary_judge', 'unknown')}</code>; panel: <code>{', '.join(metadata.get('judge_models', []))}</code>; status: <code>{agreement.get('multi_judge_status', 'not_run')}</code>.</p>
<table><tr><th>Dimension</th><th>N</th><th>Mean Range</th><th>Within 1 pt</th></tr>{rows}</table>"""

    def _section_data_quality_audit(self) -> str:
        audit = self._load_json_file(
            self.results_dir / "report" / "data_quality_audit.json"
        )
        checks = audit.get("checks") or []
        row_parts = []
        for item in checks[:20]:
            ok = bool(item.get("ok"))
            status = "pass" if ok else "fail"
            row_parts.append(
                "<tr>"
                f'<td><span class="status-pill status-{status}">{status}</span></td>'
                f"<td><code>{_html(item.get('name'))}</code></td>"
                f"<td>{_html(item.get('message'))}</td>"
                "</tr>"
            )
        rows = "".join(row_parts)
        if not rows:
            rows = "<tr><td colspan='3'>Audit artifact not available.</td></tr>"
        return f"""<h2>11. Data Quality Audit</h2>
<div class="rubric">
<strong>Definition:</strong> Data quality audit checks whether artifacts satisfy the no-fallback issue83 contract.<br>
<strong>Context:</strong> conversations, judge inputs, judge outputs, snapshots, human status, model metadata, and report artifacts.<br>
<strong>Fields:</strong> check name, pass/fail status, diagnostic message.<br>
<strong>Aggregation:</strong> run passes only when every required validation check passes.
</div>
<p>Overall audit status: <code>{'pass' if audit.get('ok') else 'fail_or_not_run'}</code></p>
<div class="table-wrap"><table class="audit-table"><tr><th>Status</th><th>Check</th><th>Message</th></tr>{rows}</table></div>"""

    def _failure_example_cards(self, examples: list[dict]) -> str:
        if not examples:
            return "<p>No failure examples available.</p>"

        cards = []
        for rank, item in enumerate(examples[:6], start=1):
            severity = item.get("severity")
            severity_text = (
                _fmt_score(severity) if isinstance(severity, (int, float)) else "n/a"
            )
            priority = " priority" if rank <= 3 else ""
            title = (
                item.get("failure_type")
                or item.get("dominant_failure_type")
                or "failure"
            )
            evidence = item.get("failure_evidence") or "No judge evidence provided."
            cards.append(
                f'<div class="example-card{priority}">'
                '<div class="example-head">'
                f'<div class="example-title">#{rank} {_html(item.get("eval_id"))}</div>'
                f'<span class="failure-pill">{_html(title)} | severity {severity_text}</span>'
                "</div>"
                '<div class="example-meta">'
                f'Dimension: <strong>{_html(item.get("dimension"))}</strong> · '
                f'Model: <strong>{_html(item.get("model"))}</strong><br>'
                f'Persona: <strong>{_html(item.get("persona_id"))}</strong> · '
                f'Task: <strong>{_html(item.get("task_id"))}</strong>'
                "</div>"
                f'<div class="example-evidence">{_html(evidence)}</div>'
                "</div>"
            )
        return '<div class="example-grid">' + "".join(cards) + "</div>"

    def _section_failure_taxonomy(self, stats: dict) -> str:
        taxonomy = stats["failure_taxonomy"]
        by_type = taxonomy.get("by_type", {})
        rows = "".join(
            f"<tr><td>{failure_type}</td><td>{count}</td><td>{taxonomy.get('severity', {}).get(failure_type, {}).get('mean', 0):.2f}</td><td>{taxonomy.get('recommendations', {}).get(failure_type, '')}</td></tr>"
            for failure_type, count in sorted(by_type.items())
        )
        if not rows:
            rows = "<tr><td colspan='4'>No emitted failure taxonomy labels.</td></tr>"
        model_rows = "".join(
            f"<tr><td>{model}</td><td>{', '.join(f'{k}: {v}' for k, v in failures.items())}</td></tr>"
            for model, failures in sorted(taxonomy.get("by_model", {}).items())
        )
        persona_rows = "".join(
            f"<tr><td>{persona}</td><td>{', '.join(f'{k}: {v}' for k, v in failures.items())}</td></tr>"
            for persona, failures in sorted(taxonomy.get("by_persona", {}).items())
        )
        task_rows = "".join(
            f"<tr><td>{task}</td><td>{', '.join(f'{k}: {v}' for k, v in failures.items())}</td></tr>"
            for task, failures in sorted(taxonomy.get("by_task", {}).items())
        )
        examples = self._failure_example_cards(taxonomy.get("top_examples", []))
        numeric = taxonomy.get("d4_numeric", {})
        return f"""<h2>12. Failure Taxonomy</h2>
<div class="rubric">
<strong>Definition:</strong> Failure taxonomy explains how persona behavior fails across generated outputs.<br>
<strong>Context:</strong> judge failure_types, dominant_failure_type, failure_evidence, metadata persona/task/model/phase/rubric, and D4 per-turn numeric fields.<br>
<strong>Fields:</strong> knowledge_leak, under_competence, emotional_mismatch, generic_student_behavior, co_teacher_drift, task_forgetting, persona_contract_contradiction.<br>
<strong>Aggregation:</strong> counts by type, persona, task, model, phase, and rubric; severity is estimated as 5 minus the primary score when available.
</div>
<table><tr><th>Failure Type</th><th>Count</th><th>Mean Severity</th><th>Recommended Action</th></tr>{rows}</table>
<h3>Dominant by Model</h3><table><tr><th>Model</th><th>Failures</th></tr>{model_rows or "<tr><td colspan='2'>No model-level failure labels.</td></tr>"}</table>
<h3>Dominant by Persona</h3><table><tr><th>Persona</th><th>Failures</th></tr>{persona_rows or "<tr><td colspan='2'>No persona-level failure labels.</td></tr>"}</table>
<h3>Dominant by Task</h3><table><tr><th>Task</th><th>Failures</th></tr>{task_rows or "<tr><td colspan='2'>No task-level failure labels.</td></tr>"}</table>
<h3>Top Examples</h3>{examples}
<div class="insight">
<strong>D4 numeric events:</strong> knowledge leak = {numeric.get('knowledge_leak_events', 0)}, co-teacher drift = {numeric.get('co_teacher_drift_events', 0)}.
</div>"""

    def _section_conclusion(self, stats: dict) -> str:
        ranking = stats["model_ranking"]
        parts = []
        if ranking:
            best = ranking[0]
            parts.append(
                f'<p><strong>Most stable model: {best["model"]}</strong> (composite: {best["composite"]:.2f}/5.0)</p>'
            )

        d4 = stats["d4"]
        onset = d4.get("drift_onset_mean", 0)
        if onset:
            quality = (
                "good" if onset >= 6 else "moderate" if onset >= 4 else "concerning"
            )
            parts.append(
                f"<p><strong>Drift:</strong> onset at turn {onset:.0f} ({quality})</p>"
            )

        ctrl = stats["control"].get("overall_mean", 0)
        if ctrl:
            quality = "strong" if ctrl >= 4 else "moderate" if ctrl >= 3 else "weak"
            parts.append(
                f"<p><strong>Persona value:</strong> distinctiveness = {ctrl:.2f} ({quality})</p>"
            )

        return f"""<h2>13. Conclusion</h2>
<div class="conclusion">{"".join(parts) or "<p>Insufficient data for conclusions.</p>"}</div>
<h3>Recommendations</h3>
<ul>
  <li>If D4 drift is high: add periodic persona reinforcement in runtime_guidance</li>
  <li>If D1 knowledge_boundary is low: make known/unknown concept lists more explicit</li>
  <li>If D3 cross-model is low: simplify behavioral rules to core behaviors</li>
  <li>If control distinctiveness is low: strengthen persona-specific language patterns</li>
</ul>"""
