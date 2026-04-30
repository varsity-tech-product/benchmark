"""Per-dimension failure mini-table Component (S1 / S3 / S2 inline)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)


class FailureInline(Component):
    """Small failure-type table filtered to one dimension.

    Embedded at the end of each S1 / S3 / S2 section so the reader sees the
    failure mix that drives the score on that dimension without leaving the
    section to consult the full Methodology Appendix cross-tab.
    """

    def __init__(self, taxonomy_stats: dict | None, dimension: str):
        self.tax = taxonomy_stats or {}
        self.dimension = dimension
        self.name = f"failure_inline_{dimension.lower()}"

    def _failures(self) -> dict[str, int]:
        by_dim = self.tax.get("by_dimension") or {}
        return by_dim.get(self.dimension) or {}

    def render_html(self) -> str:
        failures = self._failures()
        severity = self.tax.get("severity") or {}
        if not failures:
            return (
                "<table><tr><th>Failure type</th><th>Count</th>"
                "<th>Mean severity</th></tr>"
                f"<tr><td colspan='3'>No failure-type labels emitted on {self.dimension}.</td></tr>"
                "</table>"
            )
        rows = "".join(
            f"<tr><td>{ft}</td>"
            f"<td>{count}</td>"
            f"<td>{(severity.get(ft, {}) or {}).get('mean', 0):.2f}</td></tr>"
            for ft, count in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return (
            "<table><tr><th>Failure type</th><th>Count</th>"
            f"<th>Mean severity</th></tr>{rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["dimension", "failure_type", "count", "mean_severity"]
        ]
        severity = self.tax.get("severity") or {}
        for ft, count in sorted(self._failures().items()):
            rows.append(
                [
                    self.dimension,
                    ft,
                    count,
                    (severity.get(ft, {}) or {}).get("mean"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        severity = self.tax.get("severity") or {}
        failures = self._failures()
        if not failures:
            return booktabs_table(
                ["Failure type", "Count", "Mean severity"],
                "lrr",
                [[f"No failure-type labels emitted on {self.dimension}.", "", ""]],
            )
        rows: list[list[object]] = []
        for ft, count in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0])):
            mean = (severity.get(ft, {}) or {}).get("mean", 0)
            rows.append([ft, count, f"{mean:.2f}"])
        return booktabs_table(
            ["Failure type", "Count", "Mean severity"],
            "lrr",
            rows,
        )
