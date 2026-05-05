"""Standalone scoring entry — `eval.score(bundle) → EvalOutput`.

This is the "scoring is a pure function" surface from #123. It runs the
same coordinator + track pipeline as the server's REST handler, but does
not persist any score files. Suitable for CI smoke checks, ad-hoc
notebook scoring, and batch backfill — anywhere ``bench/`` is on
``sys.path`` and ``bench/server/`` does not need to be importable.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from eval.contracts import bundle_io
from eval.contracts.bundle import Bundle, Message, ToolCall
from eval.contracts.output import EvalOutput
from eval.contracts.request import EvalRequest
from eval.core.coordinator import EvalCoordinator, load_persona_by_id, load_task_by_id


def score(
    bundle: Bundle | Path | str,
    *,
    bench_root: Path | str,
    task: Any = None,
    persona: Any = None,
    eval_mode: str = "full",
    eval_model: str | None = None,
    workspace_path: Path | str | None = None,
) -> EvalOutput:
    """Score one bundle and return its :class:`EvalOutput`.

    Args:
        bundle: A loaded Bundle, or a path to ``bundle.json``.
        bench_root: Repo ``bench/`` root used to locate task and persona JSON.
        task: Pre-loaded task object (otherwise resolved from
            ``bundle.task_id``). Pass an in-memory object to avoid disk I/O.
        persona: Pre-loaded persona object (otherwise resolved from the
            QuantTutor artifact persona id).
        eval_mode: ``"full"`` (default), ``"qr"``, or ``"qp"``.
        eval_model: Override judge model (otherwise the eval default).
        workspace_path: Directory containing the agent's workspace files.
            QR's code_eval and tool_usage paths read this; bundles whose
            workspace files are not on disk will degrade those tracks
            gracefully (no error, just lower-fidelity QR).

    Returns:
        :class:`EvalOutput` with ``qr`` and ``qp`` track results. No files
        are written.
    """
    bundle_obj = (
        bundle if isinstance(bundle, Bundle) else bundle_io.read(Path(str(bundle)))
    )
    bench_root = Path(bench_root)

    if task is None:
        task = load_task_by_id(bench_root, bundle_obj.task_id)
    if persona is None:
        persona = load_persona_by_id(bench_root, bundle_obj.persona_id)

    conversation = _bundle_to_flat_conversation(bundle_obj.messages)
    tool_logs = _bundle_to_flat_tool_logs(bundle_obj.messages, bundle_obj.tool_calls)

    request = EvalRequest(
        session_id=bundle_obj.session_id,
        eval_mode=eval_mode,
        eval_model=eval_model,
    )

    coordinator = EvalCoordinator(bench_root=bench_root)
    with tempfile.TemporaryDirectory() as scratch:
        result_dir = Path(scratch)
        ws_path = (
            str(Path(workspace_path)) if workspace_path else str(result_dir)
        )
        run_state = {
            "task_id": bundle_obj.task_id,
            "persona_id": bundle_obj.persona_id,
            "session_id": bundle_obj.session_id,
            "session_status": "completed",
            "termination_reason": bundle_obj.termination_reason,
            "conversation": conversation,
            "tool_logs": tool_logs,
            "workspace_path": ws_path,
        }
        # preflight checks for run_state.json on disk; materialize it into
        # the scratch directory (never the caller's workspace) so the
        # standalone path passes preflight without polluting user files.
        (result_dir / "run_state.json").write_text(
            json.dumps(run_state), encoding="utf-8"
        )
        return coordinator.run(
            request=request,
            result_dir=result_dir,
            score_id="standalone",
            task=task,
            persona=persona,
            run_state=run_state,
            conversation=conversation,
            tool_logs=tool_logs,
            persist=False,
        )


def _bundle_to_flat_conversation(messages: list[Message]) -> list[dict]:
    """Flatten Bundle messages back to the {role, content, ts} list shape the
    coordinator pipeline reads."""
    flat: list[dict] = []
    for message in messages:
        flat.append(
            {
                "role": message.role,
                "content": _content_to_text(message.content),
                "ts": message.created_at,
            }
        )
    return flat


def _bundle_to_flat_tool_logs(
    messages: list[Message],
    tool_calls: list[ToolCall],
) -> list[dict]:
    """Reconstruct flat tool_logs for the coordinator pipeline."""
    flat: list[dict] = []
    has_send_message = False
    for tc in tool_calls:
        if tc.tool_name == "send_message":
            has_send_message = True
        flat.append(
            {
                "name": tc.tool_name,
                "args": tc.args if isinstance(tc.args, dict) else {},
                "call_id": tc.tool_call_id,
                "result": tc.result,
                "timestamp": tc.created_at,
                "duration_ms": tc.duration_ms or 0.0,
                "success": True if tc.success is None else tc.success,
                "turn_index": tc.turn_index or 0,
            }
        )
    if has_send_message:
        return flat
    for idx, message in enumerate(m for m in messages if m.role == "assistant"):
        turn_index = message.turn_index if message.turn_index is not None else idx
        flat.append(
            {
                "name": "send_message",
                "args": {
                    "text": _content_to_text(message.content),
                    "attachments": list(message.attachments),
                },
                "call_id": f"send_message_{turn_index}",
                "result": "",
                "timestamp": message.created_at,
                "duration_ms": 0.0,
                "success": True,
                "turn_index": turn_index,
            }
        )
    return flat


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)
