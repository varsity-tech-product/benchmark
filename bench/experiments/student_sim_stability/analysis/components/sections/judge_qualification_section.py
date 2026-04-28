"""§A — Judge Qualification gate, full section Component."""

from __future__ import annotations

import os
from pathlib import Path

from experiments.student_sim_stability.analysis.components.base import (
    Component,
    _fmt_score,
    _html,
)
from experiments.student_sim_stability.analysis.components.tables.judge_qualification import (
    JudgeQualification,
)
from experiments.student_sim_stability.core.io_utils import load_json
from experiments.student_sim_stability.judge_qualification.render import (
    DEFAULT_GATE_RESULTS_DIR,
)


class JudgeQualificationSection(Component):
    """Methodology Appendix §A — full block.

    Composes the inner ``JudgeQualification`` tables Component, plus the
    insight pill, qualification cards, and artifact-link rubric. Self-
    contained so Phase 4 can dump it as a single paper-export unit.
    """

    name = "judge_qualification_section"

    def __init__(self, gate_stats: dict | None, output_dir: Path):
        self.gate = gate_stats or {}
        self.output_dir = Path(output_dir)
        self._inner = JudgeQualification(gate_stats)

    def render_html(self) -> str:
        gate = self.gate
        if not gate.get("available"):
            status = gate.get("status", "not_run")
            detail = gate.get("error") or gate.get("path", "")
            return f"""<h2>A. Judge Qualification (gate &mdash; reference)</h2>
<div class="insight">
  <span class="status-pill status-warn">{_html(status)}</span>
  The fixed judge-qualification reliability/sensitivity check has not been loaded for this report.
  Run <code>judge-qualification report</code> after rendering and judging the gate inputs.
  <br><strong>Expected stats path:</strong> <code>{_html(detail)}</code>
</div>"""

        ok = bool(gate.get("ok"))
        status_class = "status-pass" if ok else "status-fail"
        counts = gate.get("counts", {})
        stability = gate.get("stability", {})
        prompt_format = gate.get("prompt_format", {})
        sensitivity = gate.get("sensitivity", {})
        failure_tags = gate.get("failure_tags", {})
        b1_identity = gate.get("b1_identity", {})
        gate_dir = Path(gate.get("gate_dir") or DEFAULT_GATE_RESULTS_DIR)
        cost = self._safe_load_json(gate_dir / "report" / "llm_cost_estimate.json")
        judge_run = self._safe_load_json(gate_dir / "report" / "judge_run_stats.json")
        estimated_cost = cost.get("estimated_total_cost_usd")
        actual_cost = judge_run.get("cost_usd")
        report_paths = gate.get("report_paths", {})
        html_href = self._artifact_href(
            report_paths.get("html")
            or gate_dir / "report" / "judge_qualification_report.html"
        )
        md_href = self._artifact_href(
            report_paths.get("markdown")
            or gate_dir / "report" / "judge_qualification_report.md"
        )
        json_href = self._artifact_href(
            report_paths.get("stats_json")
            or gate_dir / "report" / "judge_qualification_stats.json"
        )

        failed_checks = [
            key for key, value in gate.get("gate_checks", {}).items() if not value
        ]
        failed_text = (
            ", ".join(f"<code>{_html(key)}</code>" for key in failed_checks)
            if failed_checks
            else "none"
        )
        tables_html = self._inner.render_html()
        return f"""<h2>A. Judge Qualification (gate &mdash; reference)</h2>
<div class="insight">
  <span class="status-pill {status_class}">{'pass' if ok else 'fail'}</span>
  Fixed corpus judge reliability/sensitivity gate. This should be read before the main experiment metrics because it validates that the judge prompt and fixed persona checks are stable enough to trust downstream scores.
  <br><strong>Corpus version:</strong> <code>{_html(gate.get('corpus_version'))}</code>
  <br><strong>Failed checks:</strong> {failed_text}
  <br><strong>Artifacts:</strong>
  <a href="{_html(html_href)}">HTML</a>,
  <a href="{_html(md_href)}">Markdown</a>,
  <a href="{_html(json_href)}">JSON</a>
</div>
<div class="card-row">
  <div class="card"><div class="val">{_html(counts.get('valid_records'))}/{_html(counts.get('records'))}</div><div class="lbl">Valid Gate Records</div></div>
  <div class="card"><div class="val">{_html(counts.get('missing_outputs'))}</div><div class="lbl">Missing Outputs</div></div>
  <div class="card"><div class="val">{_fmt_score(stability.get('within_one_score_rate'))}</div><div class="lbl">Same-Prompt Within-One</div></div>
  <div class="card"><div class="val">{_fmt_score(stability.get('pass_fail_flip_rate'))}</div><div class="lbl">Pass/Fail Flip Rate</div></div>
  <div class="card"><div class="val">{_fmt_score(prompt_format.get('within_one_variant_rate'))}</div><div class="lbl">Prompt-Format Within-One</div></div>
  <div class="card"><div class="val">{_fmt_score(sensitivity.get('pass_rate'))}</div><div class="lbl">Sensitivity Pass Rate</div></div>
  <div class="card"><div class="val">{_fmt_score(failure_tags.get('hit_rate'))}</div><div class="lbl">Failure-Tag Hit Rate</div></div>
  <div class="card"><div class="val">{_fmt_score(b1_identity.get('match_rate'))}</div><div class="lbl">B1 Identity Match</div></div>
</div>
<div class="rubric">
<strong>Cost:</strong> actual judge run = {_fmt_score(actual_cost)} USD; estimate = {_fmt_score(estimated_cost)} USD.
</div>
{tables_html}"""

    def render_csv(self) -> bytes | None:
        return self._inner.render_csv()

    def _artifact_href(self, path: str | Path | None) -> str:
        if not path:
            return "#"
        artifact = Path(path)
        if not artifact.is_absolute():
            artifact = self.output_dir / artifact
        return Path(os.path.relpath(artifact, start=self.output_dir)).as_posix()

    @staticmethod
    def _safe_load_json(path: Path) -> dict:
        return load_json(path) if path.exists() else {}
