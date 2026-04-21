"""Path layout for evaluator output.

Eval output lives in a parallel sibling tree to the raw bundle::

    bundle:  results/server/{task_id}/{persona_id}/{ts}_{sid8}/...
    eval:    evaluations/server/{task_id}/{persona_id}/{sid8}/{eval_run_id}/

The split keeps the bundle directory free of evaluator-produced files (the
producer/consumer contract from issue #46) and lets a single bundle hold
multiple eval runs side-by-side (re-score campaigns, judge A/B, etc.).

A single helper, :func:`find_latest_eval_dir`, resolves the newest eval
output for a given bundle. It checks the new sibling tree first and falls
back to the legacy in-bundle ``evaluations/`` subdirectory so existing
data stays visible until it ages out — the fallback can be deleted in
slice 5 of the migration.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

EVAL_RUN_ID_RE = re.compile(r"^eval_\d{8}_\d{6}$")


def evaluations_root(bench_root: Path | str) -> Path:
    return Path(bench_root) / "evaluations" / "server"


def eval_session_dir(
    bench_root: Path | str, task_id: str, persona_id: str, session_id: str
) -> Path:
    """Per-session directory under the sibling tree.

    Holds one ``eval_run_id`` subdirectory per scoring run.
    """
    return (
        evaluations_root(bench_root)
        / task_id
        / persona_id
        / session_id[:8]
    )


def new_eval_run_id(now: datetime | None = None) -> str:
    """Generate a fresh ``eval_run_id`` (timestamped, sortable)."""
    when = now or datetime.now()
    return f"eval_{when.strftime('%Y%m%d_%H%M%S')}"


def eval_run_dir(
    bench_root: Path | str,
    task_id: str,
    persona_id: str,
    session_id: str,
    eval_run_id: str,
) -> Path:
    return eval_session_dir(bench_root, task_id, persona_id, session_id) / eval_run_id


def _latest_in(eval_root: Path) -> Path | None:
    if not eval_root.is_dir():
        return None
    candidates = [
        p for p in eval_root.iterdir() if p.is_dir() and EVAL_RUN_ID_RE.match(p.name)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


def find_latest_eval_dir(
    bench_root: Path | str,
    bundle_dir: Path,
    task_id: str,
    persona_id: str,
    session_id: str,
) -> Path | None:
    """Return the newest eval-run directory for a bundle, or ``None``.

    Prefers the new sibling tree; falls back to the legacy in-bundle
    ``evaluations/`` subdirectory so historical data remains visible.
    """
    sibling = _latest_in(
        eval_session_dir(bench_root, task_id, persona_id, session_id)
    )
    if sibling is not None:
        return sibling
    legacy_root = Path(bundle_dir) / "evaluations"
    legacy = _latest_in(legacy_root)
    if legacy is not None:
        return legacy
    legacy_symlink = legacy_root / "latest"
    if legacy_symlink.exists() and legacy_symlink.is_dir():
        return legacy_symlink.resolve()
    return None


def list_eval_history(
    bench_root: Path | str,
    bundle_dir: Path,
    task_id: str,
    persona_id: str,
    session_id: str,
) -> list[Path]:
    """All eval-run directories for a bundle, newest first.

    Merges the sibling tree and the legacy in-bundle location; deduplicates
    by ``eval_run_id`` (sibling wins on collision).
    """
    seen: set[str] = set()
    out: list[Path] = []

    sibling_root = eval_session_dir(bench_root, task_id, persona_id, session_id)
    if sibling_root.is_dir():
        for p in sibling_root.iterdir():
            if p.is_dir() and EVAL_RUN_ID_RE.match(p.name):
                seen.add(p.name)
                out.append(p)

    legacy_root = Path(bundle_dir) / "evaluations"
    if legacy_root.is_dir():
        for p in legacy_root.iterdir():
            if not p.is_dir() or not EVAL_RUN_ID_RE.match(p.name):
                continue
            if p.name in seen:
                continue
            out.append(p)

    out.sort(key=lambda p: p.name, reverse=True)
    return out
