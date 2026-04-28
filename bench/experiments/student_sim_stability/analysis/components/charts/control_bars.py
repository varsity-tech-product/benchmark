"""Control distinctiveness bar chart Component."""

from __future__ import annotations

import matplotlib.pyplot as plt
from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _embed_img,
    _fig_to_base64,
    _wrap_label,
)


class ControlBars(Component):
    """Bar chart: persona distinctiveness (control).

    Subtitle annotates the persona-vs-placebo evidence on the rubric's own
    scale: high-score ratio (the C1 ``aggregation_formula``) plus the
    standardized effect of distinctiveness mean against the rubric's
    "1 = no detectable difference" baseline. This intentionally avoids the
    cross-rubric Cohen's d previously embedded here, which compared D1
    overall to C1 distinctiveness and was not on a common metric scale.
    """

    name = "control_bars"

    def __init__(
        self,
        control_by_persona: dict,
        high_score_ratio: float | None = None,
        high_score_threshold: float | None = None,
        standardized_effect_vs_baseline: float | None = None,
        baseline_unrecognizable: float | None = None,
        n: int | None = None,
    ):
        self.ctrl = control_by_persona
        self.high_score_ratio = high_score_ratio
        self.high_score_threshold = high_score_threshold
        self.standardized_effect_vs_baseline = standardized_effect_vs_baseline
        self.baseline_unrecognizable = baseline_unrecognizable
        self.n = n

    def _compute(self) -> dict:
        if not self.ctrl:
            return {"empty": True}
        personas = sorted(self.ctrl.keys())
        return {
            "empty": False,
            "personas": personas,
            "vals": [self.ctrl[p]["mean"] for p in personas],
            "subtitle": self._effect_size_subtitle(),
        }

    def _draw(self, data: dict):
        if data.get("empty"):
            return None
        personas = data["personas"]
        vals = data["vals"]

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
        title = "Control: Persona vs No-Persona Distinctiveness"
        subtitle = data["subtitle"]
        if subtitle:
            ax.set_title(f"{title}\n{subtitle}", size=12)
        else:
            ax.set_title(title, size=13)
        ax.set_xticks(range(len(personas)))
        ax.set_xticklabels([_wrap_label(p) for p in personas], fontsize=9)
        ax.tick_params(axis="x", pad=8)
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.3)
        ax.axhline(y=3, color="orange", linestyle="--", alpha=0.3)
        fig.subplots_adjust(bottom=0.24)
        return fig

    def render_html(self) -> str:
        if self._data.get("empty"):
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "Control distinctiveness")

    def _effect_size_subtitle(self) -> str:
        """Persona-vs-placebo evidence on the rubric's own scale."""
        parts: list[str] = []
        if isinstance(self.high_score_ratio, (int, float)):
            thr = self.high_score_threshold or 4.0
            parts.append(
                f"high-score ratio (≥{thr:g}) = {self.high_score_ratio:.0%}"
                + (f" (n={self.n})" if self.n else "")
            )
        if isinstance(self.standardized_effect_vs_baseline, (int, float)):
            base = self.baseline_unrecognizable or 1.0
            parts.append(
                f"standardized effect vs baseline {base:g} = "
                f"{self.standardized_effect_vs_baseline:.1f}σ"
            )
        return " · ".join(parts)
