"""Standalone scoring smoke test — proves eval/ has no server/ dependency.

Two checks:

1. Static: ``grep -r "from server\\." bench/eval/`` returns 0 lines. This is
   the hard decoupling guarantee from #123 P2; if it ever drifts, the
   scoring package quietly stops being independently runnable.

2. Functional: ``eval.score()`` returns an :class:`EvalOutput` end-to-end
   given a bundle, a task JSON, and a persona JSON, without persisting
   any score files. LLM resolution is auto-mocked via conftest.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from eval.contracts.bundle import (
    SCHEMA_VERSION,
    AgentMessage,
    AgentMetadata,
    Bundle,
    ConversationTurn,
    RuntimeInfo,
    SessionInfo,
    StudentMessage,
)
from eval.contracts.output import EvalOutput
from eval.score import score


_BENCH_DIR = Path(__file__).resolve().parents[2]


def test_eval_package_has_no_server_imports():
    """The whole point of P2: eval/ doesn't reach into server/."""
    proc = subprocess.run(
        ["grep", "-rn", "from server\\.", "bench/eval", "--include=*.py"],
        cwd=_BENCH_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, (
        "bench/eval/ contains `from server.` imports — P2 decoupling broken:\n"
        + proc.stdout
    )


def _write_minimal_task(tmp_path: Path, task_id: str) -> Path:
    """Build a minimal task JSON the coordinator can ingest via SimpleNamespace."""
    task_dir = tmp_path / "tasks" / "layer2" / "strategy"
    task_dir.mkdir(parents=True)
    task_path = task_dir / f"{task_id}.json"
    task_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "version": "1.0",
                "description": "smoke task",
                "category": "strategy",
                "difficulty": "easy",
                "task_type": "multi_turn",
                "requires_code": False,
                "max_turns": 3,
                "persona_id": "smoke_persona",
                "ground_truth": {
                    "expected_mcp_tools": [],
                    "key_concepts": [],
                    "common_mistakes": [],
                    "quant_validation": None,
                },
                "environment": {
                    "sandbox_image": "quant-tutor-env:smoke",
                    "core_mcp_tools": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return task_path


def _write_minimal_persona(tmp_path: Path, persona_id: str) -> Path:
    p_dir = tmp_path / "personas"
    p_dir.mkdir(parents=True)
    p_path = p_dir / f"{persona_id}.json"
    p_path.write_text(
        json.dumps(
            {
                "persona_id": persona_id,
                "description": "smoke persona",
                "knowledge_level": "medium",
            }
        ),
        encoding="utf-8",
    )
    return p_path


def _make_bundle(task_id: str, persona_id: str) -> Bundle:
    return Bundle(
        schema_version=SCHEMA_VERSION,
        task_id=task_id,
        task_version="1.0",
        task_spec_hash="smoke",
        persona_id=persona_id,
        session=SessionInfo(
            session_id="smoke-sess",
            termination_reason="max_turns",
            turn_count=1,
        ),
        runtime=RuntimeInfo(),
        agent_metadata=AgentMetadata(harness="smoke"),
        conversation=[
            ConversationTurn(
                turn=1,
                agent=AgentMessage(text="Here's a brief plan."),
                student=StudentMessage(text="Walk me through the strategy."),
            ),
        ],
    )


def test_score_returns_eval_output_without_persisting(tmp_path):
    task_id = "S99_smoke"
    persona_id = "smoke_persona"
    _write_minimal_task(tmp_path, task_id)
    _write_minimal_persona(tmp_path, persona_id)

    bundle = _make_bundle(task_id, persona_id)
    output = score(bundle, bench_root=tmp_path)

    assert isinstance(output, EvalOutput)
    # Standalone path must clear preflight; a "failed" status here would mean
    # preflight rejected the synthetic run_state.json (regression of the
    # codex slice-5 P1 finding).
    assert output.score_status != "failed", f"unexpected failure: {output.error}"
    # Non-persisting smoke: no score_n directory under tmp_path/evaluations.
    assert not (tmp_path / "evaluations").exists()


def test_score_accepts_bundle_path(tmp_path):
    task_id = "S99_smoke"
    persona_id = "smoke_persona"
    _write_minimal_task(tmp_path, task_id)
    _write_minimal_persona(tmp_path, persona_id)

    bundle_path = tmp_path / "bundle.json"
    bundle = _make_bundle(task_id, persona_id)
    from eval.contracts import bundle_io

    bundle_io.write(bundle, bundle_path)

    output = score(bundle_path, bench_root=tmp_path)
    assert isinstance(output, EvalOutput)


def test_score_does_not_write_into_caller_workspace(tmp_path):
    """Codex slice-5 round-2 P2 #2: workspace_path is the agent's files; the
    synthetic run_state.json must land in a scratch dir, never the workspace."""
    task_id = "S99_smoke"
    persona_id = "smoke_persona"
    _write_minimal_task(tmp_path, task_id)
    _write_minimal_persona(tmp_path, persona_id)

    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()

    bundle = _make_bundle(task_id, persona_id)
    score(bundle, bench_root=tmp_path, workspace_path=workspace)

    # Workspace should be untouched by score().
    assert list(workspace.iterdir()) == []
