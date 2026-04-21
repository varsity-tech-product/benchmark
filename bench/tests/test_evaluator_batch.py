"""Tests for the batch evaluator (issue #47 slice P1).

Covers discovery, idempotency via ``config_hash``, concurrency, failure
isolation, dry-run, and CLI round-trip. Stubs the single-bundle scorer
so tests stay hermetic — the scoring pipeline itself is exercised by
``test_evaluator_single``.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from server.evaluator import batch as batch_mod
from server.evaluator.batch import (
    BundleOutcome,
    filter_pending,
    iter_bundle_dirs,
    resolve_bundles,
    run_campaign,
)
from server.evaluator.config_hash import compute_config_hash
from server.storage.result_writer import save_run_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bundle(
    bench_root: Path,
    *,
    task_id: str,
    persona_id: str,
    session_id: str,
    ts: str = "20260421_120000",
) -> Path:
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / task_id
        / persona_id
        / f"{ts}_{session_id[:8]}"
    )
    workspace = bench_root / "_ws" / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "solution.py").write_text("print(1)\n", encoding="utf-8")
    save_run_state(
        result_dir=bundle_dir,
        conversation=[
            {"role": "user", "content": "help"},
            {"role": "assistant", "content": "sure"},
        ],
        tool_logs=[],
        workspace_path=str(workspace),
        duration_seconds=1.0,
        distractor_names=[],
        task_id=task_id,
        session_id=session_id,
        persona_id=persona_id,
        session_status="completed",
        termination_reason="tc_complete",
        run_id=f"run_{task_id}_{session_id[:4]}",
        public_task_label=task_id,
    )
    return bundle_dir


def _stub_task(task_id="X01"):
    return types.SimpleNamespace(
        task_id=task_id,
        category=types.SimpleNamespace(value="data_analysis"),
        difficulty=types.SimpleNamespace(value="medium"),
        requires_code=True,
    )


def _stub_persona(persona_id="fullstack_practitioner"):
    return types.SimpleNamespace(
        persona_id=persona_id,
        knowledge_level="proficient_code",
    )


def _fake_scored_result(bundle_dir: Path, eval_run_id: str, bench_root: Path):
    """Return the shape ``score_bundle`` hands back + write the sibling artefact."""
    from server.evaluator.paths import eval_run_dir as _run_dir

    loaded_manifest = json.loads((bundle_dir / "manifest.json").read_text())
    out = _run_dir(
        bench_root,
        task_id=loaded_manifest["task_id"],
        persona_id=loaded_manifest["persona_id"],
        session_id=loaded_manifest["session_id"],
        eval_run_id=eval_run_id,
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_meta.json").write_text(
        json.dumps(
            {
                "eval_run_id": eval_run_id,
                "bundle_dir": str(bundle_dir),
                "overall_score": 0.7,
            }
        ),
        encoding="utf-8",
    )
    return {
        "_eval_run_id": eval_run_id,
        "_eval_run_dir": out,
        "_overall_score": 0.7,
        "quant_result": 0.7,
        "quant_process": 0.6,
        "tutor_scores": {},
    }


@pytest.fixture
def patched_score_bundle(tmp_path):
    """Replace ``_score_one`` inside batch with a fast stub.

    Counts per-bundle invocations so tests can assert idempotency.
    """
    calls: list[Path] = []

    def _fake(bundle_dir, *, task, persona, bench_root, eval_model, eval_mode, tutor_dims, cancel_event=None, eval_run_id=None):
        calls.append(Path(bundle_dir))
        return _fake_scored_result(
            Path(bundle_dir),
            eval_run_id or f"eval_20260421_{len(calls):06d}",
            Path(bench_root),
        )

    with patch.object(batch_mod, "_score_one", side_effect=_fake):
        yield calls


# ---------------------------------------------------------------------------
# config_hash
# ---------------------------------------------------------------------------


class TestConfigHash:
    def test_deterministic(self):
        a = compute_config_hash(judge="j", eval_mode="full")
        b = compute_config_hash(judge="j", eval_mode="full")
        assert a == b

    def test_judge_changes_hash(self):
        a = compute_config_hash(judge="claude", eval_mode="full")
        b = compute_config_hash(judge="gpt", eval_mode="full")
        assert a != b

    def test_tutor_dim_order_independent(self):
        a = compute_config_hash(judge="j", eval_mode="full", tutor_dims=["D3", "D4"])
        b = compute_config_hash(judge="j", eval_mode="full", tutor_dims=["D4", "D3"])
        assert a == b

    def test_rubric_version_changes_hash(self):
        a = compute_config_hash(judge="j", eval_mode="full", rubric_version="1.0")
        b = compute_config_hash(judge="j", eval_mode="full", rubric_version="2.0")
        assert a != b


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_iter_bundle_dirs_finds_manifested_bundles(self, tmp_path):
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        _make_bundle(tmp_path, task_id="I01", persona_id="p2", session_id="b" * 32)

        bundles = list(iter_bundle_dirs(tmp_path))
        assert len(bundles) == 2

    def test_iter_bundle_dirs_skips_pre_manifest_dirs(self, tmp_path):
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        legacy = tmp_path / "results" / "server" / "X01" / "p1" / "20200101_000000_legacy00"
        legacy.mkdir(parents=True)
        (legacy / "run_state.json").write_text("{}")

        bundles = list(iter_bundle_dirs(tmp_path))
        assert len(bundles) == 1

    def test_resolve_filters_by_task_and_persona(self, tmp_path):
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        _make_bundle(tmp_path, task_id="X02", persona_id="p1", session_id="b" * 32)
        _make_bundle(tmp_path, task_id="X01", persona_id="p2", session_id="c" * 32)

        only_x01 = resolve_bundles(tmp_path, task_ids=["X01"])
        assert len(only_x01) == 2

        only_x01_p2 = resolve_bundles(tmp_path, task_ids=["X01"], persona_ids=["p2"])
        assert len(only_x01_p2) == 1

    def test_resolve_filters_by_session_id(self, tmp_path):
        sid = "a" * 32
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id=sid)
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="b" * 32)

        got = resolve_bundles(tmp_path, session_ids=[sid])
        assert len(got) == 1

    def test_resolve_requires_filter(self, tmp_path):
        with pytest.raises(ValueError, match="needs at least one filter"):
            resolve_bundles(tmp_path)

    def test_resolve_bundle_paths_passthrough(self, tmp_path):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        got = resolve_bundles(tmp_path, bundle_paths=[b])
        assert got == [b]

    def test_resolve_bundle_paths_drops_missing_manifest(self, tmp_path):
        fake = tmp_path / "missing"
        fake.mkdir()
        got = resolve_bundles(tmp_path, bundle_paths=[fake])
        assert got == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_filter_pending_splits_on_config_hash(self, tmp_path, patched_score_bundle):
        b1 = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        b2 = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="b" * 32, ts="20260421_130000")

        run_campaign(
            bench_root=tmp_path,
            bundles=[b1],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            eval_mode="full",
            concurrency=1,
        )

        cfg = compute_config_hash(judge="claude", eval_mode="full")
        pending, scored = filter_pending([b1, b2], tmp_path, cfg)
        assert [p for p in pending] == [b2]
        assert scored and scored[0][0] == b1

    def test_skip_scored_default_reruns_nothing(
        self, tmp_path, patched_score_bundle
    ):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        kwargs = dict(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            eval_mode="full",
            concurrency=1,
        )
        run_campaign(**kwargs)
        assert len(patched_score_bundle) == 1

        run_campaign(**kwargs)
        assert len(patched_score_bundle) == 1  # skipped second time

    def test_force_rescores(self, tmp_path, patched_score_bundle):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        kwargs = dict(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            eval_mode="full",
            concurrency=1,
        )
        run_campaign(**kwargs)
        run_campaign(**{**kwargs, "force": True})
        assert len(patched_score_bundle) == 2

    def test_different_judge_triggers_rescoring(
        self, tmp_path, patched_score_bundle
    ):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        common = dict(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_mode="full",
            concurrency=1,
        )
        run_campaign(**common, eval_model="claude")
        run_campaign(**common, eval_model="gpt")
        assert len(patched_score_bundle) == 2


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class TestRunCampaign:
    def test_writes_campaign_summary(self, tmp_path, patched_score_bundle):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        summary = run_campaign(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            eval_mode="full",
            concurrency=1,
        )
        assert summary.totals["scored"] == 1
        path = (
            tmp_path / "evaluations" / "campaigns"
            / summary.campaign_id / "summary.json"
        )
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["totals"]["scored"] == 1
        assert data["config"]["judge"] == "claude"
        assert data["config"]["config_hash"]

    def test_dry_run_does_not_score(self, tmp_path, patched_score_bundle):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        summary = run_campaign(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            dry_run=True,
            concurrency=1,
        )
        assert summary.totals["pending"] == 1
        assert summary.totals["scored"] == 0
        assert len(patched_score_bundle) == 0
        # dry-run must not persist a summary
        assert not (tmp_path / "evaluations" / "campaigns").exists()

    def test_concurrency_scores_every_bundle(
        self, tmp_path, patched_score_bundle
    ):
        bundles = [
            _make_bundle(
                tmp_path,
                task_id="X01",
                persona_id="p1",
                session_id=chr(ord("a") + i) * 32,
                ts=f"2026042{i}_120000",
            )
            for i in range(5)
        ]
        summary = run_campaign(
            bench_root=tmp_path,
            bundles=bundles,
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=3,
        )
        assert summary.totals["scored"] == 5
        assert len(patched_score_bundle) == 5

    def test_failure_isolated_per_bundle(self, tmp_path):
        ok = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        boom = _make_bundle(tmp_path, task_id="X02", persona_id="p1", session_id="b" * 32, ts="20260421_130000")

        def _side_effect(
            bundle_dir, *, task, persona, bench_root, eval_model, eval_mode,
            tutor_dims, cancel_event=None, eval_run_id=None,
        ):
            if Path(bundle_dir) == boom:
                raise RuntimeError("judge timeout")
            return _fake_scored_result(
                Path(bundle_dir),
                "eval_20260421_000001",
                Path(bench_root),
            )

        with patch.object(batch_mod, "_score_one", side_effect=_side_effect):
            summary = run_campaign(
                bench_root=tmp_path,
                bundles=[ok, boom],
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
            )
        assert summary.totals["scored"] == 1
        assert summary.totals["failed"] == 1
        failed = [o for o in summary.outcomes if o["status"] == "failed"]
        assert failed and "judge timeout" in failed[0]["error"]

    def test_loader_systemexit_isolated_per_bundle(self, tmp_path, patched_score_bundle):
        """``_load_task`` raises ``SystemExit`` when a task JSON is missing.

        A bare ``except Exception`` would let that propagate and abort the
        campaign; the isolated worker must convert it to a failed outcome.
        """
        good = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        bad = _make_bundle(
            tmp_path, task_id="XNOT_FOUND", persona_id="p1",
            session_id="b" * 32, ts="20260421_130000",
        )

        def _loader(task_id: str):
            if task_id == "XNOT_FOUND":
                raise SystemExit(f"task {task_id!r} not found")
            return _stub_task(task_id)

        summary = run_campaign(
            bench_root=tmp_path,
            bundles=[good, bad],
            task_loader=_loader,
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
        )
        assert summary.totals["scored"] == 1
        assert summary.totals["failed"] == 1

    def test_per_worker_concurrency_restored_after_run(
        self, tmp_path, patched_score_bundle
    ):
        """Concurrency tuning must not leak out of the campaign.

        A long-lived process (the REST server, tests, a REPL) would
        otherwise run unrelated single-bundle evals at the throttled
        per-worker cap forever after the batch exits.

        The real evaluator modules may not import in a CI env lacking
        ``nest_asyncio``; stub ``_concurrency_modules`` so the test
        pins behaviour independent of that optional dependency.
        """
        tutor_stub = types.SimpleNamespace(
            _CONCURRENCY=20,
            set_eval_concurrency=lambda n: setattr(tutor_stub, "_CONCURRENCY", n),
        )
        proc_stub = types.SimpleNamespace(
            _CONCURRENCY=20,
            set_eval_concurrency=lambda n: setattr(proc_stub, "_CONCURRENCY", n),
        )
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        with patch.object(
            batch_mod, "_concurrency_modules", return_value=(tutor_stub, proc_stub)
        ):
            run_campaign(
                bench_root=tmp_path,
                bundles=[b],
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=8,
            )
        assert tutor_stub._CONCURRENCY == 20
        assert proc_stub._CONCURRENCY == 20

    def test_campaign_ids_unique_within_a_second(self, tmp_path, patched_score_bundle):
        """Rapid reruns (all-skipped --all-pending) must not clobber each other."""
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        kwargs = dict(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
        )
        run_campaign(**kwargs)
        run_campaign(**kwargs)
        run_campaign(**kwargs)

        campaign_root = tmp_path / "evaluations" / "campaigns"
        dirs = [p.name for p in campaign_root.iterdir() if p.is_dir()]
        assert len(dirs) == 3, f"expected 3 distinct campaign dirs, got {dirs}"

    def test_progress_callback_invoked(self, tmp_path, patched_score_bundle):
        bundles = [
            _make_bundle(
                tmp_path,
                task_id="X01",
                persona_id="p1",
                session_id=chr(ord("a") + i) * 32,
                ts=f"2026042{i}_120000",
            )
            for i in range(3)
        ]
        seen: list[tuple[int, int, str]] = []

        def _cb(o: BundleOutcome, done: int, total: int) -> None:
            seen.append((done, total, o.status))

        run_campaign(
            bench_root=tmp_path,
            bundles=bundles,
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
            on_progress=_cb,
        )
        assert len(seen) == 3
        assert all(total == 3 for _, total, _ in seen)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_all_pending_round_trip(self, tmp_path, patched_score_bundle):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        from server.evaluator import __main__ as cli

        with (
            patch.object(cli, "_load_task", return_value=_stub_task()),
            patch.object(cli, "_load_persona", return_value=_stub_persona()),
        ):
            rc = cli.main(
                [
                    "--bench-root",
                    str(tmp_path),
                    "--all-pending",
                    "--eval-model",
                    "claude",
                    "--concurrency",
                    "1",
                ]
            )
        assert rc == 0
        assert len(patched_score_bundle) == 1
        b  # silence linters

    def test_dry_run_exits_zero(self, tmp_path, patched_score_bundle):
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        from server.evaluator import __main__ as cli

        with (
            patch.object(cli, "_load_task", return_value=_stub_task()),
            patch.object(cli, "_load_persona", return_value=_stub_persona()),
        ):
            rc = cli.main(
                [
                    "--bench-root",
                    str(tmp_path),
                    "--all-pending",
                    "--dry-run",
                ]
            )
        assert rc == 0
        assert len(patched_score_bundle) == 0

    def test_missing_selector_returns_error(self, tmp_path, capsys):
        from server.evaluator import __main__ as cli

        rc = cli.main(["--bench-root", str(tmp_path)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "selector" in err
