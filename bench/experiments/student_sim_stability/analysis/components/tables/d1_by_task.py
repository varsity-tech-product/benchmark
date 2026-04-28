"""D1 persona-adherence by task table Component (independent export)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _score_class,
    booktabs_table,
    csv_bytes,
)


class D1ByTask(Component):
    name = "d1_by_task"

    def __init__(self, by_task: dict):
        self.by_task = by_task or {}

    def render_html(self) -> str:
        rows = "".join(
            f"<tr><td>{t}</td>"
            f'<td class="{_score_class(d["mean"])}">{d["mean"]:.2f}</td>'
            f'<td>[{d.get("ci_low", 0):.2f}, {d.get("ci_high", 0):.2f}]</td>'
            f'<td>{d["std"]:.2f}</td>'
            f'<td>{d["n"]}</td></tr>'
            for t, d in sorted(self.by_task.items())
        )
        return (
            "<table><tr><th>Task</th><th>D1 Mean</th><th>95% CI</th>"
            f"<th>Std</th><th>N</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["task_id", "mean", "ci_low", "ci_high", "std", "n"]
        ]
        for t, d in sorted(self.by_task.items()):
            rows.append(
                [
                    t,
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
        for t, d in sorted(self.by_task.items()):
            ci = f"[{d.get('ci_low', 0):.2f}, {d.get('ci_high', 0):.2f}]"
            rows.append([t, f"{d['mean']:.2f}", ci, f"{d['std']:.2f}", d["n"]])
        return booktabs_table(
            ["Task", "D1 Mean", "95% CI", "Std", "N"],
            "lrlrr",
            rows,
        )
