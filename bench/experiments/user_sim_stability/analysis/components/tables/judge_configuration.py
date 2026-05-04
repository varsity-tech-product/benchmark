"""Judge configuration / multi-judge agreement table Component."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)


class JudgeConfiguration(Component):
    name = "judge_configuration"

    def __init__(self, by_dimension: dict | None):
        self.by_dim = by_dimension or {}

    def render_html(self) -> str:
        rows = "".join(
            f"<tr><td>{dim}</td>"
            f"<td>{item.get('n', 0)}</td>"
            f"<td>{item.get('mean_score_range', 0):.2f}</td>"
            f"<td>{item.get('within_one_point_rate', 0):.1%}</td></tr>"
            for dim, item in sorted(self.by_dim.items())
        )
        if not rows:
            rows = "<tr><td colspan='4'>Multi-judge agreement has not been computed.</td></tr>"
        return (
            "<table><tr><th>Dimension</th><th>N</th><th>Mean Range</th>"
            f"<th>Within 1 pt</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["dimension", "n", "mean_score_range", "within_one_point_rate"]
        ]
        for dim, item in sorted(self.by_dim.items()):
            rows.append(
                [
                    dim,
                    item.get("n"),
                    item.get("mean_score_range"),
                    item.get("within_one_point_rate"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        rows: list[list[object]] = []
        for dim, item in sorted(self.by_dim.items()):
            rows.append(
                [
                    dim,
                    item.get("n", 0),
                    f"{item.get('mean_score_range', 0):.2f}",
                    f"{item.get('within_one_point_rate', 0):.1%}",
                ]
            )
        if not rows:
            rows = [["Multi-judge agreement has not been computed.", "", "", ""]]
        return booktabs_table(
            ["Dimension", "N", "Mean Range", "Within 1 pt"],
            "lrrr",
            rows,
        )
