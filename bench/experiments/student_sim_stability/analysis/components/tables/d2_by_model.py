"""S3 reproducibility by model table Component (CSV-only export today).

The HTML form is not currently embedded in the main report; it is exported
under ``results/main/report/components/d2_by_model.{html,csv}`` so the data
is independently inspectable and Phase 4 can ship it as a paper asset.
"""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _score_class,
    booktabs_table,
    csv_bytes,
)


class D2ByModel(Component):
    name = "d2_by_model"

    def __init__(self, by_model: dict):
        self.by_model = by_model or {}

    def render_html(self) -> str:
        rows = "".join(
            f"<tr><td>{m}</td>"
            f'<td class="{_score_class(d["mean"])}">{d["mean"]:.2f}</td>'
            f'<td>[{d.get("ci_low", 0):.2f}, {d.get("ci_high", 0):.2f}]</td>'
            f'<td>{d["std"]:.2f}</td>'
            f'<td>{d["n"]}</td></tr>'
            for m, d in sorted(self.by_model.items())
        )
        return (
            "<table><tr><th>Model</th><th>S3 Mean</th><th>95% CI</th>"
            f"<th>Std</th><th>N</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [["model", "mean", "ci_low", "ci_high", "std", "n"]]
        for m, d in sorted(self.by_model.items()):
            rows.append(
                [
                    m,
                    d.get("mean"),
                    d.get("ci_low"),
                    d.get("ci_high"),
                    d.get("std"),
                    d.get("n"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        rows: list[list[object]] = []
        for m, d in sorted(self.by_model.items()):
            ci = f"({d.get('ci_low', 0):.2f}, {d.get('ci_high', 0):.2f})"
            rows.append([m, f"{d['mean']:.2f}", ci, f"{d['std']:.2f}", d["n"]])
        return booktabs_table(
            ["Model", "S3 Mean", "95% CI", "Std", "N"],
            "lrlrr",
            rows,
        )
