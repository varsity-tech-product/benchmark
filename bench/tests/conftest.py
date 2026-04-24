"""Shared pytest configuration and fixtures for QuantTutorBench server tests.

Autouse fixtures mock all external dependencies (LLM APIs, HuggingFace
downloads) so tests run without network access, API keys, or Docker.

Pattern reference: backend-service/tests/conftest.py
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 1. Stub the ``mcp`` package before any server module is imported.
#    The real ``mcp`` SDK may not be installed in every test environment.
# ---------------------------------------------------------------------------

if "mcp" not in sys.modules:
    _mcp = types.ModuleType("mcp")
    _mcp_types = types.ModuleType("mcp.types")
    _mcp_server = types.ModuleType("mcp.server")

    class _TextContent:
        def __init__(self, type: str = "text", text: str = ""):
            self.type = type
            self.text = text

    class _Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    _mcp_types.TextContent = _TextContent
    _mcp_types.Tool = _Tool
    _mcp.types = _mcp_types

    class _Server:
        def __init__(self, name: str = "", instructions: str = ""):
            self.name = name
            self.instructions = instructions

        def list_tools(self):
            def decorator(fn):
                return fn

            return decorator

        def call_tool(self):
            def decorator(fn):
                return fn

            return decorator

        def create_initialization_options(self):
            return {}

    _mcp_server.Server = _Server
    _mcp.server = _mcp_server

    # Stub mcp.server.streamable_http (imported by http_app.py)
    _mcp_streamable = types.ModuleType("mcp.server.streamable_http")

    class _StreamableHTTPServerTransport:
        def __init__(self, *args, **kwargs):
            self.session_id = "stub-session"

        async def handle_request(self, scope, receive, send):
            pass

    _mcp_streamable.StreamableHTTPServerTransport = _StreamableHTTPServerTransport
    _mcp_server.streamable_http = _mcp_streamable

    # Stub mcp.client.* so client.transports package can import without
    # the real SDK present (e.g. REST transport unit tests).
    _mcp_client = types.ModuleType("mcp.client")
    _mcp_client_session = types.ModuleType("mcp.client.session")
    _mcp_client_streamable = types.ModuleType("mcp.client.streamable_http")

    class _ClientSession:
        def __init__(self, *args, **kwargs):
            pass

    async def _streamablehttp_client(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("mcp.client stubbed — real SDK not installed")

    _mcp_client_session.ClientSession = _ClientSession
    _mcp_client_streamable.streamablehttp_client = _streamablehttp_client
    _mcp_client.session = _mcp_client_session
    _mcp_client.streamable_http = _mcp_client_streamable
    _mcp.client = _mcp_client

    sys.modules["mcp"] = _mcp
    sys.modules["mcp.types"] = _mcp_types
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.streamable_http"] = _mcp_streamable
    sys.modules["mcp.client"] = _mcp_client
    sys.modules["mcp.client.session"] = _mcp_client_session
    sys.modules["mcp.client.streamable_http"] = _mcp_client_streamable

# Ensure bench/ is on sys.path so ``import server.*`` works.
_BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

# ---------------------------------------------------------------------------
# 2. Fake LLM model — deterministic, zero-cost replacement for OpenRouter.
# ---------------------------------------------------------------------------

_STUDENT_REPLIES = [
    "Thanks, that makes sense. Can you show me the actual code?",
    "I see the bug now. Let me try fixing it.",
    "Got it. What should the correct value be?",
    "Interesting. Can you explain why that matters?",
    "Ok I think I understand. Let me run it again.",
]

_CLOSING_MESSAGE = "Thanks for all the help! I understand the issue much better now."


class FakeLLMModel:
    """Deterministic LLM stand-in for StudentSimulator.

    Cycles through canned replies. Returns (text, 0.0) cost tuples
    to match the real model interface.
    """

    def __init__(self):
        self._call_count = 0

    def generate(self, prompt: str, schema=None, images=None):
        self._call_count += 1

        # StudentSimulator structured output (SimulatedInput)
        if schema is not None:
            try:
                return (
                    schema(
                        simulated_input=_STUDENT_REPLIES[
                            self._call_count % len(_STUDENT_REPLIES)
                        ]
                    ),
                    0.0,
                )
            except Exception:
                pass

        # Plain text fallback (closing messages, etc.)
        if "closing" in prompt.lower():
            return _CLOSING_MESSAGE, 0.0

        reply = _STUDENT_REPLIES[self._call_count % len(_STUDENT_REPLIES)]
        return json.dumps({"simulated_input": reply}), 0.0


class FakeTCModel:
    """Deterministic TC checker model.

    Returns "no items covered" by default. Tests can set
    ``force_all_covered = True`` to simulate TC completion.
    """

    def __init__(self):
        self.force_all_covered = False
        self._call_count = 0

    def generate(self, prompt: str, schema=None, images=None):
        self._call_count += 1
        if self.force_all_covered:
            return '{"covered_items": [1, 2, 3, 4, 5]}', 0.0
        return '{"covered_items": []}', 0.0


# ---------------------------------------------------------------------------
# 3. Autouse: mock LLM model resolution (prevents API key requirement).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_llm_resolution():
    """Replace ``require_ewan_model`` / ``resolve_ewan_model`` globally.

    Every component that resolves a model (StudentSimulator,
    evaluation pipeline) will get a FakeLLMModel instead of calling
    OpenRouter.
    """
    fake = FakeLLMModel()

    with (
        patch(
            "server.eval.judges.runtime.model_resolver.require_ewan_model",
            return_value=fake,
        ),
        patch(
            "server.eval.judges.runtime.model_resolver.resolve_ewan_model",
            return_value=fake,
        ),
    ):
        yield fake


# ---------------------------------------------------------------------------
# 4. Autouse: mock HuggingFace data downloads.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_ensure_data(tmp_path):
    """Replace ``ensure_data`` with a fixture that creates temp directories.

    Avoids downloading ~16K files from HuggingFace during tests.
    Data directories are populated with minimal placeholder files so
    staging and container setup don't fail on missing paths.
    """
    from server.data_manager import DataPaths

    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    student_code_dir = tmp_path / "student_code"
    data_dir.mkdir()
    docs_dir.mkdir()
    student_code_dir.mkdir()

    # Create minimal data file so staging doesn't produce empty dirs
    (data_dir / "AAPL_2018_2024.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2022-01-03,177.83,182.88,177.71,182.01,104487900\n"
        "2022-01-04,182.63,182.94,179.12,179.70,99310400\n",
        encoding="utf-8",
    )

    # Create minimal doc files
    (docs_dir / "moving_averages.md").write_text(
        "# Moving Averages\nSMA and EMA overview.\n", encoding="utf-8"
    )
    (docs_dir / "pandas_timeseries.md").write_text(
        "# Pandas Time Series\nBasic guide.\n", encoding="utf-8"
    )

    # Create student code files referenced by debug tasks (X01, etc.)
    (student_code_dir / "ma_offbyone.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv('AAPL_2018_2024.csv')\n"
        "df['SMA_20'] = df['Close'].rolling(19).mean()  # BUG: should be 20\n"
        "df['SMA_50'] = df['Close'].rolling(50).mean()\n",
        encoding="utf-8",
    )

    fake_paths = DataPaths(
        docs=str(docs_dir),
        lean_data=None,
        custom_data=None,
        data_search_dirs=[str(data_dir)],
        student_code=str(student_code_dir),
    )

    with patch("server.data_manager.ensure_data", return_value=fake_paths):
        yield fake_paths


# ---------------------------------------------------------------------------
# 5. Autouse: mock server env loading.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_server_env():
    """No-op ``load_server_env`` so tests don't depend on .env files."""
    with patch("server.config.bootstrap.load_server_env", return_value=None):
        yield


# ---------------------------------------------------------------------------
# 6. Server app fixture (non-autouse — import explicitly when needed).
# ---------------------------------------------------------------------------

BENCH_ROOT = _BENCH_ROOT


@pytest.fixture
def bench_root(tmp_path):
    """Isolated bench root: tasks/personas via symlink, results in tmp_path.

    Ensures tests never write to the real results/ directory.
    """
    real_bench = BENCH_ROOT
    (tmp_path / "tasks").symlink_to(real_bench / "tasks")
    (tmp_path / "personas").symlink_to(real_bench / "personas")
    (tmp_path / "server").symlink_to(real_bench / "server")
    return tmp_path


@pytest.fixture
def app(bench_root):
    """Create a QuantTutorBench ASGI app for in-process testing.

    Uses ``use_docker=False`` so no Docker daemon is required. Results
    are written to an isolated tmp directory via bench_root.

    Usage with httpx::

        async def test_something(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/session/register", json={...})
    """
    from server.api.http_app import create_app

    return create_app(
        use_docker=False,
        bench_root=str(bench_root),
        eval_model="fake-model",
    )


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace directory for attachment tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# 7. FakeTCChecker — controllable TC checker for completion tests.
# ---------------------------------------------------------------------------


class FakeTCChecker:
    """TC checker that can be configured to report completion on a specific turn.

    Usage::

        checker = FakeTCChecker(["item1", "item2"], complete_on_check=2)
        # First check() → False, second check() → True (all covered)
    """

    def __init__(
        self,
        tc_items: list[str] | None = None,
        complete_on_check: int | None = None,
    ):
        self.tc_items = tc_items or ["TC1", "TC2", "TC3"]
        self._complete_on_check = complete_on_check
        self._check_count = 0
        self.covered: list[bool] = [False] * len(self.tc_items)
        self.history: list[dict] = []
        self.total_cost: float = 0.0

    @property
    def all_covered(self) -> bool:
        return all(self.covered)

    @property
    def coverage_summary(self) -> dict:
        covered_indices = [i + 1 for i, c in enumerate(self.covered) if c]
        return {
            "total": len(self.tc_items),
            "covered": sum(self.covered),
            "covered_indices": covered_indices,
            "items": [
                {"text": t, "covered": c} for t, c in zip(self.tc_items, self.covered)
            ],
        }

    @property
    def debug_history(self) -> list[dict]:
        return list(self.history)

    @property
    def stalled_turns(self) -> int:
        count = 0
        for entry in reversed(self.history):
            if entry.get("newly_covered_indices"):
                break
            count += 1
        return count

    def check(
        self,
        conversation,
        turn_evidence=None,
        turn_index=None,
    ) -> bool:
        self._check_count += 1
        newly = []
        if (
            self._complete_on_check is not None
            and self._check_count >= self._complete_on_check
        ):
            newly = [i + 1 for i, c in enumerate(self.covered) if not c]
            self.covered = [True] * len(self.tc_items)
        self.history.append(
            {
                "turn_index": turn_index,
                "covered_before_indices": [],
                "newly_covered_indices": newly,
                "covered_after_indices": [
                    i + 1 for i, c in enumerate(self.covered) if c
                ],
            }
        )
        return self.all_covered


@pytest.fixture
def fake_tc_checker():
    """Create a FakeTCChecker. Call with ``complete_on_check=N`` to control."""
    return FakeTCChecker


# ---------------------------------------------------------------------------
# 8. TutoringSession factory for unit tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_session(_mock_llm_resolution):
    """Factory to build a TutoringSession with controlled dependencies.

    Returns a callable ``(max_turns=10, tc_checker=None,
    deadline=None, workspace_path=None) → TutoringSession``.
    The session is pre-started (opening injected).
    """
    from server.core.session import TutoringSession
    from server.core.student_sim import StudentSimulator

    def _factory(
        max_turns=10,
        tc_checker=None,
        deadline=None,
        workspace_path=None,
    ):
        # Minimal task stub
        task = types.SimpleNamespace(
            environment=None,
            sample_code="",
            category=types.SimpleNamespace(value="debug"),
            max_turns=max_turns,
            student_openings={"fullstack_practitioner": "Hi, I need help."},
        )
        persona = types.SimpleNamespace(
            persona_id="fullstack_practitioner",
            description="A developer learning quantitative finance.",
        )
        student_sim = StudentSimulator(
            scenario="Debug a moving average bug",
            user_description="Intermediate developer",
            model=_mock_llm_resolution,
        )
        session = TutoringSession(
            task=task,
            persona=persona,
            student_sim=student_sim,
            tc_checker=tc_checker,
            max_turns=max_turns,
            deadline=deadline,
            workspace_path=workspace_path,
        )
        # Inject opening so session is ready for send_message
        session.inject_student_opening("Hi, I need help.")
        return session

    return _factory


# ---------------------------------------------------------------------------
# 9. Mock eval pipeline (non-autouse).
# ---------------------------------------------------------------------------

_FAKE_EVAL_SCORES = {
    "quant_result": 0.85,
    "quant_process": 0.70,
    "tutor_score": 0.75,
    "tutor_scores": {
        "D1_finance_adaptation": 0.75,
        "D2_code_adaptation": 0.50,
        "D3_pedagogical_method": 0.75,
        "D4_instructional_accuracy": 0.50,
        "D5_empathetic_response": 0.75,
        "D6_safety_boundaries": 0.50,
    },
}


@pytest.fixture
def mock_eval_pipeline():
    """Patch eval pipeline to return fake scores immediately."""

    def _fake_run(*args, **kwargs):
        from datetime import datetime, timezone

        from server.eval.judge_reliability import build_judge_reliability_metadata
        from server.storage.score_store import (
            allocate_score_run,
            summarize_score,
            update_score_run,
            write_score_files,
        )

        result_dir = kwargs["result_dir"]
        score_id = kwargs.get("score_id")
        if score_id is None:
            run, _ = allocate_score_run(
                result_dir,
                eval_mode=kwargs.get("eval_mode", "full"),
                eval_model=kwargs.get("eval_model"),
                tutor_dims=kwargs.get("tutor_dims"),
            )
            score_id = run.score_id
            created_at = run.created_at
        else:
            created_at = datetime.now(timezone.utc).isoformat()
        completed_at = datetime.now(timezone.utc).isoformat()
        score = {
            "version": "2.0",
            "score_id": score_id,
            "score_status": "completed_scored",
            "created_at": created_at,
            "completed_at": completed_at,
            "eval_model": kwargs.get("eval_model"),
            "eval_mode": kwargs.get("eval_mode", "full"),
            "duration_seconds": 0.01,
            "judge_reliability": build_judge_reliability_metadata(
                kwargs.get("eval_model")
            ),
            "interrupted": False,
            "blocking_missing": [],
            "overall_score": 0.775,
            "qr": {
                "track": "qr",
                "status": "success",
                "score": _FAKE_EVAL_SCORES["quant_result"],
                "blocking_missing": [],
                "detail": {},
                "eval_cost": 0.001,
                "eval_cost_by_model": {"fake-model": 0.001},
            },
            "qp": {
                "track": "qp",
                "status": "success",
                "score": _FAKE_EVAL_SCORES["quant_process"],
                "blocking_missing": [],
                "detail": {},
                "eval_cost": 0.002,
                "eval_cost_by_model": {"fake-model": 0.002},
            },
            "tutor": {
                "track": "tutor",
                "status": "success",
                "score": _FAKE_EVAL_SCORES["tutor_score"],
                "blocking_missing": [],
                "detail": _FAKE_EVAL_SCORES["tutor_scores"],
                "eval_cost": 0.003,
                "eval_cost_by_model": {"fake-model": 0.003},
            },
        }
        cost = {
            "version": "2.0",
            "score_id": score_id,
            "eval_cost_usd": 0.006,
            "eval_cost_by_track": {"qr": 0.001, "qp": 0.002, "tutor": 0.003},
            "eval_cost_by_model": {"fake-model": 0.006},
            "eval_cost_by_stage_model": {},
        }
        write_score_files(result_dir, score_id, score, cost)
        update_score_run(
            result_dir,
            score_id,
            status="completed_scored",
            overall_score=score["overall_score"],
            completed_at=completed_at,
        )
        return summarize_score(score, cost)

    with patch(
        "server.storage.eval_writer.run_evaluation",
        side_effect=_fake_run,
    ) as mock_run:
        yield mock_run
