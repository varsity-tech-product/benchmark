#!/usr/bin/env python3
"""Drive a QuantTutorBench REST session with an agent-in-the-loop.

Reference driver for engineers wiring their own LLM tutor into the
benchmark. Replace ``compose_reply`` with a real model call; the rest of
the file shows the REST lifecycle and terminal-status handling.

For the full agent-in-loop reference (auth, termination semantics,
workspace tools, common pitfalls), read the project skill at
``.claude/skills/quanttutorbench-agent/SKILL.md``. Issue #57.

Usage:
    # Default target is the production VPS — see docs/agent_in_loop.md
    python bench/scripts/agent_in_loop_example.py --task I02 --persona developer_crossover

    # Override only when running a local dev server (python -m server --port 8765 --docker)
    python bench/scripts/agent_in_loop_example.py --task I02 --base-url http://localhost:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://217-15-165-83.sslip.io"  # production VPS — see docs/agent_in_loop.md
CLIENT_INFO = {"name": "agent_in_loop_example", "version": "0.1.0"}
TERMINAL = {"completed", "failed"}


def compose_reply(student_message: str, history: list[dict[str, str]]) -> str:
    """Extension point: replace this stub with your LLM tutor call.

    A production version sends ``history`` plus the latest
    ``student_message`` to OpenAI, Anthropic, Gemini, or another model and
    returns the tutor's next message. This stub stays deterministic and
    varies each turn so the server's repeat detector sees a real loop.

    Pitfall: returning the same string three turns in a row trips the
    server's repeat-detector and ends the session with
    ``reason=agent_stuck``. Always derive the reply from the actual
    ``student_message`` content.
    """
    turn = 1 + sum(1 for item in history if item["role"] == "tutor")
    focus = " ".join(student_message.split())[:180] or "the current task"
    return (
        f"Turn {turn}: I read your latest note: {focus}\n\n"
        "For the next step, keep the work concrete: identify the strategy state, "
        "write or inspect the implementation, run the relevant check, and share "
        "the output you observe."
    )


def _repo_bench_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_local_bundle(session_id: str, bench_root: Path) -> Path | None:
    short_id = session_id[:8]
    results_root = bench_root / "results" / "server"
    if not results_root.is_dir():
        return None
    matches = sorted(results_root.glob(f"*/*/*_{short_id}"))
    for path in matches:
        if (path / "manifest.json").is_file():
            return path
    return None


async def _post_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.post(path, json=json_body, headers=headers or {})
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"{path} -> HTTP {response.status_code}: {response.text}")
    return response.json()


async def run_session(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    history: list[dict[str, str]] = []
    turn_count = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        run = await _post_json(
            client,
            "/client/runs/start",
            json_body={"task": args.task, "client": CLIENT_INFO},
        )
        run_id = run["run_id"]
        token = run["token"]
        auth = {"Authorization": f"Bearer {token}"}
        print(f"run_id={run_id} task={run.get('public_task_label', args.task)}")

        register_body: dict[str, str] = {}
        if args.persona:
            register_body["persona_id"] = args.persona
        registration = await _post_json(
            client, "/session/register", json_body=register_body, headers=auth
        )
        session_id = registration["session_id"]
        print(f"session_id={session_id}")

        started = await _post_json(
            client, f"/session/{session_id}/start", json_body={}, headers=auth
        )
        background = started.get("background", "")
        student_message = started.get("student_message", "")
        history.append({"role": "system", "content": background})
        history.append({"role": "student", "content": student_message})
        status = started.get("status", "active")

        print(f"student: {student_message[:240]}")
        while status not in TERMINAL:
            turn_count += 1
            if turn_count > args.safety_turn_cap:
                # Hard kill so a misbehaving compose_reply (or a server
                # that never terminates) cannot loop forever. The bundle
                # still persists via the DELETE-with-persist path (#54).
                print(
                    f"safety-cap turn={turn_count} > {args.safety_turn_cap} "
                    "— DELETEing session"
                )
                await client.delete(f"/session/{session_id}", headers=auth)
                break
            tutor_text = compose_reply(student_message, history)
            history.append({"role": "tutor", "content": tutor_text})
            print(f"tutor: {tutor_text.splitlines()[0]}")

            turn = await _post_json(
                client,
                f"/session/{session_id}/send",
                json_body={"text": tutor_text},
                headers=auth,
            )
            status = turn.get("status", "active")
            reason = turn.get("reason", "")
            student_message = turn.get("student_message", "")
            if student_message:
                history.append({"role": "student", "content": student_message})
                print(f"student: {student_message[:240]}")
            print(f"status={status}" + (f" reason={reason}" if reason else ""))

        bundle = _find_local_bundle(session_id, args.bench_root)
        if bundle:
            print(f"bundle={bundle}")
        else:
            print(f"bundle=check server results for session {session_id}")
        # Eval is out-of-band; the agent loop never triggers it. Print
        # the command so the operator knows what to run next.
        print(
            f"score it later: python -m server.evaluator --bundle <bundle_dir> "
            f"  # session_id={session_id}"
        )
        return 0 if status == "completed" else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "Server URL. Defaults to production VPS "
            f"({DEFAULT_BASE_URL}). Override with http://localhost:8765 "
            "only when running a local dev server."
        ),
    )
    parser.add_argument("--task", default="D01")
    parser.add_argument("--persona", default="")
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help=(
            "Per-call HTTP timeout in seconds. /session/{sid}/send can "
            "take 5-15 s on heavy student-sim turns; default 900 leaves "
            "headroom without masking real hangs."
        ),
    )
    parser.add_argument(
        "--safety-turn-cap",
        type=int,
        default=50,
        help=(
            "Hard kill after this many turns even if the server has not "
            "terminated. Higher than any task.max_turns. Default: 50."
        ),
    )
    parser.add_argument(
        "--bench-root",
        type=Path,
        default=_repo_bench_root(),
        help="Local bench/ path used only to locate persisted bundles.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return asyncio.run(run_session(args))
    except (httpx.HTTPError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"error={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
