"""Human-vs-judge disagreement examples Component (rendered as <ul>)."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    csv_bytes,
    tex_escape,
)


class HumanAlignmentDisagreements(Component):
    name = "human_alignment_disagreements"

    def __init__(self, examples: list[dict] | None):
        self.examples = examples or []

    def render_html(self) -> str:
        items = "".join(
            f"<li><code>{item.get('eval_id')}</code> "
            f"({item.get('category', 'label')}): "
            f"human={item.get('human_score')}, "
            f"judge={item.get('judge_score')}, "
            f"abs diff={item.get('abs_diff')}</li>"
            for item in self.examples[:5]
        )
        items = items or "<li>No disagreement examples available.</li>"
        return f"<h3>Disagreement Examples</h3><ul>{items}</ul>"

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            [
                "eval_id",
                "category",
                "dimension",
                "human_score",
                "judge_score",
                "abs_diff",
                "human_comment",
            ]
        ]
        for item in self.examples:
            rows.append(
                [
                    item.get("eval_id"),
                    item.get("category"),
                    item.get("dimension"),
                    item.get("human_score"),
                    item.get("judge_score"),
                    item.get("abs_diff"),
                    item.get("human_comment", ""),
                ]
            )
        return csv_bytes(rows)

    def render_tex(self) -> str:
        """Render as a LaTeX itemize list (the HTML form is a <ul>)."""
        items = self.examples[:5]
        if not items:
            return (
                r"\begin{itemize}\item No disagreement examples available.\end{itemize}"
            )
        lines = [r"\begin{itemize}"]
        for item in items:
            line = (
                f"\\item \\texttt{{{tex_escape(item.get('eval_id'))}}} "
                f"({tex_escape(item.get('category', 'label'))}): "
                f"human={tex_escape(item.get('human_score'))}, "
                f"judge={tex_escape(item.get('judge_score'))}, "
                f"abs diff={tex_escape(item.get('abs_diff'))}"
            )
            lines.append(line)
        lines.append(r"\end{itemize}")
        return "\n".join(lines)
