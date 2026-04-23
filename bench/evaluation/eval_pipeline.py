"""Compatibility wrapper for the v6 evaluation coordinator."""


def evaluate_task(
    *,
    task,
    persona,
    workspace_path: str,
    conversation: list[dict],
    tool_logs: list,
    distractor_names: list[str],
    bench_root: str,
    eval_model: str,
    cancel_event=None,
    eval_mode: str = "full",
    tutor_dims: list[str] | None = None,
) -> dict:
    from server.eval.core.coordinator import evaluate_tracks

    return evaluate_tracks(
        task=task,
        persona=persona,
        workspace_path=workspace_path,
        conversation=conversation,
        tool_logs=tool_logs,
        distractor_names=distractor_names,
        bench_root=bench_root,
        eval_model=eval_model,
        cancel_event=cancel_event,
        eval_mode=eval_mode,
        tutor_dims=tutor_dims,
    )


__all__ = ["evaluate_task"]
