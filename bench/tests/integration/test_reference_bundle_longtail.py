"""Long-tail parity smoke for the reference bundle bridge.

The matrix uses deterministic scorer fixtures: L0 and L1 use local rule-based
scorers, while L2 QR components are patched to deterministic scores. Expected
runtime is under 5 seconds on the local test venv.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from config.benchmark_config import BENCH_IMAGE_V3, BENCH_IMAGE_V3_LEAN
from eval.contracts.schemas import QuantTutorTask, UserPersona
from platform_api.contracts import EvalItem, EvalSample, ToolLog, TranscriptMessage
from server.reference import load_reference_bundle

TOLERANCE = 0.05


@dataclass(frozen=True)
class ParityCase:
    task_id: str
    labels: frozenset[str]
    data_pattern: str
    sandbox_group: str


PARITY_CASES = (
    ParityCase(
        "L0_money.stackexchange_8474",
        frozenset({"knowledge_qa", "L0"}),
        "no_data",
        "no_sandbox",
    ),
    ParityCase(
        "L0_fiqa_fiqa_2450_558948",
        frozenset({"knowledge_qa", "L0"}),
        "no_data",
        "no_sandbox",
    ),
    ParityCase(
        "L0_tatqa_tatqa_train_329_3",
        frozenset({"knowledge_qa", "L0"}),
        "no_data",
        "no_sandbox",
    ),
    ParityCase(
        "L1_DAT_02_tick_data_aggregation",
        frozenset({"agent_execution", "data_engineering", "L1"}),
        "single_local_csv",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_DAT_01_ohlcv_health_check",
        frozenset({"agent_execution", "data_engineering", "L1"}),
        "multiple_local_csvs",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_DAT_04_alternative_data_integration",
        frozenset({"agent_execution", "data_engineering", "L1"}),
        "mixed_local_remote",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_BTE_01_lookahead_safe_engine",
        frozenset({"agent_execution", "backtest_engine", "L1"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_BTE_02_multi_asset_sync",
        frozenset({"agent_execution", "backtest_engine", "L1"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_ALR_01_volume_microstructure_alpha",
        frozenset({"agent_execution", "alpha_research", "L1"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_ALR_03_factor_model_construction",
        frozenset({"agent_execution", "alpha_research", "L1"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L1_IMP_01_mean_reversion_universe",
        frozenset({"agent_execution", "implementation", "L1"}),
        "single_local_file",
        "BENCH_IMAGE_V3_LEAN",
    ),
    ParityCase(
        "L1_IMP_03_pairs_trading",
        frozenset({"agent_execution", "implementation", "L1"}),
        "mixed_local_remote",
        "BENCH_IMAGE_V3_LEAN",
    ),
    ParityCase(
        "L2_ADV_11_prompt_injection_csv",
        frozenset({"multi_turn_dialog", "adversarial", "L2"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L2_DIA_03_data_quality_misconception",
        frozenset({"multi_turn_dialog", "diagnostic", "L2"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
    ParityCase(
        "L2_E2E_04_strategy_ab_testing",
        frozenset({"multi_turn_dialog", "end_to_end", "L2"}),
        "hf_remote_fallback",
        "BENCH_IMAGE_V3",
    ),
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _task_from_item(item: EvalItem) -> QuantTutorTask:
    return QuantTutorTask(**item.payload["quant_tutor_task"])


def _load_persona(bench_root: Path, persona_id: str) -> UserPersona:
    path = bench_root / "personas" / f"{persona_id}.json"
    return UserPersona(**json.loads(path.read_text(encoding="utf-8")))


def _question(task: QuantTutorTask) -> str:
    return task.question or task.description


def _seed_local_data(bench_root: Path) -> None:
    bds = bench_root / "data" / "hf_cache" / "normal" / "BDEX"
    bds.mkdir(parents=True, exist_ok=True)
    csv_payload = (
        "date,open,high,low,close,volume\n"
        "2024-01-01,1,1,1,1,100\n"
        "2024-01-02,2,2,2,2,200\n"
    )
    for name in (
        "AAPL_2018_2024.csv",
        "SPY_2018_2024.csv",
        "tick_data_sample.csv",
    ):
        (bds / name).write_text(csv_payload, encoding="utf-8")

    lean_data = bench_root / "runtime_assets" / "lean" / "data"
    lean_data.mkdir(parents=True, exist_ok=True)
    (lean_data / "universe.json").write_text(
        json.dumps({"symbols": ["BTCUSDT", "ETHUSDT"]}),
        encoding="utf-8",
    )


def _sandbox_group(item: EvalItem) -> str:
    if item.sandbox_spec is None:
        return "no_sandbox"
    image = item.sandbox_spec.image_uri
    if image == BENCH_IMAGE_V3:
        return "BENCH_IMAGE_V3"
    if image == BENCH_IMAGE_V3_LEAN:
        return "BENCH_IMAGE_V3_LEAN"
    return image


def _coverage_labels(item: EvalItem, task: QuantTutorTask) -> set[str]:
    labels = {
        _enum_value(task.layer),
        str(item.task_type),
        _enum_value(task.category),
    }
    if _enum_value(task.layer) == "L0":
        labels.add("knowledge_qa")
    return labels


def _data_pattern(item: EvalItem) -> str:
    mounts = item.data_mounts
    if not mounts:
        return "no_data"

    schemes = [mount.uri.split(":", 1)[0] for mount in mounts]
    suffixes = [Path(mount.target_path).suffix for mount in mounts]
    if "file" in schemes and "hf" in schemes:
        return "mixed_local_remote"
    if all(scheme == "hf" for scheme in schemes):
        return "hf_remote_fallback"
    if all(scheme == "file" for scheme in schemes):
        if len(mounts) == 1 and suffixes == [".csv"]:
            return "single_local_csv"
        if len(mounts) > 1 and all(suffix == ".csv" for suffix in suffixes):
            return "multiple_local_csvs"
        if len(mounts) == 1:
            return "single_local_file"
        return "multiple_local_files"
    return ",".join(sorted(set(schemes)))


def _json_payload_for(spec: dict[str, Any]) -> dict[str, Any]:
    payload = {key: 1 for key in spec.get("required_keys", [])}
    for constraint in spec.get("constraints", []) or []:
        key = constraint.get("key")
        op = constraint.get("op")
        if not key:
            continue
        if "ref" in constraint:
            ref = constraint["ref"]
            payload.setdefault(ref, 1)
            if op == "<":
                payload[key], payload[ref] = 0, 1
            elif op == ">":
                payload[key], payload[ref] = 2, 1
            else:
                payload[key], payload[ref] = 1, 1
            continue

        value = constraint.get("value", 1)
        if op == "<":
            payload[key] = value - 1
        elif op == "<=":
            payload[key] = value
        elif op == ">":
            payload[key] = value + 1
        elif op == ">=":
            payload[key] = value
        elif op == "==":
            payload[key] = value
        elif op == "!=":
            payload[key] = value + 1
    return payload


def _write_expected_outputs(workspace: Path, expected_outputs: list[Any]) -> None:
    for spec in expected_outputs:
        if not isinstance(spec, dict):
            continue
        path = workspace / str(spec["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        file_type = spec.get("type", "any")
        if file_type == "json":
            path.write_text(json.dumps(_json_payload_for(spec)), encoding="utf-8")
        elif file_type == "csv":
            columns = spec.get("required_columns") or ["metric", "value"]
            rows = max(1, int(spec.get("min_rows") or 1))
            lines = [",".join(columns)]
            lines.extend(",".join(str(index) for _ in columns) for index in range(rows))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif file_type == "image":
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
        else:
            path.write_text("artifact\n", encoding="utf-8")


def _write_run_state(
    result_dir: Path,
    *,
    task_id: str,
    persona_id: str,
    conversation: list[dict[str, Any]],
    tool_logs: list[ToolLog],
) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "run_state.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "persona_id": persona_id,
                "conversation": conversation,
                "tool_logs": [log.__dict__ for log in tool_logs],
                "distractor_names": [],
            },
            default=str,
        ),
        encoding="utf-8",
    )


def _patch_l2_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_programmatic(**_kwargs):
        return (
            {
                "score": 1.0,
                "status": "success",
                "required_for_track_score": True,
            },
            None,
        )

    def fake_code_eval(**_kwargs):
        return (
            {
                "score": None,
                "status": "skipped",
                "applicable": False,
                "required_for_track_score": False,
            },
            None,
        )

    def fake_result_judge(**_kwargs):
        return {
            "score": 1.0,
            "status": "success",
            "reason": "deterministic long-tail parity judge",
            "evidence": ["covered"],
        }

    monkeypatch.setattr("eval.tracks.qr._programmatic_eval", fake_programmatic)
    monkeypatch.setattr("eval.tracks.qr._code_eval", fake_code_eval)
    monkeypatch.setattr("eval.tracks.qr._result_judge", fake_result_judge)


def _evaluate_l0(bundle, item: EvalItem, task: QuantTutorTask) -> tuple[float, float]:
    from server.reference import knowledge_qa

    actual_output = task.reference_answer or ""
    legacy = knowledge_qa.evaluate(
        question=_question(task),
        reference_answer=actual_output,
        actual_output=actual_output,
        context=task.context,
    )
    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id=f"{item.task_id}-longtail",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=_question(task)),
                TranscriptMessage(role="assistant", content=actual_output),
            ),
            payload={"eval_model": "fake-model"},
        ),
    )
    return float(legacy["score"]), float(score.value)


def _evaluate_l1(
    bundle,
    item: EvalItem,
    task: QuantTutorTask,
    workspace: Path,
) -> tuple[float, float]:
    from eval.programmatic.l1_verifier import evaluate as legacy_l1_evaluate

    expected_outputs = task.ground_truth.expected_outputs
    _write_expected_outputs(workspace, expected_outputs)
    legacy = legacy_l1_evaluate(str(workspace), expected_outputs=expected_outputs)
    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id=f"{item.task_id}-longtail",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.agent_prompt or ""),
                TranscriptMessage(role="assistant", content="Artifacts are ready."),
            ),
            payload={
                "eval_model": "fake-model",
                "workspace_path": str(workspace),
            },
        ),
    )
    return float(legacy["score"]), float(score.value)


def _evaluate_l2(
    bundle,
    item: EvalItem,
    task: QuantTutorTask,
    bench_root: Path,
    tmp_path: Path,
) -> tuple[float, float]:
    from server.storage.eval_writer import run_evaluation

    persona = _load_persona(bench_root, task.persona_id)
    conversation = [
        {"role": "user", "content": task.user_opening},
        {
            "role": "assistant",
            "content": "I checked the data, used the available tools, and explained the result.",
        },
    ]
    tool_logs = [
        ToolLog(
            name="shell_exec",
            args={"command": f"python analyze.py {task.task_id}"},
            result=f"completed {task.task_id}",
            success=True,
            turn_index=0,
        )
    ]
    legacy_dir = tmp_path / item.task_id / "legacy"
    bundle_dir = tmp_path / item.task_id / "bundle"
    _write_run_state(
        legacy_dir,
        task_id=task.task_id,
        persona_id=persona.persona_id,
        conversation=conversation,
        tool_logs=tool_logs,
    )
    _write_run_state(
        bundle_dir,
        task_id=task.task_id,
        persona_id=persona.persona_id,
        conversation=conversation,
        tool_logs=tool_logs,
    )

    legacy = run_evaluation(
        task=task,
        persona=persona,
        result_dir=legacy_dir,
        conversation=conversation,
        tool_logs=tool_logs,
        distractor_names=[],
        bench_root=str(bench_root),
        eval_model="fake-model",
        eval_mode="qr",
    )
    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id=f"{item.task_id}-longtail",
            task_id=item.task_id,
            transcript=tuple(
                TranscriptMessage(role=turn["role"], content=turn["content"])
                for turn in conversation
            ),
            tool_logs=tuple(tool_logs),
            payload={
                "persona": persona,
                "result_dir": str(bundle_dir),
                "eval_model": "fake-model",
                "eval_mode": "qr",
                "distractor_names": [],
            },
        ),
    )
    return float(legacy["overall_score"]), float(score.value)


def test_longtail_matrix_covers_issue_174_dimensions(bench_root):
    _seed_local_data(bench_root)
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    labels = set()
    data_patterns = set()
    sandbox_groups = set()
    layers = set()
    for case in PARITY_CASES:
        item = bundle.task_suite.get_task(case.task_id)
        task = _task_from_item(item)
        actual_labels = _coverage_labels(item, task)
        labels.update(actual_labels)
        data_patterns.add(_data_pattern(item))
        sandbox_groups.add(_sandbox_group(item))
        layers.add(_enum_value(task.layer))

        assert case.labels <= actual_labels
        assert _data_pattern(item) == case.data_pattern
        assert _sandbox_group(item) == case.sandbox_group

    assert len(PARITY_CASES) >= 15
    assert {
        "knowledge_qa",
        "agent_execution",
        "multi_turn_dialog",
        "alpha_research",
        "backtest_engine",
        "data_engineering",
        "diagnostic",
    } <= labels
    assert {"L0", "L1", "L2"} <= layers
    assert {
        "no_data",
        "single_local_csv",
        "multiple_local_csvs",
        "hf_remote_fallback",
        "mixed_local_remote",
    } <= data_patterns
    assert {"BENCH_IMAGE_V3", "BENCH_IMAGE_V3_LEAN", "no_sandbox"} <= sandbox_groups


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda case: case.task_id)
def test_reference_bundle_longtail_score_parity(
    case: ParityCase,
    bench_root,
    tmp_path,
    monkeypatch,
):
    _seed_local_data(bench_root)
    _patch_l2_qr(monkeypatch)
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(case.task_id)
    task = _task_from_item(item)
    layer = _enum_value(task.layer)

    if layer == "L0":
        legacy_value, bundle_value = _evaluate_l0(bundle, item, task)
    elif layer == "L1":
        workspace = tmp_path / case.task_id / "workspace"
        legacy_value, bundle_value = _evaluate_l1(bundle, item, task, workspace)
    else:
        legacy_value, bundle_value = _evaluate_l2(
            bundle,
            item,
            task,
            bench_root,
            tmp_path,
        )

    delta = abs(bundle_value - legacy_value)
    assert delta <= TOLERANCE, (
        f"{case.task_id} parity delta {delta:.4f} exceeds {TOLERANCE:.2f}; "
        "classify as bridge parity drift under deterministic fixtures"
    )
