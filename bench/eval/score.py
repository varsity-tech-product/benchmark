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
from eval.contracts.bundle import Bundle, ConversationTurn
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
        persona: Pre-loaded persona object (otherwise resolved from
            ``bundle.persona_id``).
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

    conversation = _bundle_to_flat_conversation(bundle_obj.conversation)
    tool_logs = _bundle_to_flat_tool_logs(bundle_obj.conversation)

    request = EvalRequest(
        session_id=bundle_obj.session.session_id,
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
            "session_id": bundle_obj.session.session_id,
            "session_status": "completed",
            "termination_reason": bundle_obj.session.termination_reason,
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


def _bundle_to_flat_conversation(turns: list[ConversationTurn]) -> list[dict]:
    """Flatten Bundle turns back to the {role, content, ts} list shape the
    coordinator pipeline reads."""
    flat: list[dict] = []
    for turn in turns:
        if turn.student is not None:
            flat.append({"role": "user", "content": turn.student.text, "ts": ""})
        if turn.agent.text:
            flat.append({"role": "assistant", "content": turn.agent.text, "ts": ""})
    return flat


def _bundle_to_flat_tool_logs(turns: list[ConversationTurn]) -> list[dict]:
    """Reconstruct flat tool_logs from per-turn tool_calls plus a synthetic
    ``send_message`` entry per turn so downstream code that filters on
    ``send_message`` continues to work."""
    flat: list[dict] = []
    for turn in turns:
        idx = turn.turn - 1
        flat.append(
            {
                "name": "send_message",
                "args": {
                    "text": turn.agent.text,
                    "attachments": list(turn.agent.attachments),
                },
                "call_id": f"send_message_{idx}",
                "result": "",
                "timestamp": 0.0,
                "duration_ms": 0.0,
                "success": True,
                "turn_index": idx,
            }
        )
        for tc in turn.tool_calls:
            flat.append(
                {
                    "name": tc.tool,
                    "args": dict(tc.args),
                    "call_id": tc.call_id,
                    "result": tc.result_preview,
                    "timestamp": 0.0,
                    "duration_ms": tc.duration_ms,
                    "success": tc.success,
                    "turn_index": idx,
                }
            )
    return flat
