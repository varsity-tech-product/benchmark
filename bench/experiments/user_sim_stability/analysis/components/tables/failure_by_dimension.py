"""Cross-dimension failure mini-table Component (paper-side).

Rolls the per-dimension failure mix (S1 / S3 / S2) into a single five-column
table so the appendix carries one failure-mix table instead of three. Each
row is one ``(dimension, failure_type)`` pair, ordered by descending count
within each dimension.
"""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)

_DIMENSIONS = ("S1", "S3", "S2")


class FailureByDimension(Component):
    name = "failure_by_dimension"

    def __init__(self, taxonomy_stats: dict | None):
        self.tax = taxonomy_stats or {}

    def _rows(self) -> list[tuple[str, str, int, float | None, int]]:
        by_dim = self.tax.get("by_dimension") or {}
        severity = self.tax.get("severity") or {}
        out: list[tuple[str, str, int, float | None, int]] = []
        for dim in _DIMENSIONS:
            failures = by_dim.get(dim) or {}
            for ft, count in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0])):
                sev_block = severity.get(ft) or {}
                mean_sev = sev_block.get("mean")
                n_sev = sev_block.get("n", 0)
                out.append(
                    (
                        dim,
                        ft,
                        int(count),
                        float(mean_sev) if isinstance(mean_sev, (int, float)) else None,
                        int(n_sev) if isinstance(n_sev, (int, float)) else 0,
                    )
                )
        return out

    def render_html(self) -> str:
        rows = self._rows()
        if not rows:
            return (
                "<table><tr><th>Dimension</th><th>Failure type</th>"
                "<th>Count</th><th>Mean severity</th><th>N</th></tr>"
                "<tr><td colspan='5'>No failure-type labels emitted on S1-S2.</td>"
                "</tr></table>"
            )
        body = "".join(
            f"<tr><td>{dim}</td><td>{ft}</td>"
            f"<td>{count}</td>"
            f"<td>{(mean_sev if mean_sev is not None else 0):.2f}</td>"
            f"<td>{n_sev}</td></tr>"
            for dim, ft, count, mean_sev, n_sev in rows
        )
        return (
            "<table><tr><th>Dimension</th><th>Failure type</th>"
            "<th>Count</th><th>Mean severity</th><th>N</th></tr>"
            f"{body}</table>"
        )

    def render_csv(self) -> bytes:
        out: list[list[object]] = [
            ["dimension", "failure_type", "count", "mean_severity", "n"]
        ]
        for dim, ft, count, mean_sev, n_sev in self._rows():
            out.append([dim, ft, count, mean_sev, n_sev])
        return csv_bytes(out)

    def render_tex(self) -> str | None:
        rows = self._rows()
        if not rows:
            return None
        body: list[list[object]] = []
        for dim, ft, count, mean_sev, n_sev in rows:
            body.append(
                [
                    dim,
                    ft,
                    count,
                    f"{mean_sev:.2f}" if isinstance(mean_sev, (int, float)) else "--",
                    n_sev,
                ]
            )
        return booktabs_table(
            ["Dimension", "Failure type", "Count", "Mean severity", "N"],
            "llrrr",
            body,
        )
