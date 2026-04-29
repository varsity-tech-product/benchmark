"""B1 blind persona identification chart Component."""

from __future__ import annotations

import matplotlib.pyplot as plt
from experiments.student_sim_stability.analysis.components.base import (
    JUDGE_PGF_COLORS,
    JUDGE_PGF_LABELS,
    Component,
    _color_for,
    _embed_img,
    _fig_to_base64,
    _wrap_label,
    csv_bytes,
    pgf_csv_path,
    pgf_label_escape,
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

    def _persona_judge_rows(self) -> list[tuple[str, str, float]]:
        by_pj = self.b1.get("by_persona_judge") or {}
        rows: list[tuple[str, str, float]] = []
        for persona in sorted(by_pj):
            entry = by_pj[persona] or {}
            for judge in ("sonnet", "gpt54", "gemini"):
                cell = entry.get(judge) or {}
                acc = cell.get("accuracy")
                if isinstance(acc, (int, float)):
                    rows.append((persona, judge, float(acc)))
        return rows

    def render_csv(self) -> bytes | None:
        rows = self._persona_judge_rows()
        if not rows:
            return None
        table: list[list[object]] = [["persona", "judge", "accuracy"]]
        for persona, judge, acc in rows:
            table.append([persona, judge, f"{acc:.4f}"])
        return csv_bytes(table)

    def render_pgf(self) -> str | None:
        rows = self._persona_judge_rows()
        if not rows:
            return None
        personas = sorted({r[0] for r in rows})
        if not personas:
            return None
        symbolic = ",".join(personas)
        ticklabels = ",".join(pgf_label_escape(p) for p in personas)
        per_judge: dict[str, dict[str, float]] = {
            judge: {} for judge in ("sonnet", "gpt54", "gemini")
        }
        for persona, judge, acc in rows:
            per_judge[judge][persona] = acc
        addplots: list[str] = []
        for judge in ("sonnet", "gpt54", "gemini"):
            if not per_judge[judge]:
                continue
            coords = " ".join(
                f"({persona},{per_judge[judge].get(persona, 0.0):.3f})"
                for persona in personas
            )
            color = JUDGE_PGF_COLORS[judge]
            label = JUDGE_PGF_LABELS[judge]
            addplots.append(
                f"\\addplot+[ybar, fill={color}, draw={color}!60!black] "
                f"coordinates {{{coords}}};"
            )
            addplots.append(f"\\addlegendentry{{{label}}}")
        body = "\n".join(addplots)
        csv_ref = pgf_csv_path(self.name)
        return (
            "% Auto-generated; data: "
            f"{csv_ref}\n"
            "\\begin{tikzpicture}\n"
            "\\begin{axis}[\n"
            "  width=\\linewidth, height=0.4\\linewidth,\n"
            "  ybar, bar width=6pt, enlarge x limits=0.18,\n"
            "  ymin=0, ymax=1.05,\n"
            "  ylabel={B1 identification accuracy},\n"
            f"  symbolic x coords={{{symbolic}}},\n"
            f"  xticklabels={{{ticklabels}}},\n"
            "  xtick=data, xticklabel style={font=\\footnotesize},\n"
            "  legend style={at={(0.5,-0.22)}, anchor=north, legend columns=3,\n"
            "    font=\\footnotesize, /tikz/every even column/.append style={"
            "column sep=8pt}},\n"
            "]\n"
            f"{body}\n"
            "\\draw[dashed, gray!70] (axis cs:" + personas[0] + ",0.80) -- "
            "(axis cs:" + personas[-1] + ",0.80);\n"
            "\\draw[dashed, black!70] (axis cs:" + personas[0] + ",0.90) -- "
            "(axis cs:" + personas[-1] + ",0.90);\n"
            "\\end{axis}\n"
            "\\end{tikzpicture}\n"
        )
