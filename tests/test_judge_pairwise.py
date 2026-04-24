import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.judge_validation import run as judge_run
from experiments.judge_validation.pairwise_stats import (
    PASS_THRESHOLD,
    compute_pairwise_stats,
)
from server.eval.judges.runtime.conv_pairwise import (
    EwanPairwiseGEval,
    PairwiseTestCase,
    VALID_MARGIN,
    VALID_PREFERRED,
)


class _StubModel:
    """Deterministic model stub that returns a fixed JSON payload."""

    def __init__(self, payload: dict, model_name: str = "stub/test-model"):
        self._payload = payload
        self._model_name = model_name
        self.temperature = 0.0

    def get_model_name(self) -> str:
        return self._model_name

    async def a_generate(self, prompt: str):
        return json.dumps(self._payload), 0.0


def test_prompt_renders_shared_student_message_and_both_responses():
    metric = EwanPairwiseGEval(
        name="adv_test",
        criteria="## Dimension: Finance (1-5 scale)\n\nScore 1: bad.\nScore 5: good.",
        role="You are an Educational Analyst.",
        rules="- Compare two tutor responses.",
        model=_StubModel({"preferred": "A", "margin": "clear", "reason": "ok"}),
        rubric_metadata={
            "rubric_id": "student_adaptation.v1",
            "rubric_version": "1",
            "dimension": "D1_finance_adaptation",
        },
    )
    tc = PairwiseTestCase(
        shared_context="Why is my Sharpe negative?",
        response_a="Response A text here.",
        response_b="Response B text here.",
    )
    prompt = metric.render_prompt(tc)
    assert "Why is my Sharpe negative?" in prompt
    assert "Response A text here." in prompt
    assert "Response B text here." in prompt
    assert "Comparison Mode: pairwise" in prompt
    assert "Rubric ID: student_adaptation.v1" in prompt


def test_a_measure_parses_preferred_margin_and_reason():
    payload = {"preferred": "B", "margin": "strong", "reason": "B cites evidence"}
    metric = EwanPairwiseGEval(
        name="t",
        criteria="rubric",
        rubric_metadata={"dimension": "D4", "rubric_id": "x.v1", "rubric_version": "1"},
        model=_StubModel(payload),
    )
    tc = PairwiseTestCase(shared_context="?", response_a="A", response_b="B")
    asyncio.run(metric.a_measure(tc))
    assert metric.preferred == "B"
    assert metric.margin == "strong"
    assert metric.reason == "B cites evidence"


def test_a_measure_rejects_invalid_preferred():
    payload = {"preferred": "X", "reason": ""}
    metric = EwanPairwiseGEval(
        name="t",
        criteria="rubric",
        rubric_metadata={"dimension": "D4", "rubric_id": "x.v1", "rubric_version": "1"},
        model=_StubModel(payload),
    )
    tc = PairwiseTestCase(shared_context="?", response_a="A", response_b="B")
    raised = False
    try:
        asyncio.run(metric.a_measure(tc))
    except ValueError:
        raised = True
    assert raised


def test_a_measure_drops_invalid_margin_silently():
    payload = {"preferred": "tie", "margin": "lukewarm", "reason": "tie"}
    metric = EwanPairwiseGEval(
        name="t",
        criteria="rubric",
        rubric_metadata={"dimension": "D4", "rubric_id": "x.v1", "rubric_version": "1"},
        model=_StubModel(payload),
    )
    tc = PairwiseTestCase(shared_context="?", response_a="A", response_b="B")
    asyncio.run(metric.a_measure(tc))
    assert metric.preferred == "tie"
    assert metric.margin == ""


def test_valid_preferred_and_margin_sets():
    assert VALID_PREFERRED == {"A", "B", "tie"}
    assert VALID_MARGIN == {"slight", "clear", "strong"}


def test_pair_student_message_returns_shared_first_turn():
    turns_a = [
        judge_run.Turn(role="user", content="Q"),
        judge_run.Turn(role="assistant", content="A"),
    ]
    turns_b = [
        judge_run.Turn(role="user", content="Q"),
        judge_run.Turn(role="assistant", content="B"),
    ]
    assert judge_run._pair_student_message(turns_a, turns_b) == "Q"


def test_pairwise_inputs_splits_qr_context_into_shared_and_per_side():
    stronger = {
        "track": "qr",
        "context": (
            "## Task\nFix the SMA bug.\n\n"
            "## Required Capabilities (Acceptance Criteria)\n"
            "- Correct timing.\n\n"
            "## Agent Result\n"
            "Agent shifted signals; pytest passed."
        ),
    }
    weaker = {
        "track": "qr",
        "context": (
            "## Task\nFix the SMA bug.\n\n"
            "## Required Capabilities (Acceptance Criteria)\n"
            "- Correct timing.\n\n"
            "## Agent Result\n"
            "Agent renamed variables; timing unchanged."
        ),
    }
    shared, s_resp, w_resp = judge_run._pairwise_inputs(stronger, weaker)
    assert "## Task" in shared
    assert "## Required Capabilities" in shared
    assert "## Agent Result" not in shared
    assert "Agent shifted signals" in s_resp
    assert "Agent renamed variables" in w_resp
    assert s_resp.startswith("## Agent Result")
    assert w_resp.startswith("## Agent Result")


def test_pairwise_inputs_falls_back_when_qr_context_missing_marker():
    stronger = {"track": "qr", "context": "just a plain context blob."}
    weaker = {"track": "qr", "context": "another plain blob."}
    shared, s_resp, w_resp = judge_run._pairwise_inputs(stronger, weaker)
    # No marker — shared is stronger context, per-side falls back to the same
    assert "plain context" in shared
    assert s_resp == shared
    assert w_resp  # non-empty fallback


def test_pairwise_inputs_tutor_uses_conversation():
    stronger = {
        "track": "tutor",
        "conversation": [
            {"role": "user", "content": "Why is my Sharpe negative?"},
            {"role": "assistant", "content": "Because you're shorting."},
        ],
    }
    weaker = {
        "track": "tutor",
        "conversation": [
            {"role": "user", "content": "Why is my Sharpe negative?"},
            {"role": "assistant", "content": "The window is too short."},
        ],
    }
    shared, s_resp, w_resp = judge_run._pairwise_inputs(stronger, weaker)
    assert shared == "Why is my Sharpe negative?"
    assert "Because you're shorting." in s_resp
    assert "The window is too short." in w_resp


def test_tutor_response_keeps_role_markers():
    turns = [
        judge_run.Turn(role="user", content="Q"),
        judge_run.Turn(role="assistant", content="reply one"),
        judge_run.Turn(role="tool", content="raw output"),
    ]
    out = judge_run._tutor_response(turns)
    assert "Q" not in out
    assert "[Assistant]" in out and "reply one" in out
    assert "[Tool]" in out and "raw output" in out


def _synth_records(
    stronger_pick_rate: float,
    a_side_bias: float,
    pair_count: int = 7,
    repeats: int = 3,
) -> list[dict]:
    """Build synthetic pairwise records for stats testing.

    ``stronger_pick_rate`` controls the fraction of records where the
    judge correctly picks the stronger side. ``a_side_bias`` offsets the
    probability of returning slot A (ideal 0.5 → no bias).
    """
    records = []
    for i in range(pair_count):
        pair_id = f"pair_{i}"
        for rep in range(repeats):
            for a_side in ("stronger", "weaker"):
                picks_stronger = ((i + rep + (0 if a_side == "stronger" else 1)) % 10) / 10.0 < stronger_pick_rate
                picks_a = ((i * 7 + rep * 3) % 10) / 10.0 < 0.5 + a_side_bias
                if picks_stronger:
                    preferred_side = "stronger"
                    preferred_slot = "A" if a_side == "stronger" else "B"
                else:
                    preferred_side = "weaker"
                    preferred_slot = "B" if a_side == "stronger" else "A"
                if picks_a:
                    preferred_slot = "A"
                records.append(
                    {
                        "status": "success",
                        "pair_id": pair_id,
                        "dimension": "D4",
                        "a_side": a_side,
                        "run_index": rep,
                        "preferred_slot": preferred_slot,
                        "preferred_side": preferred_side,
                    }
                )
    return records


def test_stats_gate_passes_on_perfect_judge():
    pairs = [
        {
            "pair_id": f"pair_{i}",
            "dimension": "D4",
            "registry_rubric_id": "quant_correctness.v1",
            "stronger_sample_id": f"s{i}",
            "weaker_sample_id": f"w{i}",
        }
        for i in range(7)
    ]
    records = []
    for pair in pairs:
        for rep in range(3):
            for a_side in ("stronger", "weaker"):
                preferred_slot = "A" if a_side == "stronger" else "B"
                records.append(
                    {
                        "status": "success",
                        "pair_id": pair["pair_id"],
                        "dimension": "D4",
                        "a_side": a_side,
                        "run_index": rep,
                        "preferred_slot": preferred_slot,
                        "preferred_side": "stronger",
                    }
                )
    stats = compute_pairwise_stats(corpus={"adversarial_pairs": pairs}, records=records)
    assert stats["overall"]["stronger_preferred_rate"] == 1.0
    assert stats["overall"]["swap_consistency"] == 1.0
    assert stats["overall"]["a_side_preference_rate"] == 0.5
    assert stats["gate"]["status"] == "pass"


def test_stats_gate_flags_a_side_bias():
    pairs = [
        {
            "pair_id": f"pair_{i}",
            "dimension": "D4",
            "registry_rubric_id": "quant_correctness.v1",
            "stronger_sample_id": f"s{i}",
            "weaker_sample_id": f"w{i}",
        }
        for i in range(7)
    ]
    # Judge always picks slot A regardless of content — worst-case bias.
    records = []
    for pair in pairs:
        for rep in range(3):
            for a_side in ("stronger", "weaker"):
                preferred_side = "stronger" if a_side == "stronger" else "weaker"
                records.append(
                    {
                        "status": "success",
                        "pair_id": pair["pair_id"],
                        "dimension": "D4",
                        "a_side": a_side,
                        "run_index": rep,
                        "preferred_slot": "A",
                        "preferred_side": preferred_side,
                    }
                )
    stats = compute_pairwise_stats(corpus={"adversarial_pairs": pairs}, records=records)
    # stronger picked half the time, swap inconsistent, A side dominates
    assert stats["overall"]["stronger_preferred_rate"] == 0.5
    assert stats["overall"]["swap_consistency"] == 0.0
    assert stats["overall"]["a_side_preference_rate"] == 1.0
    assert stats["gate"]["status"] != "pass"
    assert "a_side_preference_drift" in (stats["gate"]["failures"] or [])
    assert "swap_consistency_below_target" in (stats["gate"]["failures"] or [])
    assert "stronger_preferred_rate_below_target" in (stats["gate"]["failures"] or [])


def test_thresholds_expose_expected_keys():
    assert {
        "stronger_preferred_rate",
        "swap_consistency",
        "a_side_preference_tolerance",
    } == set(PASS_THRESHOLD.keys())
