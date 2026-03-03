"""Score Report Generator for QuantTutorBench.

Produces a detailed Markdown report containing every scoring dimension,
per-model LLM-as-judge breakdowns, and programmatic metric results for
a single task execution.

Usage:
    from evaluation.score_report import generate_score_report
    md_text = generate_score_report(task_result)
    Path("scores/report.md").write_text(md_text)
"""

from datetime import datetime
from typing import Optional


def generate_score_report(
    result,
    task_id: Optional[str] = None,
    persona_id: Optional[str] = None,
) -> str:
    """Generate a comprehensive Markdown score report from a TaskResult.

    Args:
        result: TaskResult object (from schemas.py).
        task_id: Override task_id (uses result.task_id if None).
        persona_id: Override persona_id (uses result.persona_id if None).

    Returns:
        Complete Markdown string.
    """
    tid = task_id or getattr(result, "task_id", "unknown")
    pid = persona_id or getattr(result, "persona_id", "unknown")
    cat = getattr(result, "category", "")
    diff = getattr(result, "difficulty", "")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    _a = lines.append  # shorthand

    # ── Header ──
    _a(f"# Score Report: {tid} / {pid}\n")
    _a(f"**Category**: {cat} | **Difficulty**: {diff} | **Timestamp**: {ts}\n")

    # ── Overall Scores ──
    _a("## Overall Scores\n")
    _a("| Metric | Score |")
    _a("|--------|-------|")
    _a(f"| Overall Agent Score (OAS) | {_f(result.overall_score)} |")
    _a(f"| Quant Result (QR) | {_f(result.quant_result_score)} |")
    _a(f"| Quant Process (QP) | {_f(result.quant_process_score)} |")
    tutor_avg = _safe_mean(result.tutor_scores.values()) if result.tutor_scores else 0.0
    _a(f"| Tutor Score (avg 7D) | {_f(tutor_avg)} |")
    _a("")

    # ── QR Breakdown ──
    _a("## Quant Result (QR) Breakdown\n")
    _section_qr(lines, result)

    # ── QP Breakdown ──
    _a("## Quant Process (QP) Breakdown\n")
    _section_qp(lines, result)

    # ── Tutor 7D ──
    _a("## Tutor Quality (7D) Breakdown\n")
    _section_tutor(lines, result)

    # ── Workspace Files ──
    ws = getattr(result, "workspace_files", [])
    if ws:
        _a("## Workspace Files\n")
        for f in ws:
            _a(f"- {f}")
        _a("")

    # ── Sandbox Info ──
    si = getattr(result, "sandbox_info", {})
    if si:
        _a("## Sandbox Info\n")
        for k, v in si.items():
            _a(f"- **{k}**: {v}")
        _a("")

    # ── Error ──
    if result.error:
        _a(f"## Error\n\n```\n{result.error}\n```\n")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _f(v, fmt=".4f") -> str:
    """Format a float, or return '—' for None."""
    if v is None:
        return "—"
    try:
        return f"{float(v):{fmt}}"
    except (TypeError, ValueError):
        return str(v)


def _safe_mean(vals) -> float:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _short_model(name: str) -> str:
    return name.split("/")[-1] if "/" in name else name


# ──────────────────────────────────────────────────────────────
# QR Section
# ──────────────────────────────────────────────────────────────


def _section_qr(lines, result):
    _a = lines.append

    # Code eval
    ce = result.code_eval or {}
    ce_applicable = ce.get("applicable", False)
    _a("### Code Execution Eval\n")
    _a(f"- **Applicable**: {ce_applicable}")
    if ce_applicable:
        _a(f"- **Combined score**: {_f(ce.get('score'))}")
        sa = ce.get("static_analysis", {})
        if sa:
            _a(
                f"- Layer A (static): {_f(sa.get('score'))} — "
                f"syntax={'OK' if sa.get('syntax_valid') else 'FAIL'}, "
                f"files={sa.get('files_analyzed', 0)}, funcs={sa.get('total_functions', 0)}"
            )
        ex = ce.get("execution", {})
        if ex:
            _a(
                f"- Layer B (execution): {_f(ex.get('score'))} — "
                f"calls={ex.get('exec_calls_found', 0)}, "
                f"success_rate={_f(ex.get('success_rate'), '.2f')}, "
                f"untested={ex.get('untested_files', [])}"
            )
        ov = ce.get("output_verification")
        if ov:
            _a(
                f"- Layer C (output): {_f(ov.get('score'))} — "
                f"accuracy={_f(ov.get('numerical_accuracy'), '.2f')}, "
                f"completeness={_f(ov.get('output_completeness'), '.2f')}, "
                f"metrics_compared={ov.get('metrics_compared', 0)}"
            )
        else:
            _a("- Layer C (output): SKIPPED (no reference)")
    _a("")

    # Result judge
    rj = result.result_judge or {}
    _a("### LLM Result Judge\n")
    _a(f"- **Score (avg)**: {_f(rj.get('score'))}")
    _a(f"- **Has reference**: {rj.get('has_reference', False)}")

    sub = rj.get("sub_scores", {})
    per_model = rj.get("_per_model", {})
    model_names = sorted(per_model.keys()) if per_model else []

    if sub:
        # Build table header
        header = "| Sub-dimension | Avg |"
        sep = "|---------------|-----|"
        for m in model_names:
            header += f" {_short_model(m)} |"
            sep += "------|"
        _a(header)
        _a(sep)

        for dim in ("numerical_accuracy", "completeness", "correctness"):
            row = f"| {dim} | {_f(sub.get(dim))} |"
            for m in model_names:
                ms = per_model[m].get("sub_scores", {}).get(dim)
                row += f" {_f(ms)} |"
            _a(row)

        # Overall row
        row = f"| **Overall** | {_f(rj.get('score'))} |"
        for m in model_names:
            row += f" {_f(per_model[m].get('score'))} |"
        _a(row)
    _a("")

    reason = rj.get("reason", "")
    if reason:
        _a(f"> {reason[:300]}\n")

    # Blending
    _a("### QR Blending\n")
    programmatic = result.quant_result_score  # final blended
    _a(f"- **Final QR**: {_f(programmatic)}")
    if ce_applicable:
        _a("- Formula: 30% programmatic + 30% code_eval + 40% LLM judge")
    else:
        _a("- Formula: 40% programmatic + 60% LLM judge")
    _a("")


# ──────────────────────────────────────────────────────────────
# QP Section
# ──────────────────────────────────────────────────────────────

_QP_METRICS = [
    "tool_usage",
    "step_efficiency",
    "process_reasonableness",
    "process_alignment",
    "code_process",
    "role_adherence",
    "knowledge_retention",
    "topic_adherence",
]


def _section_qp(lines, result):
    _a = lines.append
    pm = result.process_metrics or {}
    per_model = pm.get("_per_model", {})
    model_names = sorted(per_model.keys()) if per_model else []

    # Table header
    header = "| Metric | Avg |"
    sep = "|--------|-----|"
    for m in model_names:
        header += f" {_short_model(m)} |"
        sep += "------|"
    _a(header)
    _a(sep)

    for mn in _QP_METRICS:
        v = pm.get(mn)
        if v is None:
            continue
        if isinstance(v, dict):
            sc = v.get("score")
            skipped = v.get("skipped", False)
            if skipped:
                avg_str = "SKIP"
            elif sc is not None:
                avg_str = _f(sc)
            else:
                avg_str = "—"
        elif isinstance(v, (int, float)):
            avg_str = _f(v)
        else:
            avg_str = str(v)

        row = f"| {mn} | {avg_str} |"
        for m in model_names:
            ms = per_model[m].get(mn)
            if ms is not None and isinstance(ms, (int, float)):
                row += f" {_f(ms)} |"
            elif ms is not None:
                row += f" {ms} |"
            else:
                row += " — |"
        _a(row)

    # Aggregate
    agg = pm.get("aggregate_process_score")
    row = f"| **Aggregate** | {_f(agg)} |"
    for m in model_names:
        ma = per_model[m].get("aggregate_process_score")
        row += f" {_f(ma)} |"
    _a(row)
    _a("")

    # Code process detail (if applicable)
    cp = result.code_process or {}
    if cp.get("applicable"):
        _a("### Code Process Detail\n")
        _a(f"- **Combined score**: {_f(cp.get('score'))}")
        prog = cp.get("programmatic", {})
        if prog and prog.get("applicable"):
            psub = prog.get("sub_scores", {})
            parts = ", ".join(
                f"{k}={_f(psub.get(k))}"
                for k in (
                    "iterative_refinement",
                    "test_before_deliver",
                    "error_recovery",
                    "code_evolution",
                )
            )
            _a(f"- Programmatic ({_f(prog.get('score'))}): {parts}")
        llm = cp.get("llm_judged", {})
        if llm and llm.get("applicable"):
            lsub = llm.get("sub_scores", {})
            parts = ", ".join(
                f"{k}={_f(lsub.get(k))}"
                for k in (
                    "debugging_competence",
                    "incremental_development",
                    "code_explanation_quality",
                )
            )
            _a(f"- LLM-judged ({_f(llm.get('score'))}): {parts}")
        _a("")


# ──────────────────────────────────────────────────────────────
# Tutor 7D Section
# ──────────────────────────────────────────────────────────────

_TUTOR_DIMS = [
    "D1_level_detection",
    "D2_language_adaptation",
    "D3_scaffolding_calibration",
    "D4_domain_accuracy",
    "D5_code_teaching",
    "D6_empathetic_response",
    "D7_safety_boundaries",
]


def _section_tutor(lines, result):
    _a = lines.append
    ts = result.tutor_scores or {}
    by_model = result.tutor_scores_by_model or {}
    model_names = sorted(by_model.keys()) if by_model else []

    # Table header
    header = "| Dimension | Avg |"
    sep = "|-----------|-----|"
    for m in model_names:
        header += f" {_short_model(m)} |"
        sep += "------|"
    _a(header)
    _a(sep)

    for dim in _TUTOR_DIMS:
        avg_val = ts.get(dim)
        row = f"| {dim} | {_f(avg_val)} |"
        for m in model_names:
            mv = by_model[m].get(dim)
            row += f" {_f(mv)} |"
        _a(row)

    # Average row
    tutor_avg = _safe_mean(ts.values()) if ts else 0.0
    row = f"| **Average** | {_f(tutor_avg)} |"
    for m in model_names:
        m_scores = by_model[m]
        m_avg = _safe_mean(m_scores.values()) if m_scores else 0.0
        row += f" {_f(m_avg)} |"
    _a(row)
    _a("")
