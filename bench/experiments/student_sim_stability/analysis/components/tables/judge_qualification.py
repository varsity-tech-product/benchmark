"""Judge-qualification gate tables Component (Methodology Appendix §A).

Renders the two tables that close the qualification block: gate-checks pass/fail
and residual numeric deltas. The surrounding insight / card-row / rubric blocks
remain in the section so the Component can be exported as a self-contained
appendix table set.
"""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _fmt_score,
    _html,
    booktabs_table,
    csv_bytes,
)


class JudgeQualification(Component):
    name = "judge_qualification"

    def __init__(self, gate_stats: dict | None):
        self.gate = gate_stats or {}

    def render_html(self) -> str:
        gate = self.gate
        if not gate.get("available"):
            return ""
        check_rows = "".join(
            "<tr>"
            f"<td><code>{_html(key)}</code></td>"
            f"<td><span class=\"status-pill {'status-pass' if value else 'status-fail'}\">"
            f"{'pass' if value else 'fail'}</span></td>"
            "</tr>"
            for key, value in gate.get("gate_checks", {}).items()
        )
        residual_groups = [
            row
            for row in (gate.get("stability") or {}).get("groups", [])
            if row.get("pass_fail_flip") or not row.get("within_one")
        ][:8]
        residual_rows = "".join(
            "<tr>"
            f"<td><code>{_html(row.get('sample_id'))}</code></td>"
            f"<td>{_html(row.get('dimension'))}</td>"
            f"<td>{_html(row.get('prompt_variant_id'))}</td>"
            f"<td>{_fmt_score(row.get('max_delta'))}</td>"
            f"<td>{_html(row.get('pass_fail_flip'))}</td>"
            f"<td><code>{_html(row.get('scores'))}</code></td>"
            "</tr>"
            for row in residual_groups
        )
        if not residual_rows:
            residual_rows = '<tr><td colspan="6">No pass/fail flips or above-one score deltas.</td></tr>'
        return (
            "<h3>Judge Qualification Checks</h3>\n"
            f"<table><tr><th>Check</th><th>Status</th></tr>{check_rows}</table>\n"
            "<h3>Residual Numeric Deltas</h3>\n"
            "<table><tr><th>Sample</th><th>Dimension</th><th>Variant</th>"
            f"<th>Max Delta</th><th>Flip</th><th>Scores</th></tr>{residual_rows}</table>"
        )

    def render_csv(self) -> bytes:
        gate = self.gate
        rows: list[list[object]] = [["section", "key", "value_or_pass_fail"]]
        for key, value in (gate.get("gate_checks") or {}).items():
            rows.append(["gate_check", key, "pass" if value else "fail"])
        residual_groups = [
            row
            for row in (gate.get("stability") or {}).get("groups", [])
            if row.get("pass_fail_flip") or not row.get("within_one")
        ]
        for row in residual_groups:
            rows.append(
                [
                    "residual_delta",
                    row.get("sample_id"),
                    f"dim={row.get('dimension')}; variant={row.get('prompt_variant_id')}; max_delta={row.get('max_delta')}; flip={row.get('pass_fail_flip')}",
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str | None:
        gate = self.gate
        if not gate.get("available"):
            return None
        check_rows = [
            [key, "pass" if value else "fail"]
            for key, value in (gate.get("gate_checks") or {}).items()
        ]
        check_table = booktabs_table(
            ["Check", "Status"], "ll", check_rows or [["(no checks)", ""]]
        )
        residual_groups = [
            row
            for row in (gate.get("stability") or {}).get("groups", [])
            if row.get("pass_fail_flip") or not row.get("within_one")
        ][:8]
        residual_rows = []
        for row in residual_groups:
            residual_rows.append(
                [
                    row.get("sample_id"),
                    row.get("dimension"),
                    row.get("prompt_variant_id"),
                    _fmt_score(row.get("max_delta")),
                    str(row.get("pass_fail_flip")),
                    str(row.get("scores")),
                ]
            )
        if not residual_rows:
            residual_rows = [
                ["No pass/fail flips or above-one score deltas.", "", "", "", "", ""]
            ]
        residual_table = booktabs_table(
            ["Sample", "Dimension", "Variant", "Max Delta", "Flip", "Scores"],
            "lllrll",
            residual_rows,
        )
        return f"{check_table}\n\n{residual_table}"
