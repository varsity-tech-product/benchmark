import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.student_sim_stability.core.io_utils import (  # noqa: E402
    input_payload_hash as _input_payload_hash,
)
from experiments.student_sim_stability.core.rubrics import (  # noqa: E402
    RUBRICS_DIR,
)
from experiments.student_sim_stability.judge_qualification.cost import (  # noqa: E402
    estimate_judge_qualification_cost,
)
from experiments.student_sim_stability.judge_qualification.render import (  # noqa: E402
    load_corpus,
    render_judge_qualification_inputs,
)
from experiments.student_sim_stability.judge_qualification.report import (  # noqa: E402
    compute_judge_qualification_stats,
    write_judge_qualification_report,
)


def test_judge_qualification_corpus_references_existing_samples():
    corpus = load_corpus()
    sample_ids = {item["sample_id"] for item in corpus["items"]}

    assert corpus["version"] == "v1.4.0"
    assert len(sample_ids) == len(corpus["items"])
    assert {item["dimension"] for item in corpus["items"]} >= {
        "P1",
        "D3",
        "B1",
        "control",
    }
    for case in corpus["sensitivity_cases"]:
        assert case["baseline_sample_id"] in sample_ids
        assert case["perturbed_sample_id"] in sample_ids
        assert case["expected_direction"] == "baseline_higher"


def test_student_sim_rubrics_use_complete_ascending_score_scales():
    for rubric_path in sorted(RUBRICS_DIR.glob("*.json")):
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        assert list(rubric["score_scales"]) == ["1", "2", "3", "4", "5"]
        assert rubric["version"] == "v1.3.0"


def test_student_sim_prompts_include_ascending_human_readable_boundaries():
    for rubric_path in sorted(RUBRICS_DIR.glob("*.json")):
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        prompt_path = RUBRICS_DIR / rubric["prompt_template_path"]
        lines = prompt_path.read_text(encoding="utf-8").splitlines()
        score_lines = {
            key: idx
            for idx, line in enumerate(lines)
            for key in ["1", "2", "3", "4", "5"]
            if line.startswith(f"{key} = ")
        }

        assert set(score_lines) == {"1", "2", "3", "4", "5"}, prompt_path
        assert [score_lines[key] for key in ["1", "2", "3", "4", "5"]] == sorted(
            score_lines.values()
        )
        text = "\n".join(lines)
        assert "Score Ceiling Rules" in text
        assert "Use failure types consistently" in text
        assert "Return ONLY valid JSON" in text


def test_d3_prompt_warns_about_mixed_score_directions():
    prompt = (RUBRICS_DIR / "prompts" / "D3_drift_detection.txt").read_text(
        encoding="utf-8"
    )

    assert "Mixed Direction Warning" in prompt
    assert "persona_fidelity is high-good" in prompt
    assert "knowledge_leak is high-bad" in prompt
    assert "co_teacher_drift is high-bad" in prompt


def test_judge_qualification_render_writes_standalone_gate_directory(tmp_path):
    manifest = render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=1,
        prompt_variants=["baseline", "role_blocks"],
        clean=True,
    )

    assert manifest["counts"]["items"] == 10
    assert manifest["counts"]["judge_inputs"] == 16
    assert (tmp_path / "judge_inputs").exists()
    assert (tmp_path / "report" / "corpus_snapshot.json").exists()
    assert not (tmp_path / "judge_qualification").exists()

    sample = json.loads(
        next((tmp_path / "judge_inputs").glob("D3__PG__*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert sample["metadata"]["judge_qualification"] is True
    assert sample["metadata"]["prompt_variant_id"] in {"baseline", "role_blocks"}
    assert "rubric_id:" in sample["prompt"]


def test_judge_qualification_render_clean_removes_stale_report_artifacts(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=1,
        prompt_variants=["baseline"],
        clean=True,
    )
    report_dir = tmp_path / "report"
    stale_paths = [
        report_dir / "judge_qualification_stats.json",
        report_dir / "judge_qualification_report.md",
        report_dir / "judge_qualification_report.html",
        report_dir / "llm_cost_estimate.json",
    ]
    for stale_path in stale_paths:
        stale_path.write_text("stale\n", encoding="utf-8")

    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=1,
        prompt_variants=["baseline"],
        clean=True,
    )

    assert (report_dir / "corpus_snapshot.json").exists()
    assert (report_dir / "render_manifest.json").exists()
    for stale_path in stale_paths:
        assert not stale_path.exists()


def _scores_for_input(input_payload: dict, *, wrong_b1_identity: bool = False) -> dict:
    sample_id = input_payload["metadata"]["sample_id"]
    dimension = input_payload["dimension"]
    is_bad = any(
        marker in sample_id
        for marker in ["leak", "under", "drift", "generic_bad", "generic_student_bad"]
    )
    failure_types = []
    if "leak" in sample_id:
        failure_types = ["knowledge_leak", "co_teacher_drift"]
    elif "under" in sample_id:
        failure_types = ["under_competence", "persona_contract_contradiction"]
    elif "drift" in sample_id:
        failure_types = ["co_teacher_drift", "knowledge_leak"]
    elif "generic" in sample_id:
        failure_types = ["generic_student_behavior"]

    high = 5
    low = 2
    value = low if is_bad else high
    common = {
        "reasoning": "synthetic test output",
        "failure_types": failure_types,
        "dominant_failure_type": failure_types[0] if failure_types else None,
        "failure_evidence": "synthetic failure evidence" if failure_types else "",
    }
    if dimension == "P1":
        return {
            **common,
            "contract_fit": value,
            "facet_fit": value,
            "overall_probe_pass": value,
        }
    if dimension == "D3":
        return {
            **common,
            "per_turn": [
                {
                    "turn": 1,
                    "persona_fidelity": value,
                    "knowledge_leak": 1 if "knowledge_leak" in failure_types else 0,
                    "co_teacher_drift": 1 if "co_teacher_drift" in failure_types else 0,
                }
            ],
            "overall_drift_score": value,
            "drift_onset_turn": 1 if is_bad else None,
        }
    if dimension == "B1":
        identified_persona = input_payload["metadata"]["persona_id"]
        if wrong_b1_identity:
            identified_persona = "developer_crossover"
        return {
            **common,
            "identified_persona": identified_persona,
            "confidence": value,
            "contract_fit": value,
        }
    if dimension == "control":
        return {
            **common,
            "distinctiveness": value,
            "persona_value_add": "synthetic distinction",
        }
    raise AssertionError(f"unexpected dimension {dimension}")


def _write_synthetic_outputs(
    gate_dir: Path,
    *,
    output_dir: Path | None = None,
    model: str = "synthetic-judge",
    wrong_b1_identity: bool = False,
    stale_hash: bool = False,
) -> None:
    input_dir = gate_dir / "judge_inputs"
    output_dir = output_dir or gate_dir / "judge_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    for input_path in input_dir.glob("*.json"):
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        output = {
            "eval_id": payload["eval_id"],
            "dimension": payload["dimension"],
            "judge_model": model,
            "scores": _scores_for_input(
                payload,
                wrong_b1_identity=wrong_b1_identity,
            ),
            "input_sha256": "stale" if stale_hash else _input_payload_hash(payload),
            "source_file": input_path.name,
        }
        (output_dir / input_path.name).write_text(
            json.dumps(output, indent=2) + "\n",
            encoding="utf-8",
        )


def test_judge_qualification_report_computes_sensitivity_and_format_stats(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=2,
        prompt_variants=["baseline", "role_blocks"],
        clean=True,
    )
    _write_synthetic_outputs(tmp_path)

    stats = write_judge_qualification_report(gate_dir=tmp_path)

    assert stats["ok"] is True
    assert stats["stability"]["within_one_score_rate"] == 1.0
    assert stats["prompt_format"]["within_one_variant_rate"] == 1.0
    assert stats["sensitivity"]["pass_rate"] == 1.0
    assert stats["failure_tags"]["hit_rate"] == 1.0
    assert stats["b1_identity"]["match_rate"] == 1.0
    assert (tmp_path / "report" / "judge_qualification_stats.json").exists()
    assert (tmp_path / "report" / "judge_qualification_report.md").exists()
    assert (tmp_path / "report" / "judge_qualification_report.html").exists()
    assert stats["report_paths"]["html"].endswith("judge_qualification_report.html")


def test_final_html_report_surfaces_judge_qualification_status(tmp_path):
    pytest.importorskip("matplotlib")
    from experiments.student_sim_stability.analysis.report import ReportGenerator

    gate_dir = tmp_path / "judge_qualification"
    results_dir = tmp_path / "pilot"
    render_judge_qualification_inputs(
        gate_dir=gate_dir,
        repeats=1,
        prompt_variants=["baseline"],
        clean=True,
    )
    _write_synthetic_outputs(gate_dir)
    stats = write_judge_qualification_report(gate_dir=gate_dir)
    reference_dir = results_dir / "report"
    reference_dir.mkdir(parents=True, exist_ok=True)
    (reference_dir / "judge_qualification_reference.json").write_text(
        json.dumps(
            {
                "version": "judge_qualification_reference_v1",
                "gate_dir": str(gate_dir),
                "stats_path": stats["report_paths"]["stats_json"],
                "ok": stats["ok"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    eval_path = results_dir / "evaluations" / "all_evaluations.json"
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.write_text(
        json.dumps({key: [] for key in ["D1", "D2", "D3", "control", "P1", "B1"]})
        + "\n",
        encoding="utf-8",
    )

    from experiments.student_sim_stability.analysis.components.sections.judge_qualification_section import (
        JudgeQualificationSection,
    )

    report = ReportGenerator(str(eval_path), str(results_dir / "report"))
    qualification_stats = report._load_judge_qualification_stats()
    section = JudgeQualificationSection(
        qualification_stats, report.output_dir
    ).render_html()

    assert "A. Judge Qualification" in section
    assert "v1.4.0" in section
    assert "judge_qualification_report.html" in section


def test_judge_qualification_report_rejects_stale_outputs(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=1,
        prompt_variants=["baseline"],
        clean=True,
    )
    _write_synthetic_outputs(tmp_path, stale_hash=True)

    stats = write_judge_qualification_report(gate_dir=tmp_path)

    assert stats["ok"] is False
    assert stats["counts"]["records"] == 0
    assert stats["counts"]["missing_outputs"] == 10
    assert stats["gate_checks"]["no_missing_outputs"] is False
    assert "stale_input_sha256" in stats["missing_outputs_sample"][0]


def test_judge_qualification_report_includes_by_model_outputs(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=2,
        prompt_variants=["baseline", "role_blocks"],
        clean=True,
    )
    _write_synthetic_outputs(tmp_path, model="primary-judge")
    _write_synthetic_outputs(
        tmp_path,
        output_dir=tmp_path / "judge_outputs_by_model" / "secondary-judge",
        model="secondary-judge",
    )

    stats = write_judge_qualification_report(gate_dir=tmp_path)

    assert stats["ok"] is True
    assert stats["counts"]["records"] == 64
    assert stats["counts"]["valid_records"] == 64


def test_judge_qualification_report_fails_wrong_b1_identity(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=2,
        prompt_variants=["baseline", "role_blocks"],
        clean=True,
    )
    _write_synthetic_outputs(tmp_path, wrong_b1_identity=True)

    stats = write_judge_qualification_report(gate_dir=tmp_path)

    assert stats["ok"] is False
    assert stats["b1_identity"]["match_rate"] < 1.0
    assert stats["gate_checks"]["b1_identity_match_rate"] is False


def test_judge_qualification_report_allows_low_fit_b1_non_identity(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=2,
        prompt_variants=["baseline", "role_blocks"],
        clean=True,
    )
    _write_synthetic_outputs(tmp_path)
    output_dir = tmp_path / "judge_outputs"
    for output_path in output_dir.glob("B1__PG__pg_b1_generic_student_bad__*.json"):
        output = json.loads(output_path.read_text(encoding="utf-8"))
        output["scores"]["identified_persona"] = "developer_crossover"
        output["scores"]["contract_fit"] = 1
        output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    stats = write_judge_qualification_report(gate_dir=tmp_path)

    assert stats["ok"] is True
    assert all(
        row["sample_id"] != "pg_b1_generic_student_bad"
        for row in stats["b1_identity"]["cases"]
    )


def test_judge_qualification_cost_estimate_uses_known_judge_prices(tmp_path):
    render_judge_qualification_inputs(
        gate_dir=tmp_path,
        repeats=1,
        prompt_variants=["baseline"],
        clean=True,
    )
    estimate = estimate_judge_qualification_cost(
        gate_dir=tmp_path,
        models=[
            "anthropic/claude-sonnet-4-6",
            "openai/gpt-5.4",
            "google/gemini-3.1-pro-preview",
        ],
    )

    assert estimate["counts"]["calls"] == 10
    assert estimate["estimated_total_cost_usd"] > 0
    assert all(
        row["pricing_status"] == "from_bench_server_config_pricing"
        for row in estimate["models"]
    )


# ---------------------------------------------------------------------------
# Codex adversarial-review regressions
# ---------------------------------------------------------------------------


def _mk_corpus(samples, sensitivity=None):
    return {
        "version": "test",
        "pass_threshold": 3,
        "default_repeats": 3,
        "items": samples,
        "sensitivity_cases": sensitivity or [],
    }


def _mk_records(
    sample_id,
    dimension,
    score,
    *,
    runs=3,
    failure_types=None,
    identified=None,
    expected_band="high",
    persona_id="x",
    expected_failure_types=None,
):
    field = {
        "P1": "overall_probe_pass",
        "D3": "overall_drift_score",
        "B1": "contract_fit",
        "control": "distinctiveness",
        "D1": "overall",
    }[dimension]
    return [
        {
            "sample_id": sample_id,
            "dimension": dimension,
            "prompt_variant_id": "baseline",
            "repeat_index": i,
            "expected_score_band": expected_band,
            "expected_failure_types": expected_failure_types or [],
            "expected_persona_id": identified if dimension == "B1" else None,
            "input_metadata": {"persona_id": persona_id},
            "scores": {
                field: score,
                "failure_types": failure_types or [],
                "dominant_failure_type": (failure_types or [None])[0],
                "identified_persona": identified,
            },
        }
        for i in range(runs)
    ]


def test_judge_qualification_flags_low_band_sample_scored_above_threshold():
    """A bad (low-band) sample scored 4 (above pass_threshold=3) must fail the
    gate via the expected_band_respected check — even though sensitivity and
    failure-tag checks still pass. This is the Codex adversarial finding."""
    corpus = _mk_corpus(
        samples=[
            {
                "sample_id": "good",
                "dimension": "P1",
                "persona_id": "x",
                "expected_score_band": "high",
                "expected_failure_types": [],
            },
            {
                "sample_id": "bad",
                "dimension": "P1",
                "persona_id": "x",
                "expected_score_band": "low",
                "expected_failure_types": ["knowledge_leak"],
            },
        ],
        sensitivity=[
            {
                "case_id": "s1",
                "factor": "x",
                "dimension": "P1",
                "baseline_sample_id": "good",
                "perturbed_sample_id": "bad",
                "expected_direction": "baseline_higher",
                "minimum_margin": 1,
            },
        ],
    )
    records = _mk_records("good", "P1", 5, expected_band="high") + _mk_records(
        "bad",
        "P1",
        4,
        expected_band="low",
        failure_types=["knowledge_leak"],
        expected_failure_types=["knowledge_leak"],
    )
    stats = compute_judge_qualification_stats(corpus=corpus, records=records)
    # The old checks are all green — this is the point of the adversarial case
    assert stats["sensitivity"]["pass_rate"] == 1.0
    assert stats["failure_tags"]["hit_rate"] == 1.0
    # But the new band check catches it
    assert stats["gate_checks"]["expected_band_respected"] is False
    assert stats["ok"] is False
    assert stats["expected_band"][
        "violations"
    ], "bad sample mean=4 above threshold=3 must show up as a band violation"


def test_judge_qualification_band_check_accepts_correct_bands():
    """Good samples >= threshold and bad samples < threshold should pass."""
    corpus = _mk_corpus(
        samples=[
            {
                "sample_id": "good",
                "dimension": "P1",
                "persona_id": "x",
                "expected_score_band": "high",
                "expected_failure_types": [],
            },
            {
                "sample_id": "bad",
                "dimension": "P1",
                "persona_id": "x",
                "expected_score_band": "low",
                "expected_failure_types": ["knowledge_leak"],
            },
        ],
    )
    records = _mk_records("good", "P1", 5, expected_band="high") + _mk_records(
        "bad",
        "P1",
        2,
        expected_band="low",
        failure_types=["knowledge_leak"],
        expected_failure_types=["knowledge_leak"],
    )
    stats = compute_judge_qualification_stats(corpus=corpus, records=records)
    assert stats["gate_checks"]["expected_band_respected"] is True


def test_judge_qualification_stats_records_version_fingerprints():
    """Stats must include rubric_version and contract_version so the cli gate
    enforcer can freshness-check them before running pilot/full."""
    from experiments.student_sim_stability.core.contracts import CONTRACT_VERSION
    from experiments.student_sim_stability.core.rubrics import RUBRIC_VERSION

    corpus = _mk_corpus(
        samples=[
            {
                "sample_id": "good",
                "dimension": "P1",
                "persona_id": "x",
                "expected_score_band": "high",
                "expected_failure_types": [],
            },
        ]
    )
    records = _mk_records("good", "P1", 5, expected_band="high")
    stats = compute_judge_qualification_stats(corpus=corpus, records=records)
    assert stats["rubric_version"] == RUBRIC_VERSION
    assert stats["contract_version"] == CONTRACT_VERSION
    assert stats["corpus_version"] == "test"
    assert stats["version"] == "judge_qualification_stats_v3"
