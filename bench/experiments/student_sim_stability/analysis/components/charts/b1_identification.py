"""B1 blind persona identification chart Component."""

from __future__ import annotations

import matplotlib.pyplot as plt
from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _color_for,
    _embed_img,
    _fig_to_base64,
    _wrap_label,
)


class B1Identification(Component):
    """Grouped bar chart: B1 identification accuracy per persona × model."""

    name = "b1_identification"

    def __init__(self, b1_stats: dict):
        self.b1 = b1_stats or {}

    def _compute(self) -> dict:
        by_pm = self.b1.get("by_persona_model") or {}
        by_persona = self.b1.get("by_persona") or {}
        if by_pm:
            personas = sorted({key.split("__", 1)[0] for key in by_pm})
            models = sorted({key.split("__", 1)[1] for key in by_pm})
            return {
                "mode": "by_pm",
                "personas": personas,
                "models": models,
                "by_pm": by_pm,
            }
        if by_persona:
            personas = sorted(by_persona.keys())
            return {
                "mode": "by_persona",
                "personas": personas,
                "accuracies": [by_persona[p].get("accuracy", 0.0) for p in personas],
            }
        return {"mode": "empty"}

    def _draw(self, data: dict):
        mode = data.get("mode")
        if mode == "by_pm":
            personas = data["personas"]
            models = data["models"]
            by_pm = data["by_pm"]
            fig_width = max(8.0, len(personas) * 1.6 + len(models) * 0.8)
            fig, ax = plt.subplots(figsize=(fig_width, 4.8))
            n_models = max(len(models), 1)
            bar_width = 0.8 / n_models
            for model_idx, model in enumerate(models):
                accuracies = [
                    (by_pm.get(f"{persona}__{model}", {}) or {}).get("accuracy", 0.0)
                    for persona in personas
                ]
                offsets = [
                    i + model_idx * bar_width - 0.4 + bar_width / 2
                    for i in range(len(personas))
                ]
                ax.bar(
                    offsets,
                    accuracies,
                    width=bar_width,
                    label=model,
                    color=_color_for(model),
                    alpha=0.9,
                )
            ax.set_xticks(range(len(personas)))
            ax.set_xticklabels([_wrap_label(p) for p in personas], fontsize=9)
            ax.set_ylim(0, 1.05)
            ax.set_ylabel("B1 identification accuracy", fontsize=11)
            ax.set_title(
                "B1 — Blind Persona Identification on Live Transcripts", size=13
            )
            ax.axhline(
                y=0.9, color="green", linestyle="--", alpha=0.35, label="90% strong"
            )
            ax.axhline(
                y=0.8,
                color="orange",
                linestyle="--",
                alpha=0.35,
                label="80% borderline",
            )
            ax.legend(fontsize=8, ncol=max(1, min(n_models + 2, 4)))
            ax.grid(axis="y", alpha=0.3)
            fig.subplots_adjust(bottom=0.22)
            return fig

        if mode == "by_persona":
            personas = data["personas"]
            accuracies = data["accuracies"]
            fig, ax = plt.subplots(figsize=(max(7.0, len(personas) * 1.5), 4.4))
            ax.bar(personas, accuracies, color="#3498db", alpha=0.85)
            ax.set_ylim(0, 1.05)
            ax.set_ylabel("B1 identification accuracy", fontsize=11)
            ax.axhline(y=0.9, color="green", linestyle="--", alpha=0.35)
            ax.axhline(y=0.8, color="orange", linestyle="--", alpha=0.35)
            ax.set_xticklabels([_wrap_label(p) for p in personas], fontsize=9)
            ax.set_xticks(range(len(personas)))
            return fig

        return None

    def render_html(self) -> str:
        if self._data.get("mode") == "empty":
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "B1 identification accuracy")
