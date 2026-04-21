"""Statistical report generator for student simulator stability experiment.

Generates an HTML report with embedded matplotlib charts (base64 PNG).
"""

import base64
import io
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

logger = logging.getLogger(__name__)

# Consistent styling
_COLORS = {
    "gpt-5.4": "#1f77b4",
    "claude-sonnet-4-6": "#ff7f0e",
    "gemini-3.1-pro-preview": "#2ca02c",
}
_MODEL_ORDER = ["gpt-5.4", "claude-sonnet-4-6", "gemini-3.1-pro-preview"]


def _safe_mean(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return float(np.mean(nums)) if nums else 0.0


def _safe_std(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return float(np.std(nums)) if len(nums) > 1 else 0.0


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _embed_img(b64: str, alt: str = "") -> str:
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;margin:10px 0;">'


def _color_for(model: str) -> str:
    short = model.split("/")[-1] if "/" in model else model
    return _COLORS.get(short, "#999999")


def _count_votes(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1
    return dict(counts)


class ReportGenerator:
    """Generate statistical report from evaluation results."""

    def __init__(self, eval_path: str, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(eval_path) as f:
            self.raw = json.load(f)

    def generate(self) -> str:
        """Generate full report. Returns path to output HTML."""
        stats = self._compute_all_stats()
        sections = [
            self._header(),
            self._section_overview(stats),
            self._section_d1(stats),
            self._section_d2(stats),
            self._section_d3(stats),
            self._section_d4(stats),
            self._section_temperature_ablation(stats),
            self._section_control(stats),
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
            "model_ranking": self._compute_model_ranking(),
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

    def _compute_model_ranking(self) -> list[dict]:
        d1 = self._aggregate_d1()["by_model"]
        d2 = self._aggregate_d2()["by_model"]
        d4 = self._aggregate_d4()["by_model"]
        models = set(d1.keys()) | set(d2.keys()) | set(d4.keys())
        rankings = []
        for m in models:
            scores = {
                "D1": d1.get(m, {}).get("mean", 0),
                "D2": d2.get(m, {}).get("mean", 0),
                "D4": d4.get(m, {}).get("mean", 0),
            }
            composite = _safe_mean(list(scores.values()))
            rankings.append({"model": m, "scores": scores, "composite": composite})
        rankings.sort(key=lambda x: x["composite"], reverse=True)
        return rankings

    # ----- Chart generators -----

    def _chart_overview_radar(self, stats: dict) -> str:
        """Radar chart: D1/D2/D4 per model."""
        ranking = stats["model_ranking"]
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

        fig, ax = plt.subplots(figsize=(6, 4))
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
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3)
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3)
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
  .high {{ color: #27ae60; font-weight: bold; }}
  .mid {{ color: #f39c12; font-weight: bold; }}
  .low {{ color: #e74c3c; font-weight: bold; }}
  .card-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 15px 0; }}
  .card {{ background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; text-align: center; }}
  .card .val {{ font-size: 2em; font-weight: bold; color: #16213e; }}
  .card .lbl {{ font-size: 0.9em; color: #666; margin-top: 5px; }}
  .insight {{ background: #f0f7ff; border-left: 4px solid #16213e; border-radius: 4px; padding: 15px; margin: 15px 0; }}
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

    def _sc(self, score: float) -> str:
        if score >= 4.0:
            return "high"
        if score >= 3.0:
            return "mid"
        return "low"

    def _section_overview(self, stats: dict) -> str:
        ranking = stats["model_ranking"]
        total_evals = sum(
            len(self.raw.get(k, [])) for k in ["D1", "D2", "D3", "D4", "control"]
        )

        cards = f"""<h2>1. Overview</h2>
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
  <td class="{self._sc(r['scores']['D1'])}">{r['scores']['D1']:.2f}</td>
  <td class="{self._sc(r['scores']['D2'])}">{r['scores']['D2']:.2f}</td>
  <td class="{self._sc(r['scores']['D4'])}">{r['scores']['D4']:.2f}</td>
  <td class="{self._sc(r['composite'])}">{r['composite']:.2f}</td></tr>"""

        return (
            cards
            + radar
            + f"""
<h3>Model Stability Ranking</h3>
<table><tr><th>Rank</th><th>Model</th><th>D1</th><th>D2</th><th>D4</th><th>Composite</th></tr>
{rows}</table>
<p><em>Composite = mean(D1, D2, D4). D3 is a group metric, not per-model.</em></p>"""
        )

    def _section_d1(self, stats: dict) -> str:
        heatmap = self._chart_d1_heatmap(stats)
        return f"""<h2>2. D1 — Persona Adherence</h2>
<p>Does each student message respect the persona's knowledge boundaries, emotional tone, and behavioral rules?</p>
{heatmap}"""

    def _section_d2(self, stats: dict) -> str:
        chart = self._chart_d2_bars(stats)
        return f"""<h2>3. D2 — Cross-run Reproducibility</h2>
<p>Same (task, persona, model) run 3 times — how consistent is the student's behavior?</p>
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
        return f"""<h2>4. D3 — Cross-model Consistency</h2>
<p>Do different LLMs produce the same persona behavior?</p>
<h3>By Task</h3>
<table><tr><th>Task</th><th>Mean Score</th><th>N</th></tr>{task_rows}</table>
<h3>Best/Worst Model Votes (judge assessment)</h3>
<table><tr><th>Model</th><th>Best Votes</th><th>Worst Votes</th></tr>{vote_rows}</table>"""

    def _section_d4(self, stats: dict) -> str:
        chart = self._chart_d4_curves(stats)
        d4 = stats["d4"]
        onset = d4.get("drift_onset_mean", 0)
        onset_text = f"{onset:.1f}" if onset else "N/A"

        model_rows = "".join(
            f'<tr><td>{m}</td><td class="{self._sc(d["mean"])}">{d["mean"]:.2f}</td><td>{d["std"]:.2f}</td><td>{d["n"]}</td></tr>'
            for m, d in sorted(d4["by_model"].items())
        )
        return f"""<h2>5. D4 — Drift Detection</h2>
<p>Does persona fidelity degrade over conversation turns?</p>
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

        return f"""<h2>6. Temperature Ablation</h2>
<p>Does tutor response diversity (temperature) affect student sim stability?</p>
<table><tr><th>Model</th><th>Tutor Temp</th><th>D2 Score</th><th>Std</th><th>N</th></tr>{rows}</table>
<div class="insight">
<strong>Interpretation:</strong> If D2 scores are similar across t=0 and t=1 for the same model,
the student simulator is robust to tutor variance — its persona behavior is driven by the prompt,
not by what the tutor says.
</div>"""

    def _section_control(self, stats: dict) -> str:
        chart = self._chart_control_bars(stats)
        overall = stats["control"].get("overall_mean", 0)
        return f"""<h2>7. Control — Persona Distinguishability</h2>
<p>Does the persona definition produce meaningfully different behavior vs a generic student?</p>
{chart}
<div class="card-row"><div class="card"><div class="val {self._sc(overall)}">{overall:.2f}</div>
<div class="lbl">Overall Distinctiveness (1-5)</div></div></div>"""

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

        return f"""<h2>8. Conclusion</h2>
<div class="conclusion">{"".join(parts) or "<p>Insufficient data for conclusions.</p>"}</div>
<h3>Recommendations</h3>
<ul>
  <li>If D4 drift is high: add periodic persona reinforcement in runtime_guidance</li>
  <li>If D1 knowledge_boundary is low: make known/unknown concept lists more explicit</li>
  <li>If D3 cross-model is low: simplify behavioral rules to core behaviors</li>
  <li>If control distinctiveness is low: strengthen persona-specific language patterns</li>
</ul>"""
