"""Shared filesystem and serialization helpers for student_sim_stability.

These helpers are consolidated here so the canonical body lives in one place
and the pipeline / analysis / judge_qualification modules import from a
single source.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        tmp_path = Path(fh.name)
    tmp_path.replace(path)


def sha256_directory(root: Path) -> dict[str, str]:
    """Return ``{relative_path: sha256_hex}`` for every file under ``root``."""
    hashes: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def safe_model_dir(model: str) -> str:
    return model.replace("/", "__").replace(".", "_")


def input_payload_hash(payload: Mapping[str, Any]) -> str:
    relevant = {
        "eval_id": payload.get("eval_id"),
        "dimension": payload.get("dimension"),
        "rubric_id": payload.get("rubric_id"),
        "rubric_version": payload.get("rubric_version"),
        "prompt": payload.get("prompt"),
        "metadata": payload.get("metadata", {}),
    }
    encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
