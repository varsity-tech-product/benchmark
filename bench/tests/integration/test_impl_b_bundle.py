import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from platform_api.contracts import EvalSample, TranscriptMessage
from platform_api.plugins import PluginLoader
from server.impl_b import IMPL_B_BUNDLE_CONFIG, load_impl_b_bundle

TASK_ID = "IMPLB_JSON_01_summary"


def _json_payload_for(spec: dict[str, Any]) -> dict[str, Any]:
    payload = {key: 1 for key in spec.get("required_keys", [])}
    for constraint in spec.get("constraints", []) or []:
        key = constraint.get("key")
        op = constraint.get("op")
        if not key:
            continue
        if "ref" in constraint:
            ref = constraint["ref"]
            payload.setdefault(ref, 1)
            if op == "<":
                payload[key], payload[ref] = 0, 1
            elif op == ">":
                payload[key], payload[ref] = 2, 1
            else:
                payload[key], payload[ref] = 1, 1
            continue

        value = constraint.get("value", 1)
        if op == "<":
            payload[key] = value - 1
        elif op == "<=":
            payload[key] = value
        elif op == ">":
            payload[key] = value + 1
        elif op == ">=":
            payload[key] = value
        elif op == "==":
            payload[key] = value
        elif op == "!=":
            payload[key] = value + 1
    return payload


def _write_expected_outputs(workspace: Path, expected_outputs: list[Any]) -> None:
    for spec in expected_outputs:
        if not isinstance(spec, dict):
            continue
        path = workspace / str(spec["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        file_type = spec.get("type", "any")
        if file_type == "json":
            path.write_text(json.dumps(_json_payload_for(spec)), encoding="utf-8")
        elif file_type == "csv":
            columns = spec.get("required_columns") or ["metric", "value"]
            rows = max(1, int(spec.get("min_rows") or 1))
            lines = [",".join(columns)]
            lines.extend(",".join(str(index) for _ in columns) for index in range(rows))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.write_text("artifact\n", encoding="utf-8")


def test_impl_b_bundle_loads_from_config(bench_root):
    bundles = PluginLoader().load_config(IMPL_B_BUNDLE_CONFIG)
    assert len(bundles) == 1

    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    supported = bundle.task_suite.supported_tasks()

    assert bundle.name == "impl_b_programmatic"
    assert len(supported) == 5
    assert TASK_ID in supported
    assert bundle.evaluator.metadata().capabilities == frozenset(
        {"programmatic_l1", "no_llm_judge"}
    )


def test_impl_b_task_suite_declares_mounts_and_business_adapter(bench_root):
    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")

    item = bundle.task_suite.get_task(TASK_ID)
    task = bundle.task_suite.get_business_task(TASK_ID)

    assert item.payload["expected_outputs"][0]["path"] == "output/summary.json"
    assert item.sandbox_spec.image_uri == "quant-bench-env:v3.0"
    assert item.data_mounts[0].uri.startswith("file://")
    assert item.data_mounts[0].target_path == "/data/reference_numbers.csv"
    assert (
        bundle.task_suite._resolve_file_uri("file://localhost/tmp/reference.csv")
        == "file:///tmp/reference.csv"
    )
    assert task.task_id == TASK_ID
    assert task.ground_truth.expected_outputs == item.payload["expected_outputs"]


def test_impl_b_trivial_npc_terminates_after_first_agent_turn(bench_root):
    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(TASK_ID)

    assert "summary.json" in bundle.npc_provider.initial_message(item)
    reply = bundle.npc_provider.respond(
        (
            TranscriptMessage(role="user", content=bundle.npc_provider.initial_message(item)),
            TranscriptMessage(role="assistant", content="I wrote the file."),
        ),
        (),
        {},
        item.payload,
    )

    assert reply.message == "continue"
    assert reply.terminate is True
    assert reply.telemetry["llm_judge_used"] is False


def test_impl_b_programmatic_evaluator_scores_expected_outputs(bench_root, tmp_path):
    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(TASK_ID)
    workspace = tmp_path / "workspace"
    _write_expected_outputs(workspace, item.payload["expected_outputs"])

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="impl-b-direct",
            task_id=TASK_ID,
            transcript=(
                TranscriptMessage(role="user", content=item.payload["prompt"]),
                TranscriptMessage(role="assistant", content="Artifacts are ready."),
            ),
            payload={"workspace_path": str(workspace), "eval_model": "fake-model"},
        ),
    )

    assert score.value == 1.0
    assert score.status == "completed_scored"
    assert score.metrics["programmatic"]["n_passed"] == 1
    assert score.telemetry["llm_judge_used"] is False


@pytest.mark.asyncio
async def test_impl_b_rest_register_complete_score_flow(bench_root):
    from server.api.http_app import create_app

    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    app = create_app(
        use_docker=False,
        bench_root=str(bench_root),
        eval_model="fake-model",
        plugin_bundle=bundle,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        start_run = await client.post("/client/runs/start", json={"task": TASK_ID})
        assert start_run.status_code == 200, start_run.text
        token = start_run.json()["token"]
        assert start_run.json()["public_task_label"] == TASK_ID

        register = await client.post(
            "/session/register",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert register.status_code == 200, register.text
        sid = register.json()["session_id"]

        start = await client.post(f"/session/{sid}/start", json={})
        assert start.status_code == 200, start.text
        assert "summary.json" in start.json()["user_message"]

        write = await client.post(
            f"/session/{sid}/tool/file_write",
            json={
                "path": "output/summary.json",
                "content": json.dumps(
                    {"row_count": 4, "column_count": 3, "valid": True}
                ),
            },
        )
        assert write.status_code == 200, write.text
        assert write.json()["success"] is True

        sent = await client.post(
            f"/session/{sid}/send",
            json={"text": "I created output/summary.json."},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "completed"

        score_body = {}
        for _ in range(50):
            scores = await client.get(f"/session/{sid}/scores")
            assert scores.status_code == 200, scores.text
            score_body = scores.json()
            if score_body.get("score_status") == "completed_scored":
                break
            await asyncio.sleep(0.02)

    assert score_body["score_status"] == "completed_scored"
    assert score_body["task_score"] == 1.0
    assert score_body["detail"]["dimensions"][0]["name"] == "programmatic"
