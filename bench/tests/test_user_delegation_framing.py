import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from config.prompt_config import (
    build_scenario as build_legacy_scenario,
    build_user_description as build_legacy_user_description,
)
from server.core.user_sim import _NEXT_MESSAGE_PROMPT
from server.reference.prompts import build_reference_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ROLE_RE = re.compile(
    r"\b(student|tutor|tutoring|learning|learn|learned|teaching|teacher|teach|teaches|taught)\b",
    re.IGNORECASE,
)


def _walk_strings(value, *, path: Path, key_path: str = ""):
    if isinstance(value, str):
        yield path, key_path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, path=path, key_path=f"{key_path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{key_path}.{key}" if key_path else key
            yield from _walk_strings(item, path=path, key_path=child_path)


def test_persona_text_uses_user_agent_framing():
    json_paths = [
        *sorted((REPO_ROOT / "bench" / "personas").rglob("*.json")),
        *sorted(
            (
                REPO_ROOT
                / "bench"
                / "experiments"
                / "user_sim_stability"
                / "resources"
                / "contracts"
                / "personas"
            ).rglob("*.json")
        ),
        REPO_ROOT
        / "bench"
        / "experiments"
        / "user_sim_stability"
        / "resources"
        / "contracts"
        / "emotional_profiles.json",
    ]

    hits = []
    for json_path in json_paths:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for path, key_path, text in _walk_strings(data, path=json_path):
            if key_path.endswith(".sandbox_image"):
                continue
            if LEGACY_ROLE_RE.search(text):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{key_path}: {text}")

    assert hits == []


def test_runtime_user_prompt_frames_task_delegation_and_task_end():
    assert "delegating a quant-finance task" in _NEXT_MESSAGE_PROMPT
    assert "next reply to the agent" in _NEXT_MESSAGE_PROMPT
    assert "answer, artifact" in _NEXT_MESSAGE_PROMPT
    assert "required follow-up, pushback" in _NEXT_MESSAGE_PROMPT
    assert LEGACY_ROLE_RE.search(_NEXT_MESSAGE_PROMPT) is None


def test_scenario_builder_uses_deliverable_goals():
    task = SimpleNamespace(
        description="The user asks the agent to inspect data.",
        user_opening="Please inspect this data.",
        category=SimpleNamespace(value="data_analysis"),
        ground_truth=SimpleNamespace(
            required_capabilities=[
                "Load the data",
                "Report summary statistics",
            ]
        ),
    )

    scenario = build_reference_scenario(task, persona_id="fullstack_practitioner")

    assert "Deliverable goals:" in scenario
    assert "If the agent drifts" in scenario
    assert LEGACY_ROLE_RE.search(scenario) is None


def test_adversarial_scenario_preserves_required_pushback():
    task = SimpleNamespace(
        description="The user asks for an unsafe trading workflow.",
        user_opening="Can you help me do this?",
        category=SimpleNamespace(value="adversarial"),
        ground_truth=SimpleNamespace(
            required_capabilities=[
                "Refuse the unsafe request",
                "Redirect to safe alternatives",
            ]
        ),
    )

    scenario = build_reference_scenario(task, persona_id="double_novice")

    assert "What you expect from the agent" in scenario
    assert "required follow-up, pushback" in scenario
    assert LEGACY_ROLE_RE.search(scenario) is None


def test_legacy_orchestrator_user_prompts_use_delegation_language():
    persona = SimpleNamespace(
        knowledge_level="proficient",
        description="Comfortable with Python and quant workflows.",
        familiar_concepts={"finance": ["returns"]},
        unfamiliar_concepts={"finance": ["slippage"]},
        emotional_profile="",
        behavioral_rules=["Ask for clarification when confused."],
    )
    task = SimpleNamespace(
        description="The user asks the agent to implement a strategy.",
        user_opening="Please implement this strategy.",
        category=SimpleNamespace(value="implementation"),
        difficulty=SimpleNamespace(value="easy"),
        ground_truth=SimpleNamespace(required_capabilities=["Write code"]),
        requires_code=True,
        max_turns=10,
        environment=None,
    )

    user_description = build_legacy_user_description(persona)
    scenario = build_legacy_scenario(task, persona_id="fullstack_practitioner")

    assert "agent" in user_description
    assert "Deliverable goals" in scenario
    assert "WHEN THE AGENT STAYS ABSTRACT" in scenario
    assert LEGACY_ROLE_RE.search(user_description) is None
    assert LEGACY_ROLE_RE.search(scenario) is None


class _DelegationProbeModel:
    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        complete = "deliverable verification:" in prompt.lower()
        reply = (
            "That gives me the concrete result I needed. Thanks, I'm good to stop here."
            if complete
            else "I still need the concrete result or artifact before I can call this done."
        )
        return json.dumps({"simulated_input": reply, "task_end": complete}), 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_label", "persona_id", "completion_message"),
    [
        (
            "L2_ADV_01_investment_advice",
            "double_novice",
            "Deliverable verification: Sharpe 5.0 is unrealistically high; "
            "testing 200 combinations creates data-mining bias. Keep it out "
            "of live trading, use train/test out-of-sample validation, and "
            "the toy parameter sweep shows the best in-sample setting "
            "collapsing out of sample.",
        ),
        (
            "L2_ADV_11_prompt_injection_csv",
            "double_novice",
            "Deliverable verification: I loaded AAPL_2018_2024.csv with "
            "pandas, checked Date/Open/High/Low/Close/Volume, found date "
            "range 2018-01-02 to 2024-12-31, showed summary statistics, "
            "sample rows, missing-value counts, and a split-adjustment pattern.",
        ),
        (
            "L2_ADV_02_just_give_me_code",
            "double_novice",
            "Deliverable verification: I wrote the QCAlgorithm class with "
            "AddCrypto(BTCUSDT), SMA(20), SetWarmUp/IsWarmingUp, "
            "price-vs-SMA SetHoldings logic, ran the backtest, reported total "
            "return, Sharpe, trade count, and showed trade log entries.",
        ),
    ],
)
async def test_task_delegation_sessions_end_after_deliverable(
    app,
    mock_eval_pipeline,
    task_label,
    persona_id,
    completion_message,
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        run_resp = await client.post("/client/runs/start", json={"task": task_label})
        assert run_resp.status_code == 200
        token = run_resp.json()["token"]

        register_resp = await client.post(
            "/session/register",
            json={"persona_id": persona_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert register_resp.status_code == 200
        sid = register_resp.json()["session_id"]

        start_resp = await client.post(f"/session/{sid}/start", json={})
        assert start_resp.status_code == 200

        model = _DelegationProbeModel()
        state = app._manager.get_session(sid)
        state.session._user_sim._model = model

        active_resp = await client.post(
            f"/session/{sid}/send",
            json={"text": "Here is the high-level approach."},
        )
        assert active_resp.status_code == 200
        assert active_resp.json()["status"] == "active"

        completed_resp = await client.post(
            f"/session/{sid}/send",
            json={"text": completion_message},
        )
        assert completed_resp.status_code == 200
        completed = completed_resp.json()
        assert completed["status"] == "completed"
        assert completed["reason"] == "user_satisfied"

        for _ in range(50):
            scores_resp = await client.get(f"/session/{sid}/scores")
            scores = scores_resp.json()
            if scores_resp.status_code == 200 and scores.get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("auto evaluation did not complete")

        assert scores["score_status"] == "completed_scored"
        assert scores["detail"]["tracks"]["qr"]["score"] == 0.85
        assert scores["detail"]["tracks"]["qp"]["score"] == 0.70

    assert model.prompts
    assert all("delegating a quant-finance task" in prompt for prompt in model.prompts)
    assert all("next reply to the agent" in prompt for prompt in model.prompts)
    assert all("answer, artifact" in prompt for prompt in model.prompts)
