"""Tests for issue #42 — silent eval-component failures must surface.

`pipeline.evaluate_task` catches per-component exceptions (missing rubric,
failed result judge, failed code eval) and returns an empty/zero score
with the error text stashed under `{component}_eval_error` in eval_results.
Before this fix those error strings were dropped before reaching
`eval_meta.json` or `/session/{sid}/scores`, leaving callers with empty
`tutor_scores: {}` and no way to tell if it was a genuine zero or a
silent upstream failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.storage.eval_writer import _collect_eval_errors


class TestCollectEvalErrors:
    def test_empty_results_returns_empty_dict(self):
        assert _collect_eval_errors({}) == {}

    def test_results_without_error_keys_returns_empty_dict(self):
        assert _collect_eval_errors({"quant_result": 0.7, "tutor_scores": {}}) == {}

    def test_tutor_error_is_surfaced_without_suffix(self):
        out = _collect_eval_errors(
            {"tutor_eval_error": "Rubric file not found: /path/rubric.json"}
        )
        assert out == {"tutor": "Rubric file not found: /path/rubric.json"}

    def test_multiple_errors_coexist(self):
        out = _collect_eval_errors(
            {
                "tutor_eval_error": "Rubric missing",
                "quant_result_error": "eval_script crashed",
                "code_eval_error": "reference store timeout",
                "tool_usage_error": "registry lookup failed",
            }
        )
        assert out == {
            "tutor": "Rubric missing",
            "quant_result": "eval_script crashed",
            "code_eval": "reference store timeout",
            "tool_usage": "registry lookup failed",
        }

    def test_tool_usage_error_surfaces_standalone(self):
        # Tool-usage eval feeds quant_process; when it fails silently the
        # QP score comes back derived/default and callers have no way to
        # tell unless the error is surfaced alongside.
        assert _collect_eval_errors({"tool_usage_error": "boom"}) == {
            "tool_usage": "boom"
        }

    def test_non_string_error_is_stringified(self):
        err = ValueError("bad")
        out = _collect_eval_errors({"tutor_eval_error": err})
        assert out == {"tutor": "bad"}

    def test_empty_string_error_is_ignored(self):
        # Guard against a no-op error stash producing a misleading key.
        assert _collect_eval_errors({"tutor_eval_error": ""}) == {}

    def test_none_error_is_ignored(self):
        assert _collect_eval_errors({"tutor_eval_error": None}) == {}

    def test_unknown_error_keys_are_not_surfaced(self):
        # Only the three known component keys propagate; anything else is
        # left in eval_results for internal use but does not leak to the
        # API surface.
        assert _collect_eval_errors({"some_other_error": "nope"}) == {}
