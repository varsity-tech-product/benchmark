"""Shared pytest configuration and fixtures for QuantTutorBench server tests.

Autouse fixtures mock all external dependencies (LLM APIs, HuggingFace
downloads) so tests run without network access, API keys, or Docker.

Pattern reference: backend-service/tests/conftest.py
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    sys.modules["mcp"] = _mcp
    sys.modules["mcp.types"] = _mcp_types
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.streamable_http"] = _mcp_streamable

# ---------------------------------------------------------------------------
# Stub ``server.web.ui_app`` — the module is planned but not yet implemented.
# Without this, ``from server.web.ui_app import ui_routes`` crashes on import.
# ---------------------------------------------------------------------------

_server_web = types.ModuleType("server.web")
_server_web_ui = types.ModuleType("server.web.ui_app")


def _stub_ui_routes(*_args, **_kwargs):
    return []


_server_web_ui.ui_routes = _stub_ui_routes
_server_web.ui_app = _server_web_ui
sys.modules["server.web"] = _server_web
sys.modules["server.web.ui_app"] = _server_web_ui

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

_CLOSING_MESSAGE = (
    "Thanks for all the help! I understand the issue much better now."
)


class FakeLLMModel:
    """Deterministic LLM stand-in for StudentSimulator / GoalChecker.

    Cycles through canned replies. Returns (text, 0.0) cost tuples
    to match the real model interface.
    """

    def __init__(self):
        self._call_count = 0

    def generate(self, prompt: str, schema=None):
        self._call_count += 1

        # GoalChecker uses ConversationCompletion schema
        if schema is not None and hasattr(schema, "model_fields"):
            if "is_complete" in schema.model_fields:
                return schema(is_complete=False, reason="still in progress"), 0.0

        # StudentSimulator structured output (SimulatedInput)
        if schema is not None:
            try:
                return schema(
                    simulated_input=_STUDENT_REPLIES[
                        self._call_count % len(_STUDENT_REPLIES)
                    ]
                ), 0.0
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

    def generate(self, prompt: str, schema=None):
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

    Every component that resolves a model (StudentSimulator, GoalChecker,
    evaluation pipeline) will get a FakeLLMModel instead of calling
    OpenRouter.
    """
    fake = FakeLLMModel()

    with patch(
        "server.eval.ewan_eval.model_resolver.require_ewan_model",
        return_value=fake,
    ), patch(
        "server.eval.ewan_eval.model_resolver.resolve_ewan_model",
        return_value=fake,
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
def bench_root():
    """Real bench root for loading tasks/personas JSON files."""
    return BENCH_ROOT


@pytest.fixture
def app(bench_root):
    """Create a QuantTutorBench ASGI app for in-process testing.

    Uses ``use_docker=False`` so no Docker daemon is required.
    Auto-eval is disabled to keep tests fast and deterministic.

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
        auto_eval=False,
    )


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace directory for attachment tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws
