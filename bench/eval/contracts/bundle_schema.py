"""JSON Schema validation tool for Bundle v1 alpha."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("bundle_v1_alpha.schema.json")


class BundleValidationError(ValueError):
    """Raised when a bundle fails JSON Schema validation."""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_bundle_dict(data: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install jsonschema>=4.0 to validate bundle JSON") from exc

    validator = Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.path))
    if errors:
        lines = []
        for err in errors:
            path = ".".join(str(part) for part in err.absolute_path) or "$"
            lines.append(f"{path}: {err.message}")
        raise BundleValidationError("\n".join(lines))


def validate_bundle_path(path: str | Path) -> None:
    bundle_path = Path(path)
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BundleValidationError("Bundle JSON root must be an object")
    validate_bundle_dict(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="*", type=Path, help="bundle.json path")
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print the Bundle v1 alpha JSON Schema",
    )
    args = parser.parse_args(argv)

    if args.print_schema:
        print(json.dumps(load_schema(), indent=2))
    failures = 0
    for path in args.bundle:
        try:
            validate_bundle_path(path)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{path}: invalid\n{exc}", file=sys.stderr)
        else:
            print(f"{path}: valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
