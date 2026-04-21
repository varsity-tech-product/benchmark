"""Batch driver for the offline evaluator.

Wraps ``server.evaluator.single.score_bundle`` with bundle discovery,
concurrency, idempotency, and a campaign-level manifest. The CLI in
``__main__.py`` is a thin argparse layer over this module; library users
(CI / release gating) can import ``run_campaign`` directly.

Idempotency is keyed on a deterministic ``config_hash`` stamped into
``eval_meta.json`` — re-running the same campaign rewrites nothing unless
``--force`` or a different judge/mode/dims/rubric is passed. Failures are
isolated per bundle so one bad bundle does not abort the campaign.

Rate-limit pooling (issue #47) is approximated by scaling the per-session
``_CONCURRENCY`` down as worker count rises, so the total judge-LLM
in-flight count stays near today's single-session default. A true shared
AsyncSemaphore across workers needs an event-loop redesign inside
``evaluate_task`` and is deferred to future work.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from server.evaluator.config_hash import compute_config_hash
from server.evaluator.paths import list_eval_history
from server.evaluator.single import score_bundle as _score_one
from server.storage.bundle import MANIFEST_FILENAME, load_bundle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BundleOutcome:
    """Per-bundle result within a campaign run."""

    bundle_dir: Path
    session_id: str
    task_id: str
    persona_id: str
    status: str  # "scored" | "skipped" | "failed" | "pending"
    eval_run_id: Optional[str] = None
    eval_run_dir: Optional[Path] = None
    overall_score: Optional[float] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class CampaignSummary:
    """Campaign-level manifest written to ``evaluations/campaigns/.../summary.json``."""

    campaign_id: str
    started_at: str
    finished_at: str
    config: dict
    totals: dict
    cost_usd: float
    duration_s: float
    outcomes: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def new_campaign_id(now: datetime | None = None) -> str:
    """Timestamp-based campaign id with microsecond suffix.

    Second-resolution collides when two campaigns start in the same
    second (rapid ``--all-pending`` reruns that only hit skips); the
    ``_%f`` microsecond tail keeps the per-campaign summary directory
    unique without adding a random component.
    """
    when = now or datetime.now()
    return f"cmp_{when.strftime('%Y%m%d_%H%M%S_%f')}"


def campaign_dir(bench_root: Path | str, campaign_id: str) -> Path:
    return Path(bench_root) / "evaluations" / "campaigns" / campaign_id


def iter_bundle_dirs(bench_root: Path | str) -> Iterable[Path]:
    """Yield every bundle directory under ``results/server``.

    A bundle is a leaf directory that carries a ``manifest.json`` — the
    contract slice 1 of #46 introduced. Pre-manifest directories (legacy
    bundles) are skipped silently; they cannot be scored by the offline
    evaluator anyway.
    """
    root = Path(bench_root) / "results" / "server"
    if not root.is_dir():
        return
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir():
            continue
        for persona_dir in sorted(task_dir.iterdir()):
            if not persona_dir.is_dir():
                continue
            for bundle in sorted(persona_dir.iterdir()):
                if bundle.is_dir() and (bundle / MANIFEST_FILENAME).is_file():
                    yield bundle


def resolve_bundles(
    bench_root: Path | str,
    *,
    session_ids: Optional[list[str]] = None,
    task_ids: Optional[list[str]] = None,
    persona_ids: Optional[list[str]] = None,
    bundle_paths: Optional[list[Path]] = None,
    all_bundles: bool = False,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[Path]:
    """Resolve a bundle set from filter arguments.

    ``bundle_paths`` (when given) wins unconditionally — each entry is
    returned if it has a manifest and dropped otherwise. Without explicit
    paths, the tree under ``results/server`` is walked and filtered by any
    combination of ``session_ids`` / ``task_ids`` / ``persona_ids``. Pass
    ``all_bundles=True`` to skip filtering entirely.

    ``since`` / ``until`` filter bundles by their manifest ``created_at``
    timestamp. Bundles without a parseable timestamp pass the filter so
    legacy producers do not drop out silently.
    """
    if bundle_paths:
        out: list[Path] = []
        for p in bundle_paths:
            path = Path(p)
            if (path / MANIFEST_FILENAME).is_file():
                out.append(path)
            else:
                logger.warning("skipping %s: no manifest.json", path)
        return out

    if not (session_ids or task_ids or persona_ids or all_bundles):
        raise ValueError(
            "resolve_bundles needs at least one filter: session_ids, "
            "task_ids, persona_ids, bundle_paths, or all_bundles=True."
        )

    sid_prefixes = {s[:8] for s in session_ids} if session_ids else None
    task_set = set(task_ids) if task_ids else None
    persona_set = set(persona_ids) if persona_ids else None

    out = []
    for bundle in iter_bundle_dirs(bench_root):
        # Path shape: results/server/{task_id}/{persona_id}/{ts}_{sid8}
        task_id = bundle.parent.parent.name
        persona_id = bundle.parent.name
        sid8 = bundle.name.rsplit("_", 1)[-1]
        if task_set and task_id not in task_set:
            continue
        if persona_set and persona_id not in persona_set:
            continue
        if sid_prefixes and sid8 not in sid_prefixes:
            continue
        if (since or until) and not _bundle_in_window(bundle, since, until):
            continue
        out.append(bundle)
    return out


def _bundle_in_window(
    bundle: Path, since: Optional[datetime], until: Optional[datetime]
) -> bool:
    """Return True when the bundle's manifest timestamp is inside the window.

    Bundles with an unparseable ``created_at`` pass the filter — we'd
    rather score an old bundle than silently drop one whose manifest
    got mangled. Both sides of the comparison are stripped to naive
    datetimes so a tz-aware ``--since 2026-04-15T00:00:00+00:00`` does
    not crash against the naive ``datetime.now().isoformat()`` writers
    stamp into manifests today.
    """
    try:
        manifest = json.loads(
            (bundle / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        created = manifest.get("created_at")
        if not created:
            return True
        ts = _strip_tz(datetime.fromisoformat(created))
    except Exception:
        return True
    since = _strip_tz(since) if since else None
    until = _strip_tz(until) if until else None
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True


def _strip_tz(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def bundle_already_scored(
    bench_root: Path | str,
    *,
    task_id: str,
    persona_id: str,
    session_id: str,
    config_hash: str,
) -> Optional[Path]:
    """Return the matching eval_run_dir if one was scored with this config.

    Walks the sibling tree's ``eval_meta.json`` files newest-first; the
    first match wins.
    """
    for run_dir in list_eval_history(
        bench_root,
        task_id=task_id,
        persona_id=persona_id,
        session_id=session_id,
    ):
        meta_path = run_dir / "eval_meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("config_hash") == config_hash:
            return run_dir
    return None


def filter_pending(
    bundles: list[Path],
    bench_root: Path | str,
    config_hash: str,
) -> tuple[list[Path], list[tuple[Path, Path]]]:
    """Partition bundles into (pending, already_scored).

    ``already_scored`` pairs each skipped bundle with its matching
    eval-run directory so callers can report why it was skipped. Bundles
    that fail to load are treated as pending — ``run_campaign`` will
    surface the load failure as a ``failed`` outcome.
    """
    pending: list[Path] = []
    scored: list[tuple[Path, Path]] = []
    for bundle in bundles:
        try:
            loaded = load_bundle(bundle)
        except Exception as exc:
            logger.warning("cannot load %s — treating as pending: %s", bundle, exc)
            pending.append(bundle)
            continue
        match = bundle_already_scored(
            bench_root,
            task_id=loaded.task_id,
            persona_id=loaded.persona_id,
            session_id=loaded.session_id,
            config_hash=config_hash,
        )
        if match is not None:
            scored.append((bundle, match))
        else:
            pending.append(bundle)
    return pending, scored


# ---------------------------------------------------------------------------
# Campaign driver
# ---------------------------------------------------------------------------


def run_campaign(
    *,
    bench_root: Path | str,
    bundles: list[Path],
    task_loader: Callable[[str], object],
    persona_loader: Callable[[str], object],
    eval_model: str,
    eval_mode: str = "full",
    tutor_dims: Optional[list[str]] = None,
    concurrency: int = 4,
    skip_scored: bool = True,
    force: bool = False,
    rubric_version: str = "",
    formula_version: str = "",
    dry_run: bool = False,
    max_cost_usd: Optional[float] = None,
    resume_campaign_id: Optional[str] = None,
    on_progress: Optional[Callable[[BundleOutcome, int, int], None]] = None,
    campaign_id: Optional[str] = None,
) -> CampaignSummary:
    """Score ``bundles`` and persist a campaign summary.

    Idempotent by default: bundles already scored with the matching
    ``config_hash`` are returned as ``skipped`` outcomes without
    triggering the judge. ``force=True`` bypasses the skip check.

    Operational controls:

    - ``max_cost_usd`` caps the campaign's cumulative eval cost. Once
      the running total meets or exceeds the cap, no further bundles
      are dispatched; remaining pending bundles are marked skipped
      with ``error='budget_exceeded'`` so the campaign summary
      preserves audit information.
    - ``resume_campaign_id`` reuses a prior campaign's id + directory;
      bundles already recorded in that campaign's ``checkpoint.jsonl``
      are skipped, so an interrupted batch can pick up where it left
      off under the same audit trail.
    """
    cfg_hash = compute_config_hash(
        judge=eval_model,
        eval_mode=eval_mode,
        tutor_dims=tutor_dims,
        rubric_version=rubric_version,
        formula_version=formula_version,
    )

    started_at = datetime.now()
    t0 = time.time()
    effective_id = campaign_id or resume_campaign_id or new_campaign_id(started_at)

    # Apply the checkpoint first. ``filter_pending`` would otherwise
    # translate every already-scored bundle into a "skipped" outcome (the
    # sibling-tree eval_meta.json already carries the matching
    # config_hash), duplicating the checkpoint entries and inflating the
    # campaign totals on resume.
    resumed_outcomes: list[BundleOutcome] = []
    if resume_campaign_id:
        resumed_outcomes = _load_checkpoint(bench_root, resume_campaign_id)
        done_bundles = {o.bundle_dir for o in resumed_outcomes}
        bundles = [b for b in bundles if b not in done_bundles]

    if skip_scored and not force:
        pending, skipped_pairs = filter_pending(bundles, bench_root, cfg_hash)
    else:
        pending, skipped_pairs = list(bundles), []

    outcomes: list[BundleOutcome] = list(resumed_outcomes)
    outcomes.extend(_skipped_outcome(b, m) for b, m in skipped_pairs)

    if dry_run:
        for bundle in pending:
            outcomes.append(_pending_outcome(bundle))
    else:
        checkpoint_path = _open_checkpoint(
            bench_root, effective_id, append=bool(resume_campaign_id)
        )
        prev_concurrency = _tune_per_worker_concurrency(concurrency)
        cost_so_far = sum(o.cost_usd or 0.0 for o in outcomes)
        try:
            def _worker(bundle: Path) -> BundleOutcome:
                return _score_bundle_isolated(
                    bundle,
                    bench_root=bench_root,
                    task_loader=task_loader,
                    persona_loader=persona_loader,
                    eval_model=eval_model,
                    eval_mode=eval_mode,
                    tutor_dims=tutor_dims,
                    config_hash=cfg_hash,
                )

            total = len(pending)
            done_count = 0
            pending_iter = iter(pending)
            budget_exceeded = (
                max_cost_usd is not None and cost_so_far >= max_cost_usd
            )

            def _record(out: BundleOutcome) -> None:
                nonlocal done_count, cost_so_far
                outcomes.append(out)
                _append_checkpoint(checkpoint_path, out)
                cost_so_far += out.cost_usd or 0.0
                done_count += 1
                if on_progress:
                    on_progress(out, done_count, total)

            if concurrency <= 1:
                for bundle in pending_iter:
                    if max_cost_usd is not None and cost_so_far >= max_cost_usd:
                        budget_exceeded = True
                        outcomes.append(_budget_skipped_outcome(bundle))
                        continue
                    _record(_worker(bundle))
            else:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    in_flight: set = set()
                    while True:
                        while (
                            len(in_flight) < concurrency
                            and not budget_exceeded
                        ):
                            try:
                                bundle = next(pending_iter)
                            except StopIteration:
                                break
                            in_flight.add(pool.submit(_worker, bundle))
                        if not in_flight:
                            break
                        finished, in_flight = wait(
                            in_flight, return_when=FIRST_COMPLETED
                        )
                        for fut in finished:
                            _record(fut.result())
                            if (
                                max_cost_usd is not None
                                and cost_so_far >= max_cost_usd
                            ):
                                budget_exceeded = True

                for bundle in pending_iter:
                    outcomes.append(_budget_skipped_outcome(bundle))
        finally:
            _restore_per_worker_concurrency(prev_concurrency)

    finished_at = datetime.now()
    totals = _tally(outcomes)
    total_cost = round(sum(o.cost_usd or 0.0 for o in outcomes), 6)
    summary = CampaignSummary(
        campaign_id=effective_id,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        config={
            "judge": eval_model,
            "eval_mode": eval_mode,
            "tutor_dims": sorted(tutor_dims) if tutor_dims else [],
            "rubric_version": rubric_version,
            "formula_version": formula_version,
            "config_hash": cfg_hash,
            "concurrency": concurrency,
            "skip_scored": skip_scored and not force,
            "force": force,
            "dry_run": dry_run,
            "max_cost_usd": max_cost_usd,
            "resumed_from": resume_campaign_id,
        },
        totals=totals,
        cost_usd=total_cost,
        duration_s=round(time.time() - t0, 2),
        outcomes=[_outcome_to_dict(o) for o in outcomes],
    )

    if not dry_run:
        write_campaign_summary(bench_root, summary)
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_bundle_isolated(
    bundle: Path,
    *,
    bench_root: Path | str,
    task_loader,
    persona_loader,
    eval_model: str,
    eval_mode: str,
    tutor_dims: Optional[list[str]],
    config_hash: str,
) -> BundleOutcome:
    start = time.time()
    try:
        loaded = load_bundle(bundle)
    except Exception as exc:
        return BundleOutcome(
            bundle_dir=bundle,
            session_id="",
            task_id="",
            persona_id="",
            status="failed",
            error=f"load_bundle: {exc}",
            duration_seconds=round(time.time() - start, 2),
        )

    try:
        task = task_loader(loaded.task_id)
        persona = persona_loader(loaded.persona_id)
        result = _score_one(
            bundle_dir=bundle,
            task=task,
            persona=persona,
            bench_root=bench_root,
            eval_model=eval_model,
            eval_mode=eval_mode,
            tutor_dims=tutor_dims,
        )
    except (Exception, SystemExit) as exc:
        # ``_load_task`` / ``_load_persona`` raise ``SystemExit`` when a task or
        # persona JSON is missing (CLI-friendly). In the batch driver that would
        # kill the whole campaign; treat it as a per-bundle failure instead so
        # the rest of the cohort keeps going.
        logger.exception("scoring failed for %s", bundle)
        return BundleOutcome(
            bundle_dir=bundle,
            session_id=loaded.session_id,
            task_id=loaded.task_id,
            persona_id=loaded.persona_id,
            status="failed",
            error=str(exc),
            duration_seconds=round(time.time() - start, 2),
        )

    run_dir: Path = result["_eval_run_dir"]
    cost_usd = _extract_eval_cost(result)
    _stamp_config_hash(run_dir / "eval_meta.json", config_hash, cost_usd=cost_usd)

    return BundleOutcome(
        bundle_dir=bundle,
        session_id=loaded.session_id,
        task_id=loaded.task_id,
        persona_id=loaded.persona_id,
        status="scored",
        eval_run_id=result.get("_eval_run_id"),
        eval_run_dir=run_dir,
        overall_score=result.get("_overall_score"),
        cost_usd=cost_usd,
        duration_seconds=round(time.time() - start, 2),
    )


def _extract_eval_cost(result: dict) -> float:
    """Sum the ``_eval_cost`` signals the pipeline leaves in its output.

    Sub-evaluators stamp their own cost under ``_eval_cost`` either at the
    top level (process metrics, tutor rubric rollup) or nested inside a
    per-component dict (``result_judge``). Summing the ones we know about
    is a faithful enough estimate for budget gating; exact dollar parity
    with ``cost.md`` is not required here.
    """
    total = 0.0
    for key in ("_eval_cost", "_tutor_eval_cost", "_process_eval_cost"):
        total += float(result.get(key, 0.0) or 0.0)

    for nested_key in ("tutor_scores", "result_judge", "process_metrics"):
        nested = result.get(nested_key) or {}
        if isinstance(nested, dict):
            total += float(nested.get("_eval_cost", 0.0) or 0.0)

    return round(total, 6)


def _stamp_config_hash(
    meta_path: Path, config_hash: str, *, cost_usd: float = 0.0
) -> None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("cannot stamp config_hash: %s unreadable", meta_path)
        return
    meta["config_hash"] = config_hash
    if cost_usd:
        meta["cost_usd"] = round(cost_usd, 6)
    meta_path.write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )


def _concurrency_modules():
    """Return (tutor_module, process_metrics_module) or None if unavailable."""
    try:
        from server.eval.ewan_eval import process_metrics as proc
        from server.eval.ewan_eval import tutor_conv_geval as tutor

        return tutor, proc
    except Exception:
        return None


def _tune_per_worker_concurrency(n_workers: int) -> tuple[int, int] | None:
    """Scale per-session LLM concurrency and return the prior values.

    Each worker thread spins up its own async loop + ``Semaphore(k)``;
    without tuning, N workers give N × k in-flight requests. Divide the
    default-20 budget across workers so the batch does not overrun
    provider limits. The caller must pass the return value to
    ``_restore_per_worker_concurrency`` so a long-lived process (REST
    server, interactive REPL) is not left throttled after the campaign.
    """
    mods = _concurrency_modules()
    if mods is None:
        return None
    tutor, proc = mods
    prev = (tutor._CONCURRENCY, proc._CONCURRENCY)
    per = max(3, 20 // max(1, n_workers))
    tutor.set_eval_concurrency(per)
    proc.set_eval_concurrency(per)
    return prev


def _restore_per_worker_concurrency(prev: tuple[int, int] | None) -> None:
    if prev is None:
        return
    mods = _concurrency_modules()
    if mods is None:
        return
    tutor, proc = mods
    tutor.set_eval_concurrency(prev[0])
    proc.set_eval_concurrency(prev[1])


def _tally(outcomes: list[BundleOutcome]) -> dict:
    return {
        "total": len(outcomes),
        "scored": sum(1 for o in outcomes if o.status == "scored"),
        "skipped": sum(1 for o in outcomes if o.status == "skipped"),
        "failed": sum(1 for o in outcomes if o.status == "failed"),
        "pending": sum(1 for o in outcomes if o.status == "pending"),
    }


def _outcome_to_dict(o: BundleOutcome) -> dict:
    d = asdict(o)
    d["bundle_dir"] = str(d["bundle_dir"])
    if d.get("eval_run_dir") is not None:
        d["eval_run_dir"] = str(d["eval_run_dir"])
    return d


def _skipped_outcome(bundle: Path, match: Path) -> BundleOutcome:
    try:
        loaded = load_bundle(bundle)
        sid = loaded.session_id
        tid = loaded.task_id
        pid = loaded.persona_id
    except Exception:
        sid = tid = pid = ""
    return BundleOutcome(
        bundle_dir=bundle,
        session_id=sid,
        task_id=tid,
        persona_id=pid,
        status="skipped",
        eval_run_id=match.name,
        eval_run_dir=match,
    )


def _pending_outcome(bundle: Path) -> BundleOutcome:
    try:
        loaded = load_bundle(bundle)
        return BundleOutcome(
            bundle_dir=bundle,
            session_id=loaded.session_id,
            task_id=loaded.task_id,
            persona_id=loaded.persona_id,
            status="pending",
        )
    except Exception as exc:
        return BundleOutcome(
            bundle_dir=bundle,
            session_id="",
            task_id="",
            persona_id="",
            status="failed",
            error=f"load_bundle: {exc}",
        )


def write_campaign_summary(
    bench_root: Path | str, summary: CampaignSummary
) -> Path:
    out_dir = campaign_dir(bench_root, summary.campaign_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "summary.json"
    path.write_text(
        json.dumps(asdict(summary), indent=2, default=str), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------


def _checkpoint_path(bench_root: Path | str, campaign_id: str) -> Path:
    return campaign_dir(bench_root, campaign_id) / "checkpoint.jsonl"


def _open_checkpoint(
    bench_root: Path | str, campaign_id: str, *, append: bool
) -> Path:
    path = _checkpoint_path(bench_root, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not append:
        path.write_text("", encoding="utf-8")
    return path


def _append_checkpoint(checkpoint: Path, outcome: BundleOutcome) -> None:
    line = json.dumps(_outcome_to_dict(outcome), default=str)
    with checkpoint.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _load_checkpoint(
    bench_root: Path | str, campaign_id: str
) -> list[BundleOutcome]:
    """Rehydrate outcomes recorded before the campaign was interrupted."""
    path = _checkpoint_path(bench_root, campaign_id)
    if not path.is_file():
        return []
    out: list[BundleOutcome] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            entry = json.loads(s)
        except Exception:
            logger.warning("skipping malformed checkpoint line in %s", path)
            continue
        out.append(
            BundleOutcome(
                bundle_dir=Path(entry.get("bundle_dir", "")),
                session_id=entry.get("session_id", ""),
                task_id=entry.get("task_id", ""),
                persona_id=entry.get("persona_id", ""),
                status=entry.get("status", "failed"),
                eval_run_id=entry.get("eval_run_id"),
                eval_run_dir=(
                    Path(entry["eval_run_dir"]) if entry.get("eval_run_dir") else None
                ),
                overall_score=entry.get("overall_score"),
                cost_usd=float(entry.get("cost_usd", 0.0) or 0.0),
                duration_seconds=float(entry.get("duration_seconds", 0.0) or 0.0),
                error=entry.get("error"),
            )
        )
    return out


def _budget_skipped_outcome(bundle: Path) -> BundleOutcome:
    try:
        loaded = load_bundle(bundle)
        sid, tid, pid = loaded.session_id, loaded.task_id, loaded.persona_id
    except Exception:
        sid = tid = pid = ""
    return BundleOutcome(
        bundle_dir=bundle,
        session_id=sid,
        task_id=tid,
        persona_id=pid,
        status="skipped",
        error="budget_exceeded",
    )
