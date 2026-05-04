"""Human-vs-judge S4 breakdown by persona table Component."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)


def _pct_or_dash(value: float | None) -> str:
    return f"{value:.0%}" if isinstance(value, (int, float)) else "—"


class HumanAlignmentB1Breakdown(Component):
    name = "human_alignment_b1_breakdown"

    def __init__(self, breakdown_by_persona: dict | None):
        self.breakdown = breakdown_by_persona or {}

    def render_html(self) -> str:
        breakdown_rows = ""
        for persona in sorted(self.breakdown.keys()):
            entry = self.breakdown[persona] or {}
            n = entry.get("n", 0) or 0
            if not n:
                continue
            breakdown_rows += (
                f"<tr><td>{persona}</td>"
                f"<td>{n}</td>"
                f"<td>{_pct_or_dash(entry.get('sonnet_accuracy'))}</td>"
                f"<td>{_pct_or_dash(entry.get('gpt54_accuracy'))}</td>"
                f"<td>{_pct_or_dash(entry.get('gemini_accuracy'))}</td></tr>"
            )
        if not breakdown_rows:
            return ""
        return (
            "<h3>S4 Breakdown by Persona</h3>"
            "<table><tr><th>Persona</th><th>N</th><th>Sonnet</th>"
            "<th>GPT-5.4</th><th>Gemini</th></tr>"
            f"{breakdown_rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            [
                "persona_id",
                "n",
                "sonnet_accuracy",
                "gpt54_accuracy",
                "gemini_accuracy",
            ]
        ]
        for persona in sorted(self.breakdown.keys()):
            entry = self.breakdown[persona] or {}
            rows.append(
                [
                    persona,
                    entry.get("n"),
                    entry.get("sonnet_accuracy"),
                    entry.get("gpt54_accuracy"),
                    entry.get("gemini_accuracy"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str | None:
        rows: list[list[object]] = []
        for persona in sorted(self.breakdown.keys()):
            entry = self.breakdown[persona] or {}
            n = entry.get("n", 0) or 0
            if not n:
                continue
            rows.append(
                [
                    persona,
                    n,
                    _pct_or_dash(entry.get("sonnet_accuracy")),
                    _pct_or_dash(entry.get("gpt54_accuracy")),
                    _pct_or_dash(entry.get("gemini_accuracy")),
                ]
            )
        if not rows:
            return None
        return booktabs_table(
            ["Persona", "N", "Sonnet", "GPT-5.4", "Gemini"],
            "lrrrr",
            rows,
        )
