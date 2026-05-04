"""Human-LLM alignment metrics table Component.

Reads ``agreement_report.json::agreement_metrics`` and emits one row per
calibration label field.
"""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    booktabs_table,
    csv_bytes,
)

_NUMERIC_KEYS = (
    "persona_fidelity",
    "knowledge_boundary_pass",
    "emotional_match",
    "drift_onset_turn",
    "control_distinctiveness",
    "p1_facet_fit",
)


class HumanAlignmentMetrics(Component):
    name = "human_alignment_metrics"

    def __init__(self, agreement_metrics: dict | None):
        self.metrics = agreement_metrics or {}

    def render_html(self) -> str:
        metric_rows = ""
        metrics = self.metrics
        for name in _NUMERIC_KEYS:
            value = metrics.get(name)
            if not value:
                continue
            metric_rows += (
                f"<tr><td>{name}</td>"
                f"<td>MAD {value.get('mean_absolute_difference', 0):.2f}; "
                f"within one point {value.get('within_one_point_rate', 0):.1%}</td>"
                f"<td>{value.get('n', 0)}</td></tr>"
            )

        failure = metrics.get("failure_type")
        if failure:
            metric_rows += (
                f"<tr><td>failure_type</td>"
                f"<td>match {failure.get('exact_or_contained_match_rate', 0):.1%}</td>"
                f"<td>{failure.get('n', 0)}</td></tr>"
            )

        signals = metrics.get("p1_expected_signals_recall")
        if signals:
            metric_rows += (
                f"<tr><td>p1_expected_signals_recall</td>"
                f"<td>mean recall {signals.get('mean_recall', 0):.1%}</td>"
                f"<td>{signals.get('n', 0)}</td></tr>"
            )

        set_a = metrics.get("control_persona_set_a_accuracy")
        if set_a:
            metric_rows += (
                f"<tr><td>control_persona_set_a_accuracy</td>"
                f"<td>accuracy {set_a.get('accuracy', 0):.1%}</td>"
                f"<td>{set_a.get('n', 0)}</td></tr>"
            )

        if not metric_rows:
            metric_rows = (
                "<tr><td colspan='3'>No human labels have been scored yet.</td></tr>"
            )

        return (
            "<table><tr><th>Label Field</th><th>Agreement</th><th>N</th></tr>"
            f"{metric_rows}</table>"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [["label_field", "metric", "value", "n"]]
        metrics = self.metrics
        for name in _NUMERIC_KEYS:
            value = metrics.get(name)
            if not value:
                continue
            rows.append(
                [
                    name,
                    "mean_absolute_difference",
                    value.get("mean_absolute_difference"),
                    value.get("n"),
                ]
            )
            rows.append(
                [
                    name,
                    "within_one_point_rate",
                    value.get("within_one_point_rate"),
                    value.get("n"),
                ]
            )
        failure = metrics.get("failure_type")
        if failure:
            rows.append(
                [
                    "failure_type",
                    "exact_or_contained_match_rate",
                    failure.get("exact_or_contained_match_rate"),
                    failure.get("n"),
                ]
            )
        signals = metrics.get("p1_expected_signals_recall")
        if signals:
            rows.append(
                [
                    "p1_expected_signals_recall",
                    "mean_recall",
                    signals.get("mean_recall"),
                    signals.get("n"),
                ]
            )
        set_a = metrics.get("control_persona_set_a_accuracy")
        if set_a:
            rows.append(
                [
                    "control_persona_set_a_accuracy",
                    "accuracy",
                    set_a.get("accuracy"),
                    set_a.get("n"),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        metrics = self.metrics
        rows: list[list[object]] = []
        for name in _NUMERIC_KEYS:
            value = metrics.get(name)
            if not value:
                continue
            agreement = (
                f"MAD {value.get('mean_absolute_difference', 0):.2f}; "
                f"within 1pt {value.get('within_one_point_rate', 0):.1%}"
            )
            rows.append([name, agreement, value.get("n", 0)])
        failure = metrics.get("failure_type")
        if failure:
            rows.append(
                [
                    "failure_type",
                    f"match {failure.get('exact_or_contained_match_rate', 0):.1%}",
                    failure.get("n", 0),
                ]
            )
        signals = metrics.get("p1_expected_signals_recall")
        if signals:
            rows.append(
                [
                    "p1_expected_signals_recall",
                    f"mean recall {signals.get('mean_recall', 0):.1%}",
                    signals.get("n", 0),
                ]
            )
        set_a = metrics.get("control_persona_set_a_accuracy")
        if set_a:
            rows.append(
                [
                    "control_persona_set_a_accuracy",
                    f"accuracy {set_a.get('accuracy', 0):.1%}",
                    set_a.get("n", 0),
                ]
            )
        if not rows:
            rows = [["No human labels have been scored yet.", "", ""]]
        return booktabs_table(["Label field", "Agreement", "N"], "llr", rows)
