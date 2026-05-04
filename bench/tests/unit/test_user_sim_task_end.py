"""Unit tests for the persona-emitted ``task_end`` flag (issue #139).

The user-sim JSON output now carries a ``task_end`` boolean alongside
``simulated_input``. The session loop terminates with
``reason=user_satisfied`` when ``task_end`` is true. Parser must
default to ``False`` when the field is missing, malformed, or the
non-canonical fallback paths are taken.
"""

import json

import pytest

from server.core.user_sim import _parse_simulated_input_strict


def _wrap(text: str, task_end=None) -> str:
    payload: dict = {"simulated_input": text}
    if task_end is not None:
        payload["task_end"] = task_end
    return json.dumps(payload)


class TestTaskEndExtraction:
    def test_explicit_true_extracted(self):
        text, task_end, method = _parse_simulated_input_strict(
            _wrap("That clears it up — I'll go try this myself.", True)
        )
        assert task_end is True
        assert method == "json"
        assert "try this myself" in text

    def test_explicit_false_extracted(self):
        text, task_end, method = _parse_simulated_input_strict(
            _wrap("Wait — what about edge cases?", False)
        )
        assert task_end is False
        assert method == "json"

    def test_missing_field_defaults_to_false(self):
        text, task_end, method = _parse_simulated_input_strict(
            _wrap("Got it, makes sense.")
        )
        assert task_end is False
        assert method == "json"

    @pytest.mark.parametrize(
        "bad_value",
        ["true", "false", "yes", 1, 0, None, [], {}],
    )
    def test_non_bool_values_default_to_false(self, bad_value):
        # Strict bool: only Python ``True`` terminates. String "true",
        # integer 1, lists, dicts — all coerce to False so that a
        # malformed model output never accidentally truncates a session.
        text, task_end, _ = _parse_simulated_input_strict(
            _wrap("Reply text.", bad_value)
        )
        assert task_end is False


class TestFallbackPathsForceFalse:
    def test_single_value_fallback_forces_false(self):
        # The model dropped the canonical key but the only string value
        # is usable. The fallback path must not surface task_end=True
        # because the field's intent is undefined here.
        raw = json.dumps(
            {"reply": "Sure, that helps. I'll think on it.", "task_end": True}
        )
        text, task_end, method = _parse_simulated_input_strict(raw)
        assert method == "json_single_value"
        assert task_end is False

    def test_natural_language_fallback_forces_false(self):
        # No JSON at all — user message accepted as raw text. No way
        # to express task_end without the schema, so default False.
        text, task_end, method = _parse_simulated_input_strict(
            "Got it, that makes sense."
        )
        assert method == "fallback_text"
        assert task_end is False


class TestEmptyAndMalformed:
    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="empty:"):
            _parse_simulated_input_strict("")

    def test_whitespace_input_raises(self):
        with pytest.raises(ValueError, match="empty:"):
            _parse_simulated_input_strict("   \n\t  ")

    def test_json_without_simulated_input_raises(self):
        # Object has multiple non-string-or-empty values, no fallback.
        raw = json.dumps({"reply_a": "", "reply_b": ""})
        with pytest.raises(ValueError):
            _parse_simulated_input_strict(raw)
