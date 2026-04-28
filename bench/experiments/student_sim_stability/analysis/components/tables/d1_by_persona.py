"""D1 persona-adherence by persona table Component (independent export)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _score_class,
    booktabs_table,
    csv_bytes,
)


class D1ByPersona(Component):
    name = "d1_by_persona"

    def __init__(self, by_persona: dict):
        self.by_persona = by_persona or {}

    def render_html(self) -> str:
        rows = "".join(
            f"<tr><td>{p}</td>"
            f'<td class="{_score_class(d["mean"])}">{d["mean"]:.2f}</td>'
            f'<td>[{d.get("ci_low", 0):.2f}, {d.get("ci_high", 0):.2f}]</td>'
            f'<td>{d["std"]:.2f}</td>'
            f'<td>{d["n"]}</td></tr>'
            for p, d in sorted(self.by_persona.items())
        )
        return (
            "<table><tr><th>Persona</th><th>D1 Mean</th><th>95% CI</th>"
            f"<th>Std</th><th>N</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["persona_id", "mean", "ci_low", "ci_high", "std", "n"]
        ]
        for p, d in sorted(self.by_persona.items()):
            rows.append(
                [
                    p,
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
        for p, d in sorted(self.by_persona.items()):
            ci = f"[{d.get('ci_low', 0):.2f}, {d.get('ci_high', 0):.2f}]"
            rows.append([p, f"{d['mean']:.2f}", ci, f"{d['std']:.2f}", d["n"]])
        return booktabs_table(
            ["Persona", "D1 Mean", "95% CI", "Std", "N"],
            "lrlrr",
            rows,
        )
