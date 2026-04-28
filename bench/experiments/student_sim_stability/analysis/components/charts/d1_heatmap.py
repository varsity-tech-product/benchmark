"""D1 persona-adherence heatmap Component.

Renders one heatmap per judge (Sonnet / GPT-5.4 / Gemini) when multi-judge data
is available, otherwise falls back to a single primary-judge heatmap.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _embed_img,
    _fig_to_base64,
    _wrap_label,
)


class D1Heatmap(Component):
    name = "d1_heatmap"

    def __init__(self, d1_stats: dict, multi: dict | None = None):
        self.d1_stats = d1_stats
        self.multi = multi

    def _compute(self) -> dict:
        d1 = self.d1_stats
        models = sorted(set(k.split("__")[0] for k in d1["by_model_persona"]))
        personas = sorted(set(k.split("__")[1] for k in d1["by_model_persona"]))
        if not models or not personas:
            return {"mode": "empty"}
        per_judge = self._matrices_per_judge(models, personas)
        if per_judge:
            return {
                "mode": "multi",
                "models": models,
                "personas": personas,
                "matrices": per_judge,
            }
        primary = np.zeros((len(models), len(personas)))
        for i, m in enumerate(models):
            for j, p in enumerate(personas):
                primary[i, j] = (
                    d1["by_model_persona"].get(f"{m}__{p}", {}).get("mean", 0)
                )
        return {
            "mode": "primary",
            "models": models,
            "personas": personas,
            "primary": primary,
        }

    def _draw(self, data: dict):
        mode = data.get("mode")
        if mode == "multi":
            return self._build_multi(data["matrices"], data["models"], data["personas"])
        if mode == "primary":
            return self._build_primary_from(
                data["primary"], data["models"], data["personas"]
            )
        return None

    def render_html(self) -> str:
        data = self._data
        if data.get("mode") == "empty":
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        alt = "D1 heatmap per judge" if data.get("mode") == "multi" else "D1 heatmap"
        return _embed_img(_fig_to_base64(fig), alt)

    def _matrices_per_judge(
        self, models: list[str], personas: list[str]
    ) -> dict[str, np.ndarray] | None:
        if not self.multi:
            return None
        d1_rows = (self.multi.get("dimensions") or {}).get("D1", {}).get("per_eval", [])
        if not d1_rows:
            return None
        model_idx = {m: i for i, m in enumerate(models)}
        persona_idx = {p: j for j, p in enumerate(personas)}
        buckets: dict[str, list[list[list[float]]]] = {
            view: [[[] for _ in personas] for _ in models]
            for view in ("sonnet", "gpt54", "gemini")
        }
        for row in d1_rows:
            meta = row.get("metadata") or {}
            model = meta.get("model")
            persona = meta.get("persona_id")
            if model not in model_idx or persona not in persona_idx:
                continue
            i = model_idx[model]
            j = persona_idx[persona]
            for view in ("sonnet", "gpt54", "gemini"):
                v = (row.get("scores_by_judge", {}).get(view) or {}).get("overall")
                if isinstance(v, (int, float)):
                    buckets[view][i][j].append(float(v))
        matrices: dict[str, np.ndarray] = {}
        for view, cells in buckets.items():
            arr = np.zeros((len(models), len(personas)))
            for i, row_cells in enumerate(cells):
                for j, vals in enumerate(row_cells):
                    arr[i, j] = float(np.mean(vals)) if vals else 0.0
            matrices[view] = arr
        if all(not np.any(arr) for arr in matrices.values()):
            return None
        return matrices

    def _build_multi(
        self,
        per_judge: dict[str, np.ndarray],
        models: list[str],
        personas: list[str],
    ):
        view_order = ("sonnet", "gpt54", "gemini")
        labels = {
            "sonnet": "Claude Sonnet",
            "gpt54": "GPT-5.4",
            "gemini": "Gemini 3.1 Pro",
        }
        fig_width = max(14, len(personas) * 1.6 * 3)
        fig_height = max(3.2, len(models) * 0.9)
        fig, axes = plt.subplots(1, 3, figsize=(fig_width, fig_height), sharey=True)
        ims = []
        for ax, view in zip(axes, view_order):
            data = per_judge[view]
            im = ax.imshow(data, cmap="RdYlGn", vmin=1, vmax=5, aspect="auto")
            ims.append(im)
            ax.set_xticks(range(len(personas)))
            ax.set_xticklabels(
                [_wrap_label(p) for p in personas], rotation=30, ha="right", fontsize=9
            )
            ax.set_yticks(range(len(models)))
            ax.set_yticklabels(models, fontsize=10)
            ax.set_title(f"Judge: {labels[view]}", size=12, pad=8)
            for i in range(len(models)):
                for j in range(len(personas)):
                    ax.text(
                        j,
                        i,
                        f"{data[i, j]:.2f}",
                        ha="center",
                        va="center",
                        fontsize=10,
                        fontweight="bold",
                    )
        fig.suptitle(
            "D1 Persona Adherence — Student Model × Persona, per Judge",
            size=14,
            y=1.02,
        )
        fig.colorbar(ims[-1], ax=axes, label="D1 overall (1-5)", shrink=0.85, pad=0.02)
        return fig

    def _build_primary_from(
        self, data: np.ndarray, models: list[str], personas: list[str]
    ):
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
        ax.set_title(
            "D1 Persona Adherence — Student Model × Persona "
            "(judged by Claude Sonnet, primary)",
            size=13,
            pad=10,
        )
        return fig
