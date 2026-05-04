"""Cross-vendor candidate-selection ranking table (S1+S3+S2+Composite)."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
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
    """S1+S3+S2+Composite ranking with bootstrap 95% CI next to each mean."""

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
                f'  <td class="{_score_class(r["scores"]["S1"])}">{_fmt_score(r["scores"]["S1"])}</td>'
                f"{_ci_cell(scores_ci.get('S1'))}\n"
                f'  <td class="{_score_class(r["scores"]["S3"])}">{_fmt_score(r["scores"]["S3"])}</td>'
                f"{_ci_cell(scores_ci.get('S3'))}\n"
                f'  <td class="{_score_class(r["scores"]["S2"])}">{_fmt_score(r["scores"]["S2"])}</td>'
                f"{_ci_cell(scores_ci.get('S2'))}\n"
                f'  <td class="{_score_class(r["composite"])}">{r["composite"]:.2f}</td>'
                f"{_ci_cell(composite_ci)}</tr>"
            )
        return (
            "<table><tr><th>Rank</th><th>Model</th>"
            "<th>S1</th><th>S1 95% CI</th>"
            "<th>S3</th><th>S3 95% CI</th>"
            "<th>S2</th><th>S2 95% CI</th>"
            "<th>Composite</th><th>Composite 95% CI</th></tr>\n"
            f"{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            [
                "rank",
                "model",
                "S1",
                "D1_ci_low",
                "D1_ci_high",
                "S3",
                "D2_ci_low",
                "D2_ci_high",
                "S2",
                "D3_ci_low",
                "D3_ci_high",
                "composite",
                "composite_ci_low",
                "composite_ci_high",
            ]
        ]
        for i, r in enumerate(self.ranking):
            scores_ci = r.get("scores_ci") or {}
            d1_ci = scores_ci.get("S1") or {}
            d2_ci = scores_ci.get("S3") or {}
            d3_ci = scores_ci.get("S2") or {}
            rows.append(
                [
                    i + 1,
                    r["model"],
                    r["scores"].get("S1"),
                    d1_ci.get("low"),
                    d1_ci.get("high"),
                    r["scores"].get("S3"),
                    d2_ci.get("low"),
                    d2_ci.get("high"),
                    r["scores"].get("S2"),
                    d3_ci.get("low"),
                    d3_ci.get("high"),
                    r["composite"],
                    r.get("composite_ci_low"),
                    r.get("composite_ci_high"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        rows: list[list[object]] = []
        for i, r in enumerate(self.ranking):
            rows.append(
                [
                    i + 1,
                    r["model"],
                    _fmt_score(r["scores"].get("S1")),
                    _fmt_score(r["scores"].get("S3")),
                    _fmt_score(r["scores"].get("S2")),
                ]
            )
        return booktabs_table(
            ["Rank", "Model", "S1", "S3", "S2"],
            "rlrrr",
            rows,
        )
