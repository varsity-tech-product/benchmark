"""D2 cross-run reproducibility bar chart Component."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from experiments.student_sim_stability.analysis.components.base import (
    _MODEL_ORDER,
    Component,
    _embed_img,
    _fig_to_base64,
)


class D2Bars(Component):
    """Grouped bar chart: D2 by model, colored by tutor temperature."""

    name = "d2_bars"

    def __init__(self, d2_by_model_temp: dict):
        self.d2 = d2_by_model_temp

    def _compute(self) -> dict:
        if not self.d2:
            return {"empty": True}
        models = [m for m in _MODEL_ORDER if any(m in k for k in self.d2)]
        temps = sorted(set(k.split("__")[1] for k in self.d2))
        per_temp = {
            t: {
                "vals": [self.d2.get(f"{m}__{t}", {}).get("mean", 0) for m in models],
                "stds": [self.d2.get(f"{m}__{t}", {}).get("std", 0) for m in models],
            }
            for t in temps
        }
        return {"empty": False, "models": models, "temps": temps, "per_temp": per_temp}

    def _draw(self, data: dict):
        if data.get("empty"):
            return None
        models = data["models"]
        temps = data["temps"]
        per_temp = data["per_temp"]

        fig, ax = plt.subplots(figsize=(8, 4.5))
        x = np.arange(len(models))
        width = 0.35
        for i, t in enumerate(temps):
            vals = per_temp[t]["vals"]
            stds = per_temp[t]["stds"]
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
        return fig

    def render_html(self) -> str:
        if self._data.get("empty"):
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "D2 bar chart")
