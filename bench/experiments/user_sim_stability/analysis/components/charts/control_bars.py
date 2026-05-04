"""Control distinctiveness bar chart Component."""

from __future__ import annotations

import matplotlib.pyplot as plt
from experiments.user_sim_stability.analysis.components.base import (
    JUDGE_PGF_COLORS,
    JUDGE_PGF_LABELS,
    Component,
    _embed_img,
    _fig_to_base64,
    _wrap_label,
    csv_bytes,
    pgf_csv_path,
    pgf_label_escape,
)


class ControlBars(Component):
    """Persona × judge distinctiveness bars.

    The HTML render path keeps the legacy persona-only matplotlib bars so the
    standalone report stays usable. The paper export path emits a sibling
    CSV (``persona,judge,mean``) plus a pgfplots ``\\addplot`` group whose
    bars are coloured by judge, matching the appendix palette.
    """

    name = "control_bars"

    def __init__(
        self,
        control_by_persona: dict,
        by_persona_judge: dict | None = None,
        high_score_ratio: float | None = None,
        high_score_threshold: float | None = None,
        standardized_effect_vs_baseline: float | None = None,
        baseline_unrecognizable: float | None = None,
        n: int | None = None,
    ):
        self.ctrl = control_by_persona
        self.by_persona_judge = by_persona_judge or {}
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

    def _persona_judge_rows(self) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        for persona in sorted(self.by_persona_judge):
            entry = self.by_persona_judge[persona] or {}
            for judge in ("sonnet", "gpt54", "gemini"):
                cell = entry.get(judge) or {}
                mean = cell.get("mean")
                if isinstance(mean, (int, float)):
                    rows.append((persona, judge, float(mean)))
        return rows

    def render_csv(self) -> bytes | None:
        rows = self._persona_judge_rows()
        if not rows:
            return None
        table: list[list[object]] = [["persona", "judge", "mean"]]
        for persona, judge, mean in rows:
            table.append([persona, judge, f"{mean:.4f}"])
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
        for persona, judge, mean in rows:
            per_judge[judge][persona] = mean
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
            "  width=\\linewidth, height=0.45\\linewidth,\n"
            "  ybar, bar width=6pt, enlarge x limits=0.18,\n"
            "  ymin=0, ymax=5.2,\n"
            "  ylabel={S6 distinctiveness mean},\n"
            f"  symbolic x coords={{{symbolic}}},\n"
            f"  xticklabels={{{ticklabels}}},\n"
            "  xtick=data, xticklabel style={font=\\footnotesize},\n"
            "  legend style={at={(0.5,-0.22)}, anchor=north, legend columns=3,\n"
            "    font=\\footnotesize, /tikz/every even column/.append style={"
            "column sep=8pt}},\n"
            "]\n"
            f"{body}\n"
            "\\end{axis}\n"
            "\\end{tikzpicture}\n"
        )
