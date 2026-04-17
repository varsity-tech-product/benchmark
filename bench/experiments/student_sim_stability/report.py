"""Statistical report generator for student simulator stability experiment.

Produces:
  1. Overall dashboard — radar chart per model (D1-D4)
  2. D1 heatmap — persona adherence by (model x persona)
  3. D2 table — ICC / reproducibility by phase
  4. D3 matrix — cross-model consistency
  5. D4 curves — per-turn drift
  6. Control comparison — persona distinguishability
  7. Conclusion — model ranking + prompt improvement suggestions
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _safe_mean(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return float(np.mean(nums)) if nums else 0.0


def _safe_std(values: list) -> float:
    nums = [v for v in values if isinstance(v, (int, float)) and v > 0]
    return float(np.std(nums)) if len(nums) > 1 else 0.0


class ReportGenerator:
    """Generate statistical report from evaluation results."""

    def __init__(self, eval_path: str, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with open(eval_path) as f:
            self.raw = json.load(f)

    def generate(self) -> str:
        """Generate full report. Returns path to output HTML."""
        sections = [
            self._header(),
            self._section_overview(),
            self._section_d1(),
            self._section_d2(),
            self._section_d3(),
            self._section_d4(),
            self._section_control(),
            self._section_conclusion(),
            self._footer(),
        ]
        html = "\n".join(sections)
        path = self.output_dir / "stability_report.html"
        with open(path, "w") as f:
            f.write(html)

        # Also save raw aggregated stats as JSON
        stats = self._compute_all_stats()
        stats_path = self.output_dir / "stability_stats.json"
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        logger.info("Report saved to %s", path)
        return str(path)

    # ----- Aggregation helpers -----

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
        """Aggregate D1 scores by model, persona, task, phase."""
        results = self.raw.get("D1", [])
        by_model: dict[str, list] = defaultdict(list)
        by_persona: dict[str, list] = defaultdict(list)
        by_task: dict[str, list] = defaultdict(list)
        by_phase: dict[str, list] = defaultdict(list)
        by_model_persona: dict[str, list] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            persona = m.get("persona_id", "")
            task = m.get("task_id", "")
            phase = m.get("phase", "")

            by_model[model].append(score)
            by_persona[persona].append(score)
            by_task[task].append(score)
            by_phase[phase].append(score)
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
            "by_phase": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_phase.items()
            },
            "by_model_persona": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model_persona.items()
            },
        }

    def _aggregate_d2(self) -> dict:
        """Aggregate D2 reproducibility scores."""
        results = self.raw.get("D2", [])
        by_model: dict[str, list] = defaultdict(list)
        by_phase: dict[str, list] = defaultdict(list)
        by_model_phase: dict[str, list] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall_reproducibility", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            phase = m.get("phase", "")
            by_model[model].append(score)
            by_phase[phase].append(score)
            by_model_phase[f"{model}__{phase}"].append(score)

        return {
            "by_model": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model.items()
            },
            "by_phase": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_phase.items()
            },
            "by_model_phase": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_model_phase.items()
            },
        }

    def _aggregate_d3(self) -> dict:
        """Aggregate D3 cross-model consistency scores."""
        results = self.raw.get("D3", [])
        by_phase: dict[str, list] = defaultdict(list)
        by_task: dict[str, list] = defaultdict(list)
        best_models: list[str] = []
        worst_models: list[str] = []

        for r in results:
            score = r["scores"].get("overall_cross_model", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            phase = m.get("phase", "")
            task = m.get("task_id", "")
            by_phase[phase].append(score)
            by_task[task].append(score)
            if m.get("best_model"):
                best_models.append(m["best_model"])
            if m.get("worst_model"):
                worst_models.append(m["worst_model"])

        return {
            "by_phase": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_phase.items()
            },
            "by_task": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_task.items()
            },
            "best_model_votes": _count_votes(best_models),
            "worst_model_votes": _count_votes(worst_models),
        }

    def _aggregate_d4(self) -> dict:
        """Aggregate D4 drift detection scores."""
        results = self.raw.get("D4", [])
        by_model: dict[str, list] = defaultdict(list)
        by_phase: dict[str, list] = defaultdict(list)
        drift_onsets: list = []
        # Per-turn fidelity curves
        all_curves: dict[str, list[list]] = defaultdict(list)

        for r in results:
            score = r["scores"].get("overall_drift_score", 0)
            if score <= 0:
                continue
            m = r["metadata"]
            model = m.get("model", "").split("/")[-1]
            phase = m.get("phase", "")
            by_model[model].append(score)
            by_phase[phase].append(score)

            onset = m.get("drift_onset_turn")
            if onset is not None:
                drift_onsets.append(onset)

            fidelity = r["scores"].get("per_turn_fidelity", [])
            if fidelity:
                all_curves[model].append(fidelity)

        # Average per-turn curves per model
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
            "by_phase": {
                k: {"mean": _safe_mean(v), "std": _safe_std(v), "n": len(v)}
                for k, v in by_phase.items()
            },
            "drift_onset_mean": _safe_mean(drift_onsets),
            "avg_fidelity_curves": avg_curves,
        }

    def _aggregate_control(self) -> dict:
        """Aggregate control group distinctiveness scores."""
        results = self.raw.get("control", [])
        scores = [r["scores"].get("distinctiveness", 0) for r in results if r["scores"]]
        by_persona: dict[str, list] = defaultdict(list)
        for r in results:
            pid = r["metadata"].get("persona_id", "")
            s = r["scores"].get("distinctiveness", 0)
            if s > 0:
                by_persona[pid].append(s)

        return {
            "overall_mean": _safe_mean(scores),
            "overall_std": _safe_std(scores),
            "by_persona": {
                k: {"mean": _safe_mean(v), "n": len(v)} for k, v in by_persona.items()
            },
        }

    def _compute_model_ranking(self) -> list[dict]:
        """Rank models by composite stability score (D1-D4 average)."""
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

    # ----- HTML sections -----

    def _header(self) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Student Simulator Stability Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #333;
         line-height: 1.6; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #16213e; padding-bottom: 10px; }}
  h2 {{ color: #16213e; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
  h3 {{ color: #0f3460; }}
  table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 10px 14px; text-align: center; }}
  th {{ background: #16213e; color: white; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  .score-high {{ color: #27ae60; font-weight: bold; }}
  .score-mid {{ color: #f39c12; font-weight: bold; }}
  .score-low {{ color: #e74c3c; font-weight: bold; }}
  .metric-card {{ background: #f8f9fa; border-radius: 8px; padding: 20px;
                  margin: 10px 0; border-left: 4px solid #16213e; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
           gap: 15px; margin: 15px 0; }}
  .card {{ background: white; border: 1px solid #e0e0e0; border-radius: 8px;
           padding: 20px; text-align: center; }}
  .card .value {{ font-size: 2em; font-weight: bold; color: #16213e; }}
  .card .label {{ font-size: 0.9em; color: #666; margin-top: 5px; }}
  .conclusion {{ background: #eef5ff; border: 1px solid #b3d4fc; border-radius: 8px;
                 padding: 20px; margin: 20px 0; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
            font-size: 0.85em; font-weight: 600; }}
  .badge-gold {{ background: #ffd700; color: #333; }}
  .badge-silver {{ background: #c0c0c0; color: #333; }}
  .badge-bronze {{ background: #cd7f32; color: white; }}
</style>
</head>
<body>
<h1>Student Simulator Stability Report</h1>
<p><em>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>
"""

    def _footer(self) -> str:
        return """
</body>
</html>"""

    def _score_class(self, score: float) -> str:
        if score >= 4.0:
            return "score-high"
        elif score >= 3.0:
            return "score-mid"
        return "score-low"

    def _section_overview(self) -> str:
        stats = self._compute_all_stats()
        ranking = stats["model_ranking"]

        # Count totals
        total_d1 = len(self.raw.get("D1", []))
        total_d2 = len(self.raw.get("D2", []))
        total_d3 = len(self.raw.get("D3", []))
        total_d4 = len(self.raw.get("D4", []))
        total_ctrl = len(self.raw.get("control", []))

        cards = f"""
<h2>1. Overview Dashboard</h2>
<div class="grid">
  <div class="card"><div class="value">{total_d1 + total_d2 + total_d3 + total_d4}</div>
    <div class="label">Total Evaluations</div></div>
  <div class="card"><div class="value">{total_ctrl}</div>
    <div class="label">Control Comparisons</div></div>
  <div class="card"><div class="value">{len(ranking)}</div>
    <div class="label">Models Tested</div></div>
</div>
"""

        # Model ranking table
        rows = ""
        badges = ["badge-gold", "badge-silver", "badge-bronze"]
        for i, r in enumerate(ranking):
            badge = (
                f'<span class="badge {badges[i]}">{["1st","2nd","3rd"][i]}</span>'
                if i < 3
                else f"{i+1}th"
            )
            sc = self._score_class
            rows += f"""<tr>
  <td>{badge}</td>
  <td><strong>{r['model']}</strong></td>
  <td class="{sc(r['scores']['D1'])}">{r['scores']['D1']:.2f}</td>
  <td class="{sc(r['scores']['D2'])}">{r['scores']['D2']:.2f}</td>
  <td class="{sc(r['scores']['D4'])}">{r['scores']['D4']:.2f}</td>
  <td class="{sc(r['composite'])}">{r['composite']:.2f}</td>
</tr>"""

        return (
            cards
            + f"""
<h3>Model Stability Ranking</h3>
<table>
<tr><th>Rank</th><th>Model</th><th>D1 Adherence</th><th>D2 Reproducibility</th>
    <th>D4 Anti-drift</th><th>Composite</th></tr>
{rows}
</table>
<p><em>Composite = mean(D1, D2, D4). D3 is a group metric and not included in per-model ranking.</em></p>
"""
        )

    def _section_d1(self) -> str:
        d1 = self._aggregate_d1()
        # Model x Persona heatmap
        rows = ""
        personas = sorted(d1["by_persona"].keys())
        models = sorted(d1["by_model"].keys())

        for model in models:
            cells = f"<td><strong>{model}</strong></td>"
            for persona in personas:
                key = f"{model}__{persona}"
                val = d1["by_model_persona"].get(key, {}).get("mean", 0)
                cells += f'<td class="{self._score_class(val)}">{val:.2f}</td>'
            # Model average
            avg = d1["by_model"].get(model, {}).get("mean", 0)
            cells += (
                f'<td class="{self._score_class(avg)}"><strong>{avg:.2f}</strong></td>'
            )
            rows += f"<tr>{cells}</tr>\n"

        # Persona averages row
        pcells = "<td><em>Average</em></td>"
        for persona in personas:
            val = d1["by_persona"].get(persona, {}).get("mean", 0)
            pcells += f'<td class="{self._score_class(val)}"><em>{val:.2f}</em></td>'
        pcells += "<td></td>"
        rows += f"<tr>{pcells}</tr>"

        headers = "".join(f"<th>{p}</th>" for p in personas)

        # Phase comparison
        phase_rows = ""
        for phase, data in d1["by_phase"].items():
            sc = self._score_class(data["mean"])
            phase_rows += f'<tr><td>{phase}</td><td class="{sc}">{data["mean"]:.2f}</td><td>{data["std"]:.2f}</td><td>{data["n"]}</td></tr>'

        return f"""
<h2>2. D1 — Persona Adherence</h2>
<p>Per-message scoring: does the student simulator respect knowledge boundaries,
emotional tone, and behavioral rules?</p>

<h3>Model x Persona Heatmap</h3>
<table>
<tr><th>Model</th>{headers}<th>Avg</th></tr>
{rows}
</table>

<h3>Phase Comparison</h3>
<table>
<tr><th>Phase</th><th>Mean Score</th><th>Std Dev</th><th>N</th></tr>
{phase_rows}
</table>
"""

    def _section_d2(self) -> str:
        d2 = self._aggregate_d2()
        rows = ""
        for key, data in sorted(d2["by_model_phase"].items()):
            model, phase = key.split("__")
            sc = self._score_class(data["mean"])
            rows += f'<tr><td>{model}</td><td>{phase}</td><td class="{sc}">{data["mean"]:.2f}</td><td>{data["std"]:.2f}</td><td>{data["n"]}</td></tr>'

        return f"""
<h2>3. D2 — Cross-run Reproducibility</h2>
<p>Same (task, persona, model) run 3 times — how consistent is the student behavior?</p>

<table>
<tr><th>Model</th><th>Phase</th><th>Mean Score</th><th>Std Dev</th><th>N</th></tr>
{rows}
</table>

<div class="metric-card">
<strong>Key insight:</strong> Phase 1 (scripted tutor) should show higher
reproducibility than Phase 2 (live tutor), since the tutor input is fixed.
The gap between phases quantifies how much tutor variance propagates to
student behavior.
</div>
"""

    def _section_d3(self) -> str:
        d3 = self._aggregate_d3()
        # Task breakdown
        task_rows = ""
        for task, data in sorted(d3["by_task"].items()):
            sc = self._score_class(data["mean"])
            task_rows += f'<tr><td>{task}</td><td class="{sc}">{data["mean"]:.2f}</td><td>{data["std"]:.2f}</td><td>{data["n"]}</td></tr>'

        # Best/worst model votes
        best_votes = d3.get("best_model_votes", {})
        worst_votes = d3.get("worst_model_votes", {})
        vote_rows = ""
        all_models = set(best_votes.keys()) | set(worst_votes.keys())
        for m in sorted(all_models):
            vote_rows += f"<tr><td>{m}</td><td>{best_votes.get(m, 0)}</td><td>{worst_votes.get(m, 0)}</td></tr>"

        return f"""
<h2>4. D3 — Cross-model Consistency</h2>
<p>Do different LLMs produce the same persona behavior given identical prompts?</p>

<h3>By Task</h3>
<table>
<tr><th>Task</th><th>Mean Score</th><th>Std Dev</th><th>N</th></tr>
{task_rows}
</table>

<h3>Best/Worst Model Votes</h3>
<p>Judge's assessment of which model best/worst captures each persona:</p>
<table>
<tr><th>Model</th><th>Best Votes</th><th>Worst Votes</th></tr>
{vote_rows}
</table>
"""

    def _section_d4(self) -> str:
        d4 = self._aggregate_d4()
        # Model breakdown
        model_rows = ""
        for model, data in sorted(d4["by_model"].items()):
            sc = self._score_class(data["mean"])
            model_rows += f'<tr><td>{model}</td><td class="{sc}">{data["mean"]:.2f}</td><td>{data["std"]:.2f}</td><td>{data["n"]}</td></tr>'

        # Per-turn fidelity curves (text representation)
        curve_text = ""
        for model, curve in d4.get("avg_fidelity_curves", {}).items():
            values = " → ".join(f"{v:.1f}" for v in curve)
            first_half = _safe_mean(curve[: len(curve) // 2])
            second_half = _safe_mean(curve[len(curve) // 2 :])
            delta = second_half - first_half
            trend = "↑" if delta > 0.2 else "↓" if delta < -0.2 else "→"
            curve_text += f"<tr><td>{model}</td><td>{values}</td><td>{first_half:.2f}</td><td>{second_half:.2f}</td><td>{delta:+.2f} {trend}</td></tr>"

        onset = d4.get("drift_onset_mean", 0)
        onset_text = f"{onset:.1f}" if onset else "N/A"

        return f"""
<h2>5. D4 — Drift Detection</h2>
<p>Does persona fidelity degrade over conversation turns?</p>

<h3>Overall Drift Score by Model</h3>
<table>
<tr><th>Model</th><th>Mean Score</th><th>Std Dev</th><th>N</th></tr>
{model_rows}
</table>

<h3>Per-turn Fidelity Curves</h3>
<p>Average persona fidelity score (1-5) at each turn:</p>
<table>
<tr><th>Model</th><th>Turn-by-turn (1→8)</th><th>First Half</th><th>Second Half</th><th>Delta</th></tr>
{curve_text}
</table>

<div class="metric-card">
<strong>Average drift onset turn:</strong> {onset_text}<br>
<em>Lower values indicate earlier persona degradation.</em>
</div>
"""

    def _section_control(self) -> str:
        ctrl = self._aggregate_control()
        overall = ctrl.get("overall_mean", 0)
        sc = self._score_class(overall)

        persona_rows = ""
        for pid, data in sorted(ctrl.get("by_persona", {}).items()):
            psc = self._score_class(data["mean"])
            persona_rows += f'<tr><td>{pid}</td><td class="{psc}">{data["mean"]:.2f}</td><td>{data["n"]}</td></tr>'

        return f"""
<h2>6. Control Group — Persona Distinguishability</h2>
<p>Does the persona definition actually produce meaningfully different behavior
compared to a generic student?</p>

<div class="grid">
  <div class="card">
    <div class="value {sc}">{overall:.2f}</div>
    <div class="label">Overall Distinctiveness (1-5)</div>
  </div>
</div>

<table>
<tr><th>Persona</th><th>Distinctiveness</th><th>N</th></tr>
{persona_rows}
</table>

<div class="metric-card">
<strong>Interpretation:</strong>
<ul>
  <li><strong>≥ 4.0:</strong> Persona definitions strongly differentiate behavior — good prompt design</li>
  <li><strong>3.0-4.0:</strong> Moderate differentiation — personas have some effect but could be stronger</li>
  <li><strong>< 3.0:</strong> Weak differentiation — persona prompts need improvement</li>
</ul>
</div>
"""

    def _section_conclusion(self) -> str:
        stats = self._compute_all_stats()
        ranking = stats["model_ranking"]

        if ranking:
            best = ranking[0]
            conclusion_parts = [
                f'<p><strong>Most stable model: {best["model"]}</strong> '
                f'(composite score: {best["composite"]:.2f}/5.0)</p>'
            ]
        else:
            conclusion_parts = ["<p>No model ranking available.</p>"]

        # D1 insight
        d1 = stats["d1"]
        if d1["by_phase"]:
            scripted_d1 = d1["by_phase"].get("scripted", {}).get("mean", 0)
            live_d1 = d1["by_phase"].get("live", {}).get("mean", 0)
            if scripted_d1 and live_d1:
                conclusion_parts.append(
                    f"<p><strong>Persona adherence:</strong> Scripted={scripted_d1:.2f}, "
                    f"Live={live_d1:.2f}. "
                    f"{'Tutor variability has minimal impact.' if abs(scripted_d1-live_d1)<0.3 else 'Tutor variability significantly affects student persona adherence.'}</p>"
                )

        # D4 insight
        d4 = stats["d4"]
        onset = d4.get("drift_onset_mean", 0)
        if onset:
            conclusion_parts.append(
                f"<p><strong>Drift:</strong> Persona drift first appears around turn {onset:.0f} on average. "
                f"{'This is late in the conversation — good stability.' if onset >= 6 else 'Early drift — prompt reinforcement may help.'}</p>"
            )

        # Control insight
        ctrl = stats["control"]
        distinctiveness = ctrl.get("overall_mean", 0)
        if distinctiveness:
            conclusion_parts.append(
                f"<p><strong>Persona value:</strong> Distinctiveness score = {distinctiveness:.2f}/5.0. "
                f"{'Persona definitions effectively shape behavior.' if distinctiveness >= 4 else 'Persona definitions could be strengthened.' if distinctiveness >= 3 else 'Persona definitions have limited effect — consider redesigning prompts.'}</p>"
            )

        return f"""
<h2>7. Conclusion & Recommendations</h2>
<div class="conclusion">
{"".join(conclusion_parts)}
</div>

<h3>Prompt Design Recommendations</h3>
<ul>
  <li>If D4 drift is high: add periodic persona reinforcement in runtime_guidance</li>
  <li>If D1 knowledge_boundary is low: make known/unknown concept lists more explicit in the prompt</li>
  <li>If D3 cross-model consistency is low: simplify behavioral rules to core behaviors that all models can follow</li>
  <li>If control distinctiveness is low: strengthen persona-specific language patterns and reaction styles</li>
</ul>
"""


def _count_votes(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1
    return dict(counts)
