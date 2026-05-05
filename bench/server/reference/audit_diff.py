"""Compare legacy and reference-bundle audit JSONL streams."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _event_key(event: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(event.get(field) or "") for field in fields)


def load_events(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def compare_events(
    legacy_events: list[dict[str, Any]],
    bundle_events: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...] = ("session_id", "action"),
) -> dict[str, Any]:
    legacy = {_event_key(event, key_fields): event for event in legacy_events}
    bundle = {_event_key(event, key_fields): event for event in bundle_events}
    legacy_keys = set(legacy)
    bundle_keys = set(bundle)
    shared = sorted(legacy_keys & bundle_keys)
    mismatched: list[dict[str, Any]] = []

    for key in shared:
        legacy_payload = legacy[key].get("payload") or {}
        bundle_payload = bundle[key].get("payload") or {}
        if legacy_payload != bundle_payload:
            mismatched.append(
                {
                    "key": key,
                    "legacy_payload": legacy_payload,
                    "bundle_payload": bundle_payload,
                }
            )

    return {
        "key_fields": list(key_fields),
        "legacy_count": len(legacy_events),
        "bundle_count": len(bundle_events),
        "shared_count": len(shared),
        "missing_in_bundle": [list(key) for key in sorted(legacy_keys - bundle_keys)],
        "extra_in_bundle": [list(key) for key in sorted(bundle_keys - legacy_keys)],
        "payload_mismatches": mismatched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare legacy and reference-bundle audit JSONL files."
    )
    parser.add_argument("legacy_jsonl")
    parser.add_argument("bundle_jsonl")
    parser.add_argument(
        "--key-field",
        action="append",
        default=[],
        help="Event field used to pair rows; repeat for compound keys.",
    )
    args = parser.parse_args()
    key_fields = tuple(args.key_field or ["session_id", "action"])
    report = compare_events(
        load_events(args.legacy_jsonl),
        load_events(args.bundle_jsonl),
        key_fields=key_fields,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
