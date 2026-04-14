"""Shared helpers for populating TaskResult after evaluation."""

_EVAL_STAGES = [
    ("Tutor 7D", "tutor_scores"),
    ("Process Metrics", "process_metrics"),
    ("Result Judge", "result_judge"),
]


def populate_eval_results(
    result, eval_results: dict, category: str, requires_code: bool, eval_mode: str
) -> None:
    """Copy evaluation outputs into a TaskResult and compute overall score.

    Args:
        result: TaskResult instance to populate.
        eval_results: Dict returned by ``_evaluate_task`` / ``eval_single_job``.
        category: Task category value (e.g. "data_analysis").
        requires_code: Whether the task requires code execution.
        eval_mode: "full", "qr_only", or "qp_only".
    """
    result.quant_result_score = eval_results.get("quant_result", 0.0)
    result.quant_process_score = eval_results.get("quant_process", 0.0)
    result.tutor_scores = eval_results.get("tutor_scores", {})
    result.tutor_scores_by_model = eval_results.get("tutor_scores_by_model", {})
    result.tutor_fallback_count = eval_results.get("tutor_fallback_count", 0)
    result.tutor_eval_error = eval_results.get("tutor_eval_error")
    result.process_metrics = eval_results.get("process_metrics", {})
    result.eval_script_detail = eval_results.get("eval_script_detail", {})
    result.code_eval = eval_results.get("code_eval", {})
    result.result_judge = eval_results.get("result_judge", {})
    result.code_process = eval_results.get("process_metrics", {}).get(
        "code_process", {}
    )
    result.tool_usage = eval_results.get("tool_usage", {})
    result.eval_mode = eval_mode

    from server.eval.scoring import compute_task_score

    score_breakdown = compute_task_score(
        quant_result_score=result.quant_result_score,
        quant_process_score=result.quant_process_score,
        tutor_dimension_scores=result.tutor_scores,
        category=category,
        requires_code=requires_code,
        eval_mode=eval_mode,
    )
    result.overall_score = score_breakdown["overall_score"]


def aggregate_eval_cost(result) -> tuple[float, dict, dict]:
    """Compute eval cost totals from result fields.

    Returns:
        (eval_cost, eval_cost_by_model, eval_cost_by_stage_model)
    """
    eval_cost = 0.0
    eval_cost_by_model: dict[str, float] = {}
    eval_cost_by_stage_model: dict[str, dict[str, float]] = {}
    for stage_name, attr in _EVAL_STAGES:
        src = getattr(result, attr, None) or {}
        eval_cost += src.get("_eval_cost", 0.0)
        by_model = src.get("_eval_cost_by_model", {})
        if by_model:
            eval_cost_by_stage_model[stage_name] = {
                m: round(c, 6) for m, c in by_model.items()
            }
        for m, c in by_model.items():
            eval_cost_by_model[m] = round(eval_cost_by_model.get(m, 0.0) + c, 6)
    return eval_cost, eval_cost_by_model, eval_cost_by_stage_model


def build_token_usage(
    agent_input: int,
    agent_output: int,
    agent_cost: float,
    api_calls: int,
    agent_model: str,
    sim_cost: float,
    sim_model: str,
    eval_cost: float,
    eval_cost_by_model: dict,
    eval_cost_by_stage_model: dict,
) -> dict:
    """Build the ``result.token_usage`` dict and return (token_usage, total_cost)."""
    total = agent_cost + sim_cost + eval_cost
    token_usage = {
        "agent": {
            "input_tokens": agent_input,
            "output_tokens": agent_output,
            "cost_usd": round(agent_cost, 6),
            "api_calls": api_calls,
            "model": agent_model,
        },
        "simulator": {
            "cost_usd": round(sim_cost, 6),
            "model": sim_model,
        },
        "eval": {
            "cost_usd": round(eval_cost, 6),
            "by_model": eval_cost_by_model,
            "by_stage_model": eval_cost_by_stage_model,
        },
        "total": {
            "cost_usd": round(total, 6),
        },
    }
    return token_usage, round(total, 4)
