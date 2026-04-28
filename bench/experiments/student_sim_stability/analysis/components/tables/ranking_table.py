"""Cross-vendor candidate-selection ranking table (D1+D2+D3+Composite)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _fmt_score,
    _score_class,
    booktabs_table,
    csv_bytes,
)


def _ci_cell(ci: dict | None) -> str:
    if not ci:
        return "<td>n/a</td>"
    lo = ci.get("low")
    hi = ci.get("high")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return "<td>n/a</td>"
    return f"<td>[{lo:.2f}, {hi:.2f}]</td>"


class RankingTable(Component):
    """D1+D2+D3+Composite ranking with bootstrap 95% CI next to each mean."""

    name = "ranking_table"

    _BADGES = ("badge-gold", "badge-silver", "badge-bronze")
    _PLACES = ("1st", "2nd", "3rd")

    def __init__(self, ranking: list[dict]):
        self.ranking = ranking

    def render_html(self) -> str:
        rows = ""
        for i, r in enumerate(self.ranking):
            if i < 3:
                badge = (
                    f'<span class="badge {self._BADGES[i]}">{self._PLACES[i]}</span>'
                )
            else:
                badge = f"{i + 1}th"
            scores_ci = r.get("scores_ci") or {}
            composite_ci = {
                "low": r.get("composite_ci_low"),
                "high": r.get("composite_ci_high"),
            }
            rows += (
                f"<tr><td>{badge}</td><td><strong>{r['model']}</strong></td>\n"
                f'  <td class="{_score_class(r["scores"]["D1"])}">{_fmt_score(r["scores"]["D1"])}</td>'
                f"{_ci_cell(scores_ci.get('D1'))}\n"
                f'  <td class="{_score_class(r["scores"]["D2"])}">{_fmt_score(r["scores"]["D2"])}</td>'
                f"{_ci_cell(scores_ci.get('D2'))}\n"
                f'  <td class="{_score_class(r["scores"]["D3"])}">{_fmt_score(r["scores"]["D3"])}</td>'
                f"{_ci_cell(scores_ci.get('D3'))}\n"
                f'  <td class="{_score_class(r["composite"])}">{r["composite"]:.2f}</td>'
                f"{_ci_cell(composite_ci)}</tr>"
            )
        return (
            "<table><tr><th>Rank</th><th>Model</th>"
            "<th>D1</th><th>D1 95% CI</th>"
            "<th>D2</th><th>D2 95% CI</th>"
            "<th>D3</th><th>D3 95% CI</th>"
            "<th>Composite</th><th>Composite 95% CI</th></tr>\n"
            f"{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            [
                "rank",
                "model",
                "D1",
                "D1_ci_low",
                "D1_ci_high",
                "D2",
                "D2_ci_low",
                "D2_ci_high",
                "D3",
                "D3_ci_low",
                "D3_ci_high",
                "composite",
                "composite_ci_low",
                "composite_ci_high",
            ]
        ]
        for i, r in enumerate(self.ranking):
            scores_ci = r.get("scores_ci") or {}
            d1_ci = scores_ci.get("D1") or {}
            d2_ci = scores_ci.get("D2") or {}
            d3_ci = scores_ci.get("D3") or {}
            rows.append(
                [
                    i + 1,
                    r["model"],
                    r["scores"].get("D1"),
                    d1_ci.get("low"),
                    d1_ci.get("high"),
                    r["scores"].get("D2"),
                    d2_ci.get("low"),
                    d2_ci.get("high"),
                    r["scores"].get("D3"),
                    d3_ci.get("low"),
                    d3_ci.get("high"),
                    r["composite"],
                    r.get("composite_ci_low"),
                    r.get("composite_ci_high"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        def _fmt_ci(ci: dict | None) -> str:
            if not ci:
                return "n/a"
            lo, hi = ci.get("low"), ci.get("high")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                return f"[{lo:.2f}, {hi:.2f}]"
            return "n/a"

        rows: list[list[object]] = []
        for i, r in enumerate(self.ranking):
            scores_ci = r.get("scores_ci") or {}
            comp_ci = {
                "low": r.get("composite_ci_low"),
                "high": r.get("composite_ci_high"),
            }
            rows.append(
                [
                    i + 1,
                    r["model"],
                    _fmt_score(r["scores"].get("D1")),
                    _fmt_ci(scores_ci.get("D1")),
                    _fmt_score(r["scores"].get("D2")),
                    _fmt_ci(scores_ci.get("D2")),
                    _fmt_score(r["scores"].get("D3")),
                    _fmt_ci(scores_ci.get("D3")),
                    f"{r['composite']:.2f}",
                    _fmt_ci(comp_ci),
                ]
            )
        return booktabs_table(
            [
                "Rank",
                "Model",
                "D1",
                "D1 95% CI",
                "D2",
                "D2 95% CI",
                "D3",
                "D3 95% CI",
                "Composite",
                "Composite 95% CI",
            ],
            "rlrlrlrlrl",
            rows,
        )
