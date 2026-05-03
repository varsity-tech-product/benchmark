"""Parallel-session sanity test for the P0 auto-eval path (issue #126 / P2).

Drives 10 sessions to completion in parallel under one owner and verifies
each one ends with a per-session score record keyed ``auto:{sid}`` and
without leaking state across sessions. Heavy judge calls are stubbed via
``_run_evaluation`` so the test stays in unit-test territory.
"""

import asyncio

import httpx
import pytest
import pytest_asyncio

from server.api.session_api import SessionState
from tests.helpers import register_and_start, send_message


PARALLEL_COUNT = 10


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _get_state(app, sid):
    state = app._manager.get_session(sid)
    assert state is not None
    return state


@pytest.mark.asyncio
async def test_ten_parallel_sessions_complete_with_auto_eval(
    app, client, monkeypatch
):
    monkeypatch.setattr(SessionState, "_run_evaluation", lambda self, sid: None)

    async def _drive_one():
        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        state.session._max_turns = 1
        body = await send_message(client, sid, "Done.")
        assert body["status"] == "completed"
        return sid, state.run_id

    results = await asyncio.gather(
        *[_drive_one() for _ in range(PARALLEL_COUNT)]
    )

    sids = [sid for sid, _ in results]
    run_ids = [run_id for _, run_id in results]
    assert len(set(sids)) == PARALLEL_COUNT, "session_ids must be unique"
    assert len(set(run_ids)) == PARALLEL_COUNT, "run_ids must be unique"

    from eval.storage.score_store import load_index

    for sid in sids:
        state = _get_state(app, sid)
        assert state._result_dir is not None
        index = load_index(state._result_dir)
        assert len(index["scores"]) == 1, f"expected 1 score for {sid}"
        entry = index["scores"][0]
        assert entry["score_id"] == "score_1"
        assert entry["idempotency_key"] == f"auto:{sid}"
