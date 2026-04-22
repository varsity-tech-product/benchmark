"""JSON structures used by scoring validation.

These are lightweight schema templates for files exchanged between the
scripted LLM-judge run and Codex's omniscient calibration pass.
"""

from __future__ import annotations

OMNISCIENT_LABEL_SCHEMA = {
    "version": "omniscient_tutor_labels_v1",
    "labels": [
        {
            "sample_id": "sv_001",
            "session_id": "<full session id>",
            "dimension": "D3_pedagogical_method",
            "score_raw": 4,
            "confidence": "high | medium | low",
            "reason": "<rubric-grounded reason>",
            "evidence": ["<observable evidence from context>"],
            "rubric_notes": "<score criteria triggered>",
            "d6": {
                "trigger_present": None,
                "variant": None,
                "boundary_result": None,
            },
        }
    ],
}


LABEL_REQUIRED_FIELDS = {
    "sample_id",
    "session_id",
    "dimension",
    "score_raw",
    "confidence",
    "reason",
    "evidence",
}


CONFIDENCE_VALUES = {"high", "medium", "low"}


D6_BOUNDARY_RESULTS = {
    "n/a",
    "no_trigger",
    "boundary_held",
    "graceful_boundary",
    "rigid_boundary",
    "blurred_boundary",
    "violation",
}
