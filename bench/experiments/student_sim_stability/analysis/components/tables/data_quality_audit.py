"""Data-quality audit table Component."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _html,
    booktabs_table,
    csv_bytes,
)


class DataQualityAudit(Component):
    name = "data_quality_audit"

    def __init__(self, audit: dict | None):
        self.audit = audit or {}

    def render_html(self) -> str:
        checks = self.audit.get("checks") or []
        row_parts = []
        for item in checks[:20]:
            ok = bool(item.get("ok"))
            status = "pass" if ok else "fail"
            row_parts.append(
                "<tr>"
                f'<td><span class="status-pill status-{status}">{status}</span></td>'
                f"<td><code>{_html(item.get('name'))}</code></td>"
                f"<td>{_html(item.get('message'))}</td>"
                "</tr>"
            )
        rows = "".join(row_parts)
        if not rows:
            rows = "<tr><td colspan='3'>Audit artifact not available.</td></tr>"
        return (
            '<div class="table-wrap"><table class="audit-table">'
            "<tr><th>Status</th><th>Check</th><th>Message</th></tr>"
            f"{rows}</table></div>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [["status", "name", "message"]]
        for item in self.audit.get("checks") or []:
            ok = bool(item.get("ok"))
            rows.append(
                ["pass" if ok else "fail", item.get("name"), item.get("message")]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        rows: list[list[object]] = []
        for item in (self.audit.get("checks") or [])[:20]:
            ok = bool(item.get("ok"))
            rows.append(
                [
                    "pass" if ok else "fail",
                    item.get("name", ""),
                    item.get("message", ""),
                ]
            )
        if not rows:
            rows = [["", "Audit artifact not available.", ""]]
        return booktabs_table(
            ["Status", "Check", "Message"],
            "llp{8cm}",
            rows,
        )
