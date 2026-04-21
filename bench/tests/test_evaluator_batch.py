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
from datetime import datetime

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


def _fake_scored_result(
    bundle_dir: Path,
    eval_run_id: str,
    bench_root: Path,
    *,
    cost: float = 0.0,
):
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
        "tutor_scores": {"_eval_cost": cost},
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


# ---------------------------------------------------------------------------
# P2: operational hardening
# ---------------------------------------------------------------------------


class TestCostAndBudget:
    def test_cost_summed_into_campaign(self, tmp_path):
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

        def _fake(bundle_dir, **_kw):
            return _fake_scored_result(
                Path(bundle_dir),
                f"eval_20260421_{abs(hash(str(bundle_dir))) % 1000000:06d}",
                tmp_path,
                cost=0.5,
            )

        with patch.object(batch_mod, "_score_one", side_effect=_fake):
            summary = run_campaign(
                bench_root=tmp_path,
                bundles=bundles,
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
            )
        assert summary.cost_usd == pytest.approx(1.5)
        scored = [o for o in summary.outcomes if o["status"] == "scored"]
        assert all(o["cost_usd"] == 0.5 for o in scored)

    def test_max_cost_stops_dispatch(self, tmp_path):
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

        def _fake(bundle_dir, **_kw):
            return _fake_scored_result(
                Path(bundle_dir),
                f"eval_20260421_{abs(hash(str(bundle_dir))) % 1000000:06d}",
                tmp_path,
                cost=0.4,
            )

        with patch.object(batch_mod, "_score_one", side_effect=_fake):
            summary = run_campaign(
                bench_root=tmp_path,
                bundles=bundles,
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
                max_cost_usd=1.0,
            )
        budget_skipped = [
            o for o in summary.outcomes
            if o["status"] == "skipped" and o.get("error") == "budget_exceeded"
        ]
        assert budget_skipped, "expected at least one budget_exceeded skip"
        assert summary.cost_usd <= 1.0 + 0.4  # the bundle that crossed the cap finishes


class TestCostExtraction:
    def test_process_metrics_cost_summed(self, tmp_path):
        """``process_metrics._eval_cost`` must reach the campaign total.

        The real pipeline stamps process-metric spend at
        ``result["process_metrics"]["_eval_cost"]``; missing it would
        let `--max-cost-usd` overshoot its cap.
        """
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)

        def _fake(bundle_dir, **_kw):
            result = _fake_scored_result(
                Path(bundle_dir),
                "eval_20260421_000001",
                tmp_path,
                cost=0.0,
            )
            # Simulate the real pipeline's nested cost surface.
            result["process_metrics"] = {"_eval_cost": 0.75}
            result["result_judge"] = {"_eval_cost": 0.1}
            return result

        with patch.object(batch_mod, "_score_one", side_effect=_fake):
            summary = run_campaign(
                bench_root=tmp_path,
                bundles=[b],
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
            )
        assert summary.cost_usd == pytest.approx(0.85)


class TestResume:
    def test_resume_reuses_campaign_id(self, tmp_path, patched_score_bundle):
        """Resumed campaign lands in the same directory as the original."""
        b1 = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        b2 = _make_bundle(
            tmp_path, task_id="X01", persona_id="p1", session_id="b" * 32,
            ts="20260421_130000",
        )

        first = run_campaign(
            bench_root=tmp_path,
            bundles=[b1],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
        )

        resumed = run_campaign(
            bench_root=tmp_path,
            bundles=[b1, b2],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
            skip_scored=False,
            resume_campaign_id=first.campaign_id,
        )
        assert resumed.campaign_id == first.campaign_id
        # Only b2 actually dispatched via _score_one; b1 came back through
        # the checkpoint plus b2 was scored fresh.
        sids_scored = [
            o["session_id"] for o in resumed.outcomes if o["status"] == "scored"
        ]
        assert set(sids_scored) == {"a" * 32, "b" * 32}

    def test_resume_skips_already_done_bundles(self, tmp_path):
        """After an interrupt, a resume dispatches only the unfinished bundles."""
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

        crashed_at = bundles[1]
        done_count = {"n": 0}

        def _fake(bundle_dir, **_kw):
            done_count["n"] += 1
            if Path(bundle_dir) == crashed_at:
                raise RuntimeError("simulated interrupt")
            return _fake_scored_result(
                Path(bundle_dir),
                f"eval_20260421_{done_count['n']:06d}",
                tmp_path,
                cost=0.0,
            )

        with patch.object(batch_mod, "_score_one", side_effect=_fake):
            first = run_campaign(
                bench_root=tmp_path,
                bundles=bundles,
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
            )
        assert first.totals["scored"] + first.totals["failed"] == 3

        # Resume: every bundle is rewritten via checkpoint; force is only
        # needed here to bypass the config_hash skip since the first run
        # also wrote eval_meta.json entries.
        call_log: list[Path] = []

        def _second(bundle_dir, **_kw):
            call_log.append(Path(bundle_dir))
            return _fake_scored_result(
                Path(bundle_dir),
                f"eval_20260421_second_{len(call_log):06d}",
                tmp_path,
                cost=0.0,
            )

        with patch.object(batch_mod, "_score_one", side_effect=_second):
            resumed = run_campaign(
                bench_root=tmp_path,
                bundles=bundles,
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=1,
                resume_campaign_id=first.campaign_id,
                skip_scored=False,
            )

        # The bundles that were successfully scored in the first run are
        # skipped by resume; the interrupted one is dispatched again.
        assert resumed.campaign_id == first.campaign_id
        assert len(call_log) < 3, f"resume re-scored too many bundles: {call_log}"


class TestBudgetEdges:
    def test_concurrency_path_honors_exhausted_budget(self, tmp_path):
        """If cost is already over the cap, the concurrent dispatcher
        must not submit any new bundles before checking.
        """
        bundles = [
            _make_bundle(
                tmp_path,
                task_id="X01",
                persona_id="p1",
                session_id=chr(ord("a") + i) * 32,
                ts=f"2026042{i}_120000",
            )
            for i in range(4)
        ]

        call_log: list[Path] = []

        def _fake(bundle_dir, **_kw):
            call_log.append(Path(bundle_dir))
            return _fake_scored_result(
                Path(bundle_dir),
                f"eval_{len(call_log):06d}",
                tmp_path,
                cost=1.0,
            )

        with patch.object(batch_mod, "_score_one", side_effect=_fake):
            summary = run_campaign(
                bench_root=tmp_path,
                bundles=bundles,
                task_loader=lambda _: _stub_task(),
                persona_loader=lambda _: _stub_persona(),
                eval_model="claude",
                concurrency=4,
                max_cost_usd=0.0,
            )
        assert call_log == [], "budget_exceeded at start must halt dispatch immediately"
        assert summary.totals["skipped"] == len(bundles)


class TestResumeDeduplicatesWithSkipScored:
    def test_checkpoint_applied_before_filter_pending(
        self, tmp_path, patched_score_bundle
    ):
        """Resume must not double-count bundles via ``filter_pending``.

        On the first run we wrote ``eval_meta.json`` with the matching
        ``config_hash``, so a naive resume with ``skip_scored=True``
        would emit both a "skipped" (from filter_pending) and a
        "scored" (from checkpoint) outcome for the same bundle.
        """
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        first = run_campaign(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
        )
        resumed = run_campaign(
            bench_root=tmp_path,
            bundles=[b],
            task_loader=lambda _: _stub_task(),
            persona_loader=lambda _: _stub_persona(),
            eval_model="claude",
            concurrency=1,
            resume_campaign_id=first.campaign_id,
        )
        # Each bundle must appear exactly once across the resumed outcomes.
        sids = [o["session_id"] for o in resumed.outcomes]
        assert len(sids) == len(set(sids))


class TestTimeWindow:
    def test_resolve_filters_since(self, tmp_path):
        _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        _make_bundle(
            tmp_path, task_id="X01", persona_id="p1", session_id="b" * 32,
            ts="20260421_130000",
        )
        # created_at is stamped at manifest write time; fake both manifests.
        for b in (tmp_path / "results" / "server" / "X01" / "p1").iterdir():
            mf = b / "manifest.json"
            data = json.loads(mf.read_text())
            if "aaaaaaaa" in b.name:
                data["created_at"] = "2026-04-01T00:00:00"
            else:
                data["created_at"] = "2026-04-20T00:00:00"
            mf.write_text(json.dumps(data))

        recent = batch_mod.resolve_bundles(
            tmp_path, all_bundles=True, since=datetime(2026, 4, 15)
        )
        assert len(recent) == 1
        assert "bbbbbbbb" in recent[0].name

    def test_resolve_handles_tz_aware_since(self, tmp_path):
        """Manifests store naive timestamps; a tz-aware ``since``
        must not crash the comparison."""
        from datetime import timezone

        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        mf = b / "manifest.json"
        data = json.loads(mf.read_text())
        data["created_at"] = "2026-04-20T00:00:00"
        mf.write_text(json.dumps(data))

        tz_since = datetime(2026, 4, 15, tzinfo=timezone.utc)
        got = batch_mod.resolve_bundles(tmp_path, all_bundles=True, since=tz_since)
        assert got == [b]

    def test_resolve_keeps_unparseable_timestamp(self, tmp_path):
        b = _make_bundle(tmp_path, task_id="X01", persona_id="p1", session_id="a" * 32)
        mf = b / "manifest.json"
        data = json.loads(mf.read_text())
        data["created_at"] = "not-a-date"
        mf.write_text(json.dumps(data))

        got = batch_mod.resolve_bundles(
            tmp_path, all_bundles=True, since=datetime(2026, 4, 15)
        )
        assert got == [b]


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
