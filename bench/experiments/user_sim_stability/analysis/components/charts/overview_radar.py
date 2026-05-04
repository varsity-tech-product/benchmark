"""Radar chart Component: S1/S3/S2 per user model."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from experiments.user_sim_stability.analysis.components.base import (
    Component,
    _color_for,
    _embed_img,
    _fig_to_base64,
)


class OverviewRadar(Component):
    """Radar chart of S1/S3/S2 per model, zoomed to the [4, 5] band."""

    name = "overview_radar"

    def __init__(self, ranking: list[dict]):
        self.ranking = [
            r
            for r in ranking
            if all(
                isinstance(r["scores"].get(dim), (int, float))
                for dim in ("S1", "S3", "S2")
            )
        ]

    def _compute(self) -> dict:
        if not self.ranking:
            return {"empty": True}
        series = [
            {
                "model": r["model"],
                "values": [r["scores"]["S1"], r["scores"]["S3"], r["scores"]["S2"]],
            }
            for r in self.ranking
        ]
        return {"empty": False, "series": series}

    def _draw(self, data: dict):
        if data.get("empty"):
            return None
        categories = ["S1\nAdherence", "S3\nReproducibility", "S2\nAnti-drift"]
        n = len(categories)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
        for entry in data["series"]:
            values = list(entry["values"])
            values += values[:1]
            color = _color_for(entry["model"])
            ax.plot(
                angles, values, "o-", linewidth=2, label=entry["model"], color=color
            )
            ax.fill(angles, values, alpha=0.1, color=color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, size=11)
        ax.set_ylim(4, 5)
        ax.set_yticks([4.0, 4.25, 4.5, 4.75, 5.0])
        ax.set_yticklabels(["4.0", "4.25", "4.5", "4.75", "5.0"], size=8)
        ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)
        ax.set_title(
            "Model Stability Profile (zoomed to 4–5 band)",
            size=14,
            pad=20,
        )
        return fig

    def render_html(self) -> str:
        if self._data.get("empty"):
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "Model radar chart")
