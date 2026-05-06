import asyncio
import json
from typing import Any

import httpx
import pytest

from scripts import baseline_run


def _wait_eval_done(app: Any, session_id: str) -> None:
    state = app._manager.get_session(session_id)
    assert state is not None
    for _ in range(100):
        with state._eval_lock:
            if state._eval_status in ("completed", "failed"):
                return
        import time

        time.sleep(0.02)
    raise AssertionError("evaluation did not finish")


@pytest.mark.asyncio
async def test_baseline_run_api_smoke_exports_valid_bundles(
    bench_root,
    tmp_path,
    monkeypatch,
    mock_eval_pipeline,
):
    from server.api.http_app import create_app

    monkeypatch.delenv("QTB_AUTH_MODE", raising=False)
    monkeypatch.delenv("QTB_CLIENT_API_KEYS", raising=False)
    monkeypatch.delenv("QTB_REQUIRE_CLIENT_AUTH", raising=False)
    monkeypatch.delenv("QTB_REQUIRE_SESSION_TOKEN", raising=False)

    app = create_app(
        use_docker=False,
        bench_root=str(bench_root),
        eval_model="fake-model",
    )

    original_async_client = httpx.AsyncClient

    def asgi_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        kwargs["base_url"] = "http://test"
        return original_async_client(*args, **kwargs)

    async def fake_execute_agent(args, cell, token):
        headers = {"Authorization": f"Bearer {token}"}
        async with original_async_client(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            register = await client.post(
                "/session/register",
                json={},
                headers=headers,
            )
            assert register.status_code == 200, register.text
            session_id = register.json()["session_id"]

            start = await client.post(
                f"/session/{session_id}/start",
                json={},
                headers=headers,
            )
            assert start.status_code == 200, start.text

            for _ in range(3):
                sent = await client.post(
                    f"/session/{session_id}/send",
                    json={"text": "Repeated message for completion"},
                    headers=headers,
                )
                assert sent.status_code == 200, sent.text

        await asyncio.to_thread(_wait_eval_done, app, session_id)
        return {
            "task_id": cell.task.task_id,
            "session_id": session_id,
            "duration_seconds": 0.01,
            "agent_cost": {},
        }

    monkeypatch.setattr(baseline_run.httpx, "AsyncClient", asgi_client_factory)
    monkeypatch.setattr(baseline_run, "execute_agent", fake_execute_agent)

    output_dir = tmp_path / "baseline"
    args = baseline_run.build_parser().parse_args(
        [
            "--bench-root",
            str(bench_root),
            "--output-dir",
            str(output_dir),
            "run",
            "--server",
            "http://test",
            "--layers",
            "L2",
            "--agents",
            "claude_haiku_4_5",
            "--conditions",
            "agent",
            "--workers",
            "2",
            "--limit",
            "2",
            "--server-results-root",
            str(bench_root / "results" / "server"),
            "--force",
        ]
    )
    args.bench_root = args.bench_root.resolve()
    args.output_dir = args.output_dir.resolve()

    matrix = baseline_run.matrix_from_args(args)
    baseline_run.write_manifest(args.output_dir, matrix)
    await baseline_run.run_cells(args, matrix)

    records = baseline_run.load_run_records(args.output_dir / "runs.jsonl")
    summary = baseline_run.summarize_records(records, matrix)
    baseline_run.write_outputs(
        output_dir=args.output_dir,
        summary=summary,
        docs_dir=tmp_path / "docs",
    )

    assert len(records) == 2
    assert {record["status"] for record in records} == {"completed"}
    assert all(record["run_id"] for record in records)
    assert all(record["session_id"] for record in records)
    assert summary["completed_cells"] == 2
    assert summary["by_agent"] == [
        {
            "agent_id": "claude_haiku_4_5",
            "n": 2,
            "pass_rate": None,
            "mean": 0.775,
            "median": 0.775,
        }
    ]
    assert baseline_run.validate_bundles(args.output_dir) == 0

    lines = (args.output_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["status"] for line in lines] == [
        "completed",
        "completed",
    ]

    broken = tmp_path / "broken" / "bundles" / "cell" / "bundle.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{}", encoding="utf-8")

    assert baseline_run.validate_bundles(tmp_path / "broken") == 1
