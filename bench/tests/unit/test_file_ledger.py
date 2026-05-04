"""Unit tests for file ledger and transcript-with-files formatting.

Tests _compute_file_diff, _format_transcript_with_files, and the
ledger budget degradation logic in isolation — no server, no LLM.
"""

import json

import pytest

from server.core.user_sim import (
    _LEDGER_BUDGET,
    _collect_images_from_ledger,
    _compute_file_diff,
    _format_transcript,
    _format_transcript_with_files,
)


# ---------------------------------------------------------------------------
# _compute_file_diff
# ---------------------------------------------------------------------------


class TestComputeFileDiff:
    def test_single_line_change(self):
        base = "line1\nline2\nline3\n"
        current = "line1\nchanged\nline3\n"
        diff = _compute_file_diff(base, current, "test.py")
        assert "--- a/test.py" in diff
        assert "+++ b/test.py" in diff
        assert "-line2" in diff
        assert "+changed" in diff

    def test_addition(self):
        base = "line1\n"
        current = "line1\nline2\n"
        diff = _compute_file_diff(base, current, "test.py")
        assert "+line2" in diff

    def test_deletion(self):
        base = "line1\nline2\n"
        current = "line1\n"
        diff = _compute_file_diff(base, current, "test.py")
        assert "-line2" in diff

    def test_identical_returns_empty(self):
        content = "line1\nline2\n"
        diff = _compute_file_diff(content, content, "test.py")
        assert diff == ""

    def test_empty_to_content(self):
        diff = _compute_file_diff("", "hello\n", "new.py")
        assert "+hello" in diff

    def test_content_to_empty(self):
        diff = _compute_file_diff("hello\n", "", "old.py")
        assert "-hello" in diff


# ---------------------------------------------------------------------------
# _format_transcript_with_files
# ---------------------------------------------------------------------------


def _make_conversation(*turns):
    """Build a conversation list from (role, content, [attachments]) tuples."""
    result = []
    for t in turns:
        entry = {"role": t[0], "content": t[1]}
        if len(t) > 2 and t[2]:
            entry["attachments"] = t[2]
        result.append(entry)
    return result


class TestFormatTranscriptWithFiles:
    def test_no_files_falls_back_to_plain(self):
        conv = _make_conversation(
            ("user", "hello"),
            ("assistant", "hi there"),
        )
        result = _format_transcript_with_files(conv, {})
        plain = _format_transcript(conv)
        assert result == plain

    def test_first_share_shows_full_content(self):
        code = "import pandas as pd\ndf = pd.read_csv('data.csv')\n"
        conv = _make_conversation(
            ("user", "help me"),
            ("assistant", "here's code", [
                {"filename": "strategy.py", "content": code, "truncated": False}
            ]),
        )
        ledger = {
            "strategy.py": {
                "prev_content": code,
                "prev_turn": 1,
                "current_content": code,
                "current_turn": 1,
            }
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert "files" in parsed[1]
        assert "[File: strategy.py]" in parsed[1]["files"]
        assert "pandas" in parsed[1]["files"]

    def test_updated_file_shows_diff(self):
        # File must be large enough that the diff is smaller than the full content.
        # Unified diff headers add ~80 chars of overhead, so the file needs
        # many unchanged lines for the diff to be more compact.
        lines = [f"line_{i} = {i}" for i in range(30)]
        base = "\n".join(lines) + "\n"
        lines[15] = "line_15 = 999  # FIXED"
        updated = "\n".join(lines) + "\n"

        conv = _make_conversation(
            ("user", "help"),
            ("assistant", "found bug", [
                {"filename": "s.py", "content": base, "truncated": False}
            ]),
            ("user", "ok"),
            ("assistant", "fixed it", [
                {"filename": "s.py", "content": updated, "truncated": False}
            ]),
        )
        ledger = {
            "s.py": {
                "prev_content": base,
                "prev_turn": 1,
                "current_content": updated,
                "current_turn": 2,
            }
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)

        # First share: full content
        assert "[File: s.py]" in parsed[1]["files"]
        assert "line_0" in parsed[1]["files"]

        # Second share: diff (smaller than full file)
        assert "(updated)" in parsed[3]["files"]
        assert "-line_15 = 15" in parsed[3]["files"]
        assert "+line_15 = 999" in parsed[3]["files"]

    def test_diff_larger_than_full_falls_back(self):
        """When diff is larger than full content, show full content instead."""
        # Completely different files produce a large diff
        base = "aaa\n"
        current = "completely\ndifferent\ncontent\nwith\nmany\nnew\nlines\n"
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "v1", [
                {"filename": "f.py", "content": base, "truncated": False}
            ]),
            ("user", "ok"),
            ("assistant", "v2", [
                {"filename": "f.py", "content": current, "truncated": False}
            ]),
        )
        ledger = {
            "f.py": {
                "prev_content": base,
                "prev_turn": 1,
                "current_content": current,
                "current_turn": 2,
            }
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        # Second entry should have file content (either diff or full)
        assert "files" in parsed[3]

    def test_entries_without_attachments_unchanged(self):
        conv = _make_conversation(
            ("user", "hello"),
            ("assistant", "hi"),
            ("user", "bye"),
        )
        ledger = {"some.py": {"prev_content": "x", "prev_turn": 1, "current_content": "x", "current_turn": 1}}
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        for entry in parsed:
            assert "files" not in entry

    def test_multiple_files_same_turn(self):
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "here", [
                {"filename": "a.py", "content": "aaa\n", "truncated": False},
                {"filename": "b.py", "content": "bbb\n", "truncated": False},
            ]),
        )
        ledger = {
            "a.py": {"prev_content": "aaa\n", "prev_turn": 1, "current_content": "aaa\n", "current_turn": 1},
            "b.py": {"prev_content": "bbb\n", "prev_turn": 1, "current_content": "bbb\n", "current_turn": 1},
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        assert "[File: a.py]" in parsed[1]["files"]
        assert "[File: b.py]" in parsed[1]["files"]


class TestBudgetDegradation:
    def test_under_budget_shows_all(self):
        small_content = "x" * 100
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "v1", [
                {"filename": "f.py", "content": small_content, "truncated": False}
            ]),
        )
        ledger = {
            "f.py": {"prev_content": small_content, "prev_turn": 1, "current_content": small_content, "current_turn": 1},
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        assert small_content in parsed[1]["files"]

    def test_over_budget_degrades_oldest(self):
        # Create content that exceeds budget
        big_content = "x" * (_LEDGER_BUDGET + 1000)
        small_content = "y" * 100
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "old file", [
                {"filename": "old.py", "content": big_content, "truncated": False}
            ]),
            ("user", "ok"),
            ("assistant", "new file", [
                {"filename": "new.py", "content": small_content, "truncated": False}
            ]),
        )
        ledger = {
            "old.py": {"prev_content": big_content, "prev_turn": 1, "current_content": big_content, "current_turn": 1},
            "new.py": {"prev_content": small_content, "prev_turn": 2, "current_content": small_content, "current_turn": 2},
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)

        # Old file should be degraded to reference
        assert "[Attached: old.py]" in parsed[1]["files"]
        assert big_content not in parsed[1]["files"]

        # New file should still have full content
        assert small_content in parsed[3]["files"]

    def test_custom_budget(self):
        content = "x" * 500
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "file", [
                {"filename": "f.py", "content": content, "truncated": False}
            ]),
        )
        ledger = {
            "f.py": {"prev_content": content, "prev_turn": 1, "current_content": content, "current_turn": 1},
        }
        # Budget smaller than content → degrades
        result = _format_transcript_with_files(conv, ledger, budget=100)
        parsed = json.loads(result)
        assert "[Attached: f.py]" in parsed[1]["files"]
        assert content not in parsed[1]["files"]

    def test_empty_ledger_no_files_field(self):
        conv = _make_conversation(
            ("user", "hello"),
            ("assistant", "hi"),
        )
        result = _format_transcript_with_files(conv, {})
        parsed = json.loads(result)
        for entry in parsed:
            assert "files" not in entry


class TestFileLedgerIntegration:
    """Test the ledger update logic as it would work in TutoringSession."""

    def test_ledger_dedup_on_same_file(self):
        """Simulates agent sending same file twice — prev shifts, current updates."""
        ledger: dict[str, dict] = {}
        v1 = "version1\n"
        v2 = "version2\n"

        # First send
        ledger["strategy.py"] = {
            "prev_content": v1,
            "prev_turn": 1,
            "current_content": v1,
            "current_turn": 1,
        }

        # Second send — shift current → prev, update current
        ledger["strategy.py"]["prev_content"] = ledger["strategy.py"]["current_content"]
        ledger["strategy.py"]["current_content"] = v2
        ledger["strategy.py"]["current_turn"] = 2

        assert ledger["strategy.py"]["prev_content"] == v1
        assert ledger["strategy.py"]["current_content"] == v2
        assert len(ledger) == 1

    def test_ledger_multiple_files(self):
        ledger: dict[str, dict] = {}
        ledger["a.py"] = {"prev_content": "a", "prev_turn": 1, "current_content": "a", "current_turn": 1}
        ledger["b.py"] = {"prev_content": "b", "prev_turn": 2, "current_content": "b", "current_turn": 2}
        assert len(ledger) == 2
        assert "a.py" in ledger
        assert "b.py" in ledger


# ---------------------------------------------------------------------------
# Image support
# ---------------------------------------------------------------------------

_FAKE_IMAGE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def _image_ledger_entry(fname="chart.png", turn=1):
    return {
        "prev_content": None,
        "prev_turn": turn,
        "current_content": None,
        "current_turn": turn,
        "is_image": True,
        "media_type": "image/png",
        "path": fname,
    }


def _text_ledger_entry(content="print('hello')\n", turn=1):
    return {
        "prev_content": content,
        "prev_turn": turn,
        "current_content": content,
        "current_turn": turn,
        "is_image": False,
        "media_type": "",
        "path": None,
    }


class TestImageInTranscript:
    def test_image_shows_reference_not_content(self):
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "here's the chart", [
                {"filename": "chart.png", "content": _FAKE_IMAGE_B64,
                 "truncated": False, "is_image": True, "media_type": "image/png"}
            ]),
        )
        ledger = {"chart.png": _image_ledger_entry()}
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        assert "[Image: chart.png]" in parsed[1]["files"]
        # Base64 data must NOT appear in transcript
        assert _FAKE_IMAGE_B64 not in result

    def test_image_does_not_count_against_budget(self):
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "image", [
                {"filename": "chart.png", "content": _FAKE_IMAGE_B64,
                 "truncated": False, "is_image": True, "media_type": "image/png"}
            ]),
        )
        ledger = {"chart.png": _image_ledger_entry()}
        # Even with budget=1 (impossibly small), image reference survives
        result = _format_transcript_with_files(conv, ledger, budget=1)
        parsed = json.loads(result)
        assert "[Image: chart.png]" in parsed[1]["files"]

    def test_mixed_text_and_image_attachments(self):
        code = "import pandas as pd\n"
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "code and chart", [
                {"filename": "strategy.py", "content": code,
                 "truncated": False, "is_image": False},
                {"filename": "plot.png", "content": _FAKE_IMAGE_B64,
                 "truncated": False, "is_image": True, "media_type": "image/png"},
            ]),
        )
        ledger = {
            "strategy.py": _text_ledger_entry(code),
            "plot.png": _image_ledger_entry("plot.png"),
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        files = parsed[1]["files"]
        assert "[File: strategy.py]" in files
        assert "[Image: plot.png]" in files
        assert "pandas" in files

    def test_image_updated_shows_reference(self):
        """Re-attaching same image shows [Image: ...] — no diff."""
        new_b64 = "AAAA"
        conv = _make_conversation(
            ("user", "hi"),
            ("assistant", "v1", [
                {"filename": "c.png", "content": _FAKE_IMAGE_B64,
                 "truncated": False, "is_image": True, "media_type": "image/png"}
            ]),
            ("user", "ok"),
            ("assistant", "v2", [
                {"filename": "c.png", "content": new_b64,
                 "truncated": False, "is_image": True, "media_type": "image/png"}
            ]),
        )
        ledger = {
            "c.png": {
                **_image_ledger_entry("c.png"),
                "current_content": new_b64,
                "current_turn": 2,
            }
        }
        result = _format_transcript_with_files(conv, ledger)
        parsed = json.loads(result)
        assert "[Image: c.png]" in parsed[1]["files"]
        assert "[Image: c.png]" in parsed[3]["files"]
        assert "updated" not in parsed[3]["files"]


class TestCollectImagesFromLedger:
    def test_empty_ledger_returns_empty(self):
        assert _collect_images_from_ledger({}) == []

    def test_no_workspace_returns_empty(self):
        ledger = {"chart.png": _image_ledger_entry()}
        assert _collect_images_from_ledger(ledger, workspace_path=None) == []

    def test_text_only_ledger_returns_empty(self, tmp_path):
        ledger = {"strategy.py": _text_ledger_entry()}
        assert _collect_images_from_ledger(ledger, str(tmp_path)) == []

    def test_single_image_reads_from_disk(self, tmp_path):
        # Write a small image file
        img_path = tmp_path / "chart.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        ledger = {"chart.png": _image_ledger_entry()}
        images = _collect_images_from_ledger(ledger, str(tmp_path))
        assert len(images) == 1
        assert images[0]["filename"] == "chart.png"
        assert images[0]["media_type"] == "image/png"
        # Should be valid base64
        import base64
        base64.b64decode(images[0]["data"])

    def test_mixed_ledger_returns_only_images(self, tmp_path):
        (tmp_path / "chart.png").write_bytes(b"\x89PNG")
        ledger = {
            "strategy.py": _text_ledger_entry(),
            "chart.png": _image_ledger_entry(),
        }
        images = _collect_images_from_ledger(ledger, str(tmp_path))
        assert len(images) == 1
        assert images[0]["filename"] == "chart.png"

    def test_missing_file_skipped(self, tmp_path):
        ledger = {"gone.png": _image_ledger_entry("gone.png")}
        images = _collect_images_from_ledger(ledger, str(tmp_path))
        assert images == []
