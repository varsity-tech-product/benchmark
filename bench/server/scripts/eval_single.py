"""Run or read one server evaluation by session id.

Examples:
    python -m server.scripts.eval_single --session SESSION_ID --mode full
    python -m server.scripts.eval_single get --session SESSION_ID --score score_2
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from server.config.llm_config import EVAL_DEFAULT_MODEL
from server.eval.contracts.request import (
    EvalError,
    parse_eval_request,
    parse_score_query,
    resolve_result_dir,
)
from server.eval.core.coordinator import (
    EvalCoordinator,
    load_persona_by_id,
    load_run_state,
    load_task_by_id,
)
from server.storage.score_store import allocate_score_run, get_scores_payload

BENCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = BENCH_ROOT / "results" / "server"


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _install_sigint_handler(cancel_event: threading.Event):
    previous = signal.getsignal(signal.SIGINT)
    state = {"seen": False}

    def handler(signum, frame):
        if state["seen"]:
            raise KeyboardInterrupt("Second SIGINT received")
        state["seen"] = True
        cancel_event.set()
        print("Interrupted: finishing current save path...", file=sys.stderr)

    signal.signal(signal.SIGINT, handler)
    return previous


def _run(args: argparse.Namespace) -> int:
    from server.config.bootstrap import load_server_env

    request = parse_eval_request(
        args,
        eval_mode=getattr(args, "mode", None) or getattr(args, "eval_mode", None),
    )
    bench_root = Path(args.bench_root)
    load_server_env(bench_root)
    results_root = Path(args.results_root)
    cancel_event = threading.Event()
    previous_handler = _install_sigint_handler(cancel_event)

    result_dir = None
    score_id = None
    created_at = None
    try:
        result_dir = resolve_result_dir(request.session_id, results_root)
        run, created = allocate_score_run(
            result_dir,
            eval_mode=request.eval_mode,
            eval_model=request.eval_model,
            tutor_dims=request.tutor_dims,
            idempotency_key=request.idempotency_key,
        )
        score_id = run.score_id
        created_at = run.created_at
        if not created:
            _json_print({"status": "running", "score_id": run.score_id})
            return 0

        try:
            run_state = load_run_state(result_dir)
        except Exception as exc:  # noqa: BLE001 - persist terminal hard failure
            from server.storage.eval_writer import save_terminal_eval_result

            message = f"run_state.json could not be loaded: {exc}"
            payload = save_terminal_eval_result(
                result_dir=result_dir,
                score_id=score_id,
                eval_mode=request.eval_mode,
                eval_model=request.eval_model,
                created_at=created_at,
                status="failed",
                error=message,
                preflight={
                    "hard_errors": [
                        {
                            "code": "run_state_invalid",
                            "message": message,
                        }
                    ],
                    "track_blockers": {"qr": [], "qp": [], "tutor": []},
                    "skipped_dependencies": [],
                },
            )
            _json_print(payload)
            return 2

        task = load_task_by_id(bench_root, run_state.get("task_id"))
        persona = load_persona_by_id(bench_root, run_state.get("persona_id"))
        outcome = EvalCoordinator(bench_root=bench_root).run(
            request=request,
            result_dir=result_dir,
            score_id=score_id,
            task=task,
            persona=persona,
            run_state=run_state,
            created_at=created_at,
            cancel_event=cancel_event,
            persist=True,
        )
        _json_print(outcome.to_summary())
        if outcome.score_status == "failed":
            return 2
        if outcome.score_status == "interrupted":
            return 130
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def _get(args: argparse.Namespace) -> int:
    query = parse_score_query(args)

    result_dir = resolve_result_dir(query.session_id, Path(args.results_root))
    payload = get_scores_payload(
        result_dir,
        history=query.history,
        score_id=query.score_id,
        score_ids=query.score_ids,
        status_filter=query.status_filter,
    )
    payload["session_id"] = query.session_id
    _json_print(payload)
    return 0


def _list(args: argparse.Namespace) -> int:
    results_root = Path(args.results_root)
    rows: list[tuple[str, str, str, str]] = []
    if results_root.is_dir():
        for run_dir in sorted(results_root.glob("*/*/*")):
            if not run_dir.is_dir():
                continue
            sid_file = run_dir / ".session_id"
            if not sid_file.exists():
                continue
            session_id = sid_file.read_text(encoding="utf-8").strip()
            task_id = run_dir.parent.parent.name
            persona_id = run_dir.parent.name
            status = "pending"
            index_path = run_dir / "evaluations" / "index.json"
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    scores = index.get("scores") or []
                    if scores:
                        status = str(scores[-1].get("status") or status)
                except json.JSONDecodeError:
                    status = "index_invalid"
            rows.append((session_id[:12], task_id, persona_id, status))

    print(f"{'SESSION_ID':<14} {'TASK':<32} {'PERSONA':<28} STATUS")
    for sid, task_id, persona_id, status in rows:
        print(f"{sid:<14} {task_id[:32]:<32} {persona_id[:28]:<28} {status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate one server result by session id"
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--session", "--session-id", dest="session_id", required=True)
        p.add_argument("--bench-root", default=str(BENCH_ROOT))
        p.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))

    parser.add_argument("--session", "--session-id", dest="session_id", required=False)
    parser.add_argument("--bench-root", default=str(BENCH_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--mode", "--eval-mode", dest="mode", default="full")
    parser.add_argument("--eval-model", default=EVAL_DEFAULT_MODEL)
    parser.add_argument("--tutor-dims", default=None)
    parser.add_argument("--idempotency-key", default=None)
    parser.add_argument("--list", action="store_true", help="list evaluable sessions")

    run_p = sub.add_parser("run", help="append a new score_n run")
    add_common(run_p)
    run_p.add_argument("--mode", "--eval-mode", dest="mode", default="full")
    run_p.add_argument("--eval-model", default=EVAL_DEFAULT_MODEL)
    run_p.add_argument("--tutor-dims", default=None)
    run_p.add_argument("--idempotency-key", default=None)

    get_p = sub.add_parser("get", help="read score_n output")
    add_common(get_p)
    get_p.add_argument("--score", "--score-id", dest="score_id", default=None)
    get_p.add_argument("--scores", "--score-ids", dest="score_ids", default=None)
    get_p.add_argument("--history", action="store_true")
    get_p.add_argument("--status", dest="status_filter", default=None)

    list_p = sub.add_parser("list", help="list evaluable sessions")
    list_p.add_argument("--bench-root", default=str(BENCH_ROOT))
    list_p.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "run"):
            if getattr(args, "list", False):
                return _list(args)
            return _run(args)
        if args.command in ("get", "list"):
            if args.command == "list":
                return _list(args)
            return _get(args)
        parser.error(f"Unknown command: {args.command}")
        return 2
    except EvalError as exc:
        _json_print({"status": "failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
