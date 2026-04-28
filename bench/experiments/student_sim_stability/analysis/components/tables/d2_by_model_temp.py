"""D2 reproducibility × tutor-temperature table Component."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _score_class,
    booktabs_table,
    csv_bytes,
)


class D2ByModelTemp(Component):
    name = "d2_by_model_temp"

    def __init__(self, d2_by_model_temp: dict):
        self.d2 = d2_by_model_temp or {}

    def render_html(self) -> str:
        rows = ""
        for key in sorted(self.d2.keys()):
            parts = key.split("__")
            model = parts[0]
            temp = parts[1] if len(parts) > 1 else "?"
            d = self.d2[key]
            rows += (
                f"<tr><td>{model}</td><td>{temp}</td>"
                f'<td class="{_score_class(d["mean"])}">{d["mean"]:.2f}</td>'
                f'<td>[{d.get("ci_low", 0):.2f}, {d.get("ci_high", 0):.2f}]</td>'
                f'<td>{d["std"]:.2f}</td>'
                f'<td>{d["n"]}</td></tr>'
            )
        return (
            "<table><tr><th>Model</th><th>Tutor Temp</th><th>D2 Score</th>"
            f"<th>95% CI</th><th>Std</th><th>N</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["model", "tutor_temperature", "mean", "ci_low", "ci_high", "std", "n"]
        ]
        for key in sorted(self.d2.keys()):
            parts = key.split("__")
            model = parts[0]
            temp = parts[1] if len(parts) > 1 else "?"
            d = self.d2[key]
            rows.append(
                [
                    model,
                    temp,
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
        for key in sorted(self.d2.keys()):
            parts = key.split("__")
            model = parts[0]
            temp = parts[1] if len(parts) > 1 else "?"
            d = self.d2[key]
            ci = f"[{d.get('ci_low', 0):.2f}, {d.get('ci_high', 0):.2f}]"
            rows.append(
                [model, temp, f"{d['mean']:.2f}", ci, f"{d['std']:.2f}", d["n"]]
            )
        return booktabs_table(
            ["Model", "Tutor Temp", "D2 Mean", "95% CI", "Std", "N"],
            "llrlrr",
            rows,
        )
