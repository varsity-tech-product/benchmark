"""Produce user-sim-stability data-quality audit artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from experiments.user_sim_stability.core.io_utils import atomic_write_json
from experiments.user_sim_stability.core.paths import (
    BENCH_ROOT,
    default_results_dir,
)

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.user_sim_stability.analysis.validate import validate  # noqa: E402


def _markdown_from_audit(audit: dict) -> str:
    lines = [
        "# User Simulator Data Quality Audit",
        "",
        f"- Overall status: `{'pass' if audit['ok'] else 'fail'}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Message |",
        "|---|---|---|",
    ]
    for check in audit["checks"]:
        status = "pass" if check["ok"] else "fail"
        lines.append(f"| {status} | `{check['name']}` | {check['message']} |")
    lines.extend(
        [
            "",
            "## Policy Notes",
            "",
            "- Missing control judge outputs are failures.",
            "- Control rows must come from real `control__*.json` judge outputs.",
            "- Fixture openings are excluded from generated-user metrics.",
            "- Exact duplicate judge score payloads across any dimension are failures.",
            "- Template-like S2 duplicate score clusters are failures.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_audit(results_dir: Path | None = None, *, profile: str = "full") -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    checks, summary = validate(out, profile=profile, require_audit=False)
    failures = [check for check in checks if not check.ok and check.required]
    audit = {
        "ok": not failures,
        "results_dir_name": out.name,
        "checks": [check.__dict__ for check in checks],
        "summary": summary,
        "profile": profile,
    }
    report_dir = out / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "data_quality_audit.json", audit)
    (report_dir / "data_quality_audit.md").write_text(
        _markdown_from_audit(audit), encoding="utf-8"
    )
    return audit


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--profile", choices=["full", "pilot"], default="full")
    args = parser.parse_args(list(argv) if argv is not None else None)
    audit = run_audit(
        Path(args.results_dir) if args.results_dir else None,
        profile=args.profile,
    )
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
