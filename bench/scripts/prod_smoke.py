#!/usr/bin/env python3
"""External availability smoke test for the QuantTutorBench prod VPS.

Drives every public HTTP endpoint in a single session-scoped flow and
asserts each responds with a non-5xx status inside the per-request
deadline. Availability only — does not validate payload semantics.

The flow creates one throwaway run + session, hits every endpoint that
depends on them, then DELETEs the session in a ``finally`` block so we
do not leak containers on the VPS.

Usage::

    python bench/scripts/prod_smoke.py
    python bench/scripts/prod_smoke.py --base-url https://benchmark-liard.vercel.app
    python bench/scripts/prod_smoke.py --jsonl /tmp/prod_smoke.jsonl

Exit 0 on full pass; 1 if any endpoint is unreachable, returns 5xx, or
exceeds the per-call timeout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

DEFAULT_BASE = "https://217-15-165-83.sslip.io"
DEFAULT_TASK = "X01"
PER_REQUEST_TIMEOUT_S = 30.0
# How long to wait for the test's run_lean_backtest job to reach a
# terminal state before proceeding to DELETE. Garbage args should make
# it fail within a second or two.
JOB_TERMINAL_DEADLINE_S = 20.0


@dataclass
class SmokeResult:
    rows: list[dict] = field(default_factory=list)
    failures: int = 0
    jsonl_path: Optional[str] = None

    def record(
        self,
        phase: str,
        method: str,
        path: str,
        status: int,
        ok: bool,
        duration_ms: float,
        detail: str = "",
    ) -> None:
        row = {
            "phase": phase,
            "method": method,
            "path": path,
            "status": status,
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "detail": detail,
        }
        self.rows.append(row)
        if not ok:
            self.failures += 1
        marker = "PASS" if ok else "FAIL"
        print(
            f"[{marker}] {method:6s} {path:62s} "
            f"{status:>4}  {row['duration_ms']:>7.1f}ms  {detail}"
        )
        if self.jsonl_path:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


async def hit(
    client: httpx.AsyncClient,
    result: SmokeResult,
    phase: str,
    method: str,
    path: str,
    *,
    expect: str = "non5xx",
    **kwargs,
) -> Optional[httpx.Response]:
    """Send a request and record the outcome.

    ``expect`` drives the pass/fail rule:
    - ``non5xx``: anything 1xx/2xx/3xx/4xx is "available". Used for most
      endpoints where availability means "the router reached a handler
      and the handler returned without blowing up".
    - ``2xx``: strict success, used for critical-path gates (run
      creation, session register) where a non-2xx blocks downstream.
    - ``202``: async-dispatch check for heavy tools.
    """
    start = time.time()
    try:
        resp = await client.request(method, path, **kwargs)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        duration = (time.time() - start) * 1000
        result.record(
            phase, method, path, 0, False, duration,
            f"{type(exc).__name__}: {exc}",
        )
        return None

    duration = (time.time() - start) * 1000
    if expect == "2xx":
        ok = 200 <= resp.status_code < 300
    elif expect == "202":
        ok = resp.status_code == 202
    else:
        ok = resp.status_code < 500

    detail = ""
    if not ok:
        try:
            detail = json.dumps(resp.json())[:120]
        except (ValueError, json.JSONDecodeError):
            detail = resp.text[:120]

    result.record(phase, method, path, resp.status_code, ok, duration, detail)
    return resp


async def wait_for_job_terminal(
    client: httpx.AsyncClient, sid: str, job_id: str, headers: dict | None = None
) -> None:
    """Poll the job until it reaches a terminal state or deadline expires.

    Does not record pass/fail — this is just cleanup so DELETE does not
    block behind the session lock the job worker holds.
    """
    deadline = time.time() + JOB_TERMINAL_DEADLINE_S
    while time.time() < deadline:
        try:
            resp = await client.get(
                f"/session/{sid}/tool/jobs/{job_id}",
                headers=headers or {},
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return
        if resp.status_code != 200:
            return
        if resp.json().get("status") in ("completed", "failed"):
            return
        await asyncio.sleep(1.0)


async def run_smoke(
    base_url: str,
    task_label: str,
    jsonl: Optional[str],
    api_key: str = "",
) -> int:
    result = SmokeResult(jsonl_path=jsonl)

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=PER_REQUEST_TIMEOUT_S,
        follow_redirects=True,
    ) as client:
        # ------------------------------------------------------------------
        # T0 — Liveness (no auth, no state)
        # ------------------------------------------------------------------
        health = await hit(client, result, "T0", "GET", "/health", expect="2xx")
        if health and health.status_code == 200:
            data = health.json()
            if data.get("status") != "ok":
                print(f"    note: /health says {data.get('status')}: {data.get('checks')}")
        await hit(client, result, "T0", "GET", "/")

        # ------------------------------------------------------------------
        # T1 — Public metadata (no auth)
        # ------------------------------------------------------------------
        await hit(client, result, "T1", "GET", "/ui/tasks/catalog")
        await hit(client, result, "T1", "GET", "/ui/tasks/catalog/labels")
        await hit(client, result, "T1", "GET", "/ui/runs")
        await hit(client, result, "T1", "GET", "/ui/results")

        # ------------------------------------------------------------------
        # T2 — Run creation (critical path)
        # ------------------------------------------------------------------
        resp = await hit(
            client, result, "T2", "POST", "/client/runs/start",
            json={"task": task_label},
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            expect="2xx",
        )
        if resp is None or resp.status_code != 200:
            print("Cannot continue: /client/runs/start did not succeed.")
            return _finalise(result)
        body = resp.json()
        run_id = body["run_id"]
        token = body["token"]
        control_token = body.get("control_token", "")
        control_auth = {"Authorization": f"Bearer {control_token}"} if control_token else None

        # The owner-only run detail route requires the control_token;
        # hitting it unauthed would pass the non-5xx rule via a 401
        # rejection and wrongly validate only the auth path.
        await hit(
            client, result, "T2", "GET", f"/ui/runs/{run_id}",
            headers=control_auth or {},
            expect="2xx",
        )

        # ------------------------------------------------------------------
        # T3 — Session lifecycle (critical path for T4+)
        # ------------------------------------------------------------------
        resp = await hit(
            client, result, "T3", "POST", "/session/register",
            json={}, headers={"Authorization": f"Bearer {token}"},
            expect="2xx",
        )
        if resp is None or resp.status_code != 200:
            print("Cannot continue: /session/register did not succeed.")
            return _finalise(result)
        sid = resp.json()["session_id"]
        session_auth = {"Authorization": f"Bearer {token}"}

        try:
            await hit(client, result, "T3", "GET", "/session/list")
            await hit(client, result, "T3", "GET", f"/session/{sid}", headers=session_auth)
            await hit(
                client, result, "T3", "POST", f"/session/{sid}/start",
                json={}, headers=session_auth,
            )
            await hit(client, result, "T3", "GET", f"/session/{sid}/tools", headers=session_auth)

            # ------------------------------------------------------------------
            # T4 — Sync domain tool (fast)
            # ------------------------------------------------------------------
            await hit(
                client, result, "T4", "POST",
                f"/session/{sid}/tool/shell_exec",
                json={"command": "echo availability"},
                headers=session_auth,
            )

            # ------------------------------------------------------------------
            # T5 — Async job path: enqueue, poll, wait for terminal
            # ------------------------------------------------------------------
            resp = await hit(
                client, result, "T5", "POST",
                f"/session/{sid}/tool/run_lean_backtest",
                json={"algorithm_path": "/nonexistent-smoke.cs"},
                headers=session_auth,
                expect="202",
            )
            job_id: Optional[str] = None
            if resp and resp.status_code == 202:
                job_id = resp.json().get("job_id")
                if not job_id:
                    # A 202 with no job_id breaks the async contract; the
                    # client cannot poll and T5 coverage is lost.
                    result.record(
                        "T5", "POST",
                        f"/session/{sid}/tool/run_lean_backtest",
                        202, False, 0.0,
                        "202 response missing job_id",
                    )

            if job_id:
                await hit(
                    client, result, "T5", "GET",
                    f"/session/{sid}/tool/jobs/{job_id}",
                    headers=session_auth,
                )
                # Let the background worker reach a terminal state so the
                # session lock frees up before we try to DELETE.
                await wait_for_job_terminal(client, sid, job_id, headers=session_auth)

            # ------------------------------------------------------------------
            # T6 — Results + scores (reachable without a completed eval).
            # Operator surface — bearer the admin token so the smoke check
            # exercises the actual handlers in secured environments rather
            # than recording a 401 as PASS under the non-5xx rule.
            # ------------------------------------------------------------------
            admin_token = os.environ.get("QTB_ADMIN_TOKEN", "").strip()
            ops_headers = (
                {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
            )
            await hit(
                client, result, "T6", "GET",
                f"/ops/session/{sid}/results",
                headers=ops_headers,
            )
            await hit(
                client, result, "T6", "GET",
                f"/ops/session/{sid}/scores",
                headers=ops_headers,
            )

            # ------------------------------------------------------------------
            # T7 — UI read endpoints (no auth)
            # ------------------------------------------------------------------
            await hit(client, result, "T7", "GET", f"/ui/results/{sid}")
            await hit(client, result, "T7", "GET", f"/ui/results/{sid}/workspace")

        finally:
            # Cancel the run first using its control_token so the
            # dashboard records a clean "cancelled" entry. The DELETE
            # after is for session/container cleanup; without the
            # cancel, _cleanup_session would mark the bound run as
            # failed with "Client disconnected" and every passing smoke
            # would leave a red entry in /ui/runs.
            if control_auth:
                await hit(
                    client, result, "T3", "POST",
                    f"/ui/runs/{run_id}/cancel",
                    headers=control_auth,
                )
            await hit(client, result, "T3", "DELETE", f"/session/{sid}", headers=session_auth)

    return _finalise(result)


def _finalise(result: SmokeResult) -> int:
    total = len(result.rows)
    print()
    print(f"=== {total} calls, {result.failures} failure(s) ===")
    return 1 if result.failures else 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="External availability smoke test for QuantTutorBench prod.",
    )
    p.add_argument("--base-url", default=DEFAULT_BASE, help=f"default: {DEFAULT_BASE}")
    p.add_argument("--task", default=DEFAULT_TASK, help=f"public task label (default: {DEFAULT_TASK})")
    p.add_argument("--jsonl", default=None, help="append-only JSONL log path")
    p.add_argument(
        "--api-key",
        default=os.environ.get("QTB_CLIENT_API_KEY", ""),
        help="REST API key from the logged-in UI account (default: QTB_CLIENT_API_KEY)",
    )
    args = p.parse_args()
    return asyncio.run(run_smoke(args.base_url, args.task, args.jsonl, args.api_key))


if __name__ == "__main__":
    sys.exit(main())
