"""S2 per-turn fidelity curve Component (mean + 95% CI bands)."""

from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from experiments.student_sim_stability.analysis.components.base import (
    _MODEL_ORDER,
    Component,
    _color_for,
    _embed_img,
    _fig_to_base64,
    csv_bytes,
    pgf_csv_path,
)

_MODEL_PGF_COLORS = {
    "claude-sonnet-4-6": "simSonnet",
    "gpt-5.4": "simGPT",
    "gemini-3.1-pro-preview": "simGemini",
}


def _pgf_color_for(model: str) -> str:
    return _MODEL_PGF_COLORS.get(model, "black")


def _safe_id(model: str) -> str:
    """Strip pgfplots-unfriendly characters from model id for path names."""
    return "".join(ch if ch.isalnum() else "" for ch in model)


class D3Curves(Component):
    """Per-turn persona-fidelity curves with 95% CI shading.

    Input shape: ``{model: [{"turn": int, "mean": float, "ci_lo": float,
    "ci_hi": float, "n": int}, ...]}``. The 95% CI is the normal-approx
    half-width over the per-conversation persona_fidelity values at that
    turn for that student model.
    """

    name = "d3_curves"

    def __init__(self, avg_fidelity_curves: dict[str, list[dict]]):
        self.curves = avg_fidelity_curves or {}

    def _compute(self) -> dict:
        if not self.curves:
            return {"empty": True}
        ordered: list[tuple[str, list[dict]]] = []
        all_means: list[float] = []
        all_lo: list[float] = []
        all_hi: list[float] = []
        for model in _MODEL_ORDER:
            stats = self.curves.get(model)
            if not stats:
                continue
            ordered.append((model, stats))
            for s in stats:
                if isinstance(s.get("mean"), (int, float)):
                    all_means.append(float(s["mean"]))
                if isinstance(s.get("ci_lo"), (int, float)):
                    all_lo.append(float(s["ci_lo"]))
                if isinstance(s.get("ci_hi"), (int, float)):
                    all_hi.append(float(s["ci_hi"]))
        y_min = min(all_lo or all_means or [4.0])
        lower = min(3.8, y_min - 0.05)
        return {"empty": False, "series": ordered, "lower": lower}

    def _draw(self, data: dict):
        if data.get("empty"):
            return None
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for model, stats in data["series"]:
            x = [s["turn"] for s in stats]
            y = [s["mean"] for s in stats]
            lo = [s["ci_lo"] for s in stats]
            hi = [s["ci_hi"] for s in stats]
            color = _color_for(model)
            ax.fill_between(x, lo, hi, color=color, alpha=0.18, linewidth=0)
            ax.plot(
                x,
                y,
                "o-",
                linewidth=2,
                markersize=6,
                label=model,
                color=color,
            )
        ax.set_xlabel("Turn", fontsize=11)
        ax.set_ylabel("Persona Fidelity (1-5)", fontsize=11)
        lower = data["lower"]
        ax.set_ylim(lower, 5.1)
        ax.set_title("S2 Per-turn Persona Fidelity (mean + 95% CI)", size=13)
        ax.legend(fontsize=9)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.4, label="_nolegend_")
        ax.grid(axis="y", alpha=0.3)
        return fig

    def render_html(self) -> str:
        if self._data.get("empty"):
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "S2 drift curves")

    def render_csv(self) -> bytes | None:
        data = self._data
        if data.get("empty"):
            return None
        rows: list[list[object]] = [["model", "turn", "mean", "ci_lo", "ci_hi", "n"]]
        for model, stats in data["series"]:
            for s in stats:
                rows.append(
                    [
                        model,
                        int(s["turn"]),
                        f"{float(s['mean']):.4f}",
                        f"{float(s['ci_lo']):.4f}",
                        f"{float(s['ci_hi']):.4f}",
                        int(s.get("n", 0)),
                    ]
                )
        return csv_bytes(rows)

    def render_pgf(self) -> str | None:
        data = self._data
        if data.get("empty"):
            return None
        series = data["series"]
        if not series:
            return None
        max_turn = max((s["turn"] for _, stats in series for s in stats), default=0)
        if max_turn == 0:
            return None
        all_lo = [s["ci_lo"] for _, stats in series for s in stats]
        all_hi = [s["ci_hi"] for _, stats in series for s in stats]
        ymin = max(0.0, min(all_lo) - 0.05) if all_lo else 3.5
        ymax = min(5.05, max(all_hi) + 0.05) if all_hi else 5.05

        addplots: list[str] = []
        for model, stats in series:
            color = _pgf_color_for(model)
            sid = _safe_id(model)
            mean_coords = " ".join(
                f"({int(s['turn'])},{float(s['mean']):.3f})" for s in stats
            )
            lo_coords = " ".join(
                f"({int(s['turn'])},{float(s['ci_lo']):.3f})" for s in stats
            )
            hi_coords = " ".join(
                f"({int(s['turn'])},{float(s['ci_hi']):.3f})" for s in stats
            )
            # Two invisible boundary plots + a fill between them, then the
            # mean line on top so the CI band sits behind the marker line.
            addplots.append(
                f"\\addplot[draw=none, name path={sid}Lo, forget plot] "
                f"coordinates {{{lo_coords}}};"
            )
            addplots.append(
                f"\\addplot[draw=none, name path={sid}Hi, forget plot] "
                f"coordinates {{{hi_coords}}};"
            )
            addplots.append(
                f"\\addplot[{color}!25, forget plot] fill between"
                f"[of={sid}Lo and {sid}Hi];"
            )
            addplots.append(
                f"\\addplot+[mark=*, thick, color={color}] "
                f"coordinates {{{mean_coords}}};"
            )
            addplots.append(f"\\addlegendentry{{{model}}}")
        body = "\n".join(addplots)
        csv_ref = pgf_csv_path(self.name)
        return (
            f"% Auto-generated; data: {csv_ref}\n"
            "\\begin{tikzpicture}\n"
            "\\begin{axis}[\n"
            "  width=\\linewidth, height=0.45\\linewidth,\n"
            "  xlabel={Scored turn}, ylabel={Per-turn fidelity},\n"
            f"  xmin=1, xmax={max_turn}, ymin={ymin:.2f}, ymax={ymax:.2f},\n"
            "  xtick=data, xticklabel style={font=\\footnotesize},\n"
            "  legend pos=south west, legend cell align=left,\n"
            "  legend style={font=\\footnotesize},\n"
            "  grid=major, grid style={dashed, gray!30},\n"
            "]\n"
            f"{body}\n"
            "\\end{axis}\n"
            "\\end{tikzpicture}\n"
        )
