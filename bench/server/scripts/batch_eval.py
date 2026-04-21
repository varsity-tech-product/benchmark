#!/usr/bin/env python3
"""Batch evaluate D3/D4 on imported sessions via REST API.

Sends concurrent evaluate requests to a running server.

Usage:
    # Pre round (generate Phase 1 cache)
    python -m server.scripts.batch_eval \
        --manifest results/rubric_comparison/import_manifest.json \
        --tag pre \
        --concurrency 10

    # Post round (load Phase 1 cache from pre)
    python -m server.scripts.batch_eval \
        --manifest results/rubric_comparison/import_manifest.json \
        --tag post \
        --phase1-cache-from pre \
        --concurrency 10
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
SERVER_URL = "http://localhost:8765"
DIMS = "D4_instructional_accuracy"
EVAL_MODE = "tutor_only"


def evaluate_session(sid: str, tag: str, phase1_cache_path: str = "") -> dict:
    """Send evaluate request and poll until completion."""
    params = f"force=true&eval_mode={EVAL_MODE}&tutor_dims={DIMS}"
    if phase1_cache_path:
        params += f"&phase1_cache={phase1_cache_path}"

    url = f"{SERVER_URL}/session/{sid}/evaluate?{params}"
    req = urllib.request.Request(url, method="POST", data=b"")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            start_resp = json.loads(resp.read())
    except Exception as e:
        return {"session_id": sid, "error": f"POST failed: {e}"}

    if start_resp.get("status") == "completed":
        return {"session_id": sid, "scores": start_resp.get("scores", {})}

    # Poll for completion
    for _ in range(60):
        time.sleep(2)
        try:
            with urllib.request.urlopen(
                f"{SERVER_URL}/session/{sid}/scores", timeout=10
            ) as resp:
                result = json.loads(resp.read())
            if result.get("status") == "completed":
                tutor = result.get("scores", {}).get("tutor_scores", {})
                dims = {
                    k: v
                    for k, v in tutor.items()
                    if not k.startswith("_") and isinstance(v, (int, float))
                }
                return {"session_id": sid, "dim_scores": dims}
            if result.get("status") == "failed":
                return {"session_id": sid, "error": result.get("error", "eval failed")}
        except Exception:
            pass

    return {"session_id": sid, "error": "timeout"}


def main():
    parser = argparse.ArgumentParser(description="Batch D3/D4 evaluation")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--tag", required=True, help="pre or post")
    parser.add_argument(
        "--phase1-cache-from",
        type=str,
        help="Load Phase 1 cache from this tag's cache dir",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    print(
        f"Sessions: {len(manifest)}, Tag: {args.tag}, Concurrency: {args.concurrency}"
    )

    # Phase 1 cache directory
    cache_dir = (
        BENCH_ROOT / "results" / "rubric_comparison" / "phase1_caches" / args.tag
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    # If loading from another tag's caches
    source_cache_dir = None
    if args.phase1_cache_from:
        source_cache_dir = (
            BENCH_ROOT
            / "results"
            / "rubric_comparison"
            / "phase1_caches"
            / args.phase1_cache_from
        )

    # Health check
    try:
        urllib.request.urlopen(f"{SERVER_URL}/session/list", timeout=5)
    except Exception:
        print(f"ERROR: Server not reachable at {SERVER_URL}")
        sys.exit(1)

    t0 = time.time()
    results = []

    def run_one(entry):
        sid = entry["session_id"]
        task_id = entry.get("task_id", "")

        if source_cache_dir:
            cache_path = str(source_cache_dir / f"{sid}.json")
        else:
            cache_path = str(cache_dir / f"{sid}.json")

        result = evaluate_session(sid, args.tag, phase1_cache_path=cache_path)
        result["task_id"] = task_id
        result["source"] = entry.get("source", "")
        return result

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_one, entry): entry for entry in manifest}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            result = fut.result()
            results.append(result)
            sid_short = result["session_id"][:8]
            if "error" in result:
                print(
                    f"  [{done_count}/{len(manifest)}] {sid_short} ERROR: {result['error']}"
                )
            else:
                dims = result.get("dim_scores", {})
                d3 = dims.get("D3_pedagogical_method", "?")
                d4 = dims.get("D4_instructional_accuracy", "?")
                print(f"  [{done_count}/{len(manifest)}] {sid_short} D3={d3} D4={d4}")

    elapsed = time.time() - t0

    # Save results
    out_path = BENCH_ROOT / "results" / "rubric_comparison" / f"{args.tag}_d3d4.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))

    ok = [r for r in results if "error" not in r]
    errs = [r for r in results if "error" in r]
    print(f"\nDone in {elapsed:.0f}s: {len(ok)} ok, {len(errs)} errors")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
