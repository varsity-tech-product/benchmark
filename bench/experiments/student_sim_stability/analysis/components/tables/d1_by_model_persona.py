"""D1 persona-adherence by (model, persona) table Component (independent export)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _score_class,
    booktabs_table,
    csv_bytes,
)


class D1ByModelPersona(Component):
    name = "d1_by_model_persona"

    def __init__(self, by_model_persona: dict):
        self.by_mp = by_model_persona or {}

    def render_html(self) -> str:
        rows = "".join(
            f'<tr><td>{key.split("__", 1)[0]}</td>'
            f'<td>{key.split("__", 1)[1] if "__" in key else ""}</td>'
            f'<td class="{_score_class(d["mean"])}">{d["mean"]:.2f}</td>'
            f'<td>[{d.get("ci_low", 0):.2f}, {d.get("ci_high", 0):.2f}]</td>'
            f'<td>{d["std"]:.2f}</td>'
            f'<td>{d["n"]}</td></tr>'
            for key, d in sorted(self.by_mp.items())
        )
        return (
            "<table><tr><th>Model</th><th>Persona</th><th>D1 Mean</th>"
            f"<th>95% CI</th><th>Std</th><th>N</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["model", "persona_id", "mean", "ci_low", "ci_high", "std", "n"]
        ]
        for key, d in sorted(self.by_mp.items()):
            model, _, persona = key.partition("__")
            rows.append(
                [
                    model,
                    persona,
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
        for key, d in sorted(self.by_mp.items()):
            model, _, persona = key.partition("__")
            ci = f"[{d.get('ci_low', 0):.2f}, {d.get('ci_high', 0):.2f}]"
            rows.append(
                [model, persona, f"{d['mean']:.2f}", ci, f"{d['std']:.2f}", d["n"]]
            )
        return booktabs_table(
            ["Model", "Persona", "D1 Mean", "95% CI", "Std", "N"],
            "llrlrr",
            rows,
        )
