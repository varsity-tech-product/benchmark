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

    eval_mode = getattr(result, "eval_mode", "full")

    # ── Header ──
    _a(f"# Score Report: {tid} / {pid}\n")
    mode_label = f" | **Eval Mode**: {eval_mode}" if eval_mode != "full" else ""
    _a(
        f"**Category**: {cat} | **Difficulty**: {diff} | **Timestamp**: {ts}{mode_label}\n"
    )

    # ── Overall Scores ──
    _a("## Overall Scores\n")
    _a("| Metric | Score |")
    _a("|--------|-------|")
    _a(f"| Overall Agent Score (OAS) | {_f(result.overall_score)} |")
    _a(f"| Quant Result (QR) | {_f(result.quant_result_score)} |")
    _a(f"| Quant Process (QP) | {_f(result.quant_process_score)} |")
    from evaluation.deepeval_metrics.tutor_conv_geval import compute_tutor_score

    tutor_avg = (
        compute_tutor_score(
            result.tutor_scores,
            category=getattr(result, "category", None),
            requires_code=getattr(result, "requires_code", False),
        )
        if result.tutor_scores
        else 0.0
    )
    _a(f"| Tutor Score (weighted 7D) | {_f(tutor_avg)} |")
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
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else 0.0


def _short_model(name: str) -> str:
    return name.split("/")[-1] if "/" in name else name


# ──────────────────────────────────────────────────────────────
# QR Section
# ──────────────────────────────────────────────────────────────


def _section_qr(lines, result):
    _a = lines.append

    # ── Code Eval ──
    ce = result.code_eval or {}
    ce_applicable = ce.get("applicable", False)
    ce_score = ce.get("score")

    summary_label = _f(ce_score) if ce_applicable else "N/A"
    _a(
        f"<details>\n<summary><b>Code Execution Eval</b> — Combined: {summary_label}</summary>\n"
    )

    if not ce_applicable:
        _a("Not applicable for this task.\n")
    else:
        sa = ce.get("static_analysis", {})
        ex = ce.get("execution", {})
        ov = ce.get("output_verification")

        _a("| Layer | Weight | Score | Diagnostics |")
        _a("|-------|--------|-------|-------------|")

        # Layer A
        if sa:
            diag_a = (
                f"syntax={'OK' if sa.get('syntax_valid') else 'FAIL'}, "
                f"files={sa.get('files_analyzed', 0)}, "
                f"funcs={sa.get('total_functions', 0)}"
            )
            _a(f"| A — Static Analysis | 15% | {_f(sa.get('score'))} | {diag_a} |")

        # Layer B
        if ex:
            diag_b = (
                f"calls={ex.get('exec_calls_found', 0)}, "
                f"success_rate={_f(ex.get('success_rate'), '.2f')}, "
                f"untested={ex.get('untested_files', [])}"
            )
            _a(f"| B — Execution | 35% | {_f(ex.get('score'))} | {diag_b} |")

        # Layer C
        if ov:
            diag_c = (
                f"accuracy={_f(ov.get('numerical_accuracy'), '.2f')}, "
                f"completeness={_f(ov.get('output_completeness'), '.2f')}, "
                f"metrics_compared={ov.get('metrics_compared', 0)}"
            )
            _a(f"| C — Output Verification | 50% | {_f(ov.get('score'))} | {diag_c} |")
        else:
            _a("| C — Output Verification | 50% | SKIP | no reference |")

    _a("\n</details>\n")

    # ── Programmatic Eval Checklist ──
    esd = result.eval_script_detail or {}
    checklist = esd.get("_checklist", [])
    prog_score = esd.get("score")
    ds_verified = esd.get("data_source_verified")

    _a(
        f"<details>\n<summary><b>Programmatic Eval (Eval Script)</b> — Score: {_f(prog_score)}</summary>\n"
    )

    if checklist:
        _a("| Check Item | Weight | Result | Weighted |")
        _a("|------------|--------|--------|----------|")
        for c in checklist:
            weight = c.get("weight", 0.0)
            if "score" in c:
                # Continuous score (e.g. behavioral_score, trial_efficiency)
                score_val = c["score"]
                result_str = f"{score_val:.3f}"
                weighted = weight * score_val
            else:
                passed = c.get("passed", False)
                result_str = "Pass" if passed else "Fail"
                weighted = weight if passed else 0.0
            _a(
                f"| {c.get('item', '')} | {weight:.2f} | {result_str} | {_f(weighted)} |"
            )
        raw_sum = sum(
            (
                c["weight"] * c["score"]
                if "score" in c
                else (c.get("weight", 0.0) if c.get("passed") else 0.0)
            )
            for c in checklist
        )
        _a(f"| **Sum (pre-cap)** | | | **{_f(raw_sum)}** |")

        if ds_verified is not None:
            ds_frac = esd.get("data_source_fraction", 1.0)
            ds_label = "Pass" if ds_verified else f"Fail (fraction={ds_frac:.2f})"
            _a(f"\n> Data source verification: {ds_label}")
            if not ds_verified:
                _a(
                    f"> Score capped: {_f(raw_sum)} x max(0.25, {ds_frac:.2f}) = {_f(prog_score)}"
                )
    else:
        _a("No checklist available.\n")

    _a("\n</details>\n")

    # ── Result Judge ──
    rj = result.result_judge or {}
    rj_score = rj.get("score")

    _a(
        f"<details>\n<summary><b>LLM Result Judge</b> — Score: {_f(rj_score)}</summary>\n"
    )
    _a(f"**Has reference**: {rj.get('has_reference', False)}\n")

    _DIM_WEIGHTS = {
        "completeness": "0.55",
        "correctness": "0.45",
    }
    sub = rj.get("sub_scores", {})
    per_model = rj.get("_per_model", {})
    model_names = sorted(per_model.keys()) if per_model else []

    if sub:
        header = "| Sub-dimension | Weight | Avg |"
        sep = "|---------------|--------|-----|"
        for m in model_names:
            header += f" {_short_model(m)} |"
            sep += "------|"
        _a(header)
        _a(sep)

        for dim in ("completeness", "correctness"):
            row = f"| {dim} | {_DIM_WEIGHTS[dim]} | {_f(sub.get(dim))} |"
            for m in model_names:
                ms = per_model[m].get("sub_scores", {}).get(dim)
                row += f" {_f(ms)} |"
            _a(row)

        row = f"| **Overall** | | **{_f(rj_score)}** |"
        for m in model_names:
            row += f" **{_f(per_model[m].get('score'))}** |"
        _a(row)

    reason = rj.get("reason", "")
    if reason:
        _a(f"\n> {reason}\n")

    _a("\n</details>\n")

    # ── QR Blending ──
    final_qr = result.quant_result_score
    eval_script_score = rj.get("_eval_script_score")
    dampening_factor = rj.get("_dampening_factor")
    code_eval_score = ce.get("score") if ce_applicable else None

    # When eval script returned None (insufficient signal), programmatic
    # weight was fully deferred to LLM Judge.
    prog_deferred = eval_script_score is None

    if prog_deferred:
        dampened_label = " (eval script deferred → 100% Judge)"
    elif dampening_factor is not None and dampening_factor < 0.9:
        dampened_label = f" (dampening={dampening_factor:.2f})"
    else:
        dampened_label = ""

    _a(
        f"<details>\n<summary><b>QR Blending</b> — Final: {_f(final_qr)}{dampened_label}</summary>\n"
    )

    _a("| Component | Raw Score | Weight | Weighted |")
    _a("|-----------|-----------|--------|----------|")

    if prog_deferred:
        _a("| Programmatic (eval script) | N/A (deferred) | 0% | — |")
        if ce_applicable and code_eval_score is not None:
            w_ce = 0.30
            w_judge = 0.70
            _a(
                f"| Code Eval | {_f(code_eval_score)} "
                f"| {w_ce:.0%} | {_f(code_eval_score * w_ce)} |"
            )
        else:
            w_judge = 1.0
        judge_raw = rj_score if rj_score is not None else 0.0
        _a(
            f"| LLM Result Judge | {_f(judge_raw)} "
            f"| {w_judge:.0%} | {_f(judge_raw * w_judge)} |"
        )
    else:
        # Standard blending with dampening
        df = dampening_factor if dampening_factor is not None else 1.0
        if ce_applicable:
            w_prog = 0.10 + 0.20 * df
            w_ce = 0.30
            w_judge = 1.0 - w_prog - w_ce
        else:
            w_prog = 0.15 + 0.25 * df
            w_ce = None
            w_judge = 1.0 - w_prog

        prog_raw = eval_script_score if eval_script_score is not None else 0.0
        _a(
            f"| Programmatic (eval script) | {_f(prog_raw)} "
            f"| {w_prog:.0%} | {_f(prog_raw * w_prog)} |"
        )
        if w_ce is not None and code_eval_score is not None:
            _a(
                f"| Code Eval | {_f(code_eval_score)} "
                f"| {w_ce:.0%} | {_f(code_eval_score * w_ce)} |"
            )
        judge_raw = rj_score if rj_score is not None else 0.0
        _a(
            f"| LLM Result Judge | {_f(judge_raw)} "
            f"| {w_judge:.0%} | {_f(judge_raw * w_judge)} |"
        )

    _a(f"| **Final QR** | | | **{_f(final_qr)}** |")

    dampened_active = (
        not prog_deferred and dampening_factor is not None and dampening_factor < 1.0
    )
    if dampened_active:
        divergence = abs(prog_raw - judge_raw)
        _a(
            f"\n> Divergence dampening: programmatic={_f(prog_raw)} vs "
            f"judge={_f(judge_raw)} "
            f"({chr(916)}={_f(divergence)}, factor={dampening_factor:.3f})"
        )
        # Show nominal vs actual weights for transparency
        if ce_applicable:
            nom_prog, nom_ce, nom_judge = "30%", "30%", "40%"
        else:
            nom_prog, nom_judge = "40%", "60%"
            nom_ce = None
        parts = [f"programmatic {nom_prog}{chr(8594)}{w_prog:.0%}"]
        if nom_ce is not None:
            parts.append(f"code_eval {nom_ce}{chr(8594)}{w_ce:.0%}")
        parts.append(f"judge {nom_judge}{chr(8594)}{w_judge:.0%}")
        _a(f"> Actual weights: {', '.join(parts)}")

    _a("\n</details>\n")


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
    "topic_adherence",
]

# Weights used in QP aggregate (must match process_metrics._QP_DIMENSION_WEIGHTS)
_QP_WEIGHTS = {
    "tool_usage": 0.20,
    "process_reasonableness": 0.20,
    "step_efficiency": 0.15,
    "code_process": 0.15,
    "process_alignment": 0.10,
    "role_adherence": 0.10,
    "topic_adherence": 0.10,
}

# Sub-dimension definitions for metrics that have them
_SUB_DIM_DEFS = {
    "step_efficiency": [
        ("action_economy", "0.40"),
        ("redundancy_avoidance", "0.30"),
        ("logical_sequencing", "0.30"),
    ],
    "process_reasonableness": [
        ("problem_decomposition", "0.30"),
        ("execution_soundness", "0.40"),
        ("error_handling", "0.30"),
    ],
    "process_alignment": [
        ("coverage", "0.40"),
        ("depth", "0.35"),
        ("soundness_delta", "0.25"),
    ],
}


def _section_qp(lines, result):
    _a = lines.append
    pm = result.process_metrics or {}
    per_model = pm.get("_per_model", {})
    model_names = sorted(per_model.keys()) if per_model else []

    # ── Summary table ──
    _a("### Summary\n")
    header = "| Metric | Weight | Avg |"
    sep = "|--------|--------|-----|"
    for m in model_names:
        header += f" {_short_model(m)} |"
        sep += "------|"
    _a(header)
    _a(sep)

    for mn in _QP_METRICS:
        v = pm.get(mn)

        weight_str = _QP_WEIGHTS.get(mn)
        if weight_str is not None:
            weight_str = f"{weight_str:.2f}"
        else:
            weight_str = "—"

        if v is None:
            avg_str = "—"
        elif isinstance(v, dict):
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

        row = f"| {mn} | {weight_str} | {avg_str} |"
        for m in model_names:
            ms = per_model[m].get(mn)
            if ms is not None and isinstance(ms, (int, float)):
                row += f" {_f(ms)} |"
            elif ms is not None:
                row += f" {ms} |"
            else:
                row += " — |"
        _a(row)

    # Aggregate row
    agg = pm.get("aggregate_process_score")
    row = f"| **Aggregate QP** | | **{_f(agg)}** |"
    for m in model_names:
        ma = per_model[m].get("aggregate_process_score")
        row += f" **{_f(ma)}** |"
    _a(row)
    _a("")

    # ── Sub-dimension detail sections (collapsible) ──

    # Tool Usage detail
    tu = pm.get("tool_usage") or result.tool_usage or {}
    if isinstance(tu, dict) and tu.get("score") is not None:
        tu_score = tu.get("score")
        _a(
            f"<details>\n<summary><b>Tool Usage Detail</b> — Score: {_f(tu_score)}</summary>\n"
        )
        _a("| Component | Weight | Score |")
        _a("|-----------|--------|-------|")
        _a(f"| Selection Score | 60% | {_f(tu.get('selection_score'))} |")
        _a(f"| Effectiveness | 40% | {_f(tu.get('effectiveness'))} |")
        _a("")
        _a("| Diagnostic | Value |")
        _a("|------------|-------|")
        base = tu.get("base")
        base_note = " (has convenient)" if base is not None and base < 1.0 else ""
        _a(f"| Base | {_f(base, '.2f')}{base_note} |")
        _a(f"| Bonus | +{_f(tu.get('bonus'))} |")
        _a(f"| Penalty (missing expected) | {_f(tu.get('penalty_expected'))} |")
        _a(f"| Penalty (distractor) | {_f(tu.get('penalty_distractor'))} |")
        _a(f"| Called convenient | {_fmt_list(tu.get('called_convenient'))} |")
        _a(f"| Missing expected | {_fmt_list(tu.get('missing_expected'))} |")
        _a(f"| Called distractors | {_fmt_list(tu.get('called_distractors'))} |")
        _a(f"| Ineffective expected | {_fmt_list(tu.get('ineffective_expected'))} |")
        _a("\n</details>\n")

    # Step Efficiency / Process Reasonableness / Process Alignment detail
    for metric_key, sub_dims in _SUB_DIM_DEFS.items():
        v = pm.get(metric_key)
        if not isinstance(v, dict) or v.get("score") is None:
            continue
        sub = v.get("sub_scores", {})
        if not sub:
            continue

        display_name = metric_key.replace("_", " ").title()
        metric_score = v.get("score")
        _a(
            f"<details>\n<summary><b>{display_name} Detail</b> — Score: {_f(metric_score)}</summary>\n"
        )

        header = "| Sub-dimension | Weight | Avg |"
        sep = "|---------------|--------|-----|"
        for m in model_names:
            header += f" {_short_model(m)} |"
            sep += "------|"
        _a(header)
        _a(sep)

        for dim_key, dim_weight in sub_dims:
            avg_val = sub.get(dim_key)
            row = f"| {dim_key} | {dim_weight} | {_f(avg_val)} |"
            for m in model_names:
                pm_sub = (per_model[m].get("_sub_scores") or {}).get(metric_key, {})
                mv = (pm_sub or {}).get(dim_key)
                row += f" {_f(mv)} |"
            _a(row)

        # Extra context for step_efficiency
        if metric_key == "step_efficiency":
            agent_steps = v.get("agent_substantive_steps")
            ref_steps = v.get("reference_step_count")
            if agent_steps is not None:
                note = f"\n> Agent steps: {agent_steps}"
                if ref_steps is not None:
                    note += f" | Reference steps: {ref_steps}"
                _a(note)

        # Extra context for process_alignment
        if metric_key == "process_alignment":
            pt = v.get("path_tolerance")
            if pt is not None:
                _a(f"\n> Path tolerance: {pt:.2f}")

        _a("\n</details>\n")

    # Code Process detail
    cp = result.code_process or {}
    if cp.get("applicable"):
        cp_score = cp.get("score")
        _a(
            f"<details>\n<summary><b>Code Process Detail</b> — Score: {_f(cp_score)}</summary>\n"
        )

        _a("| Component | Weight | Score |")
        _a("|-----------|--------|-------|")
        prog = cp.get("programmatic", {})
        if prog and prog.get("applicable"):
            _a(f"| Programmatic | 50% | {_f(prog.get('score'))} |")
        llm = cp.get("llm_judged", {})
        if llm and llm.get("applicable", True) and llm.get("score") is not None:
            _a(f"| LLM-judged | 50% | {_f(llm.get('score'))} |")
        _a(f"| **Combined** | | **{_f(cp_score)}** |")
        _a("")

        # Programmatic sub-scores
        if prog and prog.get("applicable"):
            psub = prog.get("sub_scores", {})
            _a("**Programmatic sub-scores:**\n")
            _a("| Metric | Score |")
            _a("|--------|-------|")
            for k in (
                "iterative_refinement",
                "test_before_deliver",
                "error_recovery",
                "code_evolution",
            ):
                _a(f"| {k.replace('_', ' ').title()} | {_f(psub.get(k))} |")
            _a("")

        # LLM-judged sub-scores
        if llm and llm.get("applicable", True) and llm.get("score") is not None:
            lsub = llm.get("sub_scores", {})
            if lsub:
                _a("**LLM-judged sub-scores:**\n")
                _a("| Metric | Score |")
                _a("|--------|-------|")
                for k in (
                    "debugging_competence",
                    "incremental_development",
                    "code_explanation_quality",
                ):
                    _a(f"| {k.replace('_', ' ').title()} | {_f(lsub.get(k))} |")
                _a("")

        _a("</details>\n")


def _fmt_list(items) -> str:
    """Format a list of items as inline code or '—' if empty."""
    if not items:
        return "—"
    return ", ".join(f"`{x}`" for x in items)


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

    # Average row (weighted by category)
    from evaluation.deepeval_metrics.tutor_conv_geval import compute_tutor_score

    _cat = getattr(result, "category", None)
    _rc = getattr(result, "requires_code", False)
    tutor_avg = compute_tutor_score(ts, category=_cat, requires_code=_rc) if ts else 0.0
    row = f"| **Average (weighted)** | {_f(tutor_avg)} |"
    for m in model_names:
        m_scores = by_model[m]
        m_avg = (
            compute_tutor_score(m_scores, category=_cat, requires_code=_rc)
            if m_scores
            else 0.0
        )
        row += f" {_f(m_avg)} |"
    _a(row)
    _a("")

    # Per-dimension reasons (judge explanations)
    dim_reasons = ts.get("_dim_reasons", {})
    if dim_reasons:
        _a("<details>")
        _a("<summary><b>Judge Reasons (per dimension)</b></summary>\n")
        for dim in _TUTOR_DIMS:
            reason = dim_reasons.get(dim, "")
            if reason:
                _a(f"**{dim}**: {reason}\n")
        _a("</details>\n")

    # Fallback recovery note
    fb_count = getattr(result, "tutor_fallback_count", 0)
    if fb_count > 0:
        _a(f"> **Note**: {fb_count} dimension evaluation(s) used fallback recovery\n")

    # Tutor eval error note
    tutor_err = getattr(result, "tutor_eval_error", None)
    if tutor_err:
        _a(f"> **Tutor eval error**: `{tutor_err}`\n")
