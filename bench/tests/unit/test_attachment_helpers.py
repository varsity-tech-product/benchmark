"""Unit tests for send_message attachment helpers.

Tests _read_attachment and _resolve_attachments in isolation —
no server, no session, no LLM.
"""

import os
import tempfile

import pytest

from server.core.session import (
    _ATTACHMENT_MAX_CHARS,
    _ATTACHMENT_MAX_FILES,
    _read_attachment,
    _resolve_attachments,
)


@pytest.fixture
def workspace():
    """Create a temp workspace with sample files."""
    with tempfile.TemporaryDirectory(prefix="test_ws_") as ws:
        # Normal text file
        with open(os.path.join(ws, "strategy.py"), "w") as f:
            f.write('import pandas as pd\ndf = pd.read_csv("data.csv")\n')

        # Large file (will be truncated)
        with open(os.path.join(ws, "big.txt"), "w") as f:
            f.write("x" * (_ATTACHMENT_MAX_CHARS + 5000))

        # Image file (now supported)
        with open(os.path.join(ws, "chart.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        # Binary file (still rejected)
        with open(os.path.join(ws, "model.pkl"), "wb") as f:
            f.write(b"\x80\x04\x95")

        # Subdirectory with nested file
        os.makedirs(os.path.join(ws, "results"))
        with open(os.path.join(ws, "results", "backtest.json"), "w") as f:
            f.write('{"sharpe": 0.62}')

        # Empty file
        with open(os.path.join(ws, "empty.txt"), "w") as f:
            pass

        yield ws


class TestReadAttachment:
    def test_reads_normal_file(self, workspace):
        att = _read_attachment(workspace, "strategy.py")
        assert att["filename"] == "strategy.py"
        assert "pandas" in att["content"]
        assert att["truncated"] is False

    def test_reads_nested_file(self, workspace):
        att = _read_attachment(workspace, "results/backtest.json")
        assert att["filename"] == "results/backtest.json"
        assert "sharpe" in att["content"]

    def test_truncates_large_file(self, workspace):
        att = _read_attachment(workspace, "big.txt")
        assert att["truncated"] is True
        assert len(att["content"]) < _ATTACHMENT_MAX_CHARS + 200
        assert "chars total" in att["content"]

    def test_reads_empty_file(self, workspace):
        att = _read_attachment(workspace, "empty.txt")
        assert att["content"] == ""
        assert att["truncated"] is False

    def test_rejects_path_traversal(self, workspace):
        with pytest.raises(ValueError, match="outside workspace"):
            _read_attachment(workspace, "../../etc/passwd")

    def test_rejects_absolute_path(self, workspace):
        with pytest.raises(ValueError, match="outside workspace"):
            _read_attachment(workspace, "/etc/passwd")

    def test_rejects_missing_file(self, workspace):
        with pytest.raises(ValueError, match="not found"):
            _read_attachment(workspace, "nonexistent.py")

    def test_rejects_binary_file(self, workspace):
        with pytest.raises(ValueError, match="Binary"):
            _read_attachment(workspace, "model.pkl")

    def test_reads_image_file(self, workspace):
        att = _read_attachment(workspace, "chart.png")
        assert att["is_image"] is True
        assert att["media_type"] == "image/png"
        assert att["truncated"] is False
        # Content should be valid base64
        import base64
        base64.b64decode(att["content"])

    def test_text_file_has_is_image_false(self, workspace):
        att = _read_attachment(workspace, "strategy.py")
        assert att["is_image"] is False

    def test_rejects_no_workspace(self):
        with pytest.raises(ValueError, match="No workspace"):
            _read_attachment("", "anything.txt")


class TestResolveAttachments:
    def test_empty_list_returns_empty(self, workspace):
        atts, errs = _resolve_attachments(workspace, [])
        assert atts == []
        assert errs == []

    def test_resolves_valid_files(self, workspace):
        atts, errs = _resolve_attachments(
            workspace, ["strategy.py", "results/backtest.json"]
        )
        assert len(atts) == 2
        assert errs == []
        assert atts[0]["filename"] == "strategy.py"
        assert atts[1]["filename"] == "results/backtest.json"

    def test_rejects_over_max_files(self, workspace):
        paths = [f"file{i}.txt" for i in range(_ATTACHMENT_MAX_FILES + 1)]
        atts, errs = _resolve_attachments(workspace, paths)
        assert atts == []
        assert len(errs) == 1
        assert "Maximum" in errs[0]

    def test_collects_partial_errors(self, workspace):
        atts, errs = _resolve_attachments(
            workspace, ["strategy.py", "nonexistent.py"]
        )
        assert len(atts) == 1
        assert atts[0]["filename"] == "strategy.py"
        assert len(errs) == 1
        assert "not found" in errs[0]

    def test_none_workspace(self):
        atts, errs = _resolve_attachments(None, ["file.txt"])
        assert atts == []
        assert len(errs) == 1
