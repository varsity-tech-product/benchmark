#!/usr/bin/env python3
"""Import old run-single results into results/server/ path format.

Copies run_state.json + agent_files/ into the server directory layout so that
the server's find_archived_result_dir() can discover them.

Layout:
  results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
    run_state.json   (with session_id injected)
    agent_files/     (copied)

Usage:
    python -m server.scripts.import_old_results \
        --scan-dir results/run-single/anthropic \
        --dry-run

    python -m server.scripts.import_old_results \
        --scan-dir results/run-single/anthropic
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
SERVER_RESULTS = BENCH_ROOT / "results" / "server"


def generate_session_id(result_dir: Path) -> str:
    """Generate a deterministic session_id from the result directory path.

    Uses SHA-256 of the relative path so the same source always maps to
    the same session_id (idempotent imports).
    """
    rel = str(result_dir.relative_to(BENCH_ROOT))
    return hashlib.sha256(rel.encode()).hexdigest()[:32]


def import_result(src_dir: Path, dry_run: bool = False) -> dict | None:
    """Import one old result directory into server layout."""
    rs_path = src_dir / "run_state.json"
    if not rs_path.exists():
        return None

    run_state = json.loads(rs_path.read_text())
    task_id = run_state.get("task_id", "")
    persona_id = run_state.get("persona_id", "")

    if not task_id or not persona_id:
        print(f"  SKIP (missing task_id/persona_id): {src_dir}")
        return None

    session_id = generate_session_id(src_dir)
    timestamp = "20260407_000000"  # Fixed timestamp for imported results

    dest_dir = SERVER_RESULTS / task_id / persona_id / f"{timestamp}_{session_id[:8]}"

    if dest_dir.exists():
        print(f"  EXISTS: {dest_dir.relative_to(BENCH_ROOT)}")
        return {"session_id": session_id, "dest": str(dest_dir), "status": "exists"}

    if dry_run:
        print(f"  WOULD COPY: {src_dir.name} -> {dest_dir.relative_to(BENCH_ROOT)}")
        return {"session_id": session_id, "dest": str(dest_dir), "status": "dry_run"}

    # Create destination
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy run_state.json with session_id injected
    run_state["session_id"] = session_id
    run_state["_imported_from"] = str(src_dir.relative_to(BENCH_ROOT))
    (dest_dir / "run_state.json").write_text(
        json.dumps(run_state, indent=2, default=str), encoding="utf-8"
    )

    # Copy agent_files/ if exists
    agent_src = src_dir / "agent_files"
    if agent_src.is_dir():
        shutil.copytree(agent_src, dest_dir / "agent_files", dirs_exist_ok=True)

    print(
        f"  COPIED: {src_dir.name} -> {dest_dir.relative_to(BENCH_ROOT)} (sid={session_id[:8]})"
    )
    return {
        "session_id": session_id,
        "dest": str(dest_dir),
        "status": "copied",
        "task_id": task_id,
        "persona_id": persona_id,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Import old results into server layout"
    )
    parser.add_argument("--scan-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=str, help="Save import manifest JSON")
    args = parser.parse_args()

    scan = Path(args.scan_dir)
    if not scan.is_absolute():
        scan = BENCH_ROOT / args.scan_dir

    results = []
    skipped = 0
    for rs in sorted(scan.rglob("run_state.json")):
        if "_original" in str(rs):
            continue
        src_dir = rs.parent
        # Only import results that have scores_sonnet.md (Sonnet judge baseline)
        if not (src_dir / "scores_sonnet.md").exists():
            skipped += 1
            continue
        rel = src_dir.relative_to(BENCH_ROOT)
        print(f"[{len(results)+1}] {rel}")
        result = import_result(src_dir, dry_run=args.dry_run)
        if result:
            result["source"] = str(src_dir)
            results.append(result)
    if skipped:
        print(f"(Skipped {skipped} results without scores_sonnet.md)")

    print(
        f"\nTotal: {len(results)} results {'(dry run)' if args.dry_run else 'imported'}"
    )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
        print(f"Manifest saved to {args.output}")


if __name__ == "__main__":
    main()
