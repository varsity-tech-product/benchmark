"""Unit tests for _safe_tool_log_dict (issue #135).

The /ui/runs/live snapshot iterates tool-log entries from
session.proxy.get_logs(). For live sessions those are ToolCallLog
dataclass instances; for restored completed sessions they are wrapped
as SimpleNamespace by SessionState.restore_from_storage. The helper
must serialise both shapes without raising.
"""

from types import SimpleNamespace

from server.core.proxy import ToolCallLog
from server.web.ui_app import _live_required_tool_coverage, _safe_tool_log_dict


def test_dict_passes_through():
    log = {"name": "shell_exec", "args": {"command": "ls"}, "success": True}
    assert _safe_tool_log_dict(log) == log


def test_dataclass_serialises_via_asdict():
    log = ToolCallLog(
        name="shell_exec",
        args={"command": "ls"},
        call_id="c1",
        result="file.txt\n",
        success=True,
        turn_index=1,
    )
    out = _safe_tool_log_dict(log)
    assert isinstance(out, dict)
    assert out["name"] == "shell_exec"
    assert out["args"] == {"command": "ls"}
    assert out["success"] is True


def test_simplenamespace_falls_back_to_dunder_dict():
    # Restored sessions use SimpleNamespace(**log) for tool logs.
    log = SimpleNamespace(
        name="shell_exec",
        args={"command": "ls"},
        success=True,
        turn_index=1,
    )
    out = _safe_tool_log_dict(log)
    assert isinstance(out, dict)
    assert out["name"] == "shell_exec"
    assert out["args"] == {"command": "ls"}


def test_unknown_object_returns_raw_marker():
    class Opaque:
        __slots__ = ()

    out = _safe_tool_log_dict(Opaque())
    assert isinstance(out, dict)
    assert "raw" in out


def test_live_required_tool_coverage_computes_from_proxy_logs():
    session_state = SimpleNamespace(
        task=SimpleNamespace(
            ground_truth=SimpleNamespace(
                expected_mcp_tools=["file_read", "shell_exec", "run_backtest"]
            )
        ),
        proxy=SimpleNamespace(
            get_logs=lambda: [
                ToolCallLog(name="file_read", args={}, result="ok"),
                ToolCallLog(name="shell_exec", args={}, result="ok"),
            ]
        ),
    )

    coverage = _live_required_tool_coverage(session_state)

    assert coverage["coverage_ratio"] == 0.6667
    assert coverage["missing_tools"] == ["run_backtest"]


def test_live_required_tool_coverage_skips_without_expected_tools():
    session_state = SimpleNamespace(
        task=SimpleNamespace(ground_truth=SimpleNamespace(expected_mcp_tools=[])),
        proxy=SimpleNamespace(get_logs=lambda: []),
    )

    assert _live_required_tool_coverage(session_state) is None
