import json

import httpx
import pytest

TASK_A = "L2_ADV_11_prompt_injection_csv"
TASK_B = "L2_ADV_01_investment_advice"


def _make_app(bench_root):
    from server.api.http_app import create_app

    return create_app(
        use_docker=False,
        bench_root=str(bench_root),
        eval_model="fake-model",
    )


async def _post_json(app, path, payload, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload, headers=headers or {})


async def _get_json(app, path, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, headers=headers or {})


@pytest.mark.asyncio
async def test_client_start_requires_api_key_when_auth_enabled(bench_root, monkeypatch):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.delenv("QTB_CLIENT_API_KEYS", raising=False)
    app = _make_app(bench_root)

    resp = await _post_json(app, "/client/runs/start", {"task": TASK_A})

    assert resp.status_code == 401
    assert "client_api_key" in resp.json()["error"]


@pytest.mark.asyncio
async def test_client_task_catalog_requires_api_key_when_auth_enabled(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.delenv("QTB_CLIENT_API_KEYS", raising=False)
    app = _make_app(bench_root)

    resp = await _get_json(app, "/client/tasks/catalog/labels")

    assert resp.status_code == 401
    assert "client_api_key" in resp.json()["error"]


@pytest.mark.asyncio
async def test_client_task_catalog_returns_labels_with_api_key(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv(
        "QTB_CLIENT_API_KEYS",
        "secret-alice=external:alice|alice|alice@example.com",
    )
    app = _make_app(bench_root)

    resp = await _get_json(
        app,
        "/client/tasks/catalog/labels",
        headers={"Authorization": "Bearer secret-alice"},
    )

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert {"label": TASK_A} in tasks
    assert all(set(item) == {"label"} for item in tasks)


@pytest.mark.asyncio
async def test_client_start_api_key_persists_run_owner(bench_root, monkeypatch):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv(
        "QTB_CLIENT_API_KEYS",
        "secret-alice=external:alice|alice|alice@example.com",
    )
    app = _make_app(bench_root)

    resp = await _post_json(
        app,
        "/client/runs/start",
        {"task": TASK_A, "client": {"name": "alice-agent"}},
        headers={"Authorization": "Bearer secret-alice"},
    )

    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    run_path = bench_root / "results" / "runs" / run_id / "run.json"
    stored = json.loads(run_path.read_text(encoding="utf-8"))
    assert stored["owner_user_id"] == "external:alice"
    assert stored["owner_github_login"] == "alice"
    assert stored["owner_email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_github_user_can_generate_ui_api_key_for_client_start(
    bench_root, monkeypatch
):
    from server.auth import AuthService, SESSION_COOKIE, UserContext

    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.delenv("QTB_CLIENT_API_KEYS", raising=False)
    app = _make_app(bench_root)
    user = UserContext("github:alice", "alice", "alice@example.com", "Alice", "")
    session_id = AuthService(bench_root).store.create_session(user)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE, session_id)
        empty = await client.get("/ui/api-key")
        generated = await client.post("/ui/api-key")
        api_key = generated.json()["api_key"]
        status = await client.get("/ui/api-key")
        client.cookies.clear()
        run = await client.post(
            "/client/runs/start",
            json={"task": TASK_A},
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert empty.status_code == 200
    assert empty.json()["has_key"] is False
    assert generated.status_code == 200
    assert api_key.startswith("qtbu_")
    assert "api_key" not in status.json()
    assert status.json()["has_key"] is True
    assert run.status_code == 200

    run_id = run.json()["run_id"]
    run_path = bench_root / "results" / "runs" / run_id / "run.json"
    stored = json.loads(run_path.read_text(encoding="utf-8"))
    assert stored["owner_user_id"] == "github:alice"
    assert stored["owner_github_login"] == "alice"


@pytest.mark.asyncio
async def test_client_start_api_key_subject_to_user_quota(bench_root, monkeypatch):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv("QTB_MAX_ACTIVE_RUNS_PER_USER", "1")
    monkeypatch.setenv("QTB_CLIENT_API_KEYS", "secret-alice=external:alice|alice")
    app = _make_app(bench_root)
    headers = {"Authorization": "Bearer secret-alice"}

    first = await _post_json(app, "/client/runs/start", {"task": TASK_A}, headers)
    second = await _post_json(app, "/client/runs/start", {"task": TASK_B}, headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert "active run limit" in second.json()["error"]


@pytest.mark.asyncio
async def test_client_active_runs_requires_api_key_when_auth_enabled(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.delenv("QTB_CLIENT_API_KEYS", raising=False)
    app = _make_app(bench_root)

    resp = await _get_json(app, "/client/runs/active")

    assert resp.status_code == 401
    assert "client_api_key" in resp.json()["error"]


@pytest.mark.asyncio
async def test_client_active_runs_are_owner_scoped_and_token_safe(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv(
        "QTB_CLIENT_API_KEYS",
        "secret-alice=external:alice|alice,secret-bob=external:bob|bob",
    )
    app = _make_app(bench_root)
    alice_headers = {"Authorization": "Bearer secret-alice"}
    bob_headers = {"Authorization": "Bearer secret-bob"}

    alice_run = await _post_json(
        app, "/client/runs/start", {"task": TASK_A}, alice_headers
    )
    bob_run = await _post_json(app, "/client/runs/start", {"task": TASK_B}, bob_headers)
    active = await _get_json(app, "/client/runs/active", alice_headers)

    assert alice_run.status_code == 200
    assert bob_run.status_code == 200
    assert active.status_code == 200
    payload = active.json()
    assert payload["count"] == 1
    assert [item["run_id"] for item in payload["runs"]] == [alice_run.json()["run_id"]]
    assert {"token", "control_token", "task_id", "token_hash"}.isdisjoint(
        payload["runs"][0]
    )


@pytest.mark.asyncio
async def test_client_cancel_run_is_owner_scoped_and_unblocks_quota(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv("QTB_MAX_ACTIVE_RUNS_PER_USER", "1")
    monkeypatch.setenv(
        "QTB_CLIENT_API_KEYS",
        "secret-alice=external:alice|alice,secret-bob=external:bob|bob",
    )
    app = _make_app(bench_root)
    alice_headers = {"Authorization": "Bearer secret-alice"}
    bob_headers = {"Authorization": "Bearer secret-bob"}

    first = await _post_json(app, "/client/runs/start", {"task": TASK_A}, alice_headers)
    blocked = await _post_json(
        app, "/client/runs/start", {"task": TASK_B}, alice_headers
    )
    run_id = first.json()["run_id"]
    denied = await _post_json(app, f"/client/runs/{run_id}/cancel", {}, bob_headers)
    cancelled = await _post_json(
        app, f"/client/runs/{run_id}/cancel", {}, alice_headers
    )
    active = await _get_json(app, "/client/runs/active", alice_headers)
    recovered = await _post_json(
        app, "/client/runs/start", {"task": TASK_B}, alice_headers
    )

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert denied.status_code == 403
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert active.status_code == 200
    assert active.json()["runs"] == []
    assert recovered.status_code == 200


@pytest.mark.asyncio
async def test_client_cancel_missing_run_returns_404(bench_root, monkeypatch):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv("QTB_CLIENT_API_KEYS", "secret-alice=external:alice|alice")
    app = _make_app(bench_root)

    resp = await _post_json(
        app,
        "/client/runs/run_missing/cancel",
        {},
        headers={"Authorization": "Bearer secret-alice"},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rest_session_endpoints_require_matching_run_token(bench_root, monkeypatch):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv("QTB_CLIENT_API_KEYS", "secret-alice=external:alice|alice")
    app = _make_app(bench_root)
    headers = {"Authorization": "Bearer secret-alice"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        run_a = (
            await client.post("/client/runs/start", json={"task": TASK_A}, headers=headers)
        ).json()
        run_b = (
            await client.post("/client/runs/start", json={"task": TASK_B}, headers=headers)
        ).json()

        reg = await client.post(
            "/session/register",
            json={},
            headers={"Authorization": f"Bearer {run_a['token']}"},
        )
        assert reg.status_code == 200
        sid = reg.json()["session_id"]

        no_token = await client.post(f"/session/{sid}/start", json={})
        wrong_token = await client.post(
            f"/session/{sid}/start",
            json={},
            headers={"Authorization": f"Bearer {run_b['token']}"},
        )
        right_token = await client.post(
            f"/session/{sid}/start",
            json={},
            headers={"Authorization": f"Bearer {run_a['token']}"},
        )
        tools = await client.get(
            f"/session/{sid}/tools",
            headers={"Authorization": f"Bearer {run_a['token']}"},
        )

    assert no_token.status_code == 401
    assert wrong_token.status_code == 403
    assert right_token.status_code == 200
    assert tools.status_code == 200


@pytest.mark.asyncio
async def test_session_list_is_scoped_by_run_token_when_auth_enabled(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "github")
    monkeypatch.setenv("QTB_CLIENT_API_KEYS", "secret-alice=external:alice|alice")
    app = _make_app(bench_root)
    headers = {"Authorization": "Bearer secret-alice"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        run_a = (
            await client.post("/client/runs/start", json={"task": TASK_A}, headers=headers)
        ).json()
        run_b = (
            await client.post("/client/runs/start", json={"task": TASK_B}, headers=headers)
        ).json()
        reg_a = await client.post(
            "/session/register",
            json={},
            headers={"Authorization": f"Bearer {run_a['token']}"},
        )
        reg_b = await client.post(
            "/session/register",
            json={},
            headers={"Authorization": f"Bearer {run_b['token']}"},
        )

        anonymous = await client.get("/session/list")
        scoped = await client.get(
            "/session/list",
            headers={"Authorization": f"Bearer {run_a['token']}"},
        )

    assert anonymous.status_code == 401
    assert scoped.status_code == 200
    assert [item["session_id"] for item in scoped.json()["sessions"]] == [
        reg_a.json()["session_id"]
    ]
    assert reg_b.json()["session_id"] not in {
        item["session_id"] for item in scoped.json()["sessions"]
    }


@pytest.mark.asyncio
async def test_require_client_auth_also_requires_session_run_token(
    bench_root, monkeypatch
):
    monkeypatch.setenv("QTB_AUTH_MODE", "disabled")
    monkeypatch.setenv("QTB_REQUIRE_CLIENT_AUTH", "true")
    monkeypatch.setenv("QTB_CLIENT_API_KEYS", "secret-alice=external:alice|alice")
    app = _make_app(bench_root)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        run = (
            await client.post(
                "/client/runs/start",
                json={"task": TASK_A},
                headers={"Authorization": "Bearer secret-alice"},
            )
        ).json()
        reg = await client.post(
            "/session/register",
            json={},
            headers={"Authorization": f"Bearer {run['token']}"},
        )
        sid = reg.json()["session_id"]

        no_token = await client.post(f"/session/{sid}/start", json={})
        right_token = await client.post(
            f"/session/{sid}/start",
            json={},
            headers={"Authorization": f"Bearer {run['token']}"},
        )

    assert no_token.status_code == 401
    assert right_token.status_code == 200
