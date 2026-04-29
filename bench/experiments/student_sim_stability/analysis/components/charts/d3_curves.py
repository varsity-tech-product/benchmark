"""D3 per-turn fidelity curve Component."""

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


class D3Curves(Component):
    """Per-turn persona-fidelity curves zoomed to the 3.8–5.1 band."""

    name = "d3_curves"

    def __init__(self, avg_fidelity_curves: dict[str, list[float]]):
        self.curves = avg_fidelity_curves or {}

    def _compute(self) -> dict:
        if not self.curves:
            return {"empty": True}
        ordered: list[tuple[str, list[float]]] = []
        all_y: list[float] = []
        for model in _MODEL_ORDER:
            if model in self.curves:
                y = self.curves[model]
                ordered.append((model, y))
                all_y.extend(v for v in y if isinstance(v, (int, float)))
        y_min = min(all_y) if all_y else 4.0
        lower = min(3.8, y_min - 0.15)
        return {"empty": False, "series": ordered, "lower": lower}

    def _draw(self, data: dict):
        if data.get("empty"):
            return None
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for model, y in data["series"]:
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
        lower = data["lower"]
        ax.set_ylim(lower, 5.1)
        ax.set_title("D3 Per-turn Persona Fidelity (zoomed to 4–5 band)", size=13)
        ax.legend(fontsize=9)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.axhline(y=4, color="green", linestyle="--", alpha=0.4, label="_nolegend_")
        if lower < 3:
            ax.axhline(
                y=3, color="orange", linestyle="--", alpha=0.4, label="_nolegend_"
            )
        ax.grid(axis="y", alpha=0.3)
        return fig

    def render_html(self) -> str:
        if self._data.get("empty"):
            return ""
        fig = self.figure()
        if fig is None:
            return ""
        return _embed_img(_fig_to_base64(fig), "D3 drift curves")

    def render_csv(self) -> bytes | None:
        data = self._data
        if data.get("empty"):
            return None
        rows: list[list[object]] = [["model", "turn", "mean"]]
        for model, y in data["series"]:
            for idx, value in enumerate(y):
                if isinstance(value, (int, float)):
                    rows.append([model, idx + 1, f"{float(value):.4f}"])
        return csv_bytes(rows)

    def render_pgf(self) -> str | None:
        data = self._data
        if data.get("empty"):
            return None
        series = data["series"]
        if not series:
            return None
        max_turn = max((len(y) for _, y in series), default=0)
        if max_turn == 0:
            return None
        all_y = [v for _, y in series for v in y if isinstance(v, (int, float))]
        ymin = max(0.0, min(all_y) - 0.2) if all_y else 3.5
        ymax = min(5.05, max(all_y) + 0.1) if all_y else 5.05
        addplots: list[str] = []
        for model, y in series:
            color = _pgf_color_for(model)
            coords = " ".join(
                f"({i + 1},{float(v):.3f})"
                for i, v in enumerate(y)
                if isinstance(v, (int, float))
            )
            if not coords:
                continue
            addplots.append(
                f"\\addplot+[mark=*, thick, color={color}] coordinates {{{coords}}};"
            )
            addplots.append(f"\\addlegendentry{{{model}}}")
        if not addplots:
            return None
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
