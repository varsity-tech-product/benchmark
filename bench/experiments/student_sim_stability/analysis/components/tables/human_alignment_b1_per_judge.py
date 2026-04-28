"""Human-vs-judge B1 accuracy per-judge table Component."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)

_VIEWS = (
    ("sonnet_vs_human", "Sonnet"),
    ("gpt54_vs_human", "GPT-5.4"),
    ("gemini_vs_human", "Gemini"),
    ("panel_2_strict_vs_human", "Panel-2 strict"),
)


class HumanAlignmentB1PerJudge(Component):
    name = "human_alignment_b1_per_judge"

    def __init__(self, b1_block: dict | None):
        self.b1 = b1_block or {}

    def render_html(self) -> str:
        b1 = self.b1
        if not b1.get("n", 0):
            return ""
        b1_rows = ""
        for view, label in _VIEWS:
            v = b1.get(view) or {}
            if not v:
                continue
            b1_rows += (
                f"<tr><td>{label}</td>"
                f"<td>{v.get('accuracy_vs_human', 0):.1%}</td>"
                f"<td>{v.get('n', 0)}</td></tr>"
            )
        if not b1_rows:
            return ""
        return (
            "<h3>B1 Per-Judge Accuracy vs Human</h3>"
            "<table><tr><th>Judge view</th><th>Accuracy vs human</th><th>N</th></tr>"
            f"{b1_rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [["judge_view", "accuracy_vs_human", "n"]]
        b1 = self.b1
        for view, label in _VIEWS:
            v = b1.get(view) or {}
            if not v:
                continue
            rows.append([label, v.get("accuracy_vs_human"), v.get("n")])
        return csv_bytes(rows)

    def render_tex(self) -> str | None:
        b1 = self.b1
        if not b1.get("n", 0):
            return None
        rows: list[list[object]] = []
        for view, label in _VIEWS:
            v = b1.get(view) or {}
            if not v:
                continue
            rows.append([label, f"{v.get('accuracy_vs_human', 0):.1%}", v.get("n", 0)])
        if not rows:
            return None
        return booktabs_table(["Judge view", "Accuracy vs human", "N"], "llr", rows)
